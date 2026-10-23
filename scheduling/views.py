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
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, serializers, status, views
from rest_framework.views import APIView
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response

from accounts.models import ParentLink, Role, User
from organizations.permissions import IsOrganizationMember, IsOwnerOrAdmin
from organizations.views import OrganizationScopedMixin

from .exceptions import (
    NoCapacity,
    WaitlistEntryAlreadyFulfilled,
)
from .models import (
    Availability,
    Booking,
    BookingStatus,
    ClassSessionRecording,
    Cohort,
    RoutedReason,
    StudentAvailability,
    TeacherWaitlist,
)
from .permissions import (
    CanBookSession,
    CanManageAvailability,
    IsLeadTeacher,
    IsOwnerAdminOrLeadTeacher,
    IsStudent,
    IsStudentOrParent,
    IsTeacher,
)
from .routing import promote_waitlist_entry, route_session
from .serializers import (
    AvailabilityCreateSerializer,
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingMeetingSerializer,
    BookingSerializer,
    ClassSessionRecordingSerializer,
    CohortCreateSerializer,
    CohortDetailSerializer,
    CohortSerializer,
    CohortStudentActionSerializer,
    CohortUpdateSerializer,
    MatchingTeacherSerializer,
    NoCapacitySerializer,
    RouteRequestSerializer,
    RoutedSerializer,
    ScheduleAgreementActionSerializer,
    StudentAvailabilityBatchCreateSerializer,
    StudentAvailabilitySerializer,
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


class AvailabilityListView(AcademyScopedView, generics.ListCreateAPIView):
    """GET /api/scheduling/organizations/{id}/availability/?teacher_id= — one teacher's hours.
    POST /api/scheduling/organizations/{id}/availability/ — declare availability windows.
    """

    serializer_class = AvailabilitySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Whose availability to list. Defaults to the requesting teacher.",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        raw_id = self.request.query_params.get("teacher_id")
        if raw_id:
            teacher_id = required_int_param(self.request, "teacher_id")
            return Availability.objects.filter(
                organization=self.organization, teacher_id=teacher_id
            ).select_related("teacher")

        if getattr(self.request.user, "is_teacher", False):
            teacher_id = self.request.user.id
            return Availability.objects.filter(
                organization=self.organization, teacher_id=teacher_id
            ).select_related("teacher")

        from organizations.permissions import is_owner_admin_or_lead_teacher
        if is_owner_admin_or_lead_teacher(self, self.request.user):
            return Availability.objects.filter(
                organization=self.organization
            ).select_related("teacher")

        teacher_id = required_int_param(self.request, "teacher_id")
        return Availability.objects.filter(
            organization=self.organization, teacher_id=teacher_id
        ).select_related("teacher")

    @extend_schema(
        request=AvailabilityCreateSerializer,
        responses={201: AvailabilitySerializer(many=True)},
    )
    def post(self, request, *args, **kwargs):
        user = request.user
        from organizations.permissions import is_teacher

        is_org_teacher = is_teacher(self, user)
        if not (getattr(user, "is_teacher", False) or is_org_teacher):
            raise PermissionDenied("Only teachers can declare their own availability hours.")

        teacher_id = request.data.get("teacher_id")
        if teacher_id and int(teacher_id) != user.id:
            raise PermissionDenied("You cannot declare availability hours for another teacher.")

        teacher = user

        # Auto-ensure teacher has an approved configuration in this academy and fallback teacher profile
        from accounts.models import OrganizationTeacherConfiguration, Role, TeacherProfile
        from organizations.models import MembershipStatus, OrganizationMembership
        membership = OrganizationMembership.objects.filter(
            organization=self.organization,
            user=teacher,
            status=MembershipStatus.ACTIVE,
        ).first()

        # If user is teacher in organization but global role was student/unconfigured, ensure teacher role
        if membership and not getattr(teacher, "is_teacher", False):
            teacher.role = Role.SUB
            teacher.save(update_fields=["role"])

        if membership:
            config, _ = OrganizationTeacherConfiguration.objects.get_or_create(
                membership=membership,
                defaults={"approved": True, "max_weekly_hours": 20, "hourly_payout_rate": 15.00},
            )
            if not config.approved:
                config.approved = True
                config.save(update_fields=["approved"])

        if getattr(teacher, "is_teacher", False) and not getattr(teacher, "teacher_profile", None):
            try:
                TeacherProfile.objects.get_or_create(
                    user=teacher,
                    defaults={
                        "is_lead": (teacher.role == Role.LEAD),
                        "approved": True,
                        "max_weekly_hours": 20,
                        "hourly_payout_rate": 15.00 if teacher.role == Role.SUB else None,
                    },
                )
            except Exception:
                pass

        raw_windows = request.data.get("windows")
        if raw_windows is None:
            raw_windows = [request.data]

        created = []
        for w in raw_windows:
            serializer = AvailabilityCreateSerializer(data=w)
            serializer.is_valid(raise_exception=True)
            weekday = serializer.validated_data["weekday"]
            start_time = serializer.validated_data["start_time"]
            end_time = serializer.validated_data["end_time"]

            try:
                rows = Availability.create_from_local(
                    organization=self.organization,
                    teacher=teacher,
                    weekday=weekday,
                    start_local=start_time,
                    end_local=end_time,
                    tz_name=teacher.timezone or "UTC",
                )
                created.extend(rows)
            except DjangoValidationError as exc:
                raise as_drf_error(exc) from exc

        return Response(
            AvailabilitySerializer(created, many=True, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class AvailabilityDetailView(AcademyScopedView, generics.DestroyAPIView):
    """DELETE /api/scheduling/organizations/{id}/availability/{id}/ — delete a declared window."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = AvailabilitySerializer

    def get_queryset(self):
        user = self.request.user
        # Availability hours can only be managed/deleted by the teacher themselves
        return Availability.objects.filter(organization=self.organization, teacher=user)



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
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudentOrParent]

    def get_queryset(self):
        user = self.request.user
        return (
            Booking.objects.filter(
                level__track__organization=self.organization,
            )
            .filter(
                Q(student=user) | Q(student__parent_links__parent=user)
            )
            .select_related(*BOOKING_RELATED)
            .distinct()
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


class BookingCompleteView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/scheduling/organizations/{id}/bookings/{id}/complete/ — teacher or staff marks session completed.

    This records the actual session duration, transitions status to 'completed', and makes
    the booking eligible for teacher payout generation and session assessment.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    def get_queryset(self):
        user = self.request.user
        membership = self.caller_membership
        from organizations.models import OrganizationRole

        is_staff = bool(
            membership
            and membership.role in {
                OrganizationRole.OWNER,
                OrganizationRole.ADMIN,
            }
        )
        qs = Booking.objects.filter(level__track__organization=self.organization)
        if not is_staff:
            qs = qs.filter(teacher=user)
        return qs.select_related(*BOOKING_RELATED).distinct()

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(response=BookingSerializer),
            403: NOT_A_MEMBER,
            404: OpenApiResponse(description="Booking not found or not your session."),
            409: OpenApiResponse(description="Already cancelled."),
        },
    )
    def post(self, request, *args, **kwargs):
        from .models import BookingStatus

        booking = self.get_object()
        if booking.status == BookingStatus.CANCELLED:
            raise Conflict("A cancelled booking cannot be marked completed.")

        duration_minutes = request.data.get("duration_minutes")
        if duration_minutes is not None:
            try:
                val = int(duration_minutes)
                if val > 0:
                    booking.duration_minutes = val
            except (ValueError, TypeError):
                pass

        booking.status = BookingStatus.COMPLETED
        booking.save()

        # Create or update session recording for owner reference with 60-day retention
        from datetime import timedelta
        from django.utils import timezone as dj_timezone
        try:
            ClassSessionRecording.objects.get_or_create(
                booking=booking,
                defaults={
                    "organization": self.organization,
                    "duration_minutes": booking.duration_minutes,
                    "video_room_name": booking.video_provider_meeting_id or "",
                    "recording_url": booking.video_join_url or "",
                    "recorded_at": dj_timezone.now(),
                    "expires_at": dj_timezone.now() + timedelta(days=60),
                    "status": "ready",
                    "metadata": {
                        "provider": booking.video_provider or "jitsi",
                        "teacher_username": booking.teacher.username,
                        "student_username": booking.student.username,
                        "track_name": booking.level.track.name,
                        "level_name": booking.level.name,
                    },
                },
            )
        except Exception:
            pass

        try:
            from audit_logs.services import record_event
            record_event(
                organization=self.organization,
                actor=request.user,
                action="booking.completed",
                target=booking,
                metadata={"duration_minutes": booking.duration_minutes},
            )
        except Exception:
            pass

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
        return CohortDetailSerializer

    def get_queryset(self):
        qs = (
            Cohort.objects.filter(level__track__organization=self.organization)
            .select_related("level", "level__track", "teacher")
            .prefetch_related("students", "seat_bookings")
        )
        level_id = self.request.query_params.get("level_id")
        if level_id:
            qs = qs.filter(level_id=level_id)
        track_id = self.request.query_params.get("track_id")
        if track_id:
            qs = qs.filter(level__track_id=track_id)
        teacher_id = self.request.query_params.get("teacher_id")
        if teacher_id:
            qs = qs.filter(teacher_id=teacher_id)
        return qs

    @extend_schema(
        responses={
            200: OpenApiResponse(response=CohortDetailSerializer(many=True)),
            201: OpenApiResponse(response=CohortDetailSerializer),
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
        try:
            from audit_logs.services import record_event
            from audit_logs.models import AuditAction
            record_event(
                organization=self.organization,
                actor=request.user,
                action=AuditAction.COHORT_CREATED,
                target=cohort,
                metadata={
                    "teacher_id": cohort.teacher_id,
                    "level_id": cohort.level_id,
                    "max_students": cohort.max_students,
                    "schedule_start_utc": cohort.schedule_start_utc.isoformat(),
                },
            )
        except Exception:
            pass
        body = CohortDetailSerializer(cohort, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class CohortDetailView(AcademyScopedView, generics.RetrieveUpdateDestroyAPIView):
    """GET /api/scheduling/organizations/{id}/cohorts/{pk}/ — retrieve a cohort.
    PATCH/PUT /api/scheduling/organizations/{id}/cohorts/{pk}/ — update cohort details.
    DELETE /api/scheduling/organizations/{id}/cohorts/{pk}/ — cancel cohort and seats.
    """

    serializer_class = CohortDetailSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_queryset(self):
        return (
            Cohort.objects.filter(level__track__organization=self.organization)
            .select_related("level", "level__track", "teacher")
            .prefetch_related("students", "seat_bookings")
        )

    def patch(self, request, *args, **kwargs):
        cohort = self.get_object()
        serializer = CohortUpdateSerializer(
            cohort,
            data=request.data,
            partial=True,
            context=self.get_serializer_context(),
        )
        serializer.is_valid(raise_exception=True)
        cohort = serializer.save()
        try:
            from audit_logs.services import record_event
            from audit_logs.models import AuditAction
            record_event(
                organization=self.organization,
                actor=request.user,
                action=AuditAction.COHORT_UPDATED,
                target=cohort,
                metadata={"updated_fields": list(request.data.keys())},
            )
        except Exception:
            pass
        return Response(
            CohortDetailSerializer(cohort, context=self.get_serializer_context()).data
        )

    def delete(self, request, *args, **kwargs):
        cohort = self.get_object()
        seats_count = cohort.seats_taken
        cohort.cancel(
            cancelled_by=request.user,
            reason="Cohort cancelled by academy administration",
        )
        try:
            from audit_logs.services import record_event
            from audit_logs.models import AuditAction
            record_event(
                organization=self.organization,
                actor=request.user,
                action=AuditAction.COHORT_CANCELLED,
                target=cohort,
                metadata={"seats_cancelled": seats_count},
            )
        except Exception:
            pass
        if not cohort.seat_bookings.exists():
            cohort.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CohortStudentManageView(AcademyScopedView, APIView):
    """POST /api/scheduling/organizations/{id}/cohorts/{pk}/students/ — add a student to cohort.
    DELETE /api/scheduling/organizations/{id}/cohorts/{pk}/students/<student_id>/ — remove student.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    @extend_schema(
        request=CohortStudentActionSerializer,
        responses={
            201: CohortDetailSerializer,
            400: OpenApiResponse(description="Invalid student role, not in academy, or cohort full"),
            404: OpenApiResponse(description="Cohort or student not found"),
        },
    )
    def post(self, request, pk, *args, **kwargs):
        cohort = get_object_or_404(
            Cohort.objects.filter(level__track__organization=self.organization),
            pk=pk,
        )
        student_id = request.data.get("student_id")
        if not student_id:
            return Response(
                {"detail": "student_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student = get_object_or_404(User, pk=student_id)
        if student.role != Role.STUDENT:
            return Response(
                {"detail": f"User is not a student (role: '{student.role}')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from accounts.tenancy import is_active_student_participant
        if not is_active_student_participant(user=student, organization=self.organization):
            return Response(
                {"detail": "Student is not an active participant in this academy."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not cohort.has_space:
            return Response(
                {"detail": f"Cohort is full: {cohort.seats_taken} of {cohort.max_students} seats taken."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if cohort.students.filter(pk=student.pk).exists():
            return Response(
                {"detail": "Student already enrolled in this cohort."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            duration = getattr(cohort.level, "duration_minutes", None) or 60
            booking = Booking.objects.create(
                student=student,
                teacher=cohort.teacher,
                level=cohort.level,
                start_time_utc=cohort.schedule_start_utc,
                duration_minutes=duration,
                cohort=cohort,
                status=BookingStatus.SCHEDULED,
                routed_reason=RoutedReason.COHORT_ASSIGNED,
            )
            cohort.add_student(student)

            try:
                from audit_logs.services import record_event
                from audit_logs.models import AuditAction
                record_event(
                    organization=self.organization,
                    actor=request.user,
                    action=AuditAction.COHORT_ASSIGNMENT_CHANGED,
                    target=cohort,
                    metadata={"action": "add_student", "student_id": student.id, "booking_id": booking.id},
                )
            except Exception:
                pass

        return Response(
            CohortDetailSerializer(cohort, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        responses={
            200: CohortDetailSerializer,
            400: OpenApiResponse(description="Student not enrolled in cohort or missing student_id"),
            404: OpenApiResponse(description="Cohort or student not found"),
        },
    )
    def delete(self, request, pk, student_id=None, *args, **kwargs):
        cohort = get_object_or_404(
            Cohort.objects.filter(level__track__organization=self.organization),
            pk=pk,
        )
        if not student_id:
            student_id = request.data.get("student_id") or request.query_params.get("student_id")
        if not student_id:
            return Response(
                {"detail": "student_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        student = get_object_or_404(User, pk=student_id)
        if not cohort.students.filter(pk=student.pk).exists():
            return Response(
                {"detail": "Student is not enrolled in this cohort."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            cohort.remove_student(
                student,
                cancelled_by=request.user,
                reason="Removed from cohort by academy administration",
            )
            try:
                from audit_logs.services import record_event
                from audit_logs.models import AuditAction
                record_event(
                    organization=self.organization,
                    actor=request.user,
                    action=AuditAction.COHORT_ASSIGNMENT_CHANGED,
                    target=cohort,
                    metadata={"action": "remove_student", "student_id": student.id},
                )
            except Exception:
                pass

        return Response(
            CohortDetailSerializer(cohort, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )


class CohortSelfEnrollView(AcademyScopedView, APIView):
    """POST /api/scheduling/organizations/{id}/cohorts/{pk}/enroll/ — student or parent enrolls in an open cohort."""

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudentOrParent]

    @extend_schema(
        request=CohortStudentActionSerializer,
        responses={
            201: OpenApiResponse(description="Successfully enrolled in group cohort session"),
            400: OpenApiResponse(description="Cohort is full or student not in academy"),
            403: OpenApiResponse(description="Permission denied"),
        },
    )
    def post(self, request, pk, *args, **kwargs):
        cohort = get_object_or_404(
            Cohort.objects.filter(level__track__organization=self.organization),
            pk=pk,
        )

        target_student = None
        if request.user.role == Role.STUDENT:
            target_student = request.user
        elif request.user.role == Role.PARENT:
            child_id = request.data.get("student_id")
            if not child_id:
                return Response(
                    {"detail": "student_id is required for parent enrollment."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            from accounts.models import ParentLink
            link_exists = ParentLink.objects.filter(
                parent=request.user,
                student_id=child_id,
            ).exists()
            if not link_exists:
                return Response(
                    {"detail": "You do not have a verified link to this student."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            target_student = get_object_or_404(User, pk=child_id)
        else:
            return Response(
                {"detail": "Only students or parents can enroll via this endpoint."},
                status=status.HTTP_403_FORBIDDEN,
            )

        from accounts.tenancy import is_active_student_participant
        if not is_active_student_participant(user=target_student, organization=self.organization):
            return Response(
                {"detail": "Student is not an active participant in this academy."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not cohort.has_space:
            return Response(
                {"detail": "This cohort is full. Please choose another group session."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if cohort.students.filter(pk=target_student.pk).exists():
            return Response(
                {"detail": "Student is already enrolled in this cohort."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            duration = getattr(cohort.level, "duration_minutes", None) or 60
            booking = Booking.objects.create(
                student=target_student,
                teacher=cohort.teacher,
                level=cohort.level,
                start_time_utc=cohort.schedule_start_utc,
                duration_minutes=duration,
                cohort=cohort,
                status=BookingStatus.SCHEDULED,
                routed_reason=RoutedReason.COHORT_ASSIGNED,
            )
            cohort.add_student(target_student)

            try:
                from audit_logs.services import record_event
                from audit_logs.models import AuditAction
                record_event(
                    organization=self.organization,
                    actor=request.user,
                    action=AuditAction.COHORT_ASSIGNMENT_CHANGED,
                    target=cohort,
                    metadata={"action": "self_enroll", "student_id": target_student.id, "booking_id": booking.id},
                )
            except Exception:
                pass

        return Response(
            {
                "message": "Successfully enrolled in group cohort session",
                "cohort": CohortSerializer(cohort, context={"request": request}).data,
                "booking_id": booking.id,
            },
            status=status.HTTP_201_CREATED,
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


@extend_schema(
    parameters=[
        OpenApiParameter(
            name="teacher_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Filter by requested teacher ID.",
        ),
    ],
)
class AcademyWaitlistListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/waitlist/ — all open student requests across academy."""

    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerAdminOrLeadTeacher]

    def get_queryset(self):
        qs = TeacherWaitlist.objects.in_organization(self.organization).open()
        teacher_id = self.request.query_params.get("teacher_id")
        if teacher_id:
            try:
                qs = qs.filter(requested_teacher_id=int(teacher_id))
            except (ValueError, TypeError):
                pass
        return qs.select_related(*WAITLIST_RELATED)


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

        teacher_id = serializer.validated_data.get("teacher_id")
        assigned_teacher = None
        if teacher_id:
            from accounts.models import User
            assigned_teacher = User.objects.filter(pk=teacher_id).first()
            if not assigned_teacher or not assigned_teacher.is_teacher:
                raise ValidationError({"teacher_id": ["Invalid teacher selected."]})

        try:
            routed = promote_waitlist_entry(
                entry,
                start_time_utc=serializer.validated_data.get("start_time_utc"),
                duration_minutes=serializer.validated_data.get("duration_minutes"),
                teacher=assigned_teacher,
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


class AcademySessionRecordingListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/scheduling/organizations/{id}/recordings/ — list session recordings for owner reference and audit.

    Accessible exclusively to academy owners and administrators.
    Recordings older than 60 days are excluded and automatically purged by retention policy.
    """

    serializer_class = ClassSessionRecordingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerOrAdmin]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="teacher_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter recordings by teacher ID.",
            ),
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter recordings by student ID.",
            ),
            OpenApiParameter(
                name="status",
                type=str,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by recording status (ready, processing, purged).",
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        qs = (
            ClassSessionRecording.objects.in_organization(self.organization)
            .active()
            .select_related(
                "booking__student",
                "booking__teacher",
                "booking__level__track",
            )
        )
        teacher_id = self.request.query_params.get("teacher_id")
        if teacher_id:
            try:
                qs = qs.filter(booking__teacher_id=int(teacher_id))
            except ValueError:
                pass

        student_id = self.request.query_params.get("student_id")
        if student_id:
            try:
                qs = qs.filter(booking__student_id=int(student_id))
            except ValueError:
                pass

        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        return qs


class AcademySessionRecordingDetailView(AcademyScopedView, generics.RetrieveDestroyAPIView):
    """GET /api/scheduling/organizations/{id}/recordings/{id}/ — retrieve session recording.
    DELETE /api/scheduling/organizations/{id}/recordings/{id}/ — owner purge of session recording.

    Owner/Admin only.
    """

    serializer_class = ClassSessionRecordingSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsOwnerOrAdmin]

    def get_queryset(self):
        return (
            ClassSessionRecording.objects.in_organization(self.organization)
            .select_related(
                "booking__student",
                "booking__teacher",
                "booking__level__track",
            )
        )

    def perform_destroy(self, instance):
        instance.delete()


class StudentAvailabilityListView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/scheduling/organizations/{id}/student-availability/ — list student availability windows.
    POST /api/scheduling/organizations/{id}/student-availability/ — set/replace student availability windows.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = StudentAvailabilitySerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Whose availability to list. Defaults to the requesting student.",
            )
        ],
        responses={200: StudentAvailabilitySerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        from accounts.models import ParentLink, Role
        from organizations.models import StudentEnrollment
        from organizations.permissions import is_owner_admin_or_lead_teacher

        caller = request.user
        raw_id = request.query_params.get("student_id")

        if raw_id:
            try:
                target_student_id = int(raw_id)
            except (ValueError, TypeError):
                raise ValidationError({"student_id": ["Must be an integer."]})
        elif getattr(caller, "role", None) == Role.STUDENT:
            target_student_id = caller.id
        else:
            target_student_id = None

        if target_student_id:
            # Check read permission for this student
            is_self = caller.id == target_student_id
            is_parent = ParentLink.objects.filter(parent=caller, student_id=target_student_id).exists()
            is_staff = is_owner_admin_or_lead_teacher(self, caller)
            is_assigned_teacher = StudentEnrollment.objects.filter(
                organization=self.organization,
                user_id=target_student_id,
                teacher=caller,
            ).exists()

            if not (is_self or is_parent or is_staff or is_assigned_teacher):
                raise PermissionDenied("You do not have permission to view this student's availability.")

            qs = StudentAvailability.objects.filter(
                organization=self.organization, student_id=target_student_id
            ).select_related("student", "created_by")
        else:
            # Listing without student_id:
            # If parent: list for all their linked children in this academy
            if getattr(caller, "role", None) == Role.PARENT:
                child_ids = ParentLink.objects.filter(parent=caller).values_list("student_id", flat=True)
                qs = StudentAvailability.objects.filter(
                    organization=self.organization, student_id__in=child_ids
                ).select_related("student", "created_by")
            elif is_owner_admin_or_lead_teacher(self, caller):
                qs = StudentAvailability.objects.filter(
                    organization=self.organization
                ).select_related("student", "created_by")
            elif getattr(caller, "is_teacher", False):
                student_ids = StudentEnrollment.objects.filter(
                    organization=self.organization, teacher=caller
                ).values_list("user_id", flat=True)
                qs = StudentAvailability.objects.filter(
                    organization=self.organization, student_id__in=student_ids
                ).select_related("student", "created_by")
            else:
                qs = StudentAvailability.objects.none()

        serializer = StudentAvailabilitySerializer(
            qs, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        request=StudentAvailabilityBatchCreateSerializer,
        responses={201: StudentAvailabilitySerializer(many=True)},
    )
    def post(self, request, *args, **kwargs):
        from accounts.models import ParentLink, Role, User
        from django.db import transaction
        from notifications.services import notify_student_availability_updated
        from organizations.models import ScheduleAgreementStatus, StudentEnrollment
        from organizations.permissions import is_owner_or_admin

        caller = request.user
        serializer = StudentAvailabilityBatchCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_student_id = serializer.validated_data.get("student_id")
        if not target_student_id:
            if getattr(caller, "role", None) == Role.STUDENT:
                target_student_id = caller.id
            else:
                raise ValidationError({"student_id": ["Target student_id is required."]})

        try:
            target_student = User.objects.get(id=target_student_id)
        except User.DoesNotExist:
            raise ValidationError({"student_id": ["Student user not found."]})

        # Permission check: solely for student or their parent for minors alone (owners/admins excluded)
        is_self = caller.id == target_student.id
        is_parent = ParentLink.objects.filter(parent=caller, student=target_student).exists()

        if getattr(target_student, "is_minor", False):
            if not is_parent:
                raise PermissionDenied("For minor students, weekly availability must be declared by their linked parent.")
        else:
            if not (is_self or is_parent):
                raise PermissionDenied("Student availability can only be declared directly by the student or their linked parent.")

        windows_data = serializer.validated_data.get("windows", [])
        tz_name = (
            serializer.validated_data.get("timezone")
            or getattr(target_student, "timezone", None)
            or getattr(caller, "timezone", None)
            or "UTC"
        )

        with transaction.atomic():
            # Replace existing availability windows for this student in this organization
            StudentAvailability.objects.filter(
                organization=self.organization, student=target_student
            ).delete()

            created = []
            for w in windows_data:
                rows = StudentAvailability.create_from_local(
                    organization=self.organization,
                    student=target_student,
                    created_by=caller,
                    weekday=w["weekday"],
                    start_local=w["start_time"],
                    end_local=w["end_time"],
                    tz_name=tz_name,
                )
                created.extend(rows)

            # Check if student is actively enrolled with an allocated teacher
            enrollments = StudentEnrollment.objects.filter(
                organization=self.organization,
                user=target_student,
                status="active",
                teacher__isnull=False,
            )
            if enrollments.exists():
                enrollments.update(
                    schedule_agreement_status=ScheduleAgreementStatus.PENDING,
                    schedule_agreement_notes="",
                    schedule_agreed_at=None,
                )
                notify_student_availability_updated(
                    student=target_student,
                    organization=self.organization,
                    actor=caller,
                )

        return Response(
            StudentAvailabilitySerializer(created, many=True, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )


class StudentAvailabilityDetailView(AcademyScopedView, generics.DestroyAPIView):
    """DELETE /api/scheduling/organizations/{id}/student-availability/{pk}/ — remove availability window."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = StudentAvailabilitySerializer

    def get_queryset(self):
        from accounts.models import ParentLink

        caller = self.request.user
        parent_child_ids = ParentLink.objects.filter(parent=caller).values_list("student_id", flat=True)
        return StudentAvailability.objects.filter(
            Q(organization=self.organization)
            & (Q(student=caller) | Q(student_id__in=parent_child_ids))
        )


class StudentAvailabilityAgreementView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/scheduling/organizations/{id}/student-availability/{student_id}/agree/ — teacher schedule agreement."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = ScheduleAgreementActionSerializer

    @extend_schema(
        request=ScheduleAgreementActionSerializer,
        responses={200: OpenApiResponse(description="Schedule agreement recorded.")},
    )
    def post(self, request, organization_pk=None, student_id=None, *args, **kwargs):
        from django.utils import timezone as dj_timezone
        from notifications.services import notify_schedule_agreement_response
        from organizations.models import EnrollmentStatus, ScheduleAgreementStatus, StudentEnrollment
        from organizations.permissions import is_owner_or_admin

        caller = request.user
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agreed = serializer.validated_data["agreed"]
        notes = serializer.validated_data.get("notes", "")

        qs = StudentEnrollment.objects.filter(
            organization=self.organization,
            status=EnrollmentStatus.ACTIVE,
        ).filter(
            Q(user_id=student_id) | Q(id=student_id)
        ).select_related("teacher", "track", "user")

        if not qs.exists():
            raise ValidationError({"student_id": ["Active student enrollment not found in this academy."]})

        is_admin = is_owner_or_admin(self, caller)
        enrollment = qs.filter(teacher=caller).first() or (qs.first() if is_admin else None)

        if not enrollment:
            raise PermissionDenied("Only the allocated teacher or academy leadership can respond to schedule agreements.")

        # Update all matching active enrollments for this student with this teacher (or the target enrollment for admin)
        target_enrollments = list(qs.filter(teacher=caller)) if enrollment.teacher_id == caller.id else [enrollment]
        for enr in target_enrollments:
            if agreed:
                enr.schedule_agreement_status = ScheduleAgreementStatus.AGREED
                enr.schedule_agreement_notes = notes
                enr.schedule_agreed_at = dj_timezone.now()
                enr.save(update_fields=["schedule_agreement_status", "schedule_agreement_notes", "schedule_agreed_at"])
            else:
                enr.schedule_agreement_status = ScheduleAgreementStatus.ALTERNATIVE_PROPOSED
                enr.schedule_agreement_notes = notes
                enr.save(update_fields=["schedule_agreement_status", "schedule_agreement_notes"])

        try:
            notify_schedule_agreement_response(
                enrollment=enrollment,
                teacher=caller,
                agreed=agreed,
                notes=notes,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Failed to notify schedule agreement response: %s", exc)

        from organizations.serializers import StudentDetailSerializer
        return Response(
            {
                "success": True,
                "agreed": agreed,
                "schedule_agreement_status": enrollment.schedule_agreement_status,
                "schedule_agreement_notes": enrollment.schedule_agreement_notes,
                "schedule_agreed_at": enrollment.schedule_agreed_at,
                "enrollment": StudentDetailSerializer(enrollment, context=self.get_serializer_context()).data,
            },
            status=status.HTTP_200_OK,
        )


class TeacherAvailabilityMatchView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/scheduling/organizations/{id}/matching-teachers/ — compare teacher and student availability."""

    permission_classes = [IsAuthenticated, IsOrganizationMember]
    serializer_class = MatchingTeacherSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Student user ID to match availability against.",
            ),
            OpenApiParameter(
                name="track_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Curriculum track ID (subject) to match qualified teachers. Defaults to student's enrolled track.",
            ),
        ],
        responses={200: MatchingTeacherSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        from accounts.models import OrganizationTeacherConfiguration, User
        from curriculum.models import TeacherTrack, Track
        from organizations.models import EnrollmentStatus, StudentEnrollment
        from organizations.permissions import is_owner_admin_or_lead_teacher

        if not is_owner_admin_or_lead_teacher(self, request.user):
            raise PermissionDenied("Only academy leadership can view teacher availability matching.")

        raw_student_id = request.query_params.get("student_id")
        if not raw_student_id:
            raise ValidationError({"student_id": ["student_id is required."]})
        try:
            student_id = int(raw_student_id)
        except (ValueError, TypeError):
            raise ValidationError({"student_id": ["Must be an integer."]})

        enrollment = StudentEnrollment.objects.filter(
            organization=self.organization,
            user_id=student_id,
        ).select_related("track", "teacher").first()

        raw_track_id = request.query_params.get("track_id")
        if raw_track_id:
            try:
                track_id = int(raw_track_id)
            except (ValueError, TypeError):
                raise ValidationError({"track_id": ["Must be an integer."]})
        elif enrollment and enrollment.track_id:
            track_id = enrollment.track_id
        else:
            track_id = None

        # Student weekly availability windows
        student_windows = list(
            StudentAvailability.objects.filter(
                organization=self.organization,
                student_id=student_id,
            ).order_by("weekday", "start_time_utc")
        )

        # Teachers eligible for track in this organization
        teacher_track_qs = TeacherTrack.objects.filter(
            membership__organization=self.organization,
            active=True,
        ).select_related("membership__user", "track")

        if track_id:
            teacher_track_qs = teacher_track_qs.filter(track_id=track_id)

        # Fetch candidate teachers
        eligible_teachers_map = {}
        for tt in teacher_track_qs:
            teacher_user = tt.membership.user
            eligible_teachers_map[teacher_user.id] = (teacher_user, tt.track)

        # If no track filter, also include active teachers in the academy
        if not track_id:
            from accounts.models import Role
            active_teachers = User.objects.filter(
                organization_memberships__organization=self.organization,
                role__in=[Role.SUB, Role.LEAD],
            ).distinct()
            for t in active_teachers:
                if t.id not in eligible_teachers_map:
                    eligible_teachers_map[t.id] = (t, getattr(enrollment, "track", None))

        results = []
        for teacher_id, (teacher_user, track_obj) in eligible_teachers_map.items():
            teacher_windows = list(
                Availability.objects.filter(
                    organization=self.organization,
                    teacher=teacher_user,
                ).order_by("weekday", "start_time_utc")
            )

            # Calculate overlapping windows
            overlapping = []
            total_overlap_min = 0

            for sw in student_windows:
                for tw in teacher_windows:
                    if sw.weekday == tw.weekday:
                        o_start = max(sw.start_time_utc, tw.start_time_utc)
                        o_end = min(sw.end_time_utc, tw.end_time_utc)
                        if o_start < o_end:
                            o_min = (o_end.hour * 60 + o_end.minute) - (o_start.hour * 60 + o_start.minute)
                            total_overlap_min += o_min
                            overlapping.append({
                                "weekday": sw.weekday,
                                "weekday_display": sw.get_weekday_display(),
                                "start_time_utc": o_start.strftime("%H:%M:%S"),
                                "end_time_utc": o_end.strftime("%H:%M:%S"),
                                "start_time": o_start.strftime("%H:%M:%S"),
                                "end_time": o_end.strftime("%H:%M:%S"),
                                "overlap_minutes": o_min,
                            })

            if total_overlap_min >= 240:
                match_quality = "high"
            elif total_overlap_min > 0:
                match_quality = "moderate"
            else:
                match_quality = "none"

            active_students_count = StudentEnrollment.objects.filter(
                organization=self.organization,
                teacher=teacher_user,
                status=EnrollmentStatus.ACTIVE,
            ).count()

            config = OrganizationTeacherConfiguration.objects.filter(
                membership__organization=self.organization,
                membership__user=teacher_user,
            ).first()
            max_hours = float(config.max_weekly_hours) if config and config.max_weekly_hours else None

            is_assigned = bool(enrollment and enrollment.teacher_id == teacher_user.id)
            teacher_name = teacher_user.get_full_name() or teacher_user.username

            results.append({
                "teacher_id": teacher_user.id,
                "teacher_name": teacher_name,
                "teacher_username": teacher_user.username,
                "is_currently_assigned": is_assigned,
                "track_id": track_obj.id if track_obj else 0,
                "track_name": track_obj.name if track_obj else "Unassigned",
                "match_quality": match_quality,
                "overlap_total_minutes": total_overlap_min,
                "overlap_total_hours": round(total_overlap_min / 60.0, 1),
                "overlapping_windows": overlapping,
                "teacher_windows": teacher_windows,
                "active_students_count": active_students_count,
                "max_weekly_hours": max_hours,
            })

        # Sort: Currently assigned first, then best overlap minutes descending
        results.sort(
            key=lambda x: (
                1 if x["is_currently_assigned"] else 0,
                x["overlap_total_minutes"],
            ),
            reverse=True,
        )

        serializer = MatchingTeacherSerializer(
            results, many=True, context=self.get_serializer_context()
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class DailyWebhookView(APIView):
    """POST /api/scheduling/webhooks/daily/
    
    Webhook receiver for Daily.co recording and room events.
    When a class recording finishes processing, Daily notifies this endpoint
    with 'recording.ready-to-download' and download_link, automatically updating
    the academy's ClassSessionRecording for owner review.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        event = payload.get("event") or payload.get("type", "")

        # Room name and download link can be at root or under 'recording'
        room_name = (
            payload.get("room_name")
            or payload.get("recording", {}).get("room_name")
            or payload.get("room", {}).get("name")
        )
        download_link = (
            payload.get("download_link")
            or payload.get("recording", {}).get("download_link")
            or payload.get("url")
            or payload.get("download_url")
        )
        duration_secs = (
            payload.get("duration")
            or payload.get("recording", {}).get("duration")
            or 0
        )

        if room_name and download_link:
            booking = Booking.objects.filter(video_provider_meeting_id=room_name).first()
            if booking:
                duration_minutes = (
                    max(1, round(duration_secs / 60))
                    if duration_secs
                    else booking.duration_minutes
                )
                from datetime import timedelta
                from django.utils import timezone as dj_timezone

                student_name = booking.student.get_full_name() or booking.student.username
                teacher_name = booking.teacher.get_full_name() or booking.teacher.username
                level_name = booking.level.name if booking.level else "Quran Lesson"

                ClassSessionRecording.objects.update_or_create(
                    video_room_name=room_name,
                    defaults={
                        "booking": booking,
                        "organization": booking.organization,
                        "title": f"{level_name} — Ustadh {teacher_name} with {student_name}",
                        "recording_url": download_link,
                        "duration_minutes": duration_minutes,
                        "recorded_at": dj_timezone.now(),
                        "expires_at": dj_timezone.now() + timedelta(days=60),
                        "status": "ready",
                        "metadata": payload,
                    },
                )
                return Response(
                    {"status": "ok", "message": f"Recording saved for room {room_name}"},
                    status=status.HTTP_200_OK,
                )

        return Response({"status": "ignored", "event": event}, status=status.HTTP_200_OK)

