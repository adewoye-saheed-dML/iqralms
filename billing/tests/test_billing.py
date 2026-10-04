"""Tests for billing and platform subscriptions (Phase 9a)."""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role, User
from billing.models import BillingWebhookEvent, PlatformSubscription, SubscriptionStatus
from organizations.models import MembershipStatus, Organization, OrganizationMembership, OrganizationRole


class PlatformSubscriptionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret_key = "test_paystack_secret"

        self.owner = User.objects.create_user(
            username="owner_user",
            email="owner@academy.com",
            role=Role.LEAD,
            timezone="Africa/Lagos",
        )
        self.non_owner = User.objects.create_user(
            username="teacher_user",
            email="teacher@academy.com",
            role=Role.SUB,
            timezone="Africa/Lagos",
        )
        self.org = Organization.objects.create(
            name="Al-Huda Academy",
            slug="al-huda",
            timezone="Africa/Lagos",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.non_owner,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.ACTIVE,
        )

    def _sign_payload(self, payload_dict: dict) -> tuple[bytes, str]:
        body = json.dumps(payload_dict).encode("utf-8")
        sig = hmac.new(self.secret_key.encode("utf-8"), body, hashlib.sha512).hexdigest()
        return body, sig

    @patch("payments.paystack.PaystackClient.create_customer")
    @patch("payments.paystack.PaystackClient.create_subscription")
    def test_owner_can_subscribe_organization(self, mock_sub, mock_cust):
        mock_cust.return_value = {"customer_code": "CUS_123"}
        mock_sub.return_value = {"subscription_code": "SUB_999", "email_token": "TOK_abc"}

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/billing/subscribe/",
            {"organization_id": self.org.pk, "plan_code": "PLN_gold"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(response.data["paystack_plan_code"], "PLN_gold")

        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.paystack_customer_code, "CUS_123")
        self.assertEqual(sub.paystack_subscription_code, "SUB_999")
        self.assertTrue(self.org.is_active)

    def test_non_owner_cannot_subscribe_organization(self):
        self.client.force_authenticate(user=self.non_owner)
        response = self.client.post(
            "/api/billing/subscribe/",
            {"organization_id": self.org.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_subscription_status_view(self):
        sub = PlatformSubscription.objects.create(
            organization=self.org,
            paystack_plan_code="PLN_default",
            paystack_customer_code="CUS_1",
            paystack_subscription_code="SUB_1",
            status=SubscriptionStatus.ACTIVE,
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(f"/api/billing/status/?organization_id={self.org.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "active")

    def test_webhook_invalid_signature_rejected(self):
        response = self.client.post(
            "/api/billing/webhook/paystack/",
            data=json.dumps({"event": "subscription.create"}),
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE="bad_signature",
        )
        self.assertEqual(response.status_code, 400)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_webhook_subscription_lifecycle_and_is_active_gating(self):
        sub = PlatformSubscription.objects.create(
            organization=self.org,
            paystack_plan_code="PLN_test",
            paystack_customer_code="CUS_lifecycle",
            paystack_subscription_code="SUB_lifecycle",
            status=SubscriptionStatus.ACTIVE,
        )

        # 1. invoice.create: informational, is_active stays True
        payload = {
            "id": 1001,
            "event": "invoice.create",
            "data": {"subscription_code": "SUB_lifecycle", "customer": {"customer_code": "CUS_lifecycle"}},
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        self.org.refresh_from_db()
        self.assertTrue(self.org.is_active)

        # 2. invoice.update: charge succeeded, current_period_end updated
        payload = {
            "id": 1002,
            "event": "invoice.update",
            "data": {
                "subscription_code": "SUB_lifecycle",
                "subscription": {"next_payment_date": "2026-11-01T00:00:00Z"},
            },
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.assertIsNotNone(sub.current_period_end)

        # 3. invoice.payment_failed: status -> past_due, but is_active MUST remain True (Criterion 1 & 2)
        payload = {
            "id": 1003,
            "event": "invoice.payment_failed",
            "data": {"subscription_code": "SUB_lifecycle"},
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.PAST_DUE)
        self.assertTrue(self.org.is_active)  # Still active during dunning grace period!

        # 4. subscription.not_renew: status -> not_renewing, is_active remains True
        payload = {
            "id": 1004,
            "event": "subscription.not_renew",
            "data": {"subscription_code": "SUB_lifecycle"},
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.NOT_RENEWING)
        self.assertTrue(self.org.is_active)

        # 5. subscription.disable: status -> disabled, and NOW is_active becomes False (Criterion 1)
        payload = {
            "id": 1005,
            "event": "subscription.disable",
            "data": {"subscription_code": "SUB_lifecycle"},
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.org.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.DISABLED)
        self.assertFalse(self.org.is_active)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_webhook_replay_is_idempotent(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            paystack_plan_code="PLN_test",
            paystack_customer_code="CUS_dup",
            paystack_subscription_code="SUB_dup",
            status=SubscriptionStatus.ACTIVE,
        )
        payload = {
            "id": 99999,
            "event": "invoice.payment_failed",
            "data": {"subscription_code": "SUB_dup"},
        }
        body, sig = self._sign_payload(payload)

        # First delivery
        res1 = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.data["message"], "processed")

        # Second delivery (replay)
        res2 = self.client.post("/api/billing/webhook/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data["message"], "already_processed")
        self.assertEqual(BillingWebhookEvent.objects.filter(event_id="99999").count(), 1)

    def test_public_billing_tiers_endpoint(self):
        """GET /api/billing/tiers/ is public and returns 4 tiers with display USD."""
        self.client.logout()
        res = self.client.get("/api/billing/tiers/")
        self.assertEqual(res.status_code, 200)
        data = {item["tier"]: item for item in res.data}
        self.assertIn("starter", data)
        self.assertIn("growth", data)
        self.assertIn("professional", data)
        self.assertIn("institutional", data)

        starter = data["starter"]
        self.assertEqual(starter["student_cap"], 25)
        self.assertEqual(starter["price_ngn"], 15000)
        self.assertEqual(starter["price_usd_display"], 20)
        self.assertTrue(starter["self_serve"])

        inst = data["institutional"]
        self.assertIsNone(inst["student_cap"])
        self.assertIsNone(inst["price_ngn"])
        self.assertIsNone(inst["price_usd_display"])
        self.assertFalse(inst["self_serve"])

    @patch("payments.paystack.PaystackClient.create_customer")
    @patch("payments.paystack.PaystackClient.create_subscription")
    def test_owner_can_subscribe_to_growth_tier(self, mock_sub, mock_cust):
        mock_cust.return_value = {"customer_code": "CUS_growth"}
        mock_sub.return_value = {"subscription_code": "SUB_growth", "email_token": "TOK_growth"}

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["tier"], "growth")
        self.assertEqual(res.data["student_cap"], 100)

        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.tier, "growth")
        self.assertEqual(sub.student_cap, 100)

    def test_institutional_tier_cannot_be_subscribed_self_serve(self):
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/",
            {"organization_id": self.org.pk, "tier": "institutional"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

    def test_institutional_subscription_manual_provisioning(self):
        from billing.services import create_institutional_subscription
        sub = create_institutional_subscription(organization=self.org, user=self.owner)
        self.assertEqual(sub.tier, "institutional")
        self.assertIsNone(sub.student_cap)
        self.assertIsNone(sub.paystack_plan_code)
        self.assertEqual(sub.status, SubscriptionStatus.ACTIVE)
        self.assertTrue(self.org.is_active)

    def test_student_cap_enforcement_starter_tier(self):
        from billing.services import can_add_student
        from organizations.models import OrganizationInvitation
        from organizations.services import consume_invitation
        from django.core.exceptions import ValidationError

        sub = PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.ACTIVE,
        )
        self.assertEqual(sub.student_cap, 25)

        # Add 25 students
        for i in range(25):
            student_user = User.objects.create_user(
                username=f"student_{i}",
                email=f"student_{i}@test.com",
                role=Role.STUDENT,
                timezone="Africa/Lagos",
            )
            OrganizationMembership.objects.create(
                organization=self.org,
                user=student_user,
                role=OrganizationRole.STUDENT,
                status=MembershipStatus.ACTIVE,
            )

        # 26th student addition is blocked
        self.assertFalse(can_add_student(self.org))

        new_student = User.objects.create_user(
            username="student_26",
            email="student_26@test.com",
            role=Role.STUDENT,
            timezone="Africa/Lagos",
        )
        token = "test_student_26_token"
        invitation = OrganizationInvitation.objects.create(
            organization=self.org,
            email=new_student.email,
            role=OrganizationRole.STUDENT,
            token_digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )


        with self.assertRaises(ValidationError) as ctx:
            consume_invitation(invitation=invitation, user=new_student)
        self.assertIn("This academy has reached its student limit for the Starter plan.", str(ctx.exception))


    def test_subscription_status_capacity_and_warnings(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.ACTIVE,
        )
        # Add 23 students out of 25 (>= 90% and remaining <= 3)
        for i in range(23):
            u = User.objects.create_user(
                username=f"stud_warn_{i}",
                email=f"stud_warn_{i}@test.com",
                role=Role.STUDENT,
                timezone="Africa/Lagos",
            )
            OrganizationMembership.objects.create(
                organization=self.org,
                user=u,
                role=OrganizationRole.STUDENT,
                status=MembershipStatus.ACTIVE,
            )

        self.client.force_authenticate(user=self.owner)
        res = self.client.get(f"/api/billing/status/?organization_id={self.org.pk}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["student_cap"], 25)
        self.assertEqual(res.data["active_students"], 23)
        self.assertEqual(res.data["remaining_capacity"], 2)
        self.assertTrue(res.data["capacity_warning"])
        self.assertTrue(res.data["can_add_student"])

    def test_tenant_isolation_on_subscription_status(self):
        org_b = Organization.objects.create(
            name="Second Academy",
            slug="second-academy",
            timezone="Africa/Lagos",
            is_active=True,
        )
        owner_b = User.objects.create_user(
            username="owner_b",
            email="owner_b@test.com",
            role=Role.LEAD,
            timezone="Africa/Lagos",
        )
        OrganizationMembership.objects.create(
            organization=org_b,
            user=owner_b,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

        # Owner B tries to query Org A's subscription
        self.client.force_authenticate(user=owner_b)
        res = self.client.get(f"/api/billing/status/?organization_id={self.org.pk}")
        self.assertEqual(res.status_code, 403)

