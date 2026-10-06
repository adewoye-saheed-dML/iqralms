"""Views for tuition collection, Paystack subaccount setup, and payments history (Phase 9b)."""

import json
import logging

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import ParentLink, Role, User
from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationRole,
    active_membership,
)
from pricing.models import PricingAgreement
from .models import FamilyPayment
from .paystack import PaystackAPIError, verify_paystack_signature
from .serializers import (
    FamilyPaymentSerializer,
    PaymentInitializeResponseSerializer,
    PaymentInitializeSerializer,
    PaystackWebhookPayloadSerializer,
    SubaccountConfirmSerializer,
    SubaccountResolveResponseSerializer,
    SubaccountResolveSerializer,
    SubaccountSetupResponseSerializer,
)
from .services import (
    confirm_academy_subaccount,
    initialize_family_payment,
    process_tuition_webhook,
    resolve_academy_subaccount,
    verify_family_payment,
)

logger = logging.getLogger(__name__)


def _paystack_error_response(exc: PaystackAPIError) -> Response:
    """Convert a PaystackAPIError into a clean JSON error response."""
    if exc.status_code in (429, 502):
        code = exc.status_code
    elif exc.status_code < 500:
        code = 400
    else:
        code = 502
    return Response({"detail": str(exc)}, status=code)


@extend_schema(
    summary="Resolve bank account details for tuition settlement",
    request=SubaccountResolveSerializer,
    responses={
        200: SubaccountResolveResponseSerializer,
        403: OpenApiResponse(description="Only academy owner or admin can resolve bank details."),
    },
)
class SubaccountResolveView(APIView):
    """Owner/Admin resolve bank details for tuition routing."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SubaccountResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        organization = get_object_or_404(Organization, pk=data["organization_id"])
        membership = active_membership(user=request.user, organization=organization)
        if not membership or membership.role not in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
            raise PermissionDenied("Only academy owners or administrators can resolve bank details.")

        try:
            result = resolve_academy_subaccount(
                organization=organization,
                bank_code=data["bank_code"],
                account_number=data["account_number"],
            )
        except PaystackAPIError as exc:
            return _paystack_error_response(exc)

        return Response(SubaccountResolveResponseSerializer(result).data, status=status.HTTP_200_OK)


@extend_schema(
    summary="Confirm bank account details and create Paystack settlement subaccount",
    request=SubaccountConfirmSerializer,
    responses={
        200: SubaccountSetupResponseSerializer,
        403: OpenApiResponse(description="Only academy owner or admin can confirm bank details."),
    },
)
class SubaccountConfirmView(APIView):
    """Owner/Admin confirm and create settlement subaccount."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = SubaccountConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        organization = get_object_or_404(Organization, pk=data["organization_id"])
        membership = active_membership(user=request.user, organization=organization)
        if not membership or membership.role not in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
            raise PermissionDenied("Only academy owners or administrators can configure settlement bank details.")

        try:
            result = confirm_academy_subaccount(
                organization=organization,
                bank_code=data["bank_code"],
                account_number=data["account_number"],
                business_name=data.get("business_name"),
            )
        except PaystackAPIError as exc:
            return _paystack_error_response(exc)

        return Response(SubaccountSetupResponseSerializer(result).data, status=status.HTTP_200_OK)


@extend_schema(
    summary="Initialize tuition payment for an adult student or by a parent",
    request=PaymentInitializeSerializer,
    responses={
        201: PaymentInitializeResponseSerializer,
        403: OpenApiResponse(description="Minors or unlinked parents are denied."),
    },
)
class PaymentInitializeView(APIView):
    """Initialize a tuition payment for an adult student or by a linked parent."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        user = request.user
        if getattr(user, "is_minor", False):
            raise PermissionDenied("Minor students cannot initiate payments.")

        serializer = PaymentInitializeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pricing_agreement_id = serializer.validated_data.get("pricing_agreement_id")
        organization_id = serializer.validated_data.get("organization_id")
        target_student_id = serializer.validated_data.get("student_id")
        callback_url = serializer.validated_data.get("callback_url")

        if user.role == Role.STUDENT:
            student = user
        elif user.role == Role.PARENT:
            if not target_student_id:
                raise ValidationError({"student_id": "student_id is required when paying as a parent."})
            is_linked = ParentLink.objects.filter(parent=user, student_id=target_student_id).exists()
            if not is_linked:
                raise PermissionDenied("You are not linked to this student.")
            student = get_object_or_404(User, pk=target_student_id, role=Role.STUDENT)
        else:
            raise PermissionDenied("Only students or parents can initiate tuition payments.")

        # Cross-tenant boundary enforcement:
        from accounts.tenancy import active_parent_membership, is_active_student_participant

        if organization_id:
            organization = get_object_or_404(Organization, pk=organization_id)
            if not organization.is_active:
                raise PermissionDenied("This academy is not active.")

            if user.role == Role.PARENT:
                parent_membership = active_parent_membership(user=user, organization=organization)
                if not parent_membership:
                    raise PermissionDenied("You do not have active access to this academy.")

            if not is_active_student_participant(user=student, organization=organization):
                raise PermissionDenied("Student is not an active participant of the specified academy.")

            if pricing_agreement_id:
                pricing_agreement = get_object_or_404(
                    PricingAgreement,
                    pk=pricing_agreement_id,
                    student=student,
                    active=True,
                )
                if not pricing_agreement.organization or pricing_agreement.organization.pk != organization.pk:
                    raise ValidationError(
                        {"pricing_agreement_id": "Pricing agreement does not belong to the specified academy."}
                    )
            else:
                pricing_agreement = (
                    PricingAgreement.objects.in_organization(organization)
                    .filter(
                        student=student,
                        active=True,
                    )
                    .first()
                )
                if not pricing_agreement:
                    raise ValidationError(
                        {"pricing_agreement_id": "No active pricing agreement found for this student in this academy."}
                    )
        else:
            if pricing_agreement_id:
                pricing_agreement = get_object_or_404(
                    PricingAgreement,
                    pk=pricing_agreement_id,
                    student=student,
                    active=True,
                )
            else:
                pricing_agreement = PricingAgreement.objects.filter(
                    student=student,
                    active=True,
                ).first()
                if not pricing_agreement:
                    raise ValidationError({"pricing_agreement_id": "No active pricing agreement found for this student."})

            organization = pricing_agreement.organization
            if not organization or not organization.is_active:
                raise PermissionDenied("This academy is not active.")

            if user.role == Role.PARENT:
                parent_membership = active_parent_membership(user=user, organization=organization)
                if not parent_membership:
                    raise PermissionDenied("You do not have active access to this academy.")

            if not is_active_student_participant(user=student, organization=organization):
                raise PermissionDenied("Student is not an active participant of this academy.")

        try:
            payment, init_data = initialize_family_payment(
                payer=user,
                student=student,
                pricing_agreement=pricing_agreement,
                callback_url=callback_url,
            )
        except PaystackAPIError as exc:
            return _paystack_error_response(exc)

        response_data = {
            "payment_id": payment.pk,
            "reference": payment.paystack_reference,
            "amount": payment.amount,
            "currency": payment.currency,
            "authorization_url": init_data.get("authorization_url", ""),
            "access_code": init_data.get("access_code", ""),
        }
        return Response(
            PaymentInitializeResponseSerializer(response_data).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    summary="Immediate payment verification check",
    responses={
        200: OpenApiResponse(description="Payment verification details and Paystack transaction status."),
        403: OpenApiResponse(description="Unauthorized to view this payment."),
    },
)
class PaymentVerifyView(APIView):
    """Immediate payment check on Paystack following redirect (does not mark paid)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, reference, *args, **kwargs):
        payment = get_object_or_404(FamilyPayment, paystack_reference=reference)
        user = request.user

        # Visibility check
        can_view = False
        if user == payment.student and not user.is_minor:
            can_view = True
        elif user == payment.initiated_by:
            can_view = True
        elif user.role == Role.PARENT and ParentLink.objects.filter(parent=user, student=payment.student).exists():
            can_view = True
        else:
            membership = active_membership(user=user, organization=payment.organization)
            if membership and (
                membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
                or (membership.role == OrganizationRole.TEACHER and user.role == Role.LEAD)
            ):
                can_view = True

        if not can_view:
            raise PermissionDenied("You are not authorized to view this payment.")

        try:
            verify_data = verify_family_payment(reference)
        except PaystackAPIError as exc:
            return _paystack_error_response(exc)

        return Response(
            {
                "reference": reference,
                "status": payment.status,
                "paystack_data": verify_data,
            },
            status=status.HTTP_200_OK,
        )


TUITION_EVENTS = {"charge.success", "charge.failed"}
BILLING_EVENTS = {
    "subscription.create",
    "subscription.not_renew",
    "subscription.disable",
    "invoice.create",
    "invoice.update",
    "invoice.payment_failed",
}


@extend_schema(
    summary="Unified Paystack webhook endpoint",
    request=PaystackWebhookPayloadSerializer,
    responses={200: OpenApiResponse(description="Webhook event processed or acknowledged.")},
)
class UnifiedPaystackWebhookView(APIView):
    """Unified webhook endpoint for all Paystack events."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        raw_body = request.body
        signature = request.META.get("HTTP_X_PAYSTACK_SIGNATURE")

        if not verify_paystack_signature(raw_body, signature):
            logger.warning("Invalid Paystack signature on webhook.")
            return Response({"error": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"error": "Invalid JSON payload."}, status=status.HTTP_400_BAD_REQUEST)

        event = payload.get("event")
        data = payload.get("data", {})

        if event in TUITION_EVENTS:
            # Check if this charge event is actually a platform subscription first payment
            if data.get("metadata", {}).get("type") == "platform_subscription":
                return Response({"status": "success", "message": "acknowledged"}, status=status.HTTP_200_OK)
            success, msg = process_tuition_webhook(payload)
            return Response({"status": "success", "message": msg}, status=status.HTTP_200_OK)

        elif event in BILLING_EVENTS:
            from billing.services import process_billing_webhook
            success, msg = process_billing_webhook(payload)
            return Response({"status": "success", "message": msg}, status=status.HTTP_200_OK)

        else:
            logger.info("Acknowledged unknown or unhandled Paystack event: %s", event)
            return Response({"status": "success", "message": "acknowledged"}, status=status.HTTP_200_OK)


class PaystackTuitionWebhookView(UnifiedPaystackWebhookView):
    """Webhook endpoint for Paystack tuition payments (delegates to UnifiedPaystackWebhookView)."""
    pass


@extend_schema(
    summary="List payment history for authenticated adult student",
    responses={
        200: FamilyPaymentSerializer(many=True),
        403: OpenApiResponse(description="Minor students are forbidden."),
    },
)
class StudentPaymentListView(APIView):
    """List payment history for the authenticated adult student."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        if getattr(user, "is_minor", False):
            raise PermissionDenied("Minor students have no financial access.")
        if user.role != Role.STUDENT:
            raise PermissionDenied("Only students can view their personal payment history.")

        payments = FamilyPayment.objects.filter(student=user).select_related(
            "student", "organization", "initiated_by", "pricing_agreement"
        )
        return Response(FamilyPaymentSerializer(payments, many=True).data)


@extend_schema(
    summary="List payment history for linked children (Parent only)",
    parameters=[
        OpenApiParameter(
            name="student_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Optional filter for a specific linked child.",
        )
    ],
    responses={
        200: FamilyPaymentSerializer(many=True),
        403: OpenApiResponse(description="Only parents can view children payments."),
    },
)
class ParentChildPaymentListView(APIView):
    """List payment history for linked children (Parent only)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        if user.role != Role.PARENT:
            raise PermissionDenied("Only parents can view children's payment history.")

        student_ids = list(
            ParentLink.objects.filter(parent=user).values_list("student_id", flat=True)
        )
        filter_student_id = request.query_params.get("student_id")
        if filter_student_id:
            try:
                sid = int(filter_student_id)
            except ValueError:
                raise ValidationError({"student_id": "Invalid student ID."})
            if sid not in student_ids:
                raise PermissionDenied("You are not linked to that student.")
            student_ids = [sid]

        payments = FamilyPayment.objects.filter(student_id__in=student_ids).select_related(
            "student", "organization", "initiated_by", "pricing_agreement"
        )
        return Response(FamilyPaymentSerializer(payments, many=True).data)


@extend_schema(
    summary="List payment history for an academy (Owner/Admin or Lead Teacher)",
    parameters=[
        OpenApiParameter(
            name="organization_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=True,
            description="Organization ID to fetch payments for.",
        )
    ],
    responses={
        200: FamilyPaymentSerializer(many=True),
        403: OpenApiResponse(description="Unauthorized to view academy payments."),
    },
)
class OrganizationPaymentListView(APIView):
    """List payment history for an academy (Owner/Admin or Lead Teacher)."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        org_id = request.query_params.get("organization_id")
        if not org_id:
            raise ValidationError({"organization_id": "organization_id query parameter is required."})

        organization = get_object_or_404(Organization, pk=org_id)
        membership = active_membership(user=user, organization=organization)
        if not membership:
            raise PermissionDenied("You do not belong to this organization.")

        is_allowed = (
            membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
            or (membership.role == OrganizationRole.TEACHER and user.role == Role.LEAD)
        )
        if not is_allowed:
            raise PermissionDenied("Only owners, administrators, or lead teachers can view academy payments.")

        payments = FamilyPayment.objects.in_organization(organization).select_related(
            "student", "organization", "initiated_by", "pricing_agreement"
        )
        return Response(FamilyPaymentSerializer(payments, many=True).data)