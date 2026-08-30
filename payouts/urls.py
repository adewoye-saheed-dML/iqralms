"""Payout URLs, mounted at /api/payouts/.

Order matters in the usual Django way: ``statements/mine/`` sits above
``statements/``, and both sit above ``<int:pk>/finalize/`` so that no literal
path could be read as an id. The names follow specs/phase-8-payouts.md's
suggested surface, with one documented departure — a statement is addressed by
period rather than by id, because statements are computed rather than stored (see
views.MyStatementView).
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

urlpatterns = [
    path("mine/", MyPayoutListView.as_view(), name="payout-mine"),
    path("statements/mine/", MyStatementView.as_view(), name="statement-mine"),
    path("statements/", LeadStatementView.as_view(), name="statements"),
    path("generate/", PayoutGenerateView.as_view(), name="payout-generate"),
    path("lead/", LeadPayoutListView.as_view(), name="payout-lead"),
    path("<int:pk>/finalize/", PayoutFinalizeView.as_view(), name="payout-finalize"),
]
