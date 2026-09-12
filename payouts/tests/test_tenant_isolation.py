"""SaaS Phase 7 — Teacher Payout Tenant Isolation Test Suite (Task 7.7).

Two-academy adversarial test matrix covering:
1. Academy separation: Academy A lead sees only Academy A payouts; Academy B lead sees only Academy B.
2. Teacher separation: Teacher A1 sees only their own payouts; cannot see Teacher A2's; cannot see Academy B's.
3. Multi-academy teacher isolation: A teacher belonging to both academies sees only tenant-scoped records per URL.
4. Known-id attacks: Cross-tenant GET / finalize returns 404.
5. Generation isolation: Generating in Academy A creates payouts only in Academy A; Academy B is untouched.
6. Cross-tenant ORM relationships: Direct ORM writes reject cross-tenant booking/teacher/cohort combinations.
7. Membership states: Active member allowed; suspended and non-member denied (403).
8. Role states: Owner/Admin manage; Teacher views own; Student/Parent denied (403).
9. Historical immutability: Finalized payout remains unchanged even after teacher rate change.
10. Repeat generation idempotency: Multiple runs over same period produce no duplicates.
"""

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
)
from curriculum.tests.factories import GroupEligibleLevelFactory, LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from scheduling.models import Booking, Cohort
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    CohortFactory,
    ensure_teacher_configured,
    teaches,
)

from payouts.models import PayoutStatus, TeacherPayout
from payouts.services import generate_payouts, payouts_for, statement_for
from payouts.tests.factories import TeacherPayoutFactory, past_session


class TwoAcademiesPayoutFixture(APITestCase):
    """Fixture containing two distinct academies, teachers, bookings, and a shared teacher."""

    def setUp(self):
        super().setUp()
        self.period_start = dj_timezone.now() - timedelta(weeks=4)
        self.period_end = dj_timezone.now() + timedelta(weeks=4)
        self.period = {
            "start": self.period_start.isoformat(),
            "end": self.period_end.isoformat(),
        }

        # ---------------------------------------------------------------------
        # Academy A
        # ---------------------------------------------------------------------
        self.org_a = OrganizationFactory(name="Academy Alpha")
        self.track_a = TrackFactory(organization=self.org_a, name="Track A", slug="track-a")
        self.level_a = LevelFactory(track=self.track_a, order=1)

        self.lead_a = LeadTeacherFactory()
        TeacherProfileFactory(user=self.lead_a, approved=True, hourly_payout_rate=None)
        self.lead_a_membership = admit(self.lead_a, self.org_a, role=OrganizationRole.OWNER)

        self.admin_a = SubTeacherFactory()
        TeacherProfileFactory(user=self.admin_a, approved=True, hourly_payout_rate=None)
        self.admin_a_membership = admit(self.admin_a, self.org_a, role=OrganizationRole.ADMIN)

        self.student_a = StudentFactory()
        self.student_a_membership = admit(self.student_a, self.org_a, role=OrganizationRole.STAFF)

        self.parent_a = ParentFactory()
        self.parent_a_membership = admit(self.parent_a, self.org_a, role=OrganizationRole.STAFF)
        ParentLinkFactory(parent=self.parent_a, student=self.student_a)

        # Teacher A1
        self.window_a1 = AvailabilityFactory(
            organization=self.org_a,
            teacher__teacher_profile__hourly_payout_rate=Decimal("5000.00"),
        )
        self.teacher_a1 = self.window_a1.teacher
        self.teacher_a1_membership = admit(
            self.teacher_a1, self.org_a, role=OrganizationRole.TEACHER
        )
        teaches(self.teacher_a1, self.level_a)
        self.booking_a1 = past_session(
            availability=self.window_a1,
            teacher=self.teacher_a1,
            student=self.student_a,
            level=self.level_a,
            duration_minutes=60,
        )

        # Teacher A2
        self.window_a2 = AvailabilityFactory(
            organization=self.org_a,
            teacher__teacher_profile__hourly_payout_rate=Decimal("6000.00"),
        )
        self.teacher_a2 = self.window_a2.teacher
        self.teacher_a2_membership = admit(
            self.teacher_a2, self.org_a, role=OrganizationRole.TEACHER
        )
        teaches(self.teacher_a2, self.level_a)
        self.booking_a2 = past_session(
            availability=self.window_a2,
            teacher=self.teacher_a2,
            student=self.student_a,
            level=self.level_a,
            duration_minutes=60,
        )

        # ---------------------------------------------------------------------
        # Academy B
        # ---------------------------------------------------------------------
        self.org_b = OrganizationFactory(name="Academy Beta")
        self.track_b = TrackFactory(organization=self.org_b, name="Track B", slug="track-b")
        self.level_b = LevelFactory(track=self.track_b, order=1)

        self.lead_b = LeadTeacherFactory()
        TeacherProfileFactory(user=self.lead_b, approved=True, hourly_payout_rate=None)
        self.lead_b_membership = admit(self.lead_b, self.org_b, role=OrganizationRole.OWNER)

        self.student_b = StudentFactory()
        self.student_b_membership = admit(self.student_b, self.org_b, role=OrganizationRole.STAFF)

        # Teacher B1
        self.window_b1 = AvailabilityFactory(
            organization=self.org_b,
            teacher__teacher_profile__hourly_payout_rate=Decimal("7000.00"),
        )
        self.teacher_b1 = self.window_b1.teacher
        self.teacher_b1_membership = admit(
            self.teacher_b1, self.org_b, role=OrganizationRole.TEACHER
        )
        teaches(self.teacher_b1, self.level_b)
        self.booking_b1 = past_session(
            availability=self.window_b1,
            teacher=self.teacher_b1,
            student=self.student_b,
            level=self.level_b,
            duration_minutes=60,
        )

        # ---------------------------------------------------------------------
        # Shared Multi-Academy Teacher
        # ---------------------------------------------------------------------
        self.shared_teacher = BookableTeacherFactory()
        self.shared_teacher.teacher_profile.hourly_payout_rate = Decimal("4500.00")
        self.shared_teacher.teacher_profile.save()
        self.shared_teacher_m_a = admit(
            self.shared_teacher, self.org_a, role=OrganizationRole.TEACHER
        )
        self.shared_teacher_m_b = admit(
            self.shared_teacher, self.org_b, role=OrganizationRole.TEACHER
        )
        teaches(self.shared_teacher, self.level_a)
        teaches(self.shared_teacher, self.level_b)

        self.shared_window_a = AvailabilityFactory(
            organization=self.org_a, teacher=self.shared_teacher
        )
        self.booking_shared_a = past_session(
            availability=self.shared_window_a,
            teacher=self.shared_teacher,
            student=self.student_a,
            level=self.level_a,
            duration_minutes=60,
        )
        self.shared_window_b = AvailabilityFactory(
            organization=self.org_b, teacher=self.shared_teacher
        )
        self.booking_shared_b = past_session(
            availability=self.shared_window_b,
            teacher=self.shared_teacher,
            student=self.student_b,
            level=self.level_b,
            duration_minutes=60,
        )

    def mine_url(self, org):
        return reverse("payouts:payout-mine", kwargs={"organization_pk": org.pk})

    def my_statement_url(self, org):
        return reverse("payouts:statement-mine", kwargs={"organization_pk": org.pk})

    def lead_url(self, org):
        return reverse("payouts:payout-lead", kwargs={"organization_pk": org.pk})

    def statements_url(self, org):
        return reverse("payouts:statements", kwargs={"organization_pk": org.pk})

    def generate_url(self, org):
        return reverse("payouts:payout-generate", kwargs={"organization_pk": org.pk})

    def finalize_url(self, org, pk):
        return reverse("payouts:payout-finalize", kwargs={"organization_pk": org.pk, "pk": pk})


class PayoutTenantIsolationTests(TwoAcademiesPayoutFixture):
    """Adversarial cross-tenant security and isolation tests."""

    def test_academy_separation_in_lead_listings(self):
        payout_a = TeacherPayoutFactory(booking=self.booking_a1)
        payout_b = TeacherPayoutFactory(booking=self.booking_b1)

        # Lead A sees only Academy A
        self.client.force_authenticate(user=self.lead_a)
        resp_a = self.client.get(self.lead_url(self.org_a))
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        ids_a = [r["id"] for r in resp_a.data]
        self.assertIn(payout_a.pk, ids_a)
        self.assertNotIn(payout_b.pk, ids_a)

        # Lead B sees only Academy B
        self.client.force_authenticate(user=self.lead_b)
        resp_b = self.client.get(self.lead_url(self.org_b))
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        ids_b = [r["id"] for r in resp_b.data]
        self.assertIn(payout_b.pk, ids_b)
        self.assertNotIn(payout_a.pk, ids_b)

    def test_teacher_separation_in_mine_listings(self):
        payout_a1 = TeacherPayoutFactory(booking=self.booking_a1)
        payout_a2 = TeacherPayoutFactory(booking=self.booking_a2)
        payout_b1 = TeacherPayoutFactory(booking=self.booking_b1)

        self.client.force_authenticate(user=self.teacher_a1)
        resp = self.client.get(self.mine_url(self.org_a))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [r["id"] for r in resp.data]
        self.assertEqual(ids, [payout_a1.pk])
        self.assertNotIn(payout_a2.pk, ids)
        self.assertNotIn(payout_b1.pk, ids)

    def test_multi_academy_teacher_sees_only_current_academy_payouts(self):
        payout_shared_a = TeacherPayoutFactory(booking=self.booking_shared_a)
        payout_shared_b = TeacherPayoutFactory(booking=self.booking_shared_b)

        self.client.force_authenticate(user=self.shared_teacher)

        # In Academy A
        resp_a = self.client.get(self.mine_url(self.org_a))
        self.assertEqual(resp_a.status_code, status.HTTP_200_OK)
        ids_a = [r["id"] for r in resp_a.data]
        self.assertEqual(ids_a, [payout_shared_a.pk])
        self.assertNotIn(payout_shared_b.pk, ids_a)

        # In Academy B
        resp_b = self.client.get(self.mine_url(self.org_b))
        self.assertEqual(resp_b.status_code, status.HTTP_200_OK)
        ids_b = [r["id"] for r in resp_b.data]
        self.assertEqual(ids_b, [payout_shared_b.pk])
        self.assertNotIn(payout_shared_a.pk, ids_b)

    def test_known_id_attack_finalize_returns_404(self):
        payout_a = TeacherPayoutFactory(booking=self.booking_a1)

        # Lead B tries to finalize Academy A's payout via Academy B URL
        self.client.force_authenticate(user=self.lead_b)
        response = self.client.post(self.finalize_url(self.org_b, payout_a.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        payout_a.refresh_from_db()
        self.assertEqual(payout_a.status, PayoutStatus.GENERATED)

    def test_cross_tenant_generation_isolation(self):
        # Generate in Academy A
        self.client.force_authenticate(user=self.lead_a)
        resp_a = self.client.post(
            self.generate_url(self.org_a),
            {
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
            },
        )
        self.assertEqual(resp_a.status_code, status.HTTP_201_CREATED)
        # Academy A has bookings: booking_a1, booking_a2, booking_shared_a
        self.assertEqual(resp_a.data["created_count"], 3)
        self.assertEqual(
            TeacherPayout.objects.in_organization(self.org_a).count(), 3
        )
        # Academy B has 0 payouts
        self.assertEqual(
            TeacherPayout.objects.in_organization(self.org_b).count(), 0
        )

    def test_model_invariants_reject_cross_academy_relationships(self):
        # 1. Booking from Academy A, Teacher with membership only in Academy B
        payout_bad_teacher = TeacherPayout(
            booking=self.booking_a1,
            teacher=self.teacher_b1,
            minutes_paid=60,
            rate_used=Decimal("7000.00"),
            amount=Decimal("7000.00"),
        )
        with self.assertRaises(ValidationError) as cm:
            payout_bad_teacher.full_clean()
        self.assertIn("teacher", cm.exception.message_dict)

        # 2. Booking from Academy A, Cohort from Academy B
        group_level_b = GroupEligibleLevelFactory(track=self.track_b)
        teaches(self.teacher_b1, group_level_b)
        cohort_b = CohortFactory(
            level=group_level_b,
            teacher=self.teacher_b1,
            availability=self.window_b1,
        )
        payout_cross_cohort = TeacherPayout(
            booking=self.booking_a1,
            teacher=self.teacher_a1,
            cohort=cohort_b,
            minutes_paid=60,
            rate_used=Decimal("5000.00"),
            amount=Decimal("5000.00"),
        )
        with self.assertRaises(ValidationError) as cm:
            payout_cross_cohort.full_clean()
        self.assertIn("cohort", cm.exception.message_dict)

    def test_membership_states_authorization(self):
        # Suspended member in Academy A receives 403
        self.teacher_a1_membership.status = MembershipStatus.SUSPENDED
        self.teacher_a1_membership.save()

        self.client.force_authenticate(user=self.teacher_a1)
        self.assertEqual(
            self.client.get(self.mine_url(self.org_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # Non-member receives 403
        outsider = SubTeacherFactory()
        TeacherProfileFactory(user=outsider, approved=True)
        self.client.force_authenticate(user=outsider)
        self.assertEqual(
            self.client.get(self.mine_url(self.org_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.get(self.lead_url(self.org_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_role_matrix_authorization(self):
        # 1. Owner & Admin can manage
        for user, name in [(self.lead_a, "owner"), (self.admin_a, "admin")]:
            with self.subTest(role=name):
                self.client.force_authenticate(user=user)
                self.assertEqual(
                    self.client.get(self.lead_url(self.org_a)).status_code,
                    status.HTTP_200_OK,
                )

        # 2. Teacher cannot manage
        self.client.force_authenticate(user=self.teacher_a1)
        self.assertEqual(
            self.client.get(self.lead_url(self.org_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.post(
                self.generate_url(self.org_a),
                {
                    "period_start": self.period_start.isoformat(),
                    "period_end": self.period_end.isoformat(),
                },
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

        # 3. Student has NO payout access
        self.client.force_authenticate(user=self.student_a)
        for url in [
            self.mine_url(self.org_a),
            self.lead_url(self.org_a),
            self.my_statement_url(self.org_a),
            self.statements_url(self.org_a),
            self.generate_url(self.org_a),
        ]:
            with self.subTest(url=url, user="student"):
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_403_FORBIDDEN
                )

        # 4. Parent has NO payout access
        self.client.force_authenticate(user=self.parent_a)
        for url in [
            self.mine_url(self.org_a),
            self.lead_url(self.org_a),
            self.my_statement_url(self.org_a),
            self.statements_url(self.org_a),
            self.generate_url(self.org_a),
        ]:
            with self.subTest(url=url, user="parent"):
                self.assertEqual(
                    self.client.get(url).status_code, status.HTTP_403_FORBIDDEN
                )

    def test_historical_immutability_after_rate_change(self):
        payout = TeacherPayoutFactory(booking=self.booking_a1)
        payout.finalize()

        # Update teacher profile rate
        self.teacher_a1.teacher_profile.hourly_payout_rate = Decimal("9999.00")
        self.teacher_a1.teacher_profile.save()

        payout.refresh_from_db()
        self.assertEqual(payout.rate_used, Decimal("5000.00"))
        self.assertEqual(payout.amount, Decimal("5000.00"))
        self.assertEqual(payout.status, PayoutStatus.FINALIZED)

    def test_repeat_generation_is_idempotent(self):
        self.client.force_authenticate(user=self.lead_a)
        payload = {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
        }

        # Run 1
        resp1 = self.client.post(self.generate_url(self.org_a), payload)
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        created_count = resp1.data["created_count"]
        self.assertGreater(created_count, 0)

        # Run 2
        resp2 = self.client.post(self.generate_url(self.org_a), payload)
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data["created_count"], 0)
        self.assertEqual(resp2.data["skipped_count"], created_count)
        self.assertEqual(
            TeacherPayout.objects.in_organization(self.org_a).count(),
            created_count,
        )
