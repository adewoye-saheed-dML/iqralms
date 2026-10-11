"""Unit and integration tests for tuition payments and subaccounts (Phase 9b)."""

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import ParentLink, Role, User
from accounts.tests.factories import ParentFactory, StudentFactory
from organizations.models import MembershipStatus, Organization, OrganizationMembership, OrganizationRole
from curriculum.tests.factories import LevelFactory
from payments.models import FamilyPayment, FamilyPaymentStatus, PaymentWebhookEvent
from payments.tests.factories import FamilyPaymentFactory
from pricing.tests.factories import PricingAgreementFactory


class SubaccountSetupTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username="org_owner",
            email="owner@sub.com",
            role=Role.LEAD,
            timezone="Africa/Lagos",
        )
        self.student = StudentFactory()
        self.org = Organization.objects.create(
            name="Noor Academy",
            slug="noor-academy",
            timezone="Africa/Lagos",
            is_active=True,
        )
        OrganizationMembership.objects.create(
            organization=self.org,
            user=self.owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

    @patch("payments.paystack.PaystackClient.resolve_account_number")
    def test_subaccount_resolve_does_not_create_subaccount(self, mock_resolve):
        mock_resolve.return_value = {"account_name": "Noor Academy Ltd"}

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/payments/subaccount/resolve/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["account_name"], "Noor Academy Ltd")
        self.assertEqual(response.data["bank_code"], "058")
        self.assertEqual(response.data["account_number"], "0123456789")

        self.org.refresh_from_db()
        self.assertFalse(bool(self.org.paystack_subaccount_code))

    @patch("payments.paystack.PaystackClient.resolve_account_number")
    @patch("payments.paystack.PaystackClient.create_subaccount")
    def test_subaccount_confirm_creates_and_stores_subaccount(self, mock_sub, mock_resolve):
        mock_resolve.return_value = {"account_name": "Noor Academy Ltd"}
        mock_sub.return_value = {"subaccount_code": "SUB_noor_confirmed"}

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/payments/subaccount/confirm/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
                "business_name": "Noor Academy",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["subaccount_code"], "SUB_noor_confirmed")
        self.assertEqual(response.data["account_name"], "Noor Academy Ltd")

        # Assert 0% platform commission passed to Paystack
        _, kwargs = mock_sub.call_args
        self.assertEqual(kwargs["percentage_charge"], 0.0)

        self.org.refresh_from_db()
        self.assertEqual(self.org.paystack_subaccount_code, "SUB_noor_confirmed")

    @patch("payments.paystack.PaystackClient.resolve_account_number")
    @patch("payments.paystack.PaystackClient.create_subaccount")
    def test_failed_bank_resolution_does_not_create_subaccount(self, mock_sub, mock_resolve):
        from rest_framework.exceptions import ValidationError as DRFValidationError
        mock_resolve.side_effect = DRFValidationError("Could not resolve account name.")

        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/payments/subaccount/confirm/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        mock_sub.assert_not_called()
        self.org.refresh_from_db()
        self.assertFalse(bool(self.org.paystack_subaccount_code))

    def test_non_owner_cannot_resolve_subaccount(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            "/api/payments/subaccount/resolve/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_non_owner_cannot_confirm_subaccount(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            "/api/payments/subaccount/confirm/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_legacy_subaccount_setup_endpoint_returns_404(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            "/api/payments/subaccount/setup/",
            {
                "organization_id": self.org.pk,
                "bank_code": "058",
                "account_number": "0123456789",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_subaccount_status_unconfigured(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            f"/api/payments/subaccount/status/?organization_id={self.org.pk}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["is_configured"])
        self.assertIsNone(response.data["subaccount_code"])

    @patch("payments.paystack.PaystackClient.fetch_subaccount")
    def test_subaccount_status_configured(self, mock_fetch):
        mock_fetch.return_value = {
            "subaccount_code": "SUB_test123",
            "account_name": "Noor Academy Settlement",
            "settlement_bank": "058",
            "account_number": "0123456789",
        }
        self.org.paystack_subaccount_code = "SUB_test123"
        self.org.save()

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(
            f"/api/payments/subaccount/status/?organization_id={self.org.pk}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["is_configured"])
        self.assertEqual(response.data["subaccount_code"], "SUB_test123")
        self.assertEqual(response.data["account_name"], "Noor Academy Settlement")
        self.assertEqual(response.data["bank_code"], "058")
        self.assertEqual(response.data["account_number"], "0123456789")

    def test_subaccount_status_forbidden_for_non_member(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(
            f"/api/payments/subaccount/status/?organization_id={self.org.pk}",
        )
        self.assertEqual(response.status_code, 403)


class PaymentInitializationAndVerificationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agreement = PricingAgreementFactory(agreed_rate=Decimal("15000.00"))
        self.student = self.agreement.student
        self.student.is_minor = False
        self.student.save()
        self.org = self.agreement.organization
        self.org.paystack_subaccount_code = "SUB_test_acct"
        self.org.save()

        self.parent = ParentFactory()
        ParentLink.objects.create(parent=self.parent, student=self.student)
        self.parent_membership = OrganizationMembership.objects.create(
            organization=self.org,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )

        self.minor_student = StudentFactory(is_minor=True)
        self.minor_agreement = PricingAgreementFactory(student=self.minor_student, agreed_rate=Decimal("12000.00"))
        self.minor_agreement.organization.paystack_subaccount_code = "SUB_minor_org"
        self.minor_agreement.organization.save()

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_adult_student_can_initialize_tuition_payment(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/auth123",
            "access_code": "acc_123",
            "reference": "ref_123",
        }
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            "/api/payments/initialize/",
            {"pricing_agreement_id": self.agreement.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["amount"], "15000.00")
        self.assertEqual(response.data["currency"], "NGN")
        self.assertEqual(response.data["authorization_url"], "https://checkout.paystack.com/auth123")

        payment = FamilyPayment.objects.get(pk=response.data["payment_id"])
        self.assertEqual(payment.status, FamilyPaymentStatus.PENDING)
        self.assertEqual(payment.initiated_by, self.student)
        self.assertEqual(payment.organization, self.org)

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_adult_student_can_initialize_tuition_payment_in_usd(self, mock_init):
        self.org.currency = "USD"
        self.org.save()
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/auth_usd_123",
            "access_code": "acc_usd_123",
            "reference": "ref_usd_123",
        }
        self.client.force_authenticate(user=self.student)
        response = self.client.post(
            "/api/payments/initialize/",
            {"pricing_agreement_id": self.agreement.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["currency"], "USD")
        self.assertEqual(response.data["amount"], "15000.00")
        payment = FamilyPayment.objects.get(pk=response.data["payment_id"])
        self.assertEqual(payment.currency, "USD")
        # Ensure Paystack client received USD
        _, kwargs = mock_init.call_args
        self.assertEqual(kwargs["currency"], "USD")

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_linked_parent_can_initialize_tuition_payment_for_child(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/auth456",
            "access_code": "acc_456",
        }
        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "pricing_agreement_id": self.agreement.pk,
                "student_id": self.student.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payment = FamilyPayment.objects.get(pk=response.data["payment_id"])
        self.assertEqual(payment.initiated_by, self.parent)
        self.assertEqual(payment.student, self.student)

    def test_unlinked_parent_cannot_pay_for_student(self):
        other_parent = ParentFactory()
        self.client.force_authenticate(user=other_parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "pricing_agreement_id": self.agreement.pk,
                "student_id": self.student.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_minor_student_cannot_initialize_payment_anywhere(self):
        self.client.force_authenticate(user=self.minor_student)
        response = self.client.post(
            "/api/payments/initialize/",
            {"pricing_agreement_id": self.minor_agreement.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "Minor students cannot initiate payments.")

    @patch("payments.paystack.PaystackClient.verify_transaction")
    def test_verify_endpoint_does_not_mark_payment_as_paid(self, mock_verify):
        mock_verify.return_value = {"status": "success", "amount": 1500000}
        payment = FamilyPaymentFactory(
            pricing_agreement=self.agreement,
            student=self.student,
            organization=self.org,
            initiated_by=self.student,
            status=FamilyPaymentStatus.PENDING,
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(f"/api/payments/verify/{payment.paystack_reference}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], FamilyPaymentStatus.PENDING)

        payment.refresh_from_db()
        self.assertEqual(payment.status, FamilyPaymentStatus.PENDING)
        self.assertIsNone(payment.paid_at)

    def test_linked_parent_can_discover_child_active_pricing_agreement_scoped_to_org(self):
        self.client.force_authenticate(user=self.parent)
        # Test organization path
        response = self.client.get(f"/api/pricing/organizations/{self.org.pk}/children/{self.student.pk}/active/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.agreement.pk)
        self.assertEqual(Decimal(str(response.data["agreed_rate"])), self.agreement.agreed_rate)
        self.assertNotIn("notes", response.data)

        # Test query param path
        res_query = self.client.get(f"/api/pricing/children/{self.student.pk}/active/?organization_id={self.org.pk}")
        self.assertEqual(res_query.status_code, 200)
        self.assertEqual(res_query.data["id"], self.agreement.pk)

    def test_parent_discovery_requires_organization_id(self):
        self.client.force_authenticate(user=self.parent)
        response = self.client.get(f"/api/pricing/children/{self.student.pk}/active/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("organization_id", response.data)

    def test_parent_discovery_cross_tenant_isolation(self):
        # Student enrolled in Org A and Org B
        level_b = LevelFactory()
        org_b = level_b.track.organization
        org_b.name = "Academy B"
        org_b.slug = "academy-b"
        org_b.save()
        agreement_b = PricingAgreementFactory(level=level_b, student=self.student, active=True, agreed_rate=Decimal("50000.00"))

        self.client.force_authenticate(user=self.parent)
        # Org A returns agreement A (parent has active membership)
        res_a = self.client.get(f"/api/pricing/organizations/{self.org.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res_a.status_code, 200)
        self.assertEqual(res_a.data["id"], self.agreement.pk)

        # Org B returns 403 Forbidden because parent has no active membership in Org B
        res_b_unauthorized = self.client.get(f"/api/pricing/organizations/{org_b.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res_b_unauthorized.status_code, 403)

        # Legacy query param route also returns 403 Forbidden for Org B
        res_b_query_unauth = self.client.get(f"/api/pricing/children/{self.student.pk}/active/?organization_id={org_b.pk}")
        self.assertEqual(res_b_query_unauth.status_code, 403)

        # Add parent active membership in Org B -> now 200 OK
        mem_b = OrganizationMembership.objects.create(
            organization=org_b,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )
        res_b = self.client.get(f"/api/pricing/organizations/{org_b.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.data["id"], agreement_b.pk)

        # Suspend parent membership in Org B -> 403 Forbidden
        mem_b.status = MembershipStatus.SUSPENDED
        mem_b.save()
        res_b_suspended = self.client.get(f"/api/pricing/organizations/{org_b.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res_b_suspended.status_code, 403)

        # Org C (student not member) -> 403 Forbidden
        org_c = Organization.objects.create(name="Academy C", slug="academy-c", timezone="Africa/Lagos", is_active=True)
        OrganizationMembership.objects.create(
            organization=org_c,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )
        res_c = self.client.get(f"/api/pricing/organizations/{org_c.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res_c.status_code, 403)

    def test_parent_cannot_discover_child_agreement_when_academy_is_inactive(self):
        self.org.is_active = False
        self.org.save()
        self.client.force_authenticate(user=self.parent)
        res = self.client.get(f"/api/pricing/organizations/{self.org.pk}/children/{self.student.pk}/active/")
        self.assertEqual(res.status_code, 403)

    def test_unlinked_parent_cannot_discover_child_pricing_agreement(self):
        other_parent = ParentFactory()
        OrganizationMembership.objects.create(
            organization=self.org,
            user=other_parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )
        self.client.force_authenticate(user=other_parent)
        response = self.client.get(f"/api/pricing/organizations/{self.org.pk}/children/{self.student.pk}/active/")
        self.assertEqual(response.status_code, 403)

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_parent_can_initialize_tuition_with_organization_id(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/auto123",
            "access_code": "acc_auto",
        }
        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {"student_id": self.student.pk, "organization_id": self.org.pk},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        payment = FamilyPayment.objects.get(pk=response.data["payment_id"])
        self.assertEqual(payment.pricing_agreement, self.agreement)
        self.assertEqual(payment.organization, self.org)

    def test_parent_cannot_initialize_tuition_without_active_academy_membership(self):
        level_b = LevelFactory()
        org_b = level_b.track.organization
        org_b.name = "Academy B Without Membership"
        org_b.slug = "academy-b-no-mem"
        org_b.save()
        agreement_b = PricingAgreementFactory(level=level_b, student=self.student, active=True)

        self.client.force_authenticate(user=self.parent)
        # Parent has NO membership in Org B
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "student_id": self.student.pk,
                "organization_id": org_b.pk,
                "pricing_agreement_id": agreement_b.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("You do not have active access to this academy", response.data.get("detail", ""))

    def test_parent_cannot_initialize_tuition_when_membership_is_suspended(self):
        self.parent_membership.status = MembershipStatus.SUSPENDED
        self.parent_membership.save()

        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "student_id": self.student.pk,
                "organization_id": self.org.pk,
                "pricing_agreement_id": self.agreement.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("You do not have active access to this academy", response.data.get("detail", ""))

    def test_parent_cannot_initialize_tuition_when_organization_is_inactive(self):
        self.org.is_active = False
        self.org.save()

        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "student_id": self.student.pk,
                "organization_id": self.org.pk,
                "pricing_agreement_id": self.agreement.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @patch("payments.paystack.PaystackClient.initialize_transaction")
    def test_parent_can_pay_for_minor_child_with_active_membership(self, mock_init):
        mock_init.return_value = {
            "authorization_url": "https://checkout.paystack.com/minor_auth",
            "access_code": "acc_minor",
        }
        minor_org = self.minor_agreement.organization
        ParentLink.objects.create(parent=self.parent, student=self.minor_student)
        OrganizationMembership.objects.create(
            organization=minor_org,
            user=self.parent,
            role=OrganizationRole.PARENT,
            status=MembershipStatus.ACTIVE,
        )

        self.client.force_authenticate(user=self.parent)
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "student_id": self.minor_student.pk,
                "organization_id": minor_org.pk,
                "pricing_agreement_id": self.minor_agreement.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_parent_cannot_initialize_tuition_with_mismatched_organization_agreement(self):
        level_b = LevelFactory()
        org_b = level_b.track.organization
        org_b.name = "Academy B Mismatched"
        org_b.slug = "academy-b-mismatched"
        org_b.save()
        agreement_b = PricingAgreementFactory(level=level_b, student=self.student, active=True)
        self.client.force_authenticate(user=self.parent)
        # Attempt to pay against Org A using Agreement B
        response = self.client.post(
            "/api/payments/initialize/",
            {
                "student_id": self.student.pk,
                "organization_id": self.org.pk,
                "pricing_agreement_id": agreement_b.pk,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("pricing_agreement_id", response.data)


class TuitionWebhookTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret_key = "test_paystack_secret"
        self.payment = FamilyPaymentFactory(status=FamilyPaymentStatus.PENDING)

    def _sign(self, payload_dict):
        body = json.dumps(payload_dict).encode("utf-8")
        sig = hmac.new(self.secret_key.encode("utf-8"), body, hashlib.sha512).hexdigest()
        return body, sig

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_charge_success_moves_payment_to_paid(self):
        payload = {
            "id": 501,
            "event": "charge.success",
            "data": {
                "reference": self.payment.paystack_reference,
                "amount": int(self.payment.amount * 100),
                "currency": "NGN",
            },
        }
        body, sig = self._sign(payload)
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, FamilyPaymentStatus.PAID)
        self.assertIsNotNone(self.payment.paid_at)
        self.assertEqual(self.payment.raw_webhook_payload, payload)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_unknown_reference_webhook_logs_and_creates_no_record(self):
        initial_count = FamilyPayment.objects.count()
        payload = {
            "id": 502,
            "event": "charge.success",
            "data": {"reference": "non_existent_ref_999"},
        }
        body, sig = self._sign(payload)
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(FamilyPayment.objects.count(), initial_count)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_charge_success_replay_is_idempotent(self):
        payload = {
            "id": 503,
            "event": "charge.success",
            "data": {"reference": self.payment.paystack_reference},
        }
        body, sig = self._sign(payload)

        res1 = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res1.status_code, 200)

        # Replay
        res2 = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.data["message"], "already_processed")

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_charge_failed_moves_pending_to_failed(self):
        payload = {
            "id": 504,
            "event": "charge.failed",
            "data": {"reference": self.payment.paystack_reference},
        }
        body, sig = self._sign(payload)
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, FamilyPaymentStatus.FAILED)

    @patch.object(settings, "PAYSTACK_SECRET_KEY", "test_paystack_secret")
    def test_unified_webhook_endpoint_accepts_tuition_charge(self):
        payload = {
            "id": 505,
            "event": "charge.success",
            "data": {
                "reference": self.payment.paystack_reference,
                "amount": int(self.payment.amount * 100),
                "currency": "NGN",
            },
        }
        body, sig = self._sign(payload)
        res = self.client.post("/api/webhooks/paystack/", data=body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
        self.assertEqual(res.status_code, 200)

        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, FamilyPaymentStatus.PAID)

    def test_paid_payment_is_immutable(self):
        self.payment.status = FamilyPaymentStatus.PAID
        self.payment.paid_at = timezone.now()
        self.payment.save()

        self.payment.amount = Decimal("99999.00")
        with self.assertRaises(ValidationError):
            self.payment.save()


class VisibilityAndScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.agreement = PricingAgreementFactory()
        self.student = self.agreement.student
        self.student.is_minor = False
        self.student.save()
        self.org = self.agreement.organization

        self.parent = ParentFactory()
        ParentLink.objects.create(parent=self.parent, student=self.student)

        self.minor_student = StudentFactory(is_minor=True)

        self.payment = FamilyPaymentFactory(
            pricing_agreement=self.agreement,
            student=self.student,
            organization=self.org,
            initiated_by=self.student,
            status=FamilyPaymentStatus.PAID,
            paid_at=timezone.now(),
        )

        # Other academy
        self.other_agreement = PricingAgreementFactory()
        self.other_payment = FamilyPaymentFactory(
            pricing_agreement=self.other_agreement,
            status=FamilyPaymentStatus.PAID,
            paid_at=timezone.now(),
        )

    def test_adult_student_mine_view(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get("/api/payments/mine/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], self.payment.pk)

    def test_minor_student_mine_view_forbidden(self):
        self.client.force_authenticate(user=self.minor_student)
        res = self.client.get("/api/payments/mine/")
        self.assertEqual(res.status_code, 403)

    def test_parent_children_view(self):
        self.client.force_authenticate(user=self.parent)
        res = self.client.get("/api/payments/children/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], self.payment.pk)

    def test_owner_admin_organization_payments_view_and_cross_tenant_isolation(self):
        owner = User.objects.create_user(username="academy_owner", email="lead@org.com", role=Role.LEAD, timezone="Africa/Lagos")
        OrganizationMembership.objects.create(
            organization=self.org,
            user=owner,
            role=OrganizationRole.OWNER,
            status=MembershipStatus.ACTIVE,
        )

        self.client.force_authenticate(user=owner)
        res = self.client.get(f"/api/payments/organization/?organization_id={self.org.pk}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]["id"], self.payment.pk)

        # Cross-tenant: Owner of Org A attempts to view Org B
        res_cross = self.client.get(f"/api/payments/organization/?organization_id={self.other_agreement.organization.pk}")
        self.assertEqual(res_cross.status_code, 403)
