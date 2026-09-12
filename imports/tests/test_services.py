from django.test import TestCase

from accounts.models import Role, User, ParentLink
from accounts.tests.factories import UserFactory
from organizations.models import OrganizationRole, MembershipStatus, OrganizationMembership
from organizations.tests.factories import academy
from imports.models import ImportJob, ImportKind, ImportStatus
from imports.services import ImportValidator, commit_import

class ImportServicesTests(TestCase):
    def setUp(self):
        self.owner = UserFactory()
        self.org = academy(owner=self.owner)
        self.job = ImportJob.objects.create(
            organization=self.org,
            created_by=self.owner,
            kind=ImportKind.STUDENTS,
            status=ImportStatus.UPLOADED,
            file_name="students.csv",
            file_size=100,
            file_type="text/csv",
            column_mapping={
                "email": "email",
                "first_name": "first_name",
                "last_name": "last_name",
                "timezone": "timezone",
                "parent_email": "parent_email"
            }
        )
        
    def test_idempotency(self):
        rows = [
            {"email": "student@example.com", "first_name": "S", "last_name": "S", "timezone": "UTC"}
        ]
        
        validator = ImportValidator(self.job, rows)
        validator.validate()
        
        self.assertEqual(self.job.valid_row_count, 1)
        commit_import(self.job)
        
        self.assertEqual(User.objects.filter(email="student@example.com").count(), 1)
        
        # Run again with same data
        job2 = ImportJob.objects.create(
            organization=self.org,
            created_by=self.owner,
            kind=ImportKind.STUDENTS,
            status=ImportStatus.UPLOADED,
            file_name="students.csv",
            file_size=100,
            file_type="text/csv",
            column_mapping={
                "email": "email",
                "first_name": "first_name",
                "last_name": "last_name",
                "timezone": "timezone",
                "parent_email": "parent_email"
            }
        )
        validator2 = ImportValidator(job2, rows)
        validator2.validate()
        
        self.assertEqual(job2.valid_row_count, 1)
        commit_import(job2)
        
        # User count should still be 1
        self.assertEqual(User.objects.filter(email="student@example.com").count(), 1)
        self.assertEqual(job2.updated_count, 1)
        self.assertEqual(job2.created_count, 0)
        
        # Memberships should still be 1
        u = User.objects.get(email="student@example.com")
        self.assertEqual(OrganizationMembership.objects.filter(user=u, organization=self.org).count(), 1)

    def test_parent_child_link(self):
        # Create a parent
        parent = UserFactory(email="parent@example.com", role=Role.PARENT)
        OrganizationMembership.objects.create(user=parent, organization=self.org, role=OrganizationRole.STAFF)
        
        rows = [
            {
                "email": "student_linked@example.com", 
                "first_name": "S", 
                "last_name": "S", 
                "timezone": "UTC",
                "parent_email": "parent@example.com"
            }
        ]
        
        validator = ImportValidator(self.job, rows)
        validator.validate()
        
        self.assertEqual(len(self.job.error_report), 0)
        self.assertEqual(self.job.valid_row_count, 1)
        
        commit_import(self.job)
        
        child = User.objects.get(email="student_linked@example.com")
        self.assertTrue(ParentLink.objects.filter(parent=parent, student=child).exists())
        
    def test_parent_child_link_fails_if_parent_missing(self):
        rows = [
            {
                "email": "student_unlinked@example.com", 
                "first_name": "S", 
                "last_name": "S", 
                "timezone": "UTC",
                "parent_email": "nonexistent@example.com"
            }
        ]
        
        validator = ImportValidator(self.job, rows)
        validator.validate()
        
        self.assertEqual(self.job.valid_row_count, 0)
        self.assertEqual(len(self.job.error_report), 1)
        self.assertEqual(self.job.error_report[0]["code"], "invalid_reference")
