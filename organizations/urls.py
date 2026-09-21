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
    MyStudentEnrollmentListView,
    OrganizationCreateView,
    OrganizationDetailView,
    OrganizationMembershipDetailView,
    OrganizationMembershipListView,
    OrganizationInvitationListCreateView,
    OrganizationInvitationAcceptView,
    OrganizationInvitationPreviewView,
    OrganizationInvitationRegisterView,
    OrganizationInvitationResendView,
    OrganizationInvitationRevokeView,
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
        OrganizationMembershipListView.as_view(),
        name="membership-list",
    ),
    path(
        "<int:organization_pk>/memberships/<int:pk>/",
        OrganizationMembershipDetailView.as_view(),
        name="membership-detail",
    ),
    path(
        "<int:organization_pk>/invitations/",
        OrganizationInvitationListCreateView.as_view(),
        name="invitation-list",
    ),
    path(
        "<int:organization_pk>/invitations/preview/",
        OrganizationInvitationPreviewView.as_view(),
        name="invitation-preview",
    ),
    path(
        "<int:organization_pk>/invitations/accept/",
        OrganizationInvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path(
        "<int:organization_pk>/invitations/register/",
        OrganizationInvitationRegisterView.as_view(),
        name="invitation-register",
    ),
    path(
        "<int:organization_pk>/invitations/accept-and-register/",
        OrganizationInvitationRegisterView.as_view(),
        name="invitation-accept-and-register",
    ),
    path(
        "<int:organization_pk>/invitations/<int:pk>/resend/",
        OrganizationInvitationResendView.as_view(),
        name="invitation-resend",
    ),
    path(
        "<int:organization_pk>/invitations/<int:pk>/revoke/",
        OrganizationInvitationRevokeView.as_view(),
        name="invitation-revoke",
    ),
    path(
        "<int:organization_pk>/students/mine/",
        MyStudentEnrollmentListView.as_view(),
        name="student-mine-list",
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
