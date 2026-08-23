"""API views for the scheduling app — the Phase 3 surface, nothing more.

Availability is read-only here, the same call Phases 1 and 2 made for teacher
profiles and curriculum data: a teacher's hours are maintained in the admin
(where ``Availability.create_from_local`` does the UTC conversion), and the API
only publishes them. A teacher-facing write endpoint is a deliberate gap, noted
in tech-debt.md.
"""

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .exceptions import BookingNotCancellable
from .models import Availability, Booking
from .permissions import IsStudent, IsStudentOrParent, IsTeacher
from .serializers import (
    AvailabilitySerializer,
    BookingCreateSerializer,
    BookingSerializer,
)


class Conflict(APIException):
    """409 — the request is valid but the booking is in the wrong state."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = "This booking cannot be cancelled."
    default_code = "not_cancellable"


#: One query for the booking plus everything the read serializer touches.
BOOKING_RELATED = ("student", "teacher", "level", "level__track")


class AvailabilityListView(generics.ListAPIView):
    """GET /api/scheduling/availability/?teacher_id= — public, one teacher's hours.

    ``teacher_id`` is required rather than optional: without it the honest
    behaviour would be dumping every teacher's calendar, which is a different
    endpoint than the one the spec asks for.
    """

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
        raw = self.request.query_params.get("teacher_id")
        if not raw:
            raise ValidationError({"teacher_id": ["This query parameter is required."]})
        try:
            teacher_id = int(raw)
        except (TypeError, ValueError):
            raise ValidationError({"teacher_id": ["Must be an integer."]})
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
