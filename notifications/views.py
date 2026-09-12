"""Tenant-scoped API views for the notification domain."""

from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from organizations.models import OrganizationRole
from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin

from .models import Notification, NotificationDelivery
from .permissions import CanManageAcademyNotifications
from .serializers import NotificationDeliverySerializer, NotificationSerializer


class AcademyScopedNotificationView(OrganizationScopedMixin):
    """Base class for organization-scoped notification endpoints."""

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.caller_membership:
            context["organization"] = self.organization
        return context


class MyNotificationListView(AcademyScopedNotificationView, generics.ListAPIView):
    """GET /api/notifications/organizations/<organization_pk>/mine/ — user's own notifications in this academy."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="unread",
                type=bool,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter to only unread notifications when true.",
            )
        ],
        responses={200: NotificationSerializer(many=True)},
    )
    def get_queryset(self):
        qs = (
            Notification.objects.in_organization(self.organization)
            .filter(recipient=self.request.user)
            .prefetch_related("deliveries")
        )
        unread_param = self.request.query_params.get("unread")
        if unread_param and unread_param.lower() in ("true", "1", "yes"):
            qs = qs.unread()
        return qs


class AdminNotificationListView(AcademyScopedNotificationView, generics.ListAPIView):
    """GET /api/notifications/organizations/<organization_pk>/admin/ — academy-wide notification history (admin only)."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, CanManageAcademyNotifications]

    @extend_schema(responses={200: NotificationSerializer(many=True)})
    def get_queryset(self):
        return (
            Notification.objects.in_organization(self.organization)
            .select_related("recipient")
            .prefetch_related("deliveries")
        )


class NotificationDetailView(AcademyScopedNotificationView, generics.RetrieveAPIView):
    """GET /api/notifications/organizations/<organization_pk>/<id>/ — notification detail.

    Owner/admin can read any notification in this academy.
    Regular members can only read their own notifications in this academy.
    Notifications in other academies or belonging to other users return 404.
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]
    lookup_field = "pk"

    @extend_schema(responses={200: NotificationSerializer, 404: OpenApiResponse(description="Not found.")})
    def get_queryset(self):
        membership = self.caller_membership
        if membership and membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}:
            return (
                Notification.objects.in_organization(self.organization)
                .select_related("recipient")
                .prefetch_related("deliveries")
            )
        return (
            Notification.objects.in_organization(self.organization)
            .filter(recipient=self.request.user)
            .prefetch_related("deliveries")
        )


class NotificationMarkReadView(AcademyScopedNotificationView, generics.GenericAPIView):
    """POST /api/notifications/organizations/<organization_pk>/<id>/read/ — mark a notification as read.

    A recipient can only mark their own notification as read.
    """

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember]
    lookup_field = "pk"

    def get_queryset(self):
        return Notification.objects.in_organization(self.organization).filter(
            recipient=self.request.user
        )

    @extend_schema(
        request=None,
        responses={
            200: NotificationSerializer,
            404: OpenApiResponse(description="Notification not found for this recipient in this academy."),
        },
    )
    def post(self, request, *args, **kwargs):
        notification = self.get_object()
        notification.mark_as_read()
        serializer = self.get_serializer(notification)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminDeliveryListView(AcademyScopedNotificationView, generics.ListAPIView):
    """GET /api/notifications/organizations/<organization_pk>/deliveries/ — academy delivery logs (admin only)."""

    serializer_class = NotificationDeliverySerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, CanManageAcademyNotifications]

    @extend_schema(responses={200: NotificationDeliverySerializer(many=True)})
    def get_queryset(self):
        return (
            NotificationDelivery.objects.in_organization(self.organization)
            .select_related("notification", "notification__recipient")
        )
