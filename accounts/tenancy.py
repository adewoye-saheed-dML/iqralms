"""Where the accounts domain meets the tenant boundary.

SaaS Phase 2's central rule, in four functions:

.. code-block:: text

    organization-specific account access
            ==
    active OrganizationMembership  +  the account role the operation needs

Two ideas are deliberately kept apart here, because collapsing them is how a
multi-tenant system leaks. ``OrganizationMembership.role``
(``owner``/``admin``/``staff``/``teacher``) is *authority inside one academy*.
``User.role`` (``lead``/``sub``/``student``/``parent``) is *what kind of account
this is*, globally, and it already drives booking, pricing, assessment and payout
behaviour. So "is this an active parent of this academy" is the conjunction of the
two rather than one field — and neither half is ever read from a request body.

Note what the functions below do **not** ask: which organization role the
membership carries. Belonging to an academy is ``status == active``; the role says
what the member may *do* there, which the organization permission classes already
answer. That is why a lead teacher who founded their own academy (an ``owner``
membership) is still a teaching account here, and why a ``parent`` account is a
parent of the academy whatever authority row admitted them.

Nothing in this module re-implements "is this membership active". That question has
one answer in the codebase — ``organizations.models.active_membership()``, and the
``OrganizationMembership.objects.active()`` queryset it is built on — and
everything below goes through it. A second status check that drifted out of step
with the first is the failure this module exists to prevent.

**A ``ParentLink`` is not authorization.** The link is a global family fact: a
parent stays their child's parent when the child moves to another academy.
``children_in_organization()`` is what keeps that fact from reaching across the
boundary — it answers with the linked children who are active members *here*, so
an academy never sees a student it has not admitted.
"""

from organizations.models import OrganizationMembership, active_membership

from .models import TEACHER_ROLES, Role, User

#: Account roles that can be a student, and a parent, of an academy. One-value
#: sets rather than bare comparisons, so they read the same way as
#: ``models.TEACHER_ROLES`` and widening one is a single visible edit.
STUDENT_ROLES = frozenset({Role.STUDENT.value})
PARENT_ROLES = frozenset({Role.PARENT.value})


def _active_membership_for_account_roles(*, user, organization, roles):
    """The user's active membership here, but only if their account role is in ``roles``.

    The account role is checked first because it costs no query: an anonymous
    caller (no ``role`` attribute at all) and a student asked about as a teacher
    are both refused before the database is touched.
    """
    if getattr(user, "role", None) not in roles:
        return None
    return active_membership(user=user, organization=organization)


def active_teaching_membership(*, user, organization):
    """``user``'s membership here if they are an active member and a teaching account.

    A teaching account is ``lead`` or ``sub`` — ``models.TEACHER_ROLES``, the same
    set that decides who may hold a ``TeacherProfile``. An ``admin`` or ``staff``
    membership does not make anyone a teacher, and an ``owner`` membership does not
    either; conversely a lead teacher who founded their academy owns it *and*
    teaches in it, which is why the organization role is not part of this answer.
    """
    return _active_membership_for_account_roles(
        user=user, organization=organization, roles=TEACHER_ROLES
    )


def active_student_membership(*, user, organization):
    """``user``'s membership here if they are an active member and a student account."""
    return _active_membership_for_account_roles(
        user=user, organization=organization, roles=STUDENT_ROLES
    )


def active_parent_membership(*, user, organization):
    """``user``'s membership here if they are an active member and a parent account."""
    return _active_membership_for_account_roles(
        user=user, organization=organization, roles=PARENT_ROLES
    )


def children_in_organization(*, parent, organization):
    """``parent``'s linked students who are also active members of ``organization``.

    The Phase 2 tenant-isolation rule, as a queryset:

    .. code-block:: text

        parent active in organization  AND  student active in organization

    Both halves are required, so the answer is empty when the caller's own
    membership is missing or suspended, and a linked child the academy has not
    admitted (or has suspended) is absent from it. A parent in two academies gets
    two different lists from the same global set of ``ParentLink`` rows, which is
    the point.

    Accepts an ``Organization`` or a bare pk, because callers have the URL's id.
    """
    membership = active_parent_membership(user=parent, organization=organization)
    if membership is None:
        return User.objects.none()

    # Reusing .active() rather than re-filtering on status: "which memberships
    # grant access" is the organization app's definition to own, and a copy of it
    # here is the one that would eventually disagree.
    members_here = (
        OrganizationMembership.objects.active()
        .filter(organization=membership.organization)
        .values("user_id")
    )
    return (
        User.objects.filter(
            # ParentLink.clean() already refuses a non-student, but a role edited
            # afterwards would leave the row behind; a child is a student now.
            role=Role.STUDENT,
            parent_links__parent=parent,
            pk__in=members_here,
        )
        .distinct()
        .order_by("username")
    )
