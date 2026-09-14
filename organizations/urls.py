"""Organization URLs, mounted at /api/organizations/.

Literal paths above the id route, the repository's usual ordering, so ``mine/``
can never be read as an organization id. The membership routes are nested under
the organization on purpose: the tenant is part of the address, which is what lets
one permission class ask "is the caller inside *this* academy" for every request
that touches its members.

The surface matches the phase spec's, with one addition it asks for in prose but
omits from its endpoint list — a partial update on a membership, without which
"owner and admin may suspend, reactivate and re-role members" cannot be performed
at all (see views.OrganizationMembershipDetailView).
"""

from django.urls import path

from .views import (
    MyOrganizationListView,
    OrganizationCreateView,
    OrganizationDetailView,
    OrganizationMembershipDetailView,
    OrganizationMembershipListCreateView,
    StudentEnrollmentListCreateView,
    StudentEnrollmentDetailView,
)

app_name = "organizations"

urlpatterns = [
    path("", OrganizationCreateView.as_view(), name="organization-create"),
    path("mine/", MyOrganizationListView.as_view(), name="organization-mine"),
    path("<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path(
        "<int:organization_pk>/memberships/",
        OrganizationMembershipListCreateView.as_view(),
        name="membership-list",
    ),
    path(
        "<int:organization_pk>/memberships/<int:pk>/",
        OrganizationMembershipDetailView.as_view(),
        name="membership-detail",
    ),
    path(
        "<int:organization_pk>/students/",
        StudentEnrollmentListCreateView.as_view(),
        name="student-list",
    ),
    path(
        "<int:organization_pk>/students/<int:pk>/",
        StudentEnrollmentDetailView.as_view(),
        name="student-detail",
    ),
]
