from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from organizations.views import OrganizationScopedMixin
from organizations.permissions import CanManageOrganizationMemberships
from .models import AuditLog
from .serializers import AuditLogSerializer

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 1000

class AuditLogListView(OrganizationScopedMixin, generics.ListAPIView):
    """
    GET /api/organizations/{organization_pk}/audit-logs/
    List audit logs for an organization.
    """
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    pagination_class = StandardResultsSetPagination
    organization_url_kwarg = "organization_pk"

    def get_queryset(self):
        # Enforce tenant boundary
        qs = AuditLog.objects.filter(organization_id=self.organization_id)
        
        # Filtering
        action = self.request.query_params.get("action")
        if action:
            qs = qs.filter(action=action)
            
        actor = self.request.query_params.get("actor")
        if actor:
            qs = qs.filter(actor_id=actor)
            
        object_type = self.request.query_params.get("object_type")
        if object_type:
            qs = qs.filter(object_type=object_type)
            
        object_id = self.request.query_params.get("object_id")
        if object_id:
            qs = qs.filter(object_id=object_id)
            
        created_after = self.request.query_params.get("created_after")
        if created_after:
            qs = qs.filter(created_at__gte=created_after)
            
        created_before = self.request.query_params.get("created_before")
        if created_before:
            qs = qs.filter(created_at__lte=created_before)
            
        return qs

    @extend_schema(
        parameters=[
            OpenApiParameter("action", str, required=False),
            OpenApiParameter("actor", int, required=False),
            OpenApiParameter("object_type", str, required=False),
            OpenApiParameter("object_id", str, required=False),
            OpenApiParameter("created_after", str, required=False, description="ISO 8601 datetime"),
            OpenApiParameter("created_before", str, required=False, description="ISO 8601 datetime"),
        ],
        responses={
            200: OpenApiResponse(response=AuditLogSerializer(many=True)),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

class AuditLogDetailView(OrganizationScopedMixin, generics.RetrieveAPIView):
    """
    GET /api/organizations/{organization_pk}/audit-logs/{id}/
    Retrieve a specific audit log for an organization.
    """
    serializer_class = AuditLogSerializer
    permission_classes = [IsAuthenticated, CanManageOrganizationMemberships]
    organization_url_kwarg = "organization_pk"
    
    def get_queryset(self):
        # Enforce tenant boundary
        return AuditLog.objects.filter(organization_id=self.organization_id)

    @extend_schema(
        responses={
            200: OpenApiResponse(response=AuditLogSerializer),
            401: OpenApiResponse(description="Not authenticated."),
            403: OpenApiResponse(description="Not an owner or administrator here."),
            404: OpenApiResponse(description="Not found or cross-tenant access attempt."),
        }
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
