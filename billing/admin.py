"""Admin registration for billing and subscriptions."""

from django.contrib import admin

from .models import BillingWebhookEvent, PlatformSubscription


@admin.register(PlatformSubscription)
class PlatformSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "organization",
        "status",
        "paystack_plan_code",
        "paystack_subscription_code",
        "current_period_end",
        "updated_at",
    )
    list_filter = ("status", "paystack_plan_code")
    search_fields = ("organization__name", "organization__slug", "paystack_customer_code", "paystack_subscription_code")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BillingWebhookEvent)
class BillingWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_id", "event_type", "processed_at")
    list_filter = ("event_type",)
    search_fields = ("event_id", "event_type")
    readonly_fields = ("event_id", "event_type", "payload", "processed_at")
