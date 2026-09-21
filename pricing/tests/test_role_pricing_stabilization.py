"""Tests for pricing role scoping stabilization (Section 7 of IQRA_BE_ROLE_ONBOARDING_CLASS_STABILIZATION.md)."""

from decimal import Decimal
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.tests.factories import StudentFactory, SubTeacherFactory, UserFactory
from curriculum.tests.factories import LevelFactory, TrackFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from pricing.models import PricingAgreement, PricingReason


class RolePricingStabilizationTests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()
        self.other_org = OrganizationFactory()

        self.owner = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

        self.admin_user = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.admin_user,
            role=OrganizationRole.ADMIN,
            status=MembershipStatus.ACTIVE,
        )

        self.lead_teacher = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.lead_teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

        self.sub_teacher = SubTeacherFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.sub_teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

        self.student = StudentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        )

        self.track = TrackFactory(organization=self.org)
        self.level = LevelFactory(track=self.track)

    def test_pricing_agreement_role_permissions(self):
        url = reverse("pricing:agreements", args=[self.org.pk])
        params = {"student_id": self.student.id}

        # 1. Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 2. Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. Lead teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 4. Ordinary sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Student denied
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url, params)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_pricing_agreement_clean_approved_by(self):
        agreement = PricingAgreement(
            student=self.student,
            level=self.level,
            agreed_rate=Decimal("35.00"),
            reason=PricingReason.PREMIUM_DIRECT,
        )

        # 1. Owner allowed
        agreement.approved_by = self.owner
        agreement.clean()

        # 2. Admin allowed
        agreement.approved_by = self.admin_user
        agreement.clean()

        # 3. Lead teacher allowed
        agreement.approved_by = self.lead_teacher
        agreement.clean()

        # 4. Sub-teacher denied
        agreement.approved_by = self.sub_teacher
        with self.assertRaises(ValidationError) as ctx:
            agreement.clean()
        self.assertIn("approved_by", ctx.exception.message_dict)

        # 5. User from other org denied
        other_user = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            organization=self.other_org,
            user=other_user,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        agreement.approved_by = other_user
        with self.assertRaises(ValidationError) as ctx:
            agreement.clean()
        self.assertIn("approved_by", ctx.exception.message_dict)
