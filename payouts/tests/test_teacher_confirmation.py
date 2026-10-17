"""Tests for teacher confirmation and dispute workflow on finalized payouts."""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status

from payouts.models import (
    PayoutStatus,
    TeacherConfirmationStatus,
    TeacherPayout,
)
from payouts.tests.test_api import PayoutWorld


class TeacherPayoutConfirmationTests(PayoutWorld):
    def setUp(self):
        super().setUp()
        self.payout = TeacherPayout.objects.create(
            teacher=self.teacher,
            booking=self.booking,
            minutes_paid=60,
            rate_used=Decimal("5000.00"),
            amount=Decimal("5000.00"),
            currency="USD",
        )

    def _confirm_url(self, payout_id):
        return reverse(
            "payouts:payout-confirm",
            kwargs={"organization_pk": self.organization.pk, "pk": payout_id},
        )

    def test_teacher_cannot_confirm_generated_payout(self):
        """A payout in draft/generated status cannot be confirmed; it must be finalized first."""
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(
            self._confirm_url(self.payout.pk),
            {"confirmed": True},
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.payout.refresh_from_db()
        self.assertEqual(
            self.payout.confirmation_status, TeacherConfirmationStatus.PENDING
        )

    def test_teacher_confirms_finalized_payout_yes(self):
        """Teacher confirms receipt (Yes) after owner has finalized/cleared the payout."""
        self.payout.finalize()
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(
            self._confirm_url(self.payout.pk),
            {"confirmed": True, "reason": "Received in my bank account"},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["confirmation_status"], "confirmed")

        self.payout.refresh_from_db()
        self.assertEqual(
            self.payout.confirmation_status, TeacherConfirmationStatus.CONFIRMED
        )
        self.assertEqual(
            self.payout.confirmation_notes, "Received in my bank account"
        )
        self.assertIsNotNone(self.payout.confirmed_at)
        # Financial facts remain intact
        self.assertEqual(self.payout.amount, Decimal("5000.00"))
        self.assertEqual(self.payout.status, PayoutStatus.FINALIZED)

    def test_teacher_disputes_finalized_payout_no_with_reason(self):
        """Teacher reports payment not received (No) with mandatory reason."""
        self.payout.finalize()
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(
            self._confirm_url(self.payout.pk),
            {"confirmed": False, "reason": "Bank account was not credited."},
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["confirmation_status"], "disputed")
        self.assertEqual(
            res.data["confirmation_notes"], "Bank account was not credited."
        )

        self.payout.refresh_from_db()
        self.assertEqual(
            self.payout.confirmation_status, TeacherConfirmationStatus.DISPUTED
        )
        self.assertEqual(
            self.payout.confirmation_notes, "Bank account was not credited."
        )
        self.assertIsNotNone(self.payout.confirmed_at)

    def test_teacher_disputes_without_reason_fails_validation(self):
        """When confirmed=False, a reason is required."""
        self.payout.finalize()
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(
            self._confirm_url(self.payout.pk),
            {"confirmed": False, "reason": ""},
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("reason", res.data)

    def test_other_teacher_cannot_confirm_colleague_payout(self):
        """A teacher from the same academy cannot confirm another teacher's payout."""
        self.payout.finalize()
        self.client.force_authenticate(user=self.other_teacher)
        res = self.client.post(
            self._confirm_url(self.payout.pk),
            {"confirmed": True},
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_lead_view_reflects_confirmation_status_and_notes(self):
        """The lead list endpoint and my payouts endpoint expose confirmation details."""
        self.payout.finalize()
        self.payout.confirm_receipt(
            confirmed=False, notes="Payment missing 500 NGN fee"
        )

        # 1. Teacher views own payout
        self.client.force_authenticate(user=self.teacher)
        my_res = self.client.get(
            reverse(
                "payouts:payout-mine",
                kwargs={"organization_pk": self.organization.pk},
            )
        )
        self.assertEqual(my_res.status_code, status.HTTP_200_OK)
        item = [p for p in my_res.data if p["id"] == self.payout.pk][0]
        self.assertEqual(item["confirmation_status"], "disputed")
        self.assertEqual(
            item["confirmation_notes"], "Payment missing 500 NGN fee"
        )

        # 2. Lead views academy payouts
        self.client.force_authenticate(user=self.lead)
        lead_res = self.client.get(
            reverse(
                "payouts:payout-lead",
                kwargs={"organization_pk": self.organization.pk},
            )
        )
        self.assertEqual(lead_res.status_code, status.HTTP_200_OK)
        lead_item = [p for p in lead_res.data if p["id"] == self.payout.pk][0]
        self.assertEqual(lead_item["confirmation_status"], "disputed")
        self.assertEqual(
            lead_item["confirmation_notes"], "Payment missing 500 NGN fee"
        )
