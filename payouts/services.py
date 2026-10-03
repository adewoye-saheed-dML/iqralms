"""Payout generation and statements — the only supported way a payout is written.

Two entry points, and both are deliberately narrow:

* ``generate_payouts`` turns the completed bookings of a bounded period into
  payout records. It is idempotent: running it twice over the same period creates
  nothing the second time, and it never touches a record that already exists —
  finalized or not.
* ``statement_for`` totals one teacher's payouts for a bounded period. A
  statement is a *reporting view over payout records*, so there is no statement
  model and no second copy of any financial fact: the total is computed from the
  rows every time it is asked for, and therefore cannot drift from them.

Generation never writes to ``Booking``. Marking a session taught is the
scheduling app's decision, and a payout run that could change a booking's status
would make "which sessions were completed" depend on who ran the payroll.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.db.models import Count, Q, Sum

from scheduling.models import Booking

from accounts.models import CompensationType

from .models import (
    MINIMUM_AMOUNT,
    MONEY_PRECISION,
    PAYOUT_CURRENCY,
    PAYABLE_BOOKING_STATUSES,
    PayoutStatus,
    StatementStatus,
    TeacherPayout,
    payout_amount,
)

#: Why an eligible-looking booking produced no payout. Reported rather than
#: swallowed: a generation run that silently skipped half a period would look
#: exactly like a period with half as much teaching in it.
SKIP_ALREADY_PAID = "already_paid"
SKIP_COHORT_SESSION_ALREADY_PAID = "cohort_session_already_paid"
SKIP_COHORT_SEAT_PAID_ELSEWHERE = "cohort_seat_paid_with_another_seat"
SKIP_NO_PAYOUT_RATE = "no_payout_rate"

#: Everything the payout serializers and the amount calculation touch, in one
#: query per generation run rather than one per booking.
BOOKING_RELATED = (
    "teacher",
    "teacher__teacher_profile",
    "student",
    "level",
    "level__track",
    "cohort",
)

#: The same set, reached through the payout rather than from the booking. Every
#: read endpoint in this app uses it, so a payout listing is one query.
PAYOUT_RELATED = ("teacher", "cohort", "booking") + tuple(
    f"booking__{relation}" for relation in ("student", "level", "level__track")
)


@dataclass(frozen=True)
class SkippedBooking:
    """One booking generation looked at and deliberately did not pay."""

    booking_id: int
    teacher_id: int
    reason: str


@dataclass
class GenerationResult:
    """What one generation run did. Both halves matter to the lead reading it."""

    period_start: datetime
    period_end: datetime
    created: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def total_amount(self) -> Decimal:
        return sum((payout.amount for payout in self.created), MINIMUM_AMOUNT)


@dataclass
class Statement:
    """One teacher's earnings for one period, computed from the payout records.

    ``session_count`` counts payout records, which for a group class is one per
    cohort session rather than one per seat — the same unit the amount is
    calculated in, so the two always agree.
    """

    teacher: object
    period_start: datetime
    period_end: datetime
    session_count: int
    finalized_count: int
    total_amount: Decimal
    currency: str
    status: str
    payouts: list


def applicable_compensation(teacher, *, organization=None):
    """Return (compensation_type, rate) for a teaching session.

    If compensation_type is fixed_period, rate is None because fixed period teachers
    do not earn per-session payouts.
    """
    if organization is not None:
        from accounts.models import OrganizationTeacherConfiguration
        from organizations.models import active_membership

        membership = active_membership(user=teacher, organization=organization)
        if membership is None:
            return (None, None)

        config = OrganizationTeacherConfiguration.objects.filter(
            membership=membership,
        ).first()
        if config is None or not config.approved:
            return (None, None)

        if config.compensation_type == CompensationType.PER_CLASS:
            return (CompensationType.PER_CLASS, config.per_class_rate)
        elif config.compensation_type == CompensationType.FIXED_PERIOD:
            return (CompensationType.FIXED_PERIOD, None)
        return (CompensationType.HOURLY, config.hourly_payout_rate)

    profile = getattr(teacher, "teacher_profile", None)
    if profile is None:
        return (None, None)
    return (CompensationType.HOURLY, profile.hourly_payout_rate)


def applicable_rate(teacher, *, organization=None):
    """The rate ``teacher`` earns per hour (or per class) today, or None if they have none."""
    comp_type, rate = applicable_compensation(teacher, organization=organization)
    return rate


def eligible_bookings(*, organization, period_start, period_end, teacher=None):
    """Completed sessions whose stored UTC start falls in ``[start, end)`` inside ``organization``.

    Half-open on purpose, so consecutive periods neither overlap nor leave a gap:
    a session at exactly ``period_end`` belongs to the next period. Filtering is
    on ``start_time_utc``, the stored UTC instant, so a caller's local timezone
    cannot change which period a session lands in.

    Status comes from ``PAYABLE_BOOKING_STATUSES``, which is derived from
    ``scheduling.BookingStatus`` — this app never re-defines what "completed"
    means.

    Constrained to ``organization`` through ``Booking.objects.in_organization``
    so a generation request never reads sessions from another academy.
    """
    if organization is None:
        raise ValueError("An explicit organization context is required.")
    rows = Booking.objects.in_organization(organization).filter(
        status__in=PAYABLE_BOOKING_STATUSES,
        start_time_utc__gte=period_start,
        start_time_utc__lt=period_end,
        teacher__isnull=False,
    )
    if teacher is not None:
        rows = rows.filter(teacher=getattr(teacher, "pk", teacher))
    # Deterministic order: the earliest seat of a cohort is the one that gets the
    # payout, so "earliest" has to mean the same thing on every run.
    return rows.select_related(*BOOKING_RELATED).order_by("start_time_utc", "pk")


@transaction.atomic
def generate_payouts(*, organization, period_start, period_end, teacher=None):
    """Create the missing payout records for ``[period_start, period_end)`` in ``organization``.

    Idempotent by construction, which the spec requires and which matters more
    than usual here: a lead who is unsure whether the run went through must be
    able to repeat it.

    * Only bookings belonging to ``organization`` are considered.
    * A booking that already has a payout is skipped, whatever that payout's
      status is. Existing records are never recalculated or touched.
    * A cohort session whose payout already exists in this academy is skipped for
      *every* seat, so a group class is paid once no matter how many students sat
      in it or how many times generation runs.
    * The database holds both rules as constraints, so two concurrent runs cannot
      both win.

    One ``atomic()`` block for the whole run, so a rejection mid-period leaves no
    half-generated payroll. Records are created one ``save()`` at a time — no
    ``bulk_create()``, which would bypass ``TeacherPayout.full_clean()`` and with
    it every invariant in this app.
    """
    if organization is None:
        raise ValueError("An explicit organization context is required for payout generation.")
    result = GenerationResult(period_start=period_start, period_end=period_end)
    bookings = list(
        eligible_bookings(
            organization=organization,
            period_start=period_start,
            period_end=period_end,
            teacher=teacher,
        )
    )
    if not bookings:
        return result

    booking_ids = [booking.pk for booking in bookings]
    cohort_ids = {booking.cohort_id for booking in bookings if booking.cohort_id}
    paid_bookings = set(
        TeacherPayout.objects.in_organization(organization)
        .filter(booking_id__in=booking_ids)
        .values_list("booking_id", flat=True)
    )
    # Any seat of the cohort having been paid closes the whole session, including
    # seats outside this period's booking set.
    paid_cohorts = set(
        TeacherPayout.objects.in_organization(organization)
        .filter(cohort_id__in=cohort_ids)
        .values_list("cohort_id", flat=True)
    )
    #: Cohorts paid by *this* run, so the second seat is reported with a reason of
    #: its own rather than looking like a pre-existing record.
    paid_here = set()

    for booking in bookings:
        if booking.pk in paid_bookings:
            result.skipped.append(_skip(booking, SKIP_ALREADY_PAID))
            continue
        if booking.cohort_id:
            if booking.cohort_id in paid_here:
                result.skipped.append(
                    _skip(booking, SKIP_COHORT_SEAT_PAID_ELSEWHERE)
                )
                continue
            if booking.cohort_id in paid_cohorts:
                result.skipped.append(
                    _skip(booking, SKIP_COHORT_SESSION_ALREADY_PAID)
                )
                continue

        comp_type, rate = applicable_compensation(booking.teacher, organization=organization)
        if rate is None:
            # The lead teaching their own session lands here, by decision: they
            # are not paid per hour. Reported, so a genuinely unset sub-teacher
            # rate is visible to whoever ran the payroll instead of vanishing.
            result.skipped.append(_skip(booking, SKIP_NO_PAYOUT_RATE))
            continue

        if comp_type == CompensationType.PER_CLASS:
            amount = Decimal(rate).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
        else:
            amount = payout_amount(booking.duration_minutes, rate)

        payout = TeacherPayout(
            teacher=booking.teacher,
            booking=booking,
            cohort=booking.cohort,
            compensation_type=comp_type,
            minutes_paid=booking.duration_minutes,
            rate_used=rate,
            amount=amount,
            currency=PAYOUT_CURRENCY,
        )
        payout.save()
        result.created.append(payout)
        paid_bookings.add(booking.pk)
        if booking.cohort_id:
            paid_here.add(booking.cohort_id)

    return result


def _skip(booking, reason):
    return SkippedBooking(
        booking_id=booking.pk, teacher_id=booking.teacher_id, reason=reason
    )


def payouts_for(*, organization, teacher=None, period_start=None, period_end=None):
    """The payout records for a teacher and/or a period inside ``organization``, newest session last.

    The single scoping helper every view uses, so "only your own payouts" is one
    filter in one place rather than a rule each endpoint reimplements. The period
    bounds are the same half-open pair generation uses, and again read from the
    booking's stored UTC start rather than from ``created_at``.
    """
    if organization is None:
        raise ValueError("An explicit organization context is required.")
    rows = TeacherPayout.objects.in_organization(organization).select_related(*PAYOUT_RELATED)
    if teacher is not None:
        rows = rows.filter(teacher=getattr(teacher, "pk", teacher))
    if period_start is not None:
        rows = rows.filter(booking__start_time_utc__gte=period_start)
    if period_end is not None:
        rows = rows.filter(booking__start_time_utc__lt=period_end)
    return rows


def statement_for(*, organization, teacher, period_start, period_end):
    """Total one teacher's payouts for ``[period_start, period_end)`` in ``organization``.

    Computed, never stored. The count and the total come from one aggregate over
    the same queryset the statement lists, so a statement cannot disagree with
    the records it is a view of — which is the whole reason there is no
    ``TeacherStatement`` model.

    An empty period is a statement of zero sessions and 0.00, with status
    ``empty``: "nothing has been generated for this period yet" and "this teacher
    earned nothing" are different statements, and only the records can tell the
    lead which one they are looking at.
    """
    if organization is None:
        raise ValueError("An explicit organization context is required.")
    rows = payouts_for(
        organization=organization,
        teacher=teacher,
        period_start=period_start,
        period_end=period_end,
    )
    totals = rows.aggregate(
        session_count=Count("pk"),
        total_amount=Sum("amount"),
        finalized_count=Count("pk", filter=Q(status=PayoutStatus.FINALIZED)),
    )
    session_count = totals["session_count"] or 0
    finalized_count = totals["finalized_count"] or 0
    return Statement(
        teacher=teacher,
        period_start=period_start,
        period_end=period_end,
        session_count=session_count,
        finalized_count=finalized_count,
        total_amount=totals["total_amount"] or MINIMUM_AMOUNT,
        currency=PAYOUT_CURRENCY,
        status=_statement_status(session_count, finalized_count),
        payouts=list(rows),
    )


def _statement_status(session_count, finalized_count):
    if session_count == 0:
        return StatementStatus.EMPTY
    if finalized_count == 0:
        return StatementStatus.GENERATED
    if finalized_count == session_count:
        return StatementStatus.FINALIZED
    return StatementStatus.PARTLY_FINALIZED


@dataclass
class FixedPeriodGenerationResult:
    period_start: datetime
    period_end: datetime
    created: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def total_amount(self) -> Decimal:
        return sum((p.amount for p in self.created), MINIMUM_AMOUNT)


@transaction.atomic
def generate_fixed_period_payouts(*, organization, period_start, period_end, teacher=None):
    """Generate fixed period payouts for eligible approved teachers with fixed_period compensation."""
    if organization is None:
        raise ValueError("An explicit organization context is required for fixed payout generation.")
    from accounts.models import CompensationType, OrganizationTeacherConfiguration
    from organizations.models import Organization, active_membership
    from .models import TeacherFixedPeriodPayout

    result = FixedPeriodGenerationResult(period_start=period_start, period_end=period_end)

    org_obj = organization if hasattr(organization, "pk") else Organization.objects.get(pk=organization)

    configs_qs = OrganizationTeacherConfiguration.objects.filter(
        membership__organization=org_obj,
        membership__status="active",
        approved=True,
        compensation_type=CompensationType.FIXED_PERIOD,
    ).select_related("membership", "membership__user")

    if teacher is not None:
        configs_qs = configs_qs.filter(membership__user=getattr(teacher, "pk", teacher))

    existing_payout_teachers = set(
        TeacherFixedPeriodPayout.objects.in_organization(org_obj)
        .filter(
            period_start=period_start,
            period_end=period_end,
        )
        .values_list("teacher_id", flat=True)
    )

    for config in configs_qs:
        teacher_obj = config.user
        if teacher_obj.pk in existing_payout_teachers:
            result.skipped.append({"teacher_id": teacher_obj.pk, "reason": "already_paid_for_period"})
            continue
        if config.fixed_period_amount is None or config.fixed_period_amount <= Decimal("0"):
            result.skipped.append({"teacher_id": teacher_obj.pk, "reason": "no_fixed_amount"})
            continue
        if not config.fixed_period_cadence:
            result.skipped.append({"teacher_id": teacher_obj.pk, "reason": "no_cadence"})
            continue

        payout = TeacherFixedPeriodPayout(
            teacher=teacher_obj,
            organization=org_obj,
            period_start=period_start,
            period_end=period_end,
            cadence=config.fixed_period_cadence,
            amount=config.fixed_period_amount,
            currency=PAYOUT_CURRENCY,
        )
        payout.save()
        result.created.append(payout)
        existing_payout_teachers.add(teacher_obj.pk)

    return result
