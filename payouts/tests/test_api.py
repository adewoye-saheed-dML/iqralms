"""API tests for the payout app — who may do what, and what they can see.

The authorization half of CLAUDE.md's Phase 8 list. Financial data is the payload,
so the tests that matter most are the negative ones: a sub-teacher must not reach
a colleague's income, must not finalize their own pay, and a family must not reach
the endpoints at all.

The calculation itself is tested in ``test_services.py`` and the record's
invariants in ``test_models.py``; here the amounts only appear where a response
has to agree with them.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    TeacherProfileFactory,
)
from scheduling.tests.factories import AvailabilityFactory

from payouts.models import PayoutStatus, TeacherPayout
from payouts.tests.factories import past_session

MINE_URL = reverse("payouts:payout-mine")
MY_STATEMENT_URL = reverse("payouts:statement-mine")
STATEMENTS_URL = reverse("payouts:statements")
GENERATE_URL = reverse("payouts:payout-generate")
LEAD_URL = reverse("payouts:payout-lead")

PERIOD_START = (dj_timezone.now() - timedelta(weeks=4)).isoformat()
PERIOD_END = (dj_timezone.now() + timedelta(weeks=4)).isoformat()
PERIOD = {"start": PERIOD_START, "end": PERIOD_END}


class PayoutWorld(APITestCase):
    """Two rated sub-teachers with one completed session each, a lead, a family."""

    def setUp(self):
        self.lead = LeadTeacherFactory()
        TeacherProfileFactory(user=self.lead, approved=True, hourly_payout_rate=None)
        self.student = StudentFactory()
        self.parent = ParentFactory()
        ParentLinkFactory(parent=self.parent, student=self.student)

        self.window = self._rated_window()
        self.teacher = self.window.teacher
        self.booking = past_session(
            teacher=self.teacher, student=self.student, duration_minutes=60
        )

        self.other_window = self._rated_window()
        self.other_teacher = self.other_window.teacher
        self.other_booking = past_session(
            teacher=self.other_teacher, duration_minutes=60
        )

    def _rated_window(self, rate="5000.00"):
        return AvailabilityFactory(
            teacher__teacher_profile__hourly_payout_rate=Decimal(rate)
        )

    def generate(self, user=None, **overrides):
        self.client.force_authenticate(user=user or self.lead)
        body = {"period_start": PERIOD_START, "period_end": PERIOD_END, **overrides}
        return self.client.post(GENERATE_URL, body)


class GenerationAPITests(PayoutWorld):
    """Only the lead generates, and repeating the request is safe."""

    def test_lead_generates_for_a_period(self):
        response = self.generate()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["created_count"], 2)
        self.assertEqual(response.data["total_amount"], "10000.00")
        self.assertEqual(TeacherPayout.objects.count(), 2)

    def test_repeating_the_request_creates_no_duplicates(self):
        self.generate()
        repeat = self.generate()
        self.assertEqual(repeat.status_code, status.HTTP_201_CREATED)
        self.assertEqual(repeat.data["created_count"], 0)
        self.assertEqual(repeat.data["skipped_count"], 2)
        self.assertEqual(TeacherPayout.objects.count(), 2)

    def test_generation_can_be_narrowed_to_one_teacher(self):
        response = self.generate(teacher=self.teacher.pk)
        self.assertEqual(response.data["created_count"], 1)
        self.assertEqual(
            list(TeacherPayout.objects.values_list("teacher_id", flat=True)),
            [self.teacher.pk],
        )

    def test_an_inverted_period_is_refused(self):
        response = self.generate(period_start=PERIOD_END, period_end=PERIOD_START)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("period_end", response.data)

    def test_a_sub_teacher_cannot_generate_their_own_pay(self):
        response = self.generate(user=self.teacher)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(TeacherPayout.objects.exists())

    def test_a_student_cannot_generate(self):
        self.assertEqual(
            self.generate(user=self.student).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_generate(self):
        self.assertEqual(
            self.generate(user=self.parent).status_code, status.HTTP_403_FORBIDDEN
        )


class ConcurrentGenerationTests(PayoutWorld):
    """A losing race answers 409 rather than 500, having created nothing.

    The race itself is prevented by the database — a OneToOne on ``booking`` and a
    partial unique on ``cohort`` — and the run is one ``atomic()`` block, so the
    loser rolls back whole. What is tested here is that the API says so: the
    ``IntegrityError`` a real race would raise is patched in, because reproducing
    the interleaving needs two connections and buys nothing beyond this mapping.
    """

    def test_a_racing_run_is_a_409_and_creates_nothing(self):
        with patch.object(
            TeacherPayout, "save", side_effect=IntegrityError("duplicate key")
        ):
            response = self.generate()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(TeacherPayout.objects.exists())


class TeacherScopingAPITests(PayoutWorld):
    """A teacher reads their own payouts and nobody else's."""

    def setUp(self):
        super().setUp()
        self.generate()
        self.mine = TeacherPayout.objects.get(teacher=self.teacher)
        self.theirs = TeacherPayout.objects.get(teacher=self.other_teacher)

    def test_mine_lists_only_the_callers_records(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.mine.pk])

    def test_mine_never_leaks_another_teachers_record(self):
        self.client.force_authenticate(user=self.teacher)
        ids = [row["id"] for row in self.client.get(MINE_URL).data]
        self.assertNotIn(self.theirs.pk, ids)

    def test_mine_can_be_bounded_by_period(self):
        self.client.force_authenticate(user=self.teacher)
        future = {
            "start": (dj_timezone.now() + timedelta(weeks=1)).isoformat(),
            "end": (dj_timezone.now() + timedelta(weeks=2)).isoformat(),
        }
        self.assertEqual(self.client.get(MINE_URL, future).data, [])

    def test_a_student_cannot_read_payouts(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get(MINE_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_read_payouts(self):
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(
            self.client.get(MINE_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_sub_teacher_cannot_read_the_academy_wide_listing(self):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(
            self.client.get(LEAD_URL).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_the_lead_reads_every_record(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(LEAD_URL)
        self.assertEqual(
            sorted(row["id"] for row in response.data),
            sorted([self.mine.pk, self.theirs.pk]),
        )
        self.assertEqual(
            {row["teacher"] for row in response.data},
            {self.teacher.username, self.other_teacher.username},
        )

    def test_the_lead_can_narrow_the_listing_to_one_teacher(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(LEAD_URL, {"teacher_id": self.other_teacher.pk})
        self.assertEqual([row["id"] for row in response.data], [self.theirs.pk])

    def test_an_unknown_teacher_id_is_refused_rather_than_answered_empty(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(LEAD_URL, {"teacher_id": 999999})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class StatementAPITests(PayoutWorld):
    """The teacher's own statement, and the lead's view of anyone's."""

    def setUp(self):
        super().setUp()
        self.generate()

    def test_a_teacher_reads_their_own_statement(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(MY_STATEMENT_URL, PERIOD)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session_count"], 1)
        self.assertEqual(response.data["total_amount"], "5000.00")
        self.assertEqual(response.data["currency"], "NGN")
        self.assertEqual(len(response.data["payouts"]), 1)

    def test_the_statement_total_agrees_with_the_records_it_lists(self):
        self.client.force_authenticate(user=self.teacher)
        body = self.client.get(MY_STATEMENT_URL, PERIOD).data
        self.assertEqual(
            Decimal(body["total_amount"]),
            sum(Decimal(row["amount"]) for row in body["payouts"]),
        )

    def test_a_statement_shows_the_session_that_produced_it(self):
        self.client.force_authenticate(user=self.teacher)
        row = self.client.get(MY_STATEMENT_URL, PERIOD).data["payouts"][0]
        self.assertEqual(row["booking"]["id"], self.booking.pk)
        self.assertEqual(row["booking"]["student"], self.student.username)
        self.assertEqual(row["minutes_paid"], 60)

    def test_a_statement_needs_both_period_bounds(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(MY_STATEMENT_URL)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("start", response.data)
        self.assertIn("end", response.data)

    def test_a_student_has_no_statement(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get(MY_STATEMENT_URL, PERIOD).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_parent_has_no_statement(self):
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(
            self.client.get(MY_STATEMENT_URL, PERIOD).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_sub_teacher_cannot_read_another_teachers_statement(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            STATEMENTS_URL, {**PERIOD, "teacher_id": self.other_teacher.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_lead_reads_any_teachers_statement(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(
            STATEMENTS_URL, {**PERIOD, "teacher_id": self.other_teacher.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["teacher"], self.other_teacher.username)
        self.assertEqual(response.data["session_count"], 1)

    def test_the_leads_statement_endpoint_needs_a_teacher(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(STATEMENTS_URL, PERIOD)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)


class FinalizationAPITests(PayoutWorld):
    """Finalization is the lead's, one-way, and closes the record for good."""

    def setUp(self):
        super().setUp()
        self.generate()
        self.payout = TeacherPayout.objects.get(teacher=self.teacher)
        self.url = reverse("payouts:payout-finalize", args=[self.payout.pk])

    def test_the_lead_finalizes(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], PayoutStatus.FINALIZED)
        self.payout.refresh_from_db()
        self.assertIsNotNone(self.payout.finalized_at)

    def test_finalizing_twice_conflicts(self):
        self.client.force_authenticate(user=self.lead)
        self.client.post(self.url)
        self.assertEqual(
            self.client.post(self.url).status_code, status.HTTP_409_CONFLICT
        )

    def test_a_teacher_cannot_finalize_their_own_payout(self):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(
            self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, PayoutStatus.GENERATED)

    def test_a_teacher_cannot_finalize_another_teachers_payout(self):
        theirs = TeacherPayout.objects.get(teacher=self.other_teacher)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse("payouts:payout-finalize", args=[theirs.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_family_cannot_finalize(self):
        for user in (self.student, self.parent):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                self.assertEqual(
                    self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN
                )
