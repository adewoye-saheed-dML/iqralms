"""Model-level invariants for TeacherPayout.

CLAUDE.md's testing policy asks for automated coverage of the durable, high-risk
financial rules rather than of routine CRUD, so this file tests exactly the rules
that would cost real money to get wrong: what may be paid, what the amount must
be, and what a finalized record refuses to become.

The API-layer half of the same list — who may generate, finalize and read — is in
``test_api.py``; period, idempotency and aggregation behaviour is in
``test_services.py``.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import StudentFactory
from scheduling.models import BookingStatus
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookingFactory,
    CohortFactory,
)

from payouts.exceptions import PayoutAlreadyFinalized
from payouts.models import PayoutStatus, TeacherPayout, payout_amount
from payouts.tests.factories import (
    FinalizedPayoutFactory,
    TeacherPayoutFactory,
    past_session,
)


class PayoutAmountTests(TestCase):
    """The one formula: minutes / 60 × rate, rounded once."""

    def test_whole_hour(self):
        self.assertEqual(payout_amount(60, Decimal("5000.00")), Decimal("5000.00"))

    def test_half_hour_is_half_the_rate(self):
        self.assertEqual(payout_amount(30, Decimal("5000.00")), Decimal("2500.00"))

    def test_recurring_division_rounds_half_up_once(self):
        # 20 minutes of a 5000/hour rate is 1666.666…, which must land on
        # 1666.67 — rounded at the end, not at the division.
        self.assertEqual(payout_amount(20, Decimal("5000.00")), Decimal("1666.67"))

    def test_result_is_exact_to_the_kobo(self):
        amount = payout_amount(45, Decimal("3333.33"))
        self.assertEqual(amount, Decimal("2500.00"))
        self.assertEqual(amount.as_tuple().exponent, -2)


class PayoutEligibilityTests(TestCase):
    """Only a completed session, and only for the teacher who taught it."""

    def test_completed_booking_earns_a_payout(self):
        payout = TeacherPayoutFactory()
        self.assertEqual(payout.status, PayoutStatus.GENERATED)
        self.assertIsNone(payout.finalized_at)
        self.assertEqual(payout.booking.status, BookingStatus.COMPLETED)

    def test_cancelled_booking_earns_nothing(self):
        self._assert_status_refused(BookingStatus.CANCELLED)

    def test_no_show_booking_earns_nothing(self):
        self._assert_status_refused(BookingStatus.NO_SHOW)

    def test_scheduled_booking_earns_nothing(self):
        window = AvailabilityFactory()
        booking = BookingFactory(availability=window)
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        with self.assertRaises(ValidationError) as caught:
            self._payout_for(booking).save()
        self.assertIn("booking", caught.exception.message_dict)

    def _assert_status_refused(self, status):
        booking = past_session()
        # Set the status directly: cancel() refuses a completed booking, and the
        # point here is the payout rule rather than the cancellation rule.
        booking.status = status
        booking.save()
        with self.assertRaises(ValidationError) as caught:
            self._payout_for(booking).save()
        self.assertIn("booking", caught.exception.message_dict)

    def test_payout_must_belong_to_the_booking_teacher(self):
        booking = past_session()
        other = past_session()
        payout = self._payout_for(booking)
        payout.teacher = other.teacher
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("teacher", caught.exception.message_dict)

    def test_amount_must_match_the_formula(self):
        booking = past_session()
        payout = self._payout_for(booking)
        payout.amount = payout.amount + Decimal("1000.00")
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("amount", caught.exception.message_dict)

    def test_one_payout_per_booking(self):
        payout = TeacherPayoutFactory()
        with self.assertRaises(ValidationError) as caught:
            self._payout_for(payout.booking).save()
        self.assertIn("booking", caught.exception.message_dict)

    def _payout_for(self, booking):
        rate = booking.teacher.teacher_profile.hourly_payout_rate or Decimal("5000.00")
        return TeacherPayout(
            teacher=booking.teacher,
            booking=booking,
            cohort=booking.cohort,
            minutes_paid=booking.duration_minutes,
            rate_used=rate,
            amount=payout_amount(booking.duration_minutes, rate),
        )


class CohortPayoutTests(TestCase):
    """A group class is one teacher teaching once, and is paid once."""

    def test_second_seat_of_the_same_cohort_cannot_be_paid(self):
        seats = self._cohort_seats(2)
        TeacherPayoutFactory(booking=seats[0])
        rate = seats[1].teacher.teacher_profile.hourly_payout_rate
        duplicate = TeacherPayout(
            teacher=seats[1].teacher,
            booking=seats[1],
            cohort=seats[1].cohort,
            minutes_paid=seats[1].duration_minutes,
            rate_used=rate,
            amount=payout_amount(seats[1].duration_minutes, rate),
        )
        with self.assertRaises(ValidationError) as caught:
            duplicate.save()
        self.assertIn("already been paid", str(caught.exception))
        self.assertEqual(TeacherPayout.objects.filter(cohort=seats[1].cohort).count(), 1)

    def test_cohort_must_match_the_booking(self):
        seats = self._cohort_seats(1)
        payout = TeacherPayoutFactory.build(
            booking=seats[0],
            teacher=seats[0].teacher,
            cohort=None,
            minutes_paid=seats[0].duration_minutes,
            rate_used=seats[0].teacher.teacher_profile.hourly_payout_rate,
        )
        payout.amount = payout_amount(payout.minutes_paid, payout.rate_used)
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("cohort", caught.exception.message_dict)

    def _cohort_seats(self, count):
        """``count`` completed seats in one group class, all past-dated."""
        window = AvailabilityFactory()
        start = dj_timezone.now() - timedelta(weeks=1)
        cohort = CohortFactory(availability=window, schedule_start_utc=start)
        return [
            past_session(
                teacher=cohort.teacher,
                level=cohort.level,
                cohort=cohort,
                student=StudentFactory(),
                start_time_utc=start,
            )
            for _ in range(count)
        ]


class PayoutImmutabilityTests(TestCase):
    """Finalization is one-way, and history does not move afterwards."""

    def test_finalize_stamps_the_moment(self):
        payout = TeacherPayoutFactory()
        payout.finalize()
        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutStatus.FINALIZED)
        self.assertIsNotNone(payout.finalized_at)

    def test_finalizing_twice_is_refused(self):
        payout = FinalizedPayoutFactory()
        with self.assertRaises(PayoutAlreadyFinalized):
            payout.finalize()

    def test_a_finalized_payout_refuses_every_edit(self):
        payout = FinalizedPayoutFactory()
        payout.amount = Decimal("1.00")
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("cannot be changed", str(caught.exception))

    def test_a_generated_payout_refuses_a_financial_edit(self):
        payout = TeacherPayoutFactory()
        payout.rate_used = payout.rate_used + Decimal("100.00")
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("rate_used", caught.exception.message_dict)

    def test_raising_the_teachers_rate_does_not_move_a_finalized_payout(self):
        payout = FinalizedPayoutFactory()
        original_rate, original_amount = payout.rate_used, payout.amount
        profile = payout.teacher.teacher_profile
        profile.hourly_payout_rate = original_rate * 2
        profile.save()
        payout.refresh_from_db()
        self.assertEqual(payout.rate_used, original_rate)
        self.assertEqual(payout.amount, original_amount)

    def test_finalized_at_cannot_be_stamped_without_finalizing(self):
        payout = TeacherPayoutFactory()
        payout.finalized_at = dj_timezone.now()
        with self.assertRaises(ValidationError) as caught:
            payout.save()
        self.assertIn("finalized_at", caught.exception.message_dict)
