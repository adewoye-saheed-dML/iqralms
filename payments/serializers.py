"""Serializers for tuition payments and subaccount onboarding (Phase 9b)."""

from rest_framework import serializers

from .models import FamilyPayment


class SubaccountSetupSerializer(serializers.Serializer):
    """Payload to configure an academy's settlement bank details."""

    organization_id = serializers.IntegerField(required=True)
    bank_code = serializers.CharField(required=True, max_length=10)
    account_number = serializers.CharField(required=True, max_length=20)
    business_name = serializers.CharField(required=False, allow_blank=True, default="")


class SubaccountSetupResponseSerializer(serializers.Serializer):
    """Response after resolving and creating subaccount."""

    account_name = serializers.CharField()
    subaccount_code = serializers.CharField()
    bank_code = serializers.CharField()
    account_number = serializers.CharField()


class PaymentInitializeSerializer(serializers.Serializer):
    """Payload to initiate a tuition payment."""

    pricing_agreement_id = serializers.IntegerField(required=True)
    student_id = serializers.IntegerField(required=False, default=None)
    callback_url = serializers.URLField(required=False, allow_blank=True, default=None)


class PaymentInitializeResponseSerializer(serializers.Serializer):
    """Response returned upon successful payment initialization."""

    payment_id = serializers.IntegerField()
    reference = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.CharField()
    authorization_url = serializers.URLField(required=False)
    access_code = serializers.CharField(required=False)


class FamilyPaymentSerializer(serializers.ModelSerializer):
    """Detailed view of a family tuition payment."""

    student_name = serializers.CharField(source="student.get_full_name", read_only=True)
    student_username = serializers.CharField(source="student.username", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    initiated_by_username = serializers.CharField(source="initiated_by.username", read_only=True)

    class Meta:
        model = FamilyPayment
        fields = [
            "id",
            "student",
            "student_username",
            "student_name",
            "organization",
            "organization_name",
            "initiated_by",
            "initiated_by_username",
            "pricing_agreement",
            "amount",
            "currency",
            "paystack_reference",
            "status",
            "created_at",
            "paid_at",
        ]
        read_only_fields = fields


class PaystackWebhookPayloadSerializer(serializers.Serializer):
    """Generic schema for Paystack webhook events."""

    event = serializers.CharField()
    data = serializers.DictField()

