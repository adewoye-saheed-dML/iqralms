"""Generation and statement behaviour.

The financial invariants CLAUDE.md names, in the layer that actually decides
them: which sessions a period contains, what they pay, what happens when the run
is repeated, and the two separations the phase exists to protect — family pricing
and assessment must not touch a payout.
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import StudentFactory
from assessment.tests.factories import SessionAssessmentFactory
from pricing.tests.factories import PremiumAgreementFactory, PricingAgreementFactory
from scheduling.models import Booking, BookingStatus
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    BookingFactory,
    CohortFactory,
)

from payouts.models import PAYOUT_CURRENCY, PayoutStatus, StatementStatus, TeacherPayout
from payouts.services import (
    SKIP_ALREADY_PAID,
    SKIP_COHORT_SEAT_PAID_ELSEWHERE,
    SKIP_NO_PAYOUT_RATE,
    generate_payouts,
    payouts_for,
    statement_for,
)
from payouts.tests.factories import past_session

#: A window wide enough to contain every session these tests build.
PERIOD_START = dj_timezone.now() - timedelta(weeks=4)
PERIOD_END = dj_timezone.now() + timedelta(weeks=4)


def rated_window(rate="5000.00"):
    """An availability window whose teacher earns ``rate`` an hour.

    The rate lives on the teacher's profile, which ``BookableTeacherFactory``
    builds in a post-generation hook — hence the long keyword path. Wrapped here
    so the tests read as what they are about (a 5000/hour teacher) rather than as
    factory plumbing.
    """
    return AvailabilityFactory(
        teacher__teacher_profile__hourly_payout_rate=Decimal(rate)
    )


def run(*, start=None, end=None, teacher=None, organization=None):
    if organization is None:
        booking = Booking.objects.order_by("-pk").first()
        if booking is not None and booking.organization is not None:
            organization = booking.organization
        else:
            from organizations.tests.factories import OrganizationFactory
            organization = OrganizationFactory()
    return generate_payouts(
        organization=organization,
        period_start=start or PERIOD_START,
        period_end=end or PERIOD_END,
        teacher=teacher,
    )


class EligibilityTests(TestCase):
    """Only completed sessions pay, and only inside the period."""

    def test_completed_session_produces_one_payout(self):
        booking = past_session()
        result = run()
        self.assertEqual(result.created_count, 1)
        payout = result.created[0]
        self.assertEqual(payout.booking, booking)
        self.assertEqual(payout.teacher, booking.teacher)
        self.assertEqual(payout.minutes_paid, booking.duration_minutes)
        self.assertEqual(payout.currency, PAYOUT_CURRENCY)

    def test_cancelled_and_no_show_sessions_pay_nothing(self):
        for status in (BookingStatus.CANCELLED, BookingStatus.NO_SHOW):
            with self.subTest(status=status):
                booking = past_session()
                booking.status = status
                booking.save()
                self.assertEqual(run().created_count, 0)
                self.assertFalse(TeacherPayout.objects.exists())

    def test_scheduled_session_pays_nothing_yet(self):
        BookingFactory(availability=AvailabilityFactory())
        self.assertEqual(run().created_count, 0)

    def test_generation_does_not_touch_booking_status(self):
        booking = past_session()
        run()
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.COMPLETED)

    def test_a_teacher_with_no_rate_is_skipped_not_paid(self):
        # The lead teaches their own session. hourly_payout_rate is null for the
        # lead by decision, so the session is reported rather than paid at zero.
        window = AvailabilityFactory(teacher=BookableLeadTeacherFactory())
        booking = past_session(
            teacher=window.teacher, start_time_utc=PERIOD_START + timedelta(days=1)
        )
        result = run()
        self.assertEqual(result.created_count, 0)
        self.assertEqual(
            [(skip.booking_id, skip.reason) for skip in result.skipped],
            [(booking.pk, SKIP_NO_PAYOUT_RATE)],
        )


class PeriodBoundaryTests(TestCase):
    """``[start, end)`` — inclusive start, exclusive end, on stored UTC starts."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.start = dj_timezone.now().replace(microsecond=0) - timedelta(weeks=2)
        self.end = self.start + timedelta(days=7)

    def _session_at(self, moment):
        return past_session(
            teacher=self.window.teacher,
            student=StudentFactory(),
            start_time_utc=moment,
        )

    def test_a_session_exactly_at_the_start_is_included(self):
        booking = self._session_at(self.start)
        result = run(start=self.start, end=self.end)
        self.assertEqual([p.booking_id for p in result.created], [booking.pk])

    def test_a_session_exactly_at_the_end_belongs_to_the_next_period(self):
        self._session_at(self.end)
        self.assertEqual(run(start=self.start, end=self.end).created_count, 0)

    def test_a_session_before_the_start_is_excluded(self):
        self._session_at(self.start - timedelta(seconds=1))
        self.assertEqual(run(start=self.start, end=self.end).created_count, 0)

    def test_consecutive_periods_pay_each_session_exactly_once(self):
        first = self._session_at(self.start)
        second = self._session_at(self.end)
        run(start=self.start, end=self.end)
        run(start=self.end, end=self.end + timedelta(days=7))
        self.assertEqual(
            sorted(TeacherPayout.objects.values_list("booking_id", flat=True)),
            sorted([first.pk, second.pk]),
        )


class RateTests(TestCase):
    """The rate applied is the teacher's rate *at generation time*, and it sticks."""

    def test_amount_is_duration_times_rate(self):
        window = rated_window()
        booking = past_session(teacher=window.teacher, duration_minutes=60)
        payout = run().created[0]
        self.assertEqual(payout.rate_used, Decimal("5000.00"))
        self.assertEqual(payout.amount, Decimal("5000.00"))
        self.assertEqual(payout.minutes_paid, booking.duration_minutes)

    def test_half_hour_session_pays_half_the_hourly_rate(self):
        window = rated_window()
        past_session(teacher=window.teacher, duration_minutes=30)
        self.assertEqual(run().created[0].amount, Decimal("2500.00"))

    def test_a_later_rate_change_leaves_the_old_payout_alone(self):
        window = rated_window()
        teacher = window.teacher
        january = past_session(
            teacher=teacher, duration_minutes=60, start_time_utc=PERIOD_START + timedelta(days=1)
        )
        run()
        old = TeacherPayout.objects.get(booking=january)
        old.finalize()

        profile = teacher.teacher_profile
        profile.hourly_payout_rate = Decimal("7000.00")
        profile.save()

        february = past_session(
            teacher=teacher,
            student=StudentFactory(),
            duration_minutes=60,
            start_time_utc=PERIOD_START + timedelta(days=8),
        )
        run()

        old.refresh_from_db()
        new = TeacherPayout.objects.get(booking=february)
        self.assertEqual((old.rate_used, old.amount), (Decimal("5000.00"), Decimal("5000.00")))
        self.assertEqual((new.rate_used, new.amount), (Decimal("7000.00"), Decimal("7000.00")))


class IdempotencyTests(TestCase):
    """Repeating a run creates nothing and changes nothing."""

    def test_second_run_creates_no_duplicates(self):
        past_session()
        first = run()
        second = run()
        self.assertEqual(first.created_count, 1)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(
            [skip.reason for skip in second.skipped], [SKIP_ALREADY_PAID]
        )
        self.assertEqual(TeacherPayout.objects.count(), 1)

    def test_a_finalized_payout_is_not_regenerated_or_rewritten(self):
        past_session()
        payout = run().created[0]
        payout.finalize()
        run()
        payout.refresh_from_db()
        self.assertEqual(TeacherPayout.objects.count(), 1)
        self.assertEqual(payout.status, PayoutStatus.FINALIZED)


class CohortGenerationTests(TestCase):
    """Six students in one class is one teacher teaching once — and one payout."""

    def setUp(self):
        self.window = rated_window()
        self.start = dj_timezone.now() - timedelta(weeks=1)
        self.cohort = CohortFactory(
            availability=self.window, schedule_start_utc=self.start
        )
        self.seats = [
            past_session(
                teacher=self.cohort.teacher,
                level=self.cohort.level,
                cohort=self.cohort,
                student=StudentFactory(),
                start_time_utc=self.start,
                duration_minutes=60,
            )
            for _ in range(3)
        ]

    def test_a_cohort_session_pays_once(self):
        result = run()
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.created[0].amount, Decimal("5000.00"))
        self.assertEqual(
            [skip.reason for skip in result.skipped],
            [SKIP_COHORT_SEAT_PAID_ELSEWHERE] * 2,
        )

    def test_the_paid_seat_is_the_earliest_one_deterministically(self):
        run()
        payout = TeacherPayout.objects.get()
        self.assertEqual(payout.booking_id, min(seat.pk for seat in self.seats))
        self.assertEqual(payout.cohort, self.cohort)

    def test_repeating_the_run_still_pays_the_cohort_once(self):
        run()
        run()
        self.assertEqual(TeacherPayout.objects.count(), 1)


class SeparationFromFamilyPricingTests(TestCase):
    """What the family pays never becomes what the teacher earns."""

    def setUp(self):
        self.window = rated_window()
        self.booking = past_session(teacher=self.window.teacher, duration_minutes=60)

    def test_a_premium_agreement_does_not_raise_the_payout(self):
        PremiumAgreementFactory(
            student=self.booking.student, level=self.booking.level
        )
        payout = run().created[0]
        self.assertEqual(payout.rate_used, Decimal("5000.00"))
        self.assertEqual(payout.amount, Decimal("5000.00"))

    def test_a_hardship_discount_does_not_lower_the_payout(self):
        PricingAgreementFactory(
            student=self.booking.student,
            level=self.booking.level,
            agreed_rate=Decimal("0.00"),
        )
        payout = run().created[0]
        self.assertEqual(payout.amount, Decimal("5000.00"))

    def test_repricing_the_family_afterwards_leaves_the_payout_alone(self):
        payout = run().created[0]
        PremiumAgreementFactory(
            student=self.booking.student,
            level=self.booking.level,
            agreed_rate=Decimal("99999.00"),
        )
        payout.refresh_from_db()
        self.assertEqual(payout.amount, Decimal("5000.00"))


class SeparationFromAssessmentTests(TestCase):
    """Teaching quality is measured in Phase 7 and paid for in none of it."""

    def setUp(self):
        self.window = rated_window()
        self.booking = past_session(teacher=self.window.teacher, duration_minutes=60)

    def test_a_perfect_assessment_does_not_raise_the_payout(self):
        SessionAssessmentFactory(booking=self.booking, score_value=5)
        self.assertEqual(run().created[0].amount, Decimal("5000.00"))

    def test_a_poor_assessment_does_not_lower_the_payout(self):
        SessionAssessmentFactory(booking=self.booking, score_value=1)
        self.assertEqual(run().created[0].amount, Decimal("5000.00"))

    def test_assessing_after_generation_leaves_the_payout_alone(self):
        payout = run().created[0]
        SessionAssessmentFactory(booking=self.booking, score_value=1)
        payout.refresh_from_db()
        self.assertEqual(payout.amount, Decimal("5000.00"))


class StatementTests(TestCase):
    """A statement is the sum of the records it lists, and nothing else."""

    def setUp(self):
        self.window = rated_window()
        self.teacher = self.window.teacher
        self.bookings = [
            past_session(
                teacher=self.teacher,
                student=StudentFactory(),
                duration_minutes=minutes,
                start_time_utc=PERIOD_START + timedelta(days=day),
            )
            for day, minutes in ((1, 60), (2, 30), (3, 20))
        ]
        run()

    def _statement(self):
        return statement_for(
            organization=self.bookings[0].organization,
            teacher=self.teacher,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )

    def test_total_agrees_with_the_underlying_records(self):
        statement = self._statement()
        self.assertEqual(statement.session_count, 3)
        # 5000 + 2500 + 1666.67, each rounded once at its own record.
        self.assertEqual(statement.total_amount, Decimal("9166.67"))
        self.assertEqual(
            statement.total_amount,
            sum(payout.amount for payout in statement.payouts),
        )
        self.assertEqual(statement.currency, PAYOUT_CURRENCY)

    def test_a_generated_period_is_not_finalized(self):
        statement = self._statement()
        self.assertEqual(statement.status, StatementStatus.GENERATED)
        self.assertEqual(statement.finalized_count, 0)

    def test_finalizing_some_records_shows_as_partly_finalized(self):
        TeacherPayout.objects.first().finalize()
        self.assertEqual(self._statement().status, StatementStatus.PARTLY_FINALIZED)

    def test_finalizing_every_record_finalizes_the_statement(self):
        for payout in TeacherPayout.objects.all():
            payout.finalize()
        statement = self._statement()
        self.assertEqual(statement.status, StatementStatus.FINALIZED)
        self.assertEqual(statement.finalized_count, 3)

    def test_a_period_with_no_records_is_empty_rather_than_zero_earnings(self):
        empty = statement_for(
            organization=self.bookings[0].organization,
            teacher=self.teacher,
            period_start=PERIOD_END,
            period_end=PERIOD_END + timedelta(weeks=1),
        )
        self.assertEqual(empty.status, StatementStatus.EMPTY)
        self.assertEqual(empty.session_count, 0)
        self.assertEqual(empty.total_amount, Decimal("0"))

    def test_a_statement_contains_nobody_elses_sessions(self):
        other = past_session(start_time_utc=PERIOD_START + timedelta(days=4))
        run()
        statement = self._statement()
        self.assertNotIn(other.pk, [payout.booking_id for payout in statement.payouts])
        self.assertEqual(statement.session_count, 3)


class PayoutTenancyServiceTests(TestCase):
    """SaaS Phase 7: service-layer tenancy and generation isolation."""

    def setUp(self):
        from curriculum.tests.factories import LevelFactory, TrackFactory
        from organizations.tests.factories import OrganizationFactory

        self.org_a = OrganizationFactory(name="Academy A")
        self.org_b = OrganizationFactory(name="Academy B")

        self.track_a = TrackFactory(organization=self.org_a)
        self.level_a = LevelFactory(track=self.track_a)

        self.track_b = TrackFactory(organization=self.org_b)
        self.level_b = LevelFactory(track=self.track_b)

    def test_generation_for_academy_a_never_touches_academy_b_bookings(self):
        # Create completed sessions in both academies
        booking_a = past_session(level=self.level_a, start_time_utc=PERIOD_START + timedelta(days=1))
        booking_b = past_session(level=self.level_b, start_time_utc=PERIOD_START + timedelta(days=1))

        # Generate for Academy A
        result_a = generate_payouts(
            organization=self.org_a,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        self.assertEqual(result_a.created_count, 1)
        self.assertEqual(result_a.created[0].booking, booking_a)

        # Confirm Academy B has 0 payouts
        self.assertEqual(TeacherPayout.objects.in_organization(self.org_b).count(), 0)

        # Generate for Academy B
        result_b = generate_payouts(
            organization=self.org_b,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        self.assertEqual(result_b.created_count, 1)
        self.assertEqual(result_b.created[0].booking, booking_b)

    def test_service_functions_require_explicit_organization(self):
        with self.assertRaises(ValueError):
            generate_payouts(
                organization=None,
                period_start=PERIOD_START,
                period_end=PERIOD_END,
            )
        with self.assertRaises(ValueError):
            payouts_for(organization=None)
        with self.assertRaises(ValueError):
            statement_for(
                organization=None,
                teacher=StudentFactory(),
                period_start=PERIOD_START,
                period_end=PERIOD_END,
            )

    def test_statement_contains_only_tenant_owned_payouts(self):
        from scheduling.tests.factories import ensure_teacher_configured

        window_a = rated_window("5000.00")
        teacher = window_a.teacher
        ensure_teacher_configured(teacher, self.org_a)
        ensure_teacher_configured(teacher, self.org_b)

        booking_a = past_session(
            level=self.level_a,
            teacher=teacher,
            start_time_utc=PERIOD_START + timedelta(days=1),
        )
        booking_b = past_session(
            level=self.level_b,
            teacher=teacher,
            start_time_utc=PERIOD_START + timedelta(days=2),
        )

        generate_payouts(organization=self.org_a, period_start=PERIOD_START, period_end=PERIOD_END)
        generate_payouts(organization=self.org_b, period_start=PERIOD_START, period_end=PERIOD_END)

        stmt_a = statement_for(
            organization=self.org_a,
            teacher=teacher,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        self.assertEqual(stmt_a.session_count, 1)
        self.assertEqual(stmt_a.payouts[0].booking, booking_a)

        stmt_b = statement_for(
            organization=self.org_b,
            teacher=teacher,
            period_start=PERIOD_START,
            period_end=PERIOD_END,
        )
        self.assertEqual(stmt_b.session_count, 1)
        self.assertEqual(stmt_b.payouts[0].booking, booking_b)
