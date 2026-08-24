"""API tests for Phase 4 — routing, cohorts, and the specialty rule over HTTP.

Acceptance criteria from specs/phase-4-routing.md covered here: 2, 3, 4, 5 and 6
through ``POST /route/``, and **7** — "a direct booking against a level outside
the teacher's specialties is now rejected — this is the tech-debt closure, prove
it actually closed" — through Phase 3's own endpoint.

The model layer proves the same rules in test_routing.py, test_cohorts.py and
test_capacity.py. Both layers matter, for the reason Phase 3 set out: the rules
live in ``clean()`` so they hold for direct ORM writes, and these tests prove the
API surfaces them as 400s and 409s rather than 500s.
"""

from datetime import time, timedelta

from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory, TrackFactory
from scheduling.models import Availability, Booking, Cohort, RoutedReason
from scheduling.utils import week_bounds

from .factories import (
    DEFAULT_WEEKDAY,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    AvailabilityFactory,
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    slot_at,
    teaches,
)

BOOKINGS_URL = reverse("scheduling:booking-create")
ROUTE_URL = reverse("scheduling:route")
COHORTS_URL = reverse("scheduling:cohort-create")
OPEN_COHORTS_URL = reverse("scheduling:cohort-open")


def next_monday():
    """The start of a week that has not begun yet — see test_routing.next_monday."""
    return week_bounds(dj_timezone.now())[1]


class SpecialtyEnforcementAPITests(APITestCase):
    """Acceptance criterion 7 — the Phase 3 tech-debt item, proved closed.

    The setup is the exact scenario tech-debt.md described: "a parent can book a
    hifz teacher for an Arabic level and nothing objects." Both halves are
    asserted, because a rule that rejected *everything* would also pass the
    rejection test on its own.
    """

    def setUp(self):
        self.window = AvailabilityFactory(
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        self.taught = LevelFactory(track=TrackFactory(name="Tajweed", slug="tajweed"))
        self.not_taught = LevelFactory(track=TrackFactory(name="Hifz", slug="hifz"))
        teaches(self.teacher, self.taught)
        self.student = StudentFactory()

    def payload(self, level, **overrides):
        body = {
            "teacher": self.teacher.pk,
            "level": level.pk,
            "start_time_utc": slot_at(self.window, 120).isoformat(),
        }
        body.update(overrides)
        return body

    def test_a_direct_booking_outside_the_teachers_specialties_is_rejected(self):
        """Acceptance criterion 7."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(self.not_taught))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_direct_booking_within_the_teachers_specialties_succeeds(self):
        """The other half: the rule discriminates rather than refusing everything."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(self.taught))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["level"]["id"], self.taught.pk)

    def test_a_parent_cannot_book_outside_the_teachers_specialties_either(self):
        """The tech-debt entry's own wording was about a parent booking."""
        link = ParentLinkFactory()
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            BOOKINGS_URL, self.payload(self.not_taught, student=link.student.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_with_no_specialties_recorded_teaches_nothing(self):
        """The strict reading, and the right default for a quality gate.

        Recorded in tech-debt.md as an onboarding consequence: an existing teacher
        is unbookable until their tracks are set. Asserted so it is a decision
        rather than a surprise.
        """
        blank = BookableTeacherFactory()
        AvailabilityFactory(
            teacher=blank,
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL, self.payload(self.taught, teacher=blank.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_direct_booking_records_student_choice(self):
        """Naming your own teacher *is* ``student_choice``, per the spec."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(BOOKINGS_URL, self.payload(self.taught))
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)

    def test_a_client_cannot_claim_a_routing_reason_for_a_direct_booking(self):
        """``routed_reason`` is not a client field; a supplied value is ignored."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL,
            self.payload(self.taught, routed_reason=RoutedReason.LEAD_AVAILABLE),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)

    def test_a_client_cannot_attach_a_direct_booking_to_a_cohort(self):
        """A seat is routing's to give — a self-declared one would skip the cap."""
        cohort = CohortFactory()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            BOOKINGS_URL, self.payload(self.taught, cohort=cohort.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["cohort"])
        self.assertEqual(cohort.seats_taken, 0)


class RouteAPIWorld(APITestCase):
    """Shared world-building for the routing endpoint tests."""

    def setUp(self):
        self.level = LevelFactory()
        self.student = StudentFactory(timezone="Africa/Lagos")
        self.slot = next_monday() + timedelta(hours=10)

    def available_teacher(self, *, lead=False, hours=20, teaches_level=True, level=None):
        teacher = BookableLeadTeacherFactory() if lead else BookableTeacherFactory()
        profile = teacher.teacher_profile
        profile.max_weekly_hours = hours
        profile.save()
        if teaches_level:
            teaches(teacher, level or self.level)
        for weekday in range(7):
            Availability.objects.create(
                teacher=teacher,
                weekday=weekday,
                start_time_utc=time(0, 0),
                end_time_utc=time.max,
            )
        return teacher

    def fill_week(self, teacher, minutes):
        placed, hour = 0, 0
        while placed < minutes:
            chunk = min(60, minutes - placed)
            Booking.objects.create(
                student=StudentFactory(),
                teacher=teacher,
                level=self.level,
                start_time_utc=self.slot + timedelta(days=1, hours=hour),
                duration_minutes=chunk,
            )
            placed += chunk
            hour += 2
        return teacher

    def payload(self, **overrides):
        body = {
            "level": self.level.pk,
            "requested_time_window": {
                "start_time_utc": self.slot.isoformat(),
                "duration_minutes": 30,
            },
        }
        body.update(overrides)
        return body

    def post_route(self, user=None, **overrides):
        self.client.force_authenticate(user=user or self.student)
        return self.client.post(ROUTE_URL, self.payload(**overrides), format="json")


class RouteAPITests(RouteAPIWorld):
    """Criteria 2 to 5 over HTTP — the system's choice, and how it reports it."""

    def test_a_request_within_the_leads_capacity_routes_to_the_lead(self):
        """Acceptance criterion 3, over HTTP."""
        lead = self.available_teacher(lead=True)
        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["routed"])
        self.assertEqual(response.data["routed_reason"], RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(response.data["booking"]["teacher"]["id"], lead.pk)
        self.assertIsNone(response.data["cohort"])

    def test_the_routed_booking_comes_back_ready_to_attend(self):
        """A room and a join URL, the same as a directly booked session."""
        self.available_teacher(lead=True)
        response = self.post_route()

        booking = response.data["booking"]
        self.assertTrue(booking["video_room_name"])
        self.assertIn(booking["video_room_name"], booking["video_join_url"])
        self.assertEqual(booking["status"], "scheduled")
        self.assertEqual(booking["student"]["id"], self.student.pk)

    def test_the_response_renders_the_start_in_the_callers_zone(self):
        self.available_teacher(lead=True)
        response = self.post_route()
        self.assertIn("+01:00", response.data["booking"]["start_time_local"])

    def test_an_open_cohort_takes_the_student(self):
        """Acceptance criterion 2, over HTTP."""
        self.level = GroupEligibleLevelFactory()
        lead = self.available_teacher(lead=True)
        cohort_teacher = self.available_teacher()
        cohort = CohortFactory(
            availability=cohort_teacher.availability_windows.first(),
            teacher=cohort_teacher,
            level=self.level,
            schedule_start_utc=self.slot,
        )

        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(response.data["cohort"]["id"], cohort.pk)
        self.assertEqual(response.data["cohort"]["seats_taken"], 1)
        self.assertEqual(response.data["booking"]["cohort"], cohort.pk)
        self.assertIn(self.student, cohort.students.all())
        self.assertEqual(Booking.objects.filter(teacher=lead).count(), 0)

    def test_a_full_lead_routes_to_a_sub_teacher(self):
        """Acceptance criterion 4, over HTTP."""
        lead = self.available_teacher(lead=True, hours=1)
        sub = self.available_teacher(hours=10)
        self.fill_week(lead, 60)

        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.LEAD_FULL_ROUTED)
        self.assertEqual(response.data["booking"]["teacher"]["id"], sub.pk)

    def test_a_sub_over_their_cap_is_never_selected(self):
        """Acceptance criterion 5, over HTTP.

        The over-capacity sub is the only specialist, so a 409 here is the cap
        holding rather than a shortage of candidates.
        """
        lead = self.available_teacher(lead=True, hours=1)
        self.fill_week(lead, 60)
        full_sub = self.available_teacher(hours=1)
        self.fill_week(full_sub, 60)

        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Booking.objects.filter(start_time_utc=self.slot).exists())

    def test_the_sub_with_most_remaining_capacity_is_chosen(self):
        lead = self.available_teacher(lead=True, hours=1)
        self.fill_week(lead, 60)
        busier = self.available_teacher(hours=10)
        self.fill_week(busier, 8 * 60)
        emptier = self.available_teacher(hours=10)

        response = self.post_route()

        self.assertEqual(response.data["booking"]["teacher"]["id"], emptier.pk)

    def test_a_parent_can_route_for_a_linked_child(self):
        self.available_teacher(lead=True)
        link = ParentLinkFactory()

        response = self.post_route(user=link.parent, student=link.student.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["student"]["id"], link.student.pk)

    def test_the_default_duration_is_applied(self):
        self.available_teacher(lead=True)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            ROUTE_URL,
            {
                "level": self.level.pk,
                "requested_time_window": {"start_time_utc": self.slot.isoformat()},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["duration_minutes"], 30)


class RouteNoCapacityAPITests(RouteAPIWorld):
    """Acceptance criterion 6 over HTTP — an honest refusal, not a forced booking."""

    def test_no_capacity_anywhere_is_a_structured_409(self):
        """Acceptance criterion 6."""
        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(response.data["routed"])
        self.assertEqual(response.data["reason"], "no_capacity")
        self.assertTrue(response.data["detail"])
        self.assertEqual(
            set(response.data["considered"]),
            {"cohort", "lead", "sub_teachers"},
            "the body must say what each of the three steps found",
        )
        self.assertFalse(Booking.objects.exists())

    def test_the_refusal_explains_each_step(self):
        """What makes the 409 showable to a parent rather than a bare error."""
        lead = self.available_teacher(lead=True, hours=1)
        self.fill_week(lead, 60)
        sub = self.available_teacher(hours=1)
        self.fill_week(sub, 60)

        considered = self.post_route().data["considered"]

        self.assertIn("not group-eligible", considered["cohort"])
        self.assertIn(lead.username, considered["lead"])
        self.assertIn(sub.username, considered["sub_teachers"])

    def test_a_request_for_a_past_slot_is_refused(self):
        """Routing is not a way around the Phase 3.5 past-start rule."""
        self.available_teacher(lead=True)
        past = (dj_timezone.now() - timedelta(days=1)).isoformat()

        response = self.post_route(
            requested_time_window={"start_time_utc": past, "duration_minutes": 30}
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Booking.objects.exists())

    def test_nobody_is_booked_outside_their_declared_hours(self):
        lead = BookableLeadTeacherFactory()
        teaches(lead, self.level)
        Availability.objects.create(
            teacher=lead,
            weekday=self.slot.weekday(),
            start_time_utc=time(3, 0),
            end_time_utc=time(4, 0),
        )

        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Booking.objects.exists())


class RouteAPIPermissionTests(RouteAPIWorld):
    """Who may ask the system to route, and what a bad request looks like."""

    def test_an_anonymous_caller_cannot_route(self):
        self.available_teacher(lead=True)
        response = self.client.post(ROUTE_URL, self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_cannot_route_a_session_for_themselves(self):
        self.available_teacher(lead=True)
        for user in (SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                response = self.post_route(user=user)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_student_cannot_route_for_someone_else(self):
        self.available_teacher(lead=True)
        response = self.post_route(student=StudentFactory().pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_parent_must_say_which_child(self):
        self.available_teacher(lead=True)
        response = self.post_route(user=ParentFactory())
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_a_parent_cannot_route_for_an_unlinked_student(self):
        self.available_teacher(lead=True)
        response = self.post_route(
            user=ParentFactory(), student=StudentFactory().pk
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_an_unknown_level_is_a_400(self):
        response = self.post_route(level=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_a_missing_time_window_is_a_400(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            ROUTE_URL, {"level": self.level.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("requested_time_window", response.data)

    def test_a_zero_length_session_is_a_400(self):
        self.available_teacher(lead=True)
        response = self.post_route(
            requested_time_window={
                "start_time_utc": self.slot.isoformat(),
                "duration_minutes": 0,
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_client_cannot_name_a_teacher_here(self):
        """Naming a teacher is the *other* endpoint. This one decides for you.

        An extra key is ignored rather than honoured, so a client cannot smuggle a
        preferred teacher past the routing order — the preferred-teacher waitlist
        the mvp-spec describes is a later phase, and doing it silently here would
        be exactly the "quietly redirected" behaviour that spec warns against.
        """
        lead = self.available_teacher(lead=True)
        sub = self.available_teacher()
        response = self.post_route(teacher=sub.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["teacher"]["id"], lead.pk)


class CohortCreateAPITests(APITestCase):
    """``POST /cohorts/`` — the lead opens a group class."""

    def setUp(self):
        self.lead = BookableLeadTeacherFactory()
        self.window = AvailabilityFactory(
            teacher=BookableTeacherFactory(),
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.teacher = self.window.teacher
        self.level = GroupEligibleLevelFactory()
        teaches(self.teacher, self.level)

    def payload(self, **overrides):
        body = {
            "teacher": self.teacher.pk,
            "level": self.level.pk,
            "max_students": 4,
            "schedule_start_utc": slot_at(self.window).isoformat(),
        }
        body.update(overrides)
        return body

    def test_the_lead_creates_a_cohort(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(COHORTS_URL, self.payload())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["teacher"]["id"], self.teacher.pk)
        self.assertEqual(response.data["level"]["id"], self.level.pk)
        self.assertEqual(response.data["max_students"], 4)
        self.assertEqual(response.data["seats_taken"], 0)
        self.assertEqual(response.data["seats_available"], 4)
        self.assertTrue(Cohort.objects.filter(pk=response.data["id"]).exists())

    def test_max_students_defaults_when_omitted(self):
        self.client.force_authenticate(user=self.lead)
        body = self.payload()
        del body["max_students"]
        response = self.client.post(COHORTS_URL, body)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["max_students"], 6)

    def test_a_non_group_eligible_level_is_rejected(self):
        plain = LevelFactory(group_eligible=False)
        teaches(self.teacher, plain)
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(COHORTS_URL, self.payload(level=plain.pk))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(Cohort.objects.exists())

    def test_a_teacher_who_does_not_teach_the_track_is_rejected(self):
        other = BookableTeacherFactory()
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(COHORTS_URL, self.payload(teacher=other.pk))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_a_sub_teacher_cannot_create_a_cohort(self):
        """A cohort commits a teacher's time, so it is the lead's call."""
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(COHORTS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Cohort.objects.exists())

    def test_a_student_or_parent_cannot_create_a_cohort(self):
        for user in (StudentFactory(), ParentFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(COHORTS_URL, self.payload())
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_create_a_cohort(self):
        response = self.client.post(COHORTS_URL, self.payload())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_students_cannot_be_seated_at_creation_time(self):
        """A member with no seat booking would have nothing to attend."""
        student = StudentFactory()
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(COHORTS_URL, self.payload(students=[student.pk]))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Cohort.objects.get(pk=response.data["id"]).seats_taken, 0)


class OpenCohortListAPITests(APITestCase):
    """``GET /cohorts/open/?level_id=`` — what has a seat left."""

    def setUp(self):
        self.student = StudentFactory(timezone="Asia/Karachi")
        self.cohort = CohortFactory(max_students=2)
        self.level = self.cohort.level

    def get(self, **params):
        self.client.force_authenticate(user=self.student)
        return self.client.get(OPEN_COHORTS_URL, params)

    def test_an_open_cohort_is_listed(self):
        response = self.get(level_id=self.level.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.cohort.pk])
        self.assertEqual(response.data[0]["seats_available"], 2)
        self.assertEqual(response.data[0]["track"], self.level.track.slug)

    def test_a_full_cohort_is_not_listed(self):
        self.cohort.add_student(StudentFactory())
        self.cohort.add_student(StudentFactory())
        self.assertEqual(list(self.get(level_id=self.level.pk).data), [])

    def test_a_partly_filled_cohort_is_still_listed(self):
        self.cohort.add_student(StudentFactory())
        response = self.get(level_id=self.level.pk)
        self.assertEqual(response.data[0]["seats_taken"], 1)
        self.assertEqual(response.data[0]["seats_available"], 1)

    def test_another_levels_cohorts_are_not_listed(self):
        CohortFactory()
        self.assertEqual(
            [c["id"] for c in self.get(level_id=self.level.pk).data], [self.cohort.pk]
        )

    def test_the_roster_is_not_published(self):
        """Who else is in a class is not this endpoint's business."""
        self.cohort.add_student(StudentFactory())
        payload = self.get(level_id=self.level.pk).data[0]
        self.assertNotIn("students", payload)

    def test_the_start_is_rendered_in_the_callers_zone(self):
        response = self.get(level_id=self.level.pk)
        self.assertIn("+05:00", response.data[0]["schedule_start_local"])

    def test_level_id_is_required(self):
        response = self.get()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level_id", response.data)

    def test_a_non_numeric_level_id_is_a_400(self):
        response = self.get(level_id="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level_id", response.data)

    def test_an_unknown_level_is_an_empty_list_not_a_404(self):
        response = self.get(level_id=999999)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(OPEN_COHORTS_URL, {"level_id": self.level.pk})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
