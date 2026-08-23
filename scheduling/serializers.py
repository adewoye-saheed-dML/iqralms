"""Serializers for the scheduling app.

Storage is UTC everywhere (CLAUDE.md); this layer is where a time becomes
something a human can read. Both read serializers therefore carry a ``local``
block alongside the UTC fields, rendered in the *requesting* user's stored
timezone — the same convention Phase 2 set with ``reviewed_at_local``. An
anonymous availability lookup has no requesting timezone to use, so it falls
back to the teacher's own.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import ParentLink, Role, User
from accounts.utils import to_user_timezone
from curriculum.models import Level
from curriculum.serializers import LevelSerializer

from .models import Availability, Booking, DEFAULT_DURATION_MINUTES


def viewer_timezone(context, fallback_user):
    """The zone to render in: the caller's own, else ``fallback_user``'s."""
    request = context.get("request")
    user = getattr(request, "user", None) if request else None
    if user is not None and user.is_authenticated:
        return user.timezone
    return fallback_user.timezone


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
    """Read representation, shared by create, both listings and cancel."""

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
    approved teacher — belongs to ``Booking.clean()`` and is surfaced from there,
    so the API and a direct ORM write cannot disagree. This serializer only
    resolves who the session is for.
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
        caller = self.context["request"].user
        student = attrs.get("student")

        if caller.role == Role.STUDENT:
            if student is not None and student != caller:
                raise serializers.ValidationError(
                    {"student": ["You can only book sessions for yourself."]}
                )
            attrs["student"] = caller
        else:
            # Permission classes have already established this is a parent.
            if student is None:
                raise serializers.ValidationError(
                    {"student": ["Required: which of your children this is for."]}
                )
            if not ParentLink.objects.filter(parent=caller, student=student).exists():
                raise serializers.ValidationError(
                    {"student": ["No linked student found for that id."]}
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
            # The model speaks in field names and NON_FIELD_ERRORS; hand that
            # through as a 400 rather than letting it surface as a 500.
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or {"detail": exc.messages}
            ) from exc
        return booking
