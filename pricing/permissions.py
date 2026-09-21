"""Role gates for the pricing API.

Pricing is the lead teacher's alone. mvp-spec section 3 is explicit about why:
it is the one lever that affects the lead's own margin directly, so it is not
something a well-meaning sub-teacher should be able to grant. The rule is
enforced twice over — here, so the API answers 403, and in
``PricingAgreement.clean()``, so a direct ORM write cannot bypass it.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


from organizations.permissions import is_owner_admin_or_lead_teacher


class IsLeadTeacher(BasePermission):
    """Pricing agreement management: Owner, Admin, or Lead Teacher.

    Enforces active membership in the organization, allowing owners, admins,
    and lead teachers (teacher membership + global Role.LEAD).
    """

    message = "Only an organization owner, administrator, or lead teacher can manage pricing agreements."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return is_owner_admin_or_lead_teacher(view, user)


PricingManager = IsLeadTeacher


class IsStudent(BasePermission):
    """For ``/agreements/mine/`` — a student reads their own rate, nobody else's.

    Parents are excluded for now, which is narrower than booking (where a parent
    acts for a linked child). The spec's surface says "student sees their own
    active agreement", and widening it is a decision about who in a family may
    see money, not boilerplate — see tech-debt.md.
    """

    message = "Only a student account has pricing agreements of its own."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.STUDENT)
