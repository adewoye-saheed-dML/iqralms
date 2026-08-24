"""API views for the scheduling app — the Phase 3 and Phase 4 surface.

Availability is read-only here, the same call Phases 1 and 2 made for teacher
profiles and curriculum data: a teacher's hours are maintained in the admin
(where ``Availability.create_from_local`` does the UTC conversion), and the API
only publishes them. A teacher-facing write endpoint is a deliberate gap, noted
in tech-debt.md.

Phase 4 adds the routing endpoint, which is the one place in the API where the
*system* picks a teacher rather than a human doing it.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .exceptions import BookingNotCancellable, NoCapacity
from .models import Availability, Booking, Cohort
from .permissions import IsLeadTeacher, IsStudent, IsStudentOrParent, IsTeacher
from .routing import route_session
from .serializers import (
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingSerializer,
    CohortCreateSerializer,
    CohortSerializer,
    NoCapacitySerializer,
    RouteRequestSerializer,
    RoutedSerializer,
    as_drf_error,
)


class Conflict(APIException):
    """409 — the request is valid but the booking is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This booking cannot be cancelled."
    default_code = "not_cancellable"


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


class AvailabilityListView(generics.ListAPIView):
    """GET /api/scheduling/availability/?teacher_id= — public, one teacher's hours."""

    serializer_class = AvailabilitySerializer
    permission_classes = [AllowAny]

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
        # An unknown teacher is an empty list, not a 404: "when is this person
        # free" and "does this person exist" are different questions, and only
        # the first is public.
        return Availability.objects.filter(teacher_id=teacher_id).select_related(
            "teacher"
        )


class BookingCreateView(generics.CreateAPIView):
    """POST /api/scheduling/bookings/ — a student, or their parent, books a slot.

    Validation of the slot itself lives in ``Booking.clean()``; a rejection
    arrives here as a 400 carrying the model's own message.
    """

    serializer_class = BookingCreateSerializer
    permission_classes = [IsAuthenticated, IsStudentOrParent]

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
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        body = BookingSerializer(booking, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class MyBookingListView(generics.ListAPIView):
    """GET /api/scheduling/bookings/mine/ — the student's own sessions.

    Past and upcoming, every status, earliest first. Cancelled ones stay
    visible: they are history, not noise.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsStudent]

    def get_queryset(self):
        return Booking.objects.filter(student=self.request.user).select_related(
            *BOOKING_RELATED
        )


class TeachingBookingListView(generics.ListAPIView):
    """GET /api/scheduling/bookings/teaching/ — sessions this teacher teaches."""

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated, IsTeacher]

    def get_queryset(self):
        return Booking.objects.filter(teacher=self.request.user).select_related(
            *BOOKING_RELATED
        )


class BookingCancelView(generics.GenericAPIView):
    """POST /api/scheduling/bookings/{id}/cancel/ — either party cancels.

    The row is kept and its status set; nothing is deleted, because attendance
    and payouts need the history later.

    The queryset is scoped to bookings the caller is a party to — the student,
    the teacher, or a parent linked to the student (a parent may book, so a
    parent may cancel). Anyone else gets a 404 rather than a 403, so this
    endpoint cannot be used to discover that a booking exists.
    """

    serializer_class = BookingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return (
            Booking.objects.filter(
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
            404: OpenApiResponse(description="No such booking for this caller."),
            409: OpenApiResponse(
                description="Already cancelled, or completed and now history."
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        booking = self.get_object()
        try:
            booking.cancel()
        except BookingNotCancellable as exc:
            raise Conflict(str(exc)) from exc
        body = BookingSerializer(booking, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_200_OK)


# --- Phase 4: routing and cohorts -------------------------------------------


class RouteView(generics.GenericAPIView):
    """POST /api/scheduling/route/ — the system picks who teaches.

    Cohort first, then the lead teacher if they have capacity, then a matched
    sub-teacher. The algorithm itself is in ``routing.py``; this view only
    translates its two outcomes into HTTP.

    A refusal is a **409**, not a 200 with ``routed: false`` and not a 400. The
    request was valid and was processed — what stands in the way is the state of
    the academy's capacity, which is precisely what 409 means, and it is the code
    Phase 3's cancel endpoint already uses for "valid request, wrong state". The
    body is structured either way, so a frontend can explain the refusal instead
    of showing a bare error, which is what the spec asks for.
    """

    serializer_class = RouteRequestSerializer
    permission_classes = [IsAuthenticated, IsStudentOrParent]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=RoutedSerializer,
                description=(
                    "Assigned. ``routed_reason`` says how: cohort_assigned, "
                    "lead_available or lead_full_routed. A cohort seat's start "
                    "time is the cohort's own, which may differ from the "
                    "requested one."
                ),
            ),
            400: OpenApiResponse(
                description=(
                    "The request itself is wrong: an unknown level, a parent not "
                    "naming a linked child, or a slot the assigned teacher lost "
                    "to a concurrent booking."
                )
            ),
            409: OpenApiResponse(
                response=NoCapacitySerializer,
                description=(
                    "No capacity anywhere. Carries why each step declined; "
                    "nobody is booked outside their hours or over their cap to "
                    "avoid this."
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
            # Same 400 a direct booking gets for the same reason.
            raise as_drf_error(exc) from exc

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


class CohortCreateView(generics.CreateAPIView):
    """POST /api/scheduling/cohorts/ — the lead opens a group class.

    Lead-only: a cohort commits a teacher's time, so it is not something a
    sub-teacher grants themselves. Whether the level may run as a group at all,
    and whether the teacher teaches its track, are ``Cohort.clean()``'s calls and
    arrive here as 400s.
    """

    serializer_class = CohortCreateSerializer
    permission_classes = [IsAuthenticated, IsLeadTeacher]

    @extend_schema(
        responses={
            201: OpenApiResponse(response=CohortSerializer),
            400: OpenApiResponse(
                description=(
                    "Level is not group-eligible, the teacher is unapproved, or "
                    "the teacher does not teach the level's track."
                )
            ),
            403: OpenApiResponse(description="Not the lead teacher."),
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cohort = serializer.save()
        body = CohortSerializer(cohort, context=self.get_serializer_context()).data
        return Response(body, status=status.HTTP_201_CREATED)


class OpenCohortListView(generics.ListAPIView):
    """GET /api/scheduling/cohorts/open/?level_id= — cohorts with a seat free.

    The same queryset routing's step 1 reads, exposed so a "browse open cohorts"
    view can be built on it later without a second definition of "open" existing.
    Seat counts are published; the roster is not (see ``CohortSerializer``).
    """

    serializer_class = CohortSerializer
    permission_classes = [IsAuthenticated]

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
        # An unknown level is an empty list rather than a 404, matching the
        # availability endpoint: "what is open" and "does this level exist" are
        # separate questions.
        return Cohort.open_for_level(level_id)
