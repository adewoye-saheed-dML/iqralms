"""Payout URLs, mounted at /api/payouts/.

Following SaaS multi-tenant conventions established in Phases 2-6:
    /api/payouts/organizations/<organization_pk>/mine/
    /api/payouts/organizations/<organization_pk>/statements/mine/
    /api/payouts/organizations/<organization_pk>/statements/
    /api/payouts/organizations/<organization_pk>/generate/
    /api/payouts/organizations/<organization_pk>/lead/
    /api/payouts/organizations/<organization_pk>/<pk>/finalize/

Legacy unscoped routes are retired to prevent tenant boundary bypass.
Order matters in the usual Django way: ``statements/mine/`` sits above
``statements/``, and both sit above ``<int:pk>/finalize/`` so that no literal
path could be read as an id.
"""

from django.urls import path

from .views import (
    LeadPayoutListView,
    LeadStatementView,
    MyPayoutListView,
    MyStatementView,
    PayoutFinalizeView,
    PayoutGenerateView,
)

app_name = "payouts"

ACADEMY = "organizations/<int:organization_pk>/"

urlpatterns = [
    path(f"{ACADEMY}mine/", MyPayoutListView.as_view(), name="payout-mine"),
    path(f"{ACADEMY}statements/mine/", MyStatementView.as_view(), name="statement-mine"),
    path(f"{ACADEMY}statements/", LeadStatementView.as_view(), name="statements"),
    path(f"{ACADEMY}generate/", PayoutGenerateView.as_view(), name="payout-generate"),
    path(f"{ACADEMY}lead/", LeadPayoutListView.as_view(), name="payout-lead"),
    path(f"{ACADEMY}<int:pk>/finalize/", PayoutFinalizeView.as_view(), name="payout-finalize"),
]
