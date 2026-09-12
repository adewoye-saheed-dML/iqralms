"""Django admin registration for notifications and deliveries."""

from django.contrib import admin

from .models import Notification, NotificationDelivery


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "organization",
        "event_type",
        "recipient",
        "title",
        "read_at",
        "created_at",
    )
    list_filter = ("organization", "event_type", "created_at")
    search_fields = (
        "recipient__username",
        "recipient__email",
        "title",
        "summary",
        "idempotency_key",
    )
    readonly_fields = ("created_at",)


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "notification",
        "channel",
        "provider",
        "status",
        "attempt_count",
        "created_at",
    )
    list_filter = ("channel", "status", "provider")
    search_fields = ("provider_message_id", "error_code", "error_message")
    readonly_fields = ("created_at",)
