"""Assessment: what a teacher recorded about a session, and what it adds up to.

Field sets mirror specs/phase-7-assessment-progress.md exactly. The phase's whole
purpose is stated in its spec and repeated in CLAUDE.md: this app produces the
*evidence* that delegating to a sub-teacher is safe. It does not act on that
evidence. Nothing here ranks teachers, moves a student's level, or is read by
``scheduling.routing`` — a later phase decides what the data is worth.

Decisions worth knowing before reading:

* **A rubric is live configuration; an assessment is a historical record.** That
  split is the reason ``SessionAssessment`` carries ``rubric_name`` and
  ``criteria_snapshot`` and every ``AssessmentScore`` carries ``criterion_name``.
  Renaming or deactivating a criterion tomorrow must not change what a teacher is
  recorded as having scored today, and it does not: the live rows are only
  consulted when a *new* assessment is submitted.
* **``track`` is a plain FK with a unique-when-active constraint**, not a
  OneToOne — the product owner's call (2026-08-30), and the rule the spec
  actually states is "a track has at most one *active* rubric". This is the same
  shape ``pricing.PricingAgreement`` uses for "one live row per student and
  level", not a version graph: there are no parent pointers and no version
  numbers, and history rides on the snapshots above. See learnings.md.
* **Teacher data is immutable after submission** (CLAUDE.md's assessment
  invariants). ``SessionAssessment.save()`` refuses to change any teacher-owned
  field on an existing row, and ``AssessmentScore.save()`` refuses to change a
  score at all. Lead review is three *separate* fields, so annotating a session
  cannot rewrite what the teacher said about it.
* **One definition of an average**, ``average_of`` below: sum of every criterion
  score divided by how many there are, quantised to two places once, at the end.
  Reporting, progress and snapshot generation all call it. A missing assessment
  is absent from the input, never a zero in it, so "no data" comes back as
  ``None`` rather than 0.00 everywhere.
* **A period is scoped by when the lesson happened**, ``booking.start_time_utc``,
  not by when it was scored. That is what makes "assessed sessions <= completed
  sessions" true within any window, which a snapshot's two counts have to be able
  to claim. See learnings.md.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from accounts.models import Role, User
from accounts.tenancy import active_student_membership
from curriculum.models import Track
from organizations.models import active_membership
from scheduling.models import Booking, BookingStatus, bookable_teacher_error

#: The spec's scale. 1 = needs significant improvement, 5 = excellent.
MIN_SCORE = 1
MAX_SCORE = 5

#: Two decimal places, per the spec's presentation rule. Applied once, to the
#: finished average — never to an individual score before aggregating.
SCORE_PRECISION = Decimal("0.01")

#: Teacher-owned fields on a submitted assessment. Everything a teacher recorded,
#: including the flag they raised: that a session *was* flagged is part of the
#: record, so lead review stamps its own fields rather than clearing this one.
TEACHER_OWNED_FIELDS = (
    "booking_id",
    "student_id",
    "track_id",
    "assessed_by_id",
    "assessed_at",
    "teacher_summary",
    "flagged_for_review",
    "flag_reason",
    "rubric_name",
    "criteria_snapshot",
)


def average_of(values):
    """``sum(values) / len(values)`` as a two-place Decimal, or None if empty.

    The single score definition CLAUDE.md asks for. Every caller — the per
    assessment property, the teacher report, a family's progress, a snapshot —
    goes through here, so two of them cannot end up disagreeing about what an
    average is.

    Empty input is ``None``, never ``Decimal("0.00")``. A teacher with no
    assessments has no average; a student with an unassessed month has no
    average. Rendering that as a zero would read as "scored 0 out of 5", which is
    not merely imprecise but the opposite of what happened.
    """
    values = [Decimal(value) for value in values]
    if not values:
        return None
    total = sum(values, Decimal(0))
    # Divide first, quantise once. Rounding the parts would drift.
    return (total / Decimal(len(values))).quantize(
        SCORE_PRECISION, rounding=ROUND_HALF_UP
    )


class AssessmentRubricQuerySet(models.QuerySet):
    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(track__organization_id=org_id)


class AssessmentRubric(models.Model):
    """The shared scoring sheet for one track. Live configuration, lead-owned.

    At most one row per track is ``active``, enforced by the partial unique
    constraint below and by ``supersede_active()`` running first in ``save()`` —
    the same pairing ``pricing.PricingAgreement`` uses. Superseded rubrics are
    kept rather than deleted, because assessments submitted against them still
    point at their criteria.
    """

    track = models.ForeignKey(
        # CASCADE: a rubric is configuration *for* a track and means nothing
        # without it. The historical side is protected elsewhere —
        # ``SessionAssessment.track`` is PROTECT, so a track that has ever been
        # assessed cannot be deleted in the first place.
        Track,
        on_delete=models.CASCADE,
        related_name="assessment_rubrics",
    )
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    active = models.BooleanField(
        default=True,
        help_text=(
            "False once a later rubric for the same track supersedes this one. "
            "Superseded rubrics are kept: historical assessments reference their "
            "criteria."
        ),
    )

    objects = AssessmentRubricQuerySet.as_manager()

    class Meta:
        ordering = ["track", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["track"],
                condition=Q(active=True),
                name="unique_active_rubric_per_track",
                violation_error_message=(
                    "That track already has an active assessment rubric."
                ),
            )
        ]

    @property
    def organization(self):
        """The academy that owns this rubric."""
        return self.track.organization if self.track_id and hasattr(self, "track") and self.track else None

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def active_for_track(cls, track):
        """The rubric a new assessment for ``track`` must score against, or None.

        The one supported way to ask the question, for the same reason
        ``PricingAgreement.active_for`` exists: two callers with two definitions
        of "current" eventually disagree.
        """
        return cls.objects.filter(
            track=getattr(track, "pk", track), active=True
        ).first()

    def active_criteria(self):
        """The criteria a new assessment must score, in the lead's order.

        Deactivated criteria are excluded by design: that is what deactivating
        one *means*, and it is why an old assessment holding a score for one is
        still coherent.
        """
        return self.criteria.filter(active=True).order_by("order", "pk")

    def supersede_active(self):
        """Deactivate any live rubric this one replaces. Returns the count.

        A queryset ``update()`` on purpose, like ``PricingAgreement`` does it: it
        flips one boolean on rows that are otherwise untouched, and routing them
        through ``save()`` would re-run ``full_clean()`` on historical
        configuration for no gain.
        """
        return (
            type(self)
            .objects.filter(track_id=self.track_id, active=True)
            .exclude(pk=self.pk)
            .update(active=False)
        )

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self._state.adding and self.active and self.track_id:
                # Supersede *before* validating: full_clean() checks the partial
                # unique constraint above, so the previous rubric has to be
                # deactivated first or creating its replacement would be refused
                # by the very rule it satisfies.
                self.supersede_active()
            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        state = "active" if self.active else "superseded"
        return f"{self.track.name}: {self.name} ({state})"


class AssessmentCriterion(models.Model):
    """One line on the scoring sheet — "makhraj accuracy", "revision consistency".

    Content is data owned by the lead, never hard-coded: the spec's Tajweed and
    Hifz examples are examples. Criteria are edited in place (renamed,
    reordered by a lead who deactivates and re-adds, switched off) and never
    deleted once assessed — ``AssessmentScore.criterion`` is PROTECT, and the
    snapshot on each score is what keeps an old assessment readable regardless.
    """

    rubric = models.ForeignKey(
        AssessmentRubric,
        on_delete=models.CASCADE,
        related_name="criteria",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text="Position on the sheet, 1 first. Unique within the rubric.",
    )
    active = models.BooleanField(
        default=True,
        help_text=(
            "False stops *new* assessments scoring it. Existing scores are "
            "untouched — deactivating is not deleting."
        ),
    )

    class Meta:
        ordering = ["rubric", "order", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["rubric", "order"],
                name="unique_criterion_order_per_rubric",
                violation_error_message=(
                    "That rubric already has a criterion at this position."
                ),
            )
        ]
        verbose_name_plural = "assessment criteria"

    @property
    def organization(self):
        """The academy that owns this criterion."""
        return self.rubric.organization if self.rubric_id and hasattr(self, "rubric") and self.rubric else None

    def snapshot(self):
        """This criterion as an assessment records it: identity, name, order."""
        return {"id": self.pk, "name": self.name, "order": self.order}

    def __str__(self):
        return f"{self.rubric.name} {self.order}. {self.name}"

    def save(self, *args, **kwargs):
        # Validating in save() the same way Level does, and for the same reason:
        # the order rule has to hold for the admin and direct ORM writes, not
        # only for the API serializer.
        self.full_clean()
        super().save(*args, **kwargs)


class SessionAssessmentQuerySet(models.QuerySet):
    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(track__organization_id=org_id)


class SessionAssessment(models.Model):
    """What one teacher recorded about one completed session.

    One per booking, by the teacher who taught it, on a booking that actually
    happened. Those three sentences are the whole authorisation model, and they
    are enforced in ``clean()`` rather than only in the API so a direct ORM write
    cannot disagree with the endpoint — the pattern Phase 1 set and every phase
    since has followed.

    ``rubric_name`` and ``criteria_snapshot`` are why this row stays readable
    after the rubric moves on. Nothing recomputes them; they are what the teacher
    was looking at.
    """

    booking = models.OneToOneField(
        # PROTECT, like every reference to teaching history in this project: an
        # assessed session is evidence, and deleting the booking must be a
        # deliberate act rather than a cascade.
        Booking,
        on_delete=models.PROTECT,
        related_name="assessment",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="session_assessments",
        help_text="Always booking.student. Denormalised so progress can filter cheaply.",
    )
    track = models.ForeignKey(
        Track,
        on_delete=models.PROTECT,
        related_name="session_assessments",
        help_text="Always booking.level.track — the track whose rubric was used.",
    )
    assessed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="submitted_assessments",
        help_text="Always booking.teacher, and an approved teacher.",
    )
    assessed_at = models.DateTimeField(
        default=dj_timezone.now,
        help_text="When it was submitted. Stored UTC, rendered per-viewer.",
    )
    teacher_summary = models.TextField(
        blank=True,
        help_text="Written for the family: this is the one free-text field they read.",
    )
    flagged_for_review = models.BooleanField(
        default=False,
        help_text="The teacher asking the lead to look at this session.",
    )
    flag_reason = models.TextField(
        blank=True,
        help_text="Why it was flagged. Internal — never shown to a student or parent.",
    )
    lead_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set by an explicit lead action. Viewing an assessment does not set it.",
    )
    lead_reviewed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessment_reviews",
        help_text="The lead teacher who reviewed. Nobody else may.",
    )
    lead_review_note = models.TextField(
        blank=True,
        help_text=(
            "The lead's private annotation. Not visible to the family, and not "
            "visible to the assessing sub-teacher either in this phase."
        ),
    )
    rubric_name = models.CharField(
        max_length=80,
        help_text="Snapshot of the rubric's name at submission time.",
    )
    criteria_snapshot = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Snapshot of the criteria scored: [{id, name, order}]. What the "
            "teacher was actually looking at, frozen."
        ),
    )

    objects = SessionAssessmentQuerySet.as_manager()

    class Meta:
        # Newest first: a review queue and an assessment history both read that
        # way. There is no created_at — assessed_at is the submission stamp.
        ordering = ["-assessed_at", "-pk"]

    @property
    def organization(self):
        """The academy that owns this assessment."""
        if self.track_id and hasattr(self, "track") and self.track:
            return getattr(self.track, "organization", None)
        if self.booking_id and hasattr(self, "booking") and self.booking:
            level = getattr(self.booking, "level", None)
            if level and getattr(level, "track", None):
                return getattr(level.track, "organization", None)
        return None

    def _is_active_here(self, user):
        """Is user an active member of the academy that owns this assessment?"""
        if not self.organization or not user:
            return False
        return active_membership(user=user, organization=self.organization) is not None

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def submit(
        cls,
        *,
        booking,
        assessed_by,
        scores,
        teacher_summary="",
        flagged_for_review=False,
        flag_reason="",
    ):
        """Record one session against its track's active rubric.

        ``scores`` is an iterable of ``{"criterion": <pk or instance>, "score":
        int, "comment": str}``. Every active criterion must appear exactly once:
        a missing one is an error rather than an implicit zero, an unknown or
        duplicated one is an error too. All of that is decided here, in one place,
        because "what a complete assessment is" is a domain rule and not a
        serializer's opinion.

        Rows are created one ``save()`` at a time. ``bulk_create()`` would skip
        every validator and constraint check on ``AssessmentScore``, which
        CLAUDE.md rules out for exactly that reason.
        """
        errors = {}

        track = booking.level.track if booking.level_id else None
        rubric = AssessmentRubric.active_for_track(track) if track else None
        if rubric is None:
            raise ValidationError(
                {
                    NON_FIELD_ERRORS: ValidationError(
                        "%(track)s has no active assessment rubric, so there is "
                        "nothing to score against yet.",
                        code="no_active_rubric",
                        params={"track": track.name if track else "This track"},
                    )
                }
            )

        criteria = list(rubric.active_criteria())
        if not criteria:
            raise ValidationError(
                {
                    NON_FIELD_ERRORS: ValidationError(
                        "%(rubric)s has no active criteria, so there is nothing "
                        "to score.",
                        code="rubric_has_no_criteria",
                        params={"rubric": rubric.name},
                    )
                }
            )

        by_id = {criterion.pk: criterion for criterion in criteria}
        submitted = {}
        duplicates, unknown = [], []
        for entry in scores:
            criterion = entry["criterion"]
            criterion_id = getattr(criterion, "pk", criterion)
            if criterion_id in submitted:
                duplicates.append(criterion_id)
            elif criterion_id not in by_id:
                unknown.append(criterion_id)
            else:
                submitted[criterion_id] = entry

        if duplicates:
            errors["scores"] = ValidationError(
                "Each criterion is scored once. Repeated: %(ids)s.",
                code="duplicate_criterion",
                params={"ids": ", ".join(str(i) for i in sorted(set(duplicates)))},
            )
        elif unknown:
            errors["scores"] = ValidationError(
                "Not an active criterion of %(rubric)s: %(ids)s.",
                code="unknown_criterion",
                params={
                    "rubric": rubric.name,
                    "ids": ", ".join(str(i) for i in sorted(set(unknown))),
                },
            )
        else:
            missing = [c for c in criteria if c.pk not in submitted]
            if missing:
                errors["scores"] = ValidationError(
                    "Every active criterion must be scored. Missing: %(names)s.",
                    code="missing_criterion",
                    params={"names": ", ".join(c.name for c in missing)},
                )

        if errors:
            raise ValidationError(errors)

        with transaction.atomic():
            assessment = cls(
                booking=booking,
                student=booking.student,
                track=track,
                assessed_by=assessed_by,
                teacher_summary=teacher_summary or "",
                flagged_for_review=bool(flagged_for_review),
                flag_reason=flag_reason or "",
                rubric_name=rubric.name,
                criteria_snapshot=[c.snapshot() for c in criteria],
            )
            assessment.save()
            for criterion in criteria:
                entry = submitted[criterion.pk]
                AssessmentScore(
                    assessment=assessment,
                    criterion=criterion,
                    criterion_name=criterion.name,
                    score=entry["score"],
                    comment=entry.get("comment") or "",
                ).save()
        return assessment

    def record_lead_review(self, *, reviewed_by, note=""):
        """Annotate this assessment as the lead. Teacher data is untouched.

        Three fields are written and no others, which is what "review is a
        separate annotation" means in practice. ``flagged_for_review`` in
        particular is *not* cleared: that the teacher raised a flag is part of the
        record. What takes the assessment out of the pending queue is the review
        stamp, which is why ``pending_lead_review()`` filters on that.
        """
        self.lead_reviewed_by = reviewed_by
        self.lead_reviewed_at = dj_timezone.now()
        if note:
            self.lead_review_note = note
        self.save()
        return self

    @classmethod
    def pending_lead_review(cls, organization=None):
        """Flagged and not yet reviewed — the lead's queue, one definition of it."""
        qs = cls.objects.filter(flagged_for_review=True, lead_reviewed_at__isnull=True)
        if organization is not None:
            qs = qs.in_organization(organization)
        return qs

    @property
    def is_lead_reviewed(self) -> bool:
        return self.lead_reviewed_at is not None

    @property
    def overall_average(self):
        """This session's average across its criterion scores, or None if empty.

        ``None`` is unreachable through ``submit`` — an assessment with no scores
        cannot be created that way — but a half-built object in a test or a shell
        can be in that state, and answering 0.00 for it would be a lie.
        """
        return average_of(self.scores.values_list("score", flat=True))

    # --- Validation ---------------------------------------------------------

    def _validate_booking(self, errors):
        """The three sentences at the top of the class, as code."""
        if not self.booking_id:
            return
        booking = self.booking

        if booking.status != BookingStatus.COMPLETED:
            errors["booking"] = ValidationError(
                "Only a completed session can be assessed (this one is "
                "'%(status)s'). Assessment does not mark a booking completed — "
                "that is a separate act.",
                code="booking_not_completed",
                params={"status": booking.status},
            )

        if self.student_id and self.student_id != booking.student_id:
            errors["student"] = ValidationError(
                "The assessed student must be the booking's student.",
                code="student_booking_mismatch",
            )

        if self.track_id and self.track_id != booking.level.track_id:
            errors["track"] = ValidationError(
                "The assessed track must be the track of the booking's level.",
                code="track_booking_mismatch",
            )

        if self.assessed_by_id and self.assessed_by_id != booking.teacher_id:
            errors["assessed_by"] = ValidationError(
                "Only the teacher who taught a session may assess it.",
                code="not_the_booking_teacher",
            )

        if (
            self.track_id
            and booking.level_id
            and booking.level.track_id
            and self.organization is not None
            and booking.level.track.organization_id != self.organization.id
        ):
            errors["booking"] = ValidationError(
                "The booking belongs to a different academy than the assessment track.",
                code="cross_academy_booking_mismatch",
            )

    def _validate_assessor(self, errors):
        if not self.assessed_by_id or "assessed_by" in errors:
            return
        # The same gate booking uses, reused rather than restated: role is lead
        # or sub, a teacher profile exists, and a lead has approved it in this academy.
        problem = bookable_teacher_error(self.assessed_by, organization=self.organization)
        if problem is not None:
            errors["assessed_by"] = problem

    def _validate_student(self, errors):
        if not self.student_id or "student" in errors:
            return
        if self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "Only a user with role 'student' can be assessed (got '%(role)s').",
                code="invalid_student_role",
                params={"role": self.student.role},
            )
        elif self.organization is not None and not self._is_active_here(self.student):
            errors["student"] = ValidationError(
                "That student is not an active member of the academy that owns this assessment.",
                code="student_not_in_organization",
            )

    def _validate_flag(self, errors):
        if self.flag_reason and not self.flagged_for_review:
            errors["flag_reason"] = ValidationError(
                "A flag reason without a flag says nothing. Set "
                "flagged_for_review, or leave the reason empty.",
                code="reason_without_flag",
            )

    def _validate_lead_review(self, errors):
        if self.lead_reviewed_by_id:
            if self.lead_reviewed_by.role != Role.LEAD:
                # The same restriction placement review and pricing approval carry:
                # enforced here as well as in the permission class, so a direct ORM
                # write cannot hand a sub-teacher the lead's annotation.
                errors["lead_reviewed_by"] = ValidationError(
                    "Only the lead teacher can review an assessment (got "
                    "'%(role)s').",
                    code="invalid_reviewer_role",
                    params={"role": self.lead_reviewed_by.role},
                )
            elif self.organization is not None and not self._is_active_here(self.lead_reviewed_by):
                errors["lead_reviewed_by"] = ValidationError(
                    "That reviewer is not an active member of the academy that owns this assessment.",
                    code="reviewer_not_in_organization",
                )
        if (self.lead_reviewed_at is None) != (self.lead_reviewed_by_id is None):
            errors.setdefault(
                NON_FIELD_ERRORS,
                ValidationError(
                    "lead_reviewed_at and lead_reviewed_by are set together or "
                    "not at all.",
                    code="incomplete_review",
                ),
            )
        if self.lead_review_note and self.lead_reviewed_at is None:
            errors["lead_review_note"] = ValidationError(
                "A review note belongs to a review. Mark the assessment "
                "reviewed, or leave the note empty.",
                code="note_without_review",
            )

    def _validate_teacher_data_unchanged(self, errors):
        """Teacher scores are immutable after submission (CLAUDE.md).

        Checked against the stored row rather than tracked in memory, so it holds
        however the object was loaded — an admin form, a shell, a later phase's
        service. Only the three lead-review fields are writable on an existing
        assessment, which is what makes "lead review never rewrites historical
        teacher data" a property of the model instead of a convention.
        """
        if self._state.adding:
            return
        stored = (
            type(self).objects.filter(pk=self.pk).values(*TEACHER_OWNED_FIELDS).first()
        )
        if stored is None:
            return
        for field, was in stored.items():
            if getattr(self, field) != was:
                errors[field] = ValidationError(
                    "A submitted assessment is immutable; %(field)s cannot be "
                    "changed. Lead review is a separate annotation.",
                    code="teacher_data_immutable",
                    params={"field": field},
                )

    def clean(self):
        errors = {}
        self._validate_teacher_data_unchanged(errors)
        self._validate_booking(errors)
        self._validate_assessor(errors)
        self._validate_student(errors)
        self._validate_flag(errors)
        self._validate_lead_review(errors)
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Every rule above reads another table or the stored row, so none of them
        # can be a DB constraint. Validating in save() is what makes them hold
        # for the admin and direct ORM writes as well as the API — the pattern
        # ParentLink, Level, Booking and PricingAgreement all use.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.student.username} / {self.track.name} "
            f"by {self.assessed_by.username} ({self.assessed_at:%Y-%m-%d})"
        )


class AssessmentScore(models.Model):
    """One criterion's mark on one assessment. Immutable once written.

    ``criterion_name`` is the snapshot that keeps this row readable after the live
    criterion is renamed; ``criterion`` is kept alongside it so a per-criterion
    average can group scores that mean the same thing across a rename.
    """

    assessment = models.ForeignKey(
        SessionAssessment,
        on_delete=models.CASCADE,
        related_name="scores",
    )
    criterion = models.ForeignKey(
        # PROTECT: a criterion that has ever been scored is part of the record.
        # Deactivating one is how a lead retires it; deleting is not available.
        AssessmentCriterion,
        on_delete=models.PROTECT,
        related_name="scores",
    )
    criterion_name = models.CharField(
        max_length=120,
        help_text="Snapshot of the criterion's name at submission time.",
    )
    score = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_SCORE), MaxValueValidator(MAX_SCORE)],
        help_text="1 needs significant improvement .. 5 excellent.",
    )
    comment = models.TextField(
        blank=True,
        help_text="Optional per-criterion note. Teaching feedback, not internal QC.",
    )

    class Meta:
        ordering = ["assessment", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "criterion"],
                name="unique_score_per_criterion_per_assessment",
                violation_error_message=(
                    "That criterion is already scored on this assessment."
                ),
            ),
            # DB backstop for the scale. The field validators raise a better
            # message first; this catches anything that bypasses validation.
            models.CheckConstraint(
                condition=Q(score__gte=MIN_SCORE) & Q(score__lte=MAX_SCORE),
                name="score_within_scale",
                violation_error_message=f"Scores run {MIN_SCORE} to {MAX_SCORE}.",
            ),
        ]

    @property
    def organization(self):
        """The academy that owns this score."""
        if self.assessment_id and hasattr(self, "assessment") and self.assessment:
            return self.assessment.organization
        return None

    def clean(self):
        errors = {}
        if self.criterion_id and self.assessment_id:
            snapshot_ids = {
                entry.get("id") for entry in (self.assessment.criteria_snapshot or [])
            }
            if snapshot_ids and self.criterion_id not in snapshot_ids:
                errors["criterion"] = ValidationError(
                    "That criterion is not one this assessment was submitted "
                    "against.",
                    code="criterion_not_in_snapshot",
                )
            if (
                hasattr(self, "criterion")
                and self.criterion
                and hasattr(self.criterion, "rubric")
                and self.criterion.rubric
                and hasattr(self, "assessment")
                and self.assessment
                and self.assessment.track_id
                and self.criterion.rubric.track_id != self.assessment.track_id
            ):
                errors["criterion"] = ValidationError(
                    "That criterion belongs to a different track.",
                    code="cross_track_criterion",
                )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            # Teacher scores are immutable after submission (CLAUDE.md). There is
            # deliberately no correction path in this phase — the spec says a
            # rule about correcting historical assessments is a decision to be
            # asked for, not invented, so this refuses rather than allowing one
            # quietly. See tech-debt.md.
            raise ValidationError(
                {
                    NON_FIELD_ERRORS: ValidationError(
                        "A submitted score is immutable in this phase.",
                        code="score_immutable",
                    )
                }
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.criterion_name}: {self.score}"


def within_period(queryset, *, field, start=None, end=None):
    """Narrow ``queryset`` to ``[start, end)`` on ``field``. Either bound is optional.

    Half-open on purpose, the same convention the booking overlap rule uses: a
    period ending 1 September and one starting 1 September share no session, so
    two consecutive snapshots cannot both claim the same lesson.
    """
    if start is not None:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end is not None:
        queryset = queryset.filter(**{f"{field}__lt": end})
    return queryset


def assessments_in_period(queryset=None, *, start=None, end=None):
    """Assessments of sessions *taught* within the period.

    Scoped by ``booking.start_time_utc`` rather than ``assessed_at``: a period is
    a stretch of teaching, and a teacher who writes Monday's assessment on
    Tuesday has still assessed Monday's lesson. It is also what makes
    ``assessed_sessions <= completed_sessions`` hold inside any window, which a
    snapshot's two counts have to be able to claim. See learnings.md.
    """
    if queryset is None:
        queryset = SessionAssessment.objects.all()
    return within_period(queryset, field="booking__start_time_utc", start=start, end=end)


def completed_bookings_in_period(queryset=None, *, start=None, end=None):
    """Bookings that were actually taught within the period."""
    if queryset is None:
        queryset = Booking.objects.all()
    queryset = queryset.filter(status=BookingStatus.COMPLETED)
    return within_period(queryset, field="start_time_utc", start=start, end=end)


def criterion_averages_for(scores):
    """Per-criterion averages over ``scores``: ``[{criterion, name, average, n}]``.

    Grouped by criterion *identity*, so a rename does not split one criterion's
    history into two rows — that is why ``AssessmentScore`` keeps the FK next to
    the snapshot name. Two presentation choices, both deliberate:

    * the **name** shown is the snapshot from the most recently taught session in
      the group, because that is the wording a reader recognises, and it is
      historical rather than live;
    * the **order** is the live criterion's, because ordering is presentation and
      the sheet's current shape is the useful one to read a report in.

    ``scores`` must be a queryset — the criterion is followed per row.
    """
    grouped = {}
    for score in scores.select_related("criterion", "assessment", "assessment__booking"):
        bucket = grouped.setdefault(
            score.criterion_id,
            {
                "criterion": score.criterion,
                "criterion_name": score.criterion_name,
                "taught_at": score.assessment.booking.start_time_utc,
                "scores": [],
            },
        )
        bucket["scores"].append(score.score)
        taught_at = score.assessment.booking.start_time_utc
        if bucket["taught_at"] is None or (
            taught_at is not None and taught_at > bucket["taught_at"]
        ):
            bucket["criterion_name"] = score.criterion_name
            bucket["taught_at"] = taught_at

    rows = [
        {
            "criterion_id": criterion_id,
            "criterion_name": bucket["criterion_name"],
            "average": average_of(bucket["scores"]),
            "score_count": len(bucket["scores"]),
            "_order": bucket["criterion"].order,
        }
        for criterion_id, bucket in grouped.items()
    ]
    rows.sort(key=lambda row: (row["_order"], row["criterion_id"]))
    for row in rows:
        del row["_order"]
    return rows


class ProgressSnapshotQuerySet(models.QuerySet):
    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(track__organization_id=org_id)


class ProgressSnapshot(models.Model):
    """A frozen reading of one student's progress in one track over one period.

    The point of a snapshot is that it does *not* move. An assessment submitted
    late, for a session inside an already-snapshotted period, changes the live
    progress endpoint and leaves the snapshot alone — which is what makes "here is
    where you were in August" a statement rather than a query.

    So everything computed here is immutable after generation. The one field that
    stays writable is ``visible_to_family``: publishing a snapshot is an editorial
    act, not a change to its data, and a lead who generated one unpublished must
    have some way to release it (there is no API for that in this phase — see
    tech-debt.md).
    """

    #: Fields frozen at generation. ``visible_to_family`` is deliberately absent.
    FROZEN_FIELDS = (
        "student_id",
        "track_id",
        "period_start",
        "period_end",
        "generated_at",
        "completed_sessions",
        "assessed_sessions",
        "overall_average",
        "criterion_averages",
        "summary",
        "generated_by_id",
    )

    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="progress_snapshots",
    )
    track = models.ForeignKey(
        Track,
        on_delete=models.PROTECT,
        related_name="progress_snapshots",
    )
    period_start = models.DateTimeField(help_text="Inclusive. Stored UTC.")
    period_end = models.DateTimeField(help_text="Exclusive. Stored UTC.")
    generated_at = models.DateTimeField(default=dj_timezone.now)
    completed_sessions = models.PositiveIntegerField(
        help_text="Sessions taught in the period, assessed or not."
    )
    assessed_sessions = models.PositiveIntegerField(
        help_text="How many of those carry an assessment. Never inflated to match."
    )
    overall_average = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(MIN_SCORE), MaxValueValidator(MAX_SCORE)],
        help_text=(
            "Null when nothing in the period was assessed. Deliberately nullable: "
            "'no assessments' is missing data, and 0.00 would read as a score of "
            "zero out of five."
        ),
    )
    criterion_averages = models.JSONField(
        default=list,
        blank=True,
        help_text="Snapshot of the per-criterion averages: [{criterion_id, criterion_name, average, score_count}].",
    )
    summary = models.TextField(
        blank=True,
        help_text="Short human line. Auto-composed from the numbers unless the lead supplies one.",
    )
    generated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_snapshots",
        help_text="The lead who generated it. Nullable so removing an account cannot take history with it.",
    )
    visible_to_family = models.BooleanField(
        default=False,
        help_text="Off until the lead publishes it. A family reads only published snapshots.",
    )

    objects = ProgressSnapshotQuerySet.as_manager()

    class Meta:
        ordering = ["-period_end", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "track", "period_start", "period_end"],
                name="unique_snapshot_per_student_track_period",
                violation_error_message=(
                    "A snapshot for that student, track and period already exists."
                ),
            )
        ]

    @property
    def organization(self):
        """The academy that owns this snapshot."""
        if self.track_id and hasattr(self, "track") and self.track:
            return getattr(self.track, "organization", None)
        return None

    def _is_active_here(self, user):
        """Is user an active member of the academy that owns this track?"""
        if not self.organization or not user:
            return False
        return active_membership(user=user, organization=self.organization) is not None

    # --- Behaviour ----------------------------------------------------------

    @classmethod
    def generate(
        cls,
        *,
        student,
        track,
        period_start,
        period_end,
        generated_by=None,
        summary="",
        visible_to_family=False,
    ):
        """Compute and store one snapshot. Returns ``(snapshot, created)``.

        Repeating the request returns the existing row untouched, ``created``
        False — the spec's duplicate rule. That is checked *and* backed by the
        unique constraint, so two concurrent generations cannot both insert: the
        loser catches the IntegrityError and reads the winner's row, which is the
        same answer it would have given.
        """
        existing = cls.objects.filter(
            student=student,
            track=track,
            period_start=period_start,
            period_end=period_end,
        ).first()
        if existing is not None:
            return existing, False

        assessments = assessments_in_period(
            SessionAssessment.objects.filter(student=student, track=track),
            start=period_start,
            end=period_end,
        )
        completed = completed_bookings_in_period(
            Booking.objects.filter(student=student, level__track=track),
            start=period_start,
            end=period_end,
        )
        scores = AssessmentScore.objects.filter(assessment__in=assessments)
        score_values = list(scores.values_list("score", flat=True))

        snapshot = cls(
            student=student,
            track=track,
            period_start=period_start,
            period_end=period_end,
            completed_sessions=completed.count(),
            assessed_sessions=assessments.count(),
            overall_average=average_of(score_values),
            criterion_averages=[
                {
                    "criterion_id": row["criterion_id"],
                    "criterion_name": row["criterion_name"],
                    # JSON has no decimal type; a string keeps "3.50" exact
                    # rather than handing a float to whoever reads it back.
                    "average": str(row["average"]) if row["average"] is not None else None,
                    "score_count": row["score_count"],
                }
                for row in criterion_averages_for(scores)
            ],
            generated_by=generated_by,
            visible_to_family=bool(visible_to_family),
        )
        snapshot.summary = summary or snapshot.compose_summary()
        try:
            with transaction.atomic():
                snapshot.save()
        except IntegrityError:
            # Lost the race. The winner's row answers the same question.
            return (
                cls.objects.get(
                    student=student,
                    track=track,
                    period_start=period_start,
                    period_end=period_end,
                ),
                False,
            )
        return snapshot, True

    def compose_summary(self) -> str:
        """A one-line reading of the numbers, used when the lead supplies none."""
        if not self.assessed_sessions:
            return (
                f"{self.completed_sessions} session(s) taught, none assessed yet."
            )
        return (
            f"{self.assessed_sessions} of {self.completed_sessions} session(s) "
            f"assessed; overall average {self.overall_average}."
        )

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.period_start and self.period_end and self.period_end <= self.period_start:
            errors["period_end"] = ValidationError(
                "A period ends after it starts.",
                code="inverted_period",
            )

        if self.student_id:
            if self.student.role != Role.STUDENT:
                errors["student"] = ValidationError(
                    "Only a user with role 'student' has progress to snapshot (got "
                    "'%(role)s').",
                    code="invalid_student_role",
                    params={"role": self.student.role},
                )
            elif self.track_id and self.organization is not None and not self._is_active_here(self.student):
                errors["student"] = ValidationError(
                    "That student is not an active member of the academy that owns this track.",
                    code="student_not_in_organization",
                )

        if self.generated_by_id:
            if self.generated_by.role != Role.LEAD:
                errors["generated_by"] = ValidationError(
                    "Only the lead teacher generates progress snapshots (got "
                    "'%(role)s').",
                    code="invalid_generator_role",
                    params={"role": self.generated_by.role},
                )
            elif self.track_id and self.organization is not None and not self._is_active_here(self.generated_by):
                errors["generated_by"] = ValidationError(
                    "That generator is not an active member of the academy that owns this track.",
                    code="generator_not_in_organization",
                )

        # The reporting rule, as a stored invariant: an average exists exactly
        # when something was assessed. Neither a null on an assessed period nor a
        # number on an empty one is a state worth keeping.
        if self.assessed_sessions is not None:
            if self.assessed_sessions and self.overall_average is None:
                errors["overall_average"] = ValidationError(
                    "An assessed period has an average.",
                    code="missing_average",
                )
            elif not self.assessed_sessions and self.overall_average is not None:
                errors["overall_average"] = ValidationError(
                    "Nothing was assessed, so there is no average. Missing "
                    "assessments are not zeroes.",
                    code="average_without_assessments",
                )

        if not self._state.adding:
            stored = (
                type(self)
                .objects.filter(pk=self.pk)
                .values(*self.FROZEN_FIELDS)
                .first()
            )
            if stored is not None:
                for field, was in stored.items():
                    if getattr(self, field) != was:
                        errors[field] = ValidationError(
                            "A generated snapshot is immutable; %(field)s cannot "
                            "be changed. Only visible_to_family stays writable.",
                            code="snapshot_immutable",
                            params={"field": field},
                        )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.student.username} / {self.track.name} "
            f"{self.period_start:%Y-%m-%d}..{self.period_end:%Y-%m-%d}"
        )
