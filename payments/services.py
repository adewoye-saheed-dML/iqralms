"""Tuition payment services and webhook processor (Phase 9b)."""

import logging
import uuid
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role, User
from pricing.models import PricingAgreement
from .models import FamilyPayment, FamilyPaymentStatus, PaymentWebhookEvent
from .paystack import PaystackClient

logger = logging.getLogger(__name__)


def setup_academy_subaccount(
    organization,
    bank_code: str,
    account_number: str,
    business_name: Optional[str] = None,
    client: Optional[PaystackClient] = None,
) -> Dict[str, Any]:
    """Resolve bank details and create a Paystack subaccount for an academy."""
    client = client or PaystackClient()
    business_name = business_name or organization.name

    resolved = client.resolve_account_number(
        account_number=account_number,
        bank_code=bank_code,
    )
    account_name = resolved.get("account_name", "")

    subaccount_data = client.create_subaccount(
        business_name=business_name,
        settlement_bank=bank_code,
        account_number=account_number,
        percentage_charge=0.0,  # 0% platform commission (Decision D-011)
        description=f"Tuition settlement subaccount for {business_name}",
    )
    subaccount_code = subaccount_data.get("subaccount_code")
    if not subaccount_code:
        raise ValidationError("Paystack did not return a subaccount code.")

    organization.paystack_subaccount_code = subaccount_code
    organization.save(update_fields=["paystack_subaccount_code", "updated_at"])

    return {
        "account_name": account_name,
        "subaccount_code": subaccount_code,
        "bank_code": bank_code,
        "account_number": account_number,
    }


def initialize_family_payment(
    payer: User,
    student: User,
    pricing_agreement: PricingAgreement,
    callback_url: Optional[str] = None,
    client: Optional[PaystackClient] = None,
) -> Tuple[FamilyPayment, Dict[str, Any]]:
    """Initialize a tuition payment for a student."""
    if payer.is_minor:
        raise PermissionDenied("Minor students cannot initiate payments.")

    if student.role != Role.STUDENT:
        raise ValidationError("Target must be a student.")

    if pricing_agreement.student_id != student.pk:
        raise ValidationError("Pricing agreement does not belong to this student.")

    organization = pricing_agreement.organization
    if not organization:
        raise ValidationError("Pricing agreement has no associated organization.")

    if not organization.paystack_subaccount_code:
        raise ValidationError("Academy has not configured bank details for tuition collection yet.")

    amount = pricing_agreement.agreed_rate
    if amount <= Decimal("0"):
        raise ValidationError("Pricing agreement has no payable rate.")

    client = client or PaystackClient()
    reference = f"iqra_{uuid.uuid4().hex[:16]}"
    amount_kobo = int(amount * 100)

    payment = FamilyPayment.objects.create(
        student=student,
        organization=organization,
        initiated_by=payer,
        pricing_agreement=pricing_agreement,
        amount=amount,
        currency="NGN",
        paystack_reference=reference,
        status=FamilyPaymentStatus.PENDING,
    )

    try:
        init_data = client.initialize_transaction(
            email=payer.email,
            amount_kobo=amount_kobo,
            reference=reference,
            subaccount=organization.paystack_subaccount_code,
            bearer="subaccount",  # Academy absorbs fees out of settlement (Decision D-009)
            callback_url=callback_url,
            metadata={
                "payment_id": payment.pk,
                "student_id": student.pk,
                "organization_id": organization.pk,
            },
        )
    except Exception as exc:
        payment.status = FamilyPaymentStatus.FAILED
        payment.save(update_fields=["status"])
        raise

    return payment, init_data


def verify_family_payment(reference: str, client: Optional[PaystackClient] = None) -> Dict[str, Any]:
    """Verify payment status on Paystack for immediate redirect feedback.

    Does not move the record to 'paid' itself — charge.success webhook alone does that.
    """
    client = client or PaystackClient()
    return client.verify_transaction(reference)


@transaction.atomic
def process_tuition_webhook(payload: Dict[str, Any]) -> Tuple[bool, str]:
    """Process Paystack tuition webhook event.

    Ensures at-most-once processing and adheres to Phase 9 rules:
    - Only charge.success moves FamilyPayment to paid.
    - If reference not matched, logs and exits without creating records.
    - Re-delivery is idempotent.
    """
    event_type = payload.get("event")
    data = payload.get("data", {})

    event_id = str(payload.get("id") or data.get("id") or f"{event_type}_{data.get('reference')}")
    if PaymentWebhookEvent.objects.filter(event_id=event_id).exists():
        logger.info("Tuition webhook event %s already processed; ignoring.", event_id)
        return True, "already_processed"

    reference = data.get("reference")
    if not reference:
        logger.warning("Tuition webhook missing transaction reference.")
        return False, "missing_reference"

    payment = FamilyPayment.objects.filter(paystack_reference=reference).first()
    if not payment:
        logger.warning("No FamilyPayment found for reference '%s'. Exiting without creating record.", reference)
        PaymentWebhookEvent.objects.create(
            event_id=event_id,
            event_type=event_type,
            payload=payload,
        )
        return False, "payment_not_found"

    if event_type == "charge.success":
        if payment.status == FamilyPaymentStatus.PAID:
            logger.info("Payment %s already marked paid; idempotent return.", payment.pk)
        else:
            payment.status = FamilyPaymentStatus.PAID
            payment.paid_at = timezone.now()
            payment.raw_webhook_payload = payload
            payment.save()
            logger.info("Payment %s marked paid via charge.success webhook.", payment.pk)

    elif event_type == "charge.failed":
        if payment.status == FamilyPaymentStatus.PENDING:
            payment.status = FamilyPaymentStatus.FAILED
            payment.save(update_fields=["status"])
            logger.info("Payment %s marked failed via webhook.", payment.pk)

    PaymentWebhookEvent.objects.create(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
    )
    return True, "processed"
