"""Tenant gates for the organization API.

Phase 1's one access rule, in three small classes:

.. code-block:: text

    organization access  ==  active OrganizationMembership

Every class here reads the caller's membership from ``view.caller_membership``
(see ``views.OrganizationScopedMixin``), which resolves it from the organization
named in the *URL* and the authenticated user on the request. Nothing here trusts
an ``organization_id``, a ``role`` or a ``user`` from the request body: those are
inputs to be validated, not statements about who the caller is.

Deliberately absent: ``IsOrganizationAdmin`` and ``IsOrganizationOwner``. The
phase spec offers them as suggestions, and no Phase 1 endpoint is admin-only or
owner-only — owner and admin manage memberships alike, and the one thing only the
owner *has* is their own row, which ``OwnerMembershipIsProtected`` defends from
everyone including the owner. Adding two unused permission classes would be a
framework waiting for a requirement.

These classes say who may call an endpoint. They are not the only layer: the views
scope every queryset to the organization in the URL, and the serializers refuse
role values a caller may not assign. A membership from another tenant is a 404
from the queryset, never a 403 from here.
"""

from rest_framework.permissions import BasePermission

from .models import OrganizationRole


class IsOrganizationMember(BasePermission):
    """Reading an organization the caller actually belongs to.

    A non-member is refused, and so is a suspended member — ``active_membership()``
    does not distinguish between them, which is what keeps an organization from
    confirming its own existence to someone outside it. This is the gate that
    stops ``GET /api/organizations/123/`` from being a way to read a tenant's
    details by guessing its id.
    """

    message = "You are not an active member of this organization."

    def has_permission(self, request, view):
        return view.caller_membership is not None


class CanManageOrganizationMemberships(BasePermission):
    """Listing, creating and changing memberships: owner and admin only.

    A staff or teacher member is refused the whole membership surface in Phase 1,
    the list included. The spec is explicit that an ordinary member should not
    receive the academy's membership directory while the security model is still
    being introduced — a member directory is a product decision, and a narrower
    default is the one that can be widened safely later.
    """

    message = "Only an organization owner or administrator can manage memberships."

    def has_permission(self, request, view):
        membership = view.caller_membership
        return bool(membership and membership.can_manage_memberships)


class OwnerMembershipIsProtected(BasePermission):
    """The owner's own row is not editable through the membership endpoints.

    It refuses everyone — an admin, and the owner themselves. Suspending, demoting
    or reassigning the owner is an ownership transfer, which the spec names as a
    separate business operation requiring its own decision, and letting it happen
    as a side effect of a role edit is exactly how a tenant ends up with no owner
    or two.

    Object-level only, so it composes with ``CanManageOrganizationMemberships``:
    that class answers "may this caller manage memberships at all", this one
    answers "may this particular row be changed".
    """

    message = (
        "The organization owner's membership cannot be changed here. "
        "Transferring ownership is a separate operation."
    )

    def has_object_permission(self, request, view, obj):
        return obj.role != OrganizationRole.OWNER


def get_caller_membership(view, user):
    """Resolve the active membership for a user in the view's organization."""
    if not (user and user.is_authenticated):
        return None
    if hasattr(view, "caller_membership"):
        return view.caller_membership
    from .models import active_membership
    org_kwarg = getattr(view, "organization_url_kwarg", "organization_pk")
    org_id = (
        view.kwargs.get(org_kwarg)
        or view.kwargs.get("organization_pk")
        or view.kwargs.get("pk")
    )
    if not org_id:
        return None
    return active_membership(user=user, organization=org_id)


def has_active_membership(view, user) -> bool:
    return get_caller_membership(view, user) is not None


def is_owner_or_admin(view, user) -> bool:
    m = get_caller_membership(view, user)
    return bool(m and m.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN})


def is_lead_teacher(view, user) -> bool:
    m = get_caller_membership(view, user)
    from accounts.models import Role
    return bool(
        m
        and m.role == OrganizationRole.TEACHER
        and getattr(user, "role", None) == Role.LEAD
    )


def is_teacher(view, user) -> bool:
    m = get_caller_membership(view, user)
    return bool(m and m.role == OrganizationRole.TEACHER)


def is_owner_admin_or_lead_teacher(view, user) -> bool:
    return is_owner_or_admin(view, user) or is_lead_teacher(view, user)


class IsOwnerOrAdmin(BasePermission):
    """Owner or admin in the active organization."""

    message = "Only an organization owner or administrator can perform this action."

    def has_permission(self, request, view):
        return is_owner_or_admin(view, request.user)


class IsLeadTeacherMember(BasePermission):
    """Lead teacher (teacher organization membership + global Role.LEAD)."""

    message = "Only a lead teacher can perform this action."

    def has_permission(self, request, view):
        return is_lead_teacher(view, request.user)


class IsOwnerAdminOrLeadTeacher(BasePermission):
    """Owner, admin, or lead teacher in this organization."""

    message = (
        "Only an organization owner, administrator, or lead teacher can perform this action."
    )

    def has_permission(self, request, view):
        return is_owner_admin_or_lead_teacher(view, request.user)


class IsTeacherMember(BasePermission):
    """Active teacher in this organization."""

    message = "Only an active teacher of this organization can perform this action."

    def has_permission(self, request, view):
        return is_teacher(view, request.user)

