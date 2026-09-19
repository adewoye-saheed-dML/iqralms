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
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Q, Sum

from scheduling.models import Booking

from .models import (
    MINIMUM_AMOUNT,
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


def applicable_rate(teacher, *, organization=None):
    """The rate ``teacher`` earns per hour today, or None if they have none.

    B07: The authoritative rate source is ``OrganizationTeacherConfiguration.hourly_payout_rate``
    for the teacher's active membership in ``organization``.
    Falls back to ``TeacherProfile.hourly_payout_rate`` when unconfigured on the academy config.
    """
    if organization is not None:
        from accounts.models import OrganizationTeacherConfiguration
        from organizations.models import active_membership

        membership = active_membership(user=teacher, organization=organization)
        if membership is None:
            return None

        config = OrganizationTeacherConfiguration.objects.filter(
            membership=membership,
        ).first()
        if config is not None:
            if not config.approved:
                return None
            if config.hourly_payout_rate is not None:
                return config.hourly_payout_rate

    profile = getattr(teacher, "teacher_profile", None)
    if profile is None:
        return None
    return profile.hourly_payout_rate


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

        rate = applicable_rate(booking.teacher, organization=organization)
        if rate is None:
            # The lead teaching their own session lands here, by decision: they
            # are not paid per hour. Reported, so a genuinely unset sub-teacher
            # rate is visible to whoever ran the payroll instead of vanishing.
            result.skipped.append(_skip(booking, SKIP_NO_PAYOUT_RATE))
            continue

        payout = TeacherPayout(
            teacher=booking.teacher,
            booking=booking,
            cohort=booking.cohort,
            minutes_paid=booking.duration_minutes,
            rate_used=rate,
            amount=payout_amount(booking.duration_minutes, rate),
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
