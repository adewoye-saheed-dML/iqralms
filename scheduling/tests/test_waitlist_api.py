"""API tests for Phase 5's waitlist surface — the routing extension and the queue.

Acceptance criteria from specs/phase-5-pricing-waitlist.md covered here over HTTP:
**4** (preferred teacher with capacity books directly), **5** (at capacity →
waitlist entry reported in ``considered``), **6** (never a cohort seat), **7**
(promotion books and stamps, row survives) and **8** (promoting an ineligible
entry fails cleanly).

The model layer proves the same criteria in test_waitlist_routing.py and
test_waitlist.py. Both layers matter for the reason Phase 3 set out: the rules
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
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory
from scheduling.models import (
    Availability,
    Booking,
    BookingStatus,
    RoutedReason,
    TeacherWaitlist,
)

from .factories import (
    BookableLeadTeacherFactory,
    BookableTeacherFactory,
    CohortFactory,
    teaches,
)
from .test_routing_api import RouteAPIWorld

MY_WAITLIST_URL = reverse("scheduling:waitlist-mine")
FOR_TEACHER_URL = reverse("scheduling:waitlist-for-teacher")


def promote_url(entry):
    return reverse("scheduling:waitlist-promote", args=[entry.pk])


class PreferredTeacherRouteAPIWorld(RouteAPIWorld):
    """``RouteAPIWorld`` plus a preferred-teacher POST.

    Reused rather than rebuilt so "an available teacher" means exactly what it
    means for the Phase 4 tests — a second definition would eventually diverge.
    """

    def post_prefer(self, teacher, user=None, **overrides):
        return self.post_route(user=user, preferred_teacher=teacher.pk, **overrides)


class PreferredTeacherRouteAPITests(PreferredTeacherRouteAPIWorld):
    """Acceptance criteria 4 and 5 over HTTP."""

    def test_a_preferred_teacher_with_capacity_books_directly(self):
        """Acceptance criterion 4, over HTTP."""
        wanted = self.available_teacher()

        response = self.post_prefer(wanted)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["routed"])
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)
        self.assertEqual(response.data["booking"]["teacher"]["id"], wanted.pk)
        self.assertIsNone(response.data["cohort"])
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_the_preferred_booking_comes_back_ready_to_attend(self):
        wanted = self.available_teacher()
        booking = self.post_prefer(wanted).data["booking"]

        self.assertTrue(booking["video_room_name"])
        self.assertIn(booking["video_room_name"], booking["video_join_url"])
        self.assertEqual(booking["status"], BookingStatus.SCHEDULED)

    def test_a_preference_overrides_the_routing_order(self):
        """The lead has capacity and is still not chosen — the family named a sub."""
        lead = self.available_teacher(lead=True)
        wanted = self.available_teacher()

        response = self.post_prefer(wanted)

        self.assertEqual(response.data["booking"]["teacher"]["id"], wanted.pk)
        self.assertEqual(Booking.objects.filter(teacher=lead).count(), 0)

    def test_a_full_preferred_teacher_is_a_structured_409_with_the_waitlist(self):
        """Acceptance criterion 5, over HTTP."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        response = self.post_prefer(wanted)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(response.data["routed"])
        entry = TeacherWaitlist.objects.get()
        self.assertEqual(response.data["considered"]["waitlist"]["id"], entry.pk)
        self.assertEqual(
            response.data["considered"]["waitlist"]["requested_teacher"],
            wanted.username,
        )

    def test_the_409_says_why_the_named_teacher_declined(self):
        """A parent must be able to read the refusal, not just receive it."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        response = self.post_prefer(wanted)

        self.assertIn(wanted.username, response.data["considered"]["preferred_teacher"])
        self.assertIn(wanted.username, response.data["detail"])
        self.assertIn("waitlist", response.data["detail"])

    def test_nobody_else_is_booked_when_the_named_teacher_is_full(self):
        """mvp-spec section 4's central promise, over HTTP."""
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)
        free_lead = self.available_teacher(lead=True)
        free_sub = self.available_teacher()

        self.post_prefer(wanted)

        self.assertFalse(Booking.objects.filter(start_time_utc=self.slot).exists())
        self.assertEqual(Booking.objects.filter(teacher=free_lead).count(), 0)
        self.assertEqual(Booking.objects.filter(teacher=free_sub).count(), 0)

    def test_a_teacher_who_cannot_teach_the_track_is_a_400_with_no_waitlist(self):
        """The spec's hard-block exception: fail outright, do not queue."""
        wanted = self.available_teacher(teaches_level=False)

        response = self.post_prefer(wanted)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_a_non_teacher_preference_is_a_400(self):
        """Named in the field rather than reported as a missing object."""
        response = self.post_prefer(StudentFactory())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("preferred_teacher", response.data)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_an_unknown_preferred_teacher_is_a_400(self):
        self.client.force_authenticate(user=self.student)
        body = self.payload(preferred_teacher=999999)
        response = self.client.post(reverse("scheduling:route"), body, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("preferred_teacher", response.data)

    def test_a_parent_can_name_a_teacher_for_a_linked_child(self):
        link = ParentLinkFactory()
        wanted = self.available_teacher()

        response = self.post_prefer(wanted, user=link.parent, student=link.student.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["student"]["id"], link.student.pk)
        self.assertEqual(response.data["booking"]["teacher"]["id"], wanted.pk)

    def test_a_parent_naming_a_full_teacher_waitlists_their_child(self):
        """The entry belongs to the student, not to the parent who asked."""
        link = ParentLinkFactory()
        wanted = self.available_teacher(hours=1)
        self.fill_week(wanted, 60)

        response = self.post_prefer(wanted, user=link.parent, student=link.student.pk)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        entry = TeacherWaitlist.objects.get()
        self.assertEqual(entry.student, link.student)

    def test_a_student_cannot_name_a_teacher_for_someone_else(self):
        wanted = self.available_teacher()
        response = self.post_prefer(wanted, student=StudentFactory().pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_a_teacher_cannot_route_a_preference_for_themselves(self):
        wanted = self.available_teacher()
        for user in (SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                response = self.post_prefer(wanted, user=user)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_name_a_teacher(self):
        wanted = self.available_teacher()
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("scheduling:route"),
            self.payload(preferred_teacher=wanted.pk),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(TeacherWaitlist.objects.exists())

    def test_omitting_the_preference_still_auto_routes(self):
        """Phase 4's behaviour is untouched — the field is additive."""
        lead = self.available_teacher(lead=True)
        response = self.post_route()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.LEAD_AVAILABLE)
        self.assertEqual(response.data["booking"]["teacher"]["id"], lead.pk)

    def test_a_teacher_key_is_still_ignored_by_auto_routing(self):
        """``preferred_teacher`` is the only way to express a preference.

        Phase 4 has a test asserting a smuggled ``teacher`` key is ignored, on the
        grounds that honouring it silently would be the "quietly redirected"
        failure. That reasoning holds after this phase: the honest way to name a
        teacher now exists, so the dishonest one must still do nothing.
        """
        lead = self.available_teacher(lead=True)
        sub = self.available_teacher()

        response = self.post_route(teacher=sub.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["booking"]["teacher"]["id"], lead.pk)


class PreferredTeacherNeverGetsACohortSeatAPITests(PreferredTeacherRouteAPIWorld):
    """Acceptance criterion 6, over HTTP."""

    def setUp(self):
        super().setUp()
        self.level = GroupEligibleLevelFactory()

    def open_cohort_for(self, teacher):
        return CohortFactory(
            availability=teacher.availability_windows.first(),
            teacher=teacher,
            level=self.level,
            schedule_start_utc=self.slot,
        )

    def test_a_preferred_request_is_one_to_one_even_with_an_open_cohort(self):
        """Acceptance criterion 6."""
        wanted = self.available_teacher()
        cohort = self.open_cohort_for(wanted)

        response = self.post_prefer(wanted)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)
        self.assertIsNone(response.data["booking"]["cohort"])
        self.assertIsNone(response.data["cohort"])
        self.assertEqual(cohort.seats_taken, 0)

    def test_the_same_request_without_a_preference_takes_the_seat(self):
        """Proof the rule is the preference, not the world it was tested in."""
        wanted = self.available_teacher()
        cohort = self.open_cohort_for(wanted)

        response = self.post_route()

        self.assertEqual(response.data["routed_reason"], RoutedReason.COHORT_ASSIGNED)
        self.assertEqual(response.data["booking"]["cohort"], cohort.pk)
        self.assertEqual(cohort.seats_taken, 1)


class WaitlistPromoteAPITests(PreferredTeacherRouteAPIWorld):
    """Acceptance criteria 7 and 8 over HTTP — ``POST /waitlist/{id}/promote/``."""

    def setUp(self):
        super().setUp()
        self.lead = BookableLeadTeacherFactory()

    def waiting_entry(self):
        """An open entry created the way production creates them, then freed.

        Built by a refused preferred-teacher request rather than by the factory, so
        promotion is tested against a real entry; the teacher's week is then
        cancelled clear, which is the situation a promotion exists for.
        """
        teacher = self.available_teacher(hours=1)
        self.fill_week(teacher, 60)
        self.post_prefer(teacher)
        entry = TeacherWaitlist.objects.get()
        for booking in Booking.objects.filter(teacher=teacher):
            booking.cancel()
        return entry

    def promote(self, entry, user=None, **body):
        self.client.force_authenticate(user=user or self.lead)
        return self.client.post(promote_url(entry), body, format="json")

    def test_the_lead_promotes_an_entry_into_a_booking(self):
        """Acceptance criterion 7, over HTTP."""
        entry = self.waiting_entry()

        response = self.promote(entry)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], entry.student.pk)
        self.assertEqual(response.data["teacher"]["id"], entry.requested_teacher.pk)
        self.assertEqual(response.data["routed_reason"], RoutedReason.STUDENT_CHOICE)
        self.assertTrue(response.data["video_room_name"])

    def test_promotion_stamps_the_entry_and_keeps_the_row(self):
        """Acceptance criterion 7 — the row survives as a record."""
        entry = self.waiting_entry()

        response = self.promote(entry)

        entry.refresh_from_db()
        self.assertEqual(entry.fulfilled_booking_id, response.data["id"])
        self.assertFalse(entry.is_open)
        self.assertTrue(TeacherWaitlist.objects.filter(pk=entry.pk).exists())

    def test_promotion_can_name_a_different_slot(self):
        entry = self.waiting_entry()
        later = entry.requested_start_utc + timedelta(hours=2)

        response = self.promote(entry, start_time_utc=later.isoformat())

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            Booking.objects.get(pk=response.data["id"]).start_time_utc, later
        )

    def test_promotion_can_name_a_different_duration(self):
        entry = self.waiting_entry()
        response = self.promote(entry, duration_minutes=60)
        self.assertEqual(response.data["duration_minutes"], 60)

    def test_a_zero_length_promotion_is_a_400(self):
        entry = self.waiting_entry()
        response = self.promote(entry, duration_minutes=0)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_promoting_an_ineligible_entry_is_a_400(self):
        """Acceptance criterion 8, over HTTP — refused, not forced."""
        entry = self.waiting_entry()
        Booking.objects.create(
            student=StudentFactory(),
            teacher=entry.requested_teacher,
            level=entry.level,
            start_time_utc=entry.requested_start_utc,
            duration_minutes=30,
        )
        before = Booking.objects.count()

        response = self.promote(entry)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        entry.refresh_from_db()
        self.assertTrue(entry.is_open, "the family is still waiting")
        self.assertEqual(Booking.objects.count(), before, "nothing was forced")

    def test_the_400_explains_why_the_slot_is_gone(self):
        """The lead needs to know it is a clash, not a bug."""
        entry = self.waiting_entry()
        Booking.objects.create(
            student=StudentFactory(),
            teacher=entry.requested_teacher,
            level=entry.level,
            start_time_utc=entry.requested_start_utc,
            duration_minutes=30,
        )

        response = self.promote(entry)
        self.assertIn("overlapping", str(response.data))

    def test_promoting_a_teacher_who_is_full_again_is_a_400(self):
        entry = self.waiting_entry()
        self.fill_week(entry.requested_teacher, 60)

        response = self.promote(entry)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        entry.refresh_from_db()
        self.assertTrue(entry.is_open)

    def test_promoting_an_already_promoted_entry_is_a_409(self):
        """A one-way transition, the same 409 an already-reviewed placement gets."""
        entry = self.waiting_entry()
        self.promote(entry)

        response = self.promote(entry)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.filter(student=entry.student).count(), 1)

    def test_an_unknown_entry_is_a_404(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            reverse("scheduling:waitlist-promote", args=[999999]), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_sub_teacher_cannot_promote(self):
        """Promotion commits a teacher's time, so it is the lead's call."""
        entry = self.waiting_entry()

        response = self.promote(entry, user=entry.requested_teacher)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        entry.refresh_from_db()
        self.assertTrue(entry.is_open)

    def test_a_student_cannot_promote_themselves_off_the_waitlist(self):
        """Otherwise the queue would be a formality anybody could skip."""
        entry = self.waiting_entry()

        response = self.promote(entry, user=entry.student)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        entry.refresh_from_db()
        self.assertTrue(entry.is_open)

    def test_a_parent_cannot_promote_their_child(self):
        entry = self.waiting_entry()
        response = self.promote(entry, user=ParentFactory())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_promote(self):
        entry = self.waiting_entry()
        # Building the entry authenticated as the student, so drop that first —
        # otherwise this asserts "a student cannot promote" a second time.
        self.client.force_authenticate(user=None)

        response = self.client.post(promote_url(entry), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        entry.refresh_from_db()
        self.assertTrue(entry.is_open)


class TeacherWaitlistListAPITests(APITestCase):
    """``GET /waitlist/for-teacher/?teacher_id=`` — the queue the lead works."""

    def setUp(self):
        self.lead = BookableLeadTeacherFactory()
        self.teacher = BookableTeacherFactory()
        self.window = Availability.objects.create(
            teacher=self.teacher,
            weekday=0,
            start_time_utc=time(0, 0),
            end_time_utc=time.max,
        )
        self.level = LevelFactory()
        teaches(self.teacher, self.level)

    def entry(self, *, priority=0, hours=1, student=None):
        from .factories import WaitlistEntryFactory, slot_at

        return WaitlistEntryFactory(
            availability=self.window,
            requested_teacher=self.teacher,
            level=self.level,
            student=student or StudentFactory(),
            requested_start_utc=slot_at(self.window, hours * 60),
            priority=priority,
        )

    def get(self, user=None, **params):
        self.client.force_authenticate(user=user or self.lead)
        return self.client.get(FOR_TEACHER_URL, params)

    def test_open_entries_are_listed(self):
        entry = self.entry()
        response = self.get(teacher_id=self.teacher.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [entry.pk])
        self.assertEqual(response.data[0]["status"], "open")
        self.assertEqual(response.data[0]["track"], self.level.track.slug)

    def test_the_queue_is_ordered_by_priority_then_waiting_time(self):
        """The spec: priority desc, then ``requested_at`` asc."""
        first_asked = self.entry(hours=1)
        second_asked = self.entry(hours=2)
        bumped = self.entry(priority=5, hours=3)

        response = self.get(teacher_id=self.teacher.pk)

        self.assertEqual(
            [row["id"] for row in response.data],
            [bumped.pk, first_asked.pk, second_asked.pk],
        )

    def test_a_fulfilled_entry_is_not_in_the_queue(self):
        from .factories import BookingFactory

        open_entry = self.entry(hours=1)
        done = self.entry(hours=2)
        done.mark_fulfilled(
            BookingFactory(
                availability=self.window,
                student=done.student,
                level=self.level,
                start_time_utc=done.requested_start_utc,
            )
        )

        response = self.get(teacher_id=self.teacher.pk)
        self.assertEqual([row["id"] for row in response.data], [open_entry.pk])

    def test_another_teachers_queue_is_not_listed(self):
        from .factories import WaitlistEntryFactory

        mine = self.entry()
        WaitlistEntryFactory()

        response = self.get(teacher_id=self.teacher.pk)
        self.assertEqual([row["id"] for row in response.data], [mine.pk])

    def test_teacher_id_is_required(self):
        response = self.get()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_a_non_numeric_teacher_id_is_a_400(self):
        response = self.get(teacher_id="abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)

    def test_an_unknown_teacher_is_an_empty_list_not_a_404(self):
        response = self.get(teacher_id=999999)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_sub_teacher_cannot_read_the_queue(self):
        """Deliberately narrow — see views.py and tech-debt.md."""
        self.entry()
        response = self.get(user=self.teacher, teacher_id=self.teacher.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_read_the_queue(self):
        """Who else is waiting is not published — the same call cohort rosters make."""
        entry = self.entry()
        response = self.get(user=entry.student, teacher_id=self.teacher.pk)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(FOR_TEACHER_URL, {"teacher_id": self.teacher.pk})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MyWaitlistAPITests(APITestCase):
    """``GET /waitlist/mine/`` — what a family sees of their own requests."""

    def setUp(self):
        from .factories import WaitlistEntryFactory

        self.entry = WaitlistEntryFactory(student=StudentFactory(timezone="Africa/Lagos"))
        self.student = self.entry.student

    def get(self, user=None):
        self.client.force_authenticate(user=user or self.student)
        return self.client.get(MY_WAITLIST_URL)

    def test_a_student_sees_their_own_entry_and_its_status(self):
        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.entry.pk])
        self.assertEqual(response.data[0]["status"], "open")
        self.assertEqual(
            response.data[0]["requested_teacher"]["id"],
            self.entry.requested_teacher.pk,
        )

    def test_the_slot_is_rendered_in_the_callers_zone(self):
        """Storage is UTC; a family reads their own clock (CLAUDE.md)."""
        response = self.get()
        self.assertIn("+01:00", response.data[0]["requested_start_local"])
        self.assertIn("+01:00", response.data[0]["requested_at_local"])

    def test_a_fulfilled_entry_stays_visible(self):
        """It is the record of a request that was honoured."""
        from .factories import BookingFactory

        booking = BookingFactory(
            availability=self.entry.requested_teacher.availability_windows.first(),
            student=self.student,
            level=self.entry.level,
            start_time_utc=self.entry.requested_start_utc,
        )
        self.entry.mark_fulfilled(booking)

        response = self.get()

        self.assertEqual(response.data[0]["status"], "fulfilled")
        self.assertEqual(response.data[0]["fulfilled_booking"], booking.pk)

    def test_another_students_entry_is_not_visible(self):
        from .factories import WaitlistEntryFactory

        WaitlistEntryFactory()
        response = self.get()
        self.assertEqual([row["id"] for row in response.data], [self.entry.pk])

    def test_a_teacher_has_no_waitlist_entries_of_their_own(self):
        response = self.get(user=self.entry.requested_teacher)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_sees_their_linked_childs_entries(self):
        """The requester reads their own request back (product owner, 2026-08-25).

        ``/route/`` lets a parent name a teacher for a linked child, so a parent
        is one of the two people who can *create* an entry. Refusing them the
        listing would mean a request nobody who made it can check.
        """
        parent = ParentFactory()
        ParentLinkFactory(parent=parent, student=self.student)

        response = self.get(user=parent)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.entry.pk])
        self.assertEqual(response.data[0]["status"], "open")

    def test_a_parent_does_not_see_an_unlinked_students_entries(self):
        """Scoped through ParentLink, exactly as cancelling a booking is."""
        response = self.get(user=ParentLinkFactory().parent)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_a_child_with_two_linked_parents_is_listed_once(self):
        """What the queryset's distinct() is for — a join, not a duplicate."""
        parent = ParentFactory()
        ParentLinkFactory(parent=parent, student=self.student)
        ParentLinkFactory(parent=ParentFactory(), student=self.student)

        response = self.get(user=parent)

        self.assertEqual([row["id"] for row in response.data], [self.entry.pk])

    def test_the_slot_is_rendered_in_the_parents_zone(self):
        """The caller's clock, not the child's — the same rule as everywhere.

        Computed rather than hardcoded: New York's offset is seasonal, and a test
        asserting "-04:00" would start failing in November for no real reason.
        """
        from accounts.utils import to_user_timezone

        parent = ParentFactory(timezone="America/New_York")
        ParentLinkFactory(parent=parent, student=self.student)

        response = self.get(user=parent)

        self.assertEqual(
            response.data[0]["requested_start_local"],
            to_user_timezone(
                self.entry.requested_start_utc, "America/New_York"
            ).isoformat(),
        )
        self.assertNotIn("+01:00", response.data[0]["requested_start_local"])

    def test_an_anonymous_caller_is_refused(self):
        response = self.client.get(MY_WAITLIST_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class WaitlistIsCreatedOnlyByRoutingAPITests(PreferredTeacherRouteAPIWorld):
    """There is no create endpoint, and that is the design.

    Being on a waitlist is the *outcome* of asking for a teacher who was full, not
    something a client declares — so a family cannot add themselves to the lead's
    queue without a real refused request behind it.
    """

    def test_there_is_no_waitlist_create_endpoint(self):
        from django.urls.exceptions import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("scheduling:waitlist-create")

    def test_posting_to_the_listing_is_refused(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(MY_WAITLIST_URL, {}, format="json")
        self.assertEqual(
            response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
        )
        self.assertFalse(TeacherWaitlist.objects.exists())
