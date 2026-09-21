"""Tests for payouts role scoping stabilization (Section 8 of IQRA_BE_ROLE_ONBOARDING_CLASS_STABILIZATION.md)."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.tests.factories import StudentFactory, SubTeacherFactory, UserFactory
from organizations.models import MembershipStatus, OrganizationMembership, OrganizationRole
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory


class RolePayoutsStabilizationTests(APITestCase):
    def setUp(self):
        self.org = OrganizationFactory()

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

    def test_payout_manager_permissions(self):
        url = reverse("payouts:payout-lead", args=[self.org.pk])

        # 1. Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 2. Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. Lead teacher allowed
        self.client.force_authenticate(user=self.lead_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 4. Ordinary sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Student denied
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_payout_teacher_self_service_permissions(self):
        url = reverse("payouts:payout-mine", args=[self.org.pk])

        # 1. Active teacher allowed
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 2. Student denied
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
