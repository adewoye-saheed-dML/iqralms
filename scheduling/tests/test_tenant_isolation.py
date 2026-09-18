"""SaaS Phase 4 — Scheduling Tenant Isolation Test Suite (Task 4.10).

Two-academy adversarial test matrix and cross-tenant object-id protection:
- Cross-tenant object-ID protection (foreign Level, Booking, Cohort, Waitlist, Teacher, Student)
- Multi-academy teacher scenario (independent tracks, availability, capacity counters)
- Cross-academy physical overlap (global human-time conflict protection)
- Routing isolation (lead and sub candidates never leak across tenant boundaries)
- Preferred teacher isolation (foreign teacher rejected, no waitlist created, no info leak)
- Parent-child isolation (parent only books children in matching academy)
- Cohort isolation (open cohort discovery and seating never cross tenants)
- Waitlist isolation (waitlist view, promotion, and fulfillment strictly partitioned)
- Suspended membership and approval isolation
- API queryset isolation across all scheduling endpoints
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import OrganizationTeacherConfiguration, Role
from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    UserFactory,
)
from curriculum.models import TeacherTrack
from curriculum.tests.factories import (
    GroupEligibleLevelFactory,
    LevelFactory,
    admit,
)
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import academy
from scheduling.exceptions import NoCapacity
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    Cohort,
    TeacherWaitlist,
    Weekday,
    remaining_weekly_minutes,
)
from scheduling.routing import promote_waitlist_entry, route_session
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    ensure_teacher_configured,
    slot_at,
    teaches,
)
from scheduling.utils import next_date_for_weekday

UTC = ZoneInfo("UTC")


def _url(name, organization, **kwargs):
    org_pk = getattr(organization, "pk", organization)
    return reverse(f"scheduling:{name}", kwargs={"organization_pk": org_pk, **kwargs})


def bookings_url(org):
    return _url("booking-create", org)


def cancel_url(org, booking_or_pk):
    pk = getattr(booking_or_pk, "pk", booking_or_pk)
    return _url("booking-cancel", org, pk=pk)


def availability_url(org):
    return _url("availability-list", org)


def mine_url(org):
    return _url("booking-mine", org)


def teaching_url(org):
    return _url("booking-teaching", org)


def route_url(org):
    return _url("route", org)


def cohorts_url(org):
    return _url("cohort-create", org)


def open_cohorts_url(org):
    return _url("cohort-open", org)


def my_waitlist_url(org):
    return _url("waitlist-mine", org)


def for_teacher_url(org):
    return _url("waitlist-for-teacher", org)


def promote_url(org, entry_or_pk):
    pk = getattr(entry_or_pk, "pk", entry_or_pk)
    return _url("waitlist-promote", org, pk=pk)


class TwoAcademiesFixture(APITestCase):
    """Base fixture creating two distinct academies with isolated users, tracks, and levels."""

    def setUp(self):
        super().setUp()
        self.here = academy(owner=UserFactory(username="owner-a"), slug="academy-a")
        self.there = academy(owner=UserFactory(username="owner-b"), slug="academy-b")

        self.owner_here = self.here.owner_membership.user
        self.owner_there = self.there.owner_membership.user

        self.lead_here = admit(
            LeadTeacherFactory(username="lead-a"), self.here, OrganizationRole.TEACHER
        ).user
        self.lead_there = admit(
            LeadTeacherFactory(username="lead-b"), self.there, OrganizationRole.TEACHER
        ).user

        self.sub_here = admit(
            BookableTeacherFactory(username="sub-a"), self.here, OrganizationRole.TEACHER
        ).user
        self.sub_there = admit(
            BookableTeacherFactory(username="sub-b"), self.there, OrganizationRole.TEACHER
        ).user

        self.student_here = admit(
            StudentFactory(username="student-a"), self.here, OrganizationRole.STAFF
        ).user
        self.student_there = admit(
            StudentFactory(username="student-b"), self.there, OrganizationRole.STAFF
        ).user

        self.parent_here = admit(
            ParentFactory(username="parent-a"), self.here, OrganizationRole.STAFF
        ).user
        self.parent_there = admit(
            ParentFactory(username="parent-b"), self.there, OrganizationRole.STAFF
        ).user

        # Levels and tracks in both academies
        self.level_here = LevelFactory(track__organization=self.here, track__name="Tajweed")
        self.level_there = LevelFactory(track__organization=self.there, track__name="Arabic")

        teaches(self.lead_here, self.level_here)
        teaches(self.sub_here, self.level_here)
        teaches(self.lead_there, self.level_there)
        teaches(self.sub_there, self.level_there)

        # Availability in both academies
        self.window_here = AvailabilityFactory(
            organization=self.here,
            teacher=self.sub_here,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        self.window_there = AvailabilityFactory(
            organization=self.there,
            teacher=self.sub_there,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.slot_here = slot_at(self.window_here, 60)
        self.slot_there = slot_at(self.window_there, 60)


class CrossTenantObjectIDProtectionTests(TwoAcademiesFixture):
    """Section 46: Cross-Tenant Object-ID Protection.

    Valid IDs from another academy must fail safely and never be returned or processed.
    """

    def test_academy_a_route_with_academy_b_level_id_fails(self):
        """Academy A route + Academy B Level id -> rejected as invalid level."""
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            route_url(self.here),
            {
                "level": self.level_there.pk,
                "requested_time_window": {"start_time_utc": self.slot_here.isoformat()},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_academy_a_direct_booking_with_academy_b_level_id_fails(self):
        """Academy A direct booking + Academy B Level id -> rejected."""
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            bookings_url(self.here),
            {
                "level": self.level_there.pk,
                "teacher": self.sub_here.pk,
                "start_time_utc": self.slot_here.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_academy_a_booking_cancel_with_academy_b_booking_id_returns_404(self):
        """Academy A route + Academy B Booking id -> 404 Not Found."""
        booking_there = Booking.objects.create(
            student=self.student_there,
            teacher=self.sub_there,
            level=self.level_there,
            start_time_utc=self.slot_there,
        )
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(cancel_url(self.here, booking_there.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_academy_a_open_cohorts_does_not_contain_academy_b_cohort(self):
        """Academy A open-cohort endpoint + Academy B Cohort id -> absent."""
        cohort_level_there = GroupEligibleLevelFactory(track__organization=self.there)
        teaches(self.sub_there, cohort_level_there)
        cohort_there = CohortFactory(
            availability=self.window_there,
            teacher=self.sub_there,
            level=cohort_level_there,
            schedule_start_utc=self.slot_there,
        )
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(
            open_cohorts_url(self.here), {"level_id": cohort_level_there.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = [c["id"] for c in response.data]
        self.assertNotIn(cohort_there.pk, returned_ids)

    def test_academy_a_waitlist_promote_with_academy_b_waitlist_id_returns_404(self):
        """Academy A route + Academy B Waitlist id -> 404 Not Found."""
        waitlist_there = TeacherWaitlist.record(
            student=self.student_there,
            requested_teacher=self.sub_there,
            level=self.level_there,
            requested_start_utc=self.slot_there,
            requested_duration_minutes=30,
        )
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.post(
            promote_url(self.here, waitlist_there.pk),
            {"start_time_utc": self.slot_here.isoformat(), "duration_minutes": 30},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_academy_a_direct_booking_with_academy_b_only_teacher_fails(self):
        """Academy A route + Academy B-only Teacher id -> 400 Bad Request."""
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            bookings_url(self.here),
            {
                "level": self.level_here.pk,
                "teacher": self.sub_there.pk,
                "start_time_utc": self.slot_here.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_academy_a_route_with_academy_b_only_teacher_as_preferred_fails(self):
        """Academy A route + Academy B-only preferred teacher -> 400 Bad Request."""
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            route_url(self.here),
            {
                "level": self.level_here.pk,
                "preferred_teacher": self.sub_there.pk,
                "requested_time_window": {"start_time_utc": self.slot_here.isoformat()},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("preferred_teacher", response.data)

    def test_academy_a_parent_booking_with_academy_b_only_student_fails(self):
        """Academy A parent + Academy B-only Student id -> 400 Bad Request."""
        child_there = admit(
            MinorStudentFactory(username="minor-b"), self.there, OrganizationRole.STAFF
        ).user
        ParentLinkFactory(parent=self.parent_here, student=child_there)

        self.client.force_authenticate(user=self.parent_here)
        response = self.client.post(
            bookings_url(self.here),
            {
                "student": child_there.pk,
                "level": self.level_here.pk,
                "teacher": self.sub_here.pk,
                "start_time_utc": self.slot_here.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)


class MultiAcademyTeacherScenarioTests(APITestCase):
    """Section 47: Multi-Academy Teacher Scenario.

    Teacher T holds independent memberships, configurations, tracks, availability,
    and capacity counters in Academy A and Academy B.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-47"), slug="academy-a-47")
        self.academy_b = academy(owner=UserFactory(username="owner-b-47"), slug="academy-b-47")

        self.teacher_t = BookableTeacherFactory(username="teacher-t")

        # Academy A setup
        m_a = admit(self.teacher_t, self.academy_a, OrganizationRole.TEACHER)
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m_a,
            defaults={"approved": True, "max_weekly_hours": 10},
        )
        self.track_tajweed = LevelFactory(
            track__organization=self.academy_a, track__name="Tajweed"
        ).track
        self.level_tajweed = self.track_tajweed.levels.first()
        TeacherTrack.objects.update_or_create(
            membership=m_a, track=self.track_tajweed, defaults={"active": True}
        )
        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher_t,
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(12, 0),
        )

        # Academy B setup
        m_b = admit(self.teacher_t, self.academy_b, OrganizationRole.TEACHER)
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m_b,
            defaults={"approved": True, "max_weekly_hours": 4},
        )
        self.track_arabic = LevelFactory(
            track__organization=self.academy_b, track__name="Arabic"
        ).track
        self.level_arabic = self.track_arabic.levels.first()
        TeacherTrack.objects.update_or_create(
            membership=m_b, track=self.track_arabic, defaults={"active": True}
        )
        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher_t,
            weekday=Weekday.MONDAY,
            start_time_utc=time(14, 0),
            end_time_utc=time(18, 0),
        )



        self.student_a = admit(
            StudentFactory(username="student-a-47"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.student_b = admit(
            StudentFactory(username="student-b-47"), self.academy_b, OrganizationRole.STAFF
        ).user

        ref_date = dj_timezone.now().astimezone(UTC).date() + timedelta(days=1)
        self.monday_date = next_date_for_weekday(Weekday.MONDAY, ref_date)
        self.slot_a_morning = datetime.combine(self.monday_date, time(9, 0), tzinfo=UTC)
        self.slot_b_afternoon = datetime.combine(self.monday_date, time(15, 0), tzinfo=UTC)

    def test_a_tajweed_booking_during_a_availability_is_valid(self):
        """A Tajweed booking during A availability -> potentially valid."""
        booking = Booking.objects.create(
            student=self.student_a,
            teacher=self.teacher_t,
            level=self.level_tajweed,
            start_time_utc=self.slot_a_morning,
        )
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.organization, self.academy_a)

    def test_a_arabic_booking_is_denied(self):
        """A Arabic booking -> denied (track not taught in Academy A)."""
        track_arabic_a = LevelFactory(
            track__organization=self.academy_a, track__name="Arabic A"
        ).track
        level_arabic_a = track_arabic_a.levels.first()
        with self.assertRaises(ValidationError) as cm:
            booking = Booking(
                student=self.student_a,
                teacher=self.teacher_t,
                level=level_arabic_a,
                start_time_utc=self.slot_a_morning,
            )
            booking.full_clean()
        self.assertTrue(
            "teacher_does_not_teach_track" in [e.code for e in cm.exception.error_dict.get("level", [])]
            or "does not teach" in str(cm.exception)
        )

    def test_b_arabic_booking_during_b_availability_is_valid(self):
        """B Arabic booking during B availability -> potentially valid."""
        booking = Booking.objects.create(
            student=self.student_b,
            teacher=self.teacher_t,
            level=self.level_arabic,
            start_time_utc=self.slot_b_afternoon,
        )
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.organization, self.academy_b)

    def test_b_tajweed_booking_is_denied(self):
        """B Tajweed booking -> denied (track not taught in Academy B)."""
        track_tajweed_b = LevelFactory(
            track__organization=self.academy_b, track__name="Tajweed B"
        ).track
        level_tajweed_b = track_tajweed_b.levels.first()
        with self.assertRaises(ValidationError) as cm:
            booking = Booking(
                student=self.student_b,
                teacher=self.teacher_t,
                level=level_tajweed_b,
                start_time_utc=self.slot_b_afternoon,
            )
            booking.full_clean()
        self.assertTrue(
            "teacher_does_not_teach_track" in [e.code for e in cm.exception.error_dict.get("level", [])]
            or "does not teach" in str(cm.exception)
        )

    def test_a_cannot_use_b_availability(self):
        """A cannot use B availability -> booking in A at 15:00 rejected."""
        with self.assertRaises(ValidationError) as cm:
            booking = Booking(
                student=self.student_a,
                teacher=self.teacher_t,
                level=self.level_tajweed,
                start_time_utc=self.slot_b_afternoon,
            )
            booking.full_clean()
        self.assertTrue(
            "outside_availability" in [e.code for e in cm.exception.error_dict.get("__all__", [])]
            or "has no availability covering" in str(cm.exception)
        )

    def test_b_cannot_use_a_availability(self):
        """B cannot use A availability -> booking in B at 09:00 rejected."""
        with self.assertRaises(ValidationError) as cm:
            booking = Booking(
                student=self.student_b,
                teacher=self.teacher_t,
                level=self.level_arabic,
                start_time_utc=self.slot_a_morning,
            )
            booking.full_clean()
        self.assertTrue(
            "outside_availability" in [e.code for e in cm.exception.error_dict.get("__all__", [])]
            or "has no availability covering" in str(cm.exception)
        )

    def test_capacity_counters_remain_independent(self):
        """Capacity counters remain independent between Academy A and B."""
        # Initial capacity: A has 10h (600m), B has 4h (240m)
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_a_morning, organization=self.academy_a),
            600,
        )
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_b_afternoon, organization=self.academy_b),
            240,
        )

        # Book 1 hour (two 30-min sessions) in Academy A
        Booking.objects.create(
            student=self.student_a,
            teacher=self.teacher_t,
            level=self.level_tajweed,
            start_time_utc=self.slot_a_morning,
            duration_minutes=30,
        )
        Booking.objects.create(
            student=self.student_a,
            teacher=self.teacher_t,
            level=self.level_tajweed,
            start_time_utc=self.slot_a_morning + timedelta(minutes=30),
            duration_minutes=30,
        )

        # A capacity decrements by 60 min, B capacity is completely unaffected
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_a_morning, organization=self.academy_a),
            540,
        )
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_b_afternoon, organization=self.academy_b),
            240,
        )

        # Book 30 minutes in Academy B
        Booking.objects.create(
            student=self.student_b,
            teacher=self.teacher_t,
            level=self.level_arabic,
            start_time_utc=self.slot_b_afternoon,
            duration_minutes=30,
        )

        # B drops by 30 min, A stays 540 min
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_b_afternoon, organization=self.academy_b),
            210,
        )
        self.assertEqual(
            remaining_weekly_minutes(self.teacher_t, self.slot_a_morning, organization=self.academy_a),
            540,
        )


class CrossAcademyDoubleBookingScenarioTests(APITestCase):
    """Section 48: Cross-Academy Double Booking Scenario.

    Global physical human-time protection across academy boundaries.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-48"), slug="academy-a-48")
        self.academy_b = academy(owner=UserFactory(username="owner-b-48"), slug="academy-b-48")

        self.teacher_t = BookableTeacherFactory(username="teacher-t-48")

        # Teacher T active and approved in both academies
        m_a = admit(self.teacher_t, self.academy_a, OrganizationRole.TEACHER)
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m_a, defaults={"approved": True, "max_weekly_hours": 10}
        )
        m_b = admit(self.teacher_t, self.academy_b, OrganizationRole.TEACHER)
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m_b, defaults={"approved": True, "max_weekly_hours": 10}
        )

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)
        teaches(self.teacher_t, self.level_a)
        teaches(self.teacher_t, self.level_b)

        # Overlapping availability: Monday 10:00-12:00 in both academies
        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher_t,
            weekday=Weekday.MONDAY,
            start_time_utc=time(10, 0),
            end_time_utc=time(12, 0),
        )
        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher_t,
            weekday=Weekday.MONDAY,
            start_time_utc=time(10, 0),
            end_time_utc=time(12, 0),
        )

        self.student_a = admit(
            StudentFactory(username="student-a-48"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.student_b = admit(
            StudentFactory(username="student-b-48"), self.academy_b, OrganizationRole.STAFF
        ).user

        ref_date = dj_timezone.now().astimezone(UTC).date() + timedelta(days=1)
        monday_date = next_date_for_weekday(Weekday.MONDAY, ref_date)
        self.slot = datetime.combine(monday_date, time(10, 0), tzinfo=UTC)

    def test_booking_in_academy_a_blocks_overlapping_booking_in_academy_b(self):
        """First booking in A succeeds, second booking in B is rejected as overlapping."""
        booking_a = Booking.objects.create(
            student=self.student_a,
            teacher=self.teacher_t,
            level=self.level_a,
            start_time_utc=self.slot,
            duration_minutes=30,
        )
        self.assertEqual(booking_a.status, BookingStatus.SCHEDULED)

        with self.assertRaises(ValidationError) as cm:
            booking_b = Booking(
                student=self.student_b,
                teacher=self.teacher_t,
                level=self.level_b,
                start_time_utc=self.slot,
                duration_minutes=30,
            )
            booking_b.full_clean()
        self.assertTrue(
            "teacher_double_booked" in [e.code for e in cm.exception.error_dict.get("__all__", [])]
            or "overlapping that time" in str(cm.exception)
        )

    def test_booking_in_academy_b_blocks_overlapping_booking_in_academy_a(self):
        """Order reversed: first booking in B succeeds, second booking in A is rejected."""
        booking_b = Booking.objects.create(
            student=self.student_b,
            teacher=self.teacher_t,
            level=self.level_b,
            start_time_utc=self.slot,
            duration_minutes=30,
        )
        self.assertEqual(booking_b.status, BookingStatus.SCHEDULED)

        with self.assertRaises(ValidationError) as cm:
            booking_a = Booking(
                student=self.student_a,
                teacher=self.teacher_t,
                level=self.level_a,
                start_time_utc=self.slot,
                duration_minutes=30,
            )
            booking_a.full_clean()
        self.assertTrue(
            "teacher_double_booked" in [e.code for e in cm.exception.error_dict.get("__all__", [])]
            or "overlapping that time" in str(cm.exception)
        )


class RoutingIsolationScenarioTests(APITestCase):
    """Section 49: Routing Isolation Scenario.

    Academy A has no teachers, Academy B has teachers. Route request in Academy A
    must produce NoCapacity and never borrow Academy B's teachers.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-49"), slug="academy-a-49")
        self.academy_b = academy(owner=UserFactory(username="owner-b-49"), slug="academy-b-49")

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)

        # Academy B has available lead and eligible sub
        self.lead_b = admit(
            BookableLeadTeacherFactory(username="lead-b-49"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.lead_b, self.level_b)
        self.sub_b = admit(
            BookableTeacherFactory(username="sub-b-49"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.sub_b, self.level_b)

        ref_date = dj_timezone.now().astimezone(UTC).date() + timedelta(days=1)
        monday_date = next_date_for_weekday(Weekday.MONDAY, ref_date)
        self.slot = datetime.combine(monday_date, time(10, 0), tzinfo=UTC)

        Availability.objects.create(
            organization=self.academy_b,
            teacher=self.lead_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        Availability.objects.create(
            organization=self.academy_b,
            teacher=self.sub_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.student_a = admit(
            StudentFactory(username="student-a-49"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.student_b = admit(
            StudentFactory(username="student-b-49"), self.academy_b, OrganizationRole.STAFF
        ).user

    def test_routing_in_academy_a_produces_no_capacity_and_never_borrows_academy_b_teacher(self):
        """Academy A route produces NoCapacity, does not route to Academy B lead or sub."""
        with self.assertRaises(NoCapacity):
            route_session(
                student=self.student_a,
                level=self.level_a,
                start_time_utc=self.slot,
                organization=self.academy_a,
            )

        # Also verify over the HTTP API
        self.client.force_authenticate(user=self.student_a)
        response = self.client.post(
            route_url(self.academy_a),
            {
                "level": self.level_a.pk,
                "requested_time_window": {"start_time_utc": self.slot.isoformat()},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.filter(student=self.student_a).count(), 0)

    def test_routing_in_academy_b_succeeds(self):
        """Academy B routing works as expected with its own teachers."""
        routed = route_session(
            student=self.student_b,
            level=self.level_b,
            start_time_utc=self.slot,
            organization=self.academy_b,
        )
        self.assertEqual(routed.booking.teacher, self.lead_b)
        self.assertEqual(routed.booking.organization, self.academy_b)


class PreferredTeacherIsolationScenarioTests(APITestCase):
    """Section 50: Preferred Teacher Isolation Scenario.

    Teacher T belongs only to Academy B. Student in Academy A submits preferred_teacher = T.
    Must be rejected with 400, no waitlist entry in Academy A, no Academy B config leaked.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-50"), slug="academy-a-50")
        self.academy_b = academy(owner=UserFactory(username="owner-b-50"), slug="academy-b-50")

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)

        # Teacher T belongs only to Academy B
        self.teacher_b = admit(
            BookableTeacherFactory(username="teacher-b-50"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.teacher_b, self.level_b)

        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.student_a = admit(
            StudentFactory(username="student-a-50"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.slot = slot_at(self.window_b, 60)

    def test_preferred_teacher_from_foreign_academy_is_rejected_without_waitlist(self):
        """Student A prefers Teacher B -> 400, no waitlist created, no info leaked."""
        self.client.force_authenticate(user=self.student_a)
        response = self.client.post(
            route_url(self.academy_a),
            {
                "level": self.level_a.pk,
                "preferred_teacher": self.teacher_b.pk,
                "requested_time_window": {"start_time_utc": self.slot.isoformat()},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("preferred_teacher", response.data)
        # Ensure no waitlist was created in Academy A
        self.assertEqual(TeacherWaitlist.objects.count(), 0)
        # Ensure Academy B details are not leaked
        self.assertNotIn("Academy B", str(response.data))
        self.assertNotIn(self.academy_b.name, str(response.data))


class ParentIsolationScenarioTests(APITestCase):
    """Section 51: Parent Isolation Scenario.

    Parent P -> Student S globally.
    Parent P active in Academy A, Student S active only in Academy B.
    An Academy A booking request for S must fail.
    When P is given active Academy B membership, Academy B allows the booking.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-51"), slug="academy-a-51")
        self.academy_b = academy(owner=UserFactory(username="owner-b-51"), slug="academy-b-51")

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)

        self.teacher_a = admit(
            BookableTeacherFactory(username="teacher-a-51"), self.academy_a, OrganizationRole.TEACHER
        ).user
        teaches(self.teacher_a, self.level_a)
        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.teacher_b = admit(
            BookableTeacherFactory(username="teacher-b-51"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.teacher_b, self.level_b)
        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        # Parent P and Student S linked globally
        self.parent = ParentFactory(username="parent-51")
        self.student = MinorStudentFactory(username="student-51")
        ParentLinkFactory(parent=self.parent, student=self.student)

        # Parent active in Academy A, Student active only in Academy B
        admit(self.parent, self.academy_a, OrganizationRole.STAFF)
        admit(self.student, self.academy_b, OrganizationRole.STAFF)

        self.slot_a = slot_at(self.window_a, 60)
        self.slot_b = slot_at(self.window_b, 60)

    def test_parent_cannot_book_for_student_in_wrong_academy(self):
        """Academy A booking request for S by P must fail (S is not in Academy A)."""
        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            bookings_url(self.academy_a),
            {
                "student": self.student.pk,
                "teacher": self.teacher_a.pk,
                "level": self.level_a.pk,
                "start_time_utc": self.slot_a.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_parent_with_matching_membership_can_book_in_academy_b(self):
        """Giving P active membership in Academy B allows booking S in Academy B."""
        # Parent not yet member of B -> 403 Forbidden
        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            bookings_url(self.academy_b),
            {
                "student": self.student.pk,
                "teacher": self.teacher_b.pk,
                "level": self.level_b.pk,
                "start_time_utc": self.slot_b.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Now admit Parent to Academy B
        admit(self.parent, self.academy_b, OrganizationRole.STAFF)

        # Now booking succeeds
        response = self.client.post(
            bookings_url(self.academy_b),
            {
                "student": self.student.pk,
                "teacher": self.teacher_b.pk,
                "level": self.level_b.pk,
                "start_time_utc": self.slot_b.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], self.student.pk)


class CohortIsolationScenarioTests(APITestCase):
    """Section 52: Cohort Isolation Scenario.

    Cohort A under Level A, Cohort B under Level B.
    Open cohort endpoints return own academy only.
    Routing in Academy A never seats a student in Academy B cohort.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-52"), slug="academy-a-52")
        self.academy_b = academy(owner=UserFactory(username="owner-b-52"), slug="academy-b-52")

        self.level_a = GroupEligibleLevelFactory(track__organization=self.academy_a)
        self.level_b = GroupEligibleLevelFactory(track__organization=self.academy_b)

        self.teacher_a = admit(
            BookableTeacherFactory(username="teacher-a-52"), self.academy_a, OrganizationRole.TEACHER
        ).user
        teaches(self.teacher_a, self.level_a)
        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.teacher_b = admit(
            BookableTeacherFactory(username="teacher-b-52"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.teacher_b, self.level_b)
        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.student_a = admit(
            StudentFactory(username="student-a-52"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.student_b = admit(
            StudentFactory(username="student-b-52"), self.academy_b, OrganizationRole.STAFF
        ).user

        ref_date = dj_timezone.now().astimezone(UTC).date() + timedelta(days=1)
        monday_date = next_date_for_weekday(Weekday.MONDAY, ref_date)
        self.slot = datetime.combine(monday_date, time(11, 0), tzinfo=UTC)

        self.cohort_a = CohortFactory(
            availability=self.window_a,
            teacher=self.teacher_a,
            level=self.level_a,
            schedule_start_utc=self.slot,
        )
        self.cohort_b = CohortFactory(
            availability=self.window_b,
            teacher=self.teacher_b,
            level=self.level_b,
            schedule_start_utc=self.slot,
        )

    def test_open_cohort_endpoints_are_isolated(self):
        """Academy A open-cohort endpoint returns A only; Academy B returns B only."""
        self.client.force_authenticate(user=self.student_a)
        response_a = self.client.get(
            open_cohorts_url(self.academy_a), {"level_id": self.level_a.pk}
        )
        self.assertEqual(response_a.status_code, status.HTTP_200_OK)
        cohort_ids_a = [c["id"] for c in response_a.data]
        self.assertIn(self.cohort_a.pk, cohort_ids_a)
        self.assertNotIn(self.cohort_b.pk, cohort_ids_a)

        self.client.force_authenticate(user=self.student_b)
        response_b = self.client.get(
            open_cohorts_url(self.academy_b), {"level_id": self.level_b.pk}
        )
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)
        cohort_ids_b = [c["id"] for c in response_b.data]
        self.assertIn(self.cohort_b.pk, cohort_ids_b)
        self.assertNotIn(self.cohort_a.pk, cohort_ids_b)

    def test_routing_in_academy_a_never_seats_student_in_cohort_b(self):
        """Academy A routing seats student in Cohort A, never in Cohort B."""
        routed = route_session(
            student=self.student_a,
            level=self.level_a,
            start_time_utc=self.slot,
            organization=self.academy_a,
        )
        self.assertEqual(routed.cohort, self.cohort_a)
        self.assertNotEqual(routed.cohort, self.cohort_b)
        self.assertEqual(routed.booking.organization, self.academy_a)


class WaitlistIsolationScenarioTests(APITestCase):
    """Section 53: Waitlist Isolation Scenario.

    Teacher works for both academies. Independent waitlist entries in A and B.
    Endpoints return own entries only. Promoting A does not mutate B.
    Fulfilling A does not fulfill B.
    """

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-53"), slug="academy-a-53")
        self.academy_b = academy(owner=UserFactory(username="owner-b-53"), slug="academy-b-53")

        self.teacher = BookableTeacherFactory(username="teacher-shared-53")
        admit(self.teacher, self.academy_a, OrganizationRole.TEACHER)
        admit(self.teacher, self.academy_b, OrganizationRole.TEACHER)

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)
        teaches(self.teacher, self.level_a)
        teaches(self.teacher, self.level_b)

        self.lead_a = admit(
            BookableLeadTeacherFactory(username="lead-a-53"), self.academy_a, OrganizationRole.TEACHER
        ).user
        self.lead_b = admit(
            BookableLeadTeacherFactory(username="lead-b-53"), self.academy_b, OrganizationRole.TEACHER
        ).user
        teaches(self.lead_a, self.level_a)
        teaches(self.lead_b, self.level_b)

        self.student_a = admit(
            StudentFactory(username="student-a-53"), self.academy_a, OrganizationRole.STAFF
        ).user
        self.student_b = admit(
            StudentFactory(username="student-b-53"), self.academy_b, OrganizationRole.STAFF
        ).user

        # Availability in both academies at different times
        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(12, 0),
        )
        self.window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(14, 0),
            end_time_utc=time(17, 0),
        )

        self.slot_a = slot_at(self.window_a, 30)
        self.slot_b = slot_at(self.window_b, 30)

        # Independent waitlist entries
        self.waitlist_a = TeacherWaitlist.record(
            student=self.student_a,
            requested_teacher=self.teacher,
            level=self.level_a,
            requested_start_utc=self.slot_a,
            requested_duration_minutes=30,
        )
        self.waitlist_b = TeacherWaitlist.record(
            student=self.student_b,
            requested_teacher=self.teacher,
            level=self.level_b,
            requested_start_utc=self.slot_b,
            requested_duration_minutes=30,
        )

    def test_waitlist_endpoints_return_own_academy_entries_only(self):
        """A teacher waitlist endpoint under A returns A only; B returns B only."""
        self.client.force_authenticate(user=self.lead_a)
        response_a = self.client.get(
            for_teacher_url(self.academy_a), {"teacher_id": self.teacher.pk}
        )
        self.assertEqual(response_a.status_code, status.HTTP_200_OK)
        ids_a = [e["id"] for e in response_a.data]
        self.assertIn(self.waitlist_a.pk, ids_a)
        self.assertNotIn(self.waitlist_b.pk, ids_a)

        self.client.force_authenticate(user=self.lead_b)
        response_b = self.client.get(
            for_teacher_url(self.academy_b), {"teacher_id": self.teacher.pk}
        )
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)
        ids_b = [e["id"] for e in response_b.data]
        self.assertIn(self.waitlist_b.pk, ids_b)
        self.assertNotIn(self.waitlist_a.pk, ids_b)

    def test_student_mine_waitlist_endpoint_is_isolated(self):
        """Student A sees only their Academy A waitlist entries."""
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(my_waitlist_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [e["id"] for e in response.data]
        self.assertIn(self.waitlist_a.pk, ids)
        self.assertNotIn(self.waitlist_b.pk, ids)

    def test_promoting_a_does_not_mutate_or_fulfill_b(self):
        """Promoting waitlist entry A leaves waitlist entry B unfulfilled and open."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.post(
            promote_url(self.academy_a, self.waitlist_a.pk),
            {"start_time_utc": self.slot_a.isoformat(), "duration_minutes": 30},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.waitlist_a.refresh_from_db()
        self.waitlist_b.refresh_from_db()

        self.assertIsNotNone(self.waitlist_a.fulfilled_booking)
        self.assertIsNone(self.waitlist_b.fulfilled_booking)


class MembershipAndApprovalIsolationTests(APITestCase):
    """Section 61 (items 6, 7, 18): Suspended membership, approval, and API queryset isolation."""

    def setUp(self):
        super().setUp()
        self.academy_a = academy(owner=UserFactory(username="owner-a-61"), slug="academy-a-61")
        self.academy_b = academy(owner=UserFactory(username="owner-b-61"), slug="academy-b-61")

        self.level_a = LevelFactory(track__organization=self.academy_a)
        self.level_b = LevelFactory(track__organization=self.academy_b)

        self.teacher = BookableTeacherFactory(username="teacher-61")
        self.m_a = admit(self.teacher, self.academy_a, OrganizationRole.TEACHER)
        self.m_b = admit(self.teacher, self.academy_b, OrganizationRole.TEACHER)
        teaches(self.teacher, self.level_a)
        teaches(self.teacher, self.level_b)

        self.window_a = AvailabilityFactory(
            organization=self.academy_a,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        self.slot_a = slot_at(self.window_a, 60)

        self.student_a = admit(
            StudentFactory(username="student-a-61"), self.academy_a, OrganizationRole.STAFF
        ).user

    def test_suspended_teacher_membership_cannot_be_booked(self):
        """A suspended teacher membership prevents booking in that academy."""
        self.m_a.status = MembershipStatus.SUSPENDED
        self.m_a.save()

        self.client.force_authenticate(user=self.student_a)
        response = self.client.post(
            bookings_url(self.academy_a),
            {
                "level": self.level_a.pk,
                "teacher": self.teacher.pk,
                "start_time_utc": self.slot_a.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_suspended_student_membership_is_forbidden(self):
        """A suspended student membership is rejected with 403 Forbidden."""
        m_student = self.student_a.organization_memberships.get(organization=self.academy_a)
        m_student.status = MembershipStatus.SUSPENDED
        m_student.save()

        self.client.force_authenticate(user=self.student_a)
        response = self.client.post(
            bookings_url(self.academy_a),
            {
                "level": self.level_a.pk,
                "teacher": self.teacher.pk,
                "start_time_utc": self.slot_a.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_approved_in_a_unapproved_in_b(self):
        """Teacher approved in Academy A but unapproved in Academy B can only book in A."""
        window_b = AvailabilityFactory(
            organization=self.academy_b,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        slot_b = slot_at(window_b, 60)

        # Unapprove in Academy B
        config_b = OrganizationTeacherConfiguration.objects.get(membership=self.m_b)
        config_b.approved = False
        config_b.save()

        # Booking in A succeeds
        self.client.force_authenticate(user=self.student_a)
        response_a = self.client.post(
            bookings_url(self.academy_a),
            {
                "level": self.level_a.pk,
                "teacher": self.teacher.pk,
                "start_time_utc": self.slot_a.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response_a.status_code, status.HTTP_201_CREATED)

        # Booking in B is denied
        student_b = admit(
            StudentFactory(username="student-b-61"), self.academy_b, OrganizationRole.STAFF
        ).user

        self.client.force_authenticate(user=student_b)
        response_b = self.client.post(
            bookings_url(self.academy_b),
            {
                "level": self.level_b.pk,
                "teacher": self.teacher.pk,
                "start_time_utc": slot_b.isoformat(),
            },
            format="json",
        )
        self.assertEqual(response_b.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response_b.data)

    def test_api_queryset_isolation_on_mine_and_teaching(self):
        """Bookings in Academy A do not leak into Academy B's mine or teaching lists."""
        booking_a = Booking.objects.create(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=self.slot_a,
        )

        # Student A checks mine in Academy A -> booking_a present
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(mine_url(self.academy_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(booking_a.pk, [b["id"] for b in response.data])

        # Teacher checks teaching in Academy A -> booking_a present
        self.client.force_authenticate(user=self.teacher)
        response_teach_a = self.client.get(teaching_url(self.academy_a))
        self.assertEqual(response_teach_a.status_code, status.HTTP_200_OK)
        self.assertIn(booking_a.pk, [b["id"] for b in response_teach_a.data])

        # Teacher checks teaching in Academy B -> booking_a ABSENT
        response_teach_b = self.client.get(teaching_url(self.academy_b))
        self.assertEqual(response_teach_b.status_code, status.HTTP_200_OK)
        self.assertNotIn(booking_a.pk, [b["id"] for b in response_teach_b.data])

        # Availability list in Academy B does not leak Academy A windows
        response_avail_b = self.client.get(
            availability_url(self.academy_b), {"teacher_id": self.teacher.pk}
        )
        self.assertEqual(response_avail_b.status_code, status.HTTP_200_OK)
        self.assertNotIn(self.window_a.pk, [w["id"] for w in response_avail_b.data])
