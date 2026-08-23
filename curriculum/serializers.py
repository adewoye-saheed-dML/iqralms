"""Serializers for the curriculum app.

Datetimes are stored in UTC and rendered in the *requesting* user's stored
timezone here at the serializer layer, per CLAUDE.md — a lead in one zone and a
student in another each read the same review stamp in their own local time.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.models import User
from accounts.utils import to_user_timezone

from .exceptions import TrackHasNoFirstLevel
from .models import Level, PlacementResult, Track


class LevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Level
        fields = ["id", "order", "name", "min_age", "group_eligible"]
        read_only_fields = fields


class TrackSerializer(serializers.ModelSerializer):
    """Public track listing. Levels come back in track order."""

    levels = LevelSerializer(many=True, read_only=True)

    class Meta:
        model = Track
        fields = ["id", "name", "slug", "levels"]
        read_only_fields = fields


class PlacementStudentSerializer(serializers.ModelSerializer):
    """The student as the review queue needs them — no signup_code, no sprawl."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "timezone", "is_minor"]
        read_only_fields = fields


class PlacementResultSerializer(serializers.ModelSerializer):
    """Read representation, shared by the submit, queue and 'mine' endpoints."""

    student = PlacementStudentSerializer(read_only=True)
    track = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    recommended_level = LevelSerializer(read_only=True)
    reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    reviewed_at_local = serializers.SerializerMethodField()

    class Meta:
        model = PlacementResult
        fields = [
            "id",
            "student",
            "track",
            "audio_sample",
            "skipped_as_beginner",
            "recommended_level",
            "reviewed_by",
            "reviewed_at",
            "reviewed_at_local",
            "status",
        ]
        read_only_fields = fields

    def get_reviewed_at_local(self, obj) -> str | None:
        """reviewed_at (stored UTC) in the requesting user's own timezone."""
        if obj.reviewed_at is None:
            return None
        request = self.context.get("request")
        tz_name = getattr(request.user, "timezone", None) if request else None
        local = to_user_timezone(obj.reviewed_at, tz_name or obj.student.timezone)
        return local.isoformat() if local else None


class PlacementSubmitSerializer(serializers.Serializer):
    """A student submits a recitation sample, or declares themselves a beginner.

    Exactly one of the two. Rejected at this layer with a 400 so the client gets
    a field error rather than the model's invariant surfacing as a 500.
    """

    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.all())
    audio_sample = serializers.FileField(required=False, allow_null=True)
    skipped_as_beginner = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        has_audio = bool(attrs.get("audio_sample"))
        skipped = attrs.get("skipped_as_beginner", False)
        if has_audio and skipped:
            raise serializers.ValidationError(
                "Provide either an audio sample or skipped_as_beginner, not both."
            )
        if not has_audio and not skipped:
            raise serializers.ValidationError(
                "Provide either an audio sample or skipped_as_beginner."
            )
        return attrs

    def create(self, validated_data):
        try:
            return PlacementResult.submit(
                student=self.context["request"].user,
                track=validated_data["track"],
                audio_sample=validated_data.get("audio_sample"),
                skipped_as_beginner=validated_data["skipped_as_beginner"],
            )
        except TrackHasNoFirstLevel as exc:
            raise serializers.ValidationError({"track": [str(exc)]}) from exc
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or {"detail": exc.messages}
            ) from exc


class PlacementReviewSerializer(serializers.Serializer):
    """The lead teacher sets the level; the model stamps who and when."""

    recommended_level = serializers.PrimaryKeyRelatedField(queryset=Level.objects.all())

    def validate_recommended_level(self, value):
        placement = self.instance
        if placement is not None and value.track_id != placement.track_id:
            raise serializers.ValidationError(
                "That level belongs to a different track than this placement."
            )
        return value

    def update(self, instance, validated_data):
        try:
            return instance.review(
                recommended_level=validated_data["recommended_level"],
                reviewed_by=self.context["request"].user,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                getattr(exc, "message_dict", None) or {"detail": exc.messages}
            ) from exc
