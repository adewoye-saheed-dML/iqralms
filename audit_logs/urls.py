from django.urls import path
from .views import AuditLogListView, AuditLogDetailView

app_name = "audit_logs"

urlpatterns = [
    path(
        "<int:organization_pk>/audit-logs/",
        AuditLogListView.as_view(),
        name="audit-log-list",
    ),
    path(
        "<int:organization_pk>/audit-logs/<int:pk>/",
        AuditLogDetailView.as_view(),
        name="audit-log-detail",
    ),
]
