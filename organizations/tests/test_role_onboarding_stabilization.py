"""Tests for role, onboarding, and student access stabilization (Section 9 & 10 of IQRA_BE_ROLE_ONBOARDING_CLASS_STABILIZATION.md)."""

import hashlib
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import ParentLink, Role, User
from accounts.tests.factories import ParentFactory, StudentFactory, SubTeacherFactory, UserFactory
from curriculum.tests.factories import LevelFactory, TrackFactory
from organizations.models import (
    InvitationStatus,
    MembershipStatus,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
    StudentEnrollment,
)
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory
from scheduling.models import Booking


class RoleOnboardingStabilizationTests(APITestCase):
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

        self.parent = ParentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )

        self.student = StudentFactory()
        OrganizationMembershipFactory(
            organization=self.org,
            user=self.student,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        )
        ParentLink.objects.create(parent=self.parent, student=self.student)

        self.track = TrackFactory(organization=self.org)
        self.level = LevelFactory(track=self.track)
        self.enrollment = StudentEnrollment.objects.create(
            organization=self.org,
            user=self.student,
            track=self.track,
            level=self.level,
        )

    def _create_invitation(self, email: str, role: str, raw_token: str = "secure-token-12345"):
        token_digest = hashlib.sha256(raw_token.encode()).hexdigest()
        return OrganizationInvitation.objects.create(
            organization=self.org,
            email=email,
            role=role,
            token_digest=token_digest,
            status=InvitationStatus.PENDING,
            expires_at=timezone.now() + timedelta(days=7),
        )

    # --- Section 10: Invitation acceptance & registration tests ---

    def test_brand_new_teacher_registers_from_invitation(self):
        raw_token = "teacher-token-abc"
        self._create_invitation("newteacher@example.com", OrganizationRole.TEACHER, raw_token)

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        payload = {
            "token": raw_token,
            "username": "newteacher",
            "first_name": "New",
            "last_name": "Teacher",
            "password": "StrongPassword123!#",
            "timezone": "UTC",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn("key", res.data)

        user = User.objects.get(email="newteacher@example.com")
        self.assertEqual(user.role, Role.SUB)
        self.assertEqual(user.username, "newteacher")

        membership = OrganizationMembership.objects.get(organization=self.org, user=user)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)

    def test_brand_new_parent_registers_from_invitation(self):
        raw_token = "parent-token-abc"
        self._create_invitation("newparent@example.com", OrganizationRole.PARENT, raw_token)

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        payload = {
            "token": raw_token,
            "password": "StrongPassword123!#",
            "timezone": "UTC",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="newparent@example.com")
        self.assertEqual(user.role, Role.PARENT)
        membership = OrganizationMembership.objects.get(organization=self.org, user=user)
        self.assertEqual(membership.role, OrganizationRole.PARENT)

    def test_brand_new_student_registers_from_invitation(self):
        raw_token = "student-token-abc"
        self._create_invitation("newstudent@example.com", OrganizationRole.STUDENT, raw_token)

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        payload = {
            "token": raw_token,
            "password": "StrongPassword123!#",
            "timezone": "UTC",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="newstudent@example.com")
        self.assertEqual(user.role, Role.STUDENT)
        membership = OrganizationMembership.objects.get(organization=self.org, user=user)
        self.assertEqual(membership.role, OrganizationRole.STUDENT)

    def test_client_cannot_escalate_role_via_invitation_payload(self):
        raw_token = "escalate-token-abc"
        self._create_invitation("teacher.escalate@example.com", OrganizationRole.TEACHER, raw_token)

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        payload = {
            "token": raw_token,
            "password": "StrongPassword123!#",
            "timezone": "UTC",
            "role": "lead",
            "organization_role": "owner",
        }
        res = self.client.post(url, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="teacher.escalate@example.com")
        self.assertEqual(user.role, Role.SUB)  # Not lead

        membership = OrganizationMembership.objects.get(organization=self.org, user=user)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)  # Not owner

    def test_existing_account_accepts_invitation(self):
        raw_token = "existing-teacher-token"
        teacher_user = SubTeacherFactory(email="existing.sub@example.com")
        inv = self._create_invitation("existing.sub@example.com", OrganizationRole.TEACHER, raw_token)

        self.client.force_authenticate(user=teacher_user)
        url = reverse("organizations:invitation-accept", args=[self.org.pk])
        res = self.client.post(url, {"token": raw_token})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        inv.refresh_from_db()
        self.assertEqual(inv.status, InvitationStatus.ACCEPTED)
        self.assertTrue(
            OrganizationMembership.objects.filter(
                organization=self.org, user=teacher_user, role=OrganizationRole.TEACHER
            ).exists()
        )

    def test_invitation_wrong_email_rejected_on_accept(self):
        raw_token = "wrong-email-token"
        self._create_invitation("intended@example.com", OrganizationRole.TEACHER, raw_token)

        different_user = SubTeacherFactory(email="different@example.com")
        self.client.force_authenticate(user=different_user)
        url = reverse("organizations:invitation-accept", args=[self.org.pk])
        res = self.client.post(url, {"token": raw_token})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("different email", str(res.data).lower())

    def test_expired_invitation_rejected(self):
        raw_token = "expired-token"
        inv = self._create_invitation("expired@example.com", OrganizationRole.TEACHER, raw_token)
        inv.expires_at = timezone.now() - timedelta(minutes=1)
        inv.save()

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        res = self.client.post(url, {"token": raw_token, "password": "StrongPassword123!#", "timezone": "UTC"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", str(res.data).lower())

    def test_reused_invitation_rejected(self):
        raw_token = "reused-token"
        inv = self._create_invitation("reused@example.com", OrganizationRole.TEACHER, raw_token)
        inv.status = InvitationStatus.ACCEPTED
        inv.save()

        url = reverse("organizations:invitation-accept-and-register", args=[self.org.pk])
        res = self.client.post(url, {"token": raw_token, "password": "StrongPassword123!#", "timezone": "UTC"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("no longer pending", str(res.data).lower())

    # --- Section 9: Student Directory Scoping Tests ---

    def test_admin_student_directory_permissions(self):
        url = reverse("organizations:student-list", args=[self.org.pk])

        # Owner allowed
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Admin allowed
        self.client.force_authenticate(user=self.admin_user)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Sub-teacher denied
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Parent denied
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Student denied
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_student_enrollment_scoping(self):
        url = reverse("organizations:student-mine-list", args=[self.org.pk])

        # 1. Owner sees all students
        self.client.force_authenticate(user=self.owner)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], self.student.id)

        # 2. Teacher with NO booking sees empty list
        self.client.force_authenticate(user=self.sub_teacher)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 0)

        # 3. Create a Booking between sub_teacher and student
        Booking.objects.bulk_create([
            Booking(
                student=self.student,
                teacher=self.sub_teacher,
                level=self.level,
                start_time_utc=timezone.now() + timedelta(days=1),
                duration_minutes=30,
            )
        ])

        # Now sub_teacher sees assigned student
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], self.student.id)

        # 4. Parent sees linked child
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], self.student.id)

        # 5. Student denied access
        self.client.force_authenticate(user=self.student)
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
