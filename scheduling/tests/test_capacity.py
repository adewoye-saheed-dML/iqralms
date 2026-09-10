"""Weekly capacity — ``max_weekly_hours`` as a hard cap, and the week it counts over.

Phase 4 turns a field that had existed unenforced since Phase 1 into a rule that
refuses bookings. Two things are being pinned here.

**The cap is hard.** The spec: "A booking that would push a teacher over their
weekly cap is rejected, not just discouraged", for the lead and sub-teachers
equally.

**The week has exactly one definition.** ``utils.week_bounds`` — Monday 00:00 UTC
to the following Monday 00:00 UTC — and the spec asks specifically that a future
payout calculation not invent a second one. The boundary tests below are what
would notice if it drifted.

Times here are built from ``week_bounds`` and explicit dates rather than from
``slot_at``, because the boundary is the thing under test and ``slot_at``
deliberately hides which week it lands in.
"""

from datetime import datetime, time, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.tests.factories import StudentFactory
from curriculum.tests.factories import LevelFactory
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    MINUTES_PER_HOUR,
    remaining_weekly_minutes,
    weekly_committed_minutes,
)
from scheduling.utils import UTC, week_bounds

from .factories import (
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    slot_at,
    teaches,
)

#: A Monday well clear of "now", so these tests never depend on the wall clock.
A_MONDAY = datetime(2026, 9, 7, tzinfo=UTC)


class WeekBoundsTests(TestCase):
    """The single definition of "the current week"."""

    def test_a_week_runs_monday_to_monday_in_utc(self):
        start, end = week_bounds(A_MONDAY + timedelta(days=3, hours=14))
        self.assertEqual(start, A_MONDAY)
        self.assertEqual(end, A_MONDAY + timedelta(days=7))

    def test_monday_midnight_belongs_to_the_week_it_opens(self):
        start, _ = week_bounds(A_MONDAY)
        self.assertEqual(start, A_MONDAY)

    def test_sunday_night_is_still_the_same_week(self):
        last_moment = A_MONDAY + timedelta(days=6, hours=23, minutes=59)
        self.assertEqual(week_bounds(last_moment)[0], A_MONDAY)

    def test_the_next_monday_opens_a_new_week(self):
        self.assertEqual(
            week_bounds(A_MONDAY + timedelta(days=7))[0],
            A_MONDAY + timedelta(days=7),
        )

    def test_every_weekday_maps_to_the_same_monday(self):
        for offset in range(7):
            with self.subTest(offset=offset):
                self.assertEqual(
                    week_bounds(A_MONDAY + timedelta(days=offset))[0], A_MONDAY
                )

    def test_a_non_utc_moment_is_read_in_utc(self):
        """Storage is UTC, so the week is a UTC week whatever zone comes in.

        A Monday 00:30 in Lagos (UTC+1) is Sunday 23:30 UTC, which belongs to the
        *previous* week. Counting it as the new one would let a teacher's load
        straddle two weeks depending on who asked.
        """
        from zoneinfo import ZoneInfo

        lagos_monday_early = datetime(
            2026, 9, 7, 0, 30, tzinfo=ZoneInfo("Africa/Lagos")
        )
        self.assertEqual(
            week_bounds(lagos_monday_early)[0], A_MONDAY - timedelta(days=7)
        )


def set_teacher_cap(teacher, hours, organization=None):
    """Set max_weekly_hours on TeacherProfile and OrganizationTeacherConfiguration."""
    profile = getattr(teacher, "teacher_profile", None)
    if profile is not None:
        profile.max_weekly_hours = hours
        profile.save()
    from accounts.models import OrganizationTeacherConfiguration

    configs = OrganizationTeacherConfiguration.objects.filter(membership__user=teacher)
    if organization is not None:
        configs = configs.filter(membership__organization=organization)
    configs.update(max_weekly_hours=hours)


class WeeklyCommittedMinutesTests(TestCase):
    """What counts towards a teacher's week, and what does not."""

    def setUp(self):
        self.teacher = BookableTeacherFactory()
        self.level = LevelFactory()
        teaches(self.teacher, self.level)
        # A full-day window on every weekday, so availability can never be the
        # reason a booking below is refused.
        for weekday in range(7):
            Availability.objects.create(
                organization=self.level.track.organization,
                teacher=self.teacher,
                weekday=weekday,
                start_time_utc=time(0, 0),
                end_time_utc=time.max,
            )

    def book(self, start, minutes=30, status=BookingStatus.SCHEDULED, cohort=None):
        """Write a booking straight to the row, bypassing creation-time rules.

        These tests place sessions on fixed 2026 dates so the week boundary is
        unambiguous, which means most of them are in the past relative to the test
        run — a state ``clean()`` refuses to *create* but which every taught
        session reaches. An ``objects.create`` followed by an ``update`` is the
        same trick ``test_models.age_booking`` uses, and for the same reason.
        """
        booking = Booking.objects.create(
            student=StudentFactory(),
            teacher=self.teacher,
            level=cohort.level if cohort else self.level,
            start_time_utc=slot_at(self.teacher.availability_windows.first()),
            duration_minutes=minutes,
        )
        Booking.objects.filter(pk=booking.pk).update(
            start_time_utc=start, status=status, cohort=cohort
        )
        booking.refresh_from_db()
        return booking

    def committed(self, moment=None):
        return weekly_committed_minutes(self.teacher.pk, moment or A_MONDAY)

    def test_an_empty_week_is_zero(self):
        self.assertEqual(self.committed(), 0)

    def test_scheduled_minutes_are_counted(self):
        self.book(A_MONDAY + timedelta(hours=10), minutes=45)
        self.assertEqual(self.committed(), 45)

    def test_minutes_across_the_week_are_summed(self):
        self.book(A_MONDAY + timedelta(hours=10), minutes=30)
        self.book(A_MONDAY + timedelta(days=3, hours=10), minutes=60)
        self.assertEqual(self.committed(), 90)

    def test_a_cancelled_booking_does_not_count(self):
        """Cancelling is the one thing that gives capacity back."""
        self.book(
            A_MONDAY + timedelta(hours=10), minutes=30, status=BookingStatus.CANCELLED
        )
        self.assertEqual(self.committed(), 0)

    def test_a_completed_session_still_counts(self):
        """Product owner's call: teaching a session does not refund the budget.

        Were this not so, a 20-hour teacher could be booked well past 20 hours in
        one week simply because Monday's sessions had been marked completed by
        Thursday. The spec's wording said "scheduled"; this is the hole in it.
        """
        self.book(
            A_MONDAY + timedelta(hours=10), minutes=90, status=BookingStatus.COMPLETED
        )
        self.assertEqual(self.committed(), 90)

    def test_a_no_show_still_counts(self):
        """The teacher held the slot and turned up; the student did not."""
        self.book(
            A_MONDAY + timedelta(hours=10), minutes=30, status=BookingStatus.NO_SHOW
        )
        self.assertEqual(self.committed(), 30)

    def test_last_weeks_bookings_do_not_count(self):
        self.book(A_MONDAY - timedelta(hours=1), minutes=120)
        self.assertEqual(self.committed(), 0)

    def test_next_weeks_bookings_do_not_count(self):
        self.book(A_MONDAY + timedelta(days=7), minutes=120)
        self.assertEqual(self.committed(), 0)

    def test_a_session_at_the_very_start_of_the_week_counts(self):
        self.book(A_MONDAY, minutes=30)
        self.assertEqual(self.committed(), 30)

    def test_a_session_in_the_last_minute_of_the_week_counts(self):
        self.book(A_MONDAY + timedelta(days=6, hours=23, minutes=30), minutes=30)
        self.assertEqual(self.committed(), 30)

    def test_another_teachers_bookings_do_not_count(self):
        other = BookableTeacherFactory()
        teaches(other, self.level)
        booking = self.book(A_MONDAY + timedelta(hours=10), minutes=60)
        Booking.objects.filter(pk=booking.pk).update(teacher=other)
        self.assertEqual(self.committed(), 0)

    def test_a_cohort_session_counts_once_however_many_seats(self):
        """Six students in one class is one teacher teaching for thirty minutes.

        Charging the teacher per seat would make cohorts — the entire throughput
        lever this phase exists for — look more expensive than the 1:1 sessions
        they replace, and a six-seat class would eat three hours of a cap.
        """
        cohort = CohortFactory(teacher=self.teacher, max_students=6)
        for _ in range(4):
            self.book(cohort.schedule_start_utc, minutes=30, cohort=cohort)

        moment = cohort.schedule_start_utc
        self.assertEqual(weekly_committed_minutes(self.teacher.pk, moment), 30)

    def test_two_different_cohorts_count_separately(self):
        first = CohortFactory(teacher=self.teacher)
        second = CohortFactory(teacher=self.teacher, level=first.level)
        self.book(A_MONDAY + timedelta(hours=9), minutes=30, cohort=first)
        self.book(A_MONDAY + timedelta(hours=11), minutes=30, cohort=second)
        self.assertEqual(self.committed(), 60)

    def test_a_cohort_and_a_one_to_one_session_both_count(self):
        cohort = CohortFactory(teacher=self.teacher)
        self.book(A_MONDAY + timedelta(hours=9), minutes=30, cohort=cohort)
        self.book(A_MONDAY + timedelta(hours=14), minutes=30)
        self.assertEqual(self.committed(), 60)

    def test_including_weighs_a_prospective_booking(self):
        """How the cap is checked before the row exists."""
        self.book(A_MONDAY + timedelta(hours=10), minutes=30)
        self.assertEqual(
            weekly_committed_minutes(
                self.teacher.pk, A_MONDAY, including=(None, 45)
            ),
            75,
        )

    def test_including_a_seat_in_an_already_counted_cohort_adds_nothing(self):
        cohort = CohortFactory(teacher=self.teacher)
        self.book(cohort.schedule_start_utc, minutes=30, cohort=cohort)
        self.assertEqual(
            weekly_committed_minutes(
                self.teacher.pk,
                cohort.schedule_start_utc,
                including=(cohort.pk, 30),
            ),
            30,
        )

    def test_excluding_pk_leaves_a_booking_out(self):
        booking = self.book(A_MONDAY + timedelta(hours=10), minutes=30)
        self.assertEqual(
            weekly_committed_minutes(
                self.teacher.pk, A_MONDAY, excluding_pk=booking.pk
            ),
            0,
        )

    def test_remaining_minutes_is_the_cap_less_the_load(self):
        set_teacher_cap(self.teacher, 2, self.level.track.organization)
        self.book(A_MONDAY + timedelta(hours=10), minutes=30)
        self.assertEqual(
            remaining_weekly_minutes(
                self.teacher, A_MONDAY, organization=self.level.track.organization
            ),
            2 * MINUTES_PER_HOUR - 30,
        )

    def test_remaining_minutes_can_go_negative(self):
        """A cap lowered under an existing load, reported honestly.

        Returned rather than clamped so an over-committed teacher sorts *below* an
        exactly-full one in routing's step 3 instead of tying with them. The
        booking comes first and the cap is cut afterwards, which is both the only
        order ``clean()`` allows and the order it happens in real life.
        """
        self.book(A_MONDAY + timedelta(hours=10), minutes=90)

        set_teacher_cap(self.teacher, 1, self.level.track.organization)

        self.assertEqual(
            remaining_weekly_minutes(
                self.teacher, A_MONDAY, organization=self.level.track.organization
            ),
            -30,
        )


class WeeklyCapEnforcementTests(TestCase):
    """The cap refuses bookings — the rule Phase 4 actually turns on."""

    def setUp(self):
        self.level = LevelFactory()
        self.window_start = time(0, 0)

    def teacher_with_cap(self, hours, factory=BookableTeacherFactory):
        """A teacher capped at ``hours``, free all week, teaching this track."""
        teacher = factory()
        teaches(teacher, self.level)
        set_teacher_cap(teacher, hours, self.level.track.organization)
        for weekday in range(7):
            Availability.objects.create(
                organization=self.level.track.organization,
                teacher=teacher,
                weekday=weekday,
                start_time_utc=self.window_start,
                end_time_utc=time.max,
            )
        return teacher

    def next_monday(self):
        """The start of a week that has not happened yet.

        The cap counts the week containing the booking, and a booking cannot be
        created in the past — so these tests need a future week, and they need to
        know which one it is.
        """
        _, next_week = week_bounds(dj_timezone.now())
        return next_week

    def book(self, teacher, offset, minutes=30):
        return Booking.objects.create(
            student=StudentFactory(),
            teacher=teacher,
            level=self.level,
            start_time_utc=self.next_monday() + offset,
            duration_minutes=minutes,
        )

    def test_a_booking_within_the_cap_is_accepted(self):
        teacher = self.teacher_with_cap(2)
        booking = self.book(teacher, timedelta(hours=9), minutes=60)
        self.assertEqual(booking.duration_minutes, 60)

    def test_a_booking_filling_the_cap_exactly_is_accepted(self):
        """The boundary is inclusive: at the cap is under it, over it is not."""
        teacher = self.teacher_with_cap(1)
        self.book(teacher, timedelta(hours=9), minutes=30)
        self.book(teacher, timedelta(hours=10), minutes=30)
        self.assertEqual(
            weekly_committed_minutes(teacher.pk, self.next_monday()), 60
        )

    def test_a_booking_over_the_cap_is_rejected(self):
        teacher = self.teacher_with_cap(1)
        self.book(teacher, timedelta(hours=9), minutes=60)

        with self.assertRaises(ValidationError) as ctx:
            self.book(teacher, timedelta(hours=11), minutes=30)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["__all__"]],
            ["teacher_weekly_capacity_exceeded"],
        )
        self.assertEqual(Booking.objects.filter(teacher=teacher).count(), 1)

    def test_a_single_booking_longer_than_the_whole_cap_is_rejected(self):
        teacher = self.teacher_with_cap(1)
        with self.assertRaises(ValidationError) as ctx:
            self.book(teacher, timedelta(hours=9), minutes=120)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["__all__"]],
            ["teacher_weekly_capacity_exceeded"],
        )

    def test_the_cap_applies_to_the_lead_teacher_too(self):
        """The spec: a hard cap "for both you and sub-teachers"."""
        lead = self.teacher_with_cap(1, factory=BookableLeadTeacherFactory)
        self.book(lead, timedelta(hours=9), minutes=60)
        with self.assertRaises(ValidationError) as ctx:
            self.book(lead, timedelta(hours=11), minutes=30)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["__all__"]],
            ["teacher_weekly_capacity_exceeded"],
        )

    def test_next_weeks_booking_is_unaffected_by_this_weeks_load(self):
        """The cap is weekly, so a full week must not block the following one."""
        teacher = self.teacher_with_cap(1)
        self.book(teacher, timedelta(hours=9), minutes=60)

        later = self.book(teacher, timedelta(days=7, hours=9), minutes=60)
        self.assertTrue(later.pk)

    def test_a_cancelled_booking_gives_its_capacity_back(self):
        teacher = self.teacher_with_cap(1)
        first = self.book(teacher, timedelta(hours=9), minutes=60)
        first.cancel()

        replacement = self.book(teacher, timedelta(hours=11), minutes=60)
        self.assertTrue(replacement.pk)

    def test_a_cohort_seat_does_not_re_charge_the_teacher(self):
        """Filling a group class must not consume the cap once per student.

        A one-hour-capped teacher can seat six students in a 30-minute class; a
        per-seat count would have refused the third.
        """
        teacher = self.teacher_with_cap(1)
        cohort = CohortFactory(
            availability=teacher.availability_windows.first(),
            teacher=teacher,
            max_students=6,
            schedule_start_utc=self.next_monday() + timedelta(hours=9),
        )
        for _ in range(6):
            Booking.objects.create(
                student=StudentFactory(),
                teacher=teacher,
                level=cohort.level,
                cohort=cohort,
                start_time_utc=cohort.schedule_start_utc,
            )
        self.assertEqual(
            weekly_committed_minutes(teacher.pk, cohort.schedule_start_utc), 30
        )

    def test_lowering_a_cap_does_not_freeze_existing_bookings(self):
        """Creation-only, the same scope as the availability rule.

        A teacher whose cap is cut below their current load must still be able to
        cancel what they hold — an unsaveable booking is an uncancellable one
        (learnings.md).
        """
        teacher = self.teacher_with_cap(2)
        booking = self.book(teacher, timedelta(hours=9), minutes=90)

        set_teacher_cap(teacher, 1, self.level.track.organization)

        booking.cancel()  # must not raise
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_a_full_teacher_can_still_be_marked_completed(self):
        """The other write every finished session needs."""
        teacher = self.teacher_with_cap(1)
        booking = self.book(teacher, timedelta(hours=9), minutes=60)

        booking.status = BookingStatus.COMPLETED
        booking.save()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.COMPLETED)


class MultiAcademyCapacityIsolationTests(TestCase):
    """SaaS Phase 4 Task 4.4 — Multi-academy capacity isolation (spec Section 17).

    Example from spec:
    Teacher T
    Academy A cap = 10 hours
    Academy B cap = 5 hours

    If T has used:
    8 hours in Academy A
    1 hour in Academy B

    remaining capacity is:
    Academy A = 2 hours
    Academy B = 4 hours

    Do not sum Academy A's booking minutes into Academy B's contracted weekly cap.
    """

    def setUp(self):
        from organizations.tests.factories import OrganizationFactory
        from curriculum.tests.factories import TrackFactory, admit

        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")

        self.teacher = BookableTeacherFactory()
        self.track_a = TrackFactory(organization=self.org_a)
        self.level_a = LevelFactory(track=self.track_a)
        self.track_b = TrackFactory(organization=self.org_b)
        self.level_b = LevelFactory(track=self.track_b)

        teaches(self.teacher, self.level_a)
        teaches(self.teacher, self.level_b)

        # Set Academy A cap = 10 hours, Academy B cap = 5 hours
        set_teacher_cap(self.teacher, 10, self.org_a)
        set_teacher_cap(self.teacher, 5, self.org_b)

        # Create availability all week in both academies
        for weekday in range(7):
            Availability.objects.create(
                organization=self.org_a,
                teacher=self.teacher,
                weekday=weekday,
                start_time_utc=time(0, 0),
                end_time_utc=time.max,
            )
            Availability.objects.create(
                organization=self.org_b,
                teacher=self.teacher,
                weekday=weekday,
                start_time_utc=time(0, 0),
                end_time_utc=time.max,
            )

        self.student_a = StudentFactory()
        admit(self.student_a, self.org_a)
        self.student_b = StudentFactory()
        admit(self.student_b, self.org_b)

    def test_multi_academy_capacity_consumption_is_isolated(self):
        """Spec Section 17: 8h used in A, 1h used in B -> remaining A=2h, B=4h."""
        _, next_week = week_bounds(dj_timezone.now())
        future_monday = next_week
        moment = future_monday + timedelta(hours=10)

        # Book 8 hours (16 x 30m) in Academy A
        for i in range(16):
            Booking.objects.create(
                student=self.student_a,
                teacher=self.teacher,
                level=self.level_a,
                start_time_utc=future_monday + timedelta(hours=i),
                duration_minutes=30,
            )

        # Book 1 hour (2 x 30m) in Academy B
        for i in range(2):
            Booking.objects.create(
                student=self.student_b,
                teacher=self.teacher,
                level=self.level_b,
                start_time_utc=future_monday + timedelta(days=1, hours=i),
                duration_minutes=30,
            )

        # Committed minutes isolated per academy
        self.assertEqual(
            weekly_committed_minutes(self.teacher.pk, moment, organization=self.org_a),
            8 * 60,
        )
        self.assertEqual(
            weekly_committed_minutes(self.teacher.pk, moment, organization=self.org_b),
            1 * 60,
        )

        # Remaining minutes: A = 2 hours (120 mins), B = 4 hours (240 mins)
        self.assertEqual(
            remaining_weekly_minutes(self.teacher, moment, organization=self.org_a),
            2 * 60,
        )
        self.assertEqual(
            remaining_weekly_minutes(self.teacher, moment, organization=self.org_b),
            4 * 60,
        )

        # A 2-hour booking in Academy B is accepted (1h + 2h = 3h <= 5h cap)
        # even though Academy A has already used 8 hours!
        booking_b = Booking(
            student=self.student_b,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=future_monday + timedelta(days=2, hours=10),
            duration_minutes=120,
        )
        booking_b.full_clean()
        booking_b.save()
        self.assertEqual(booking_b.status, BookingStatus.SCHEDULED)

        # A 3-hour booking in Academy A is rejected (8h + 3h = 11h > 10h cap)
        booking_a = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=future_monday + timedelta(days=2, hours=14),
            duration_minutes=180,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking_a.full_clean()
        self.assertIn(
            "teacher_weekly_capacity_exceeded",
            [e.code for e in ctx.exception.error_dict.get("__all__", [])],
        )
