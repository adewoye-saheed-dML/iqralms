"""Pricing URLs, mounted at /api/pricing/.

Following SaaS multi-tenant conventions established in Phases 2-4:
    /api/pricing/organizations/<organization_pk>/agreements/
    /api/pricing/organizations/<organization_pk>/agreements/mine/

Legacy unscoped routes (/api/pricing/agreements/) are retired to prevent
tenant boundary bypass.
"""

from django.urls import path

from .views import (
    ChildActivePricingAgreementView,
    MyPricingAgreementListView,
    PricingAgreementView,
)

app_name = "pricing"

ACADEMY = "organizations/<int:organization_pk>/"

urlpatterns = [
    path(
        f"{ACADEMY}children/<int:student_id>/active/",
        ChildActivePricingAgreementView.as_view(),
        name="org-child-active-agreement",
    ),
    path(
        "children/<int:student_id>/active/",
        ChildActivePricingAgreementView.as_view(),
        name="child-active-agreement",
    ),
    path(
        f"{ACADEMY}agreements/mine/",
        MyPricingAgreementListView.as_view(),
        name="agreement-mine",
    ),
    path(
        f"{ACADEMY}agreements/",
        PricingAgreementView.as_view(),
        name="agreements",
    ),
]

