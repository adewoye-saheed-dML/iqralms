"""Phase 3.5 acceptance criterion 1, and Phase 4 criterion 8 — the overlap rule
under real concurrency, through both entry points.

Separate from test_models.py because these need ``TransactionTestCase``, not
``TestCase``. ``TestCase`` wraps each test in one transaction and rolls it back,
which defeats the whole point twice over: the threads below would each get their
own connection and so could not see rows the others had written, and nothing
would ever commit, which is the exact moment the race is won or lost.
``TransactionTestCase`` commits for real and truncates afterwards.

What these prove, and what they cannot: the invariant asserted here (one slot,
one winner, everyone else cleanly refused) is the behaviour the phase is for. A
regression that removed the lock would not fail *every* run of this file, because
two threads have to interleave inside the window between the overlap check and
the INSERT to double-book. Four contenders per slot and a barrier to start them
together is what makes that window likely to be hit rather than merely possible.

Phase 4 adds ``RoutingConcurrencyTests`` at the bottom. The fix landed in 3.5 and
is not re-litigated; what is new is the *entry point* — routing writes bookings
itself, so it needs its own proof that it goes through ``Booking.save()`` and
therefore takes the lock. A ``bulk_create`` of cohort seats would pass every
single-threaded test in this repository and fail only here.
"""

import threading
from datetime import time

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import connection
from django.test import TransactionTestCase

from accounts.tests.factories import StudentFactory
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory
from scheduling.exceptions import NoCapacity
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    TeacherBookingLock,
    Weekday,
    weekly_committed_minutes,
)
from scheduling.routing import route_session

from .factories import (
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    CohortFactory,
    slot_at,
    teaches,
)

#: Enough contenders that the check-then-insert window is likely to be hit if the
#: lock ever stops being taken, without making the test slow.
CONTENDERS = 4

#: A thread that cannot get started, or a booking that cannot get its lock, must
#: fail the test rather than hang the suite.
THREAD_TIMEOUT = 30


class BookingConcurrencyTests(TransactionTestCase):
    """Two or more people going for one teacher's last free slot at once."""

    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        self.level = LevelFactory()
        # Phase 4's specialty rule would otherwise refuse every contender, which
        # would pass "only one booking survives" for entirely the wrong reason.
        teaches(self.teacher, self.level)
        # The one slot everybody wants. Future-dated by slot_at, so the Phase 3.5
        # past-start rule plays no part in what is being measured here.
        self.slot = slot_at(self.window, 120)

    # --- Machinery ----------------------------------------------------------

    def book_concurrently(self, students, start_times=None):
        """Have each student attempt a booking at the same instant.

        Returns a list of ``(outcome, detail)`` in the students' order, where
        outcome is ``"created"``, ``"refused"`` (a ValidationError, the correct
        way to lose) or ``"crashed"`` (anything else — an IntegrityError or a
        SQLite "database is locked" both land here, and both fail the test).
        """
        start_times = start_times or [self.slot] * len(students)
        barrier = threading.Barrier(len(students), timeout=THREAD_TIMEOUT)
        results = [None] * len(students)

        def attempt(index, student, start_time):
            try:
                # Line every thread up so they contend, instead of politely
                # arriving one after another and never racing at all.
                barrier.wait()
                booking = Booking.objects.create(
                    student=student,
                    teacher=self.teacher,
                    level=self.level,
                    start_time_utc=start_time,
                )
                results[index] = ("created", booking.pk)
            except ValidationError as exc:
                results[index] = ("refused", exc)
            except Exception as exc:  # noqa: BLE001 — the point is to report it
                results[index] = ("crashed", exc)
            finally:
                # Django connections are per thread and nothing else will close
                # these; leaving them open wedges the post-test flush.
                connection.close()

        threads = [
            threading.Thread(target=attempt, args=(index, student, start_time))
            for index, (student, start_time) in enumerate(zip(students, start_times))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT)
            self.assertFalse(thread.is_alive(), "a booking thread never finished")

        return results

    def assert_no_crashes(self, results):
        """No contender may lose by exception rather than by validation."""
        crashed = [detail for outcome, detail in results if outcome == "crashed"]
        self.assertEqual(
            crashed,
            [],
            "a losing booking must be refused by the overlap rule, not by a "
            f"database error: {[repr(exc) for exc in crashed]}",
        )

    # --- The criterion ------------------------------------------------------

    def test_only_one_of_two_simultaneous_bookings_for_one_slot_survives(self):
        """Acceptance criterion 1, minimal form: two requests, one slot."""
        results = self.book_concurrently([StudentFactory(), StudentFactory()])

        self.assert_no_crashes(results)
        outcomes = sorted(outcome for outcome, _ in results)
        self.assertEqual(outcomes, ["created", "refused"])
        self.assertEqual(Booking.objects.filter(teacher=self.teacher).count(), 1)

    def test_the_loser_is_refused_by_the_overlap_rule_specifically(self):
        """A clean rejection, not merely *a* failure.

        Asserting the error code is what separates "the lock worked" from "the
        database happened to blow up on the second write", which would also
        leave one booking behind and would otherwise pass the test above.
        """
        results = self.book_concurrently([StudentFactory(), StudentFactory()])

        self.assert_no_crashes(results)
        refusals = [detail for outcome, detail in results if outcome == "refused"]
        self.assertEqual(len(refusals), 1)
        self.assertEqual(
            [error.code for error in refusals[0].error_dict[NON_FIELD_ERRORS]],
            ["teacher_double_booked"],
        )

    def test_one_slot_survives_several_simultaneous_contenders(self):
        """The same invariant with more pressure than two threads."""
        students = [StudentFactory() for _ in range(CONTENDERS)]
        results = self.book_concurrently(students)

        self.assert_no_crashes(results)
        created = [detail for outcome, detail in results if outcome == "created"]
        self.assertEqual(len(created), 1, "exactly one contender may win the slot")
        self.assertEqual(
            Booking.objects.filter(teacher=self.teacher).count(),
            1,
            "the database must hold one booking, whatever the threads believed",
        )

    def test_the_winner_is_a_real_scheduled_booking(self):
        """Whoever wins must end up with an ordinary, complete booking row.

        Serialising the write must not leave the survivor half-built — the room
        name is generated in ``save()`` and validated for uniqueness, so it is
        the field most likely to show damage from a rolled-back sibling.
        """
        results = self.book_concurrently([StudentFactory(), StudentFactory()])
        self.assert_no_crashes(results)

        booking = Booking.objects.get(teacher=self.teacher)
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.start_time_utc, self.slot)
        self.assertTrue(booking.video_room_name)
        winner_pk = next(detail for outcome, detail in results if outcome == "created")
        self.assertEqual(booking.pk, winner_pk)

    # --- The lock's shape ---------------------------------------------------

    def test_two_teachers_are_not_serialised_into_one_queue(self):
        """Different teachers contend for different locks, so both bookings stand.

        This is why the lock is keyed on the teacher rather than being one global
        mutex: two students booking two different teachers at the same instant is
        not a conflict and must not be treated as one.
        """
        other_window = AvailabilityFactory(
            weekday=self.window.weekday,
            start_time_utc=self.window.start_time_utc,
            end_time_utc=self.window.end_time_utc,
        )
        other_teacher = teaches(other_window.teacher, self.level)
        students = [StudentFactory(), StudentFactory()]
        barrier = threading.Barrier(2, timeout=THREAD_TIMEOUT)
        results = [None, None]

        def attempt(index, student, teacher):
            try:
                barrier.wait()
                Booking.objects.create(
                    student=student,
                    teacher=teacher,
                    level=self.level,
                    start_time_utc=self.slot,
                )
                results[index] = ("created", None)
            except Exception as exc:  # noqa: BLE001
                results[index] = ("crashed", exc)
            finally:
                connection.close()

        threads = [
            threading.Thread(target=attempt, args=(0, students[0], self.teacher)),
            threading.Thread(target=attempt, args=(1, students[1], other_teacher)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT)

        self.assert_no_crashes(results)
        self.assertEqual(Booking.objects.filter(teacher=self.teacher).count(), 1)
        self.assertEqual(Booking.objects.filter(teacher=other_teacher).count(), 1)

    def test_a_contended_slot_leaves_one_lock_row_per_teacher(self):
        """The lock table is a mutex, not a log: one row per teacher, reused.

        ``acquire()`` uses ``get_or_create``, so a burst of contenders must not
        leave a pile of duplicate rows behind — the OneToOneField is what
        guarantees that, and this is the test that would notice it being relaxed.
        """
        self.book_concurrently([StudentFactory() for _ in range(CONTENDERS)])

        self.assertEqual(
            TeacherBookingLock.objects.filter(teacher=self.teacher).count(), 1
        )
        lock = TeacherBookingLock.objects.get(teacher=self.teacher)
        # Every contender that got as far as its own transaction bumped it, and
        # a rolled-back loser's bump rolls back with it — so this is only ever
        # "at least the winner", not a reliable contention count.
        self.assertGreaterEqual(lock.revision, 0)


class RoutingConcurrencyTests(TransactionTestCase):
    """Phase 4 acceptance criterion 8 — the 3.5 fix covers routing's path too.

    Routing creates bookings itself, so "the lock is taken" is a claim about
    ``routing.py``, not only about ``Booking.save()``. CLAUDE.md names the exact
    regression this guards: a ``bulk_create`` of cohort seat assignments would
    bypass both the lock and ``clean()``, pass every single-threaded test here, and
    fail only under contention.

    Two distinct races are covered, because routing has two writes:

    * **The 1:1 slot.** Several students route for the same time. Routing may
      legitimately hand them to *different* teachers — that is what it is for — so
      the invariant is per teacher rather than "one winner overall".
    * **The last cohort seat.** Several students route into a class with one seat
      left. Seats deliberately do *not* clash with each other, so the overlap rule
      cannot be what protects the cap here — the seat check inside the routing
      transaction is.
    """

    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        self.slot = slot_at(self.window, 120)

    def route_concurrently(self, students, level):
        """Have every student ask routing for the same slot at the same instant.

        Returns ``(outcome, detail)`` per student: ``"routed"`` with the reason,
        ``"refused"`` for a ``ValidationError`` or ``NoCapacity`` (both correct
        ways to lose), or ``"crashed"`` for anything else — an ``IntegrityError``
        or a SQLite "database is locked" land there, and both fail the test.
        """
        barrier = threading.Barrier(len(students), timeout=THREAD_TIMEOUT)
        results = [None] * len(students)

        def attempt(index, student):
            try:
                barrier.wait()
                routed = route_session(
                    student=student,
                    level=level,
                    start_time_utc=self.slot,
                )
                results[index] = ("routed", routed)
            except (ValidationError, NoCapacity) as exc:
                results[index] = ("refused", exc)
            except Exception as exc:  # noqa: BLE001 — the point is to report it
                results[index] = ("crashed", exc)
            finally:
                # Django connections are per thread and nothing else will close
                # these; leaving them open wedges the post-test flush.
                connection.close()

        threads = [
            threading.Thread(target=attempt, args=(index, student))
            for index, student in enumerate(students)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=THREAD_TIMEOUT)
            self.assertFalse(thread.is_alive(), "a routing thread never finished")
        return results

    def assert_no_crashes(self, results):
        crashed = [detail for outcome, detail in results if outcome == "crashed"]
        self.assertEqual(
            crashed,
            [],
            "a losing routing request must be refused cleanly, not by a database "
            f"error: {[repr(exc) for exc in crashed]}",
        )

    def test_one_teacher_cannot_be_double_booked_by_concurrent_routing(self):
        """Criterion 8 for the 1:1 path.

        The lead is the only eligible teacher, so every thread is routed at the
        same person and the slot can hold exactly one of them. Without the lock the
        contenders all pass the overlap check against a database that still shows
        the slot free, and all commit.
        """
        level = LevelFactory()
        lead = teaches(BookableLeadTeacherFactory(), level)
        Availability.objects.create(
            teacher=lead,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        results = self.route_concurrently(
            [StudentFactory() for _ in range(CONTENDERS)], level
        )

        self.assert_no_crashes(results)
        self.assertEqual(
            Booking.objects.filter(
                teacher=lead, start_time_utc=self.slot, status=BookingStatus.SCHEDULED
            ).count(),
            1,
            "one teacher, one slot, one booking — whatever the threads believed",
        )
        self.assertEqual(
            len([r for outcome, r in results if outcome == "routed"]),
            1,
            "exactly one contender may be told they were routed",
        )

    def test_concurrent_routing_never_exceeds_a_teachers_capacity(self):
        """The routing path must not become a way around the weekly cap either.

        A one-hour lead and four simultaneous 30-minute requests: at most two can
        be accepted. Nothing else is eligible, so any third booking would be the
        check-then-write race the lock exists to close.
        """
        level = LevelFactory()
        lead = teaches(BookableLeadTeacherFactory(), level)
        profile = lead.teacher_profile
        profile.max_weekly_hours = 1
        profile.save()
        Availability.objects.create(
            teacher=lead,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        results = self.route_concurrently(
            [StudentFactory() for _ in range(CONTENDERS)], level
        )

        self.assert_no_crashes(results)
        self.assertLessEqual(
            weekly_committed_minutes(lead.pk, self.slot),
            60,
            "the cap must hold under contention, not only in single-file writes",
        )

    def test_the_last_cohort_seat_goes_to_exactly_one_student(self):
        """Criterion 8 for the cohort path — the race the overlap rule cannot see.

        Seats in one cohort deliberately do not clash with each other, so nothing
        in ``clean()`` limits how many are created. Only the seat check, holding
        the teacher's lock inside routing's transaction, keeps a one-seat class from
        taking four students.
        """
        level = GroupEligibleLevelFactory()
        cohort_teacher = teaches(self.teacher, level)
        cohort = CohortFactory(
            availability=self.window,
            teacher=cohort_teacher,
            level=level,
            max_students=1,
            schedule_start_utc=self.slot,
        )

        results = self.route_concurrently(
            [StudentFactory() for _ in range(CONTENDERS)], level
        )

        self.assert_no_crashes(results)
        cohort.refresh_from_db()
        self.assertEqual(
            cohort.seats_taken, 1, "a one-seat cohort may hold exactly one student"
        )
        self.assertEqual(
            Booking.objects.filter(
                cohort=cohort, status=BookingStatus.SCHEDULED
            ).count(),
            1,
            "and exactly one seat booking, so membership and sessions agree",
        )

    def test_a_routed_booking_takes_the_lock_under_contention(self):
        """The mechanism, not only the outcome.

        A lock row for the routed teacher is proof the write went through
        ``Booking.save()`` — which is precisely what a ``bulk_create`` of seats
        would skip.
        """
        level = LevelFactory()
        lead = teaches(BookableLeadTeacherFactory(), level)
        Availability.objects.create(
            teacher=lead,
            weekday=self.slot.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )

        self.route_concurrently([StudentFactory(), StudentFactory()], level)

        self.assertEqual(
            TeacherBookingLock.objects.filter(teacher=lead).count(),
            1,
            "routing must acquire the per-teacher lock, exactly once per teacher",
        )
