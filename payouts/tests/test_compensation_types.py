"""Tests for Phase 9 teacher compensation types (hourly, per_class, fixed_period)

Verifies Acceptance Criterion 7:
- compensation_type of per_class or fixed_period produces correct payout figures
  without touching hourly teachers' numbers.
- No compensation figure can be derived from, or reveal, what any individual family pays.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.models import (
    CompensationType,
    FixedPeriodCadence,
    OrganizationTeacherConfiguration,
    Role,
    User,
)
from accounts.tests.factories import StudentFactory, SubTeacherFactory
from curriculum.tests.factories import LevelFactory, admit
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from payouts.models import TeacherFixedPeriodPayout, TeacherPayout
from payouts.services import (
    generate_fixed_period_payouts,
    generate_payouts,
)
from pricing.models import PricingAgreement, PricingReason
from scheduling.models import Booking, BookingStatus


class CompensationTypesPayoutTests(TestCase):
    def setUp(self):
        self.level = LevelFactory()
        self.org = self.level.track.organization

        self.hourly_teacher = SubTeacherFactory()
        self.per_class_teacher = SubTeacherFactory()
        self.fixed_teacher = SubTeacherFactory()

        admit(self.hourly_teacher, self.org)
        admit(self.per_class_teacher, self.org)
        admit(self.fixed_teacher, self.org)

        m_hourly = OrganizationMembership.objects.get(user=self.hourly_teacher, organization=self.org)
        OrganizationTeacherConfiguration.objects.create(
            membership=m_hourly,
            max_weekly_hours=20,
            compensation_type=CompensationType.HOURLY,
            hourly_payout_rate=Decimal("20.00"),
            approved=True,
        )

        m_per_class = OrganizationMembership.objects.get(user=self.per_class_teacher, organization=self.org)
        OrganizationTeacherConfiguration.objects.create(
            membership=m_per_class,
            max_weekly_hours=20,
            compensation_type=CompensationType.PER_CLASS,
            per_class_rate=Decimal("50.00"),
            approved=True,
        )

        m_fixed = OrganizationMembership.objects.get(user=self.fixed_teacher, organization=self.org)
        OrganizationTeacherConfiguration.objects.create(
            membership=m_fixed,
            max_weekly_hours=20,
            compensation_type=CompensationType.FIXED_PERIOD,
            fixed_period_amount=Decimal("500.00"),
            fixed_period_cadence=FixedPeriodCadence.MONTHLY,
            approved=True,
        )

        from curriculum.models import TeacherTrack
        TeacherTrack.objects.create(membership=m_hourly, track=self.level.track)
        TeacherTrack.objects.create(membership=m_per_class, track=self.level.track)
        TeacherTrack.objects.create(membership=m_fixed, track=self.level.track)

        self.student = StudentFactory()
        admit(self.student, self.org)

        self.period_start = dj_timezone.make_aware(datetime(2026, 8, 1, 0, 0, 0))
        self.period_end = dj_timezone.make_aware(datetime(2026, 8, 31, 23, 59, 59))

    def _create_booking(self, teacher, duration_minutes=30, start_dt=None):
        start_dt = start_dt or (self.period_start + timedelta(days=2))
        return Booking.objects.create(
            student=self.student,
            teacher=teacher,
            level=self.level,
            start_time_utc=start_dt,
            duration_minutes=duration_minutes,
            status=BookingStatus.COMPLETED,
        )

    def test_hourly_payout_calculation_is_duration_multiplied(self):
        # 30 minutes at 20.00/hour = 10.00
        booking = self._create_booking(self.hourly_teacher, duration_minutes=30)
        result = generate_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.hourly_teacher,
        )
        self.assertEqual(result.created_count, 1)
        payout = result.created[0]
        self.assertEqual(payout.amount, Decimal("10.00"))
        self.assertEqual(payout.compensation_type, CompensationType.HOURLY)
        self.assertEqual(payout.rate_used, Decimal("20.00"))

    def test_per_class_payout_calculation_is_flat_rate_regardless_of_duration(self):
        # 30 min session -> 50.00
        booking_30 = self._create_booking(
            self.per_class_teacher, duration_minutes=30, start_dt=self.period_start + timedelta(days=1)
        )
        # 60 min session -> 50.00
        booking_60 = self._create_booking(
            self.per_class_teacher, duration_minutes=60, start_dt=self.period_start + timedelta(days=2)
        )

        result = generate_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.per_class_teacher,
        )
        self.assertEqual(result.created_count, 2)
        for payout in result.created:
            self.assertEqual(payout.amount, Decimal("50.00"))
            self.assertEqual(payout.compensation_type, CompensationType.PER_CLASS)
            self.assertEqual(payout.rate_used, Decimal("50.00"))

    def test_fixed_period_teacher_is_skipped_in_session_generation(self):
        # Fixed-period teachers do not earn from completed sessions
        booking = self._create_booking(self.fixed_teacher, duration_minutes=60)
        result = generate_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.fixed_teacher,
        )
        self.assertEqual(result.created_count, 0)
        self.assertEqual(result.skipped_count, 1)
        self.assertEqual(result.skipped[0].reason, "no_payout_rate")

    def test_fixed_period_generation_creates_cadence_payout(self):
        res = generate_fixed_period_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.fixed_teacher,
        )
        self.assertEqual(res.created_count, 1)
        payout = res.created[0]
        self.assertEqual(payout.amount, Decimal("500.00"))
        self.assertEqual(payout.cadence, FixedPeriodCadence.MONTHLY)
        self.assertEqual(payout.teacher, self.fixed_teacher)
        self.assertEqual(payout.organization, self.org)

        # Idempotency check: running again creates nothing
        res2 = generate_fixed_period_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.fixed_teacher,
        )
        self.assertEqual(res2.created_count, 0)
        self.assertEqual(res2.skipped_count, 1)

    def test_family_pricing_separation_teacher_payout_cannot_be_derived(self):
        """Student A on standard rate (25.00), Student B on heavy hardship discount (5.00).

        Teacher taught an identical session for each student.
        Teacher's payout for both sessions must be completely identical.
        """
        student_a = StudentFactory()
        student_b = StudentFactory()
        admit(student_a, self.org)
        admit(student_b, self.org)

        owner = User.objects.create_user(username="lead_approver", email="app@org.com", role=Role.LEAD, timezone="Africa/Lagos")
        admit(owner, self.org)

        # Student A: Standard 25.00
        PricingAgreement.objects.create(
            student=student_a,
            level=self.level,
            standard_rate=Decimal("25.00"),
            agreed_rate=Decimal("25.00"),
            reason=PricingReason.STANDARD,
            approved_by=owner,
            active=True,
        )

        # Student B: Hardship 5.00
        PricingAgreement.objects.create(
            student=student_b,
            level=self.level,
            standard_rate=Decimal("25.00"),
            agreed_rate=Decimal("5.00"),
            reason=PricingReason.DISCOUNT_HARDSHIP,
            approved_by=owner,
            active=True,
        )

        booking_a = Booking.objects.create(
            student=student_a,
            teacher=self.hourly_teacher,
            level=self.level,
            start_time_utc=self.period_start + timedelta(days=5),
            duration_minutes=30,
            status=BookingStatus.COMPLETED,
        )
        booking_b = Booking.objects.create(
            student=student_b,
            teacher=self.hourly_teacher,
            level=self.level,
            start_time_utc=self.period_start + timedelta(days=6),
            duration_minutes=30,
            status=BookingStatus.COMPLETED,
        )

        res = generate_payouts(
            organization=self.org,
            period_start=self.period_start,
            period_end=self.period_end,
            teacher=self.hourly_teacher,
        )
        self.assertEqual(res.created_count, 2)
        payout_a = TeacherPayout.objects.get(booking=booking_a)
        payout_b = TeacherPayout.objects.get(booking=booking_b)

        # Both payouts are 10.00 (30 mins @ 20.00/hr)
        self.assertEqual(payout_a.amount, Decimal("10.00"))
        self.assertEqual(payout_b.amount, Decimal("10.00"))
        self.assertEqual(payout_a.amount, payout_b.amount)
