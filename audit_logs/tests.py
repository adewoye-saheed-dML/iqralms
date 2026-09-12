from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from accounts.models import User, Role
from organizations.models import Organization, OrganizationMembership, OrganizationRole, MembershipStatus
from .models import AuditLog
from .services import record_event

class AuditLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(email="owner@test.com", username="owner")
        self.org = Organization.objects.create(name="Test Org", slug="test-org", timezone="UTC")
        self.membership = OrganizationMembership.objects.create(
            organization=self.org, user=self.user, role=OrganizationRole.OWNER, status=MembershipStatus.ACTIVE
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        
    def test_record_event_and_immutability(self):
        log = record_event(
            organization=self.org,
            actor=self.user,
            action="test.action",
            target=self.membership,
            metadata={"password": "secret_password", "safe_key": "safe_value"}
        )
        
        self.assertEqual(AuditLog.objects.count(), 1)
        # Check metadata sanitization
        self.assertEqual(log.metadata["password"], "[REDACTED]")
        self.assertEqual(log.metadata["safe_key"], "safe_value")
        
        # Check immutability
        with self.assertRaises(ValueError):
            log.action = "changed"
            log.save()
            
        with self.assertRaises(ValueError):
            log.delete()

    def test_api_list_and_detail(self):
        record_event(
            organization=self.org,
            actor=self.user,
            action="test.action",
            target=self.membership
        )
        
        url = reverse("audit_logs:audit-log-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        
        log_id = response.data[0]["id"]
        detail_url = reverse("audit_logs:audit-log-detail", kwargs={"organization_pk": self.org.pk, "pk": log_id})
        detail_response = self.client.get(detail_url)
        self.assertEqual(detail_response.status_code, status.HTTP_200_OK)
        
    def test_tenant_isolation(self):
        other_org = Organization.objects.create(name="Other Org", slug="other", timezone="UTC")
        record_event(
            organization=self.org,
            actor=self.user,
            action="test.action",
            target=self.membership
        )
        
        # Accessing other org's list
        url = reverse("audit_logs:audit-log-list", kwargs={"organization_pk": other_org.pk})
        response = self.client.get(url)
        # Should be forbidden because user is not a member of other_org
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        
        # Even if we create a membership in other_org as a student, it should be forbidden to view audit logs
        OrganizationMembership.objects.create(
            organization=other_org, user=self.user, role=OrganizationRole.TEACHER, status=MembershipStatus.ACTIVE
        )
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, status.HTTP_403_FORBIDDEN)
