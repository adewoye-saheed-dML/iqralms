"""Cohort seat-cap enforcement, for the paths ``add_student()`` cannot cover.

``Cohort.students`` is a many-to-many, and the spec asks for ``max_students`` to
be a real cap rather than a serializer-layer courtesy. Two reasons that cannot
live in ``clean()`` like every other rule in this app:

* An M2M is written *after* the row is saved, so ``full_clean()`` on a new cohort
  runs before any student is attached and has nothing to count.
* ``cohort.students.add(...)`` never touches ``save()`` or ``clean()`` at all. It
  is a write straight to the join table.

So the cap is enforced where the write actually happens. ``Cohort.add_student()``
is the supported path and raises a clear ``CohortFull``; this receiver is the
backstop that catches a raw ``.add()`` — including the reverse direction,
``user.cohort_memberships.add(cohort)`` — so a fixture or a shell session cannot
overfill a class either. Same reasoning as ``curriculum/signals.py``: the rule
should hold for direct ORM writes, not only for the API.
"""

from django.core.exceptions import ValidationError
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from accounts.models import Role

from .models import Cohort


def _refuse_overfilling(cohort, joining_pks):
    """Raise if seating ``joining_pks`` would break ``cohort``'s rules.

    Members already seated are not counted again — an M2M is a set, so re-adding
    one is a no-op and must not read as a request for another seat.
    """
    seated = set(cohort.students.values_list("pk", flat=True))
    joining = set(joining_pks) - seated
    if not joining:
        return

    non_students = (
        cohort.students.model.objects.filter(pk__in=joining)
        .exclude(role=Role.STUDENT)
        .values_list("username", "role")
    )
    if non_students:
        raise ValidationError(
            "Only a user with role 'student' can join a cohort: "
            + ", ".join(f"{username} is '{role}'" for username, role in non_students),
            code="invalid_student_role",
        )

    if len(seated) + len(joining) > cohort.max_students:
        raise ValidationError(
            f"{cohort.level} cohort holds {cohort.max_students} students; "
            f"{len(seated)} seated and {len(joining)} more would overfill it.",
            code="cohort_full",
        )


@receiver(
    m2m_changed,
    sender=Cohort.students.through,
    dispatch_uid="scheduling.enforce_cohort_seat_cap",
)
def enforce_cohort_seat_cap(sender, instance, action, reverse, pk_set, **kwargs):
    """Refuse an ``add`` that would exceed a cohort's seats, before it lands.

    ``pre_add`` rather than ``post_add`` so nothing is written and then undone.
    ``pk_set`` is None for ``clear``, which is why only ``pre_add`` is handled at
    all — removing students never overfills anything.
    """
    if action != "pre_add" or not pk_set:
        return

    if reverse:
        # instance is the User; pk_set holds cohort pks.
        for cohort in Cohort.objects.filter(pk__in=pk_set):
            _refuse_overfilling(cohort, {instance.pk})
    else:
        _refuse_overfilling(instance, pk_set)
