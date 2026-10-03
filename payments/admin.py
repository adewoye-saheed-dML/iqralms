"""Admin configuration for payments app."""

from django.contrib import admin

from .models import FamilyPayment, FamilyPaymentStatus, PaymentWebhookEvent


@admin.register(FamilyPayment)
class FamilyPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "organization",
        "amount",
        "currency",
        "status",
        "paystack_reference",
        "created_at",
        "paid_at",
    )
    list_filter = ("status", "currency", "organization")
    search_fields = (
        "student__username",
        "student__email",
        "paystack_reference",
        "organization__name",
    )
    readonly_fields = (
        "student",
        "organization",
        "initiated_by",
        "pricing_agreement",
        "amount",
        "currency",
        "paystack_reference",
        "created_at",
    )

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == FamilyPaymentStatus.PAID:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event_type", "processed_at")
    list_filter = ("event_type",)
    search_fields = ("event_id", "event_type")
    readonly_fields = ("event_id", "event_type", "payload", "processed_at")
