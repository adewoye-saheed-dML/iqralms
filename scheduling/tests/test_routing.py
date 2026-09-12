"""The routing engine — Phase 4's acceptance criteria 2 to 6, at the model layer.

Criterion by criterion, and each one asserted on ``routed_reason`` rather than
merely on "a booking appeared": the whole value of this phase is that the system's
choice is *legible*, so a test that only checked a booking exists would pass for
routing that picked the right teacher by accident.

Every class here builds the whole world explicitly — the lead, the subs, their
hours, their caps, their specialties — because routing's answer is a function of
all of it, and a fixture that quietly supplied one of them would make the
assertions ambiguous. ``Routed.considered`` is checked wherever a step is meant to
have *declined*, so a step skipped for the wrong reason cannot pass as one
declined for the right one.

API-layer coverage of the same criteria is in test_api.py; criterion 8 (the
Phase 3.5 concurrency fix holding under routing) is in test_concurrency.py, which
needs ``TransactionTestCase``.
"""

from datetime import time, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import (
    MinorStudentFactory,
    ParentLinkFactory,
    StudentFactory,
)
from curriculum.tests.factories import (
    GroupEligibleLevelFactory,
    LevelFactory,
    TrackFactory,
    admit,
)
from organizations.tests.factories import OrganizationFactory
from scheduling.exceptions import NoCapacity
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    RoutedReason,
    TeacherBookingLock,
    TeacherWaitlist,
    weekly_committed_minutes,
)
from scheduling.routing import lead_teacher, matching_sub_teachers, route_session
from scheduling.utils import week_bounds

from .factories import (
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    ensure_teacher_configured,
    teaches,
)
from .test_models import unapprove


def next_monday():
    """The start of a week that has not begun yet.

    Routing creates real bookings, which cannot start in the past (Phase 3.5), and
    the weekly cap counts the week the booking lands in — so these tests need a
    future week and need to know which one it is. Derived from ``week_bounds`` so
    the two can never disagree about where the week starts.
    """
    return week_bounds(dj_timezone.now())[1]


def refusal_codes(step):
    """Every error ``code`` inside one ``considered`` entry, flattened.

    ``considered`` is nested per candidate then per field, and a test only ever
    cares *why* a step declined. Asserting on codes rather than on messages is
    what keeps these tests honest without pinning wording: several rules can
    refuse the same booking, and one refused by the wrong rule would otherwise
    pass a test claiming to be about another.
    """
    if not isinstance(step, dict):
        # A whole step declined before reaching candidates ("no lead exists"), in
        # which case the reason is a plain sentence and there are no codes.
        return []
    return sorted(
        error["code"]
        for by_field in step.values()
        for errors in by_field.values()
        for error in errors
    )


class RoutingWorld:
    """Builds the cast routing chooses between. Mixed into the TestCases below.

    Everything is explicit and nothing is shared: a teacher is only free, only
    capable and only under cap because a test said so.
    """

    def make_teacher(self, *, lead=False, hours=20, teaches_level=True):
        teacher = BookableLeadTeacherFactory() if lead else BookableTeacherFactory()
        profile = teacher.teacher_profile
        profile.max_weekly_hours = hours
        profile.save()
        if hasattr(self, "level") and getattr(getattr(self.level, "track", None), "organization", None):
            org = self.level.track.organization
            ensure_teacher_configured(teacher, org)
            from accounts.models import OrganizationTeacherConfiguration

            OrganizationTeacherConfiguration.objects.filter(
                membership__user=teacher, membership__organization=org
            ).update(max_weekly_hours=hours)
        if teaches_level:
            teaches(teacher, self.level)
        return teacher

    def free_all_week(self, teacher):
        """Declared hours covering every UTC day end to end.

        Availability is not what any of these tests are about, so it is made
        maximal — a routing failure then always means capacity, specialty or a
        clash, never "nobody had declared hours".
        """
        org = None
        if hasattr(self, "level") and getattr(getattr(self.level, "track", None), "organization", None):
            org = self.level.track.organization
        for weekday in range(7):
            Availability.objects.create(
                teacher=teacher,
                organization=org,
                weekday=weekday,
                start_time_utc=time(0, 0),
                end_time_utc=time.max,
            )
        return teacher

    def available_teacher(self, *, approved=True, **kwargs):
        """A teacher with hours, a cap and (usually) the specialty.

        ``approved=False`` is built approved and then revoked, because
        ``Availability`` refuses to save against an unapproved teacher — the same
        order ``test_models.unapprove`` uses, and the order it happens in real
        life when a lead withdraws approval from someone who already had hours.
        """
        teacher = self.free_all_week(self.make_teacher(**kwargs))
        if not approved:
            org = getattr(getattr(self.level, "track", None), "organization", None)
            teacher = unapprove(teacher, organization=org)
        return teacher

    def fill_week(self, teacher, minutes):
        """Consume ``minutes`` of ``teacher``'s week with real bookings.

        Written as ordinary bookings rather than by editing the cap, because the
        cap is what the routing check reads *against* — filling the week is the
        state a busy teacher is genuinely in.
        """
        placed = 0
        hour = 0
        org = getattr(getattr(self.level, "track", None), "organization", None)
        while placed < minutes:
            chunk = min(60, minutes - placed)
            student = StudentFactory()
            if org:
                admit(student, org)
            Booking.objects.create(
                student=student,
                teacher=teacher,
                level=self.level,
                start_time_utc=self.slot + timedelta(days=1, hours=hour),
                duration_minutes=chunk,
            )
            placed += chunk
            hour += 2
        assert (
            weekly_committed_minutes(teacher.pk, self.slot, organization=org)
            == minutes
        )
        return teacher

    def book(self, *, student=None, teacher=None, level=None, **kwargs):
        level = level or self.level
        student = student or StudentFactory()
        org = getattr(getattr(level, "track", None), "organization", None)
        if org:
            admit(student, org)
        return Booking.objects.create(
            student=student,
            teacher=teacher,
            level=level,
            **kwargs,
        )

    def route(self, **overrides):
        kwargs = {
            "student": self.student,
            "level": self.level,
            "start_time_utc": self.slot,
            "duration_minutes": 30,
        }
        kwargs.update(overrides)
        org = getattr(getattr(kwargs["level"], "track", None), "organization", None)
        if org and kwargs.get("student"):
            from organizations.models import active_membership

            if active_membership(user=kwargs["student"], organization=org) is None:
                admit(kwargs["student"], org)
        return route_session(**kwargs)


class RoutingHelperTests(RoutingWorld, TestCase):
    """The two queries routing narrows the field with."""

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)

    def test_lead_teacher_finds_the_approved_lead(self):
        lead = self.available_teacher(lead=True)
        self.assertEqual(lead_teacher(), lead)

    def test_lead_teacher_is_none_when_there_is_no_lead(self):
        self.available_teacher()  # a sub, not a lead
        self.assertIsNone(lead_teacher())

    def test_lead_teacher_ignores_an_unapproved_lead(self):
        lead = BookableLeadTeacherFactory()
        profile = lead.teacher_profile
        profile.approved = False
        profile.save()
        self.assertIsNone(lead_teacher())

    def test_matching_sub_teachers_finds_a_specialist(self):
        sub = self.available_teacher()
        self.assertEqual(list(matching_sub_teachers(self.level)), [sub])

    def test_matching_sub_teachers_excludes_a_non_specialist(self):
        self.available_teacher(teaches_level=False)
        self.assertEqual(list(matching_sub_teachers(self.level)), [])

    def test_matching_sub_teachers_excludes_an_unapproved_specialist(self):
        self.available_teacher(approved=False)
        self.assertEqual(list(matching_sub_teachers(self.level)), [])

    def test_matching_sub_teachers_excludes_the_lead(self):
        """Step 3 is about subs; the lead had their own turn in step 2."""
        self.available_teacher(lead=True)
        self.assertEqual(list(matching_sub_teachers(self.level)), [])

    def test_a_teacher_with_several_specialties_appears_once(self):
        """The join could duplicate them; ``distinct()`` is what stops it."""
        sub = self.available_teacher()
        teaches(sub, LevelFactory())
        teaches(sub, LevelFactory())
        self.assertEqual(list(matching_sub_teachers(self.level)), [sub])


class RouteToCohortTests(RoutingWorld, TestCase):
    """Acceptance criterion 2 — a matching request gets a cohort seat."""

    def setUp(self):
        self.level = GroupEligibleLevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)
        # A lead with room, so a cohort assignment proves cohorts come *first*
        # rather than proving there was nothing else available.
        self.lead = self.available_teacher(lead=True)
        self.cohort_teacher = self.available_teacher()
        self.cohort = CohortFactory(
            availability=self.cohort_teacher.availability_windows.first(),
            teacher=self.cohort_teacher,
            level=self.level,
            max_students=6,
            schedule_start_utc=self.slot,
        )

    def test_an_open_cohort_takes_the_student(self):
        """Acceptance criterion 2."""
        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(routed.booking.routed_reason, RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(routed.cohort, self.cohort)
        self.assertIn(self.student, self.cohort.students.all())
        self.assertEqual(routed.booking.cohort, self.cohort)
        self.assertEqual(routed.booking.teacher, self.cohort_teacher)

    def test_the_seat_is_a_real_booking(self):
        """A seat has to be attendable — a room, a status, a level."""
        booking = self.route().booking
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertTrue(booking.video_provider_meeting_id)
        self.assertEqual(booking.level, self.level)
        self.assertEqual(booking.start_time_utc, self.cohort.schedule_start_utc)

    def test_a_cohort_beats_an_available_lead_teacher(self):
        """Step 1 really is first: the lead here had capacity and hours."""
        self.assertEqual(self.route().reason, RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(
            Booking.objects.filter(teacher=self.lead).count(),
            0,
            "the lead must not have been booked while a cohort had a seat",
        )

    def test_several_students_fill_one_cohort(self):
        students = [StudentFactory() for _ in range(4)]
        for student in students:
            routed = self.route(student=student)
            self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)

        self.cohort.refresh_from_db()
        self.assertEqual(self.cohort.seats_taken, 4)
        self.assertEqual(
            Booking.objects.filter(cohort=self.cohort).count(),
            4,
            "one seat booking per student, all at the same instant",
        )

    def test_a_full_cohort_is_skipped_and_the_lead_takes_it(self):
        """The fallback works: a full class is not a dead end."""
        for _ in range(self.cohort.max_students):
            self.cohort.add_student(StudentFactory())

        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(routed.booking.teacher, self.lead)
        self.assertIsNone(routed.booking.cohort)

    def test_a_non_group_eligible_level_never_routes_to_a_cohort(self):
        plain = LevelFactory(
            track__organization=self.level.track.organization,
            group_eligible=False,
        )
        teaches(self.lead, plain)

        routed = self.route(level=plain)

        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)
        self.assertIn("not group-eligible", routed.considered["cohort"])

    def test_a_cohort_starting_far_from_the_request_is_not_used(self):
        far = self.slot + timedelta(hours=8)
        routed = self.route(start_time_utc=far)

        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)
        self.assertIn("near the requested time", routed.considered["cohort"])

    def test_a_cohort_starting_near_the_request_is_used(self):
        """Its own start wins, which is what "reasonably close" means."""
        asked = self.slot + timedelta(minutes=45)
        routed = self.route(start_time_utc=asked)

        self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(routed.booking.start_time_utc, self.cohort.schedule_start_utc)

    def test_a_student_already_seated_is_not_given_a_second_seat(self):
        self.route()
        second = self.route()

        self.assertEqual(second.reason, RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(self.cohort.students.filter(pk=self.student.pk).count(), 1)

    def test_the_closest_of_two_open_cohorts_wins(self):
        nearer = CohortFactory(
            availability=self.cohort_teacher.availability_windows.first(),
            teacher=self.cohort_teacher,
            level=self.level,
            schedule_start_utc=self.slot + timedelta(minutes=30),
        )
        routed = self.route(start_time_utc=self.slot + timedelta(minutes=25))
        self.assertEqual(routed.cohort, nearer)

    def test_a_minor_with_no_parent_link_is_not_seated(self):
        """The seat booking is refused, so no half-assignment is left behind.

        Membership and the booking are written in one transaction precisely so
        that this cannot leave a student in a class with nothing to attend.
        """
        minor = MinorStudentFactory()
        with self.assertRaises(NoCapacity):
            self.route(student=minor)

        self.assertEqual(self.cohort.students.filter(pk=minor.pk).count(), 0)
        self.assertFalse(Booking.objects.filter(student=minor).exists())

    def test_a_minor_with_a_parent_link_is_seated(self):
        link = ParentLinkFactory()
        routed = self.route(student=link.student)
        self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)


class RouteToLeadTests(RoutingWorld, TestCase):
    """Acceptance criterion 3 — within your hours and under your cap, it's yours."""

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)
        self.lead = self.available_teacher(lead=True, hours=2)

    def test_a_request_the_lead_can_take_routes_to_the_lead(self):
        """Acceptance criterion 3."""
        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(routed.booking.routed_reason, RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(routed.booking.teacher, self.lead)
        self.assertIsNone(routed.booking.cohort)
        self.assertEqual(routed.booking.status, BookingStatus.SCHEDULED)

    def test_the_lead_beats_an_idle_sub_teacher(self):
        """Step 2 precedes step 3, even when a sub is entirely free."""
        sub = self.available_teacher(hours=40)
        routed = self.route()

        self.assertEqual(routed.booking.teacher, self.lead)
        self.assertEqual(Booking.objects.filter(teacher=sub).count(), 0)

    def test_the_lead_is_skipped_outside_their_declared_hours(self):
        """No hours on that day, so step 2 declines and a sub takes it."""
        self.lead.availability_windows.all().delete()
        org = getattr(getattr(self.level, "track", None), "organization", None)
        Availability.objects.create(
            teacher=self.lead,
            organization=org,
            weekday=(self.slot.weekday() + 1) % 7,
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )
        sub = self.available_teacher()

        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(routed.booking.teacher, sub)
        self.assertEqual(
            refusal_codes(routed.considered["lead"]),
            ["outside_availability"],
            "the lead must have declined for want of hours, not for another reason",
        )

    def test_the_lead_is_skipped_when_already_booked_at_that_time(self):
        self.book(
            teacher=self.lead,
            start_time_utc=self.slot,
        )
        sub = self.available_teacher()

        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(routed.booking.teacher, sub)

    def test_the_lead_is_skipped_for_a_level_they_do_not_teach(self):
        """Specialties gate the lead as much as anyone."""
        other = LevelFactory(track__organization=self.level.track.organization)
        sub = self.available_teacher()
        teaches(sub, other)

        routed = self.route(level=other)

        self.assertEqual(routed.reason, RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(routed.booking.teacher, sub)


class RouteToSubTests(RoutingWorld, TestCase):
    """Acceptance criteria 4 and 5 — the lead is full, and a sub's own cap holds."""

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)
        self.lead = self.available_teacher(lead=True, hours=1)

    def test_a_full_lead_routes_to_a_sub_teacher(self):
        """Acceptance criterion 4."""
        sub = self.available_teacher(hours=10)
        self.fill_week(self.lead, 60)  # the lead's whole one-hour cap

        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(routed.booking.routed_reason, RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(routed.booking.teacher, sub)
        self.assertEqual(
            refusal_codes(routed.considered["lead"]),
            ["teacher_weekly_capacity_exceeded"],
            "the lead must have declined on capacity specifically",
        )

    def test_a_sub_over_their_own_cap_is_never_selected(self):
        """Acceptance criterion 5.

        The over-capacity sub is the *only* specialist, so if the cap were not
        enforced they would certainly be picked — which is what makes this a test
        of the cap rather than of the ranking.
        """
        self.fill_week(self.lead, 60)
        full_sub = self.available_teacher(hours=1)
        self.fill_week(full_sub, 60)

        with self.assertRaises(NoCapacity) as ctx:
            self.route()

        self.assertEqual(
            refusal_codes(ctx.exception.considered["sub_teachers"]),
            ["teacher_weekly_capacity_exceeded"],
        )
        self.assertFalse(Booking.objects.filter(start_time_utc=self.slot).exists())

    def test_a_full_sub_is_passed_over_for_one_with_room(self):
        """Criterion 5 again, with an alternative available.

        The full sub is created *first*, so the lowest-pk tiebreak would pick them
        if capacity were not consulted at all.
        """
        self.fill_week(self.lead, 60)
        full_sub = self.available_teacher(hours=1)
        self.fill_week(full_sub, 60)
        free_sub = self.available_teacher(hours=10)

        routed = self.route()

        self.assertEqual(routed.booking.teacher, free_sub)
        self.assertIn(full_sub.username, routed.considered["sub_teachers"])

    def test_the_sub_with_most_remaining_capacity_is_chosen(self):
        """The spec's "simplest correct rule" — spread the load, no scoring."""
        self.fill_week(self.lead, 60)
        busier = self.available_teacher(hours=10)
        self.fill_week(busier, 8 * 60)
        emptier = self.available_teacher(hours=10)
        self.fill_week(emptier, 2 * 60)

        self.assertEqual(self.route().booking.teacher, emptier)

    def test_capacity_is_compared_as_remaining_not_as_a_ratio(self):
        """A big cap barely used loses to a small cap not used at all.

        Guards the "most *remaining* capacity" wording against being quietly
        reinterpreted as a percentage, which is the first step towards the
        weighted score the spec says to resist.
        """
        self.fill_week(self.lead, 60)
        # 20h cap, 19h used -> 60 minutes left.
        nearly_full = self.available_teacher(hours=20)
        self.fill_week(nearly_full, 19 * 60)
        # 2h cap, nothing used -> 120 minutes left.
        small_but_free = self.available_teacher(hours=2)

        self.assertEqual(self.route().booking.teacher, small_but_free)

    def test_a_tie_on_capacity_is_broken_deterministically(self):
        self.fill_week(self.lead, 60)
        first = self.available_teacher(hours=10)
        self.available_teacher(hours=10)

        self.assertEqual(self.route().booking.teacher, first)

    def test_a_non_specialist_sub_is_never_selected(self):
        self.fill_week(self.lead, 60)
        self.available_teacher(teaches_level=False, hours=40)

        with self.assertRaises(NoCapacity) as ctx:
            self.route()
        self.assertIn("specialises", ctx.exception.considered["sub_teachers"])

    def test_an_unapproved_sub_is_never_selected(self):
        self.fill_week(self.lead, 60)
        self.available_teacher(approved=False, hours=40)

        with self.assertRaises(NoCapacity):
            self.route()

    def test_a_sub_outside_their_hours_is_not_selected(self):
        self.fill_week(self.lead, 60)
        sub = self.make_teacher(hours=10)  # specialist, but no declared hours

        with self.assertRaises(NoCapacity) as ctx:
            self.route()
        self.assertEqual(
            refusal_codes(ctx.exception.considered["sub_teachers"]),
            ["outside_availability"],
        )
        self.assertEqual(Booking.objects.filter(teacher=sub).count(), 0)

    def test_a_sub_already_booked_at_that_time_is_not_selected(self):
        self.fill_week(self.lead, 60)
        busy = self.available_teacher(hours=10)
        self.book(
            teacher=busy,
            start_time_utc=self.slot,
        )
        free = self.available_teacher(hours=10)

        self.assertEqual(self.route().booking.teacher, free)


class NoCapacityTests(RoutingWorld, TestCase):
    """Acceptance criterion 6 — a clear failure, not a forced booking.

    The spec is emphatic: "do not fall through to 'just pick anyone anyway.' A
    clear failure is correct behaviour here, not a bug to route around." So each
    test asserts both the refusal *and* that the database is untouched.
    """

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)

    def assert_refused(self):
        """Routing refused, and nothing was booked for the requested slot.

        Scoped to ``self.slot`` rather than to the whole table, because filling a
        teacher's week is how these tests *build* the no-capacity state — the
        bookings that made everyone full are supposed to be there. What must not
        exist is a booking for the session that was refused.
        """
        with self.assertRaises(NoCapacity) as ctx:
            self.route()
        self.assertFalse(
            Booking.objects.filter(start_time_utc=self.slot).exists(),
            "nothing may be booked for the slot routing refused",
        )
        return ctx.exception

    def test_no_cohort_no_lead_and_no_sub_is_a_clear_failure(self):
        """Acceptance criterion 6, in its emptiest form."""
        exc = self.assert_refused()
        self.assertEqual(
            set(exc.considered),
            {"cohort", "lead", "sub_teachers"},
            "the refusal must say what each of the three steps found",
        )

    def test_the_failure_names_every_step_that_declined(self):
        """The response has to be explainable, which is what ``considered`` is for."""
        self.available_teacher(lead=True, hours=1)
        self.fill_week(lead_teacher(), 60)
        self.available_teacher(hours=1)
        self.fill_week(matching_sub_teachers(self.level).first(), 60)

        exc = self.assert_refused()
        self.assertIn("not group-eligible", exc.considered["cohort"])
        self.assertEqual(
            refusal_codes(exc.considered["lead"]),
            ["teacher_weekly_capacity_exceeded"],
        )
        self.assertEqual(
            refusal_codes(exc.considered["sub_teachers"]),
            ["teacher_weekly_capacity_exceeded"],
        )

    def test_everyone_full_is_a_failure_not_an_over_booking(self):
        """Nobody is pushed past their cap to avoid returning an error."""
        lead = self.available_teacher(lead=True, hours=1)
        self.fill_week(lead, 60)
        sub = self.available_teacher(hours=1)
        self.fill_week(sub, 60)

        with self.assertRaises(NoCapacity):
            self.route()

        for teacher in (lead, sub):
            with self.subTest(teacher=teacher.username):
                self.assertEqual(
                    weekly_committed_minutes(teacher.pk, self.slot),
                    60,
                    "their week must be exactly as full as it was",
                )

    def test_nobody_free_at_that_hour_is_a_failure_not_a_booking_outside_hours(self):
        lead = self.available_teacher(lead=True)
        sub = self.available_teacher()
        org = getattr(getattr(self.level, "track", None), "organization", None)
        for teacher in (lead, sub):
            teacher.availability_windows.all().delete()
            Availability.objects.create(
                teacher=teacher,
                organization=org,
                weekday=self.slot.weekday(),
                start_time_utc=time(3, 0),
                end_time_utc=time(4, 0),
            )

        exc = self.assert_refused()
        self.assertEqual(refusal_codes(exc.considered["lead"]), ["outside_availability"])
        self.assertEqual(
            refusal_codes(exc.considered["sub_teachers"]), ["outside_availability"]
        )

    def test_a_level_nobody_teaches_is_a_failure(self):
        self.available_teacher(lead=True, teaches_level=False)
        self.available_teacher(teaches_level=False)
        self.assert_refused()

    def test_a_request_in_the_past_is_a_failure_not_a_backdated_booking(self):
        """Routing must not become a way around the Phase 3.5 past-start rule."""
        self.available_teacher(lead=True)
        self.available_teacher()

        with self.assertRaises(NoCapacity) as ctx:
            self.route(start_time_utc=dj_timezone.now() - timedelta(days=1))
        self.assertIn(
            "start_time_in_past", refusal_codes(ctx.exception.considered["lead"])
        )
        self.assertFalse(Booking.objects.exists())


class RoutingWritesThroughSaveTests(RoutingWorld, TestCase):
    """CLAUDE.md's Phase 4 note: routing must go through ``Booking.save()``.

    Not a style preference — ``save()`` is what takes ``TeacherBookingLock`` and
    runs ``clean()``, so a ``bulk_create`` of seats would bypass both and silently
    reopen the race Phase 3.5 closed. These assert the observable consequences of
    having gone through ``save()``, since the call itself cannot be asserted.
    """

    def setUp(self):
        self.level = GroupEligibleLevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)
        self.lead = self.available_teacher(lead=True)

    def test_a_routed_booking_gets_a_video_room(self):
        """Generated in ``save()``, so its presence proves ``save()`` ran."""
        booking = self.route().booking
        self.assertTrue(booking.video_provider_meeting_id)
        self.assertTrue(booking.video_join_url.endswith(booking.video_provider_meeting_id))

    def test_a_routed_booking_takes_the_teachers_lock(self):
        """``TeacherBookingLock`` is created by ``save()`` and by nothing else."""
        routed = self.route()
        self.assertTrue(
            TeacherBookingLock.objects.filter(teacher=routed.booking.teacher).exists()
        )

    def test_a_routed_cohort_seat_takes_the_lock_too(self):
        cohort_teacher = self.available_teacher()
        CohortFactory(
            availability=cohort_teacher.availability_windows.first(),
            teacher=cohort_teacher,
            level=self.level,
            schedule_start_utc=self.slot,
        )
        routed = self.route()

        self.assertEqual(routed.reason, RoutedReason.COHORT_ASSIGNED)
        self.assertTrue(
            TeacherBookingLock.objects.filter(teacher=cohort_teacher).exists(),
            "a cohort seat must be written through save(), not bulk_create",
        )

    def test_a_routed_booking_is_validated(self):
        """``clean()`` ran, so an impossible booking never reaches the database.

        Proved by the one rule routing cannot pre-empt: a duplicate video room is
        impossible, but a level the teacher stopped teaching between the candidate
        check and the write is exactly what ``save()`` re-checks.
        """
        routed = self.route()
        stored = Booking.objects.get(pk=routed.booking.pk)
        self.assertEqual(stored.routed_reason, RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(stored.status, BookingStatus.SCHEDULED)


class RoutedBookingsAreOrdinaryBookingsTests(RoutingWorld, TestCase):
    """Whatever routing produces has to behave like anything else in the system."""

    def setUp(self):
        self.level = GroupEligibleLevelFactory()
        self.student = StudentFactory()
        self.slot = next_monday() + timedelta(hours=10)

    def test_a_routed_booking_can_be_cancelled(self):
        self.available_teacher(lead=True)
        booking = self.route().booking

        booking.cancel()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_cancelling_a_routed_booking_frees_the_slot(self):
        lead = self.available_teacher(lead=True)
        first = self.route().booking
        first.cancel()

        second = self.route(student=StudentFactory())

        self.assertEqual(second.booking.teacher, lead)
        self.assertEqual(second.booking.start_time_utc, self.slot)

    def test_a_routed_seat_keeps_the_student_in_the_cohort_after_cancelling(self):
        """A documented consequence, not a claim that it is ideal.

        Cancelling a seat leaves the membership behind, so the student holds a seat
        with nothing to attend and the class looks fuller than it is. Recorded in
        tech-debt.md; asserted here so a later phase that fixes it has to change a
        test on purpose rather than discovering the rule by accident.
        """
        cohort_teacher = self.available_teacher()
        cohort = CohortFactory(
            availability=cohort_teacher.availability_windows.first(),
            teacher=cohort_teacher,
            level=self.level,
            schedule_start_utc=self.slot,
        )
        routed = self.route()
        routed.booking.cancel()

        cohort.refresh_from_db()
        self.assertIn(self.student, cohort.students.all())
        self.assertEqual(cohort.seats_taken, 1)


class RoutingTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.7: Candidate routing and preferred-teacher resolution are academy-scoped."""

    def setUp(self):
        self.org_a = OrganizationFactory(name="Academy A")
        self.org_b = OrganizationFactory(name="Academy B")
        self.track_a = TrackFactory(organization=self.org_a)
        self.track_b = TrackFactory(organization=self.org_b)
        self.level_a = LevelFactory(track=self.track_a)
        self.level_b = LevelFactory(track=self.track_b)
        self.student_a = StudentFactory()
        admit(self.student_a, self.org_a)
        self.student_b = StudentFactory()
        admit(self.student_b, self.org_b)
        self.slot = next_monday() + timedelta(hours=10)

    def test_lead_teacher_scoped_to_academy(self):
        """Lead candidate resolution searches only active members of target academy."""
        lead_a = BookableLeadTeacherFactory()
        ensure_teacher_configured(lead_a, self.org_a)
        teaches(lead_a, self.level_a)

        lead_b = BookableLeadTeacherFactory()
        ensure_teacher_configured(lead_b, self.org_b)
        teaches(lead_b, self.level_b)

        self.assertEqual(
            lead_teacher(organization=self.org_a, track=self.track_a), lead_a
        )
        self.assertEqual(
            lead_teacher(organization=self.org_b, track=self.track_b), lead_b
        )
        self.assertIsNone(
            lead_teacher(organization=self.org_a, track=self.track_b)
        )

    def test_sub_teacher_scoped_to_academy(self):
        """Sub-teacher candidate resolution matches only within target academy."""
        sub_a = BookableTeacherFactory()
        ensure_teacher_configured(sub_a, self.org_a)
        teaches(sub_a, self.level_a)

        sub_b = BookableTeacherFactory()
        ensure_teacher_configured(sub_b, self.org_b)
        teaches(sub_b, self.level_b)

        self.assertEqual(
            list(matching_sub_teachers(self.level_a, organization=self.org_a)),
            [sub_a],
        )
        self.assertEqual(
            list(matching_sub_teachers(self.level_b, organization=self.org_b)),
            [sub_b],
        )
        self.assertEqual(
            list(matching_sub_teachers(self.level_a, organization=self.org_b)),
            [],
        )

    def test_route_session_does_not_borrow_lead_from_another_academy(self):
        """Academy A route request raises NoCapacity when A has no lead, even if B has an available lead."""
        lead_b = BookableLeadTeacherFactory()
        ensure_teacher_configured(lead_b, self.org_b)
        teaches(lead_b, self.level_b)
        Availability.objects.create(
            teacher=lead_b,
            organization=self.org_b,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        with self.assertRaises(NoCapacity):
            route_session(
                student=self.student_a,
                level=self.level_a,
                start_time_utc=self.slot,
                organization=self.org_a,
            )

    def test_route_session_does_not_borrow_sub_from_another_academy(self):
        """Academy A route request raises NoCapacity when A has no sub, even if B has a free sub."""
        sub_b = BookableTeacherFactory()
        ensure_teacher_configured(sub_b, self.org_b)
        teaches(sub_b, self.level_b)
        Availability.objects.create(
            teacher=sub_b,
            organization=self.org_b,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        with self.assertRaises(NoCapacity):
            route_session(
                student=self.student_a,
                level=self.level_a,
                start_time_utc=self.slot,
                organization=self.org_a,
            )

    def test_route_session_does_not_borrow_cohort_from_another_academy(self):
        """Academy A route request does not borrow an open cohort belonging to Academy B."""
        level_a_group = GroupEligibleLevelFactory(track=self.track_a)
        level_b_group = GroupEligibleLevelFactory(track=self.track_b)
        teacher_b = BookableTeacherFactory()
        ensure_teacher_configured(teacher_b, self.org_b)
        teaches(teacher_b, level_b_group)
        avail_b = Availability.objects.create(
            teacher=teacher_b,
            organization=self.org_b,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )
        CohortFactory(
            availability=avail_b,
            teacher=teacher_b,
            level=level_b_group,
            schedule_start_utc=self.slot,
            max_students=6,
        )

        with self.assertRaises(NoCapacity):
            route_session(
                student=self.student_a,
                level=level_a_group,
                start_time_utc=self.slot,
                organization=self.org_a,
            )

    def test_preferred_teacher_from_another_academy_rejected_no_waitlist(self):
        """Teacher belonging only to Academy B is rejected outright in Academy A; no waitlist created."""
        teacher_b = BookableTeacherFactory()
        ensure_teacher_configured(teacher_b, self.org_b)
        teaches(teacher_b, self.level_b)
        Availability.objects.create(
            teacher=teacher_b,
            organization=self.org_b,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        with self.assertRaises(ValidationError) as ctx:
            route_session(
                student=self.student_a,
                level=self.level_a,
                start_time_utc=self.slot,
                preferred_teacher=teacher_b,
                organization=self.org_a,
            )
        self.assertIn("teacher", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["teacher"][0].code,
            "teacher_not_active_member",
        )
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_preferred_teacher_in_academy_temporarily_unavailable_creates_waitlist(self):
        """Teacher belonging to Academy A who has no availability at requested slot waitlists."""
        teacher_a = BookableTeacherFactory()
        ensure_teacher_configured(teacher_a, self.org_a)
        teaches(teacher_a, self.level_a)
        Availability.objects.create(
            teacher=teacher_a,
            organization=self.org_a,
            weekday=(self.slot.weekday() + 1) % 7,
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        with self.assertRaises(NoCapacity) as ctx:
            route_session(
                student=self.student_a,
                level=self.level_a,
                start_time_utc=self.slot,
                preferred_teacher=teacher_a,
                organization=self.org_a,
            )
        self.assertIn("waitlist", ctx.exception.considered)
        self.assertTrue(
            TeacherWaitlist.objects.filter(
                student=self.student_a,
                requested_teacher=teacher_a,
                level=self.level_a,
            ).exists()
        )

    def test_route_session_mismatched_organization_rejected(self):
        """Passing level from Academy B with explicit organization Academy A raises ValidationError."""
        with self.assertRaises(ValidationError) as ctx:
            route_session(
                student=self.student_a,
                level=self.level_b,
                start_time_utc=self.slot,
                organization=self.org_a,
            )
        self.assertIn("level", ctx.exception.error_dict)
