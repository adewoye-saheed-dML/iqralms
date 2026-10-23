from rest_framework import serializers

from .constants import SUBSCRIPTION_TIERS
from .models import PlatformSubscription, SubscriptionTier


class TierDisplaySerializer(serializers.Serializer):
    """Public display representation of a platform subscription tier."""

    tier = serializers.CharField()
    student_cap = serializers.IntegerField(allow_null=True)
    price_ngn = serializers.IntegerField(allow_null=True)
    price_usd_display = serializers.IntegerField(allow_null=True)
    self_serve = serializers.BooleanField()


class PlatformSubscriptionSerializer(serializers.ModelSerializer):
    """Current subscription status and capacity usage for an academy."""

    organization_id = serializers.IntegerField(source="organization.id", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)
    student_cap = serializers.SerializerMethodField()
    active_students = serializers.SerializerMethodField()
    remaining_capacity = serializers.SerializerMethodField()
    capacity_warning = serializers.SerializerMethodField()
    can_add_student = serializers.SerializerMethodField()

    class Meta:
        model = PlatformSubscription
        fields = [
            "id",
            "organization_id",
            "organization_name",
            "organization_slug",
            "tier",
            "paystack_plan_code",
            "status",
            "current_period_end",
            "student_cap",
            "active_students",
            "remaining_capacity",
            "capacity_warning",
            "can_add_student",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_student_cap(self, obj) -> int | None:
        return obj.student_cap

    def get_active_students(self, obj) -> int:
        from .services import get_active_student_count
        return get_active_student_count(obj.organization)

    def get_remaining_capacity(self, obj) -> int | None:
        cap = obj.student_cap
        if cap is None:
            return None
        active = self.get_active_students(obj)
        return max(0, cap - active)

    def get_capacity_warning(self, obj) -> bool:
        cap = obj.student_cap
        if cap is None:
            return False
        active = self.get_active_students(obj)
        # Warning triggers when >= 90% full or <= 3 spots remaining (Decision D-014)
        return active >= (cap * 0.9) or (cap - active) <= 3

    def get_can_add_student(self, obj) -> bool:
        from .services import can_add_student
        return can_add_student(obj.organization)


class SubscribeRequestSerializer(serializers.Serializer):
    """Request payload to subscribe an organization."""

    organization_id = serializers.IntegerField(required=True)
    tier = serializers.ChoiceField(
        choices=[
            SubscriptionTier.STARTER,
            SubscriptionTier.GROWTH,
            SubscriptionTier.PROFESSIONAL,
        ],
        required=False,
        default=SubscriptionTier.STARTER,
    )
    currency = serializers.ChoiceField(
        choices=["NGN", "USD"],
        required=False,
        default=None,
    )
    callback_url = serializers.CharField(required=False, allow_blank=True, default=None)
    tz = serializers.CharField(required=False, allow_blank=True, default=None)

    def to_internal_value(self, data):
        if "plan_code" in data:
            raise serializers.ValidationError(
                {"plan_code": "Client-supplied plan_code is not supported. Plan code is determined solely by the requested tier."}
            )
        return super().to_internal_value(data)


class SubscriptionInitializeResponseSerializer(serializers.Serializer):
    """Response payload returned upon initializing platform subscription checkout."""

    authorization_url = serializers.CharField()
    access_code = serializers.CharField()
    reference = serializers.CharField()


class BillingLocationSerializer(serializers.Serializer):
    """Response payload for detected client country and default currency."""

    country = serializers.CharField()
    is_nigeria = serializers.BooleanField()
    currency = serializers.CharField()

