"""URL patterns for the notification domain."""

from django.urls import path

from .views import (
    AdminDeliveryListView,
    AdminNotificationListView,
    MyNotificationListView,
    NotificationDetailView,
    NotificationMarkReadView,
)

app_name = "notifications"

urlpatterns = [
    path(
        "organizations/<int:organization_pk>/mine/",
        MyNotificationListView.as_view(),
        name="notification-mine",
    ),
    path(
        "organizations/<int:organization_pk>/admin/",
        AdminNotificationListView.as_view(),
        name="notification-admin-list",
    ),
    path(
        "organizations/<int:organization_pk>/deliveries/",
        AdminDeliveryListView.as_view(),
        name="delivery-admin-list",
    ),
    path(
        "organizations/<int:organization_pk>/<int:pk>/",
        NotificationDetailView.as_view(),
        name="notification-detail",
    ),
    path(
        "organizations/<int:organization_pk>/<int:pk>/read/",
        NotificationMarkReadView.as_view(),
        name="notification-read",
    ),
]
