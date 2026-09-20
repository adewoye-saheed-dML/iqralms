import csv
import io
from unittest.mock import patch
import openpyxl

from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.tests.factories import StudentFactory, SubTeacherFactory, UserFactory
from audit_logs.models import AuditAction, AuditLog
from imports.models import ImportJob, ImportKind, ImportStatus
from imports.services import ImportValidator, commit_import
from organizations.models import (
    InvitationStatus,
    MembershipStatus,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationMembershipFactory, SuspendedMembershipFactory, academy


class TeacherBulkImportTests(APITestCase):
    def setUp(self):
        self.owner = UserFactory(role=Role.LEAD)
        self.org = academy(name="Test Academy", slug="test-academy", owner=self.owner)
        self.client.force_authenticate(user=self.owner)

    def _create_csv_file(self, rows, filename="teachers.csv"):
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        file_obj = io.BytesIO(out.getvalue().encode("utf-8"))
        file_obj.name = filename
        return file_obj

    def _create_xlsx_file(self, rows, filename="teachers.xlsx"):
        wb = openpyxl.Workbook()
        ws = wb.active
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h) for h in headers])
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)
        out.name = filename
        return out

    def test_valid_csv_creates_pending_invitations_and_sends_emails(self):
        mail.outbox.clear()
        file_obj = self._create_csv_file([
            {"email": "t1@example.com", "first_name": "Teacher", "last_name": "One", "timezone": "Africa/Lagos"},
            {"email": "t2@example.com", "first_name": "Teacher", "last_name": "Two", "timezone": "UTC"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ImportStatus.VALIDATED)
        self.assertEqual(response.data["valid_row_count"], 2)

        # Validation MUST create zero invitations or memberships
        self.assertEqual(OrganizationInvitation.objects.filter(organization=self.org).count(), 0)
        self.assertEqual(OrganizationMembership.objects.filter(organization=self.org).count(), 1)  # only owner

        # Commit
        commit_url = reverse("imports:commit", kwargs={"organization_pk": self.org.pk, "pk": response.data["id"]})
        commit_res = self.client.post(commit_url)
        self.assertEqual(commit_res.status_code, status.HTTP_200_OK)
        self.assertEqual(commit_res.data["status"], ImportStatus.COMPLETED)
        self.assertEqual(commit_res.data["invitations_created"], 2)
        self.assertEqual(commit_res.data["emails_sent"], 2)
        self.assertEqual(commit_res.data["emails_failed"], 0)

        # Invitations are created and pending
        invs = OrganizationInvitation.objects.filter(organization=self.org).order_by("email")
        self.assertEqual(invs.count(), 2)
        self.assertEqual(invs[0].email, "t1@example.com")
        self.assertEqual(invs[0].status, InvitationStatus.PENDING)
        self.assertEqual(invs[0].role, OrganizationRole.TEACHER)
        self.assertEqual(invs[0].email_delivery_status, "sent")

        # Two emails sent
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("Test Academy", mail.outbox[0].subject)
        self.assertIn("/accept-invitation?organization=", mail.outbox[0].body)

        # No memberships created yet!
        self.assertFalse(OrganizationMembership.objects.filter(organization=self.org, user__email="t1@example.com").exists())

    def test_valid_xlsx_creates_pending_invitations(self):
        mail.outbox.clear()
        file_obj = self._create_xlsx_file([
            {"email": "xlsx_teacher@example.com", "first_name": "X", "last_name": "T"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_row_count"], 1)

        commit_url = reverse("imports:commit", kwargs={"organization_pk": self.org.pk, "pk": response.data["id"]})
        commit_res = self.client.post(commit_url)
        self.assertEqual(commit_res.status_code, status.HTTP_200_OK)
        self.assertEqual(commit_res.data["invitations_created"], 1)
        self.assertTrue(OrganizationInvitation.objects.filter(organization=self.org, email="xlsx_teacher@example.com").exists())

    def test_validation_detects_duplicate_pending_invitation(self):
        token, digest = OrganizationInvitation.generate_token_and_digest()
        OrganizationInvitation.objects.create(
            organization=self.org,
            email="existing_pending@example.com",
            role=OrganizationRole.TEACHER,
            token_digest=digest,
            expires_at=timezone.now() + timezone.timedelta(days=7),
            status=InvitationStatus.PENDING,
        )

        file_obj = self._create_csv_file([
            {"email": "existing_pending@example.com"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_row_count"], 0)
        self.assertEqual(response.data["invalid_row_count"], 1)
        self.assertEqual(response.data["error_report"][0]["code"], "duplicate_pending_invitation")

    def test_validation_detects_existing_active_membership(self):
        teacher = SubTeacherFactory(email="already_member@example.com")
        OrganizationMembershipFactory(organization=self.org, user=teacher, role=OrganizationRole.TEACHER)

        file_obj = self._create_csv_file([
            {"email": "already_member@example.com"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_row_count"], 0)
        self.assertEqual(response.data["error_report"][0]["code"], "existing_member")

    def test_validation_detects_existing_suspended_membership(self):
        teacher = SubTeacherFactory(email="suspended_member@example.com")
        SuspendedMembershipFactory(organization=self.org, user=teacher, role=OrganizationRole.TEACHER)

        file_obj = self._create_csv_file([
            {"email": "suspended_member@example.com"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_row_count"], 0)
        self.assertEqual(response.data["error_report"][0]["code"], "suspended_membership")

    def test_validation_detects_conflicting_account_role(self):
        student = StudentFactory(email="conflicting_student@example.com")

        file_obj = self._create_csv_file([
            {"email": "conflicting_student@example.com"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["valid_row_count"], 0)
        self.assertEqual(response.data["error_report"][0]["code"], "role_conflict")

    def test_partial_email_failure_records_failed_count_without_deleting_invitations(self):
        file_obj = self._create_csv_file([
            {"email": "ok_teacher@example.com"},
            {"email": "fail_teacher@example.com"},
        ])

        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {"file": file_obj, "kind": ImportKind.TEACHERS}, format="multipart")
        job_id = response.data["id"]

        original_send_mail = mail.send_mail

        def mock_send_mail(subject, message, from_email, recipient_list, **kwargs):
            if "fail_teacher@example.com" in recipient_list:
                raise RuntimeError("SMTP gateway rejected fail_teacher")
            return original_send_mail(subject, message, from_email, recipient_list, **kwargs)

        with patch("django.core.mail.send_mail", side_effect=mock_send_mail):
            commit_url = reverse("imports:commit", kwargs={"organization_pk": self.org.pk, "pk": job_id})
            commit_res = self.client.post(commit_url)

        self.assertEqual(commit_res.status_code, status.HTTP_200_OK)
        self.assertEqual(commit_res.data["status"], ImportStatus.PARTIALLY_COMPLETED)
        self.assertEqual(commit_res.data["invitations_created"], 2)
        self.assertEqual(commit_res.data["emails_sent"], 1)
        self.assertEqual(commit_res.data["emails_failed"], 1)

        # Both invitations exist in DB!
        self.assertTrue(OrganizationInvitation.objects.filter(organization=self.org, email="ok_teacher@example.com").exists())
        self.assertTrue(OrganizationInvitation.objects.filter(organization=self.org, email="fail_teacher@example.com").exists())

        fail_inv = OrganizationInvitation.objects.get(organization=self.org, email="fail_teacher@example.com")
        self.assertEqual(fail_inv.email_delivery_status, "failed")
