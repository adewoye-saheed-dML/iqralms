"""URL configuration for payments app (Phase 9b)."""

from django.urls import path

from .views import (
    BankListView,
    OrganizationPaymentListView,
    ParentChildPaymentListView,
    PaymentInitializeView,
    PaymentVerifyView,
    PaystackTuitionWebhookView,
    StudentPaymentListView,
    SubaccountConfirmView,
    SubaccountResolveView,
    SubaccountStatusView,
)

app_name = "payments"

urlpatterns = [
    path("banks/", BankListView.as_view(), name="bank_list"),
    path("subaccount/status/", SubaccountStatusView.as_view(), name="subaccount_status"),
    path("subaccount/resolve/", SubaccountResolveView.as_view(), name="subaccount_resolve"),
    path("subaccount/confirm/", SubaccountConfirmView.as_view(), name="subaccount_confirm"),
    path("initialize/", PaymentInitializeView.as_view(), name="initialize"),
    path("verify/<str:reference>/", PaymentVerifyView.as_view(), name="verify"),
    path("mine/", StudentPaymentListView.as_view(), name="mine"),
    path("children/", ParentChildPaymentListView.as_view(), name="children"),
    path("organization/", OrganizationPaymentListView.as_view(), name="organization"),
    path("webhook/", PaystackTuitionWebhookView.as_view(), name="webhook"),
]
