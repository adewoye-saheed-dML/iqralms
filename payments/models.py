"""Tuition payment models (Phase 9b).

Direct tuition payment from families/parents to academy subaccounts.
IqraLMS takes 0% cut and never holds custody of this money.
"""

from decimal import Decimal

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from accounts.models import Role, User
from pricing.models import PricingAgreement


class FamilyPaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PAID = "paid", "Paid"
    FAILED = "failed", "Failed"
    ABANDONED = "abandoned", "Abandoned"


PAID_IMMUTABLE_FIELDS = (
    "student_id",
    "organization_id",
    "initiated_by_id",
    "pricing_agreement_id",
    "amount",
    "currency",
    "paystack_reference",
)


class FamilyPaymentQuerySet(models.QuerySet):
    def in_organization(self, organization):
        if organization is None:
            return self.none()
        org_id = getattr(organization, "pk", organization)
        return self.filter(organization_id=org_id)


class FamilyPayment(models.Model):
    """Tuition payment initiated by an adult student or parent for an academy."""

    objects = FamilyPaymentQuerySet.as_manager()

    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="family_tuition_payments",
        help_text="Student whose tuition is being paid. Must have role 'student'.",
    )
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="family_tuition_payments",
        help_text="The academy receiving this tuition directly via Paystack subaccount.",
    )
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="initiated_tuition_payments",
        help_text="User who initiated payment (adult student or parent; never a minor).",
    )
    pricing_agreement = models.ForeignKey(
        PricingAgreement,
        on_delete=models.PROTECT,
        related_name="payments",
        help_text="The PricingAgreement snapshot this payment satisfies.",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Snapshotted amount in NGN at initialization. Never recomputed.",
    )
    currency = models.CharField(
        max_length=3,
        default="NGN",
        help_text="Tuition currency, matching payouts (NGN).",
    )
    paystack_reference = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Server-generated unique reference sent to Paystack.",
    )
    status = models.CharField(
        max_length=20,
        choices=FamilyPaymentStatus.choices,
        default=FamilyPaymentStatus.PENDING,
        help_text="pending -> paid | failed | abandoned.",
    )
    raw_webhook_payload = models.JSONField(
        null=True,
        blank=True,
        help_text="Raw payload from Paystack charge.success webhook.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when charge.success finalized this payment.",
    )

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_paid(self) -> bool:
        return self.status == FamilyPaymentStatus.PAID

    def clean(self):
        errors = {}

        if not self._state.adding:
            stored = type(self).objects.filter(pk=self.pk).first()
            if stored is not None and stored.status == FamilyPaymentStatus.PAID:
                for field in PAID_IMMUTABLE_FIELDS:
                    if getattr(self, field) != getattr(stored, field):
                        errors[field.removesuffix("_id")] = ValidationError(
                            "A paid tuition record is immutable; %(field)s cannot be altered.",
                            code="paid_payment_immutable",
                            params={"field": field.removesuffix("_id")},
                        )

        if self.student_id and self.student.role != Role.STUDENT:
            errors["student"] = ValidationError(
                "A tuition payment must name a student account.",
                code="invalid_student_role",
            )

        if self.initiated_by_id and self.initiated_by.is_minor:
            errors["initiated_by"] = ValidationError(
                "A minor student cannot initiate payments.",
                code="minor_cannot_initiate_payment",
            )

        if self.is_paid and self.paid_at is None:
            errors["paid_at"] = ValidationError(
                "paid_at must be recorded when status is 'paid'.",
                code="missing_paid_at",
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.username} -> {self.organization.name}: {self.amount} {self.currency} ({self.status})"


class PaymentWebhookEvent(models.Model):
    """Processed Paystack webhook events for tuition payments.

    Guarantees at-most-once processing.
    """

    event_id = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Unique event ID / reference from Paystack.",
    )
    event_type = models.CharField(
        max_length=100,
        help_text="Event type, e.g. charge.success.",
    )
    payload = models.JSONField(
        help_text="Full payload received from Paystack.",
    )
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]

    def __str__(self):
        return f"{self.event_type} ({self.event_id}) at {self.processed_at:%Y-%m-%d %H:%M}"
