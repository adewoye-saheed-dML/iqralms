"""Account URLs, mounted at /api/accounts/.

Two groups. The first three are global account routes, unchanged since Phase 1.

The rest are organization-scoped, and they establish the convention the later
tenancy phases should follow:

.. code-block:: text

    /api/<domain>/organizations/<organization_id>/<resource>/

The tenant is part of the address, which is what lets one mixin resolve it to the
caller's verified membership and one permission class ask "is this caller inside
*this* academy" (see views and organizations.views.OrganizationScopedMixin). Each
domain keeps its own routes rather than writing into organizations/urls.py, so
curriculum, scheduling, pricing, assessment and payout tenancy can each add theirs
without four apps editing one file.

Literal segments sit above id routes, the repository's usual ordering.
"""

from django.urls import path

from .views import (
    MeView,
    MyChildrenView,
    OrganizationChildrenView,
    OrganizationTeacherConfigurationDetailView,
    OrganizationTeacherConfigurationListCreateView,
    ParentLinkCreateView,
)

app_name = "accounts"

urlpatterns = [
    # Global account routes.
    path("me/", MeView.as_view(), name="me"),
    path("my-children/", MyChildrenView.as_view(), name="my-children"),
    path("parent-links/", ParentLinkCreateView.as_view(), name="parent-link-create"),
    # Organization-scoped account routes.
    path(
        "organizations/<int:organization_pk>/children/",
        OrganizationChildrenView.as_view(),
        name="organization-children",
    ),
    path(
        "organizations/<int:organization_pk>/teacher-configurations/",
        OrganizationTeacherConfigurationListCreateView.as_view(),
        name="organization-teacher-configuration-list",
    ),
    path(
        "organizations/<int:organization_pk>/teacher-configurations/<int:pk>/",
        OrganizationTeacherConfigurationDetailView.as_view(),
        name="organization-teacher-configuration-detail",
    ),
]
