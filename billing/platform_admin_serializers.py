"""Serializers for Platform Creator / Super Admin Command Center."""

from decimal import Decimal
from rest_framework import serializers
from django.utils import timezone as dj_timezone

from accounts.models import User
from organizations.models import Organization, OrganizationRole, MembershipStatus
from .models import (
    PlatformSubscription,
    SubscriptionStatus,
    SubscriptionTier,
    SubscriberOnboardingDispatch,
    PlatformAnnouncement,
)
from .constants import SUBSCRIPTION_TIERS


class GuideVideoSerializer(serializers.Serializer):
    """Guide video item for subscriber onboarding."""

    id = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    duration = serializers.CharField()
    url = serializers.CharField()
    category = serializers.CharField()
    order = serializers.IntegerField()


class SystemHealthSerializer(serializers.Serializer):
    """System health metrics."""

    database = serializers.CharField()
    paystack = serializers.CharField()
    notifications = serializers.CharField()
    video_service = serializers.CharField()
    environment = serializers.CharField()


class PlatformOverviewSerializer(serializers.Serializer):
    """Executive KPI metrics for the platform creator."""

    total_academies = serializers.IntegerField()
    active_subscribers = serializers.IntegerField()
    trial_subscribers = serializers.IntegerField()
    past_due_subscribers = serializers.IntegerField()
    disabled_subscribers = serializers.IntegerField()
    total_students = serializers.IntegerField()
    total_teachers = serializers.IntegerField()
    total_completed_classes = serializers.IntegerField()
    mrr_usd = serializers.FloatField()
    mrr_ngn = serializers.FloatField()
    platform_gross_volume_usd = serializers.FloatField()
    platform_gross_volume_ngn = serializers.FloatField()
    tiers_distribution = serializers.DictField()
    system_health = SystemHealthSerializer()


class SubscriberDetailSerializer(serializers.Serializer):
    """Detailed view of an academy subscriber for the creator."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()
    owner_id = serializers.IntegerField(allow_null=True)
    owner_username = serializers.CharField(allow_blank=True)
    owner_email = serializers.CharField(allow_blank=True)
    owner_name = serializers.CharField(allow_blank=True)
    tier = serializers.CharField()
    tier_display = serializers.CharField()
    status = serializers.CharField()
    status_display = serializers.CharField()
    currency = serializers.CharField()
    monthly_price_usd = serializers.FloatField(allow_null=True)
    monthly_price_ngn = serializers.FloatField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    active_students_count = serializers.IntegerField()
    student_cap = serializers.IntegerField(allow_null=True)
    teachers_count = serializers.IntegerField()
    paystack_customer_code = serializers.CharField(allow_blank=True)
    paystack_subscription_code = serializers.CharField(allow_blank=True)
    paystack_subaccount_code = serializers.CharField(allow_blank=True)
    is_active = serializers.BooleanField()
    onboarding_email_sent_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class SubscriberSubscriptionManageSerializer(serializers.Serializer):
    """Payload to update an academy's platform subscription."""

    action = serializers.ChoiceField(
        choices=["update_tier", "activate", "comp", "suspend", "extend_period"]
    )
    tier = serializers.ChoiceField(
        choices=SubscriptionTier.choices, required=False
    )
    status = serializers.ChoiceField(
        choices=SubscriptionStatus.choices, required=False
    )
    extension_days = serializers.IntegerField(required=False, min_value=1, max_value=365)


class PlatformActivitySerializer(serializers.Serializer):
    """Unified notable platform activity event."""

    id = serializers.IntegerField()
    organization_id = serializers.IntegerField(allow_null=True)
    organization_name = serializers.CharField()
    action = serializers.CharField()
    action_display = serializers.CharField()
    category = serializers.CharField()
    actor_username = serializers.CharField()
    actor_email = serializers.CharField()
    object_type = serializers.CharField(allow_blank=True)
    object_id = serializers.CharField(allow_blank=True)
    metadata = serializers.DictField()
    created_at = serializers.DateTimeField()


class OnboardingEmailSendSerializer(serializers.Serializer):
    """Payload to trigger an onboarding guide email to a subscriber."""

    organization_id = serializers.IntegerField(required=False)
    recipient_email = serializers.EmailField(required=False)
    subject = serializers.CharField(
        required=False,
        default="Welcome to Iqra LMS: Complete Academy Setup Guide & Video Walkthroughs",
    )
    custom_message = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    include_all_videos = serializers.BooleanField(required=False, default=True)
    selected_video_ids = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


class SubscriberOnboardingDispatchSerializer(serializers.ModelSerializer):
    """Model serializer for sent onboarding emails."""

    organization_name = serializers.CharField(source="organization.name", read_only=True)
    sent_by_username = serializers.CharField(source="sent_by.username", read_only=True)

    class Meta:
        model = SubscriberOnboardingDispatch
        fields = [
            "id",
            "organization",
            "organization_name",
            "recipient_email",
            "subject",
            "sent_by",
            "sent_by_username",
            "video_links",
            "custom_message",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class PlatformAnnouncementSerializer(serializers.ModelSerializer):
    """Model serializer for platform announcements."""

    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = PlatformAnnouncement
        fields = [
            "id",
            "title",
            "message",
            "is_active",
            "level",
            "created_by",
            "created_by_username",
            "created_at",
        ]
        read_only_fields = ["id", "created_by", "created_at"]
