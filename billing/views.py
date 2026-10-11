"""Views for platform subscription billing and Paystack webhooks (Phase 9a)."""

import json
import logging

from django.conf import settings
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import MembershipStatus, Organization, OrganizationRole
from payments.paystack import verify_paystack_signature
from payments.serializers import PaystackWebhookPayloadSerializer
from .constants import get_tiers_display
from .models import PlatformSubscription
from .serializers import (
    PlatformSubscriptionSerializer,
    SubscribeRequestSerializer,
    SubscriptionInitializeResponseSerializer,
    TierDisplaySerializer,
)
from .services import (
    initialize_platform_subscription,
    process_billing_webhook,
    subscribe_organization,
)

logger = logging.getLogger(__name__)


@extend_schema(
    summary="List all platform subscription tiers with pricing and student caps",
    responses={200: TierDisplaySerializer(many=True)},
)
class BillingTiersListView(APIView):
    """Public, unauthenticated endpoint listing subscription tiers with NGN and USD display pricing."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, *args, **kwargs):
        return Response(get_tiers_display(), status=status.HTTP_200_OK)


@extend_schema(
    summary="Initialize platform subscription payment for an academy",
    request=SubscribeRequestSerializer,
    responses={
        200: SubscriptionInitializeResponseSerializer,
        403: OpenApiResponse(description="Only organization owner can subscribe."),
    },
)
class SubscribeView(APIView):
    """Initialize platform subscription checkout for an academy (Owner only)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SubscribeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org_id = serializer.validated_data["organization_id"]
        tier = serializer.validated_data.get("tier", "starter")
        callback_url = serializer.validated_data.get("callback_url")

        organization = get_object_or_404(Organization, pk=org_id)
        membership = organization.memberships.filter(
            user=request.user,
            status=MembershipStatus.ACTIVE,
        ).first()
        if not membership or membership.role != OrganizationRole.OWNER:
            raise PermissionDenied("Only the academy owner can manage platform subscriptions.")

        subscription, init_data = initialize_platform_subscription(
            organization=organization,
            user=request.user,
            tier=tier,
            callback_url=callback_url,
        )
        return Response(
            {
                "authorization_url": init_data.get("authorization_url", ""),
                "access_code": init_data.get("access_code", ""),
                "reference": subscription.paystack_initialization_reference,
            },
            status=status.HTTP_200_OK,
        )



@extend_schema(
    summary="Get platform subscription status for an academy",
    parameters=[
        OpenApiParameter(
            name="organization_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Organization ID to check subscription status for.",
        )
    ],
    responses={
        200: PlatformSubscriptionSerializer,
        404: OpenApiResponse(description="No subscription found."),
    },
)
class SubscriptionStatusView(APIView):
    """View subscription status for an academy.

    Accessible to any active member of the academy (Owner, Admin, Teacher,
    Student, Parent) as well as parents of enrolled students, so their
    respective dashboards can verify the academy's platform subscription standing.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        org_id = request.query_params.get("organization_id")
        if not org_id:
            # Fall back to the first organization where user is owner, or has active membership
            membership = request.user.organization_memberships.filter(
                role=OrganizationRole.OWNER,
                status=MembershipStatus.ACTIVE,
            ).first()
            if not membership:
                membership = request.user.organization_memberships.filter(
                    status=MembershipStatus.ACTIVE,
                ).first()
            if not membership:
                raise ValidationError({"organization_id": "Organization ID is required."})
            organization = membership.organization
        else:
            organization = get_object_or_404(Organization, pk=org_id)
            membership = organization.memberships.filter(
                user=request.user,
                status=MembershipStatus.ACTIVE,
            ).first()
            has_child_membership = organization.memberships.filter(
                user__parent_links__parent=request.user,
                status=MembershipStatus.ACTIVE,
            ).exists()
            has_enrollment = (
                hasattr(organization, "student_enrollments")
                and organization.student_enrollments.filter(user=request.user).exists()
            )
            has_child_enrollment = (
                hasattr(organization, "student_enrollments")
                and organization.student_enrollments.filter(user__parent_links__parent=request.user).exists()
            )

            if not (
                request.user.is_superuser
                or request.user.is_staff
                or membership
                or has_child_membership
                or has_enrollment
                or has_child_enrollment
            ):
                raise PermissionDenied("Only members of this academy can view its subscription status.")

        subscription = PlatformSubscription.objects.filter(organization=organization).first()
        if not subscription:
            return Response(
                {"detail": "No subscription found for this organization.", "status": "none"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Fallback sync: If subscription is pending and has a reference, check Paystack directly
        # so user is immediately activated even if the webhook was delayed or dropped.
        ref = request.query_params.get("reference") or subscription.paystack_initialization_reference
        if subscription.status == "pending" and ref:
            from payments.paystack import PaystackClient
            from .models import SubscriptionStatus
            try:
                client = PaystackClient()
                v_data = client.verify_transaction(ref)
                if v_data.get("status") == "success":
                    subscription.status = SubscriptionStatus.ACTIVE
                    customer_code = v_data.get("customer", {}).get("customer_code", "")
                    if customer_code:
                        subscription.paystack_customer_code = customer_code
                    plan_obj = v_data.get("plan_object") or {}
                    sub_code = (
                        v_data.get("subscription_code")
                        or plan_obj.get("subscription_code")
                        or plan_obj.get("plan_code")
                    )
                    if sub_code:
                        subscription.paystack_subscription_code = sub_code
                    subscription.save()
                    if not organization.is_active:
                        organization.is_active = True
                        organization.save(update_fields=["is_active", "updated_at"])
                    logger.info("Subscription for organization %s auto-activated via Paystack verify.", organization.pk)
            except Exception as exc:
                logger.warning("Could not auto-verify subscription reference %s: %s", ref, exc)

        return Response(PlatformSubscriptionSerializer(subscription).data)


from payments.views import UnifiedPaystackWebhookView


@extend_schema(
    summary="Paystack platform subscription webhook endpoint",
    request=PaystackWebhookPayloadSerializer,
    responses={200: OpenApiResponse(description="Webhook event processed or acknowledged.")},
)
class PaystackBillingWebhookView(UnifiedPaystackWebhookView):
    """Webhook endpoint for Paystack platform subscription events (delegates to UnifiedPaystackWebhookView)."""
    pass
