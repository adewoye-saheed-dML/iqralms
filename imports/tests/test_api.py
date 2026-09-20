import io
import csv
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User, ParentLink
from accounts.tests.factories import UserFactory
from organizations.models import OrganizationRole, MembershipStatus, OrganizationMembership
from organizations.tests.factories import academy, OwnerMembershipFactory
from imports.models import ImportJob, ImportKind, ImportStatus

class ImportAPITests(APITestCase):
    def setUp(self):
        self.owner = UserFactory(role=Role.LEAD)
        self.org = academy(owner=self.owner)
        self.client.force_authenticate(user=self.owner)
        
    def _create_csv(self, rows):
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()

    def test_validate_and_commit_teachers(self):
        csv_data = self._create_csv([
            {"email": "teacher1@example.com", "first_name": "T1", "last_name": "L1", "timezone": "Africa/Lagos"},
            {"email": "teacher2@example.com", "first_name": "T2", "last_name": "L2", "timezone": "UTC"},
        ])
        
        file_obj = io.BytesIO(csv_data.encode('utf-8'))
        file_obj.name = "teachers.csv"
        
        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        
        response = self.client.post(validate_url, {
            "file": file_obj,
            "kind": ImportKind.TEACHERS
        }, format="multipart")
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ImportStatus.VALIDATED)
        self.assertEqual(response.data["valid_row_count"], 2)
        
        job_id = response.data["id"]
        commit_url = reverse("imports:commit", kwargs={"organization_pk": self.org.pk, "pk": job_id})
        
        commit_response = self.client.post(commit_url)
        self.assertEqual(commit_response.status_code, status.HTTP_200_OK)
        
        self.assertEqual(commit_response.data["status"], ImportStatus.COMPLETED)
        self.assertEqual(commit_response.data["created_count"], 2)
        self.assertEqual(commit_response.data["invitations_created"], 2)
        self.assertEqual(commit_response.data["emails_sent"], 2)
        
        # Verify db: invitations are pending and no membership is created yet
        from organizations.models import OrganizationInvitation, InvitationStatus
        inv1 = OrganizationInvitation.objects.get(email="teacher1@example.com", organization=self.org)
        self.assertEqual(inv1.role, OrganizationRole.TEACHER)
        self.assertEqual(inv1.status, InvitationStatus.PENDING)
        self.assertEqual(inv1.email_delivery_status, "sent")
        self.assertFalse(OrganizationMembership.objects.filter(user__email="teacher1@example.com", organization=self.org).exists())

    def test_tenant_isolation_cannot_access_other_org(self):
        other_owner = UserFactory()
        other_org = academy(owner=other_owner)
        
        job = ImportJob.objects.create(
            organization=other_org,
            created_by=other_owner,
            kind=ImportKind.TEACHERS,
            status=ImportStatus.VALIDATED,
            file_name="other.csv",
            file_size=100,
            file_type="text/csv"
        )
        
        commit_url = reverse("imports:commit", kwargs={"organization_pk": self.org.pk, "pk": job.pk})
        response = self.client.post(commit_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_emails_are_invalid(self):
        csv_data = self._create_csv([
            {"email": "dup@example.com", "first_name": "T1", "last_name": "L1", "timezone": "UTC"},
            {"email": "dup@example.com", "first_name": "T2", "last_name": "L2", "timezone": "UTC"},
        ])
        
        file_obj = io.BytesIO(csv_data.encode('utf-8'))
        file_obj.name = "dup.csv"
        
        validate_url = reverse("imports:validate", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(validate_url, {
            "file": file_obj,
            "kind": ImportKind.TEACHERS
        }, format="multipart")
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        
        # The first row is valid, second is invalid (duplicate)
        self.assertEqual(response.data["valid_row_count"], 1)
        self.assertEqual(response.data["invalid_row_count"], 1)
        
        errors = response.data["error_report"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["code"], "duplicate_row")
