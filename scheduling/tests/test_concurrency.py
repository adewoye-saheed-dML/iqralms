"""Phase 3.5 acceptance criterion 1 — the overlap rule under real concurrency.

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
"""

import threading

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import connection
from django.test import TransactionTestCase

from accounts.tests.factories import StudentFactory
from curriculum.tests.factories import LevelFactory
from scheduling.models import Booking, BookingStatus, TeacherBookingLock, Weekday

from .factories import (
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    slot_at,
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
        other_teacher = other_window.teacher
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
