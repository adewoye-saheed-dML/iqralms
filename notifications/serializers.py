"""Serializers for notifications and delivery records."""

from rest_framework import serializers

from .models import Notification, NotificationDelivery


class NotificationDeliverySerializer(serializers.ModelSerializer):
    """Delivery record serializer — strictly provider secrets/credentials free."""

    channel_display = serializers.CharField(source="get_channel_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = NotificationDelivery
        fields = [
            "id",
            "notification_id",
            "channel",
            "channel_display",
            "provider",
            "status",
            "status_display",
            "attempt_count",
            "provider_message_id",
            "error_code",
            "error_message",
            "attempted_at",
            "delivered_at",
            "created_at",
        ]
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    """Academy notification serializer with masked deliveries and read status."""

    event_type_display = serializers.CharField(source="get_event_type_display", read_only=True)
    recipient_username = serializers.CharField(source="recipient.username", read_only=True)
    is_read = serializers.BooleanField(read_only=True)
    deliveries = NotificationDeliverySerializer(many=True, read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "organization_id",
            "event_type",
            "event_type_display",
            "recipient_id",
            "recipient_username",
            "title",
            "summary",
            "payload",
            "is_read",
            "read_at",
            "created_at",
            "deliveries",
        ]
        read_only_fields = fields
