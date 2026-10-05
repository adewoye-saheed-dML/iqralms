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

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_owner_can_request_subscription_initialization(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/sub_checkout_123",
            "access_code": "acc_sub_123",
        }
        self.org.is_active = False
        self.org.save()

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["authorization_url"], "https://checkout.paystack.com/sub_checkout_123")
        self.assertEqual(response.data["access_code"], "acc_sub_123")
        self.assertTrue(response.data["reference"].startswith("sub_"))

        # Invariants: subscription is pending, organization is NOT activated yet
        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(sub.tier, "growth")
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_subscription_create_webhook_activates_subscription_and_organization(self):
        sub = PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_initialization_reference="sub_pending_ref_123",
            status=SubscriptionStatus.PENDING,
        )
        self.org.is_active = False
        self.org.save()

        payload = {
            "id": "evt_sub_create_1",
            "event": "subscription.create",
            "data": {
                "reference": "sub_pending_ref_123",
                "subscription_code": "SUB_active_456",
                "email_token": "TOK_email_456",
                "customer": {"customer_code": "CUS_active_456"},
            },
        }
        body, sig = self._sign_payload(payload)
        res = self.client.post(
            "/api/webhooks/paystack/",
            data=body,
            content_type="application/json",
            HTTP_X_PAYSTACK_SIGNATURE=sig,
        )
        self.assertEqual(res.status_code, 200)

        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.ACTIVE)
        self.assertEqual(sub.paystack_subscription_code, "SUB_active_456")
        self.org.refresh_from_db()
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
            "/api/webhooks/paystack/",
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
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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
        res1 = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.data["message"], "processed")

        # Second delivery (replay)
        res2 = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
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

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_owner_can_initialize_growth_tier(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/growth_123",
            "access_code": "acc_growth",
        }

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["authorization_url"], "https://checkout.paystack.com/growth_123")
        self.assertEqual(res.data["access_code"], "acc_growth")

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

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_no_subscription_starter_checkout_allowed(self, mock_init):
        from billing.constants import get_tier_plan_code
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/starter_123",
            "access_code": "acc_starter",
        }
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "starter"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["authorization_url"], "https://checkout.paystack.com/starter_123")
        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs["plan_code"], get_tier_plan_code("starter"))
        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(sub.tier, "starter")
        self.assertEqual(sub.paystack_plan_code, get_tier_plan_code("starter"))

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_no_subscription_growth_checkout_allowed(self, mock_init):
        from billing.constants import get_tier_plan_code
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/growth_123",
            "access_code": "acc_growth",
        }
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs["plan_code"], get_tier_plan_code("growth"))
        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(sub.tier, "growth")
        self.assertEqual(sub.paystack_plan_code, get_tier_plan_code("growth"))

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_no_subscription_professional_checkout_allowed(self, mock_init):
        from billing.constants import get_tier_plan_code
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/pro_123",
            "access_code": "acc_pro",
        }
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "professional"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs["plan_code"], get_tier_plan_code("professional"))
        sub = PlatformSubscription.objects.get(organization=self.org)
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(sub.tier, "professional")
        self.assertEqual(sub.paystack_plan_code, get_tier_plan_code("professional"))

    def test_existing_pending_subscription_second_checkout_rejected(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.PENDING,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("already has a subscription", str(res.data))

    def test_existing_active_subscription_second_checkout_rejected(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.ACTIVE,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("already has a subscription", str(res.data))

    def test_existing_past_due_subscription_second_checkout_rejected(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.PAST_DUE,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("already has a subscription", str(res.data))

    def test_existing_not_renewing_subscription_second_checkout_rejected(self):
        PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.NOT_RENEWING,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "growth"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("already has a subscription", str(res.data))

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_existing_disabled_subscription_new_checkout_allowed(self, mock_init):
        from billing.constants import get_tier_plan_code
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/pro_reactivate",
            "access_code": "acc_pro_reactivate",
        }
        sub = PlatformSubscription.objects.create(
            organization=self.org,
            tier="starter",
            paystack_plan_code="PLN_starter",
            status=SubscriptionStatus.DISABLED,
        )
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "professional"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        sub.refresh_from_db()
        self.assertEqual(sub.status, SubscriptionStatus.PENDING)
        self.assertEqual(sub.tier, "professional")
        self.assertEqual(sub.paystack_plan_code, get_tier_plan_code("professional"))

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_client_supplied_plan_code_is_rejected(self, mock_init):
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {
                "organization_id": self.org.pk,
                "tier": "starter",
                "plan_code": "PLN_MALICIOUS_CHEAT_FREE",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("plan_code", res.data)
        # Ensure Paystack initialize_transaction was never called with malicious plan_code
        mock_init.assert_not_called()

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_organization_remains_inactive_during_initialization(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/inactive_init",
            "access_code": "acc_inactive",
        }
        self.org.is_active = False
        self.org.save()

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "starter"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        self.org.refresh_from_db()
        self.assertFalse(self.org.is_active)

    def test_institutional_tier_remains_rejected(self):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        from billing.services import initialize_platform_subscription

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/billing/subscribe/initialize/",
            {"organization_id": self.org.pk, "tier": "institutional"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)

        # Also verify service layer directly blocks institutional self-serve
        with self.assertRaises(DRFValidationError) as ctx:
            initialize_platform_subscription(
                organization=self.org,
                user=self.owner,
                tier="institutional",
            )
        self.assertIn("Institutional tier is sales-assisted", str(ctx.exception))

