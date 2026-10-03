"""URL configuration for payments app (Phase 9b)."""

from django.urls import path

from .views import (
    OrganizationPaymentListView,
    ParentChildPaymentListView,
    PaymentInitializeView,
    PaymentVerifyView,
    PaystackTuitionWebhookView,
    StudentPaymentListView,
    SubaccountSetupView,
)

app_name = "payments"

urlpatterns = [
    path("subaccount/setup/", SubaccountSetupView.as_view(), name="subaccount_setup"),
    path("initialize/", PaymentInitializeView.as_view(), name="initialize"),
    path("verify/<str:reference>/", PaymentVerifyView.as_view(), name="verify"),
    path("webhook/paystack/", PaystackTuitionWebhookView.as_view(), name="webhook_paystack"),
    path("mine/", StudentPaymentListView.as_view(), name="mine"),
    path("children/", ParentChildPaymentListView.as_view(), name="children"),
    path("organization/", OrganizationPaymentListView.as_view(), name="organization"),
]
