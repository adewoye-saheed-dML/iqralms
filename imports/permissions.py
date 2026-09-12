from rest_framework.permissions import BasePermission
from organizations.models import OrganizationRole

class CanImportRecords(BasePermission):
    message = "Only organization owners, administrators, or staff may import records."

    def has_permission(self, request, view):
        membership = getattr(view, "caller_membership", None)
        if not membership:
            return False
        
        # OWNER, ADMIN, STAFF
        return membership.role in {
            OrganizationRole.OWNER,
            OrganizationRole.ADMIN,
            OrganizationRole.STAFF,
        }
