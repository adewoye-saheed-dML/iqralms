import datetime
import hashlib
from unittest.mock import patch

from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.tests.factories import ParentFactory, StudentFactory, SubTeacherFactory, UserFactory
from audit_logs.models import AuditAction, AuditLog
from organizations.models import (
    InvitationDelivery,
    InvitationStatus,
    MembershipStatus,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import (
    OrganizationMembershipFactory,
    SuspendedMembershipFactory,
    academy,
)
from organizations.tests.test_api import TenantWorld


def invitations_url(organization):
    return reverse("organizations:invitation-list", args=[organization.pk])


def invitation_preview_url(organization):
    return reverse("organizations:invitation-preview", args=[organization.pk])


def accept_invitation_url(organization):
    return reverse("organizations:invitation-accept", args=[organization.pk])


def resend_invitation_url(organization, pk):
    return reverse("organizations:invitation-resend", args=[organization.pk, pk])


def revoke_invitation_url(organization, pk):
    return reverse("organizations:invitation-revoke", args=[organization.pk, pk])


class InvitationCreationTests(TenantWorld):
    def test_owner_can_invite_teacher(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "newteacher@example.com", "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], "newteacher@example.com")
        self.assertEqual(response.data["role"], "teacher")
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["email_delivery_status"], "sent")
        self.assertNotIn("raw_token", response.data)
        self.assertNotIn("token", response.data)

        inv = OrganizationInvitation.objects.get(email="newteacher@example.com", organization=self.org_a)
        self.assertEqual(inv.role, OrganizationRole.TEACHER)
        self.assertEqual(inv.status, InvitationStatus.PENDING)
        self.assertEqual(inv.deliveries.count(), 1)
        self.assertEqual(inv.deliveries.first().status, "sent")

        # Email content verification
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertIn("Academy A", sent.subject)
        self.assertIn("/accept-invitation?organization=", sent.body)
        self.assertIn("as a teacher", sent.body)

    def test_admin_can_invite_teacher(self):
        response = self.as_user(self.admin_a).post(
            invitations_url(self.org_a),
            {"email": "teacher_by_admin@example.com", "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_owner_can_invite_parent(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "parent@example.com", "role": "parent"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "parent")
        inv = OrganizationInvitation.objects.get(email="parent@example.com", organization=self.org_a)
        self.assertEqual(inv.role, OrganizationRole.PARENT)
        self.assertIn("as a parent", mail.outbox[0].body)

    def test_owner_can_invite_student(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "student@example.com", "role": "student"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "student")
        inv = OrganizationInvitation.objects.get(email="student@example.com", organization=self.org_a)
        self.assertEqual(inv.role, OrganizationRole.STUDENT)
        self.assertIn("as a student", mail.outbox[0].body)

    def test_owner_can_invite_admin(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "admin2@example.com", "role": "admin"},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], "admin")
        self.assertIn("as an administrator", mail.outbox[0].body)

    def test_unauthorized_roles_cannot_invite(self):
        parent_user = ParentFactory()
        student_user = StudentFactory()
        OrganizationMembershipFactory(organization=self.org_a, user=parent_user, role=OrganizationRole.PARENT)
        OrganizationMembershipFactory(organization=self.org_a, user=student_user, role=OrganizationRole.STUDENT)

        for caller in (self.teacher_a, self.staff_a, parent_user, student_user, self.outsider):
            response = self.as_user(caller).post(
                invitations_url(self.org_a),
                {"email": f"test_{caller.username}@example.com", "role": "teacher"},
            )
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_role_cannot_be_invited(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "newowner@example.com", "role": "owner"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_role_cannot_be_invited(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "newstaff@example.com", "role": "staff"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malformed_email_rejected(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "not-an-email", "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_pending_invitation_rejected(self):
        self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "dup@example.com", "role": "teacher"},
        )
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": "DUP@example.com", "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("A pending invitation already exists", str(response.data))

    def test_existing_active_member_cannot_be_invited(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": self.teacher_a.email, "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already a member", str(response.data))

    def test_existing_suspended_member_cannot_be_invited(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_a),
            {"email": self.suspended_a.email, "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("suspended", str(response.data))

    def test_email_delivery_failure_leaves_invitation_pending_with_failed_delivery(self):
        with patch("django.core.mail.send_mail", side_effect=RuntimeError("SMTP connection timed out")):
            response = self.as_user(self.owner_a).post(
                invitations_url(self.org_a),
                {"email": "faildelivery@example.com", "role": "teacher"},
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email_delivery_status"], "failed")

        inv = OrganizationInvitation.objects.get(email="faildelivery@example.com", organization=self.org_a)
        self.assertEqual(inv.status, InvitationStatus.PENDING)
        self.assertEqual(inv.deliveries.count(), 1)
        delivery = inv.deliveries.first()
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.error_code, "SMTP_ERROR")

        # Verify audit log recorded email failure
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org_a,
                action=AuditAction.INVITATION_EMAIL_FAILED,
            ).exists()
        )

    def test_tenant_isolation_cannot_invite_other_org(self):
        response = self.as_user(self.owner_a).post(
            invitations_url(self.org_b),
            {"email": "cross@example.com", "role": "teacher"},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class InvitationPreviewTests(TenantWorld):
    def setUp(self):
        super().setUp()
        self.token, self.digest = OrganizationInvitation.generate_token_and_digest()
        self.invitation = OrganizationInvitation.objects.create(
            organization=self.org_a,
            email="preview@example.com",
            role=OrganizationRole.TEACHER,
            token_digest=self.digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

    def test_unauthenticated_can_preview_invitation(self):
        self.client.logout()
        response = self.client.get(
            f"{invitation_preview_url(self.org_a)}?token={self.token}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["organization_id"], self.org_a.id)
        self.assertEqual(response.data["organization_name"], self.org_a.name)
        self.assertEqual(response.data["role"], "teacher")
        self.assertEqual(response.data["status"], "pending")
        self.assertNotIn("email", response.data)
        self.assertNotIn("token", response.data)

    def test_preview_missing_token_returns_400(self):
        response = self.client.get(invitation_preview_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_preview_invalid_token_returns_404(self):
        response = self.client.get(
            f"{invitation_preview_url(self.org_a)}?token=nonexistent-token"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_preview_expired_token_returns_expired_status(self):
        self.invitation.expires_at = timezone.now() - datetime.timedelta(days=1)
        self.invitation.save(update_fields=["expires_at"])

        response = self.client.get(
            f"{invitation_preview_url(self.org_a)}?token={self.token}"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "expired")


class InvitationResendAndRevokeTests(TenantWorld):
    def setUp(self):
        super().setUp()
        self.token, self.digest = OrganizationInvitation.generate_token_and_digest()
        self.invitation = OrganizationInvitation.objects.create(
            organization=self.org_a,
            email="resend@example.com",
            role=OrganizationRole.TEACHER,
            token_digest=self.digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

    def test_owner_can_resend_invitation(self):
        mail.outbox.clear()
        response = self.as_user(self.owner_a).post(
            resend_invitation_url(self.org_a, self.invitation.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invitation.refresh_from_db()
        self.assertNotEqual(self.invitation.token_digest, self.digest)
        self.assertEqual(len(mail.outbox), 1)

        # Old token no longer accepted
        user = SubTeacherFactory(email="resend@example.com")
        accept_res = self.as_user(user).post(
            accept_invitation_url(self.org_a), {"token": self.token}
        )
        self.assertEqual(accept_res.status_code, status.HTTP_400_BAD_REQUEST)

        # Resend audit log
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org_a,
                action=AuditAction.INVITATION_RESENT,
            ).exists()
        )

    def test_cannot_resend_accepted_invitation(self):
        self.invitation.status = InvitationStatus.ACCEPTED
        self.invitation.save(update_fields=["status"])

        response = self.as_user(self.owner_a).post(
            resend_invitation_url(self.org_a, self.invitation.id)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_can_revoke_invitation(self):
        response = self.as_user(self.owner_a).post(
            revoke_invitation_url(self.org_a, self.invitation.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.status, InvitationStatus.REVOKED)

        # Revoked token cannot be accepted
        user = SubTeacherFactory(email="resend@example.com")
        accept_res = self.as_user(user).post(
            accept_invitation_url(self.org_a), {"token": self.token}
        )
        self.assertEqual(accept_res.status_code, status.HTTP_400_BAD_REQUEST)

        # Revoke audit log
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org_a,
                action=AuditAction.INVITATION_REVOKED,
            ).exists()
        )

    def test_cross_tenant_resend_or_revoke_is_404(self):
        response = self.as_user(self.owner_b).post(
            resend_invitation_url(self.org_b, self.invitation.id)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.as_user(self.owner_b).post(
            revoke_invitation_url(self.org_b, self.invitation.id)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class InvitationAcceptanceRoleSemanticsTests(TenantWorld):
    def test_teacher_invitation_requires_lead_or_sub_role(self):
        token, digest = OrganizationInvitation.generate_token_and_digest()
        OrganizationInvitation.objects.create(
            organization=self.org_a,
            email="accept_teach@example.com",
            role=OrganizationRole.TEACHER,
            token_digest=digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

        student_account = StudentFactory(email="accept_teach@example.com")
        response = self.as_user(student_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not compatible with a teacher invitation", str(response.data))

        # With sub teacher, acceptance succeeds
        student_account.role = Role.SUB
        student_account.save()
        response = self.as_user(student_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = OrganizationMembership.objects.get(user=student_account, organization=self.org_a)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)

    def test_parent_invitation_requires_parent_role(self):
        token, digest = OrganizationInvitation.generate_token_and_digest()
        OrganizationInvitation.objects.create(
            organization=self.org_a,
            email="accept_parent@example.com",
            role=OrganizationRole.PARENT,
            token_digest=digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

        student_account = StudentFactory(email="accept_parent@example.com")
        response = self.as_user(student_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        parent_account = ParentFactory(email="accept_parent@example.com")
        student_account.email = "other@example.com"
        student_account.save()
        parent_account.email = "accept_parent@example.com"
        parent_account.save()

        response = self.as_user(parent_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = OrganizationMembership.objects.get(user=parent_account, organization=self.org_a)
        self.assertEqual(membership.role, OrganizationRole.PARENT)

    def test_student_invitation_requires_student_role_and_no_fake_enrollment(self):
        token, digest = OrganizationInvitation.generate_token_and_digest()
        OrganizationInvitation.objects.create(
            organization=self.org_a,
            email="accept_stud@example.com",
            role=OrganizationRole.STUDENT,
            token_digest=digest,
            expires_at=timezone.now() + datetime.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

        teacher_account = SubTeacherFactory(email="accept_stud@example.com")
        response = self.as_user(teacher_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        student_account = StudentFactory(email="accept_stud@example.com")
        teacher_account.email = "other_teacher@example.com"
        teacher_account.save()

        response = self.as_user(student_account).post(
            accept_invitation_url(self.org_a), {"token": token}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        membership = OrganizationMembership.objects.get(user=student_account, organization=self.org_a)
        self.assertEqual(membership.role, OrganizationRole.STUDENT)

        # No fake StudentEnrollment is created
        from organizations.models import StudentEnrollment
        self.assertFalse(
            StudentEnrollment.objects.filter(user=student_account, organization=self.org_a).exists()
        )
