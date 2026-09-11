"""SaaS Phase 5 — Pricing Tenant Isolation Test Suite (Task 5.5).

Two-academy adversarial test matrix covering:
1. Academy A list cannot return Academy B agreements.
2. Academy A cannot retrieve Academy B agreement by ID.
3. Academy A student cannot access Academy B through `/mine/`.
4. Academy A lead cannot mutate Academy B agreement.
5. Cross-academy student/level creation is rejected.
6. Cross-academy approver is rejected.
7. Legacy unscoped endpoints cannot expose another academy's data.
8. Serializers do not leak tenant-private fields.
9. Empty and mixed-tenant querysets remain isolated.
10. Direct object IDs cannot bypass permissions.

Includes regression tests for valid same-academy pricing workflows.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory
from pricing.models import PricingAgreement, PricingReason
from pricing.serializers import (
    MyPricingAgreementSerializer,
    PricingAgreementCreateSerializer,
)
from pricing.tests.factories import (
    HARDSHIP_RATE,
    PREMIUM_RATE,
    STANDARD_RATE,
    PricingAgreementFactory,
)


class TwoAcademiesPricingFixture(APITestCase):
    """Fixture containing two separate academies and a shared multi-academy student."""

    def setUp(self):
        super().setUp()

        # Academy A
        self.academy_a = OrganizationFactory(name="Academy Alpha")
        self.track_a = TrackFactory(
            organization=self.academy_a, name="Hifz A", slug="hifz-a"
        )
        self.level_a1 = LevelFactory(track=self.track_a, order=1)
        self.level_a2 = LevelFactory(track=self.track_a, order=2)
        self.lead_a = LeadTeacherFactory()
        self.student_a = StudentFactory()
        self.lead_a_membership = admit(
            self.lead_a, self.academy_a, role=OrganizationRole.OWNER
        )
        self.student_a_membership = admit(
            self.student_a, self.academy_a, role=OrganizationRole.STAFF
        )

        # Academy B
        self.academy_b = OrganizationFactory(name="Academy Beta")
        self.track_b = TrackFactory(
            organization=self.academy_b, name="Hifz B", slug="hifz-b"
        )
        self.level_b1 = LevelFactory(track=self.track_b, order=1)
        self.level_b2 = LevelFactory(track=self.track_b, order=2)
        self.lead_b = LeadTeacherFactory()
        self.student_b = StudentFactory()
        self.lead_b_membership = admit(
            self.lead_b, self.academy_b, role=OrganizationRole.OWNER
        )
        self.student_b_membership = admit(
            self.student_b, self.academy_b, role=OrganizationRole.STAFF
        )

        # Multi-academy student (enrolled in both A and B)
        self.multi_student = StudentFactory()
        self.multi_student_membership_a = admit(
            self.multi_student, self.academy_a, role=OrganizationRole.STAFF
        )
        self.multi_student_membership_b = admit(
            self.multi_student, self.academy_b, role=OrganizationRole.STAFF
        )

        # Baseline active agreements
        self.agreement_a1 = PricingAgreementFactory(
            student=self.student_a,
            level=self.level_a1,
            approved_by=self.lead_a,
            standard_rate=STANDARD_RATE,
            agreed_rate=HARDSHIP_RATE,
            reason=PricingReason.DISCOUNT_HARDSHIP,
            notes="Academy A discount for student A",
        )
        self.agreement_b1 = PricingAgreementFactory(
            student=self.student_b,
            level=self.level_b1,
            approved_by=self.lead_b,
            standard_rate=STANDARD_RATE,
            agreed_rate=PREMIUM_RATE,
            reason=PricingReason.PREMIUM_DIRECT,
            notes="Academy B premium for student B",
        )

    def agreements_url(self, org):
        return reverse("pricing:agreements", kwargs={"organization_pk": org.pk})

    def my_agreements_url(self, org):
        return reverse("pricing:agreement-mine", kwargs={"organization_pk": org.pk})


class PricingTenantIsolationTests(TwoAcademiesPricingFixture):
    """Adversarial tests verifying strict tenant isolation between Academy A and B."""

    def test_01_academy_a_list_cannot_return_academy_b_agreements(self):
        """Item 1: Academy A agreement list returns only Academy A agreements."""
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(
            self.agreements_url(self.academy_a), {"student_id": self.student_a.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {row["id"] for row in response.data}
        self.assertIn(self.agreement_a1.pk, returned_ids)
        self.assertNotIn(self.agreement_b1.pk, returned_ids)

        # If Lead A queries for student B, nothing in Academy B is leaked
        response_b = self.client.get(
            self.agreements_url(self.academy_a), {"student_id": self.student_b.pk}
        )
        self.assertEqual(response_b.status_code, status.HTTP_200_OK)
        self.assertEqual(response_b.data, [])

    def test_02_academy_a_cannot_retrieve_academy_b_agreement_by_id(self):
        """Item 2: An agreement belonging to Academy B cannot be queried or scoped under Academy A."""
        # Pricing has no unscoped detail URL, but through queryset isolation:
        scoped_qs = PricingAgreement.objects.in_organization(self.academy_a)
        self.assertTrue(scoped_qs.filter(pk=self.agreement_a1.pk).exists())
        self.assertFalse(scoped_qs.filter(pk=self.agreement_b1.pk).exists())

        # Attempting to fetch Academy B's agreement through Academy A returns empty
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.get(
            self.agreements_url(self.academy_a), {"student_id": self.agreement_b1.student_id}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_03_academy_a_student_cannot_access_academy_b_through_mine(self):
        """Item 3: Academy A student cannot access Academy B's mine endpoint."""
        self.client.force_authenticate(user=self.student_a)

        # Accessing own academy works
        res_own = self.client.get(self.my_agreements_url(self.academy_a))
        self.assertEqual(res_own.status_code, status.HTTP_200_OK)
        self.assertEqual([r["id"] for r in res_own.data], [self.agreement_a1.pk])

        # Accessing Academy B returns 403 Forbidden (not an active member)
        res_foreign = self.client.get(self.my_agreements_url(self.academy_b))
        self.assertEqual(res_foreign.status_code, status.HTTP_403_FORBIDDEN)

    def test_04_academy_a_lead_cannot_mutate_academy_b_agreement(self):
        """Item 4: Academy A lead cannot mutate or supersede Academy B agreement."""
        # 1. Lead A cannot post directly to Academy B
        self.client.force_authenticate(user=self.lead_a)
        payload = {
            "student": self.student_b.pk,
            "level": self.level_b1.pk,
            "standard_rate": str(STANDARD_RATE),
            "agreed_rate": str(HARDSHIP_RATE),
            "reason": PricingReason.DISCOUNT_HARDSHIP,
            "notes": "Attempting cross-tenant overwrite",
        }
        res_cross = self.client.post(self.agreements_url(self.academy_b), payload)
        self.assertEqual(res_cross.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(PricingAgreement.objects.get(pk=self.agreement_b1.pk).active)

        # 2. Lead A posting to Academy A with Academy B's student/level fails
        res_scope = self.client.post(self.agreements_url(self.academy_a), payload)
        self.assertEqual(res_scope.status_code, status.HTTP_400_BAD_REQUEST)

        # Academy B's agreement remains unaffected and active
        self.agreement_b1.refresh_from_db()
        self.assertTrue(self.agreement_b1.active)

    def test_05_cross_academy_student_and_level_creation_is_rejected(self):
        """Item 5: Cross-academy student or level is rejected at API and model layers."""
        self.client.force_authenticate(user=self.lead_a)

        # Foreign level in Academy A
        res_level = self.client.post(
            self.agreements_url(self.academy_a),
            {
                "student": self.student_a.pk,
                "level": self.level_b1.pk,
                "standard_rate": str(STANDARD_RATE),
                "agreed_rate": str(HARDSHIP_RATE),
                "reason": PricingReason.DISCOUNT_HARDSHIP,
            },
        )
        self.assertEqual(res_level.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", res_level.data)

        # Foreign student in Academy A
        res_student = self.client.post(
            self.agreements_url(self.academy_a),
            {
                "student": self.student_b.pk,
                "level": self.level_a2.pk,
                "standard_rate": str(STANDARD_RATE),
                "agreed_rate": str(HARDSHIP_RATE),
                "reason": PricingReason.DISCOUNT_HARDSHIP,
            },
        )
        self.assertEqual(res_student.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", res_student.data)

        # Model clean validation rejects cross-academy student
        agreement_bad_student = PricingAgreement(
            student=self.student_b,
            level=self.level_a2,
            approved_by=self.lead_a,
            standard_rate=STANDARD_RATE,
            agreed_rate=HARDSHIP_RATE,
            reason=PricingReason.DISCOUNT_HARDSHIP,
        )
        with self.assertRaises(ValidationError) as ctx:
            agreement_bad_student.clean()
        self.assertIn("student", ctx.exception.message_dict)

    def test_06_cross_academy_approver_is_rejected(self):
        """Item 6: An approver not belonging to the level's academy is rejected."""
        # Lead B cannot approve an agreement for Academy A
        agreement_bad_approver = PricingAgreement(
            student=self.student_a,
            level=self.level_a2,
            approved_by=self.lead_b,
            standard_rate=STANDARD_RATE,
            agreed_rate=HARDSHIP_RATE,
            reason=PricingReason.DISCOUNT_HARDSHIP,
        )
        with self.assertRaises(ValidationError) as ctx:
            agreement_bad_approver.clean()
        self.assertIn("approved_by", ctx.exception.message_dict)

        # Approver with suspended membership is also rejected
        self.lead_a_membership.status = MembershipStatus.SUSPENDED
        self.lead_a_membership.save()
        agreement_suspended_approver = PricingAgreement(
            student=self.student_a,
            level=self.level_a2,
            approved_by=self.lead_a,
            standard_rate=STANDARD_RATE,
            agreed_rate=HARDSHIP_RATE,
            reason=PricingReason.DISCOUNT_HARDSHIP,
        )
        with self.assertRaises(ValidationError) as ctx:
            agreement_suspended_approver.clean()
        self.assertIn("approved_by", ctx.exception.message_dict)

    def test_07_legacy_unscoped_endpoints_cannot_expose_data(self):
        """Item 7: Legacy unscoped endpoints are retired and return 404."""
        for endpoint in ["/api/pricing/agreements/", "/api/pricing/agreements/mine/"]:
            self.client.force_authenticate(user=self.lead_a)
            self.assertEqual(
                self.client.get(endpoint).status_code, status.HTTP_404_NOT_FOUND
            )
            self.assertEqual(
                self.client.post(endpoint, {}).status_code, status.HTTP_404_NOT_FOUND
            )

            self.client.force_authenticate(user=self.student_a)
            self.assertEqual(
                self.client.get(endpoint).status_code, status.HTTP_404_NOT_FOUND
            )

    def test_08_serializers_do_not_leak_tenant_private_fields(self):
        """Item 8: MyPricingAgreementSerializer hides notes and approver, and write serializer scopes choices."""
        # Family-facing serializer
        student_serializer = MyPricingAgreementSerializer(instance=self.agreement_a1)
        self.assertNotIn("notes", student_serializer.data)
        self.assertNotIn("approved_by", student_serializer.data)

        # Write serializer choices are scoped to the context organization
        create_serializer = PricingAgreementCreateSerializer(
            context={"organization": self.academy_a}
        )
        student_choices = list(create_serializer.fields["student"].queryset)
        level_choices = list(create_serializer.fields["level"].queryset)

        self.assertIn(self.student_a, student_choices)
        self.assertNotIn(self.student_b, student_choices)
        self.assertIn(self.level_a1, level_choices)
        self.assertNotIn(self.level_b1, level_choices)

    def test_09_empty_and_mixed_tenant_querysets_remain_isolated(self):
        """Item 9: Model querysets strictly filter agreements to the requested organization."""
        # Academy A queryset contains only A
        qs_a = PricingAgreement.objects.in_organization(self.academy_a)
        self.assertIn(self.agreement_a1, qs_a)
        self.assertNotIn(self.agreement_b1, qs_a)

        # Academy B queryset contains only B
        qs_b = PricingAgreement.objects.in_organization(self.academy_b)
        self.assertIn(self.agreement_b1, qs_b)
        self.assertNotIn(self.agreement_a1, qs_b)

        # Multi-academy student with agreements in both A and B
        agreement_multi_a = PricingAgreementFactory(
            student=self.multi_student,
            level=self.level_a2,
            approved_by=self.lead_a,
        )
        agreement_multi_b = PricingAgreementFactory(
            student=self.multi_student,
            level=self.level_b2,
            approved_by=self.lead_b,
        )

        qs_multi_a = PricingAgreement.objects.in_organization(self.academy_a).filter(
            student=self.multi_student
        )
        self.assertEqual(list(qs_multi_a), [agreement_multi_a])

        qs_multi_b = PricingAgreement.objects.in_organization(self.academy_b).filter(
            student=self.multi_student
        )
        self.assertEqual(list(qs_multi_b), [agreement_multi_b])

    def test_10_direct_object_ids_cannot_bypass_permissions(self):
        """Item 10: Supplying another tenant's object ID cannot bypass tenant boundaries."""
        # Lead A tries to create an agreement for multi_student using level_b1
        self.client.force_authenticate(user=self.lead_a)
        response = self.client.post(
            self.agreements_url(self.academy_a),
            {
                "student": self.multi_student.pk,
                "level": self.level_b1.pk,
                "standard_rate": str(STANDARD_RATE),
                "agreed_rate": str(HARDSHIP_RATE),
                "reason": PricingReason.DISCOUNT_HARDSHIP,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("level", response.data)

        # Student A cannot supply Academy B's organization pk to /mine/
        self.client.force_authenticate(user=self.student_a)
        response_mine = self.client.get(self.my_agreements_url(self.academy_b))
        self.assertEqual(response_mine.status_code, status.HTTP_403_FORBIDDEN)

    def test_regression_same_academy_full_lifecycle(self):
        """Regression test verifying the complete pricing lifecycle works within one academy."""
        self.client.force_authenticate(user=self.lead_a)

        # 1. Lead creates initial agreement for level_a2
        create_res = self.client.post(
            self.agreements_url(self.academy_a),
            {
                "student": self.student_a.pk,
                "level": self.level_a2.pk,
                "standard_rate": str(STANDARD_RATE),
                "agreed_rate": str(HARDSHIP_RATE),
                "reason": PricingReason.DISCOUNT_HARDSHIP,
                "notes": "Initial rate",
            },
        )
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        first_id = create_res.data["id"]

        # 2. Student reads their rates
        self.client.force_authenticate(user=self.student_a)
        mine_res = self.client.get(self.my_agreements_url(self.academy_a))
        self.assertEqual(mine_res.status_code, status.HTTP_200_OK)
        active_ids = {r["id"] for r in mine_res.data}
        self.assertIn(first_id, active_ids)

        # 3. Lead supersedes agreement with premium rate
        self.client.force_authenticate(user=self.lead_a)
        supersede_res = self.client.post(
            self.agreements_url(self.academy_a),
            {
                "student": self.student_a.pk,
                "level": self.level_a2.pk,
                "standard_rate": str(STANDARD_RATE),
                "agreed_rate": str(PREMIUM_RATE),
                "reason": PricingReason.PREMIUM_DIRECT,
                "notes": "Premium upgrade",
            },
        )
        self.assertEqual(supersede_res.status_code, status.HTTP_201_CREATED)
        second_id = supersede_res.data["id"]

        # 4. Lead views history: both exist, newest first
        history_res = self.client.get(
            self.agreements_url(self.academy_a), {"student_id": self.student_a.pk}
        )
        self.assertEqual(history_res.status_code, status.HTTP_200_OK)
        level_a2_entries = [
            row for row in history_res.data if row["level"]["id"] == self.level_a2.pk
        ]
        self.assertEqual(len(level_a2_entries), 2)
        self.assertEqual(level_a2_entries[0]["id"], second_id)
        self.assertTrue(level_a2_entries[0]["active"])
        self.assertEqual(level_a2_entries[1]["id"], first_id)
        self.assertFalse(level_a2_entries[1]["active"])
