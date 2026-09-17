"""Model-layer tests for the scheduling app.

Acceptance criteria from specs/phase-3-scheduling.md covered at the model layer:
1 (no model change is missing a migration), 2 (inside a declared window books,
outside it is rejected), 3 (an overlapping booking for the same teacher is
rejected, a neighbouring one is not), 4 (an unapproved teacher is rejected),
5 (``video_provider_meeting_id`` exists and is unique) and 6 (cancelling sets the status
and keeps the row).

Criteria 2-6 are covered again through HTTP in test_api.py. That is not
duplication for its own sake: the rules live in ``clean()``, called from
``save()``, precisely so they hold for the admin and direct ORM writes too, so
each layer needs its own proof.
"""

from datetime import datetime, time, timedelta

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.models import Role
from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.tests.factories import (
    GroupEligibleLevelFactory,
    LevelFactory,
    TrackFactory,
    admit,
)
from organizations.models import MembershipStatus
from organizations.tests.factories import OrganizationFactory
from scheduling.exceptions import BookingNotCancellable
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    Cohort,
    DEFAULT_DURATION_MINUTES,
    PAST_BOOKING_GRACE,
    TeacherWaitlist,
    Weekday,
)
from scheduling.utils import UTC, next_date_for_weekday

from .factories import (
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    BookingFactory,
    CancelledBookingFactory,
    CohortFactory,
    CompletedBookingFactory,
    UnapprovedTeacherFactory,
    WaitlistEntryFactory,
    ensure_teacher_configured,
    slot_at,
    teaches,
)


def error_codes(exc, field=NON_FIELD_ERRORS):
    """The ``code`` of every error ``exc`` raised against ``field``.

    Asserting on codes rather than on "a ValidationError happened" is what keeps
    a test honest: several rules guard a booking, and an attempt rejected by the
    wrong one would otherwise pass silently.
    """
    return [error.code for error in exc.error_dict.get(field, [])]


def age_booking(booking, age):
    """Move an existing booking's start ``age`` into the past, bypassing ``save()``.

    The criterion-4 tests need a booking that already exists and whose start time
    has since passed — the state every session reaches once it has been taught.
    Building it can't go through ``save()``: the past-start rule would reject the
    very state we are trying to set up. A direct ``UPDATE`` via ``QuerySet.update``
    writes the column without running model validation, which is exactly the
    "already stored, now past" starting point these tests describe. Returns the
    same instance, refreshed from the row.
    """
    Booking.objects.filter(pk=booking.pk).update(
        start_time_utc=dj_timezone.now() - age
    )
    booking.refresh_from_db()
    return booking


def unapprove(teacher, organization=None):
    """Withdraw approval from a teacher who already has availability.

    Criterion 4 needs a teacher who *has* declared hours but is not approved,
    and that state cannot be built forwards: ``Availability`` refuses to save
    against an unapproved teacher. Building it approved and then flipping the
    flag is the same order it happens in real life, when a lead revokes someone.
    """
    profile = getattr(teacher, "teacher_profile", None)
    if profile is not None:
        profile.approved = False
        profile.save()
    from accounts.models import OrganizationTeacherConfiguration

    memberships = teacher.organization_memberships.all()
    if organization is not None:
        memberships = memberships.filter(organization=organization)
    for m in memberships:
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m,
            defaults={"approved": False},
        )
    teacher.refresh_from_db()
    return teacher


class AvailabilityModelTests(TestCase):
    def test_window_can_be_created_with_spec_fields(self):
        from curriculum.tests.factories import admit
        from organizations.tests.factories import OrganizationFactory

        organization = OrganizationFactory()
        teacher = BookableTeacherFactory()
        ensure_teacher_configured(teacher, organization)
        window = Availability.objects.create(
            organization=organization,
            teacher=teacher,
            weekday=Weekday.WEDNESDAY,
            start_time_utc=time(14, 0),
            end_time_utc=time(16, 30),
        )
        window.refresh_from_db()
        self.assertEqual(window.organization, organization)
        self.assertEqual(window.teacher, teacher)
        self.assertEqual(window.weekday, 2)
        self.assertEqual(window.start_time_utc, time(14, 0))
        self.assertEqual(window.end_time_utc, time(16, 30))
        self.assertEqual(
            str(window), f"{teacher.username} Wednesday 14:00-16:30 UTC"
        )

    def test_a_teacher_may_hold_several_windows(self):
        """Multiple windows in a day, and windows on different days."""
        teacher = BookableTeacherFactory()
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(10, 0),
        )
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(15, 0),
            end_time_utc=time(18, 0),
        )
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.SATURDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(12, 0),
        )
        self.assertEqual(teacher.availability_windows.count(), 3)

    def test_windows_are_ordered_by_teacher_then_day_then_start(self):
        teacher = BookableTeacherFactory()
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.FRIDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(10, 0),
        )
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(16, 0),
            end_time_utc=time(17, 0),
        )
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(7, 0),
            end_time_utc=time(8, 0),
        )
        self.assertEqual(
            list(
                Availability.objects.filter(teacher=teacher).values_list(
                    "weekday", "start_time_utc"
                )
            ),
            [(0, time(7, 0)), (0, time(16, 0)), (4, time(9, 0))],
        )

    def test_a_window_must_end_after_it_starts(self):
        with self.assertRaises(ValidationError) as ctx:
            AvailabilityFactory(start_time_utc=time(17, 0), end_time_utc=time(9, 0))
        self.assertIn("end_time_utc", ctx.exception.message_dict)

    def test_a_zero_length_window_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            AvailabilityFactory(start_time_utc=time(9, 0), end_time_utc=time(9, 0))
        self.assertIn("end_time_utc", ctx.exception.message_dict)

    def test_a_non_teacher_cannot_hold_availability(self):
        for user in (StudentFactory(), ParentFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(ValidationError) as ctx:
                    AvailabilityFactory(teacher=user)
                self.assertIn("teacher", ctx.exception.message_dict)

    def test_a_teacher_with_no_profile_cannot_hold_availability(self):
        with self.assertRaises(ValidationError) as ctx:
            AvailabilityFactory(teacher=SubTeacherFactory())
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_an_unapproved_teacher_cannot_hold_availability(self):
        with self.assertRaises(ValidationError) as ctx:
            AvailabilityFactory(teacher=UnapprovedTeacherFactory())
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_the_lead_teacher_can_hold_availability(self):
        """The lead teaches too — bookable in their own right."""
        window = AvailabilityFactory(teacher=BookableLeadTeacherFactory())
        self.assertEqual(window.teacher.role, Role.LEAD)

    def test_covers_is_inclusive_of_the_start_and_exclusive_of_the_end(self):
        window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(10, 0),
        )
        self.assertTrue(window.covers(0, time(9, 0), time(9, 30)))
        self.assertTrue(window.covers(0, time(9, 30), time(10, 0)))
        self.assertFalse(window.covers(0, time(8, 45), time(9, 15)))
        self.assertFalse(window.covers(0, time(9, 45), time(10, 15)))
        # Right times, wrong day.
        self.assertFalse(window.covers(1, time(9, 0), time(9, 30)))


class AvailabilityLocalConversionTests(TestCase):
    """CLAUDE.md: local time is converted at the point of entry, never stored."""

    def test_a_local_window_is_stored_as_utc(self):
        teacher = BookableTeacherFactory(timezone="Africa/Lagos")  # UTC+1, no DST
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        windows = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_local=time(9, 0),
            end_local=time(17, 0),
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].weekday, Weekday.MONDAY)
        self.assertEqual(windows[0].start_time_utc, time(8, 0))
        self.assertEqual(windows[0].end_time_utc, time(16, 0))

    def test_the_teachers_own_zone_is_the_default(self):
        teacher = BookableTeacherFactory(timezone="Asia/Karachi")  # UTC+5
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        window = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.TUESDAY,
            start_local=time(20, 0),
            end_local=time(22, 0),
        )[0]
        self.assertEqual(window.start_time_utc, time(15, 0))
        self.assertEqual(window.end_time_utc, time(17, 0))

    def test_a_window_crossing_utc_midnight_is_stored_as_two_rows(self):
        """A Lagos small-hours window lands on two different UTC days."""
        teacher = BookableTeacherFactory(timezone="Africa/Lagos")
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        windows = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_local=time(0, 30),
            end_local=time(2, 30),
        )
        self.assertEqual(
            [(w.weekday, w.start_time_utc, w.end_time_utc) for w in windows],
            [
                (Weekday.SUNDAY, time(23, 30), time.max),
                (Weekday.MONDAY, time(0, 0), time(1, 30)),
            ],
        )

    def test_conversion_can_shift_the_weekday_without_splitting(self):
        """A New Zealand morning is the previous UTC day, in one piece."""
        teacher = BookableTeacherFactory(timezone="Pacific/Auckland")  # UTC+12/+13
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        windows = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.MONDAY,
            start_local=time(9, 0),
            end_local=time(11, 0),
            # Pinned: Auckland's offset is +12 in July, +13 in January, and the
            # weekday shift is what this asserts, not the exact hour.
            on_or_after=datetime(2026, 7, 1).date(),
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].weekday, Weekday.SUNDAY)
        self.assertEqual(windows[0].start_time_utc, time(21, 0))

    def test_local_window_renders_a_stored_window_back_in_a_zone(self):
        window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(16, 0),
        )
        self.assertEqual(
            window.local_window("Africa/Lagos"), (Weekday.MONDAY, time(9, 0), time(17, 0))
        )

    def test_a_stored_window_round_trips_through_the_teachers_zone(self):
        teacher = BookableTeacherFactory(timezone="America/New_York")
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        window = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.THURSDAY,
            start_local=time(14, 0),
            end_local=time(16, 0),
            on_or_after=datetime(2026, 7, 1).date(),
        )[0]
        self.assertEqual(
            window.local_window("America/New_York"),
            (Weekday.THURSDAY, time(14, 0), time(16, 0)),
        )

    def test_a_window_ending_at_utc_midnight_is_stored_as_end_of_day(self):
        """``END_OF_DAY`` is a sentinel, so this end does *not* round-trip.

        A window ending exactly at 00:00 UTC is stored as ``time.max`` rather
        than 00:00, because ``end_time_utc > start_time_utc`` has to hold. It
        reads back as 19:59:59.999999 local, not 20:00. Harmless, because the
        same sentinel is applied to a booking's segments — the next test proves
        a session ending at UTC midnight still books.
        """
        teacher = BookableTeacherFactory(timezone="America/New_York")
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        windows = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.THURSDAY,
            start_local=time(18, 0),
            end_local=time(20, 0),  # 22:00-00:00 UTC in EDT
            on_or_after=datetime(2026, 7, 1).date(),
        )
        self.assertEqual(len(windows), 1)
        self.assertEqual(
            (windows[0].weekday, windows[0].start_time_utc, windows[0].end_time_utc),
            (Weekday.THURSDAY, time(22, 0), time.max),
        )
        self.assertEqual(
            windows[0].local_window("America/New_York"),
            (Weekday.THURSDAY, time(18, 0), time(19, 59, 59, 999999)),
        )

    def test_a_session_ending_at_utc_midnight_still_books(self):
        """The end-of-day sentinel is applied on both sides, so it cancels out."""
        teacher = BookableTeacherFactory(timezone="America/New_York")
        org = OrganizationFactory()
        ensure_teacher_configured(teacher, org)
        window = Availability.create_from_local(
            organization=org,
            teacher=teacher,
            weekday=Weekday.THURSDAY,
            start_local=time(18, 0),
            end_local=time(20, 0),
            on_or_after=datetime(2026, 7, 1).date(),
        )[0]

        booking = BookingFactory(
            availability=window, start_time_utc=slot_at(window, 90)
        )
        self.assertEqual(booking.start_time_utc.time(), time(23, 30))
        self.assertEqual(booking.end_time_utc.time(), time(0, 0))


class BookingModelTests(TestCase):
    def test_booking_can_be_created_with_spec_defaults(self):
        window = AvailabilityFactory()
        student = StudentFactory()
        admit(student, window.organization)
        level = LevelFactory(track__organization=window.organization)
        teaches(window.teacher, level)
        start = slot_at(window)

        booking = Booking.objects.create(
            student=student,
            teacher=window.teacher,
            level=level,
            start_time_utc=start,
        )
        booking.refresh_from_db()
        self.assertEqual(booking.student, student)
        self.assertEqual(booking.teacher, window.teacher)
        self.assertEqual(booking.level, level)
        self.assertEqual(booking.start_time_utc, start)
        self.assertEqual(booking.duration_minutes, 30)
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)

    def test_end_time_is_derived_from_the_duration(self):
        booking = BookingFactory(duration_minutes=45)
        self.assertEqual(
            booking.end_time_utc, booking.start_time_utc + timedelta(minutes=45)
        )

    def test_a_zero_length_session_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            BookingFactory(duration_minutes=0)
        self.assertIn("duration_minutes", ctx.exception.message_dict)

    def test_str_names_both_parties_and_the_status(self):
        booking = BookingFactory()
        rendered = str(booking)
        self.assertIn(booking.student.username, rendered)
        self.assertIn(booking.teacher.username, rendered)
        self.assertIn("scheduled", rendered)

    def test_utc_segments_is_one_segment_for_a_normal_session(self):
        booking = BookingFactory()
        self.assertEqual(len(booking.utc_segments()), 1)


class VideoRoomTests(TestCase):
    """Acceptance criterion 5, plus the immutability the spec asks for."""


    def test_two_bookings_get_present_and_distinct_room_names(self):
        """Acceptance criterion 5."""
        window = AvailabilityFactory()
        first = BookingFactory(availability=window)
        second = BookingFactory(
            availability=window, start_time_utc=slot_at(window, 60)
        )
        self.assertTrue(first.video_provider_meeting_id)
        self.assertTrue(second.video_provider_meeting_id)
        self.assertNotEqual(first.video_provider_meeting_id, second.video_provider_meeting_id)

    def test_the_join_url_is_built_from_the_room_name(self):
        booking = BookingFactory()
        with self.settings(JITSI_DOMAIN="meet.jit.si"):
            self.assertEqual(
                booking.video_join_url,
                f"https://meet.jit.si/{booking.video_provider_meeting_id}",
            )

    def test_the_room_name_survives_an_unrelated_update(self):
        booking = BookingFactory()
        original = booking.video_provider_meeting_id
        booking.status = BookingStatus.COMPLETED
        booking.save()
        booking.refresh_from_db()
        self.assertEqual(booking.video_provider_meeting_id, original)

    def test_the_room_name_cannot_be_changed(self):
        """Cancel and recreate is the only reschedule path this phase."""
        booking = BookingFactory()
        booking.video_provider_meeting_id = "changed"
        with self.assertRaises(ValidationError) as ctx:
            booking.save()
        self.assertIn("video_provider_meeting_id", ctx.exception.message_dict)


class BookingAvailabilityRulesTests(TestCase):
    """Acceptance criteria 2 and 4 — declared hours, and an approved teacher."""

    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher

    def book(self, **kwargs):
        return BookingFactory(availability=self.window, **kwargs)

    def test_a_booking_inside_the_window_succeeds(self):
        """Acceptance criterion 2, first half."""
        booking = self.book(start_time_utc=slot_at(self.window, 120))
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.start_time_utc.time(), time(11, 0))

    def test_a_booking_at_the_very_start_of_the_window_succeeds(self):
        booking = self.book(start_time_utc=slot_at(self.window))
        self.assertEqual(booking.start_time_utc.time(), DEFAULT_WINDOW_START)

    def test_a_booking_ending_exactly_at_the_window_end_succeeds(self):
        booking = self.book(
            start_time_utc=slot_at(self.window, 8 * 60 - 30), duration_minutes=30
        )
        self.assertEqual(booking.end_time_utc.time(), DEFAULT_WINDOW_END)

    def test_a_booking_before_the_window_is_rejected(self):
        """Acceptance criterion 2, second half."""
        with self.assertRaises(ValidationError) as ctx:
            self.book(start_time_utc=slot_at(self.window, -60))
        self.assertEqual(error_codes(ctx.exception), ["outside_availability"])

    def test_a_booking_that_overruns_the_window_end_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            self.book(
                start_time_utc=slot_at(self.window, 8 * 60 - 15), duration_minutes=30
            )
        self.assertEqual(error_codes(ctx.exception), ["outside_availability"])

    def test_a_booking_on_the_wrong_weekday_is_rejected(self):
        """Right time of day, wrong day of week."""
        tuesday = slot_at(self.window) + timedelta(days=1)
        with self.assertRaises(ValidationError) as ctx:
            self.book(start_time_utc=tuesday)
        self.assertEqual(error_codes(ctx.exception), ["outside_availability"])

    def test_a_booking_against_a_teacher_with_no_availability_is_rejected(self):
        other = BookableTeacherFactory()
        level = LevelFactory(track__organization=self.window.organization)
        teaches(other, level)
        with self.assertRaises(ValidationError) as ctx:
            Booking.objects.create(
                student=StudentFactory(),
                teacher=other,
                level=level,
                start_time_utc=slot_at(self.window),
            )
        self.assertEqual(error_codes(ctx.exception), ["outside_availability"])

    def test_one_teachers_window_does_not_make_another_teacher_bookable(self):
        """The window lookup is scoped to the booking's own teacher."""
        other = BookableTeacherFactory()
        AvailabilityFactory(
            teacher=other,
            organization=self.window.organization,
            weekday=Weekday.MONDAY,
            start_time_utc=time(3, 0),
            end_time_utc=time(4, 0),
        )
        level = LevelFactory(track__organization=self.window.organization)
        teaches(self.teacher, level)
        with self.assertRaises(ValidationError) as ctx:
            Booking.objects.create(
                student=StudentFactory(),
                teacher=self.teacher,
                level=level,
                start_time_utc=slot_at(self.window, -6 * 60),  # 03:00, other's window
            )
        self.assertEqual(error_codes(ctx.exception), ["outside_availability"])

    def test_a_second_window_on_another_day_is_also_bookable(self):
        friday = AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.FRIDAY,
            start_time_utc=time(6, 0),
            end_time_utc=time(8, 0),
        )
        booking = BookingFactory(availability=friday)
        self.assertEqual(booking.start_time_utc.time(), time(6, 0))

    def test_a_booking_against_an_unapproved_teacher_is_rejected(self):
        """Acceptance criterion 4."""
        unapprove(self.teacher)
        with self.assertRaises(ValidationError) as ctx:
            self.book()
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_a_booking_against_a_teacher_with_no_profile_is_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            Booking.objects.create(
                student=StudentFactory(),
                teacher=SubTeacherFactory(),
                level=LevelFactory(),
                start_time_utc=slot_at(self.window),
            )
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_a_non_teacher_cannot_be_the_teacher_of_a_booking(self):
        with self.assertRaises(ValidationError) as ctx:
            Booking.objects.create(
                student=StudentFactory(),
                teacher=ParentFactory(),
                level=LevelFactory(),
                start_time_utc=slot_at(self.window),
            )
        self.assertIn("teacher", ctx.exception.message_dict)

    def test_only_a_student_can_be_booked_into_a_session(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(ValidationError) as ctx:
                    self.book(student=user)
                self.assertIn("student", ctx.exception.message_dict)

    def test_a_minor_with_no_linked_parent_cannot_be_booked(self):
        """The Phase 1 ``is_fully_active`` gate, used for the first time here."""
        with self.assertRaises(ValidationError) as ctx:
            self.book(student=MinorStudentFactory())
        self.assertIn("student", ctx.exception.message_dict)

    def test_a_minor_with_a_linked_parent_can_be_booked(self):
        link = ParentLinkFactory()
        booking = self.book(student=link.student)
        self.assertEqual(booking.student, link.student)

    def test_a_session_cannot_run_across_utc_midnight(self):
        """No single availability window spans midnight, so no booking may."""
        late = AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.TUESDAY,
            start_time_utc=time(23, 0),
            end_time_utc=time.max,
        )
        start = slot_at(late, 45)  # 23:45 for 30 minutes
        with self.assertRaises(ValidationError) as ctx:
            BookingFactory(availability=late, start_time_utc=start)
        self.assertEqual(error_codes(ctx.exception), ["crosses_utc_midnight"])


class BookingOverlapTests(TestCase):
    """Acceptance criterion 3 — one teacher, one session at a time.

    The reference booking sits in the *middle* of the declared window so an
    attempt can approach it from either side and still be inside declared hours:
    an attempt that lands outside the window would be rejected by the wrong
    rule, and would pass this class's assertions for the wrong reason. Every
    rejection here is checked by error code for the same reason.
    """

    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        # 11:00-11:30 UTC on the window's next Monday, two hours into 09:00-17:00.
        self.existing = self.book(120)

    def book(self, offset_minutes, **kwargs):
        return BookingFactory(
            availability=self.window,
            start_time_utc=slot_at(self.window, offset_minutes),
            **kwargs,
        )

    def assert_rejected_as_double_booking(self, offset_minutes, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            self.book(offset_minutes, **kwargs)
        self.assertEqual(
            error_codes(ctx.exception), ["teacher_double_booked"]
        )

    def test_an_overlapping_booking_for_the_same_teacher_is_rejected(self):
        """Acceptance criterion 3, first half."""
        self.assert_rejected_as_double_booking(135)  # 11:15-11:45
        self.assertEqual(Booking.objects.filter(teacher=self.teacher).count(), 1)

    def test_an_identical_slot_is_rejected(self):
        self.assert_rejected_as_double_booking(120)

    def test_a_booking_starting_before_and_ending_inside_is_rejected(self):
        self.assert_rejected_as_double_booking(105, duration_minutes=30)

    def test_a_booking_swallowing_the_existing_one_is_rejected(self):
        self.assert_rejected_as_double_booking(90, duration_minutes=120)

    def test_a_booking_starting_inside_and_ending_after_is_rejected(self):
        self.assert_rejected_as_double_booking(145, duration_minutes=30)

    def test_a_non_overlapping_booking_for_the_same_teacher_succeeds(self):
        """Acceptance criterion 3, second half."""
        later = self.book(240)
        self.assertEqual(later.status, BookingStatus.SCHEDULED)
        self.assertEqual(Booking.objects.filter(teacher=self.teacher).count(), 2)

    def test_a_session_starting_exactly_when_another_ends_succeeds(self):
        """Ranges are half-open: back-to-back sessions are not a clash."""
        back_to_back = self.book(150)
        self.assertEqual(back_to_back.start_time_utc, self.existing.end_time_utc)

    def test_a_session_ending_exactly_when_another_starts_succeeds(self):
        """The other side of the half-open range."""
        before = self.book(90, duration_minutes=30)  # 10:30-11:00
        self.assertEqual(before.end_time_utc, self.existing.start_time_utc)

    def test_the_same_slot_next_week_is_not_a_clash(self):
        next_week = BookingFactory(
            availability=self.window,
            start_time_utc=self.existing.start_time_utc + timedelta(days=7),
        )
        self.assertEqual(next_week.status, BookingStatus.SCHEDULED)

    def test_two_teachers_can_be_booked_at_the_same_time(self):
        """The rule is per teacher, not global."""
        other_window = AvailabilityFactory(
            weekday=self.window.weekday,
            start_time_utc=self.window.start_time_utc,
            end_time_utc=self.window.end_time_utc,
        )
        concurrent = BookingFactory(
            availability=other_window, start_time_utc=self.existing.start_time_utc
        )
        self.assertNotEqual(concurrent.teacher, self.teacher)

    def test_a_student_may_double_book_themselves_across_two_teachers(self):
        """Spec: explicitly not this phase's problem — asserted so a later phase
        that decides otherwise has to change a test on purpose (learnings.md)."""
        other_window = AvailabilityFactory(
            weekday=self.window.weekday,
            start_time_utc=self.window.start_time_utc,
            end_time_utc=self.window.end_time_utc,
        )
        clash = BookingFactory(
            availability=other_window,
            student=self.existing.student,
            start_time_utc=self.existing.start_time_utc,
        )
        self.assertEqual(clash.student, self.existing.student)

    def test_a_cancelled_booking_frees_its_slot(self):
        """This is what makes cancel-and-rebook the supported reschedule path."""
        self.existing.cancel()
        replacement = self.book(120)
        self.assertEqual(replacement.start_time_utc, self.existing.start_time_utc)

    def test_a_completed_booking_does_not_block_its_slot(self):
        """Spec wording: the rule is between *scheduled* bookings only.

        A completed session is in the past, so in practice the slot it occupied
        can only be re-booked by booking in the past — which nothing in this
        phase forbids. Recorded in tech-debt.md rather than fixed here, because
        "no bookings in the past" is a rule the spec doesn't ask for.
        """
        completed = CompletedBookingFactory(
            availability=self.window, start_time_utc=slot_at(self.window, 240)
        )
        overlapping = self.book(240)
        self.assertEqual(overlapping.start_time_utc, completed.start_time_utc)

    def test_a_no_show_does_not_block_its_slot_either(self):
        self.existing.status = BookingStatus.NO_SHOW
        self.existing.save()
        replacement = self.book(120)
        self.assertEqual(replacement.start_time_utc, self.existing.start_time_utc)

    def test_a_booking_can_be_resaved_without_clashing_with_itself(self):
        self.existing.status = BookingStatus.NO_SHOW
        self.existing.save()
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.status, BookingStatus.NO_SHOW)

    def test_clashing_bookings_reports_the_conflicting_row(self):
        candidate = Booking(
            student=StudentFactory(),
            teacher=self.teacher,
            level=self.existing.level,
            start_time_utc=slot_at(self.window, 135),
        )
        self.assertEqual(
            [other.pk for other in candidate.clashing_bookings()], [self.existing.pk]
        )


class BookingCancelTests(TestCase):
    """Acceptance criterion 6 — cancelling keeps the row."""

    def test_cancelling_sets_the_status_and_keeps_the_row(self):
        booking = BookingFactory()
        booking.cancel()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())
        self.assertEqual(Booking.objects.count(), 1)

    def test_cancelling_twice_is_refused_rather_than_a_silent_no_op(self):
        booking = CancelledBookingFactory()
        with self.assertRaises(BookingNotCancellable):
            booking.cancel()

    def test_a_completed_session_cannot_be_cancelled(self):
        booking = CompletedBookingFactory()
        with self.assertRaises(BookingNotCancellable):
            booking.cancel()
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.COMPLETED)

    def test_a_booking_stays_cancellable_after_the_teacher_edits_their_hours(self):
        """The reason the availability check is creation-only.

        If it ran on every save, a teacher narrowing their hours would leave an
        existing booking unsaveable — and therefore uncancellable, since
        ``cancel()`` goes through ``save()``.
        """
        window = AvailabilityFactory()
        booking = BookingFactory(availability=window)

        window.start_time_utc = time(14, 0)
        window.end_time_utc = time(16, 0)
        window.save()

        booking.cancel()
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_a_booking_stays_cancellable_after_the_teacher_loses_approval(self):
        """Same reasoning as the hours edit, one table over."""
        booking = BookingFactory()
        unapprove(booking.teacher)

        booking.refresh_from_db()
        with self.assertRaises(ValidationError):
            booking.cancel()
        # Documented consequence rather than desired behaviour: the teacher gate
        # is not creation-only, so revoking approval freezes live bookings. See
        # learnings.md — the lead cancels first, then unapproves.
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)


class WeeklyRuleHelperTests(TestCase):
    """The weekday-plus-time helpers the whole app's UTC handling rests on."""

    def test_next_date_for_weekday_lands_on_that_weekday(self):
        reference = datetime(2026, 8, 23).date()  # a Sunday
        for weekday in range(7):
            with self.subTest(weekday=weekday):
                found = next_date_for_weekday(weekday, reference)
                self.assertEqual(found.weekday(), weekday)
                self.assertGreaterEqual(found, reference)
                self.assertLess((found - reference).days, 7)

    def test_next_date_for_weekday_returns_today_when_it_matches(self):
        sunday = datetime(2026, 8, 23).date()
        self.assertEqual(next_date_for_weekday(6, sunday), sunday)

    def test_slot_at_lands_inside_the_window_it_came_from(self):
        window = AvailabilityFactory(
            weekday=Weekday.THURSDAY,
            start_time_utc=time(10, 0),
            end_time_utc=time(12, 0),
        )
        start = slot_at(window, 30)
        self.assertEqual(start.weekday(), Weekday.THURSDAY)
        self.assertEqual(start.time(), time(10, 30))
        self.assertEqual(start.tzinfo, UTC)
        self.assertTrue(window.covers(start.weekday(), start.time(), time(11, 0)))


class PastBookingRuleTests(TestCase):
    """Phase 3.5 criteria 2-4 — a *new* booking may not start in the past.

    Every booking here is built from ``dj_timezone.now()`` rather than from
    ``slot_at``, because what is under test is the relationship between the start
    time and the clock, and ``slot_at`` deliberately hides that by always
    returning a future slot.

    Each test gives the teacher hours that cover the attempt, so the availability
    rule cannot be what rejects it. That is why the rejections below assert an
    empty ``NON_FIELD_ERRORS`` as well as the expected field code: without it,
    a booking refused for being outside declared hours would pass a test claiming
    to be about past-dating.
    """

    def covering_teacher(self, moment, level):
        """A bookable teacher whose declared hours cover a session at ``moment``.

        A full UTC day on that instant's own weekday. Availability is a weekly
        rule with no notion of a date, so a window covers last Tuesday exactly as
        much as next Tuesday — which is precisely why nothing before this phase
        objected to a booking in the past.

        They also teach ``level``'s track, so Phase 4's specialty rule cannot be
        what refuses these bookings; the assertions below check for an otherwise
        empty error set for exactly that reason.
        """
        teacher = BookableTeacherFactory()
        teaches(teacher, level)
        Availability.objects.create(
            organization=level.track.organization,
            teacher=teacher,
            weekday=moment.weekday(),
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )
        return teacher

    def attempt(self, start):
        """Try to create a default-length booking starting at ``start``."""
        level = LevelFactory()
        student = StudentFactory()
        admit(student, level.track.organization)
        return Booking.objects.create(
            student=student,
            teacher=self.covering_teacher(start, level),
            level=level,
            start_time_utc=start,
        )

    def skip_if_it_would_cross_utc_midnight(self, start):
        """A session spanning midnight UTC is rejected by a different rule.

        No single availability window crosses midnight, so a run happening in the
        last half hour of the UTC day cannot build the "covered by declared
        hours" precondition these tests need. Skipping is honest; quietly
        asserting the wrong error code would not be.
        """
        session_end = start + timedelta(minutes=DEFAULT_DURATION_MINUTES)
        if session_end.date() != start.date():
            self.skipTest(
                "this run is within half an hour of UTC midnight, where a "
                "session crosses the day boundary and no window can cover it"
            )

    def assert_rejected_as_past(self, start):
        with self.assertRaises(ValidationError) as ctx:
            self.attempt(start)
        self.assertEqual(
            error_codes(ctx.exception, "start_time_utc"), ["start_time_in_past"]
        )
        # Nothing else objected, so the past-start rule is genuinely what fired.
        self.assertEqual(error_codes(ctx.exception), [])
        self.assertFalse(Booking.objects.exists())

    # --- Criterion 2: the past is refused -----------------------------------

    def test_a_booking_well_in_the_past_is_rejected(self):
        """Acceptance criterion 2."""
        start = dj_timezone.now() - timedelta(hours=1)
        self.skip_if_it_would_cross_utc_midnight(start)
        self.assert_rejected_as_past(start)

    def test_a_booking_last_week_is_rejected(self):
        """The slot a completed session used to occupy is not re-bookable.

        This is the hole the tech-debt entry called out: the overlap rule only
        considers *scheduled* bookings, so a past slot held by a completed
        session reads as free. Being in the past is now what stops it.
        """
        start = dj_timezone.now() - timedelta(days=7)
        self.skip_if_it_would_cross_utc_midnight(start)
        self.assert_rejected_as_past(start)

    def test_a_booking_just_beyond_the_grace_window_is_rejected(self):
        """The far side of the boundary the next test proves the near side of."""
        start = dj_timezone.now() - (PAST_BOOKING_GRACE + timedelta(minutes=2))
        self.skip_if_it_would_cross_utc_midnight(start)
        self.assert_rejected_as_past(start)

    # --- Criterion 3: the grace window works --------------------------------

    def test_a_booking_inside_the_grace_window_is_accepted(self):
        """Acceptance criterion 3 — the grace period does something.

        Derived from ``PAST_BOOKING_GRACE`` rather than hardcoded, so shrinking
        the constant cannot leave this test silently asserting the old boundary.
        A booking this far back is what a student clicking an available 09:00
        slot at 09:00:0x actually produces.
        """
        start = dj_timezone.now() - (PAST_BOOKING_GRACE / 2)
        self.skip_if_it_would_cross_utc_midnight(start)

        booking = self.attempt(start)
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.start_time_utc, start)

    def test_a_booking_starting_now_is_accepted(self):
        """The boundary case the grace window exists for: a session starting now."""
        start = dj_timezone.now()
        self.skip_if_it_would_cross_utc_midnight(start)
        self.assertEqual(self.attempt(start).status, BookingStatus.SCHEDULED)

    def test_a_booking_in_the_future_is_unaffected(self):
        """The ordinary case has to keep working, which is most of the point."""
        start = dj_timezone.now() + timedelta(hours=1)
        self.skip_if_it_would_cross_utc_midnight(start)
        self.assertEqual(self.attempt(start).status, BookingStatus.SCHEDULED)

    # --- Criterion 4: creation-time only ------------------------------------

    def test_a_taught_session_can_still_be_marked_completed(self):
        """Acceptance criterion 4, in the form it actually happens.

        A session is future-dated when it is booked and past-dated by the time it
        has been taught. If the rule were not creation-only, marking it completed
        — the one write every finished session needs — would be impossible.
        """
        booking = age_booking(BookingFactory(), timedelta(days=1))
        self.assertLess(
            booking.start_time_utc,
            dj_timezone.now() - PAST_BOOKING_GRACE,
            "precondition: the start really is past, so the rule would fire on a "
            "new booking with this time",
        )

        booking.status = BookingStatus.COMPLETED
        booking.save()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.COMPLETED)

    def test_a_still_scheduled_booking_whose_time_has_passed_stays_saveable(self):
        """The ``_state.adding`` half of criterion 4, not the status half.

        A booking that is still ``scheduled`` runs the overlap rule on every
        save, so this one goes through the same branch a new booking does and is
        spared only because it is not being created. Were the rule gated on
        status alone, a session that had merely started would freeze.
        """
        booking = age_booking(BookingFactory(), timedelta(hours=2))

        booking.save()  # must not raise

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)

    def test_a_session_that_has_already_started_can_still_be_cancelled(self):
        """``cancel()`` goes through ``save()``, so the rule could have wedged it.

        The same trap the availability check was made creation-only to avoid —
        an unsaveable booking is an uncancellable one.
        """
        booking = age_booking(BookingFactory(), timedelta(minutes=45))

        booking.cancel()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_a_past_booking_can_be_recorded_as_a_no_show(self):
        """The other end-state a past session needs to be able to reach."""
        booking = age_booking(BookingFactory(), timedelta(days=2))

        booking.status = BookingStatus.NO_SHOW
        booking.save()

        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.NO_SHOW)


class SchedulingTenancyQuerysetTests(TestCase):
    """SaaS Phase 4 Task 4.2 — tenant query helpers and derived properties."""

    def setUp(self):
        self.track_a = TrackFactory(slug="track-a")
        self.track_b = TrackFactory(slug="track-b")
        self.level_a = LevelFactory(track=self.track_a)
        self.level_b = LevelFactory(track=self.track_b)
        self.group_level_a = GroupEligibleLevelFactory(track=self.track_a)
        self.group_level_b = GroupEligibleLevelFactory(track=self.track_b)

    def test_booking_derived_organization_and_queryset(self):
        booking_a = BookingFactory(level=self.level_a)
        booking_b = BookingFactory(level=self.level_b)

        # Derived organization property
        self.assertEqual(booking_a.organization, self.track_a.organization)
        self.assertEqual(booking_b.organization, self.track_b.organization)

        # in_organization queryset helper with Organization instance
        self.assertEqual(
            list(Booking.objects.in_organization(self.track_a.organization)),
            [booking_a],
        )
        self.assertEqual(
            list(Booking.objects.in_organization(self.track_b.organization)),
            [booking_b],
        )

        # in_organization queryset helper with PK
        self.assertEqual(
            list(Booking.objects.in_organization(self.track_a.organization.pk)),
            [booking_a],
        )

        # Unsaved booking with no level returns None
        unsaved = Booking()
        self.assertIsNone(unsaved.organization)

    def test_cohort_derived_organization_and_queryset(self):
        cohort_a = CohortFactory(level=self.group_level_a)
        cohort_b = CohortFactory(level=self.group_level_b)

        # Derived organization property
        self.assertEqual(cohort_a.organization, self.track_a.organization)
        self.assertEqual(cohort_b.organization, self.track_b.organization)

        # in_organization queryset helper
        self.assertEqual(
            list(Cohort.objects.in_organization(self.track_a.organization)),
            [cohort_a],
        )
        self.assertEqual(
            list(Cohort.objects.in_organization(self.track_b.organization)),
            [cohort_b],
        )

        # in_organization with PK
        self.assertEqual(
            list(Cohort.objects.in_organization(self.track_a.organization.pk)),
            [cohort_a],
        )

        # open() queryset helper
        self.assertIn(cohort_a, Cohort.objects.open())

        # Unsaved cohort with no level returns None
        unsaved = Cohort()
        self.assertIsNone(unsaved.organization)

    def test_waitlist_derived_organization_and_queryset(self):
        entry_a = WaitlistEntryFactory(level=self.level_a)
        entry_b = WaitlistEntryFactory(level=self.level_b)

        # Derived organization property
        self.assertEqual(entry_a.organization, self.track_a.organization)
        self.assertEqual(entry_b.organization, self.track_b.organization)

        # in_organization queryset helper
        self.assertEqual(
            list(TeacherWaitlist.objects.in_organization(self.track_a.organization)),
            [entry_a],
        )
        self.assertEqual(
            list(TeacherWaitlist.objects.in_organization(self.track_b.organization)),
            [entry_b],
        )

        # in_organization with PK
        self.assertEqual(
            list(TeacherWaitlist.objects.in_organization(self.track_a.organization.pk)),
            [entry_a],
        )

        # open() queryset helper
        self.assertIn(entry_a, TeacherWaitlist.objects.open())

        # Unsaved waitlist entry with no level returns None
        unsaved = TeacherWaitlist()
        self.assertIsNone(unsaved.organization)


class AvailabilityTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.3 — Availability academy ownership and access semantics."""

    def setUp(self):
        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")
        self.teacher = BookableTeacherFactory()
        self.membership_a = admit(self.teacher, self.org_a)
        self.membership_b = admit(self.teacher, self.org_b)

        self.track_a = TrackFactory(organization=self.org_a)
        self.level_a = LevelFactory(track=self.track_a)
        self.track_b = TrackFactory(organization=self.org_b)
        self.level_b = LevelFactory(track=self.track_b)
        teaches(self.teacher, self.level_a)
        teaches(self.teacher, self.level_b)

    def test_clean_requires_active_membership_in_target_organization(self):
        """Teacher must hold active membership in availability.organization."""
        outsider = BookableTeacherFactory()
        window = Availability(
            teacher=outsider,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        with self.assertRaises(ValidationError) as ctx:
            window.full_clean()
        self.assertIn(
            "teacher_not_active_member", error_codes(ctx.exception, field="teacher")
        )

    def test_clean_rejects_suspended_membership(self):
        """Suspended membership does not permit creating availability."""
        self.membership_a.status = MembershipStatus.SUSPENDED
        self.membership_a.save()

        window = Availability(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        with self.assertRaises(ValidationError) as ctx:
            window.full_clean()
        self.assertIn(
            "teacher_not_active_member", error_codes(ctx.exception, field="teacher")
        )

    def test_clean_accepts_active_membership(self):
        """Active membership allows window creation."""
        window = Availability(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        window.full_clean()  # should not raise
        window.save()
        self.assertEqual(window.organization, self.org_a)

    def test_in_organization_isolates_windows_per_academy(self):
        """Looking up availability in Academy A never discloses Academy B hours."""
        w_a = AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(12, 0),
        )
        w_b = AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(14, 0),
            end_time_utc=time(18, 0),
        )

        self.assertEqual(
            list(Availability.objects.in_organization(self.org_a)),
            [w_a],
        )
        self.assertEqual(
            list(Availability.objects.in_organization(self.org_b)),
            [w_b],
        )
        self.assertEqual(
            list(Availability.objects.in_organization(self.org_a.pk)),
            [w_a],
        )

    def test_cross_academy_availability_cannot_be_consumed_by_booking(self):
        """An Academy A booking may not succeed merely because Academy B has an
        availability window covering the requested time (spec Section 6)."""
        AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(14, 0),
            end_time_utc=time(18, 0),
        )
        start_utc = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(
            datetime.combine(start_utc, time(14, 0)), UTC
        )

        student = StudentFactory()
        admit(student, self.org_a)

        # Booking in Academy A (level_a belongs to org_a) has no availability in org_a
        booking = Booking(
            student=student,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn("outside_availability", error_codes(ctx.exception))

    def test_suspended_teacher_membership_makes_availability_unusable(self):
        """Suspended teacher membership shuts off availability for new bookings (spec Section 44)."""
        AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        start_utc = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(
            datetime.combine(start_utc, time(10, 0)), UTC
        )

        student = StudentFactory()
        admit(student, self.org_a)

        # Suspend teacher in org_a
        self.membership_a.status = MembershipStatus.SUSPENDED
        self.membership_a.save()

        booking = Booking(
            student=student,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "teacher_not_active_member", error_codes(ctx.exception, field="teacher")
        )

    def test_booking_succeeds_when_teacher_active_in_correct_academy(self):
        """Booking succeeds when teacher is active and availability belongs to same academy."""
        AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        start_utc = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(
            datetime.combine(start_utc, time(10, 0)), UTC
        )
        student = StudentFactory()
        admit(student, self.org_a)

        booking = Booking(
            student=student,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
        )
        booking.full_clean()
        booking.save()
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.organization, self.org_a)

    def test_create_from_local_infers_single_organization(self):
        """create_from_local infers organization if teacher belongs to exactly one."""
        single_org_teacher = BookableTeacherFactory()
        single_org = OrganizationFactory()
        ensure_teacher_configured(single_org_teacher, single_org)

        windows = Availability.create_from_local(
            organization=single_org,
            teacher=single_org_teacher,
            weekday=Weekday.TUESDAY,
            start_local=time(10, 0),
            end_local=time(14, 0),
        )
        self.assertTrue(len(windows) > 0)
        for w in windows:
            self.assertEqual(w.organization, single_org)


class TeacherConfigurationTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.4 — OrganizationTeacherConfiguration approval and validation."""

    def setUp(self):
        from accounts.models import OrganizationTeacherConfiguration

        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")

        self.teacher = BookableTeacherFactory()
        self.track_a = TrackFactory(organization=self.org_a)
        self.level_a = LevelFactory(track=self.track_a)
        self.track_b = TrackFactory(organization=self.org_b)
        self.level_b = LevelFactory(track=self.track_b)

        # Admitted and configured in A as approved
        ensure_teacher_configured(self.teacher, self.org_a)
        teaches(self.teacher, self.level_a)

        # Admitted in B but configured as unapproved
        m_b = admit(self.teacher, self.org_b)
        OrganizationTeacherConfiguration.objects.update_or_create(
            membership=m_b,
            defaults={"approved": False, "max_weekly_hours": 10},
        )
        teaches(self.teacher, self.level_b)

    def test_teacher_approved_in_a_can_be_booked_in_a(self):
        """Spec Section 13: Teacher T approved in A can be booked by A."""
        AvailabilityFactory(
            teacher=self.teacher,
            organization=self.org_a,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        student_a = StudentFactory()
        admit(student_a, self.org_a)

        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        booking.full_clean()
        booking.save()
        self.assertEqual(booking.status, BookingStatus.SCHEDULED)
        self.assertEqual(booking.organization, self.org_a)

    def test_teacher_unapproved_in_b_cannot_be_booked_in_b(self):
        """Spec Section 13: Teacher T unapproved in B cannot be booked by B."""
        from accounts.models import OrganizationTeacherConfiguration

        config_b = OrganizationTeacherConfiguration.objects.get(
            membership__user=self.teacher, membership__organization=self.org_b
        )
        config_b.approved = True
        config_b.save()

        Availability.objects.filter(
            teacher=self.teacher, organization=self.org_b
        ).delete()
        Availability.objects.create(
            teacher=self.teacher,
            organization=self.org_b,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        # Now revoke approval in Academy B
        config_b.approved = False
        config_b.save()

        student_b = StudentFactory()
        admit(student_b, self.org_b)

        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=student_b,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "teacher_not_approved", error_codes(ctx.exception, field="teacher")
        )

    def test_teacher_without_configuration_is_rejected(self):
        """Spec Section 16: Missing configuration in academy is rejected with teacher_not_configured."""
        from accounts.models import OrganizationTeacherConfiguration

        org_c = OrganizationFactory(name="Academy C", slug="academy-c")
        track_c = TrackFactory(organization=org_c)
        level_c = LevelFactory(track=track_c)
        admit(self.teacher, org_c)
        self.teacher.teacher_profile.specialties.add(track_c)
        # Ensure no configuration exists in org_c
        OrganizationTeacherConfiguration.objects.filter(
            membership__user=self.teacher, membership__organization=org_c
        ).delete()

        student_c = StudentFactory()
        admit(student_c, org_c)
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=student_c,
            teacher=self.teacher,
            level=level_c,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "teacher_not_configured", error_codes(ctx.exception, field="teacher")
        )


class TeacherTrackSchedulingTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.5 — TeacherTrack curriculum eligibility migration (spec Section 14 & 15)."""

    def setUp(self):
        from curriculum.models import TeacherTrack

        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")

        self.teacher = BookableTeacherFactory()
        self.membership_a = admit(self.teacher, self.org_a)
        self.membership_b = admit(self.teacher, self.org_b)
        ensure_teacher_configured(self.teacher, self.org_a)
        ensure_teacher_configured(self.teacher, self.org_b)

        self.track_a = TrackFactory(organization=self.org_a, name="Tajweed A")
        self.level_a = LevelFactory(track=self.track_a, name="Tajweed Level 1")
        self.track_b = TrackFactory(organization=self.org_b, name="Tajweed B")
        self.level_b = LevelFactory(track=self.track_b, name="Tajweed Level 1")

        # Give availability in both academies
        Availability.objects.create(
            organization=self.org_a,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        Availability.objects.create(
            organization=self.org_b,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        # Grant eligibility in Academy A only
        self.tt_a = TeacherTrack.objects.create(
            membership=self.membership_a,
            track=self.track_a,
            active=True,
        )

        self.student_a = StudentFactory()
        admit(self.student_a, self.org_a)
        self.student_b = StudentFactory()
        admit(self.student_b, self.org_b)

    def test_teachertrack_in_academy_a_does_not_grant_eligibility_in_academy_b(self):
        """Spec Section 14: A track assignment in Academy A grants no eligibility in Academy B."""
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        # Booking in Academy A succeeds
        booking_a = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        booking_a.full_clean()
        booking_a.save()
        self.assertEqual(booking_a.status, BookingStatus.SCHEDULED)

        # Booking in Academy B is rejected with teacher_lacks_specialty on level
        booking_b = Booking(
            student=self.student_b,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking_b.full_clean()
        self.assertIn(
            "teacher_lacks_specialty", error_codes(ctx.exception, field="level")
        )

    def test_inactive_teachertrack_is_rejected(self):
        """Spec Section 14: TeacherTrack.active == true is required."""
        self.tt_a.active = False
        self.tt_a.save()

        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "teacher_lacks_specialty", error_codes(ctx.exception, field="level")
        )

    def test_cohort_clean_enforces_teachertrack(self):
        """Cohort.clean rejects a teacher who does not have an active TeacherTrack."""
        from curriculum.tests.factories import GroupEligibleLevelFactory

        group_level_b = GroupEligibleLevelFactory(track=self.track_b)
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        cohort = Cohort(
            teacher=self.teacher,
            level=group_level_b,
            schedule_start_utc=start_utc,
        )
        with self.assertRaises(ValidationError) as ctx:
            cohort.full_clean()
        self.assertIn(
            "teacher_lacks_specialty", error_codes(ctx.exception, field="teacher")
        )

    def test_specialty_error_helper_directly(self):
        """Spec Section 15: Direct evaluation of specialty_error helper."""
        from scheduling.models import specialty_error

        # With active eligibility in Academy A
        self.assertIsNone(specialty_error(self.teacher, self.level_a, organization=self.org_a))

        # Without eligibility in Academy B
        err_b = specialty_error(self.teacher, self.level_b, organization=self.org_b)
        self.assertIsNotNone(err_b)
        self.assertEqual(err_b.code, "teacher_lacks_specialty")

        # When deactivated in Academy A
        self.tt_a.active = False
        self.tt_a.save()
        err_a = specialty_error(self.teacher, self.level_a, organization=self.org_a)
        self.assertIsNotNone(err_a)
        self.assertEqual(err_a.code, "teacher_lacks_specialty")


class BookingAndCohortTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.6 — Booking and Cohort Tenancy (specs/saas/phase-4/4.6-booking-cohort-tenancy.md)."""

    def setUp(self):
        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")

        self.teacher = BookableTeacherFactory()
        admit(self.teacher, self.org_a)
        admit(self.teacher, self.org_b)
        ensure_teacher_configured(self.teacher, self.org_a)
        ensure_teacher_configured(self.teacher, self.org_b)

        self.track_a = TrackFactory(organization=self.org_a, name="Track A")
        self.level_a = LevelFactory(track=self.track_a, name="Level A")
        self.group_level_a = GroupEligibleLevelFactory(track=self.track_a, name="Group Level A")
        teaches(self.teacher, self.level_a)
        teaches(self.teacher, self.group_level_a)

        self.track_b = TrackFactory(organization=self.org_b, name="Track B")
        self.level_b = LevelFactory(track=self.track_b, name="Level B")
        self.group_level_b = GroupEligibleLevelFactory(track=self.track_b, name="Group Level B")
        teaches(self.teacher, self.level_b)
        teaches(self.teacher, self.group_level_b)

        Availability.objects.create(
            organization=self.org_a,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )
        Availability.objects.create(
            organization=self.org_b,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(9, 0),
            end_time_utc=time(17, 0),
        )

        self.student_a = StudentFactory()
        self.student_a_membership = admit(self.student_a, self.org_a)
        self.student_b = StudentFactory()
        self.student_b_membership = admit(self.student_b, self.org_b)

    def test_student_in_academy_a_cannot_be_booked_in_academy_b(self):
        """Spec Section 9: Active membership for booking.organization is required."""
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "student_not_active_member", error_codes(ctx.exception, field="student")
        )

    def test_suspended_student_cannot_receive_new_booking(self):
        """Spec Section 9: A suspended student cannot receive a new academy booking."""
        self.student_a_membership.status = MembershipStatus.SUSPENDED
        self.student_a_membership.save()

        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn(
            "student_not_active_member", error_codes(ctx.exception, field="student")
        )

    def test_cross_academy_physical_overlap_is_rejected(self):
        """Spec Section 18: Simultaneous sessions across academies must conflict (global overlap)."""
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        # Booking in Academy A succeeds
        booking_a = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        booking_a.full_clean()
        booking_a.save()
        self.assertEqual(booking_a.status, BookingStatus.SCHEDULED)

        # Booking in Academy B for the same time fails with teacher_double_booked
        booking_b = Booking(
            student=self.student_b,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking_b.full_clean()
        self.assertIn(
            "teacher_double_booked",
            [e.code for e in ctx.exception.error_dict[NON_FIELD_ERRORS]],
        )

        # But a neighbouring non-overlapping booking in Academy B succeeds
        booking_b_next = Booking(
            student=self.student_b,
            teacher=self.teacher,
            level=self.level_b,
            start_time_utc=start_utc + timedelta(minutes=30),
            duration_minutes=30,
        )
        booking_b_next.full_clean()
        booking_b_next.save()
        self.assertEqual(booking_b_next.status, BookingStatus.SCHEDULED)

    def test_historical_booking_stays_saveable_and_cancellable_after_student_suspended(self):
        """Spec Section 45: Membership suspension does not retroactively invalidate accepted bookings."""
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        booking = Booking(
            student=self.student_a,
            teacher=self.teacher,
            level=self.level_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        booking.full_clean()
        booking.save()

        # Suspend student
        self.student_a_membership.status = MembershipStatus.SUSPENDED
        self.student_a_membership.save()

        # Historical booking remains cancellable
        booking.refresh_from_db()
        booking.cancel()
        booking.refresh_from_db()
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_cohort_seat_booking_must_match_cohort_organization(self):
        """Spec Section 8 & 21: A booking combining records from different academies must fail."""
        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        cohort_a = Cohort.objects.create(
            teacher=self.teacher,
            level=self.group_level_a,
            schedule_start_utc=start_utc,
        )

        booking = Booking(
            student=self.student_b,
            teacher=self.teacher,
            level=self.group_level_b,
            cohort=cohort_a,
            start_time_utc=start_utc,
            duration_minutes=30,
        )
        with self.assertRaises(ValidationError) as ctx:
            booking.full_clean()
        self.assertIn("cohort_mismatch", error_codes(ctx.exception, field="cohort"))

    def test_teacher_not_active_in_academy_cannot_run_cohort(self):
        """Spec Section 21: Cohort teacher must be an active member of cohort academy."""
        teacher_foreign = BookableTeacherFactory()
        admit(teacher_foreign, self.org_a)
        ensure_teacher_configured(teacher_foreign, self.org_a)
        teaches(teacher_foreign, self.group_level_a)

        start = next_date_for_weekday(Weekday.MONDAY)
        start_utc = dj_timezone.make_aware(datetime.combine(start, time(10, 0)), UTC)

        cohort = Cohort(
            teacher=teacher_foreign,
            level=self.group_level_b,
            schedule_start_utc=start_utc,
        )
        with self.assertRaises(ValidationError) as ctx:
            cohort.full_clean()
        self.assertIn(
            "teacher_not_active_member", error_codes(ctx.exception, field="teacher")
        )


class ParentBookingTenancyTests(TestCase):
    """SaaS Phase 4 Task 4.6 — Parent Booking Rule (specs/saas/phase-4/4.6-booking-cohort-tenancy.md Section 10)."""

    def setUp(self):
        from accounts.models import ParentLink

        self.org_a = OrganizationFactory(name="Academy A", slug="academy-a")
        self.org_b = OrganizationFactory(name="Academy B", slug="academy-b")

        self.parent = ParentFactory()
        self.student = StudentFactory()

        # Global parent link exists
        ParentLink.objects.create(parent=self.parent, student=self.student)

        # Level in Academy A and Level in Academy B
        self.track_a = TrackFactory(organization=self.org_a)
        self.level_a = LevelFactory(track=self.track_a)

        self.track_b = TrackFactory(organization=self.org_b)
        self.level_b = LevelFactory(track=self.track_b)

    def test_parent_booking_requires_both_active_in_organization(self):
        """Parent P active in A and B, Student S active only in B:
        P booking S in B -> allowed
        P booking S in A -> denied
        """
        from scheduling.serializers import resolve_requested_student
        from rest_framework.exceptions import ValidationError as DRFValidationError

        # Parent active in A and B
        admit(self.parent, self.org_a)
        admit(self.parent, self.org_b)

        # Student active only in B
        admit(self.student, self.org_b)

        # In Academy B: resolved successfully
        resolved = resolve_requested_student(
            self.parent, self.student, organization=self.org_b
        )
        self.assertEqual(resolved, self.student)

        # In Academy A: denied
        with self.assertRaises(DRFValidationError) as ctx:
            resolve_requested_student(
                self.parent, self.student, organization=self.org_a
            )
        self.assertIn("student", ctx.exception.detail)


class MigrationStateTests(TestCase):
    def test_no_model_changes_are_missing_a_migration(self):
        """Guards acceptance criterion 1 against later model edits."""
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)

class AvailabilityValidationTests(TestCase):
    def test_missing_organization_raises_type_error(self):
        with self.assertRaises(TypeError):
            Availability.create_from_local(
                teacher=None,
                weekday=Weekday.MONDAY,
                start_local=time(10, 0),
                end_local=time(12, 0),
            )

    def test_teacher_not_in_organization_raises_validation_error(self):
        teacher = BookableTeacherFactory()
        org = OrganizationFactory()
        with self.assertRaises(ValidationError) as cm:
            Availability.create_from_local(
                organization=org,
                teacher=teacher,
                weekday=Weekday.MONDAY,
                start_local=time(10, 0),
                end_local=time(12, 0),
            )
        self.assertIn("active membership in the organization", str(cm.exception))

    def test_teacher_not_configured_raises_validation_error(self):
        teacher = BookableTeacherFactory()
        org = OrganizationFactory()
        # Give them membership, but no configuration
        from organizations.models import OrganizationMembership, OrganizationRole
        OrganizationMembership.objects.create(organization=org, user=teacher, role=OrganizationRole.TEACHER)
        
        with self.assertRaises(ValidationError) as cm:
            Availability.create_from_local(
                organization=org,
                teacher=teacher,
                weekday=Weekday.MONDAY,
                start_local=time(10, 0),
                end_local=time(12, 0),
            )
        self.assertIn("valid academy configuration", str(cm.exception))
