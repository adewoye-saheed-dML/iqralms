"""API views for the scheduling app — the Phase 3, 4 and 5 surface.

Availability is read-only here, the same call Phases 1 and 2 made for teacher
profiles and curriculum data: a teacher's hours are maintained in the admin
(where ``Availability.create_from_local`` does the UTC conversion), and the API
only publishes them. A teacher-facing write endpoint is a deliberate gap, noted
in tech-debt.md.

Phase 4 adds the routing endpoint, which is the one place in the API where the
*system* picks a teacher rather than a human doing it. Phase 5 extends that same
endpoint with ``preferred_teacher`` — the one place where naming a teacher who is
full produces a tracked promise (a waitlist entry) instead of either a silent
redirect or a bare refusal — plus the three waitlist endpoints the lead and the
family read it through.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin

from .exceptions import (
    NoCapacity,
    WaitlistEntryAlreadyFulfilled,
)
from .models import Availability, Booking, Cohort, TeacherWaitlist
from .permissions import (
    IsLeadTeacher,
    IsOwnerAdminOrLeadTeacher,
    IsStudent,
    IsStudentOrParent,
    IsTeacher,
)
from .routing import promote_waitlist_entry, route_session
from .serializers import (
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingMeetingSerializer,
    BookingSerializer,
    CohortCreateSerializer,
    CohortSerializer,
    NoCapacitySerializer,
    RouteRequestSerializer,
    RoutedSerializer,
    WaitlistEntrySerializer,
    WaitlistPromoteSerializer,
    as_drf_error,
)


NOT_A_MEMBER = OpenApiResponse(
    description="Not an active member of this organization."
)


class AcademyScopedView(OrganizationScopedMixin):
    """Shared plumbing for the academy-scoped scheduling views.

    The organization comes from the URL kwarg ``organization_pk`` and is resolved
    to the caller's membership by the parent mixin; and it is put into the
    serializer context, which is how write serializers narrow their querysets
    to one tenant.
    """

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.caller_membership:
            context["organization"] = self.organization
        return context


class Conflict(APIException):
    """409 — the request is valid but the target is in the wrong state.

    Two callers: cancelling a booking that is not ``scheduled`` (Phase 3) and
    promoting a waitlist entry that already has a session (Phase 5). Both pass an
    explicit message, so the default below is only ever a fallback.
    """

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This action conflicts with the current state of the record."
    default_code = "conflict"


#: One query for the booking plus everything the read serializer touches.
BOOKING_RELATED = ("student", "teacher", "level", "level__track")


def required_int_param(request, name):
    """Read a required integer query parameter, or raise a 400 explaining why.

    Shared by the two endpoints that scope their listing to one object: without
    the parameter the honest behaviour would be dumping every teacher's calendar
    or every cohort in the academy, which is a different endpoint than the one
    the spec asks for.
    """
    raw = request.query_params.get(name)
    if not raw:
        raise ValidationError({name: ["This query parameter is required."]})
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ["Must be an integer."]})


class AvailabilityListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/availability/?teacher_id= — one teacher's hours."""

    serializer_class = AvailabilitySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Whose availability to list.",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        teacher_id = required_int_param(self.request, "teacher_id")
        # An unknown teacher or a teacher with no hours in this academy is an
        # empty list, not a 404: "when is this person free" and "does this person
        # exist" are different questions.
        return Availability.objects.filter(
            organization=self.organization, teacher_id=teacher_id
        ).select_related("teacher")


class BookingCreateView(AcademyScopedView, generics.CreateAPIView):
    """POST /api/scheduling/organizations/{id}/bookings/ — a student, or their parent, books a slot.

    Validation of the slot itself lives in ``Booking.clean()``; a rejection
    arrives here as a 400 carrying the model's own message.
    """

    serializer_class = BookingCreateSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudentOrParent]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=BookingSerializer,
                description=(
                    "Created, with a generated video room and the Jitsi join "
                    "URL both sides use."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "Outside the teacher's declared hours, overlapping another "
                    "scheduled session, an unapproved teacher, or a minor with "
                    "no linked parent."
                )
            ),
            403: NOT_A_MEMBER,
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        from notifications.services import notify_booking_confirmed
        notify_booking_confirmed(booking)
        body = BookingSerializer(booking, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class MyBookingListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/bookings/mine/ — the student's own sessions.

    Past and upcoming, every status, earliest first. Cancelled ones stay
    visible: they are history, not noise.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def get_queryset(self):
        return (
            Booking.objects.filter(
                level__track__organization=self.organization,
                student=self.request.user,
            )
            .select_related(*BOOKING_RELATED)
        )


class TeachingBookingListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/bookings/teaching/ — sessions this teacher teaches."""

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_queryset(self):
        return (
            Booking.objects.filter(
                level__track__organization=self.organization,
                teacher=self.request.user,
            )
            .select_related(*BOOKING_RELATED)
        )


class AcademyBookingListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/bookings/academy/ — academy-wide schedule for management."""

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_queryset(self):
        queryset = Booking.objects.filter(
            level__track__organization=self.organization
        ).select_related(*BOOKING_RELATED)

        teacher_id = self.request.query_params.get("teacher_id") or self.request.query_params.get("teacher")
        if teacher_id:
            queryset = queryset.filter(teacher_id=teacher_id)

        student_id = self.request.query_params.get("student_id") or self.request.query_params.get("student")
        if student_id:
            queryset = queryset.filter(student_id=student_id)

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(status=status_param)

        date_param = self.request.query_params.get("date")
        if date_param:
            queryset = queryset.filter(start_time_utc__date=date_param)

        start_date = self.request.query_params.get("start_date")
        if start_date:
            queryset = queryset.filter(start_time_utc__date__gte=start_date)

        end_date = self.request.query_params.get("end_date")
        if end_date:
            queryset = queryset.filter(start_time_utc__date__lte=end_date)

        track_id = self.request.query_params.get("track_id") or self.request.query_params.get("track")
        if track_id:
            queryset = queryset.filter(level__track_id=track_id)

        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(name="teacher_id", type=int, location=OpenApiParameter.QUERY, required=False, description="Filter by teacher ID."),
            OpenApiParameter(name="student_id", type=int, location=OpenApiParameter.QUERY, required=False, description="Filter by student ID."),
            OpenApiParameter(name="track_id", type=int, location=OpenApiParameter.QUERY, required=False, description="Filter by track ID."),
            OpenApiParameter(name="status", type=str, location=OpenApiParameter.QUERY, required=False, description="Filter by booking status."),
            OpenApiParameter(name="date", type=str, location=OpenApiParameter.QUERY, required=False, description="Filter by exact date (YYYY-MM-DD)."),
            OpenApiParameter(name="start_date", type=str, location=OpenApiParameter.QUERY, required=False, description="Filter from start date (YYYY-MM-DD)."),
            OpenApiParameter(name="end_date", type=str, location=OpenApiParameter.QUERY, required=False, description="Filter to end date (YYYY-MM-DD)."),
        ],
        responses={
            200: OpenApiResponse(response=BookingSerializer(many=True)),
            401: OpenApiResponse(description="Unauthenticated."),
            403: OpenApiResponse(description="Permission denied."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class BookingCancelView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/scheduling/organizations/{id}/bookings/{id}/cancel/ — either party cancels.

    The row is kept and its status set; nothing is deleted, because attendance
    and payouts need the history later.

    The queryset is scoped to bookings the caller is a party to — the student,
    the teacher, or a parent linked to the student (a parent may book, so a
    parent may cancel) — strictly within the route academy. Anyone else gets a 404
    rather than a 403, so this endpoint cannot be used to discover that a
    booking exists.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    def get_queryset(self):
        user = self.request.user
        return (
            Booking.objects.filter(
                level__track__organization=self.organization
            )
            .filter(
                Q(student=user)
                | Q(teacher=user)
                | Q(student__parent_links__parent=user)
            )
            .select_related(*BOOKING_RELATED)
            .distinct()
        )

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(response=BookingSerializer),
            403: NOT_A_MEMBER,
            404: OpenApiResponse(description="No such booking for this caller in this organization."),
            409: OpenApiResponse(
                description="Already cancelled, or completed and now history."
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        from .exceptions import BookingNotCancellable
        booking = self.get_object()
        try:
            booking.cancel()
        except BookingNotCancellable as exc:
            raise Conflict(str(exc)) from exc
        from notifications.services import notify_booking_cancelled
        notify_booking_cancelled(booking)
        body = BookingSerializer(booking, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_200_OK)


class BookingMeetingView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/scheduling/organizations/{id}/bookings/{id}/meeting/ — provider-neutral class meeting access."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = BookingMeetingSerializer

    def get_queryset(self):
        return Booking.objects.filter(
            level__track__organization=self.organization
        ).select_related(*BOOKING_RELATED)

    @extend_schema(
        responses={
            200: OpenApiResponse(response=BookingMeetingSerializer),
            400: OpenApiResponse(description="Booking is cancelled."),
            403: OpenApiResponse(description="You do not have access to this class."),
            404: OpenApiResponse(description="Booking not found in this academy."),
        }
    )
    def get(self, request, *args, **kwargs):
        booking = self.get_object()
        user = request.user

        from .models import BookingStatus

        if booking.status == BookingStatus.CANCELLED:
            raise serializers.ValidationError({"detail": "This class has been cancelled."})

        membership = self.caller_membership
        from organizations.models import OrganizationRole

        is_owner_admin = bool(
            membership
            and membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
        )
        is_teacher = booking.teacher_id == user.id
        is_student = booking.student_id == user.id
        from accounts.models import Role

        is_parent = getattr(user, "role", None) == Role.PARENT and user.child_links.filter(
            student_id=booking.student_id
        ).exists()

        if not (is_owner_admin or is_teacher or is_student or is_parent):
            raise PermissionDenied("You do not have access to this class.")

        display_name = user.get_full_name() or user.username
        data = {
            "provider": booking.video_provider or "jitsi",
            "provider_meeting_id": booking.video_provider_meeting_id,
            "join_url": booking.video_join_url,
            "display_name": display_name,
        }
        return Response(data, status=status.HTTP_200_OK)



# --- Phase 4: routing and cohorts -------------------------------------------


class RouteView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/scheduling/organizations/{id}/route/ — the system picks who teaches.

    Cohort first, then the lead teacher if they have capacity, then a matched
    sub-teacher. The algorithm itself is in ``routing.py``; this view only
    translates its two outcomes into HTTP.

    A refusal is a **409**, not a 200 with ``routed: false`` and not a 400. The
    request was valid and was processed — what stands in the way is the state of
    the academy's capacity, which is precisely what 409 means, and it is the code
    Phase 3's cancel endpoint already uses for "valid request, wrong state". The
    body is structured either way, so a frontend can explain the refusal instead
    of showing a bare error, which is what the spec asks for.

    Phase 5's ``preferred_teacher`` reuses that 409 rather than adding an outcome:
    a waitlisted request is still "valid request, wrong state", and the entry it
    created is reported inside the same ``considered`` payload. So a client that
    already renders the refusal keeps working, and one that wants to say "you are
    on Ustadh's list" reads one extra key.
    """

    serializer_class = RouteRequestSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudentOrParent]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=RoutedSerializer,
                description=(
                    "Assigned. ``routed_reason`` says how: cohort_assigned, "
                    "lead_available, lead_full_routed, or student_choice when a "
                    "``preferred_teacher`` was named and could take it. A cohort "
                    "seat's start time is the cohort's own, which may differ from "
                    "the requested one."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "The request itself is wrong: an unknown level, a parent not "
                    "naming a linked child, a slot the assigned teacher lost "
                    "to a concurrent booking, or a ``preferred_teacher`` who "
                    "cannot teach this at all (unapproved, wrong track) — a "
                    "refusal no amount of waiting fixes, so no waitlist entry is "
                    "created for it."
                )
            ),
            403: NOT_A_MEMBER,
            409: OpenApiResponse(
                response=NoCapacitySerializer,
                description=(
                    "No capacity. Carries why each step declined; nobody is "
                    "booked outside their hours or over their cap to avoid this. "
                    "For a ``preferred_teacher`` request this is also the "
                    "waitlisted outcome: ``considered.waitlist`` carries the "
                    "entry, and no other teacher has been assigned."
                ),
            ),
        }
    )
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        window = serializer.validated_data["requested_time_window"]

        try:
            routed = route_session(
                student=serializer.validated_data["student"],
                level=serializer.validated_data["level"],
                start_time_utc=window["start_time_utc"],
                duration_minutes=window["duration_minutes"],
                preferred_teacher=serializer.validated_data.get("preferred_teacher"),
                organization=self.organization,
            )
        except NoCapacity as exc:
            return Response(
                {
                    "routed": False,
                    "reason": "no_capacity",
                    "detail": str(exc),
                    "considered": exc.considered,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except DjangoValidationError as exc:
            # Routing validated the candidate, then lost the slot (or the last
            # cohort seat) to a concurrent write while taking the teacher's lock.
            # Also the preferred-teacher hard-block case: a teacher who does not
            # teach this track is refused the same way a direct booking naming
            # them would be. Same 400 either way.
            raise as_drf_error(exc) from exc

        if routed and hasattr(routed, "booking") and routed.booking:
            from notifications.services import notify_booking_confirmed
            notify_booking_confirmed(routed.booking)

        context = self.get_serializer_context()
        return Response(
            {
                "routed": True,
                "routed_reason": routed.reason,
                "booking": BookingSerializer(routed.booking, context=context).data,
                "cohort": (
                    CohortSerializer(routed.cohort, context=context).data
                    if routed.cohort
                    else None
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class CohortListCreateView(AcademyScopedView, generics.ListCreateAPIView):
    """GET /api/scheduling/organizations/{id}/cohorts/ — list academy cohorts.
    POST /api/scheduling/organizations/{id}/cohorts/ — open a group class.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return CohortCreateSerializer
        return CohortSerializer

    def get_queryset(self):
        return (
            Cohort.objects.filter(level__track__organization=self.organization)
            .select_related("level", "level__track", "teacher")
            .prefetch_related("memberships")
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(response=CohortSerializer(many=True)),
            201: OpenApiResponse(response=CohortSerializer),
            400: OpenApiResponse(
                description=(
                    "Level is not group-eligible, the teacher is unapproved, or "
                    "the teacher does not teach the level's track."
                )
            ),
            403: NOT_A_MEMBER,
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cohort = serializer.save()
        body = CohortSerializer(cohort, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class CohortDetailView(AcademyScopedView, generics.RetrieveAPIView):
    """GET /api/scheduling/organizations/{id}/cohorts/{pk}/ — retrieve a cohort."""

    serializer_class = CohortSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_queryset(self):
        return (
            Cohort.objects.filter(level__track__organization=self.organization)
            .select_related("level", "level__track", "teacher")
            .prefetch_related("memberships")
        )


CohortCreateView = CohortListCreateView


class OpenCohortListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/cohorts/open/?level_id= — cohorts with a seat free.

    The same queryset routing's step 1 reads, exposed so a "browse open cohorts"
    view can be built on it later without a second definition of "open" existing.
    Seat counts are published; the roster is not (see ``CohortSerializer``).
    """

    serializer_class = CohortSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="level_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Which level's open cohorts to list.",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        level_id = required_int_param(self.request, "level_id")
        # An unknown level or a level belonging to another academy is an empty
        # list rather than a 404, matching the availability endpoint.
        return (
            Cohort.objects.filter(
                level__track__organization=self.organization,
                level_id=level_id,
            )
            .open()
            .select_related("level", "level__track", "teacher")
        )


# --- Phase 5: the preferred-teacher waitlist ---------------------------------
#
# Entries are *created* by the routing endpoint above, never by a POST of their
# own: being on a waitlist is the outcome of asking for a teacher who was full,
# not something a client declares. So there is no create view here — only the two
# read views and the lead's promotion.


#: One query for the entry plus everything the read serializer touches.
WAITLIST_RELATED = ("student", "requested_teacher", "level", "level__track")


class MyWaitlistListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/waitlist/mine/ — the family's own entries and status.

    Open and fulfilled both, newest-priority-first per ``Meta.ordering``. A
    fulfilled entry stays visible because it is the record of a request that was
    honoured — "you asked for Ustadh in March and got the 4th of April" is the
    history the phase exists to keep.

    A **parent** reads their linked children's entries here as well as a student
    reading their own (product owner's call, 2026-08-25). ``/route/`` is
    ``IsStudentOrParent``, so a parent is one of the two people who can *create*
    an entry — and a requester who cannot then read their own request back is a
    hole, not a privacy boundary. Scoped through ``ParentLink`` exactly as
    ``BookingCancelView`` scopes cancellation, and for the same reason: a parent
    acts for their own children and nobody else's.

    Deliberately *not* matched by ``/api/pricing/agreements/mine/``, which stays
    student-only. A waitlist entry is scheduling; a negotiated rate is money, and
    who in a family may read one is a separate decision (tech-debt.md).
    """

    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudentOrParent]

    def get_queryset(self):
        user = self.request.user
        # distinct() because a student with two linked parents joins twice, which
        # would list their entry once per link.
        return (
            TeacherWaitlist.objects.in_organization(self.organization)
            .filter(
                Q(student=user) | Q(student__parent_links__parent=user)
            )
            .select_related(*WAITLIST_RELATED)
            .distinct()
        )


class TeacherWaitlistListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/waitlist/for-teacher/?teacher_id= — the queue to work.

    Open entries only, priority desc then longest-waiting, which comes from
    ``TeacherWaitlist.Meta.ordering`` rather than being re-stated here — the
    endpoint and any future automatic offer must agree about who is next.

    Management-only (owner/admin/lead teacher).
    """

    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Whose waitlist to list.",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        teacher_id = required_int_param(self.request, "teacher_id")
        # An unknown teacher is an empty list rather than a 404, matching the
        # availability and open-cohort endpoints.
        return TeacherWaitlist.open_for_teacher(
            teacher_id, organization=self.organization
        )


class WaitlistPromoteView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/scheduling/organizations/{id}/waitlist/{id}/promote/ — grant the request.

    Owner, admin, or lead teacher.
    """

    serializer_class = WaitlistPromoteSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_queryset(self):
        return TeacherWaitlist.objects.in_organization(
            self.organization
        ).select_related(*WAITLIST_RELATED)

    @extend_schema(
        request=WaitlistPromoteSerializer,
        responses={
            201: OpenApiResponse(
                response=BookingSerializer,
                description=(
                    "Booked. The entry keeps its original request and now carries "
                    "``fulfilled_booking``; it is not deleted."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "The teacher is no longer eligible for that slot — full, "
                    "outside their hours, or already booked. The entry stays open."
                )
            ),
            403: NOT_A_MEMBER,
            409: OpenApiResponse(description="Already fulfilled."),
        },
    )
    def post(self, request, *args, **kwargs):
        entry = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            routed = promote_waitlist_entry(
                entry,
                start_time_utc=serializer.validated_data.get("start_time_utc"),
                duration_minutes=serializer.validated_data.get("duration_minutes"),
                organization=self.organization,
            )
        except WaitlistEntryAlreadyFulfilled as exc:
            raise Conflict(str(exc)) from exc
        except DjangoValidationError as exc:
            # No longer eligible. A 400 carrying the model's own reason, so the
            # lead sees *why* the slot is gone rather than a bare failure.
            raise as_drf_error(exc) from exc

        body = BookingSerializer(
            routed.booking, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)
