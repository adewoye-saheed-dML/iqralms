"""API tests for the scheduling app.

Every endpoint gets a happy path and at least one failure case, per CLAUDE.md.
Acceptance criteria from specs/phase-3-scheduling.md covered here: 2 (a booking
inside a declared window succeeds, outside is rejected), 3 (an overlapping
booking for the same teacher is rejected), 4 (an unapproved teacher is
rejected), 5 (``video_room_name`` and the Jitsi join URL come back), 6
(cancelling sets the status and keeps the row) and 7 (a student cannot see
another student's bookings).

The model layer proves the same rules in test_models.py. Both matter: the rules
live in ``clean()`` so they hold for direct ORM writes, and these tests prove
the API surfaces them as 400s rather than 500s.
"""

from datetime import time, timedelta

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)
from curriculum.tests.factories import LevelFactory
from scheduling.models import Booking, BookingStatus, Weekday

from .factories import (
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    BookableTeacherFactory,
    BookingFactory,
    CancelledBookingFactory,
    CompletedBookingFactory,
    slot_at,
    teaches,
)
from .test_models import unapprove

AVAILABILITY_URL = reverse("scheduling:availability-list")
BOOKINGS_URL = reverse("scheduling:booking-create")
MINE_URL = reverse("scheduling:booking-mine")
TEACHING_URL = reverse("scheduling:booking-teaching")


def cancel_url(booking_or_pk):
    pk = getattr(booking_or_pk, "pk", booking_or_pk)
    return reverse("scheduling:booking-cancel", args=[pk])


class AvailabilityListAPITests(APITestCase):
    def setUp(self):
        self.teacher = BookableTeacherFactory(timezone="Africa/Lagos")
        self.window = AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(16, 0),
        )

    def test_listing_a_teachers_windows_is_public(self):
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": self.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        payload = response.data[0]
        self.assertEqual(payload["teacher"], self.teacher.pk)
        self.assertEqual(payload["teacher_username"], self.teacher.username)
        self.assertEqual(payload["weekday"], Weekday.MONDAY)
        self.assertEqual(payload["weekday_display"], "Monday")
        self.assertEqual(payload["start_time_utc"], "08:00:00")
        self.assertEqual(payload["end_time_utc"], "16:00:00")

    def test_an_anonymous_caller_sees_the_teachers_own_zone(self):
        """There is no requesting timezone to render in, so fall back to theirs.

        Asserted against the rendered JSON rather than ``response.data``: the
        ``local`` block is a ``SerializerMethodField``, so ``.data`` holds raw
        ``time`` objects and only the renderer formats them.
        """
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": self.teacher.pk})
        local = response.json()[0]["local"]
        self.assertEqual(local["timezone"], "Africa/Lagos")
        self.assertEqual(local["start_time"], "09:00:00")
        self.assertEqual(local["end_time"], "17:00:00")

    def test_an_authenticated_caller_sees_their_own_zone(self):
        self.client.force_authenticate(user=StudentFactory(timezone="Asia/Karachi"))
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": self.teacher.pk})
        local = response.json()[0]["local"]
        self.assertEqual(local["timezone"], "Asia/Karachi")
        self.assertEqual(local["start_time"], "13:00:00")  # 08:00 UTC + 5
        self.assertEqual(local["end_time"], "21:00:00")

    def test_the_local_weekday_is_reported_alongside_the_times(self):
        """A conversion can cross midnight, so the weekday may differ from UTC."""
        teacher = BookableTeacherFactory(timezone="Pacific/Auckland")
        AvailabilityFactory(
            teacher=teacher,
            weekday=Weekday.SUNDAY,
            start_time_utc=time(21, 0),
            end_time_utc=time(23, 0),
        )
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": teacher.pk})
        payload = response.json()[0]
        self.assertEqual(payload["weekday"], Weekday.SUNDAY)
        self.assertEqual(payload["local"]["weekday"], Weekday.MONDAY)

    def test_windows_come_back_ordered_by_day_then_start(self):
        AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(3, 0),
            end_time_utc=time(5, 0),
        )
        AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.SATURDAY,
            start_time_utc=time(6, 0),
            end_time_utc=time(7, 0),
        )
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": self.teacher.pk})
        self.assertEqual(
            [(w["weekday"], w["start_time_utc"]) for w in response.data],
            [(0, "03:00:00"), (0, "08:00:00"), (5, "06:00:00")],
        )

    def test_only_the_requested_teachers_windows_come_back(self):
        AvailabilityFactory()  # a different teacher entirely
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": self.teacher.pk})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["teacher"], self.teacher.pk)

    def test_teacher_id_is_required(self):
        response = self.client.get(AVAILABILITY_URL)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_a_non_numeric_teacher_id_is_a_400(self):
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": "abc"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_an_unknown_teacher_is_an_empty_list_not_a_404(self):
        """"When is this person free" and "does this person exist" are different
        questions, and only the first one is public."""
        response = self.client.get(AVAILABILITY_URL, {"teacher_id": 999999})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])


class BookingCreateAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        self.level = LevelFactory()
        # Phase 4 makes the track specialty a hard rule for direct booking too,
        # so a bookable teacher is now one who *also* teaches the level in
        # question. The rejection when they don't is asserted separately, in
        # SpecialtyEnforcementAPITests.
        teaches(self.teacher, self.level)
        self.student = StudentFactory(timezone="Africa/Lagos")

    def payload(self, offset_minutes=0, **overrides):
        body = {
            "teacher": self.teacher.pk,
            "level": self.level.pk,
            "start_time_utc": slot_at(self.window, offset_minutes).isoformat(),
        }
        body.update(overrides)
        return body

    # --- Happy paths --------------------------------------------------------

    def test_a_student_books_a_slot_inside_the_declared_window(self):
        """Acceptance criteria 2 and 5, over HTTP."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(120))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], self.student.pk)
        self.assertEqual(response.data["teacher"]["id"], self.teacher.pk)
        self.assertEqual(response.data["level"]["id"], self.level.pk)
        self.assertEqual(response.data["status"], BookingStatus.SCHEDULED)
        self.assertEqual(response.data["duration_minutes"], 30)
        self.assertTrue(response.data["video_room_name"])

        booking = Booking.objects.get(pk=response.data["id"])
        self.assertEqual(booking.student, self.student)
        self.assertEqual(booking.teacher, self.teacher)

    @override_settings(JITSI_DOMAIN="meet.jit.si")
    def test_the_response_carries_a_working_join_url(self):
        """Acceptance criterion 5 — the URL has to exist and be correct."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertEqual(
            response.data["video_join_url"],
            f"https://meet.jit.si/{response.data['video_room_name']}",
        )

    def test_two_bookings_get_distinct_rooms(self):
        """Acceptance criterion 5, second half."""
        self.client.force_authenticate(user=self.student)
        first = self.client.post(BOOKINGS_URL, self.payload(0))
        second = self.client.post(BOOKINGS_URL, self.payload(60))
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(
            first.data["video_room_name"], second.data["video_room_name"]
        )

    def test_the_response_renders_the_start_in_the_callers_zone(self):
        self.client.force_authenticate(user=self.student)  # Africa/Lagos, UTC+1
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertIn("+01:00", response.data["start_time_local"])

    def test_the_end_time_and_track_are_derived_for_the_client(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(duration_minutes=45))
        self.assertEqual(response.data["duration_minutes"], 45)
        self.assertEqual(response.data["track"], self.level.track.slug)

    def test_a_parent_books_for_a_linked_child(self):
        link = ParentLinkFactory()
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            BOOKINGS_URL, self.payload(student=link.student.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], link.student.pk)

    def test_a_student_may_name_themselves_explicitly(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(student=self.student.pk))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # --- Who may book -------------------------------------------------------

    def test_an_anonymous_caller_cannot_book(self):
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_cannot_book_a_session(self):
        for user in (SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(BOOKINGS_URL, self.payload())
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_student_cannot_book_for_someone_else(self):
        other = StudentFactory()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(student=other.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_parent_must_say_which_child(self):
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_a_parent_cannot_book_for_an_unlinked_student(self):
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.post(
            BOOKINGS_URL, self.payload(student=StudentFactory().pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_minor_with_no_linked_parent_cannot_be_booked(self):
        minor = MinorStudentFactory()
        self.client.force_authenticate(user=minor)
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    # --- Slot rules, surfaced from the model as 400s ------------------------

    def test_a_booking_outside_the_declared_window_is_rejected(self):
        """Acceptance criterion 2, second half, over HTTP."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(-60))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_an_overlapping_booking_for_the_same_teacher_is_rejected(self):
        """Acceptance criterion 3, over HTTP."""
        BookingFactory(availability=self.window, start_time_utc=slot_at(self.window, 120))
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(135))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Booking.objects.count(), 1)

    def test_a_neighbouring_booking_for_the_same_teacher_succeeds(self):
        """Acceptance criterion 3, second half, over HTTP."""
        BookingFactory(availability=self.window, start_time_utc=slot_at(self.window, 120))
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(150))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 2)

    def test_a_booking_in_the_past_is_rejected(self):
        """Phase 3.5 — the past-start rule surfaces as a 400, not a 500.

        The timestamp is a *fortnight-old* Monday at 11:00, so it sits squarely
        inside the teacher's declared Monday 09:00-17:00 hours: availability is a
        weekly rule and has no opinion about which Monday. That is what makes this
        a test of the past-start rule rather than of the availability rule, and
        why the assertion is on the ``start_time_utc`` key specifically.
        """
        two_weeks_ago = (dj_timezone.now() - timedelta(days=14)).date()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL,
            self.payload(
                start_time_utc=slot_at(
                    self.window, 120, on_or_after=two_weeks_ago
                ).isoformat()
            ),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("start_time_utc", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_booking_against_an_unapproved_teacher_is_rejected(self):
        """Acceptance criterion 4, over HTTP."""
        unapprove(self.teacher)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_booking_against_a_non_teacher_is_rejected(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL, self.payload(teacher=StudentFactory().pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_an_unknown_teacher_or_level_is_a_400(self):
        self.client.force_authenticate(user=self.student)
        for field in ("teacher", "level"):
            with self.subTest(field=field):
                response = self.client.post(
                    BOOKINGS_URL, self.payload(**{field: 999999})
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_a_zero_length_session_is_rejected(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(duration_minutes=0))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("duration_minutes", response.data)

    def test_the_client_cannot_choose_its_own_room_name(self):
        """Not a writable field, so a supplied value is ignored, not honoured."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL, self.payload(video_room_name="guessable-room")
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(response.data["video_room_name"], "guessable-room")

    def test_the_client_cannot_create_an_already_completed_booking(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL, self.payload(status=BookingStatus.COMPLETED)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], BookingStatus.SCHEDULED)


class MyBookingsAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory()
        self.student = StudentFactory()
        self.mine = BookingFactory(availability=self.window, student=self.student)

    def test_a_student_sees_their_own_bookings(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([b["id"] for b in response.data], [self.mine.pk])

    def test_a_student_cannot_see_another_students_bookings(self):
        """Acceptance criterion 7."""
        intruder = StudentFactory()
        self.client.force_authenticate(user=intruder)
        response = self.client.get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_past_and_upcoming_bookings_both_appear_earliest_first(self):
        past = BookingFactory(
            availability=self.window,
            student=self.student,
            start_time_utc=self.mine.start_time_utc - timedelta(days=7),
            status=BookingStatus.COMPLETED,
        )
        future = BookingFactory(
            availability=self.window,
            student=self.student,
            start_time_utc=self.mine.start_time_utc + timedelta(days=7),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(MINE_URL)
        self.assertEqual(
            [b["id"] for b in response.data], [past.pk, self.mine.pk, future.pk]
        )

    def test_a_cancelled_booking_stays_visible(self):
        """History, not noise — payouts and attendance need it later."""
        cancelled = CancelledBookingFactory(
            availability=self.window,
            student=self.student,
            start_time_utc=slot_at(self.window, 120),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(MINE_URL)
        by_id = {b["id"]: b for b in response.data}
        self.assertEqual(by_id[cancelled.pk]["status"], BookingStatus.CANCELLED)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_student_has_no_bookings_of_their_own(self):
        for user in (self.window.teacher, ParentFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(MINE_URL)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TeachingBookingsAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory()
        self.teacher = self.window.teacher
        self.booking = BookingFactory(availability=self.window)

    def test_a_teacher_sees_the_sessions_they_teach(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(TEACHING_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([b["id"] for b in response.data], [self.booking.pk])
        self.assertEqual(
            response.data[0]["student"]["id"], self.booking.student.pk
        )

    def test_a_teacher_does_not_see_another_teachers_sessions(self):
        BookingFactory()  # its own window, its own teacher
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(TEACHING_URL)
        self.assertEqual([b["id"] for b in response.data], [self.booking.pk])

    def test_a_teacher_with_nothing_booked_sees_an_empty_list(self):
        self.client.force_authenticate(user=BookableTeacherFactory())
        response = self.client.get(TEACHING_URL)
        self.assertEqual(list(response.data), [])

    def test_a_student_cannot_use_the_teaching_endpoint(self):
        for user in (self.booking.student, ParentFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(TEACHING_URL)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(TEACHING_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class BookingCancelAPITests(APITestCase):
    """Acceptance criterion 6 — either party cancels, and the row survives."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.booking = BookingFactory(availability=self.window)
        self.student = self.booking.student
        self.teacher = self.booking.teacher

    def assert_cancelled(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], BookingStatus.CANCELLED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CANCELLED)
        # The row is kept: history for payouts and attendance.
        self.assertTrue(Booking.objects.filter(pk=self.booking.pk).exists())
        self.assertEqual(Booking.objects.count(), 1)

    def test_the_student_can_cancel(self):
        self.client.force_authenticate(user=self.student)
        self.assert_cancelled(self.client.post(cancel_url(self.booking)))

    def test_the_teacher_can_cancel(self):
        self.client.force_authenticate(user=self.teacher)
        self.assert_cancelled(self.client.post(cancel_url(self.booking)))

    def test_a_linked_parent_can_cancel(self):
        """A parent may book, so a parent may cancel."""
        link = ParentLinkFactory()
        self.booking = BookingFactory(
            availability=self.window,
            student=link.student,
            start_time_utc=slot_at(self.window, 120),
        )
        Booking.objects.exclude(pk=self.booking.pk).delete()
        self.client.force_authenticate(user=link.parent)
        self.assert_cancelled(self.client.post(cancel_url(self.booking)))

    def test_the_room_name_is_unchanged_by_cancelling(self):
        original = self.booking.video_room_name
        self.client.force_authenticate(user=self.student)
        response = self.client.post(cancel_url(self.booking))
        self.assertEqual(response.data["video_room_name"], original)

    def test_a_stranger_gets_a_404_rather_than_a_403(self):
        """A 403 would confirm the booking exists; a 404 does not."""
        for user in (StudentFactory(), ParentFactory(), BookableTeacherFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(cancel_url(self.booking))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.SCHEDULED)

    def test_an_unlinked_parent_cannot_cancel(self):
        ParentLinkFactory(student=self.student)  # some *other* child's parent
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.post(cancel_url(self.booking))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.post(cancel_url(self.booking))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_cancelling_an_unknown_booking_is_a_404(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(cancel_url(999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancelling_twice_is_a_409(self):
        cancelled = CancelledBookingFactory(
            availability=self.window, start_time_utc=slot_at(self.window, 120)
        )
        self.client.force_authenticate(user=cancelled.student)
        response = self.client.post(cancel_url(cancelled))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_cancelling_a_completed_session_is_a_409(self):
        completed = CompletedBookingFactory(
            availability=self.window, start_time_utc=slot_at(self.window, 180)
        )
        self.client.force_authenticate(user=completed.student)
        response = self.client.post(cancel_url(completed))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

        completed.refresh_from_db()
        self.assertEqual(completed.status, BookingStatus.COMPLETED)

    def test_cancelling_frees_the_slot_for_a_new_booking(self):
        """Cancel-and-rebook is the supported reschedule path this phase."""
        self.client.force_authenticate(user=self.student)
        self.client.post(cancel_url(self.booking))

        response = self.client.post(
            BOOKINGS_URL,
            {
                "teacher": self.teacher.pk,
                "level": self.booking.level.pk,
                "start_time_utc": self.booking.start_time_utc.isoformat(),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(
            response.data["video_room_name"], self.booking.video_room_name
        )
