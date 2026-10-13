"""URL patterns for platform subscription billing (Phase 9a)."""

from django.urls import path

from .views import (
    BillingLocationView,
    BillingTiersListView,
    PaystackBillingWebhookView,
    SubscribeView,
    SubscriptionStatusView,
)

app_name = "billing"

urlpatterns = [
    path("location/", BillingLocationView.as_view(), name="location"),
    path("tiers/", BillingTiersListView.as_view(), name="tiers"),
    path("subscribe/initialize/", SubscribeView.as_view(), name="subscribe_initialize"),
    path("subscribe/", SubscribeView.as_view(), name="subscribe"),
    path("status/", SubscriptionStatusView.as_view(), name="status"),
]

