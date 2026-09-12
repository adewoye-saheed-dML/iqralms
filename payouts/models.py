"""Teacher payouts: what a completed teaching session earned the teacher.

Field sets mirror specs/phase-8-payouts.md. The single rule the whole app exists
to protect is the one the spec puts at the top of its own file:

.. code-block:: text

    Family pricing  ≠  Teacher payout

Nothing here reads ``pricing.PricingAgreement``, and nothing here reads
``assessment.SessionAssessment``. A family's discount is absorbed by the lead's
margin (mvp-spec section 3), and teaching quality is measured rather than priced,
so neither can reach the number a teacher is owed. The absence of those two
imports is a design decision, not an oversight — see learnings.md.

Four product decisions were taken before this app was written (product owner,
2026-08-30), because the spec's "stop and ask" list named all four:

* **The rate source is the existing** ``TeacherProfile.hourly_payout_rate``.
  No second rate model, so there is nothing to keep in sync.
* **Currency is NGN, rounded to 0.01 with ROUND_HALF_UP, quantized exactly
  once** at the end of the calculation. The repository had no monetary
  convention before this phase; ``assessment`` already rounds its averages
  ROUND_HALF_UP, so this follows it rather than inventing a second rule.
* **A cohort session is paid once, not once per seat.** Six students in one
  group class is one teacher teaching for thirty minutes — exactly the reasoning
  ``scheduling.weekly_committed_minutes`` already applies to capacity. Paying per
  seat would make the product's main throughput lever cost six times what the
  1:1 sessions it replaces cost.
* **A teacher with no rate is not payout-eligible.** ``hourly_payout_rate`` is
  null for the lead, who is not paid per hour, so a lead-taught session produces
  no payout record and generation reports it as skipped rather than failing.

What makes a payout record trustworthy is that it stores the numbers it was
computed from — ``minutes_paid``, ``rate_used``, ``amount``, ``currency`` — and
then refuses to let them change. Raising a teacher's rate tomorrow cannot rewrite
what was paid yesterday, because yesterday's amount is a stored fact rather than
a derivation from a live field.
"""

from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone as dj_timezone

from accounts.models import User
from organizations.models import active_membership
from scheduling.models import Booking, BookingStatus, Cohort, MINUTES_PER_HOUR

from .exceptions import PayoutAlreadyFinalized

#: The academy's one currency. Conversion is explicitly out of scope for Phase 8,
#: so this is a stored constant rather than a configurable per-teacher field: a
#: second currency is a product decision with its own rounding questions, and
#: storing the code on every record is what makes that decision possible later
#: without reinterpreting the rows written today.
PAYOUT_CURRENCY = "NGN"

#: Money is exact to the kobo. One quantize, at the end of the calculation.
MONEY_PRECISION = Decimal("0.01")

#: Nothing below zero is a meaningful payout. Zero is legal, the same way a full
#: scholarship is a legal pricing agreement (``pricing.MINIMUM_RATE``).
MINIMUM_AMOUNT = Decimal("0")

#: Booking statuses that earn a teaching payout. Only one, and it is deliberately
#: a frozenset rather than a bare comparison so that widening it later is a
#: visible edit in one place. ``cancelled`` and ``no_show`` earn nothing;
#: ``scheduled`` has not happened yet.
PAYABLE_BOOKING_STATUSES = frozenset({BookingStatus.COMPLETED})


def payout_amount(minutes, rate):
    """``minutes × rate`` as money — the one payout formula in the codebase.

    Generation calls it to compute an amount and ``TeacherPayout.clean()`` calls
    it to verify one, so a record whose ``amount`` disagrees with its own
    ``minutes_paid`` and ``rate_used`` cannot be saved through any path.

    The division is inexact for a duration that is not a whole number of hours
    (20 minutes is 0.333… of an hour) and that is fine: ``Decimal`` carries 28
    significant digits through the multiplication, and the result is rounded
    exactly once, here, rather than at each step.
    """
    hours = Decimal(minutes) / Decimal(MINUTES_PER_HOUR)
    return (hours * Decimal(rate)).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)


class PayoutStatus(models.TextChoices):
    """The whole lifecycle. Two states, on purpose.

    ``generated`` is a draft the lead may review; ``finalized`` is history. The
    spec asks for the smallest lifecycle that makes payout history immutable, and
    a correction workflow is named as a *later* phase rather than a third state —
    so there is no ``reversed``, no ``paid`` and no ``void`` here. Payment
    execution is out of scope, which is why ``finalized`` is the terminal state.
    """

    GENERATED = "generated", "Generated"
    FINALIZED = "finalized", "Finalized"


class StatementStatus(models.TextChoices):
    """How settled one teacher's period is. A statement is computed, never stored.

    Four honest answers rather than two, because a period can legitimately be
    half-finalized while the lead works through it. ``empty`` is not "zero
    earned" — it is "no payout records exist for this period", which is the
    difference between a missing statement and a statement of nothing (the same
    distinction assessment draws between a missing assessment and a zero score).
    """

    EMPTY = "empty", "No payout records in this period"
    GENERATED = "generated", "Generated, none finalized"
    PARTLY_FINALIZED = "partly_finalized", "Partly finalized"
    FINALIZED = "finalized", "Fully finalized"


#: Fields a saved payout may never change. The only permitted transition on an
#: existing record is ``generated`` → ``finalized``, which touches ``status`` and
#: ``finalized_at`` and nothing else.
IMMUTABLE_FIELDS = (
    "teacher_id",
    "booking_id",
    "cohort_id",
    "minutes_paid",
    "rate_used",
    "amount",
    "currency",
)


class TeacherPayoutQuerySet(models.QuerySet):
    def in_organization(self, organization):
        """The payouts that belong to one academy through their booking's track."""
        if organization is None:
            return self.none()
        organization_id = getattr(organization, "pk", organization)
        return self.filter(booking__level__track__organization_id=organization_id)


class TeacherPayout(models.Model):
    """One teacher's earnings from one eligible completed teaching session.

    Usually one row per ``Booking``. For a group class it is one row per *cohort
    session*, attached to the earliest seat of that cohort: the seats share a
    teacher, a level and an instant, so paying each of them would pay the teacher
    once per student for teaching once. ``cohort`` is therefore stored on the
    payout as well — denormalized from ``booking.cohort`` purely so the database
    can hold the "one payout per cohort session" rule as a constraint rather than
    trusting the service that writes it.

    Deleting rows is not part of normal behaviour: ``booking`` and ``teacher`` are
    both PROTECT, so a payout keeps its own evidence alive, and the admin refuses
    to delete a finalized one.
    """

    objects = TeacherPayoutQuerySet.as_manager()

    teacher = models.ForeignKey(
        # PROTECT for the reason ``Booking.teacher`` is PROTECT, only more so: a
        # payout is a statement about what this person is owed, so removing the
        # person has to be deliberate rather than a cascade that quietly erases
        # the academy's own financial history.
        User,
        on_delete=models.PROTECT,
        related_name="payouts",
        help_text="The teacher who taught the session. Always booking.teacher.",
    )
    booking = models.OneToOneField(
        Booking,
        on_delete=models.PROTECT,
        related_name="payout",
        help_text=(
            "The completed session earning this payout. OneToOne is the "
            "one-payout-per-booking rule; for a cohort it is the earliest seat "
            "of the session, and the other seats earn nothing of their own."
        ),
    )
    cohort = models.ForeignKey(
        Cohort,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payouts",
        help_text=(
            "Set when the paid session is a group class, so the unique "
            "constraint below can enforce one payout per cohort session. Always "
            "equal to booking.cohort."
        ),
    )
    minutes_paid = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
        help_text=(
            "Session length used in the calculation, snapshotted from the "
            "booking. Stored rather than derived so the record explains its own "
            "amount without depending on a row that could later be corrected."
        ),
    )
    rate_used = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(MINIMUM_AMOUNT)],
        help_text=(
            "The teacher's hourly rate at generation time, snapshotted the same "
            "way PricingAgreement.standard_rate is: changing the live rate must "
            "not reprice a payout that has already been made."
        ),
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(MINIMUM_AMOUNT)],
        help_text="minutes_paid / 60 × rate_used, rounded once. See payout_amount().",
    )
    currency = models.CharField(
        max_length=3,
        default=PAYOUT_CURRENCY,
        help_text="ISO code, snapshotted. NGN today; conversion is out of scope.",
    )
    status = models.CharField(
        max_length=16,
        choices=PayoutStatus.choices,
        default=PayoutStatus.GENERATED,
        help_text="generated → finalized. Finalized records are immutable.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Stamped when the lead finalizes. Null while generated.",
    )

    class Meta:
        # Chronological by the session that earned it, matching Booking's own
        # ordering — which is what a statement reads as. The join this costs is
        # paid back immediately: every list view here select_relates the booking.
        ordering = ["booking__start_time_utc", "booking__pk"]
        constraints = [
            # DB backstop for "a group class is paid once". The service checks it
            # first and gives a better answer (the seat is reported as skipped),
            # so this only fires for a hand-written INSERT or a second concurrent
            # generation of the same period. Deduplication is by cohort_id, which
            # is exact while a cohort has a single schedule_start_utc — the same
            # caveat weekly_committed_minutes() carries, and the same fix if
            # recurring cohorts ever land (tech-debt.md).
            models.UniqueConstraint(
                fields=["cohort"],
                condition=Q(cohort__isnull=False),
                name="unique_payout_per_cohort_session",
                violation_error_message=(
                    "That group-class session has already been paid; a cohort "
                    "earns one payout, not one per seat."
                ),
            ),
            # finalized_at exists exactly when the record is finalized. Without
            # this, a stamped-but-generated row could claim to be history while
            # still being editable.
            models.CheckConstraint(
                condition=(
                    Q(status=PayoutStatus.GENERATED, finalized_at__isnull=True)
                    | Q(status=PayoutStatus.FINALIZED, finalized_at__isnull=False)
                ),
                name="payout_finalized_at_matches_status",
                violation_error_message=(
                    "finalized_at is set exactly when status is 'finalized'."
                ),
            ),
        ]

    # --- Behaviour ----------------------------------------------------------

    @property
    def organization(self):
        """The academy this payout belongs to, reached through booking.level.track."""
        if self.booking_id:
            return self.booking.organization
        return None

    @property
    def is_finalized(self) -> bool:
        return self.status == PayoutStatus.FINALIZED

    @property
    def session_start_utc(self):
        """The instant that decides which period this payout belongs to.

        Always the booking's stored UTC start, never ``created_at``: a payout
        generated in September for an August session is August's earnings. The
        spec is explicit that generation time must not decide period membership.
        """
        return self.booking.start_time_utc

    def finalize(self, *, when=None):
        """Move ``generated`` → ``finalized``, stamping the moment. Lead-only.

        The one transition this model supports, and the point at which the record
        stops being editable at all — ``clean()`` refuses every later write. A
        second finalization raises rather than passing silently, because it means
        the caller is working from a stale copy of a financial record.
        """
        if self.is_finalized:
            raise PayoutAlreadyFinalized(
                f"Payout {self.pk} was already finalized at {self.finalized_at}."
            )
        self.status = PayoutStatus.FINALIZED
        self.finalized_at = when or dj_timezone.now()
        self.save()
        return self

    # --- Validation ---------------------------------------------------------

    def _stored(self):
        """This row as the database currently has it, or None if it is new."""
        if self._state.adding:
            return None
        return type(self).objects.filter(pk=self.pk).first()

    def _validate_still_mutable(self, errors, stored):
        """A finalized payout is history; a generated one may only be finalized."""
        if stored.status == PayoutStatus.FINALIZED:
            errors[NON_FIELD_ERRORS] = ValidationError(
                "Payout %(pk)s was finalized on %(when)s and cannot be changed. "
                "A correction is a new decision, not an edit to an old one.",
                code="payout_finalized",
                params={"pk": self.pk, "when": stored.finalized_at},
            )
            return
        for field in IMMUTABLE_FIELDS:
            if getattr(self, field) != getattr(stored, field):
                errors[field.removesuffix("_id")] = ValidationError(
                    "A payout's %(field)s is fixed at generation. The only "
                    "permitted change to an existing payout is finalizing it.",
                    code="payout_field_immutable",
                    params={"field": field.removesuffix("_id")},
                )

    def _validate_earns_a_payout(self, errors):
        """Creation-time eligibility: the booking really was taught, by this teacher.

        Checked on creation only, following the precedent ``Booking.clean()``
        sets for its own creation-time rules: a rule that keeps re-running on
        every save eventually makes a historical record unsaveable, which for a
        financial row would mean it could not even be finalized.
        """
        booking = self.booking
        if booking.teacher_id is None:
            errors["booking"] = ValidationError(
                "A booking with no teacher earns no payout.",
                code="payout_booking_unassigned",
            )
        if booking.status not in PAYABLE_BOOKING_STATUSES:
            errors["booking"] = ValidationError(
                "Only a completed session earns a payout (booking %(pk)s is "
                "'%(status)s').",
                code="payout_booking_not_completed",
                params={"pk": booking.pk, "status": booking.status},
            )

    def clean(self):
        errors = {}

        stored = self._stored()
        if stored is not None:
            self._validate_still_mutable(errors, stored)

        if self.booking_id:
            if self.organization is None:
                errors["booking"] = ValidationError(
                    "A booking must belong to an academy to earn a payout.",
                    code="payout_booking_unscoped",
                )

            if self.teacher_id and self.booking.teacher_id != self.teacher_id:
                # The payout must be owed to whoever actually taught. Enforced
                # here as well as in the service, so a direct ORM write cannot
                # credit one teacher's session to another.
                errors["teacher"] = ValidationError(
                    "A payout is owed to the teacher who taught the session "
                    "(booking %(pk)s was taught by %(teacher)s).",
                    code="payout_teacher_mismatch",
                    params={
                        "pk": self.booking_id,
                        "teacher": self.booking.teacher.username,
                    },
                )
            if self.cohort_id != self.booking.cohort_id:
                errors["cohort"] = ValidationError(
                    "A payout's cohort must be the paid booking's cohort, so "
                    "that one group-class session cannot be paid twice.",
                    code="payout_cohort_mismatch",
                )
            elif (
                self.cohort_id
                and self.organization is not None
                and self.cohort.organization != self.organization
            ):
                errors["cohort"] = ValidationError(
                    "The cohort belongs to a different academy than the paid session.",
                    code="cross_academy_cohort_mismatch",
                )
            if stored is None:
                self._validate_earns_a_payout(errors)
                if self.teacher_id and self.organization is not None:
                    membership = active_membership(
                        user=self.teacher, organization=self.organization
                    )
                    if membership is None:
                        errors["teacher"] = ValidationError(
                            "That teacher is not an active member of the academy that owns this payout.",
                            code="teacher_not_in_organization",
                        )

        if self.teacher_id and not self.teacher.is_teacher:
            errors.setdefault(
                "teacher",
                ValidationError(
                    "Only a teacher account earns a payout (got '%(role)s').",
                    code="payout_invalid_teacher_role",
                    params={"role": self.teacher.role},
                ),
            )

        if self.minutes_paid and self.rate_used is not None:
            expected = payout_amount(self.minutes_paid, self.rate_used)
            if self.amount != expected:
                # The stored amount must be the one the formula produces, or the
                # record is not evidence of anything. This is what stops an
                # amount from being edited on its own.
                errors["amount"] = ValidationError(
                    "amount must be minutes_paid / 60 × rate_used = %(expected)s "
                    "(got %(actual)s).",
                    code="payout_amount_mismatch",
                    params={"expected": expected, "actual": self.amount},
                )

        stamped = self.finalized_at is not None
        if self.is_finalized != stamped:
            errors["finalized_at"] = ValidationError(
                "finalized_at is set exactly when status is 'finalized'.",
                code="payout_finalized_at_mismatch",
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Immutability, teacher agreement and the amount formula all read other
        # tables or the stored row, so none of them can be a DB constraint alone.
        # Validating in save() makes them hold for the admin and direct ORM
        # writes as well as the API — the same pattern ParentLink, Booking and
        # PricingAgreement use.
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return (
            f"{self.teacher.username}: {self.amount} {self.currency} for "
            f"{self.booking.start_time_utc:%Y-%m-%d %H:%M} UTC ({self.status})"
        )
