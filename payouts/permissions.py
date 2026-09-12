"""Tenant and role gates for the payout API.

Financial data, so the walls are the point. Two classes, and between them they
say the whole visibility rule for SaaS Phase 7: an academy owner or administrator
manages the academy's payouts, an active teacher reads their own, and a student
or parent has no payout access at all.

Every check inspects ``view.caller_membership``, which resolves active membership
inside the academy named by the URL (via ``organizations.views.OrganizationScopedMixin``).
Global role alone is never trusted — an owner/admin without active membership cannot
manage payouts, a teacher cannot view records in an academy where they do not hold an
active membership, and suspended memberships are refused outright.

Neither class decides *whose* records a caller sees. That is the view's job, and
it is always the same tenant-scoped filter: ``services.payouts_for(organization=..., teacher=...)``.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role
from organizations.models import OrganizationRole


class IsLeadTeacher(BasePermission):
    """Generation, finalization, academy-wide listings, anyone's statement.

    In SaaS Phase 7, managing payouts belongs to the academy's owner or administrator.
    Global Role.LEAD alone is not sufficient; the caller must hold an active membership
    with role OWNER or ADMIN in the requested academy.
    Students and parents are denied unconditionally.
    """

    message = "Only an organization owner or administrator can manage payouts."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "role", None) in {Role.STUDENT, Role.PARENT}:
            return False
        membership = getattr(view, "caller_membership", None)
        if membership is None:
            return False
        return membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}


class IsTeacher(BasePermission):
    """Reading one's own payout records and statements.

    The caller must be an authenticated teacher (Role.LEAD or Role.SUB) with an
    active membership in this academy.
    Students and parents are refused unconditionally.
    """

    message = "Only an active teacher of this organization has payouts of their own."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_teacher):
            return False
        if getattr(user, "role", None) in {Role.STUDENT, Role.PARENT}:
            return False
        membership = getattr(view, "caller_membership", None)
        if membership is None:
            return False
        return True
