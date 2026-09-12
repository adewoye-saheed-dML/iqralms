"""Tenant permission classes for the notification API."""

from rest_framework.permissions import BasePermission

from organizations.models import OrganizationRole


class CanManageAcademyNotifications(BasePermission):
    """Owner and administrator permission for academy notification logs and deliveries.

    An ordinary member (teacher, student, parent) or non-member is denied.
    """

    message = "Only an organization owner or administrator can access academy notification history."

    def has_permission(self, request, view):
        membership = getattr(view, "caller_membership", None)
        if not membership:
            return False
        return membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
