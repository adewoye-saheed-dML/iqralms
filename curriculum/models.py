"""Curriculum: what can be taught, and where a new student starts.

Field sets mirror specs/phase-2-curriculum.md exactly. Two spec questions were
resolved with the product owner before this was written (see learnings.md):

* ``status`` is a stored field, not derived from ``reviewed_by`` — a beginner
  skip is ``reviewed`` with ``reviewed_by`` null, so ``reviewed_by`` cannot be
  the signal. ``save()`` always recomputes it from ``reviewed_at``, so the
  stored value cannot drift away from the review state.
* "No gaps in ``Level.order``" is enforced as append-only: a new level's order
  must be the track's current max + 1.

Only the lead teacher may review placements for now — sub-teachers are
excluded, so ``reviewed_by`` is validated against role ``lead``.

Nothing about bookings, cohorts or session assessment belongs here.
"""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone as dj_timezone

from accounts.models import Role, User

from .exceptions import PlacementAlreadyReviewed, TrackHasNoFirstLevel

#: The first level of any track. A beginner skip places the student here.
FIRST_LEVEL_ORDER = 1


class Status(models.TextChoices):
    PENDING = "pending", "Pending review"
    REVIEWED = "reviewed", "Reviewed"


def placement_audio_path(instance, filename):
    """Keep each student's placement samples together, one dir per student."""
    return f"placements/{instance.student_id}/{filename}"


class Track(models.Model):
    """A subject that can be taught, e.g. Tajweed, Hifz, Arabic."""

    name = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Level(models.Model):
    """A position within a track's sequence. Phase 3 hangs bookings off these."""

    track = models.ForeignKey(Track, on_delete=models.CASCADE, related_name="levels")
    order = models.PositiveIntegerField(
        validators=[MinValueValidator(FIRST_LEVEL_ORDER)],
        help_text="Sequence within the track, 1 = first. Appended, never inserted.",
    )
    name = models.CharField(max_length=80)
    min_age = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            "Informational only in this phase — not a hard gate. A 10-year-old "
            "and a 40-year-old can both sit at Beginner Arabic."
        ),
    )
    group_eligible = models.BooleanField(
        default=False,
        help_text=(
            "Whether this level may run as a cohort. Cohorts arrive in Phase 4; "
            "the data is captured now so routing has something to read."
        ),
    )

    class Meta:
        ordering = ["track", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["track", "order"],
                name="unique_level_order_per_track",
            )
        ]

    @classmethod
    def next_order_for(cls, track) -> int:
        """The only order a new level in ``track`` may take."""
        track_id = getattr(track, "pk", track)
        current_max = cls.objects.filter(track_id=track_id).aggregate(
            models.Max("order")
        )["order__max"]
        return FIRST_LEVEL_ORDER if current_max is None else current_max + 1

    def clean(self):
        errors = {}
        if self.order is not None and self.track_id:
            if self._state.adding:
                # Gaps cannot be expressed as a DB constraint (the rule reads
                # sibling rows), so it is enforced here: append only.
                expected = self.next_order_for(self.track_id)
                if self.order != expected:
                    errors["order"] = ValidationError(
                        "Levels are appended, not inserted: the next order for "
                        "this track is %(expected)d, got %(got)d.",
                        code="non_contiguous_order",
                        params={"expected": expected, "got": self.order},
                    )
            else:
                stored = (
                    type(self)
                    .objects.filter(pk=self.pk)
                    .values_list("order", flat=True)
                    .first()
                )
                if stored is not None and self.order != stored:
                    errors["order"] = ValidationError(
                        "An existing level's order cannot be changed (%(stored)d "
                        "-> %(got)d); renumbering a track is a deliberate "
                        "operation, not a field edit.",
                        code="order_immutable",
                        params={"stored": stored, "got": self.order},
                    )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Contiguity spans sibling rows, so validating in save() is what makes
        # the rule hold for the admin and direct ORM writes as well as the API.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.track.name} {self.order}. {self.name}"


class PlacementResult(models.Model):
    """One student's starting point in one track.

    A student holds at most one row per track: re-submitting updates it back to
    pending rather than stacking a second request (see ``submit``).
    """

    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="placement_results",
    )
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="placement_results",
    )
    audio_sample = models.FileField(
        upload_to=placement_audio_path,
        null=True,
        blank=True,
        help_text="Recitation sample. Empty when the student skipped as beginner.",
    )
    skipped_as_beginner = models.BooleanField(
        default=False,
        help_text=(
            "Student declared themselves a complete beginner instead of "
            "recording. Mutually exclusive with audio_sample."
        ),
    )
    recommended_level = models.ForeignKey(
        Level,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="placements",
        help_text="Null until reviewed. Must belong to this placement's track.",
    )
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="placement_reviews",
        help_text=(
            "The lead teacher who reviewed. Stays null for a beginner skip — "
            "that placement is a system decision, not a teacher's."
        ),
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        help_text="Recomputed from reviewed_at on every save(); never set by a client.",
    )

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "track"],
                name="unique_placement_per_student_track",
            ),
            # DB backstop for the audio/skip invariant. clean() raises a better
            # message first; this catches anything that bypasses validation.
            models.CheckConstraint(
                condition=(
                    (
                        Q(skipped_as_beginner=True)
                        & (Q(audio_sample__isnull=True) | Q(audio_sample=""))
                    )
                    | (
                        Q(skipped_as_beginner=False)
                        & Q(audio_sample__isnull=False)
                        & ~Q(audio_sample="")
                    )
                ),
                name="placement_audio_xor_beginner_skip",
                violation_error_message=(
                    "Provide exactly one of audio_sample or skipped_as_beginner."
                ),
            ),
        ]

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def submit(cls, *, student, track, audio_sample=None, skipped_as_beginner=False):
        """Create or replace this student's placement request for ``track``.

        A second request for a track the student already has a row for updates
        that row and resets it to pending, per the spec — it never creates a
        duplicate. Any previous review is discarded, because the sample it was
        based on is gone.
        """
        placement = cls.objects.filter(student=student, track=track).first()
        if placement is None:
            placement = cls(student=student, track=track)

        # Normalise '' to None so "no audio" has one representation.
        placement.audio_sample = audio_sample or None
        placement.skipped_as_beginner = bool(skipped_as_beginner)
        placement.recommended_level = None
        placement.reviewed_by = None
        placement.reviewed_at = None
        placement.save()
        return placement

    def review(self, *, recommended_level, reviewed_by):
        """Stamp a lead teacher's review onto a pending placement."""
        if self.status == Status.REVIEWED:
            raise PlacementAlreadyReviewed(
                "This placement has already been reviewed."
            )
        self.recommended_level = recommended_level
        self.reviewed_by = reviewed_by
        self.reviewed_at = dj_timezone.now()
        self.save()
        return self

    def _apply_beginner_skip(self):
        """Auto-place a self-declared beginner into the track's first level.

        No human review: the level is implied by the student's own declaration,
        so this is stamped immediately with ``reviewed_by`` left null.
        """
        if not self.skipped_as_beginner or self.audio_sample:
            # Not a skip, or a both-set submission that clean() will reject.
            return
        if self.recommended_level_id is None:
            first = Level.objects.filter(
                track_id=self.track_id, order=FIRST_LEVEL_ORDER
            ).first()
            if first is None:
                raise TrackHasNoFirstLevel(
                    "Track has no level at order 1, so a beginner cannot be "
                    "placed into it yet."
                )
            self.recommended_level = first
        if self.reviewed_at is None:
            self.reviewed_at = dj_timezone.now()

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "Only a user with role 'student' can have a placement result "
                "(got '%(role)s').",
                code="invalid_student_role",
                params={"role": self.student.role},
            )

        has_audio = bool(self.audio_sample)
        if has_audio and self.skipped_as_beginner:
            errors[NON_FIELD_ERRORS] = ValidationError(
                "Provide either an audio sample or skipped_as_beginner, not both.",
                code="audio_and_skip",
            )
        elif not has_audio and not self.skipped_as_beginner:
            errors[NON_FIELD_ERRORS] = ValidationError(
                "Provide either an audio sample or skipped_as_beginner.",
                code="audio_or_skip_required",
            )

        if self.reviewed_by_id:
            if self.skipped_as_beginner:
                errors["reviewed_by"] = ValidationError(
                    "A beginner skip is a system decision; reviewed_by must "
                    "stay null.",
                    code="skip_has_reviewer",
                )
            elif self.reviewed_by.role != Role.LEAD:
                # Phase 2 decision: lead only, sub-teachers excluded for now.
                errors["reviewed_by"] = ValidationError(
                    "Only the lead teacher can review placements (got "
                    "'%(role)s').",
                    code="invalid_reviewer_role",
                    params={"role": self.reviewed_by.role},
                )

        if self.recommended_level_id and self.track_id:
            if self.recommended_level.track_id != self.track_id:
                errors["recommended_level"] = ValidationError(
                    "The recommended level belongs to a different track.",
                    code="level_track_mismatch",
                )

        # status is derived from reviewed_at, so a level without a review
        # timestamp would leave the row claiming to be pending yet placed.
        if (self.recommended_level_id is None) != (self.reviewed_at is None):
            errors.setdefault(
                NON_FIELD_ERRORS,
                ValidationError(
                    "recommended_level and reviewed_at are set together or not "
                    "at all.",
                    code="incomplete_review",
                ),
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self._apply_beginner_skip()
        # Single source of truth for the stored status: the review timestamp.
        self.status = Status.REVIEWED if self.reviewed_at else Status.PENDING
        # Role rules span two tables and the audio/skip rule must hold for the
        # admin and direct ORM writes too, so validation lives in save().
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.username} / {self.track.name} ({self.status})"
