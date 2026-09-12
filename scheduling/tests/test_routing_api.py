"""API tests for Phase 4 — routing, cohorts, and the specialty rule over HTTP.

Acceptance criteria from specs/phase-4-routing.md covered here: 2, 3, 4, 5 and 6
through ``POST /route/``, and **7** — "a direct booking against a level outside
the teacher's specialties is now rejected — this is the tech-debt closure, prove
it actually closed" — through Phase 3's own endpoint.

Task 4.9 scopes all endpoints under /api/scheduling/organizations/<organization_pk>/...
and enforces tenant isolation on levels, teachers, cohorts, and membership.
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
from curriculum.tests.factories import (
    GroupEligibleLevelFactory,
    LevelFactory,
    TrackFactory,
    admit,
)
from organizations.tests.factories import OrganizationFactory
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
    ensure_teacher_configured,
    slot_at,
    teaches,
)


def _url(name, organization, **kwargs):
    org_pk = getattr(organization, "pk", organization)
    return reverse(f"scheduling:{name}", kwargs={"organization_pk": org_pk, **kwargs})


def bookings_url(organization):
    return _url("booking-create", organization)


def route_url(organization):
    return _url("route", organization)


def cohorts_url(organization):
    return _url("cohort-create", organization)


def open_cohorts_url(organization):
    return _url("cohort-open", organization)


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
        self.organization = self.window.organization
        self.teacher = self.window.teacher
        self.taught = LevelFactory(
            track=TrackFactory(
                organization=self.organization, name="Tajweed", slug="tajweed"
            )
        )
        self.not_taught = LevelFactory(
            track=TrackFactory(
                organization=self.organization, name="Hifz", slug="hifz"
            )
        )
        teaches(self.teacher, self.taught)
        self.student = admit(StudentFactory(), self.organization).user
        self.url = bookings_url(self.organization)

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
        response = self.client.post(self.url, self.payload(self.not_taught))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_direct_booking_within_the_teachers_specialties_succeeds(self):
        """The other half: the rule discriminates rather than refusing everything."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(self.taught))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["level"]["id"], self.taught.pk)

    def test_a_parent_cannot_book_outside_the_teachers_specialties_either(self):
        """The tech-debt entry's own wording was about a parent booking."""
        link = ParentLinkFactory()
        admit(link.parent, self.organization)
        admit(link.student, self.organization)
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            self.url, self.payload(self.not_taught, student=link.student.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_with_no_specialties_recorded_teaches_nothing(self):
        blank = BookableTeacherFactory()
        AvailabilityFactory(
            organization=self.organization,
            teacher=blank,
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(self.taught, teacher=blank.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_direct_booking_records_student_choice(self):
        """Naming your own teacher *is* ``student_choice``, per the spec."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, self.payload(self.taught))
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)

    def test_a_client_cannot_claim_a_routing_reason_for_a_direct_booking(self):
        """``routed_reason`` is not a client field; a supplied value is ignored."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url,
            self.payload(self.taught, routed_reason=RoutedReason.LEAD_AVAILABLE),
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)

    def test_a_client_cannot_attach_a_direct_booking_to_a_cohort(self):
        """A seat is routing's to give — a self-declared one would skip the cap."""
        cohort = CohortFactory(
            availability=self.window,
            teacher=self.teacher,
            level=GroupEligibleLevelFactory(track__organization=self.organization),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            self.url, self.payload(self.taught, cohort=cohort.pk)
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["cohort"])
        self.assertEqual(cohort.seats_taken, 0)


class RouteAPIWorld(APITestCase):
    """Shared world-building for the routing endpoint tests."""

    def setUp(self):
        self.organization = OrganizationFactory()
        self.level = LevelFactory(track__organization=self.organization)
        self.student = admit(
            StudentFactory(timezone="Africa/Lagos"), self.organization
        ).user
        self.slot = next_monday() + timedelta(hours=10)

    def available_teacher(
        self, *, lead=False, hours=20, teaches_level=True, level=None
    ):
        from accounts.models import OrganizationTeacherConfiguration

        teacher = BookableLeadTeacherFactory() if lead else BookableTeacherFactory()
        ensure_teacher_configured(teacher, self.organization)
        OrganizationTeacherConfiguration.objects.filter(
            membership__user=teacher, membership__organization=self.organization
        ).update(max_weekly_hours=hours)
        profile = teacher.teacher_profile
        profile.max_weekly_hours = hours
        profile.save()
        if teaches_level:
            teaches(teacher, level or self.level)
        for weekday in range(7):
            Availability.objects.create(
                organization=self.organization,
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
                student=admit(StudentFactory(), self.organization).user,
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
        url = route_url(self.organization)
        return self.client.post(url, self.payload(**overrides), format="json")


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
        self.assertTrue(booking["video_provider_meeting_id"])
        self.assertIn(booking["video_provider_meeting_id"], booking["video_join_url"])
        self.assertEqual(booking["status"], "scheduled")
        self.assertEqual(booking["student"]["id"], self.student.pk)

    def test_the_response_renders_the_start_in_the_callers_zone(self):
        self.available_teacher(lead=True)
        response = self.post_route()
        self.assertIn("+01:00", response.data["booking"]["start_time_local"])

    def test_an_open_cohort_takes_the_student(self):
        """Acceptance criterion 2, over HTTP."""
        self.level = GroupEligibleLevelFactory(track__organization=self.organization)
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
        """Acceptance criterion 5, over HTTP."""
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
        admit(link.parent, self.organization)
        admit(link.student, self.organization)

        response = self.post_route(user=link.parent, student=link.student.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["student"]["id"], link.student.pk)

    def test_the_default_duration_is_applied(self):
        self.available_teacher(lead=True)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            route_url(self.organization),
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

        response = self.post_route()
        considered = response.data["considered"]

        self.assertIn("cohort", considered)
        self.assertIn("lead", considered)
        self.assertIn("sub_teachers", considered)
        self.assertTrue(considered["sub_teachers"])

    def test_the_candidate_did_not_teach_that_track_is_explained(self):
        other_level = LevelFactory(track__organization=self.organization)
        self.available_teacher(teaches_level=False)
        self.available_teacher(level=other_level)
        response = self.post_route()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn(
            f"No approved sub-teacher specialises in {self.level.track.name}",
            response.data["considered"]["sub_teachers"],
        )

    def test_the_candidate_was_outside_their_hours_is_explained(self):
        lead = BookableLeadTeacherFactory()
        ensure_teacher_configured(lead, self.organization)
        teaches(lead, self.level)
        Availability.objects.create(
            organization=self.organization,
            teacher=lead,
            weekday=self.slot.weekday(),
            start_time_utc=time(2, 0),
            end_time_utc=time(4, 0),
        )

        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(Booking.objects.exists())


class RouteAPIPermissionTests(RouteAPIWorld):
    """Who may ask the system to route, and what a bad request looks like."""

    def test_an_anonymous_caller_cannot_route(self):
        self.available_teacher(lead=True)
        response = self.client.post(
            route_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Booking.objects.exists())

    def test_a_non_member_cannot_route(self):
        self.available_teacher(lead=True)
        outsider = StudentFactory()
        response = self.post_route(user=outsider)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_teacher_cannot_route_a_session_for_themselves(self):
        self.available_teacher(lead=True)
        for user in (
            admit(SubTeacherFactory(), self.organization).user,
            admit(LeadTeacherFactory(), self.organization).user,
        ):
            with self.subTest(role=user.role):
                response = self.post_route(user=user)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Booking.objects.exists())

    def test_a_student_cannot_route_for_someone_else(self):
        self.available_teacher(lead=True)
        other = admit(StudentFactory(), self.organization).user
        response = self.post_route(student=other.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(Booking.objects.exists())

    def test_a_parent_must_say_which_child(self):
        self.available_teacher(lead=True)
        parent = admit(ParentFactory(), self.organization).user
        response = self.post_route(user=parent)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_a_parent_cannot_route_for_an_unlinked_student(self):
        self.available_teacher(lead=True)
        parent = admit(ParentFactory(), self.organization).user
        unlinked = admit(StudentFactory(), self.organization).user
        response = self.post_route(user=parent, student=unlinked.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Booking.objects.exists())

    def test_a_level_from_another_academy_is_rejected(self):
        self.available_teacher(lead=True)
        other_level = LevelFactory()  # different organization
        response = self.post_route(level=other_level.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_an_unknown_level_is_a_400(self):
        response = self.post_route(level=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_a_missing_time_window_is_a_400(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            route_url(self.organization), {"level": self.level.pk}, format="json"
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
        lead = self.available_teacher(lead=True)
        sub = self.available_teacher()
        response = self.post_route(teacher=sub.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["teacher"]["id"], lead.pk)


class CohortCreateAPITests(APITestCase):
    """``POST /cohorts/`` — the lead opens a group class."""

    def setUp(self):
        self.organization = OrganizationFactory()
        self.lead = admit(LeadTeacherFactory(), self.organization).user
        self.teacher = BookableTeacherFactory()
        ensure_teacher_configured(self.teacher, self.organization)
        self.level = GroupEligibleLevelFactory(track__organization=self.organization)
        teaches(self.teacher, self.level)
        self.window = AvailabilityFactory(
            organization=self.organization,
            teacher=self.teacher,
            weekday=DEFAULT_WEEKDAY,
            start_time_utc=DEFAULT_WINDOW_START,
            end_time_utc=DEFAULT_WINDOW_END,
        )
        self.url = cohorts_url(self.organization)

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
        response = self.client.post(self.url, self.payload())

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
        response = self.client.post(self.url, body)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["max_students"], 6)

    def test_a_non_group_eligible_level_is_rejected(self):
        plain = LevelFactory(
            track__organization=self.organization, group_eligible=False
        )
        teaches(self.teacher, plain)
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, self.payload(level=plain.pk))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(Cohort.objects.exists())

    def test_a_teacher_who_does_not_teach_the_track_is_rejected(self):
        other = BookableTeacherFactory()
        ensure_teacher_configured(other, self.organization)
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, self.payload(teacher=other.pk))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_a_level_from_another_academy_is_rejected(self):
        foreign_level = GroupEligibleLevelFactory()
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, self.payload(level=foreign_level.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

    def test_a_teacher_from_another_academy_is_rejected(self):
        foreign_teacher = BookableTeacherFactory()
        AvailabilityFactory(teacher=foreign_teacher)
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, self.payload(teacher=foreign_teacher.pk))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_a_sub_teacher_cannot_create_a_cohort(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Cohort.objects.exists())

    def test_a_student_or_parent_cannot_create_a_cohort(self):
        for user in (
            admit(StudentFactory(), self.organization).user,
            admit(ParentFactory(), self.organization).user,
        ):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(self.url, self.payload())
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_create_a_cohort(self):
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_lead_cannot_create_a_cohort(self):
        outsider = LeadTeacherFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.post(self.url, self.payload())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_students_cannot_be_seated_at_creation_time(self):
        student = admit(StudentFactory(), self.organization).user
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, self.payload(students=[student.pk]))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Cohort.objects.get(pk=response.data["id"]).seats_taken, 0)


class OpenCohortListAPITests(APITestCase):
    """``GET /cohorts/open/?level_id=`` — what has a seat left."""

    def setUp(self):
        self.organization = OrganizationFactory()
        self.student = admit(
            StudentFactory(timezone="Asia/Karachi"), self.organization
        ).user
        self.level = GroupEligibleLevelFactory(track__organization=self.organization)
        teacher = BookableTeacherFactory()
        ensure_teacher_configured(teacher, self.organization)
        window = AvailabilityFactory(
            organization=self.organization,
            teacher=teacher,
        )
        self.cohort = CohortFactory(
            availability=window,
            teacher=teacher,
            level=self.level,
            max_students=2,
        )
        self.url = open_cohorts_url(self.organization)

    def get(self, **params):
        self.client.force_authenticate(user=self.student)
        return self.client.get(self.url, params)

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
        other_level = GroupEligibleLevelFactory(track__organization=self.organization)
        CohortFactory(
            level=other_level,
        )
        self.assertEqual(
            [c["id"] for c in self.get(level_id=self.level.pk).data], [self.cohort.pk]
        )

    def test_a_foreign_organization_cohort_is_not_listed(self):
        foreign_cohort = CohortFactory(max_students=2)
        self.assertEqual(
            list(self.get(level_id=foreign_cohort.level_id).data), []
        )

    def test_the_roster_is_not_published(self):
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
        response = self.client.get(self.url, {"level_id": self.level.pk})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        outsider = StudentFactory()
        self.client.force_authenticate(user=outsider)
        response = self.client.get(self.url, {"level_id": self.level.pk})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
