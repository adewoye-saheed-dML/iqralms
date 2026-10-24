"""Views for Platform Creator / Super Admin Command Center."""

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Dict, List, Set

from django.conf import settings
from django.core.mail import send_mail
from django.db import connection
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone as dj_timezone
from django.utils.html import strip_tags
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import Role, User
from audit_logs.models import AuditAction, AuditLog
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    StudentEnrollment,
)
from scheduling.models import Booking
from .constants import SUBSCRIPTION_TIERS
from .models import (
    PlatformAnnouncement,
    PlatformSubscription,
    SubscriberOnboardingDispatch,
    SubscriptionStatus,
    SubscriptionTier,
)
from .platform_admin_serializers import (
    GuideVideoSerializer,
    OnboardingEmailSendSerializer,
    PlatformActivitySerializer,
    PlatformAnnouncementSerializer,
    PlatformOverviewSerializer,
    SubscriberDetailSerializer,
    SubscriberOnboardingDispatchSerializer,
    SubscriberSubscriptionManageSerializer,
    PlatformVideoConfigSerializer,
)

logger = logging.getLogger(__name__)


class IsPlatformAdmin(permissions.BasePermission):
    """Strict permission: only global superuser, staff, or platform admin role may access."""

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        # Allow accounts with admin role
        if getattr(user, "role", None) in {"admin", Role.OWNER, "owner"}:
            return getattr(user, "is_platform_admin", False) or user.is_superuser or user.is_staff
        return False


DEFAULT_GUIDE_VIDEOS: List[Dict[str, Any]] = [
    {
        "id": "video-1",
        "title": "Academy Setup & Custom Branding",
        "description": "Configure your Quran academy name, custom logo icon, timezone, and student tuition billing currency.",
        "duration": "4:15",
        "url": "https://www.youtube.com/watch?v=setup-academy",
        "category": "Setup",
        "order": 1,
    },
    {
        "id": "video-2",
        "title": "Curriculum Architecture, Tracks & Progressive Levels",
        "description": "Establish progressive tracks (Hifz, Tajweed, Noorani Qaidah), age gates, and audio placement audition workflows.",
        "duration": "5:30",
        "url": "https://www.youtube.com/watch?v=curriculum-levels",
        "category": "Curriculum",
        "order": 2,
    },
    {
        "id": "video-3",
        "title": "Faculty Recruitment, Compensation & Bulk Bank Payouts",
        "description": "Authorize instructors to teach specific tracks, define hourly/per-class compensation rates, generate bank payout CSVs, and manage teacher receipts.",
        "duration": "6:45",
        "url": "https://www.youtube.com/watch?v=faculty-payouts",
        "category": "Faculty",
        "order": 3,
    },
    {
        "id": "video-4",
        "title": "Student Availability & Side-by-Side Teacher Allocation",
        "description": "How students and parents declare learning availability, comparing candidate teachers side-by-side with match quality badges, and teacher schedule agreements.",
        "duration": "4:50",
        "url": "https://www.youtube.com/watch?v=schedule-matching",
        "category": "Scheduling",
        "order": 4,
    },
    {
        "id": "video-5",
        "title": "Virtual Classroom, Interactive Whiteboard & Session Recording",
        "description": "Using the built-in video room, interactive Quran page whiteboard, real-time audio recitation review, and attendance logs.",
        "duration": "5:10",
        "url": "https://www.youtube.com/watch?v=classroom-tools",
        "category": "Classroom",
        "order": 5,
    },
]


def _categorize_audit_action(action: str) -> str:
    """Categorize an audit action string into a UI section."""
    if action.startswith("auth."):
        return "Security"
    if action.startswith("organization.") or action.startswith("membership."):
        return "Academies"
    if action.startswith("curriculum.") or action.startswith("enrollment."):
        return "Curriculum"
    if action.startswith("booking.") or action.startswith("availability.") or action.startswith("cohort."):
        return "Scheduling"
    if action.startswith("pricing.") or action.startswith("payout."):
        return "Billing & Payouts"
    if action.startswith("notification."):
        return "Notifications"
    return "Operations"


@extend_schema(
    summary="Platform Creator / Super Admin Overview KPIs",
    responses={200: PlatformOverviewSerializer},
    tags=["platform-admin"],
)
class PlatformAdminOverviewView(APIView):
    """Returns high-level platform health, MRR, academies, students, and subscriptions count."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        total_academies = Organization.objects.count()
        subs = PlatformSubscription.objects.all()

        active_subs = subs.filter(status=SubscriptionStatus.ACTIVE)
        active_subscribers = active_subs.count()
        trial_subscribers = subs.filter(status=SubscriptionStatus.PENDING).count()
        past_due_subscribers = subs.filter(status=SubscriptionStatus.PAST_DUE).count()
        disabled_subscribers = subs.filter(status=SubscriptionStatus.DISABLED).count()

        # Count distinct active student placements across all tenant academies
        # (combines active student memberships and active student enrollments)
        active_member_students = set(
            OrganizationMembership.objects.filter(
                role=OrganizationRole.STUDENT,
                status=MembershipStatus.ACTIVE,
            ).values_list("organization_id", "user_id")
        )
        active_enrolled_students = set(
            StudentEnrollment.objects.filter(
                status="active",
            ).values_list("organization_id", "user_id")
        )
        total_students = len(active_member_students.union(active_enrolled_students))

        total_teachers = OrganizationMembership.objects.filter(
            role=OrganizationRole.TEACHER, status=MembershipStatus.ACTIVE
        ).values("user").distinct().count()

        total_completed_classes = Booking.objects.filter(status="completed").count()

        # Calculate MRR across active subscriptions
        mrr_usd = 0.0
        mrr_ngn = 0.0
        tiers_distribution: Dict[str, int] = {
            "starter": 0,
            "growth": 0,
            "professional": 0,
            "institutional": 0,
        }

        for sub in active_subs:
            tier_info = SUBSCRIPTION_TIERS.get(sub.tier, {})
            tiers_distribution[sub.tier] = tiers_distribution.get(sub.tier, 0) + 1
            org_currency = getattr(sub.organization, "currency", "USD") or "USD"

            if org_currency.upper() == "NGN":
                mrr_ngn += float(tier_info.get("price_ngn") or 0)
            else:
                mrr_usd += float(tier_info.get("price_usd") or 0)

        # Database health check
        db_healthy = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            db_healthy = False

        paystack_configured = bool(getattr(settings, "PAYSTACK_SECRET_KEY", ""))

        from .models import PlatformSystemConfig
        video_cfg = PlatformSystemConfig.get_current()

        system_health = {
            "database": "operational" if db_healthy else "degraded",
            "paystack": "connected" if paystack_configured else "not_configured",
            "notifications": "operational",
            "video_service": "operational",
            "video_provider": video_cfg.video_provider,
            "environment": "production" if getattr(settings, "PRODUCTION", False) else "development",
        }

        data = {
            "total_academies": total_academies,
            "active_subscribers": active_subscribers,
            "trial_subscribers": trial_subscribers,
            "past_due_subscribers": past_due_subscribers,
            "disabled_subscribers": disabled_subscribers,
            "total_students": total_students,
            "total_teachers": total_teachers,
            "total_completed_classes": total_completed_classes,
            "mrr_usd": round(mrr_usd, 2),
            "mrr_ngn": round(mrr_ngn, 2),
            "platform_gross_volume_usd": round(mrr_usd * 1.5, 2),
            "platform_gross_volume_ngn": round(mrr_ngn * 1.5, 2),
            "tiers_distribution": tiers_distribution,
            "system_health": system_health,
        }
        return Response(data, status=status.HTTP_200_OK)


@extend_schema(
    summary="List All Academy Subscribers",
    parameters=[
        OpenApiParameter(name="q", description="Search academy name, slug, or owner email", required=False, type=str),
        OpenApiParameter(name="status", description="Filter by subscription status", required=False, type=str),
        OpenApiParameter(name="tier", description="Filter by subscription tier", required=False, type=str),
    ],
    responses={200: SubscriberDetailSerializer(many=True)},
    tags=["platform-admin"],
)
class PlatformAdminSubscribersView(APIView):
    """Lists all tenant academies with owner, subscription tier, student counts, and status."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        qs = Organization.objects.all().prefetch_related(
            "memberships__user",
            "student_enrollments",
        ).select_related("platform_subscription").order_by("-created_at")

        q = request.query_params.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(slug__icontains=q)
                | Q(memberships__user__email__icontains=q, memberships__role=OrganizationRole.OWNER)
                | Q(memberships__user__username__icontains=q, memberships__role=OrganizationRole.OWNER)
            ).distinct()

        status_filter = request.query_params.get("status", "").strip()
        if status_filter:
            qs = qs.filter(platform_subscription__status=status_filter)

        tier_filter = request.query_params.get("tier", "").strip()
        if tier_filter:
            qs = qs.filter(platform_subscription__tier=tier_filter)

        subscribers_data = []
        org_list = list(qs)
        org_ids = [org.id for org in org_list]

        # Batch query active student memberships and enrollments across listed academies
        member_students = OrganizationMembership.objects.filter(
            organization_id__in=org_ids,
            role=OrganizationRole.STUDENT,
            status=MembershipStatus.ACTIVE,
        ).values_list("organization_id", "user_id")

        enrolled_students = StudentEnrollment.objects.filter(
            organization_id__in=org_ids,
            status="active",
        ).values_list("organization_id", "user_id")

        org_students_map: Dict[int, Set[int]] = defaultdict(set)
        for o_id, u_id in member_students:
            org_students_map[o_id].add(u_id)
        for o_id, u_id in enrolled_students:
            org_students_map[o_id].add(u_id)

        for org in org_list:
            owner = org.owner_membership.user if org.owner_membership else None
            sub = getattr(org, "platform_subscription", None)

            tier = sub.tier if sub else "starter"
            tier_info = SUBSCRIPTION_TIERS.get(tier, {})
            sub_status = sub.status if sub else "pending"

            active_students = len(org_students_map[org.id])
            teachers_count = org.memberships.filter(
                role=OrganizationRole.TEACHER, status=MembershipStatus.ACTIVE
            ).count()

            owner_name = ""
            if owner:
                owner_name = f"{owner.first_name} {owner.last_name}".strip() or owner.username

            currency = getattr(org, "currency", "USD") or "USD"

            subscribers_data.append({
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "owner_id": owner.id if owner else None,
                "owner_username": owner.username if owner else "",
                "owner_email": owner.email if owner else "",
                "owner_name": owner_name,
                "tier": tier,
                "tier_display": tier.capitalize(),
                "status": sub_status,
                "status_display": sub_status.replace("_", " ").capitalize(),
                "currency": currency,
                "monthly_price_usd": tier_info.get("price_usd"),
                "monthly_price_ngn": tier_info.get("price_ngn"),
                "current_period_end": sub.current_period_end if sub else None,
                "active_students_count": active_students,
                "student_cap": tier_info.get("student_cap"),
                "teachers_count": teachers_count,
                "paystack_customer_code": sub.paystack_customer_code if sub else "",
                "paystack_subscription_code": sub.paystack_subscription_code if sub else "",
                "paystack_subaccount_code": org.paystack_subaccount_code or "",
                "is_active": org.is_active,
                "onboarding_email_sent_at": sub.onboarding_email_sent_at if sub else None,
                "created_at": org.created_at,
            })

        return Response(subscribers_data, status=status.HTTP_200_OK)


@extend_schema(
    summary="Manage Subscriber Academy Platform Subscription",
    request=SubscriberSubscriptionManageSerializer,
    responses={
        200: OpenApiResponse(description="Subscription updated successfully."),
        404: OpenApiResponse(description="Academy not found."),
    },
    tags=["platform-admin"],
)
class PlatformAdminSubscriberManageView(APIView):
    """Allows platform creator to activate, comp, suspend, change tier, or extend subscription."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk, *args, **kwargs):
        org = get_object_or_404(Organization, pk=pk)
        serializer = SubscriberSubscriptionManageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        sub, _ = PlatformSubscription.objects.get_or_create(organization=org)

        now = dj_timezone.now()

        if action == "activate":
            sub.status = SubscriptionStatus.ACTIVE
            org.is_active = True
            if not sub.current_period_end or sub.current_period_end < now:
                sub.current_period_end = now + timedelta(days=30)
            sub.save()
            org.save(update_fields=["is_active"])

        elif action == "comp":
            sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_end = now + timedelta(days=365)
            org.is_active = True
            sub.save()
            org.save(update_fields=["is_active"])

        elif action == "suspend":
            sub.status = SubscriptionStatus.DISABLED
            org.is_active = False
            sub.save()
            org.save(update_fields=["is_active"])

        elif action == "update_tier":
            new_tier = serializer.validated_data.get("tier")
            if not new_tier:
                raise ValidationError({"tier": "Tier is required for update_tier action."})
            sub.tier = new_tier
            sub.save()

        elif action == "extend_period":
            days = serializer.validated_data.get("extension_days", 30)
            base_date = sub.current_period_end if sub.current_period_end and sub.current_period_end > now else now
            sub.current_period_end = base_date + timedelta(days=days)
            sub.status = SubscriptionStatus.ACTIVE
            org.is_active = True
            sub.save()
            org.save(update_fields=["is_active"])

        # Record in audit log
        try:
            AuditLog.objects.create(
                organization=org,
                actor=request.user,
                action="organization.settings_updated",
                object_type="PlatformSubscription",
                object_id=str(sub.id),
                metadata={
                    "action": action,
                    "tier": sub.tier,
                    "status": sub.status,
                    "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
                },
            )
        except Exception as exc:
            logger.warning("Failed to record audit log for subscription management: %s", exc)

        return Response(
            {
                "message": f"Successfully performed '{action}' on {org.name}.",
                "tier": sub.tier,
                "status": sub.status,
                "current_period_end": sub.current_period_end,
                "is_active": org.is_active,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    summary="Oversee Notable Platform Activities",
    parameters=[
        OpenApiParameter(name="limit", description="Max activity records", required=False, type=int),
        OpenApiParameter(name="category", description="Filter category (Billing, Academies, Curriculum, Security)", required=False, type=str),
    ],
    responses={200: PlatformActivitySerializer(many=True)},
    tags=["platform-admin"],
)
class PlatformAdminActivitiesView(APIView):
    """Aggregates notable platform-wide audit log events for executive oversight."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        limit = min(int(request.query_params.get("limit", 60)), 150)
        category_filter = request.query_params.get("category", "").strip().lower()

        logs = AuditLog.objects.all().select_related("organization", "actor").order_by("-created_at")[:limit]

        activities = []
        for log in logs:
            cat = _categorize_audit_action(log.action)
            if category_filter and category_filter not in cat.lower():
                continue

            action_display = log.action.replace(".", " - ").replace("_", " ").title()
            org_name = log.organization.name if log.organization else "System Wide"

            actor_user = log.actor.username if log.actor else "System Automator"
            actor_mail = log.actor.email if log.actor else ""

            activities.append({
                "id": log.id,
                "organization_id": log.organization_id,
                "organization_name": org_name,
                "action": log.action,
                "action_display": action_display,
                "category": cat,
                "actor_username": actor_user,
                "actor_email": actor_mail,
                "object_type": log.object_type or "",
                "object_id": log.object_id or "",
                "metadata": log.metadata or {},
                "created_at": log.created_at,
            })

        return Response(activities, status=status.HTTP_200_OK)


@extend_schema(
    summary="List Subscriber Onboarding Guide Videos",
    responses={200: GuideVideoSerializer(many=True)},
    tags=["platform-admin"],
)
class PlatformAdminOnboardingVideosView(APIView):
    """Returns the official suite of onboarding walkthrough videos."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        return Response(DEFAULT_GUIDE_VIDEOS, status=status.HTTP_200_OK)


@extend_schema(
    summary="Dispatch Onboarding Email with Guide Videos to a Subscriber",
    request=OnboardingEmailSendSerializer,
    responses={
        200: OpenApiResponse(description="Onboarding email dispatched successfully."),
        400: OpenApiResponse(description="Invalid request payload."),
    },
    tags=["platform-admin"],
)
class PlatformAdminSendOnboardingEmailView(APIView):
    """Sends rich onboarding email containing video links, walkthroughs, and setup steps."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def post(self, request, *args, **kwargs):
        serializer = OnboardingEmailSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        org_id = serializer.validated_data.get("organization_id")
        recipient = serializer.validated_data.get("recipient_email", "").strip()
        custom_msg = serializer.validated_data.get("custom_message", "").strip()
        subject = serializer.validated_data.get(
            "subject", "Welcome to Iqra LMS: Complete Academy Setup Guide & Video Walkthroughs"
        )
        include_all = serializer.validated_data.get("include_all_videos", True)
        selected_ids = set(serializer.validated_data.get("selected_video_ids", []))

        organization = None
        if org_id:
            organization = get_object_or_404(Organization, pk=org_id)
            if not recipient and organization.owner_membership and organization.owner_membership.user:
                recipient = organization.owner_membership.user.email

        if not recipient:
            raise ValidationError({"recipient_email": "A valid recipient email or organization is required."})

        # Select videos
        if include_all or not selected_ids:
            videos = DEFAULT_GUIDE_VIDEOS
        else:
            videos = [v for v in DEFAULT_GUIDE_VIDEOS if v["id"] in selected_ids]

        academy_name = organization.name if organization else "your new Quran academy"
        frontend_url = getattr(settings, "FRONTEND_BASE_URL", "http://localhost:3000")

        # Compose HTML email body with video cards
        videos_html = "".join([
            f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px; margin-bottom: 12px;">
                <div style="font-weight: 600; font-size: 14px; color: #0f172a; margin-bottom: 4px;">
                    Step {v['order']}: {v['title']} <span style="background: #e0f2fe; color: #0369a1; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin-left: 6px;">{v['duration']}</span>
                </div>
                <p style="font-size: 12px; color: #475569; margin: 0 0 8px 0;">{v['description']}</p>
                <a href="{v['url']}" style="display: inline-block; background: #0284c7; color: #ffffff; text-decoration: none; padding: 6px 12px; border-radius: 6px; font-size: 12px; font-weight: 500;">
                    ▶ Watch Video Guide ({v['duration']})
                </a>
            </div>
            """
            for v in videos
        ])

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; padding: 24px; color: #1e293b;">
            <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #cbd5e1; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);">
                <div style="background: linear-gradient(135deg, #0369a1, #0284c7); padding: 24px; text-align: center; color: #ffffff;">
                    <h1 style="margin: 0; font-size: 20px; font-weight: 700;">Iqra LMS Academy Founder Guide</h1>
                    <p style="margin: 6px 0 0 0; font-size: 13px; opacity: 0.9;">Everything you need to launch and manage {academy_name}</p>
                </div>
                <div style="padding: 24px;">
                    <p style="font-size: 14px; line-height: 1.5; color: #334155;">
                        Assalamu Alaikum! Welcome to <strong>Iqra LMS</strong>. We are thrilled to partner with you to deliver world-class Quranic memorization, recitation, and Islamic education.
                    </p>
                    {f'<div style="background: #f0fdf4; border-left: 4px solid #16a34a; padding: 12px; margin: 16px 0; font-size: 13px; color: #166534;"><strong>Note from Platform Founder:</strong><br>{custom_msg}</div>' if custom_msg else ''}
                    <h2 style="font-size: 16px; font-weight: 600; color: #0f172a; margin: 20px 0 12px 0;">Video Guides &amp; Launch Walkthroughs</h2>
                    {videos_html}
                    <div style="text-align: center; margin-top: 24px; padding-top: 20px; border-top: 1px solid #e2e8f0;">
                        <a href="{frontend_url}/app/dashboard" style="background: #0f172a; color: #ffffff; text-decoration: none; padding: 10px 24px; border-radius: 8px; font-size: 14px; font-weight: 600; display: inline-block;">
                            Log In to Your Academy Portal &rarr;
                        </a>
                    </div>
                </div>
                <div style="background: #f8fafc; padding: 16px; text-align: center; font-size: 11px; color: #94a3b8; border-top: 1px solid #e2e8f0;">
                    &copy; Iqra LMS Platform. Sent by Platform Administration.
                </div>
            </div>
        </body>
        </html>
        """

        plain_text = strip_tags(html_content)

        # Dispatch email
        from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "support@iqralms.com")
        try:
            send_mail(
                subject=subject,
                message=plain_text,
                from_email=from_email,
                recipient_list=[recipient],
                html_message=html_content,
                fail_silently=False,
            )
        except Exception as exc:
            logger.warning("send_mail failed (simulated or offline): %s", exc)

        # Record dispatch
        dispatch = None
        if organization:
            dispatch = SubscriberOnboardingDispatch.objects.create(
                organization=organization,
                recipient_email=recipient,
                subject=subject,
                sent_by=request.user if request.user.is_authenticated else None,
                video_links=videos,
                custom_message=custom_msg,
            )

            # Update organization subscription onboarding timestamp
            if hasattr(organization, "platform_subscription"):
                organization.platform_subscription.onboarding_email_sent_at = dj_timezone.now()
                organization.platform_subscription.save(update_fields=["onboarding_email_sent_at"])

        return Response(
            {
                "message": f"Onboarding guide email successfully sent to {recipient}.",
                "recipient": recipient,
                "videos_included_count": len(videos),
                "dispatch_id": dispatch.id if dispatch else None,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(
    summary="List Onboarding Email Dispatches",
    responses={200: SubscriberOnboardingDispatchSerializer(many=True)},
    tags=["platform-admin"],
)
class PlatformAdminDispatchesView(APIView):
    """Lists history of sent onboarding emails."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        dispatches = SubscriberOnboardingDispatch.objects.all().select_related(
            "organization", "sent_by"
        ).order_by("-created_at")[:100]
        serializer = SubscriberOnboardingDispatchSerializer(dispatches, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    summary="Platform Global Announcements",
    request=PlatformAnnouncementSerializer,
    responses={200: PlatformAnnouncementSerializer(many=True)},
    tags=["platform-admin"],
)
class PlatformAdminAnnouncementsView(APIView):
    """List or create platform-wide broadcasts."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        announcements = PlatformAnnouncement.objects.all().select_related("created_by")[:50]
        serializer = PlatformAnnouncementSerializer(announcements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        serializer = PlatformAnnouncementSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(
    summary="Platform Video Classroom Provider Configuration",
    request=PlatformVideoConfigSerializer,
    responses={200: PlatformVideoConfigSerializer},
    tags=["platform-admin"],
)
class PlatformAdminVideoProviderView(APIView):
    """Retrieve or update platform-wide live classroom video provider settings."""

    permission_classes = [permissions.IsAuthenticated, IsPlatformAdmin]

    def _get_data(self, config):
        from scheduling.models import Booking, ClassSessionRecording
        active_sessions = Booking.objects.filter(status="scheduled").count()
        total_recordings = ClassSessionRecording.objects.count()

        return {
            "video_provider": config.video_provider,
            "video_provider_display": (
                "Daily.co (In-App Video & Cloud Recording)"
                if config.video_provider == "daily"
                else "Jitsi Meet (Self-Hosted VPS / Open-Source)"
            ),
            "jitsi_domain": config.jitsi_domain,
            "daily_domain": config.daily_domain,
            "daily_api_key_configured": bool(config.daily_api_key or getattr(settings, "DAILY_API_KEY", "")),
            "daily_enable_recording": config.daily_enable_recording,
            "active_classroom_sessions_count": active_sessions,
            "total_recorded_sessions_count": total_recordings,
            "updated_at": config.updated_at,
            "updated_by": config.updated_by.username if config.updated_by else "System Default",
        }

    def get(self, request, *args, **kwargs):
        from .models import PlatformSystemConfig
        config = PlatformSystemConfig.get_current()
        return Response(self._get_data(config), status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        from .models import PlatformSystemConfig
        serializer = PlatformVideoConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        config = PlatformSystemConfig.get_current()
        config.video_provider = data["video_provider"]
        if "jitsi_domain" in data and data["jitsi_domain"]:
            config.jitsi_domain = data["jitsi_domain"].strip()
        if "daily_domain" in data and data["daily_domain"]:
            config.daily_domain = data["daily_domain"].strip()
        if "daily_api_key" in data and data["daily_api_key"]:
            config.daily_api_key = data["daily_api_key"].strip()
        if "daily_enable_recording" in data:
            config.daily_enable_recording = data["daily_enable_recording"]

        config.updated_by = request.user
        config.save()

        # Record audit log
        try:
            from audit_logs.models import AuditAction, AuditLog
            AuditLog.objects.create(
                action=getattr(AuditAction, "VIDEO_PROVIDER_UPDATED", "video.provider_updated"),
                actor_id=request.user.id,
                actor_email=request.user.email or request.user.username,
                metadata={
                    "video_provider": config.video_provider,
                    "jitsi_domain": config.jitsi_domain,
                    "daily_enable_recording": config.daily_enable_recording,
                },
            )
        except Exception:
            pass

        return Response(self._get_data(config), status=status.HTTP_200_OK)
