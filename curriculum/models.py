"""Curriculum: what one academy can teach, and where a new student starts there.

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

**SaaS Phase 3 made this domain organization-owned.** One field carries the whole
tenant boundary:

.. code-block:: text

    Organization
        |
        +-- Track                       Track.organization
              |
              +-- Level                 Level.track.organization
              |
              +-- PlacementResult       PlacementResult.track.organization
              |
              +-- TeacherTrack          membership.organization == track.organization

``Track.organization`` is the only stored tenant column, and everything else in
the app derives its academy from it. That is deliberate rather than economical: a
second copy of the organization on ``Level`` or ``PlacementResult`` is a second
thing that can disagree with the first, and the invariant "a curriculum object has
exactly one unambiguous owning academy" is easier to keep true when there is one
place it is written. ``Level.organization`` and ``PlacementResult.organization``
are properties, not fields.

The two cross-tenant rules that cannot be expressed as database constraints —
because they read rows in ``organizations`` and ``accounts`` — are enforced in
``clean()``, which every ``save()`` here runs: a placement's student must be an
active member of the academy that owns the track, and so must its reviewer. A
suspended membership is not access (``organizations.active_membership()`` is the
one function that decides that, and this module calls it rather than re-deriving
it).

Nothing about bookings, cohorts or session assessment belongs here.
"""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone as dj_timezone

from accounts.models import Role, User
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    active_membership,
)

from .exceptions import PlacementAlreadyReviewed, TrackHasNoFirstLevel
from .validators import validate_placement_audio

#: The first level of any track. A beginner skip places the student here.
FIRST_LEVEL_ORDER = 1


class Status(models.TextChoices):
    PENDING = "pending", "Pending review"
    REVIEWED = "reviewed", "Reviewed"


def placement_audio_path(instance, filename):
    """Keep each student's placement samples together, one dir per student.

    Also the object key inside the private bucket from Phase 6 onward. The
    layout is unchanged by that move: the path was never the access-control
    mechanism, and it is not one now — the bucket is private and the key is
    reached only through a signed URL.
    """
    return f"placements/{instance.student_id}/{filename}"


class Track(models.Model):
    """A subject one academy teaches, e.g. Tajweed, Hifz, Arabic.

    The tenant boundary for the whole domain. ``slug`` used to be globally
    unique, which quietly asserted that only one academy existed: two academies
    both teaching ``tajweed`` is normal, and the second one to sign up must not
    be refused its own copy. So uniqueness is ``(organization, slug)`` now, and
    the global index is gone rather than kept "just in case" — leaving it would
    make the first academy to claim a slug the owner of that word.
    """

    organization = models.ForeignKey(
        Organization,
        # An academy's curriculum is meaningless without the academy. Nothing
        # deletes an organization today — that workflow is unbuilt and has to
        # decide what happens to placement history (see tech-debt.md) — so this
        # cascade is a statement about ownership rather than a live code path.
        on_delete=models.CASCADE,
        related_name="tracks",
        help_text=(
            "The academy that owns this track, and through it the track's levels "
            "and placements. Set at creation; not editable afterwards."
        ),
    )
    name = models.CharField(max_length=80)
    slug = models.SlugField(
        max_length=80,
        help_text="Unique within the owning academy, not across the platform.",
    )

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "slug"],
                name="unique_track_slug_per_organization",
                violation_error_message=(
                    "This academy already has a track with that slug."
                ),
            )
        ]

    def clean(self):
        errors = {}
        if not self._state.adding and self.organization_id is not None:
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("organization_id", flat=True)
                .first()
            )
            if stored is not None and stored != self.organization_id:
                # Moving a track moves its levels, its placements and its
                # teacher eligibility with it, into an academy that never
                # created any of them. The spec calls a transfer a separate
                # product decision, so this is not something a field edit does.
                errors["organization"] = ValidationError(
                    "A track cannot be moved to another academy. Its levels, "
                    "placements and teacher assignments belong to the academy "
                    "that created them; transferring curriculum is a separate "
                    "operation, not a field edit.",
                    code="organization_immutable",
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Track gained a cross-row rule in SaaS Phase 3 (ownership is immutable),
        # which is exactly the condition under which the other models in this app
        # validate inside save(). One consequence worth knowing: a duplicate slug
        # within an academy is now a ValidationError rather than the
        # IntegrityError Phase 2 documented (see learnings.md).
        self.full_clean()
        super().save(*args, **kwargs)

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

    @property
    def organization(self):
        """The academy this level belongs to, reached through its track.

        A property rather than a column, deliberately. The spec asks not to
        duplicate ``organization`` on ``Level`` without a proven need, and the
        reason is that a copy can disagree with the original — a level whose
        stored organization differs from its track's would be a curriculum object
        with two owning academies, which is the one thing the tenant model must
        never allow.
        """
        return self.track.organization

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


class TeacherTrackQuerySet(models.QuerySet):
    def active(self):
        """The assignments that currently authorize teaching. One definition."""
        return self.filter(active=True)

    def in_organization(self, organization):
        """Assignments inside one academy, checked from both ends.

        ``membership__organization`` and ``track__organization`` are validated to
        agree in ``clean()``, so filtering on both is redundant — and that is the
        point. A queryset that would return a row only if the invariant had been
        broken returns nothing instead, so a bug in one write path cannot become a
        cross-tenant read.
        """
        organization_id = getattr(organization, "pk", organization)
        return self.filter(
            membership__organization_id=organization_id,
            track__organization_id=organization_id,
        )


class TeacherTrack(models.Model):
    """A teacher's eligibility to teach one track, inside one academy.

    The model SaaS Phase 3 exists to establish, and the reason it hangs off
    ``OrganizationMembership`` rather than ``TeacherProfile``:

    .. code-block:: text

        Teacher T  (one User, one identity)
            Academy A  ->  Tajweed, Hifz
            Academy B  ->  Arabic

    ``TeacherProfile.specialties`` is a global many-to-many to ``Track`` and
    cannot express that — it says "T teaches these tracks", full stop, and with
    tracks now academy-owned that would let one academy's roster decide what T may
    teach in another. The membership already means "this user in this academy",
    already carries the uniqueness rule for that pair, and already knows whether
    the relationship is live, which is the same reasoning
    ``accounts.OrganizationTeacherConfiguration`` follows.

    **The legacy relation is still there and still read.** Scheduling enforces
    ``TeacherProfile.specialties`` in ``Booking.clean()`` and ``routing``; Phase 3
    is explicitly forbidden from rewriting either, so nothing here is consumed by
    them yet. SaaS Phase 4 is what switches the readers over. Until it does, this
    table is the tenant-safe record and that one is the compatibility layer (see
    tech-debt.md).
    """

    membership = models.ForeignKey(
        OrganizationMembership,
        # The eligibility is meaningless without the relationship it qualifies.
        # Nothing in the API deletes a membership — suspension keeps the row — so
        # this only fires when an organization itself is removed.
        on_delete=models.CASCADE,
        related_name="teacher_tracks",
        help_text="The academy-and-teacher relationship this eligibility is for.",
    )
    track = models.ForeignKey(
        Track,
        on_delete=models.CASCADE,
        related_name="teacher_tracks",
        help_text="A track owned by the same academy as the membership.",
    )
    active = models.BooleanField(
        default=True,
        help_text=(
            "Whether the teacher may currently be assigned this track here. "
            "Withdrawing eligibility keeps the row, the way a suspended "
            "membership does, so the record of what was once granted survives."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TeacherTrackQuerySet.as_manager()

    class Meta:
        ordering = ["membership_id", "track_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "track"],
                name="unique_teacher_track_per_membership",
                violation_error_message=(
                    "This teacher is already assigned that track in this "
                    "academy. Change the existing assignment instead of adding "
                    "a second one."
                ),
            )
        ]

    @property
    def user(self):
        """The teacher this eligibility is for. Reached through the membership."""
        return self.membership.user

    @property
    def organization(self):
        """The academy that granted it."""
        return self.membership.organization

    def clean(self):
        errors = {}
        if self.membership_id:
            if self.track_id and (
                self.membership.organization_id != self.track.organization_id
            ):
                # The invariant the whole model is for. It spans two tables, so
                # no database constraint can hold it — a check constraint cannot
                # read the organization off a related row.
                errors["track"] = ValidationError(
                    "That track belongs to a different academy than this "
                    "membership. A teacher's eligibility is granted by the "
                    "academy that owns the track.",
                    code="cross_organization_teacher_track",
                )
            if not self.membership.user.is_teacher:
                # The same rule ``OrganizationTeacherConfiguration`` enforces, and
                # deliberately the same rule: an organization role is authority,
                # not a teaching identity, so a student or parent account is
                # refused here even when the academy is happy to call them a
                # teacher. Note what is *not* checked — the membership's own role —
                # because the lead teacher who founded their academy holds the
                # ``owner`` row (see accounts/tenancy.py).
                errors["membership"] = ValidationError(
                    "Only a member whose account role is 'lead' or 'sub' can be "
                    "assigned a track (got '%(role)s').",
                    code="invalid_role_for_teacher_track",
                    params={"role": self.membership.user.role},
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # The cross-tenant rule spans two tables, so validating in save() is what
        # makes it hold for the admin and direct ORM writes as well as the API.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.membership.user.username} teaches {self.track.slug} "
            f"@ {self.membership.organization.slug}"
        )


class PlacementResultQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The placements one academy may see. The phase's central queryset.

        Both halves are required, and the second is the one that is easy to
        forget: a placement is this academy's only if its *track* is owned here
        **and** its student is an active member here. Filtering on the track alone
        would keep showing a student's placement after the academy suspended them,
        and a global ``PlacementResult.objects`` in an organization-scoped view is
        the whole cross-tenant leak in one line.

        The membership join cannot duplicate rows: ``OrganizationMembership`` is
        unique per ``(organization, user)``, so at most one row matches.
        """
        organization_id = getattr(organization, "pk", organization)
        return self.filter(
            track__organization_id=organization_id,
            student__organization_memberships__organization_id=organization_id,
            student__organization_memberships__status=MembershipStatus.ACTIVE,
        )


class PlacementResult(models.Model):
    """One student's starting point in one track.

    A student holds at most one row per track: re-submitting updates it back to
    pending rather than stacking a second request (see ``submit``).

    Academy-scoped through ``track``, with no organization column of its own — the
    unique ``(student, track)`` rule then means "one placement per student per
    academy track", so the same person studying at two academies holds two
    independent placements and neither academy can see the other's.
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

    objects = PlacementResultQuerySet.as_manager()

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

    @property
    def organization(self):
        """The academy this placement belongs to, reached through its track.

        A property rather than a column, for the reason ``Level.organization``
        gives: a stored copy is a second answer to "which academy owns this", and
        two answers is one too many.
        """
        return self.track.organization

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

    def _validate_incoming_audio(self, errors):
        """Re-run the upload rules for a file that did not come via the API.

        ``PlacementSubmitSerializer`` is where an upload is normally rejected,
        with a proper field error. This is the backstop the Phase 6 spec asks
        for, and it exists because the serializer is not the only HTTP path to
        this field: the admin's change form lets the lead attach a file
        directly, and a future view could assign ``request.FILES[...]`` to the
        model without going through that serializer at all.

        Scoped to files that arrived *over HTTP*, and the scope is the whole
        point rather than a shortcut:

        * ``UploadedFile`` is exactly Django's marker for "these bytes came from
          a request", which is exactly CLAUDE.md's untrusted input. A
          ``File`` handed over by a factory, a fixture or a management command is
          trusted code writing a known file, and re-validating it would only
          break test data and data migrations.
        * The check reads ``_file`` rather than ``self.audio_sample.file``
          because the public property *opens the stored file* when the value came
          from the database — one storage round-trip, so a GET against the bucket,
          on every single save of every placement. ``_file`` is set only when a
          file object has been assigned in this process, which is precisely the
          case worth validating.
        """
        candidate = getattr(self.audio_sample, "_file", None)
        if not isinstance(candidate, UploadedFile):
            return
        try:
            validate_placement_audio(candidate)
        except ValidationError as exc:
            errors["audio_sample"] = exc

    def _is_active_here(self, user) -> bool:
        """Is ``user`` an active member of the academy that owns this track?

        Goes through ``organizations.active_membership()`` rather than filtering
        memberships here, because "which memberships grant access" is one
        question with one answer in this codebase, and a second copy of it is the
        one that would eventually disagree. Passing the bare organization id keeps
        this to a single query — the ``Organization`` row itself is not needed to
        answer the question.
        """
        return (
            active_membership(user=user, organization=self.track.organization_id)
            is not None
        )

    def clean(self):
        errors = {}

        if self.student_id:
            if self.student.role != Role.STUDENT:
                errors["student"] = ValidationError(
                    "Only a user with role 'student' can have a placement result "
                    "(got '%(role)s').",
                    code="invalid_student_role",
                    params={"role": self.student.role},
                )
            elif self.track_id and not self._is_active_here(self.student):
                # SaaS Phase 3. A ``student`` account is a global identity, not a
                # student *of this academy*: being enrolled somewhere is not
                # enrolment here, and a suspended membership is a record rather
                # than a key. Enforced in the model because a data migration, the
                # admin and a direct ORM write must all be held to it, not only
                # the endpoint.
                errors["student"] = ValidationError(
                    "That student is not an active member of the academy that "
                    "owns this track.",
                    code="student_not_in_organization",
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
        elif has_audio:
            self._validate_incoming_audio(errors)

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
            elif self.track_id and not self._is_active_here(self.reviewed_by):
                # SaaS Phase 3. ``role == lead`` means "a lead teacher
                # somewhere", which in a multi-tenant platform authorizes
                # nothing: the reviewer must be a lead teacher *of the academy
                # whose curriculum this placement is in*.
                errors["reviewed_by"] = ValidationError(
                    "That reviewer is not an active member of the academy that "
                    "owns this track.",
                    code="reviewer_not_in_organization",
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
