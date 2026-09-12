"""API tests for the payout app — who may do what, and what they can see.

The authorization half of CLAUDE.md's Phase 8 list and SaaS Phase 7 tenancy.
Financial data is the payload, so the tests that matter most are the negative ones:
a sub-teacher must not reach a colleague's income, must not finalize their own pay,
and students and parents must not reach the endpoints at all.
All endpoints require active organization membership and appropriate tenant role.
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
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from scheduling.tests.factories import AvailabilityFactory, teaches

from payouts.models import PayoutStatus, TeacherPayout
from payouts.tests.factories import past_session

PERIOD_START = (dj_timezone.now() - timedelta(weeks=4)).isoformat()
PERIOD_END = (dj_timezone.now() + timedelta(weeks=4)).isoformat()
PERIOD = {"start": PERIOD_START, "end": PERIOD_END}


class PayoutWorld(APITestCase):
    """Two rated sub-teachers with one completed session each, a lead/owner, a family in one academy."""

    def setUp(self):
        self.organization = OrganizationFactory(name="Al-Furqan Academy")
        self.track = TrackFactory(organization=self.organization)
        self.level = LevelFactory(track=self.track)

        self.lead = LeadTeacherFactory()
        TeacherProfileFactory(user=self.lead, approved=True, hourly_payout_rate=None)
        self.lead_membership = admit(
            self.lead, self.organization, role=OrganizationRole.OWNER
        )

        self.student = StudentFactory()
        self.student_membership = admit(
            self.student, self.organization, role=OrganizationRole.STAFF
        )
        self.parent = ParentFactory()
        self.parent_membership = admit(
            self.parent, self.organization, role=OrganizationRole.STAFF
        )
        ParentLinkFactory(parent=self.parent, student=self.student)

        self.window = self._rated_window()
        self.teacher = self.window.teacher
        self.teacher_membership = admit(
            self.teacher, self.organization, role=OrganizationRole.TEACHER
        )
        teaches(self.teacher, self.level)
        self.booking = past_session(
            availability=self.window,
            teacher=self.teacher,
            student=self.student,
            level=self.level,
            duration_minutes=60,
        )

        self.other_window = self._rated_window()
        self.other_teacher = self.other_window.teacher
        self.other_teacher_membership = admit(
            self.other_teacher, self.organization, role=OrganizationRole.TEACHER
        )
        teaches(self.other_teacher, self.level)
        self.other_booking = past_session(
            availability=self.other_window,
            teacher=self.other_teacher,
            student=self.student,
            level=self.level,
            duration_minutes=60,
        )

    def _rated_window(self, rate="5000.00"):
        return AvailabilityFactory(
            organization=self.organization,
            teacher__teacher_profile__hourly_payout_rate=Decimal(rate),
        )

    @property
    def mine_url(self):
        return reverse(
            "payouts:payout-mine", kwargs={"organization_pk": self.organization.pk}
        )

    @property
    def my_statement_url(self):
        return reverse(
            "payouts:statement-mine", kwargs={"organization_pk": self.organization.pk}
        )

    @property
    def statements_url(self):
        return reverse(
            "payouts:statements", kwargs={"organization_pk": self.organization.pk}
        )

    @property
    def generate_url(self):
        return reverse(
            "payouts:payout-generate", kwargs={"organization_pk": self.organization.pk}
        )

    @property
    def lead_url(self):
        return reverse(
            "payouts:payout-lead", kwargs={"organization_pk": self.organization.pk}
        )

    def finalize_url(self, pk, organization=None):
        org = organization or self.organization
        return reverse(
            "payouts:payout-finalize",
            kwargs={"organization_pk": org.pk, "pk": pk},
        )

    def generate(self, user=None, organization=None, **overrides):
        org = organization or self.organization
        url = reverse(
            "payouts:payout-generate", kwargs={"organization_pk": org.pk}
        )
        self.client.force_authenticate(user=user or self.lead)
        body = {"period_start": PERIOD_START, "period_end": PERIOD_END, **overrides}
        return self.client.post(url, body)


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
        response = self.client.get(self.mine_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.mine.pk])

    def test_mine_never_leaks_another_teachers_record(self):
        self.client.force_authenticate(user=self.teacher)
        ids = [row["id"] for row in self.client.get(self.mine_url).data]
        self.assertNotIn(self.theirs.pk, ids)

    def test_mine_can_be_bounded_by_period(self):
        self.client.force_authenticate(user=self.teacher)
        future = {
            "start": (dj_timezone.now() + timedelta(weeks=1)).isoformat(),
            "end": (dj_timezone.now() + timedelta(weeks=2)).isoformat(),
        }
        self.assertEqual(self.client.get(self.mine_url, future).data, [])

    def test_a_student_cannot_read_payouts(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get(self.mine_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_read_payouts(self):
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(
            self.client.get(self.mine_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_sub_teacher_cannot_read_the_academy_wide_listing(self):
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(
            self.client.get(self.lead_url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_the_lead_reads_every_record(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.lead_url)
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
        response = self.client.get(self.lead_url, {"teacher_id": self.other_teacher.pk})
        self.assertEqual([row["id"] for row in response.data], [self.theirs.pk])

    def test_an_unknown_teacher_id_is_refused_rather_than_answered_empty(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.lead_url, {"teacher_id": 999999})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class StatementAPITests(PayoutWorld):
    """The teacher's own statement, and the lead's view of anyone's."""

    def setUp(self):
        super().setUp()
        self.generate()

    def test_a_teacher_reads_their_own_statement(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.my_statement_url, PERIOD)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["session_count"], 1)
        self.assertEqual(response.data["total_amount"], "5000.00")
        self.assertEqual(response.data["currency"], "NGN")
        self.assertEqual(len(response.data["payouts"]), 1)

    def test_the_statement_total_agrees_with_the_records_it_lists(self):
        self.client.force_authenticate(user=self.teacher)
        body = self.client.get(self.my_statement_url, PERIOD).data
        self.assertEqual(
            Decimal(body["total_amount"]),
            sum(Decimal(row["amount"]) for row in body["payouts"]),
        )

    def test_a_statement_shows_the_session_that_produced_it(self):
        self.client.force_authenticate(user=self.teacher)
        row = self.client.get(self.my_statement_url, PERIOD).data["payouts"][0]
        self.assertEqual(row["booking"]["id"], self.booking.pk)
        self.assertEqual(row["booking"]["student"], self.student.username)
        self.assertEqual(row["minutes_paid"], 60)

    def test_a_statement_needs_both_period_bounds(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.my_statement_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("start", response.data)
        self.assertIn("end", response.data)

    def test_a_student_has_no_statement(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get(self.my_statement_url, PERIOD).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_parent_has_no_statement(self):
        self.client.force_authenticate(user=self.parent)
        self.assertEqual(
            self.client.get(self.my_statement_url, PERIOD).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_sub_teacher_cannot_read_another_teachers_statement(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            self.statements_url, {**PERIOD, "teacher_id": self.other_teacher.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_lead_reads_any_teachers_statement(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(
            self.statements_url, {**PERIOD, "teacher_id": self.other_teacher.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["teacher"], self.other_teacher.username)
        self.assertEqual(response.data["session_count"], 1)

    def test_the_leads_statement_endpoint_needs_a_teacher(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.statements_url, PERIOD)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)


class FinalizationAPITests(PayoutWorld):
    """Finalization is the owner/admin's, one-way, and closes the record for good."""

    def setUp(self):
        super().setUp()
        self.generate()
        self.payout = TeacherPayout.objects.get(teacher=self.teacher)
        self.url = self.finalize_url(self.payout.pk)

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
        response = self.client.post(self.finalize_url(theirs.pk))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_family_cannot_finalize(self):
        for user in (self.student, self.parent):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                self.assertEqual(
                    self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN
                )


class TenantIsolationAPITests(PayoutWorld):
    """SaaS Phase 7 tenant isolation on API surface."""

    def test_legacy_unscoped_routes_are_retired(self):
        self.client.force_authenticate(user=self.lead)
        for legacy_path in [
            "/api/payouts/mine/",
            "/api/payouts/lead/",
            "/api/payouts/generate/",
            "/api/payouts/statements/",
            "/api/payouts/statements/mine/",
            f"/api/payouts/{self.booking.pk}/finalize/",
        ]:
            with self.subTest(path=legacy_path):
                response = self.client.get(legacy_path)
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_member_cannot_access_tenant_endpoints(self):
        outsider = LeadTeacherFactory()
        TeacherProfileFactory(user=outsider, approved=True)
        self.client.force_authenticate(user=outsider)

        self.assertEqual(
            self.client.get(self.mine_url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get(self.lead_url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.post(self.generate_url, {"period_start": PERIOD_START, "period_end": PERIOD_END}).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_suspended_member_cannot_access_tenant_endpoints(self):
        self.teacher_membership.status = MembershipStatus.SUSPENDED
        self.teacher_membership.save()

        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(
            self.client.get(self.mine_url).status_code, status.HTTP_403_FORBIDDEN
        )
        self.assertEqual(
            self.client.get(self.my_statement_url, PERIOD).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_cross_tenant_finalize_known_id_is_404(self):
        self.generate()
        payout = TeacherPayout.objects.get(teacher=self.teacher)

        other_org = OrganizationFactory(name="Another Academy")
        other_lead = LeadTeacherFactory()
        TeacherProfileFactory(user=other_lead, approved=True)
        admit(other_lead, other_org, role=OrganizationRole.OWNER)

        self.client.force_authenticate(user=other_lead)
        url = self.finalize_url(payout.pk, organization=other_org)
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        payout.refresh_from_db()
        self.assertEqual(payout.status, PayoutStatus.GENERATED)

    def test_generating_for_teacher_from_another_academy_is_refused(self):
        other_org = OrganizationFactory(name="Another Academy")
        foreign_teacher = LeadTeacherFactory()
        TeacherProfileFactory(user=foreign_teacher, approved=True)
        admit(foreign_teacher, other_org, role=OrganizationRole.TEACHER)

        response = self.generate(teacher=foreign_teacher.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher", response.data)

    def test_statement_for_teacher_from_another_academy_is_refused(self):
        other_org = OrganizationFactory(name="Another Academy")
        foreign_teacher = LeadTeacherFactory()
        TeacherProfileFactory(user=foreign_teacher, approved=True)
        admit(foreign_teacher, other_org, role=OrganizationRole.TEACHER)

        self.client.force_authenticate(user=self.lead)
        response = self.client.get(
            self.statements_url, {**PERIOD, "teacher_id": foreign_teacher.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("teacher_id", response.data)
