"""Role gates for the scheduling API.

Booking is the first thing a *parent* acts through rather than only reads, so
the "who may book" gate is wider than Phase 2's student-only placement gate: a
parent books on behalf of a linked child. Which child is still checked per
object, in the serializer — this only says the caller is the right kind of user.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


class IsStudent(BasePermission):
    message = "Only a student account has bookings of its own."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role == Role.STUDENT:
            return True
        membership = getattr(view, "caller_membership", None)
        if membership:
            from organizations.models import OrganizationRole
            return membership.role == OrganizationRole.STUDENT
        return False


class IsTeacher(BasePermission):
    message = "Only a teacher account teaches sessions."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_teacher", False):
            return True
        from organizations.permissions import is_teacher
        return is_teacher(view, user)


class IsStudentOrParent(BasePermission):
    message = "Only a student or a parent of a student can access these sessions."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role in {Role.STUDENT, Role.PARENT}:
            return True
        membership = getattr(view, "caller_membership", None)
        if membership:
            from organizations.models import OrganizationRole
            return membership.role in {OrganizationRole.STUDENT, OrganizationRole.PARENT}
        return False


class IsLeadTeacher(BasePermission):
    """Owner, admin, or lead teacher in this organization."""

    message = (
        "Only an organization owner, administrator, or lead teacher can manage this."
    )

    def has_permission(self, request, view):
        from organizations.permissions import is_owner_admin_or_lead_teacher

        return is_owner_admin_or_lead_teacher(view, request.user)


class IsOwnerAdminOrLeadTeacher(IsLeadTeacher):
    pass


class CanBookSession(BasePermission):
    message = "Only a student, parent, teacher, or organization staff can book a session."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.role in {Role.STUDENT, Role.PARENT} or getattr(user, "is_teacher", False):
            return True
        from organizations.permissions import is_owner_admin_or_lead_teacher

        return is_owner_admin_or_lead_teacher(view, user)


class CanManageAvailability(BasePermission):
    message = "Only the teacher can declare or manage their own availability hours."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_teacher", False):
            return True
        from organizations.permissions import is_teacher

        return is_teacher(view, user)


