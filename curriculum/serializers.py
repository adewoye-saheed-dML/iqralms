"""Serializers for the curriculum app.

Datetimes are stored in UTC and rendered in the *requesting* user's stored
timezone here at the serializer layer, per CLAUDE.md — a lead in one zone and a
student in another each read the same review stamp in their own local time.

Phase 6 changed two things about placement audio:

* The read representation no longer carries a media URL. It used to serialise
  ``audio_sample`` as a path under ``MEDIA_URL``, which is exactly the permanent
  public URL to a minor's voice recording that the phase removes. Callers get
  ``has_audio_sample`` and the bare ``audio_filename`` instead, and ask
  ``.../placements/{id}/audio-url/`` for a short-lived signed URL when they
  actually need to play it.
* The submit serializer validates the upload. A recitation sample is untrusted
  input: size, extension, declared type and actual leading bytes are all checked
  before anything is persisted (``curriculum/validators.py``).

**SaaS Phase 3 added the write shapes, and one rule governs all of them: the
academy comes from the view, never from the request body.** Every serializer that
resolves a ``track``, a ``recommended_level`` or a ``user`` narrows its queryset to
``self.context["organization"]`` — the tenant the view has already verified the
caller into. That is what makes "Academy A route + Academy B track id" a
validation error rather than a cross-tenant write, and it is why no serializer here
accepts an ``organization`` field at all: a field a client can set is a field a
client can lie about.

None of this replaces the model's own checks. ``Track``, ``Level``,
``TeacherTrack`` and ``PlacementResult`` all validate inside ``save()``, so the
admin and a direct ORM write are held to the same rules — a serializer is where a
caller gets a readable 400, not where the invariant lives.
"""

import os

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from accounts.models import User
from accounts.utils import to_user_timezone
from organizations.models import OrganizationMembership

from .exceptions import TrackHasNoFirstLevel
from .models import Level, PlacementResult, TeacherTrack, Track
from .validators import (
    ALLOWED_AUDIO_EXTENSIONS,
    MAX_PLACEMENT_AUDIO_BYTES,
    validate_placement_audio,
)


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    The local copy every app in this repository keeps: four lines, and the apps
    are otherwise independent.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


class AcademyScopedSerializer(serializers.Serializer):
    """Base for the write shapes: relations resolve inside one academy only.

    ``scoped_querysets`` names the related fields to narrow and the queryset to
    narrow each to. It is applied in ``get_fields()`` rather than ``__init__``
    because the context — and therefore the organization — is attached to the
    serializer after construction, and a field whose queryset was built before the
    tenant was known would be a global lookup wearing a scoped name.

    The narrowing is the security boundary, not a convenience: a track id from
    another academy is *absent* from the field's queryset, so it comes back as
    "object does not exist" without the response ever confirming that the row is
    real somewhere else.

    Each scoped field is *declared* with an empty queryset, which is what makes the
    arrangement fail closed. Outside a request there is no organization in the
    context — drf-spectacular instantiates serializers bare to read their fields —
    and the empty queryset is left in place. If a view ever forgot to put the
    organization in its context, the result would be an endpoint that rejects every
    id, not one that accepts any.
    """

    #: ``{field_name: callable(organization) -> queryset}``
    scoped_querysets = {}

    @property
    def organization(self):
        """The tenant the view has verified the caller into, or ``None``."""
        return self.context.get("organization")

    def get_fields(self):
        fields = super().get_fields()
        organization = self.organization
        if organization is None:
            return fields
        for name, build in self.scoped_querysets.items():
            fields[name].queryset = build(organization)
        return fields


def tracks_in(organization):
    return Track.objects.filter(organization=organization)


def levels_in(organization):
    return Level.objects.filter(track__organization=organization)


# --- Read shapes -------------------------------------------------------------


class LevelSerializer(serializers.ModelSerializer):
    """One rung of a track's ladder.

    ``track`` is on the read shape because the academy-scoped level endpoints are
    flat — a client listing an academy's levels needs to know which track each one
    belongs to, and the level's academy is exactly its track's academy.
    """

    class Meta:
        model = Level
        fields = ["id", "track", "order", "name", "min_age", "group_eligible"]
        read_only_fields = fields


class TrackSerializer(serializers.ModelSerializer):
    """One academy's track, with its levels nested in order.

    ``organization`` travels in the body even though it is already in the URL. The
    future frontend holds tracks from several academies in one client — a teacher
    working for two of them — and a track that does not say whose it is has to be
    remembered rather than read.
    """

    levels = LevelSerializer(many=True, read_only=True)

    class Meta:
        model = Track
        fields = ["id", "organization", "name", "slug", "levels"]
        read_only_fields = fields


class TeacherTrackSerializer(serializers.ModelSerializer):
    """One teacher's eligibility for one track, inside one academy.

    ``user`` and ``organization`` are read off the membership rather than stored
    twice, which is the same shape ``accounts.OrganizationTeacherConfiguration``
    exposes and for the same reason: the membership is the only place the pair is
    written. ``username`` because an id does not identify a person to the human
    deciding what they may teach.
    """

    user = serializers.PrimaryKeyRelatedField(source="membership.user", read_only=True)
    username = serializers.CharField(source="membership.user.username", read_only=True)
    organization = serializers.PrimaryKeyRelatedField(
        source="membership.organization", read_only=True
    )
    track_slug = serializers.CharField(source="track.slug", read_only=True)

    class Meta:
        model = TeacherTrack
        fields = [
            "id",
            "membership",
            "user",
            "username",
            "organization",
            "track",
            "track_slug",
            "active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class PlacementStudentSerializer(serializers.ModelSerializer):
    """The student as the review queue needs them — no signup_code, no sprawl."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "timezone", "is_minor"]
        read_only_fields = fields


class PlacementResultSerializer(serializers.ModelSerializer):
    """Read representation, shared by the submit, queue, 'mine' and children endpoints.

    Carries no URL for the audio. ``has_audio_sample`` is what a client branches
    on, ``audio_filename`` is what it shows in a list, and a signed URL comes
    from the dedicated endpoint when someone actually presses play.

    ``organization`` is the placement's own academy, read through its track. It is
    the only academy identifier in the payload — the spec forbids exposing
    unrelated ones, and there is nowhere here for one to come from.
    """

    student = PlacementStudentSerializer(read_only=True)
    organization = serializers.SerializerMethodField()
    track = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    recommended_level = LevelSerializer(read_only=True)
    reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)
    reviewed_at_local = serializers.SerializerMethodField()
    has_audio_sample = serializers.SerializerMethodField()
    audio_filename = serializers.SerializerMethodField()

    class Meta:
        model = PlacementResult
        fields = [
            "id",
            "student",
            "organization",
            "track",
            "has_audio_sample",
            "audio_filename",
            "skipped_as_beginner",
            "recommended_level",
            "reviewed_by",
            "reviewed_at",
            "reviewed_at_local",
            "status",
        ]
        read_only_fields = fields

    def get_organization(self, obj) -> int:
        """The owning academy's id, without loading the organization row."""
        return obj.track.organization_id

    def get_reviewed_at_local(self, obj) -> str | None:
        """reviewed_at (stored UTC) in the requesting user's own timezone."""
        if obj.reviewed_at is None:
            return None
        request = self.context.get("request")
        tz_name = getattr(request.user, "timezone", None) if request else None
        local = to_user_timezone(obj.reviewed_at, tz_name or obj.student.timezone)
        return local.isoformat() if local else None

    def get_has_audio_sample(self, obj) -> bool:
        return bool(obj.audio_sample)

    def get_audio_filename(self, obj) -> str | None:
        """The sample's filename, without the storage path that holds it.

        The basename only. The full key is an implementation detail of the
        private bucket, and publishing it would put the one piece of the storage
        layout a client has no use for into every response.
        """
        if not obj.audio_sample:
            return None
        return os.path.basename(obj.audio_sample.name)


class PlacementAudioAccessSerializer(serializers.Serializer):
    """The response of the audio-access endpoint: a URL with an expiry.

    Serializer rather than a hand-built dict so the field types reach the
    OpenAPI schema — a client needs to know the URL stops working, and
    ``expires_at`` is the only thing telling it that.
    """

    url = serializers.URLField(
        read_only=True,
        help_text=(
            "Short-lived private access to the recitation sample. Treat it as a "
            "secret: whoever holds it can fetch the file until it expires."
        ),
    )
    expires_at = serializers.DateTimeField(
        read_only=True, help_text="UTC instant after which the URL stops working."
    )
    expires_in = serializers.IntegerField(
        read_only=True, help_text="Seconds of remaining validity when issued."
    )


# --- Track and level write shapes --------------------------------------------


class TrackWriteSerializer(serializers.Serializer):
    """Creating or renaming one academy's track.

    ``organization`` is absent by design, not omitted for brevity: the academy is
    the view's verified route context, so there is no field for a caller to point
    at a tenant they do not administer. Nor is it editable afterwards —
    ``Track.clean()`` refuses to move a track between academies, because that
    would carry its levels, placements and teacher assignments with it.

    Slug collisions are reported on the ``slug`` field rather than left to the
    database, and the check is scoped: ``tajweed`` may be taken *here* and free
    everywhere else. The model's ``(organization, slug)`` constraint is still the
    backstop.
    """

    name = serializers.CharField(max_length=80)
    slug = serializers.SlugField(max_length=80)

    def validate_slug(self, value):
        taken = Track.objects.filter(
            organization=self.context["organization"], slug=value
        )
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError(
                "This academy already has a track with that slug. The slug only "
                "has to be free within your own academy."
            )
        return value

    def validate(self, attrs):
        if self.instance is not None and not attrs:
            raise serializers.ValidationError(
                "Send 'name' or 'slug' — a track's academy is not editable."
            )
        return attrs

    def create(self, validated_data):
        track = Track(
            organization=self.context["organization"],
            name=validated_data["name"],
            slug=validated_data["slug"],
        )
        try:
            track.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return track

    def update(self, track, validated_data):
        for field in ("name", "slug"):
            if field in validated_data:
                setattr(track, field, validated_data[field])
        try:
            track.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return track


class LevelCreateSerializer(AcademyScopedSerializer):
    """Appending a level to one of this academy's tracks.

    ``order`` is deliberately not accepted. The Phase 2 rule is that levels are
    appended and never inserted, and a server that computes the next order cannot
    be argued with — there is no request that expresses "insert at 2", so the rule
    holds without having to reject anything. ``Level.next_order_for()`` is the same
    function the model validates against, so the two cannot disagree.

    ``track`` resolves inside this academy only, which is what rejects the phase
    spec's named attack: Academy A's route plus Academy B's track id.
    """

    scoped_querysets = {"track": tracks_in}

    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.none())
    name = serializers.CharField(max_length=80)
    min_age = serializers.IntegerField(
        min_value=1,
        required=False,
        allow_null=True,
        default=None,
        help_text="Informational only — not a hard gate on who may sit the level.",
    )
    group_eligible = serializers.BooleanField(required=False, default=False)

    def create(self, validated_data):
        track = validated_data["track"]
        level = Level(
            track=track,
            order=Level.next_order_for(track),
            name=validated_data["name"],
            min_age=validated_data["min_age"],
            group_eligible=validated_data["group_eligible"],
        )
        try:
            level.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return level


class LevelUpdateSerializer(serializers.Serializer):
    """Editing a level's description. Not its position, and not its track.

    ``order`` and ``track`` are both absent. Renumbering a ladder is a deliberate
    operation on the whole track rather than a field edit (``Level.clean()``
    refuses it), and moving a level to another track would move it to another
    academy — the same thing ``Track`` refuses for itself.
    """

    name = serializers.CharField(max_length=80, required=False)
    min_age = serializers.IntegerField(
        min_value=1, required=False, allow_null=True
    )
    group_eligible = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Send 'name', 'min_age' or 'group_eligible' — a level's order and "
                "track are not editable."
            )
        return attrs

    def update(self, level, validated_data):
        for field in ("name", "min_age", "group_eligible"):
            if field in validated_data:
                setattr(level, field, validated_data[field])
        try:
            level.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return level


# --- Teacher curriculum eligibility write shapes ------------------------------


class TeacherTrackCreateSerializer(AcademyScopedSerializer):
    """Giving one of this academy's teachers a track to teach.

    The caller names a ``user``, not a membership — the shape
    ``accounts.OrganizationTeacherConfigurationCreateSerializer`` established, and
    for the same reason: the membership is then looked up *inside* the view's
    verified organization, so a membership belonging to another academy is not
    something a request can express. An academy administrator therefore cannot
    reach a teacher's eligibility in a different academy even when they know both
    ids, which is the phase spec's cross-academy teacher requirement.

    ``track`` is narrowed to this academy for the same reason.
    """

    scoped_querysets = {"track": tracks_in}

    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.none())
    active = serializers.BooleanField(required=False, default=True)

    def validate_user(self, user):
        membership = OrganizationMembership.objects.filter(
            organization=self.organization, user=user
        ).first()
        if membership is None:
            # A statement about *this* academy, which the caller already
            # administers. It says nothing about any other tenant the user may
            # belong to, so it leaks nothing.
            raise serializers.ValidationError(
                "That user is not a member of this organization. Add the "
                "membership first."
            )
        if not user.is_teacher:
            # The model enforces this too, keyed on 'membership'; repeating it
            # here puts the error on the field the caller actually sent.
            raise serializers.ValidationError(
                "Only an account whose role is 'lead' or 'sub' can be assigned a "
                f"track (got '{user.role}')."
            )
        self._membership = membership
        return user

    def validate(self, attrs):
        membership = getattr(self, "_membership", None)
        if membership is not None and "track" in attrs:
            if TeacherTrack.objects.filter(
                membership=membership, track=attrs["track"]
            ).exists():
                # Friendlier than the unique constraint's message, and on the
                # fields the caller sent. The constraint still holds underneath.
                raise serializers.ValidationError(
                    "That teacher already has this track here. Change the "
                    "existing assignment instead of adding a second one."
                )
        return attrs

    def create(self, validated_data):
        assignment = TeacherTrack(
            membership=self._membership,
            track=validated_data["track"],
            active=validated_data["active"],
        )
        try:
            assignment.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return assignment


class TeacherTrackUpdateSerializer(serializers.Serializer):
    """Withdrawing or restoring a teacher's eligibility for a track.

    ``active`` only. Moving an assignment to a different teacher or a different
    track is not a change to a relationship, it is a different relationship — and
    the row is never deleted, so the record of what was once granted survives the
    way a suspended membership does.
    """

    active = serializers.BooleanField()

    def update(self, assignment, validated_data):
        assignment.active = validated_data["active"]
        try:
            assignment.save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return assignment


# --- Placement write shapes ---------------------------------------------------


class PlacementSubmitSerializer(AcademyScopedSerializer):
    """A student submits a recitation sample, or declares themselves a beginner.

    Exactly one of the two. Rejected at this layer with a 400 so the client gets
    a field error rather than the model's invariant surfacing as a 500 — which
    is the same reason the Phase 6 upload rules are attached to the field here
    rather than only enforced in the model.

    SaaS Phase 3 narrowed ``track`` to the academy in the URL. A student who
    belongs to two academies submits to each through its own route, and a track id
    from the academy they are *not* addressing is simply not in the queryset.
    """

    scoped_querysets = {"track": tracks_in}

    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.none())
    audio_sample = serializers.FileField(
        required=False,
        allow_null=True,
        validators=[validate_placement_audio],
        help_text=(
            f"Up to {MAX_PLACEMENT_AUDIO_BYTES // (1024 * 1024)} MB, one of: "
            f"{', '.join(ALLOWED_AUDIO_EXTENSIONS)}. The file's actual contents "
            "are checked, not just its name and declared type."
        ),
    )
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
            raise as_drf_error(exc) from exc


class PlacementReviewSerializer(AcademyScopedSerializer):
    """The lead teacher sets the level; the model stamps who and when.

    Two narrowings, one inside the other. ``recommended_level`` is restricted to
    levels of *this academy's* tracks, and then to this placement's own track — the
    first is the tenant boundary, the second is the Phase 2 product rule, and
    neither substitutes for the other.
    """

    scoped_querysets = {"recommended_level": levels_in}

    recommended_level = serializers.PrimaryKeyRelatedField(
        queryset=Level.objects.none()
    )

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
            raise as_drf_error(exc) from exc
