"""Views for platform subscription billing and Paystack webhooks (Phase 9a)."""

import json
import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import Organization, OrganizationRole, active_membership
from payments.paystack import verify_paystack_signature
from payments.serializers import PaystackWebhookPayloadSerializer
from .models import PlatformSubscription
from .serializers import PlatformSubscriptionSerializer, SubscribeRequestSerializer
from .services import process_billing_webhook, subscribe_organization

logger = logging.getLogger(__name__)


@extend_schema(
    summary="Subscribe organization to IqraLMS platform plan",
    request=SubscribeRequestSerializer,
    responses={
        200: PlatformSubscriptionSerializer,
        403: OpenApiResponse(description="Only organization owner can subscribe."),
    },
)
class SubscribeView(APIView):
    """Subscribe an academy to IqraLMS platform plan (Owner only)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SubscribeRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org_id = serializer.validated_data["organization_id"]
        plan_code = serializer.validated_data.get("plan_code")

        organization = get_object_or_404(Organization, pk=org_id)
        membership = active_membership(user=request.user, organization=organization)
        if not membership or membership.role != OrganizationRole.OWNER:
            raise PermissionDenied("Only the academy owner can manage platform subscriptions.")

        subscription = subscribe_organization(
            organization=organization,
            user=request.user,
            plan_code=plan_code,
        )
        return Response(
            PlatformSubscriptionSerializer(subscription).data,
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
    """View subscription status for an academy (Owner/Admin only)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        org_id = request.query_params.get("organization_id")
        if not org_id:
            # Fall back to the first organization where user is owner
            membership = request.user.organization_memberships.filter(
                role=OrganizationRole.OWNER,
                status="active",
            ).first()
            if not membership:
                raise ValidationError({"organization_id": "Organization ID is required."})
            organization = membership.organization
        else:
            organization = get_object_or_404(Organization, pk=org_id)
            membership = active_membership(user=request.user, organization=organization)
            if not membership or membership.role not in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
                raise PermissionDenied("Only academy owners or administrators can view subscription status.")

        subscription = PlatformSubscription.objects.filter(organization=organization).first()
        if not subscription:
            return Response(
                {"detail": "No subscription found for this organization.", "status": "none"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(PlatformSubscriptionSerializer(subscription).data)


@extend_schema(
    summary="Paystack platform subscription webhook endpoint",
    request=PaystackWebhookPayloadSerializer,
    responses={200: OpenApiResponse(description="Webhook event processed or acknowledged.")},
)
class PaystackBillingWebhookView(APIView):
    """Webhook endpoint for Paystack platform subscription events.

    Verifies HMAC-SHA512 signature on raw payload.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        raw_body = request.body
        signature = request.META.get("HTTP_X_PAYSTACK_SIGNATURE")

        if not verify_paystack_signature(raw_body, signature):
            logger.warning("Invalid Paystack signature on billing webhook.")
            return Response({"error": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"error": "Invalid JSON payload."}, status=status.HTTP_400_BAD_REQUEST)

        success, msg = process_billing_webhook(payload)
        return Response({"status": "success", "message": msg}, status=status.HTTP_200_OK)
