"""Negotiated pricing: what a family actually pays, and why it differs.

Field sets mirror specs/phase-5-pricing-waitlist.md exactly. The principle behind
the phase (mvp-spec section 3) is that **exceptions are tracked, not silent** — a
family paying less than the standard rate is a decision with a reason, an
approver and a note attached, rather than something that lives in the lead
teacher's memory or a WhatsApp thread.

Three things this app deliberately does *not* do:

* **It charges nobody.** ``PricingAgreement`` is a lookup table recording what
  rate *should* apply. Nothing here takes a payment, and ``Booking`` gains no
  price field — a future payments phase reads these rows, this phase only
  produces them.
* **It says nothing about sub-teacher pay.** A family's discount is absorbed by
  the lead's margin, never by the teacher's income (mvp-spec section 3), so a
  sub-teacher's ``hourly_payout_rate`` is independent of anything here and must
  stay that way.
* **It does not feed the waitlist.** Whether a ``premium_direct`` agreement buys
  faster access to the lead specifically was the spec's open question; the
  product owner's answer (2026-08-25) is **no** — ``TeacherWaitlist.priority``
  stays a manually set field with no automatic inputs. So these two models are
  neighbours in one phase, not coupled.

Why an agreement is a row of its own rather than fields on ``Booking`` or
``User``: pricing is per student *per level* (a family can be on a hardship rate
for Qaida and the standard rate for Hifz), it is superseded rather than edited,
and the history is worth keeping. Same reasoning the spec gives.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q

from accounts.models import Role, User
from curriculum.models import Level
from organizations.models import active_membership

#: Rates are money, so nothing below zero is meaningful. Zero *is* — a full
#: scholarship is a legitimate hardship arrangement.
MINIMUM_RATE = Decimal("0")


class PricingAgreementQuerySet(models.QuerySet):
    """QuerySet for PricingAgreement with multi-tenant filtering."""

    def in_organization(self, organization):
        """Agreements that belong to one academy through their curriculum level."""
        organization_id = getattr(organization, "pk", organization)
        return self.filter(level__track__organization_id=organization_id)



class PricingReason(models.TextChoices):
    """Why this rate differs from the standard one — or that it doesn't.

    The four the spec lists, no more. ``standard`` exists so that "we discussed
    it and agreed no change" is a recordable outcome rather than an absent row:
    an agreement whose ``agreed_rate`` equals its ``standard_rate`` still says
    somebody asked and somebody answered.
    """

    DISCOUNT_HARDSHIP = "discount_hardship", "Discount — financial hardship"
    PREMIUM_DIRECT = "premium_direct", "Premium — chose the lead teacher directly"
    SIBLING_DISCOUNT = "sibling_discount", "Sibling discount"
    STANDARD = "standard", "Standard — no negotiation"


class PricingAgreement(models.Model):
    """One student's agreed rate for one level, and the reasoning behind it.

    At most one row per student and level is ``active``. Creating a second one
    **supersedes** the first (``active=False``) rather than deleting it: this is a
    financial record, so the history is the point. That is a deliberate departure
    from the keep-current-only call made for placement audio in Phase 2 — the
    spec re-decides it here rather than inheriting it, and this phase's answer is
    keep everything.
    """

    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="pricing_agreements",
        help_text="Role 'student'. Pricing is per student, not per family.",
    )
    level = models.ForeignKey(
        # PROTECT, like every other reference to a Level: an agreement is a
        # record of something that was agreed, so removing the level it was
        # agreed for has to be a deliberate act rather than a cascade.
        Level,
        on_delete=models.PROTECT,
        related_name="pricing_agreements",
        help_text="A student can hold a different arrangement per level.",
    )
    standard_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(MINIMUM_RATE)],
        help_text=(
            "What this level would normally cost, snapshotted when the agreement "
            "was made. Deliberately stored rather than derived, so changing the "
            "standard rate later cannot silently reprice an existing agreement."
        ),
    )
    agreed_rate = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(MINIMUM_RATE)],
        help_text="What was actually negotiated. May equal standard_rate.",
    )
    reason = models.CharField(max_length=20, choices=PricingReason.choices)
    approved_by = models.ForeignKey(
        # PROTECT rather than SET_NULL (which PlacementResult.reviewed_by uses):
        # a placement can legitimately have no reviewer — a beginner skip is a
        # system decision — but a rate is never agreed by nobody. "Who approved
        # this" is half of what makes the record auditable six months later.
        User,
        on_delete=models.PROTECT,
        related_name="pricing_approvals",
        help_text="The lead teacher. Nobody else may approve a rate.",
    )
    notes = models.TextField(
        blank=True,
        help_text=(
            "Private: what was actually agreed and why. Never exposed to the "
            "student or their parent — see serializers.py."
        ),
    )
    active = models.BooleanField(
        default=True,
        help_text=(
            "False once a later agreement for the same student and level "
            "supersedes this one. Superseded rows are kept, never deleted."
        ),
    )

    objects = PricingAgreementQuerySet.as_manager()

    class Meta:
        # Newest first, which is what a pricing history reads as. There is no
        # created_at — the spec's field list does not include one and this phase
        # does not add fields it was not asked for (tech-debt.md) — so insertion
        # order comes from the primary key.
        ordering = ["-pk"]
        constraints = [
            # DB backstop for the one-active-agreement rule. save() supersedes
            # the previous row first, so this should never fire through any
            # supported path; it is here so a hand-written INSERT or a fixture
            # load cannot leave two live rates for one student and level.
            models.UniqueConstraint(
                fields=["student", "level"],
                condition=Q(active=True),
                name="unique_active_pricing_per_student_level",
                violation_error_message=(
                    "That student already has an active pricing agreement for "
                    "this level."
                ),
            )
        ]

    # --- Behaviour ----------------------------------------------------------

    @property
    def organization(self):
        """The academy this agreement belongs to, reached through its level's track.

        A property rather than a column, deliberately. The Phase 5 audit proved
        that derived ownership through level -> track -> organization is
        sufficient and avoids redundant tenant fields.
        """
        if self.level_id:
            return self.level.track.organization
        return None

    def _is_active_here(self, user):
        """Is ``user`` an active member/participant of the academy that owns this level?

        Goes through ``organizations.active_membership()`` or ``is_active_student_participant()``
        rather than querying memberships directly.
        """
        if not self.organization or not user:
            return False
        if getattr(user, "role", None) == Role.STUDENT:
            from accounts.tenancy import is_active_student_participant

            return is_active_student_participant(
                user=user, organization=self.organization
            )
        return (
            active_membership(user=user, organization=self.organization)
            is not None
        )

    @classmethod
    def active_for(cls, student, level):
        """The rate currently in force for ``student`` at ``level``, or None.

        The single supported way to ask "what does this family pay". A future
        payments phase reads this rather than assembling its own filter, for the
        same reason capacity has one ``weekly_committed_minutes``: two callers
        with two definitions of "current" will eventually disagree.
        """
        return cls.objects.filter(
            student=getattr(student, "pk", student),
            level=getattr(level, "pk", level),
            active=True,
        ).first()

    @property
    def is_discount(self) -> bool:
        """Whether the family pays less than the standard rate."""
        return self.agreed_rate < self.standard_rate

    @property
    def is_premium(self) -> bool:
        """Whether the family pays more than the standard rate."""
        return self.agreed_rate > self.standard_rate

    def supersede_active(self):
        """Deactivate any live agreement this one replaces. Returns the count.

        A queryset ``update()`` on purpose: it flips one boolean on rows that are
        otherwise untouched, and routing them through ``save()`` would re-run
        ``full_clean()`` on historical records for no gain (see tech-debt.md on
        ``full_clean()`` inside ``save()``).
        """
        return (
            type(self)
            .objects.filter(student_id=self.student_id, level_id=self.level_id, active=True)
            .exclude(pk=self.pk)
            .update(active=False)
        )

    # --- Validation ---------------------------------------------------------

    def clean(self):
        errors = {}

        if self.student_id:
            if self.student.role != Role.STUDENT:
                errors["student"] = ValidationError(
                    "Only a user with role 'student' can have a pricing agreement "
                    "(got '%(role)s').",
                    code="invalid_student_role",
                    params={"role": self.student.role},
                )
            elif self.level_id and self.organization is not None and not self._is_active_here(self.student):
                errors["student"] = ValidationError(
                    "That student is not an active member of the academy that "
                    "owns this level.",
                    code="student_not_in_organization",
                )

        if self.approved_by_id:
            approver = self.approved_by
            if self.level_id and self.organization is not None:
                if not self._is_active_here(approver):
                    errors["approved_by"] = ValidationError(
                        "That approver is not an active member of the academy that owns this level.",
                        code="approver_not_in_organization",
                    )
                else:
                    membership = active_membership(user=approver, organization=self.organization)
                    from organizations.models import OrganizationRole

                    is_manager = membership and (
                        membership.role in {OrganizationRole.OWNER, OrganizationRole.ADMIN}
                        or (membership.role == OrganizationRole.TEACHER and approver.role == Role.LEAD)
                    )
                    if not is_manager:
                        errors["approved_by"] = ValidationError(
                            "Only an organization owner, administrator, or lead teacher can approve a pricing agreement.",
                            code="invalid_approver_role",
                        )
            else:
                if approver.role != Role.LEAD:
                    errors["approved_by"] = ValidationError(
                        "Only the lead teacher can approve a pricing agreement (got '%(role)s').",
                        code="invalid_approver_role",
                        params={"role": approver.role},
                    )

        if errors:
            raise ValidationError(errors)


    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self._state.adding and self.active and self.student_id and self.level_id:
                # Supersede *before* validating: full_clean() checks the
                # partial unique constraint above, so the old row has to be
                # deactivated first or creating the replacement would be
                # refused by the very rule it satisfies.
                self.supersede_active()
            # The role rules read another table, so they cannot be DB
            # constraints; validating in save() makes them hold for the admin
            # and direct ORM writes as well as the API — the same pattern as
            # ParentLink, Level and Booking.
            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        state = "active" if self.active else "superseded"
        return (
            f"{self.student.username} / {self.level}: {self.agreed_rate} "
            f"(standard {self.standard_rate}, {self.reason}, {state})"
        )
