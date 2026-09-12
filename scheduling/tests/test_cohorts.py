"""Model-layer tests for ``Cohort`` — the group class Phase 4 adds.

Acceptance criteria from specs/phase-4-routing.md covered here: the
``group_eligible`` rule ("enforce this even though the admin UI should already
prevent it; direct ORM/fixture creation shouldn't be able to violate it") and the
``max_students`` cap ("enforce in ``clean()`` or a custom ``add_student()``
method, not just at the serializer layer").

The cap is proved twice over on purpose: through ``add_student()``, which is the
supported path, and through a raw ``students.add()``, which is the path a fixture
or a shell session takes. Only the second one proves the rule cannot be walked
around, which is what the spec asks for.
"""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from accounts.tests.factories import (
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
)
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory
from scheduling.exceptions import CohortFull
from scheduling.models import Booking, BookingStatus, Cohort, DEFAULT_MAX_STUDENTS

from .factories import (
    DEFAULT_WEEKDAY,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    BookableTeacherFactory,
    CohortFactory,
    UnapprovedTeacherFactory,
    slot_at,
    teaches,
)


class CohortModelTests(TestCase):
    def test_cohort_can_be_created_with_spec_fields(self):
        window = AvailabilityFactory()
        level = GroupEligibleLevelFactory()
        teaches(window.teacher, level)
        start = slot_at(window)

        cohort = Cohort.objects.create(
            teacher=window.teacher,
            level=level,
            max_students=4,
            schedule_start_utc=start,
        )
        cohort.refresh_from_db()
        self.assertEqual(cohort.teacher, window.teacher)
        self.assertEqual(cohort.level, level)
        self.assertEqual(cohort.max_students, 4)
        self.assertEqual(cohort.schedule_start_utc, start)
        self.assertEqual(list(cohort.students.all()), [])

    def test_max_students_defaults_to_the_spec_value(self):
        self.assertEqual(CohortFactory().max_students, DEFAULT_MAX_STUDENTS)
        self.assertEqual(DEFAULT_MAX_STUDENTS, 6)

    def test_seat_counts_track_membership(self):
        cohort = CohortFactory(max_students=3)
        self.assertEqual((cohort.seats_taken, cohort.seats_available), (0, 3))
        self.assertTrue(cohort.has_space)

        cohort.add_student(StudentFactory())
        self.assertEqual((cohort.seats_taken, cohort.seats_available), (1, 2))

    def test_seats_available_never_goes_negative(self):
        """A cap lowered under an existing roster reports zero, not a negative."""
        cohort = CohortFactory(max_students=2)
        cohort.add_student(StudentFactory())
        cohort.add_student(StudentFactory())

        cohort.max_students = 1
        cohort.save()

        self.assertEqual(cohort.seats_available, 0)
        self.assertFalse(cohort.has_space)

    def test_cohorts_are_ordered_by_start_time(self):
        window = AvailabilityFactory()
        later = CohortFactory(
            availability=window, schedule_start_utc=slot_at(window, 120)
        )
        earlier = CohortFactory(
            availability=window,
            level=later.level,
            schedule_start_utc=slot_at(window, 30),
        )
        self.assertEqual(list(Cohort.objects.all()), [earlier, later])

    def test_str_names_the_level_teacher_and_occupancy(self):
        cohort = CohortFactory(max_students=6)
        cohort.add_student(StudentFactory())
        rendered = str(cohort)
        self.assertIn(cohort.teacher.username, rendered)
        self.assertIn("1/6", rendered)


class CohortLevelRuleTests(TestCase):
    """The ``group_eligible`` rule, which must hold against direct ORM writes."""

    def test_a_non_group_eligible_level_cannot_run_as_a_cohort(self):
        window = AvailabilityFactory()
        level = LevelFactory(group_eligible=False)
        teaches(window.teacher, level)

        with self.assertRaises(ValidationError) as ctx:
            Cohort.objects.create(
                teacher=window.teacher,
                level=level,
                schedule_start_utc=slot_at(window),
            )
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["level"]],
            ["level_not_group_eligible"],
        )
        self.assertFalse(Cohort.objects.exists())

    def test_a_group_eligible_level_is_accepted(self):
        self.assertTrue(CohortFactory().level.group_eligible)

    def test_the_rule_still_fires_on_a_later_save(self):
        """Flipping a live cohort's level to a non-eligible one is refused too.

        Not creation-only, unlike the booking rules: a cohort has no "already
        happened" state to be frozen out of, so there is no cancellation path to
        protect. The rule is simply always true.
        """
        cohort = CohortFactory()
        other = LevelFactory(group_eligible=False)
        teaches(cohort.teacher, other)

        cohort.level = other
        with self.assertRaises(ValidationError) as ctx:
            cohort.save()
        self.assertIn("level", ctx.exception.message_dict)


class CohortTeacherRuleTests(TestCase):
    def test_an_unapproved_teacher_cannot_run_a_cohort(self):
        teacher = UnapprovedTeacherFactory()
        level = GroupEligibleLevelFactory()
        teaches(teacher, level)

        with self.assertRaises(ValidationError) as ctx:
            Cohort.objects.create(
                teacher=teacher,
                level=level,
                schedule_start_utc=slot_at(AvailabilityFactory()),
            )
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_a_non_teacher_cannot_run_a_cohort(self):
        with self.assertRaises(ValidationError) as ctx:
            Cohort.objects.create(
                teacher=StudentFactory(),
                level=GroupEligibleLevelFactory(),
                schedule_start_utc=slot_at(AvailabilityFactory()),
            )
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_a_teacher_who_does_not_teach_the_track_cannot_run_the_cohort(self):
        """Not in the spec's list, but a cohort like this could seat nobody.

        Every seat booking would be refused by ``Booking.clean()``'s specialty
        rule, so failing at cohort creation is failing where the mistake was.
        """
        window = AvailabilityFactory()
        level = GroupEligibleLevelFactory(
            track__organization=window.organization
        )  # teacher's specialties left empty

        with self.assertRaises(ValidationError) as ctx:
            Cohort.objects.create(
                teacher=window.teacher,
                level=level,
                schedule_start_utc=slot_at(window),
            )
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["teacher"]],
            ["teacher_lacks_specialty"],
        )


class CohortCapacityTests(TestCase):
    """``max_students`` is a real cap, on both the supported and raw paths.

    The raw-``add()`` tests wrap the refusal in ``transaction.atomic()``. Django
    runs an M2M ``add()`` inside ``atomic(savepoint=False)``, so raising from the
    ``m2m_changed`` receiver marks the surrounding transaction as needing
    rollback — and ``TestCase`` *is* a surrounding transaction, so any query after
    the refusal would fail with ``TransactionManagementError`` instead of the
    assertion being tested. The savepoint an inner ``atomic()`` adds is what
    contains the rollback. In production, where the write runs in autocommit,
    ``add()``'s own block is outermost and the refusal leaves the connection
    perfectly usable — verified directly, not assumed. Same class of trap as the
    ``IntegrityError`` note in learnings.md.
    """

    def setUp(self):
        self.cohort = CohortFactory(max_students=2)

    def fill(self):
        for _ in range(self.cohort.max_students):
            self.cohort.add_student(StudentFactory())

    def test_add_student_seats_a_student(self):
        student = StudentFactory()
        self.assertIs(self.cohort.add_student(student), self.cohort)
        self.assertIn(student, self.cohort.students.all())

    def test_add_student_refuses_to_exceed_max_students(self):
        self.fill()
        with self.assertRaises(CohortFull):
            self.cohort.add_student(StudentFactory())
        self.assertEqual(self.cohort.seats_taken, 2)

    def test_a_raw_students_add_cannot_overfill_the_cohort(self):
        """The rule the spec asks for: not just at the serializer layer.

        ``students.add()`` never reaches ``save()`` or ``clean()``, so without the
        ``m2m_changed`` receiver this would silently seat a third student in a
        two-seat class.
        """
        self.fill()
        with transaction.atomic():
            with self.assertRaises(ValidationError) as ctx:
                self.cohort.students.add(StudentFactory())
        self.assertEqual(ctx.exception.code, "cohort_full")
        self.assertEqual(self.cohort.seats_taken, 2)

    def test_a_raw_bulk_add_cannot_overfill_the_cohort_either(self):
        """One ``add()`` with several students is checked as a whole."""
        students = [StudentFactory() for _ in range(3)]
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                self.cohort.students.add(*students)
        self.assertEqual(self.cohort.seats_taken, 0)

    def test_the_reverse_relation_is_capped_too(self):
        """``user.cohort_memberships.add(cohort)`` is the same write, backwards."""
        self.fill()
        student = StudentFactory()
        with transaction.atomic():
            with self.assertRaises(ValidationError):
                student.cohort_memberships.add(self.cohort)
        self.assertEqual(self.cohort.seats_taken, 2)

    def test_reseating_an_existing_member_is_not_a_second_seat(self):
        """An M2M is a set, so a retry must not read as a request for a seat."""
        student = StudentFactory()
        self.cohort.add_student(student)
        self.cohort.add_student(StudentFactory())

        self.cohort.add_student(student)  # full, but this one is already in

        self.assertEqual(self.cohort.seats_taken, 2)

    def test_only_a_student_can_be_seated(self):
        for user in (ParentFactory(), BookableTeacherFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(CohortFull):
                    self.cohort.add_student(user)
        self.assertEqual(self.cohort.seats_taken, 0)

    def test_a_raw_add_of_a_non_student_is_refused_too(self):
        with transaction.atomic():
            with self.assertRaises(ValidationError) as ctx:
                self.cohort.students.add(ParentFactory())
        self.assertEqual(ctx.exception.code, "invalid_student_role")

    def test_a_minor_can_be_seated_without_a_parent_link(self):
        """Membership is not a booking, and it is the *booking* that gates minors.

        ``Booking.clean()`` refuses a session for a minor with no linked parent,
        which is where the Phase 1 rule belongs. A cohort seat is only ever given
        alongside a booking (by routing), so nothing is bypassed here.
        """
        self.cohort.add_student(MinorStudentFactory())
        self.assertEqual(self.cohort.seats_taken, 1)

    def test_removing_a_student_frees_their_seat(self):
        self.fill()
        self.cohort.students.remove(self.cohort.students.first())
        self.assertTrue(self.cohort.has_space)
        self.cohort.add_student(StudentFactory())  # must not raise


class OpenCohortQueryTests(TestCase):
    """``open_for_level`` and ``open_near`` — what routing's step 1 reads."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.cohort = CohortFactory(availability=self.window, max_students=2)
        self.level = self.cohort.level

    def test_a_cohort_with_seats_is_open(self):
        self.assertEqual(list(Cohort.open_for_level(self.level)), [self.cohort])

    def test_a_full_cohort_is_not_open(self):
        self.cohort.add_student(StudentFactory())
        self.cohort.add_student(StudentFactory())
        self.assertEqual(list(Cohort.open_for_level(self.level)), [])

    def test_a_partly_filled_cohort_is_still_open(self):
        self.cohort.add_student(StudentFactory())
        self.assertEqual(list(Cohort.open_for_level(self.level)), [self.cohort])

    def test_another_levels_cohort_is_not_returned(self):
        CohortFactory()  # its own level entirely
        self.assertEqual(list(Cohort.open_for_level(self.level)), [self.cohort])

    def test_open_for_level_accepts_a_level_id(self):
        """The list endpoint passes an id, routing passes an instance."""
        self.assertEqual(list(Cohort.open_for_level(self.level.pk)), [self.cohort])

    def test_open_near_finds_a_cohort_starting_at_the_requested_time(self):
        found = Cohort.open_near(self.level, self.cohort.schedule_start_utc)
        self.assertEqual(found, [self.cohort])

    def test_open_near_tolerates_a_nearby_start(self):
        """"Reasonably close to the requested window" is a real tolerance."""
        asked = self.cohort.schedule_start_utc + timedelta(minutes=45)
        self.assertEqual(Cohort.open_near(self.level, asked), [self.cohort])

    def test_open_near_excludes_a_cohort_starting_far_away(self):
        asked = self.cohort.schedule_start_utc + timedelta(hours=6)
        self.assertEqual(Cohort.open_near(self.level, asked), [])

    def test_open_near_returns_the_closest_start_first(self):
        near = CohortFactory(
            availability=self.window,
            level=self.level,
            schedule_start_utc=self.cohort.schedule_start_utc + timedelta(minutes=90),
        )
        asked = self.cohort.schedule_start_utc + timedelta(minutes=80)
        self.assertEqual(Cohort.open_near(self.level, asked), [near, self.cohort])

    def test_open_near_respects_an_explicit_tolerance(self):
        asked = self.cohort.schedule_start_utc + timedelta(hours=6)
        self.assertEqual(
            Cohort.open_near(self.level, asked, tolerance=timedelta(hours=7)),
            [self.cohort],
        )


class CohortSeatBookingTests(TestCase):
    """A seat is an ordinary ``Booking`` that happens to point at a cohort."""

    def setUp(self):
        self.cohort = CohortFactory()

    def seat(self, student=None, **overrides):
        from curriculum.tests.factories import admit

        student = student or StudentFactory()
        if hasattr(self.cohort, "organization") and self.cohort.organization is not None:
            admit(student, self.cohort.organization)
        fields = {
            "student": student,
            "teacher": self.cohort.teacher,
            "level": self.cohort.level,
            "cohort": self.cohort,
            "start_time_utc": self.cohort.schedule_start_utc,
        }
        fields.update(overrides)
        return Booking.objects.create(**fields)

    def test_several_students_can_hold_seats_at_the_same_instant(self):
        """The overlap relaxation that makes cohorts possible at all.

        Six students in one class is one teacher teaching once. Were the
        per-teacher overlap rule applied naively, the second student in every
        cohort would be unbookable.
        """
        seats = [self.seat() for _ in range(3)]
        self.assertEqual(len({s.pk for s in seats}), 3)
        self.assertEqual(
            len({s.start_time_utc for s in seats}),
            1,
            "all three seats are the same session",
        )

    def test_each_seat_still_gets_its_own_video_room(self):
        first, second = self.seat(), self.seat()
        self.assertNotEqual(first.video_provider_meeting_id, second.video_provider_meeting_id)

    def test_a_one_to_one_session_still_clashes_with_a_cohort_seat(self):
        """The relaxation is same-cohort only, not "cohorts don't clash"."""
        self.seat()
        with self.assertRaises(ValidationError) as ctx:
            self.seat(cohort=None)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["__all__"]],
            ["teacher_double_booked"],
        )

    def test_two_different_cohorts_at_one_time_still_clash(self):
        """One teacher cannot run two group classes at once."""
        other = CohortFactory(
            availability=AvailabilityFactory(teacher=self.cohort.teacher),
            schedule_start_utc=self.cohort.schedule_start_utc,
        )
        self.seat()
        with self.assertRaises(ValidationError) as ctx:
            self.seat(cohort=other, level=other.level)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["__all__"]],
            ["teacher_double_booked"],
        )

    def test_a_seat_must_match_its_cohorts_teacher(self):
        """A seat naming a different teacher is refused as a mismatch.

        Not merely cosmetic: the same-cohort exclusion in
        ``clashing_bookings()`` is keyed on ``cohort_id``, so a row that carried a
        cohort while naming somebody else's teacher would opt itself out of the
        overlap rule for a session it is not actually part of.
        """
        other = BookableTeacherFactory()
        AvailabilityFactory(
            teacher=other,
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        teaches(other, self.cohort.level)
        with self.assertRaises(ValidationError) as ctx:
            self.seat(teacher=other)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["cohort"]], ["cohort_mismatch"]
        )

    def test_a_seat_must_match_its_cohorts_start_time(self):
        with self.assertRaises(ValidationError) as ctx:
            self.seat(
                start_time_utc=self.cohort.schedule_start_utc + timedelta(minutes=30)
            )
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["cohort"]], ["cohort_mismatch"]
        )

    def test_a_seat_must_match_its_cohorts_level(self):
        other = GroupEligibleLevelFactory()
        teaches(self.cohort.teacher, other)
        with self.assertRaises(ValidationError) as ctx:
            self.seat(level=other)
        self.assertEqual(
            [e.code for e in ctx.exception.error_dict["cohort"]], ["cohort_mismatch"]
        )

    def test_a_minor_still_needs_a_linked_parent_for_a_seat(self):
        """The Phase 1 gate applies to a group session exactly as to a 1:1 one."""
        with self.assertRaises(ValidationError) as ctx:
            self.seat(student=MinorStudentFactory())
        self.assertIn("student", ctx.exception.message_dict)

        linked = ParentLinkFactory()
        self.assertTrue(self.seat(student=linked.student).pk)

    def test_a_seat_can_be_cancelled_like_any_other_booking(self):
        seat = self.seat()
        seat.cancel()
        seat.refresh_from_db()
        self.assertEqual(seat.status, BookingStatus.CANCELLED)
