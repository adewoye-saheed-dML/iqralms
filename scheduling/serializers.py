"""Serializers for the scheduling app.

Storage is UTC everywhere (CLAUDE.md); this layer is where a time becomes
something a human can read. Read serializers therefore carry a ``local`` block or
a ``*_local`` field alongside the UTC ones, rendered in the *requesting* user's
stored timezone — the same convention Phase 2 set with ``reviewed_at_local``. An
anonymous availability lookup has no requesting timezone to use, so it falls
back to the teacher's own.

Whether a slot is *legal* is never decided here. Declared hours, overlap, an
approved teacher, the track specialty and the weekly cap all live in
``Booking.clean()`` and are surfaced from there, so the API and a direct ORM
write cannot disagree about what a valid booking is.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import ParentLink, Role, User
from accounts.utils import to_user_timezone
from curriculum.models import Level
from curriculum.serializers import LevelSerializer

from .models import (
    Availability,
    Booking,
    Cohort,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_MAX_STUDENTS,
)


def viewer_timezone(context, fallback_user):
    """The zone to render in: the caller's own, else ``fallback_user``'s."""
    request = context.get("request")
    user = getattr(request, "user", None) if request else None
    if user is not None and user.is_authenticated:
        return user.timezone
    return fallback_user.timezone


def resolve_requested_student(caller, student):
    """Which student a request is for, given the caller and an optional id.

    Shared by direct booking and routing so the two cannot drift: a student acts
    only for themselves, and a parent must name one of their linked children.
    Permission classes have already established the caller is one or the other.
    """
    if caller.role == Role.STUDENT:
        if student is not None and student != caller:
            raise serializers.ValidationError(
                {"student": ["You can only book sessions for yourself."]}
            )
        return caller
    if student is None:
        raise serializers.ValidationError(
            {"student": ["Required: which of your children this is for."]}
        )
    if not ParentLink.objects.filter(parent=caller, student=student).exists():
        raise serializers.ValidationError(
            {"student": ["No linked student found for that id."]}
        )
    return student


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    The model speaks in field names and ``NON_FIELD_ERRORS``; handing that
    through keeps the model's own wording rather than flattening it into a 500.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class BookingPartySerializer(serializers.ModelSerializer):
    """A person on one side of a booking. No signup_code, no sprawl."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "role", "timezone"]
        read_only_fields = fields


class LocalWindowSerializer(serializers.Serializer):
    """A weekly window as it reads in one particular timezone."""

    timezone = serializers.CharField()
    weekday = serializers.IntegerField(
        help_text="May differ from the UTC weekday — a conversion can cross midnight."
    )
    start_time = serializers.TimeField()
    end_time = serializers.TimeField()


class AvailabilitySerializer(serializers.ModelSerializer):
    """Read representation of one declared window. Public endpoint."""

    teacher_username = serializers.CharField(source="teacher.username", read_only=True)
    weekday_display = serializers.CharField(source="get_weekday_display", read_only=True)
    local = serializers.SerializerMethodField()

    class Meta:
        model = Availability
        fields = [
            "id",
            "teacher",
            "teacher_username",
            "weekday",
            "weekday_display",
            "start_time_utc",
            "end_time_utc",
            "local",
        ]
        read_only_fields = fields

    @extend_schema_field(LocalWindowSerializer)
    def get_local(self, obj) -> dict:
        tz_name = viewer_timezone(self.context, obj.teacher)
        weekday, start, end = obj.local_window(tz_name)
        return {
            "timezone": tz_name,
            "weekday": weekday,
            "start_time": start,
            "end_time": end,
        }


class BookingSerializer(serializers.ModelSerializer):
    """Read representation, shared by create, both listings, cancel and routing."""

    student = BookingPartySerializer(read_only=True)
    teacher = BookingPartySerializer(read_only=True)
    level = LevelSerializer(read_only=True)
    track = serializers.SlugRelatedField(
        source="level.track", slug_field="slug", read_only=True
    )
    end_time_utc = serializers.DateTimeField(read_only=True)
    start_time_local = serializers.SerializerMethodField()
    video_join_url = serializers.CharField(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "id",
            "student",
            "teacher",
            "level",
            "track",
            "cohort",
            "routed_reason",
            "start_time_utc",
            "start_time_local",
            "end_time_utc",
            "duration_minutes",
            "status",
            "video_room_name",
            "video_join_url",
        ]
        read_only_fields = fields

    def get_start_time_local(self, obj) -> str | None:
        """start_time_utc in the caller's own zone, so nobody does the maths."""
        local = to_user_timezone(
            obj.start_time_utc, viewer_timezone(self.context, obj.student)
        )
        return local.isoformat() if local else None


class BookingCreateSerializer(serializers.Serializer):
    """A student books themselves, or a parent books a linked child.

    Everything about *whether* the slot is legal — declared hours, overlap, an
    approved teacher, the track specialty, the weekly cap — belongs to
    ``Booking.clean()`` and is surfaced from there, so the API and a direct ORM
    write cannot disagree. This serializer only resolves who the session is for.

    ``routed_reason`` is not a client field: naming your own teacher *is*
    ``student_choice``, which the model already defaults to. Phase 4's other
    three reasons are the routing engine's to record.
    """

    teacher = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())
    start_time_utc = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(
        required=False, default=DEFAULT_DURATION_MINUTES, min_value=1
    )
    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=Role.STUDENT),
        required=False,
        help_text="Required when a parent books; ignored when a student books.",
    )

    def validate(self, attrs):
        attrs["student"] = resolve_requested_student(
            self.context["request"].user, attrs.get("student")
        )
        return attrs

    def create(self, validated_data):
        booking = Booking(
            student=validated_data["student"],
            teacher=validated_data["teacher"],
            level=validated_data["level"],
            start_time_utc=validated_data["start_time_utc"],
            duration_minutes=validated_data["duration_minutes"],
        )
        try:
            booking.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return booking


class CohortSerializer(serializers.ModelSerializer):
    """Read representation of a group class.

    Seat *counts* are published; the seated students are not. Who else is in a
    class is not something the open-cohorts endpoint needs to tell every
    authenticated caller, and a roster is a different endpoint's decision to make.
    """

    teacher = BookingPartySerializer(read_only=True)
    level = LevelSerializer(read_only=True)
    track = serializers.SlugRelatedField(
        source="level.track", slug_field="slug", read_only=True
    )
    seats_taken = serializers.IntegerField(read_only=True)
    seats_available = serializers.IntegerField(read_only=True)
    schedule_start_local = serializers.SerializerMethodField()

    class Meta:
        model = Cohort
        fields = [
            "id",
            "teacher",
            "level",
            "track",
            "max_students",
            "seats_taken",
            "seats_available",
            "schedule_start_utc",
            "schedule_start_local",
        ]
        read_only_fields = fields

    def get_schedule_start_local(self, obj) -> str | None:
        """The start in the caller's zone, falling back to the teacher's."""
        local = to_user_timezone(
            obj.schedule_start_utc, viewer_timezone(self.context, obj.teacher)
        )
        return local.isoformat() if local else None


class CohortCreateSerializer(serializers.ModelSerializer):
    """The lead teacher opens a group class.

    ``students`` is deliberately not writable: seats are given by routing or by
    ``Cohort.add_student``, both of which create the seat's ``Booking`` too. A
    cohort that could be created with students already in it would have members
    holding no session.
    """

    class Meta:
        model = Cohort
        fields = ["teacher", "level", "max_students", "schedule_start_utc"]
        extra_kwargs = {
            "max_students": {"required": False, "default": DEFAULT_MAX_STUDENTS}
        }

    def create(self, validated_data):
        cohort = Cohort(**validated_data)
        try:
            cohort.save()
        except DjangoValidationError as exc:
            # group_eligible=False, an unapproved teacher, or a teacher who does
            # not teach the level's track — all model rules, surfaced as 400s.
            raise as_drf_error(exc) from exc
        return cohort


class TimeWindowSerializer(serializers.Serializer):
    """The slot a requested session would occupy: when it starts, how long it runs.

    The Phase 4 spec names the field ``requested_time_window`` without defining
    its shape. This is the exact-slot reading (product owner's call, 2026-08-24):
    routing decides *who* teaches, never *when*, so a student is never quietly
    moved to a different time than the one they asked for. The single exception is
    a cohort, whose own ``schedule_start_utc`` wins within
    ``COHORT_START_TOLERANCE`` — and the response says so, in ``routed_reason``.
    """

    start_time_utc = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(
        required=False, default=DEFAULT_DURATION_MINUTES, min_value=1
    )


class RouteRequestSerializer(serializers.Serializer):
    """``POST /route/`` — let the system decide who teaches this."""

    level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())
    requested_time_window = TimeWindowSerializer()
    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=Role.STUDENT),
        required=False,
        help_text="Required when a parent requests; ignored when a student does.",
    )

    def validate(self, attrs):
        attrs["student"] = resolve_requested_student(
            self.context["request"].user, attrs.get("student")
        )
        return attrs


class RoutedSerializer(serializers.Serializer):
    """What routing decided. Read-only; assembled from a ``routing.Routed``."""

    routed = serializers.BooleanField()
    routed_reason = serializers.CharField()
    booking = BookingSerializer()
    cohort = CohortSerializer(allow_null=True)


class NoCapacitySerializer(serializers.Serializer):
    """The honest failure. Documented as a schema so a client can rely on it."""

    routed = serializers.BooleanField()
    reason = serializers.CharField()
    detail = serializers.CharField()
    considered = serializers.DictField(
        help_text=(
            "Why each step declined, keyed by step: 'cohort', 'lead', "
            "'sub_teachers'. Present so the frontend can explain the refusal "
            "rather than showing a bare error."
        )
    )
