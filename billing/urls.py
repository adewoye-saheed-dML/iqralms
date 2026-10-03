"""URL patterns for platform subscription billing (Phase 9a)."""

from django.urls import path

from .views import PaystackBillingWebhookView, SubscribeView, SubscriptionStatusView

app_name = "billing"

urlpatterns = [
    path("subscribe/", SubscribeView.as_view(), name="subscribe"),
    path("status/", SubscriptionStatusView.as_view(), name="status"),
    path("webhook/paystack/", PaystackBillingWebhookView.as_view(), name="webhook_paystack"),
]
