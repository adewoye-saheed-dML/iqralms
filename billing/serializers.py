"""Serializers for billing and platform subscriptions."""

from rest_framework import serializers

from .models import PlatformSubscription


class PlatformSubscriptionSerializer(serializers.ModelSerializer):
    """Current subscription status for an academy."""

    organization_id = serializers.IntegerField(source="organization.id", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)

    class Meta:
        model = PlatformSubscription
        fields = [
            "id",
            "organization_id",
            "organization_name",
            "organization_slug",
            "paystack_plan_code",
            "status",
            "current_period_end",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class SubscribeRequestSerializer(serializers.Serializer):
    """Request payload to subscribe an organization."""

    organization_id = serializers.IntegerField(required=True)
    plan_code = serializers.CharField(required=False, allow_blank=True, default=None)
