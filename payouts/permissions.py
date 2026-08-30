"""Role gates for the payout API.

Financial data, so the walls are the point. Two classes, and between them they
say the whole visibility rule from specs/phase-8-payouts.md: the lead manages the
academy's payouts, a teacher reads their own, and a student or parent has no
payout access at all — which is what falls out of ``IsTeacher`` refusing them.

Neither class decides *whose* records a caller sees. That is the view's job, and
it is always the same filter: ``services.payouts_for(teacher=request.user)``. A
permission class that returned True for the wrong teacher's row would be one
mistake away from exposing another person's income, so the scoping happens in the
queryset rather than in an object-level check.

Local to this app rather than imported from ``pricing`` or ``assessment``,
following the precedent those two set: the test is ``role == "lead"`` in all
three, but they answer different questions — "who sets what a family pays", "who
inspects teaching quality", "who decides what a teacher is owed" — and widening
one must not silently widen the others.
"""

from rest_framework.permissions import BasePermission

from accounts.models import Role


class IsLeadTeacher(BasePermission):
    """Generation, finalization, academy-wide listings, anyone's statement.

    All four are the lead's alone. A sub-teacher who could generate or finalize
    would be deciding their own pay, and a sub-teacher who could list
    academy-wide payouts would be reading their colleagues' income — the specific
    thing the phase spec rules out.
    """

    message = "Only the lead teacher can manage payouts."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == Role.LEAD)


class IsTeacher(BasePermission):
    """Reading one's own payout records and statements.

    Role only: which records those are is the queryset's decision, never this
    class's. Students and parents are refused here, which is the entirety of
    "families have no payout access" — there is no family-facing payout endpoint
    for them to be scoped out of.

    The lead passes this gate too, because the lead is a teacher. They will see
    an empty list, which is the honest answer: the lead is not paid per hour, so
    no payout record is ever generated for them (see ``services.applicable_rate``).
    """

    message = "Only a teacher account has payouts of its own."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_teacher)
