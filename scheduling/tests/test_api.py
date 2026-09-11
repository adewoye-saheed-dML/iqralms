"""API tests for the scheduling app — academy-scoped endpoints.

Every endpoint gets a happy path and at least one failure case, per CLAUDE.md.
Acceptance criteria from specs/phase-3-scheduling.md covered here: 2 (a booking
inside a declared window succeeds, outside is rejected), 3 (an overlapping
booking for the same teacher is rejected), 4 (an unapproved teacher is
rejected), 5 (``video_room_name`` and the Jitsi join URL come back), 6
(cancelling sets the status and keeps the row) and 7 (a student cannot see
another student's bookings).

Task 4.9 adds tenant authorization: all scheduling endpoints live under
/api/scheduling/organizations/<organization_pk>/... and enforce active
membership, role gates, and foreign tenant rejection.
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
from curriculum.tests.factories import LevelFactory, admit
from organizations.tests.factories import OrganizationFactory
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


def _url(name, organization, **kwargs):
    org_pk = getattr(organization, "pk", organization)
    return reverse(f"scheduling:{name}", kwargs={"organization_pk": org_pk, **kwargs})


def availability_url(organization):
    return _url("availability-list", organization)


def bookings_url(organization):
    return _url("booking-create", organization)


def mine_url(organization):
    return _url("booking-mine", organization)


def teaching_url(organization):
    return _url("booking-teaching", organization)


def cancel_url(organization, booking_or_pk):
    pk = getattr(booking_or_pk, "pk", booking_or_pk)
    return _url("booking-cancel", organization, pk=pk)


class AvailabilityListAPITests(APITestCase):
    def setUp(self):
        self.organization = OrganizationFactory()
        self.teacher = BookableTeacherFactory(timezone="Africa/Lagos")
        self.window = AvailabilityFactory(
            organization=self.organization,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(8, 0),
            end_time_utc=time(16, 0),
        )
        self.member = admit(StudentFactory(), self.organization).user
        self.url = availability_url(self.organization)

    def test_listing_a_teachers_windows_requires_active_membership(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        payload = response.data[0]
        self.assertEqual(payload["teacher"], self.teacher.pk)
        self.assertEqual(payload["teacher_username"], self.teacher.username)
        self.assertEqual(payload["weekday"], Weekday.MONDAY)
        self.assertEqual(payload["weekday_display"], "Monday")
        self.assertEqual(payload["start_time_utc"], "08:00:00")
        self.assertEqual(payload["end_time_utc"], "16:00:00")

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        outsider = StudentFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_authenticated_caller_sees_their_own_zone(self):
        viewer = admit(
            StudentFactory(timezone="Asia/Karachi"), self.organization
        ).user
        self.client.force_authenticate(user=viewer)
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        local = response.json()[0]["local"]
        self.assertEqual(local["timezone"], "Asia/Karachi")
        self.assertEqual(local["start_time"], "13:00:00")  # 08:00 UTC + 5
        self.assertEqual(local["end_time"], "21:00:00")

    def test_the_local_weekday_is_reported_alongside_the_times(self):
        """A conversion can cross midnight, so the weekday may differ from UTC."""
        teacher = BookableTeacherFactory(timezone="Pacific/Auckland")
        AvailabilityFactory(
            organization=self.organization,
            teacher=teacher,
            weekday=Weekday.SUNDAY,
            start_time_utc=time(21, 0),
            end_time_utc=time(23, 0),
        )
        viewer = admit(
            StudentFactory(timezone="Pacific/Auckland"), self.organization
        ).user
        self.client.force_authenticate(user=viewer)
        response = self.client.get(self.url, {"teacher_id": teacher.pk})
        payload = response.json()[0]
        self.assertEqual(payload["weekday"], Weekday.SUNDAY)
        self.assertEqual(payload["local"]["weekday"], Weekday.MONDAY)

    def test_windows_come_back_ordered_by_day_then_start(self):
        AvailabilityFactory(
            organization=self.organization,
            teacher=self.teacher,
            weekday=Weekday.MONDAY,
            start_time_utc=time(3, 0),
            end_time_utc=time(5, 0),
        )
        AvailabilityFactory(
            organization=self.organization,
            teacher=self.teacher,
            weekday=Weekday.SATURDAY,
            start_time_utc=time(6, 0),
            end_time_utc=time(7, 0),
        )
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        self.assertEqual(
            [(w["weekday"], w["start_time_utc"]) for w in response.data],
            [(0, "03:00:00"), (0, "08:00:00"), (5, "06:00:00")],
        )

    def test_only_the_requested_teachers_windows_come_back(self):
        AvailabilityFactory(organization=self.organization)  # different teacher
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": self.teacher.pk})
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["teacher"], self.teacher.pk)

    def test_a_teacher_from_another_organization_returns_empty_list(self):
        foreign_avail = AvailabilityFactory()  # different organization
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": foreign_avail.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_teacher_id_is_required(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_a_non_numeric_teacher_id_is_a_400(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": "abc"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_an_unknown_teacher_is_an_empty_list_not_a_404(self):
        self.client.force_authenticate(user=self.member)
        response = self.client.get(self.url, {"teacher_id": 999999})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])


class BookingCreateAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=Weekday.MONDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.organization = self.window.organization
        self.teacher = self.window.teacher
        self.level = LevelFactory(track__organization=self.organization)
        teaches(self.teacher, self.level)
        self.student = admit(
            StudentFactory(timezone="Africa/Lagos"), self.organization
        ).user
        self.url = bookings_url(self.organization)

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
        response = self.client.post(self.url, self.payload(120))

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
        response = self.client.post(self.url, self.payload())
        self.assertEqual(
            response.data["video_join_url"],
            f"https://meet.jit.si/{response.data['video_room_name']}",
        )

    def test_two_bookings_get_distinct_rooms(self):
        """Acceptance criterion 5, second half."""
        self.client.force_authenticate(user=self.student)
        first = self.client.post(self.url, self.payload(0))
        second = self.client.post(self.url, self.payload(60))
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(
            first.data["video_room_name"], second.data["video_room_name"]
        )

    def test_the_response_renders_the_start_in_the_callers_zone(self):
        self.client.force_authenticate(user=self.student)  # Africa/Lagos, UTC+1
        response = self.client.post(self.url, self.payload())
        self.assertIn("+01:00", response.data["start_time_local"])

    def test_the_end_time_and_track_are_derived_for_the_client(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(duration_minutes=45))
        self.assertEqual(response.data["duration_minutes"], 45)
        self.assertEqual(response.data["track"], self.level.track.slug)

    def test_a_parent_books_for_a_linked_child(self):
        link = ParentLinkFactory()
        admit(link.parent, self.organization)
        admit(link.student, self.organization)
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            self.url, self.payload(student=link.student.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], link.student.pk)

    def test_a_student_may_name_themselves_explicitly(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(student=self.student.pk))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    # --- Who may book -------------------------------------------------------

    def test_an_anonymous_caller_cannot_book(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Booking.objects.exists())

    def test_a_non_member_cannot_book(self):
        outsider = StudentFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_cannot_book_a_session(self):
        for user in (
            admit(SubTeacherFactory(), self.organization).user,
            admit(LeadTeacherFactory(), self.organization).user,
        ):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(self.url, self.payload())
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_student_cannot_book_for_someone_else(self):
        other = admit(StudentFactory(), self.organization).user
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(student=other.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_parent_must_say_which_child(self):
        parent = admit(ParentFactory(), self.organization).user
        self.client.force_authenticate(user=parent)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_a_parent_cannot_book_for_an_unlinked_student(self):
        parent = admit(ParentFactory(), self.organization).user
        unlinked = admit(StudentFactory(), self.organization).user
        self.client.force_authenticate(user=parent)
        response = self.client.post(
            self.url, self.payload(student=unlinked.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_parent_cannot_book_for_a_child_in_another_organization(self):
        link = ParentLinkFactory()
        admit(link.parent, self.organization)
        # child is NOT admitted to self.organization
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            self.url, self.payload(student=link.student.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_minor_with_no_linked_parent_cannot_be_booked(self):
        minor = admit(MinorStudentFactory(), self.organization).user
        self.client.force_authenticate(user=minor)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    # --- Slot rules, surfaced from the model as 400s ------------------------

    def test_a_booking_outside_the_declared_window_is_rejected(self):
        """Acceptance criterion 2, second half, over HTTP."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(-60))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_an_overlapping_booking_for_the_same_teacher_is_rejected(self):
        """Acceptance criterion 3, over HTTP."""
        BookingFactory(
            availability=self.window,
            level=self.level,
            start_time_utc=slot_at(self.window, 120),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(135))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Booking.objects.count(), 1)

    def test_a_neighbouring_booking_for_the_same_teacher_succeeds(self):
        """Acceptance criterion 3, second half, over HTTP."""
        BookingFactory(
            availability=self.window,
            level=self.level,
            start_time_utc=slot_at(self.window, 120),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(150))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Booking.objects.count(), 2)

    def test_a_booking_in_the_past_is_rejected(self):
        """Phase 3.5 — the past-start rule surfaces as a 400, not a 500."""
        two_weeks_ago = (dj_timezone.now() - timedelta(days=14)).date()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url,
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
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_booking_against_a_non_teacher_is_rejected(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(teacher=StudentFactory().pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_a_booking_against_a_teacher_from_another_academy_is_rejected(self):
        foreign_teacher = BookableTeacherFactory()
        AvailabilityFactory(teacher=foreign_teacher)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(teacher=foreign_teacher.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_a_booking_with_a_level_from_another_academy_is_rejected(self):
        foreign_level = LevelFactory()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(level=foreign_level.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_an_unknown_teacher_or_level_is_a_400(self):
        self.client.force_authenticate(user=self.student)
        for field in ("teacher", "level"):
            with self.subTest(field=field):
                response = self.client.post(
                    self.url, self.payload(**{field: 999999})
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_a_zero_length_session_is_rejected(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(duration_minutes=0))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("duration_minutes", response.data)

    def test_the_client_cannot_choose_its_own_room_name(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(video_room_name="guessable-room")
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(response.data["video_room_name"], "guessable-room")

    def test_the_client_cannot_create_an_already_completed_booking(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(status=BookingStatus.COMPLETED)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], BookingStatus.SCHEDULED)


class MyBookingsAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory()
        self.organization = self.window.organization
        self.level = LevelFactory(track__organization=self.organization)
        self.student = admit(StudentFactory(), self.organization).user
        self.mine = BookingFactory(
            availability=self.window, student=self.student, level=self.level
        )
        self.url = mine_url(self.organization)

    def test_a_student_sees_their_own_bookings(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([b["id"] for b in response.data], [self.mine.pk])

    def test_a_student_cannot_see_another_students_bookings(self):
        """Acceptance criterion 7."""
        intruder = admit(StudentFactory(), self.organization).user
        self.client.force_authenticate(user=intruder)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_student_does_not_see_bookings_from_another_academy(self):
        other_window = AvailabilityFactory()
        other_level = LevelFactory(track__organization=other_window.organization)
        admit(self.student, other_window.organization)
        BookingFactory(
            availability=other_window, student=self.student, level=other_level
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual([b["id"] for b in response.data], [self.mine.pk])

    def test_past_and_upcoming_bookings_both_appear_earliest_first(self):
        past = BookingFactory(
            availability=self.window,
            student=self.student,
            level=self.level,
            start_time_utc=self.mine.start_time_utc - timedelta(days=7),
            status=BookingStatus.COMPLETED,
        )
        future = BookingFactory(
            availability=self.window,
            student=self.student,
            level=self.level,
            start_time_utc=self.mine.start_time_utc + timedelta(days=7),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(
            [b["id"] for b in response.data], [past.pk, self.mine.pk, future.pk]
        )

    def test_a_cancelled_booking_stays_visible(self):
        """History, not noise — payouts and attendance need it later."""
        cancelled = CancelledBookingFactory(
            availability=self.window,
            student=self.student,
            level=self.level,
            start_time_utc=slot_at(self.window, 120),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        by_id = {b["id"]: b for b in response.data}
        self.assertEqual(by_id[cancelled.pk]["status"], BookingStatus.CANCELLED)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        outsider = StudentFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_non_student_has_no_bookings_of_their_own(self):
        for user in (
            self.window.teacher,
            admit(ParentFactory(), self.organization).user,
        ):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TeachingBookingsAPITests(APITestCase):
    def setUp(self):
        self.window = AvailabilityFactory()
        self.organization = self.window.organization
        self.teacher = self.window.teacher
        self.level = LevelFactory(track__organization=self.organization)
        self.booking = BookingFactory(
            availability=self.window, level=self.level
        )
        self.url = teaching_url(self.organization)

    def test_a_teacher_sees_the_sessions_they_teach(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([b["id"] for b in response.data], [self.booking.pk])
        self.assertEqual(
            response.data[0]["student"]["id"], self.booking.student.pk
        )

    def test_a_teacher_does_not_see_another_teachers_sessions(self):
        BookingFactory(
            availability=AvailabilityFactory(organization=self.organization),
            level=self.level,
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual([b["id"] for b in response.data], [self.booking.pk])

    def test_a_teacher_does_not_see_sessions_from_another_academy(self):
        other_window = AvailabilityFactory(
            teacher=self.teacher,
            weekday=Weekday.TUESDAY,
        )
        other_level = LevelFactory(track__organization=other_window.organization)
        BookingFactory(availability=other_window, level=other_level)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual([b["id"] for b in response.data], [self.booking.pk])

    def test_a_teacher_with_nothing_booked_sees_an_empty_list(self):
        other_teacher = BookableTeacherFactory()
        admit(other_teacher, self.organization)
        self.client.force_authenticate(user=other_teacher)
        response = self.client.get(self.url)
        self.assertEqual(list(response.data), [])

    def test_a_student_cannot_use_the_teaching_endpoint(self):
        for user in (
            self.booking.student,
            admit(ParentFactory(), self.organization).user,
        ):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        outsider_teacher = BookableTeacherFactory()
        self.client.force_authenticate(user=outsider_teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class BookingCancelAPITests(APITestCase):
    """Acceptance criterion 6 — either party cancels, and the row survives."""

    def setUp(self):
        self.window = AvailabilityFactory()
        self.organization = self.window.organization
        self.level = LevelFactory(track__organization=self.organization)
        self.booking = BookingFactory(
            availability=self.window, level=self.level
        )
        self.student = self.booking.student
        self.teacher = self.booking.teacher
        admit(self.student, self.organization)
        admit(self.teacher, self.organization)

    def assert_cancelled(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], BookingStatus.CANCELLED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.CANCELLED)
        self.assertTrue(Booking.objects.filter(pk=self.booking.pk).exists())
        self.assertEqual(Booking.objects.count(), 1)

    def test_the_student_can_cancel(self):
        self.client.force_authenticate(user=self.student)
        self.assert_cancelled(
            self.client.post(cancel_url(self.organization, self.booking))
        )

    def test_the_teacher_can_cancel(self):
        self.client.force_authenticate(user=self.teacher)
        self.assert_cancelled(
            self.client.post(cancel_url(self.organization, self.booking))
        )

    def test_a_linked_parent_can_cancel(self):
        link = ParentLinkFactory()
        admit(link.parent, self.organization)
        admit(link.student, self.organization)
        self.booking = BookingFactory(
            availability=self.window,
            student=link.student,
            level=self.level,
            start_time_utc=slot_at(self.window, 120),
        )
        Booking.objects.exclude(pk=self.booking.pk).delete()
        self.client.force_authenticate(user=link.parent)
        self.assert_cancelled(
            self.client.post(cancel_url(self.organization, self.booking))
        )

    def test_the_room_name_is_unchanged_by_cancelling(self):
        original = self.booking.video_room_name
        self.client.force_authenticate(user=self.student)
        response = self.client.post(cancel_url(self.organization, self.booking))
        self.assertEqual(response.data["video_room_name"], original)

    def test_a_stranger_gets_a_404_rather_than_a_403(self):
        stranger_student = admit(StudentFactory(), self.organization).user
        stranger_parent = admit(ParentFactory(), self.organization).user
        stranger_teacher = admit(BookableTeacherFactory(), self.organization).user
        for user in (stranger_student, stranger_parent, stranger_teacher):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    cancel_url(self.organization, self.booking)
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, BookingStatus.SCHEDULED)

    def test_cancelling_under_wrong_organization_is_a_404(self):
        other_org = OrganizationFactory()
        admit(self.student, other_org)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(cancel_url(other_org, self.booking))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unlinked_parent_cannot_cancel(self):
        ParentLinkFactory(student=self.student)  # different child
        unlinked_parent = admit(ParentFactory(), self.organization).user
        self.client.force_authenticate(user=unlinked_parent)
        response = self.client.post(cancel_url(self.organization, self.booking))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.post(cancel_url(self.organization, self.booking))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        outsider = StudentFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.post(cancel_url(self.organization, self.booking))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cancelling_an_unknown_booking_is_a_404(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(cancel_url(self.organization, 999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cancelling_twice_is_a_409(self):
        cancelled = CancelledBookingFactory(
            availability=self.window,
            level=self.level,
            start_time_utc=slot_at(self.window, 120),
        )
        self.client.force_authenticate(user=cancelled.student)
        response = self.client.post(cancel_url(self.organization, cancelled))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_cancelling_a_completed_session_is_a_409(self):
        completed = CompletedBookingFactory(
            availability=self.window,
            level=self.level,
            start_time_utc=slot_at(self.window, 180),
        )
        self.client.force_authenticate(user=completed.student)
        response = self.client.post(cancel_url(self.organization, completed))
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

        completed.refresh_from_db()
        self.assertEqual(completed.status, BookingStatus.COMPLETED)

    def test_cancelling_frees_the_slot_for_a_new_booking(self):
        """Cancel-and-rebook is the supported reschedule path this phase."""
        self.client.force_authenticate(user=self.student)
        self.client.post(cancel_url(self.organization, self.booking))

        response = self.client.post(
            bookings_url(self.organization),
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
