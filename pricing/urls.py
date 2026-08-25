"""Pricing URLs, mounted at /api/pricing/.

``agreements/`` carries both the lead's POST and their GET, which is the surface
the spec lists. ``agreements/mine/`` sits above it in the file for the usual
Django reason — a more specific path must be matched before a broader one could
swallow it — though these two cannot actually collide.
"""

from django.urls import path

from .views import MyPricingAgreementListView, PricingAgreementView

app_name = "pricing"

urlpatterns = [
    path(
        "agreements/mine/",
        MyPricingAgreementListView.as_view(),
        name="agreement-mine",
    ),
    path("agreements/", PricingAgreementView.as_view(), name="agreements"),
]
