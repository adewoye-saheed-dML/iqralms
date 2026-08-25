"""Model-layer tests for ``PricingAgreement``.

Acceptance criterion 3 lives here in its truest form — "both rows still exist,
queryable" is a database claim, so it is asserted against the database. The API
layer proves the same rule over HTTP in test_api.py; both matter for the reason
Phase 1 set out, that the rules live in ``clean()``/``save()`` so they hold for
direct ORM writes and not only for the endpoint.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    StudentFactory,
    SubTeacherFactory,
)
from curriculum.tests.factories import LevelFactory, TrackFactory
from pricing.models import PricingAgreement, PricingReason

from .factories import (
    HARDSHIP_RATE,
    STANDARD_RATE,
    PremiumAgreementFactory,
    PricingAgreementFactory,
    StandardRateAgreementFactory,
)


class PricingAgreementCreationTests(TestCase):
    """CLAUDE.md's "at least one test per model (creation)"."""

    def test_an_agreement_is_created_active(self):
        agreement = PricingAgreementFactory()

        self.assertTrue(agreement.pk)
        self.assertTrue(agreement.active)
        self.assertEqual(agreement.standard_rate, STANDARD_RATE)
        self.assertEqual(agreement.agreed_rate, HARDSHIP_RATE)
        self.assertEqual(agreement.reason, PricingReason.DISCOUNT_HARDSHIP)

    def test_the_standard_rate_is_a_snapshot_not_a_live_lookup(self):
        """The spec's reason for storing it: later changes must not reprice.

        There is nothing in this phase that *could* reprice an agreement — the
        standard rate lives nowhere else yet — which is precisely why this is
        worth pinning now: a future payments phase that introduces a rate card
        must not start deriving this field.
        """
        agreement = PricingAgreementFactory()
        stored = PricingAgreement.objects.get(pk=agreement.pk)

        self.assertEqual(stored.standard_rate, STANDARD_RATE)
        self.assertNotEqual(stored.standard_rate, stored.agreed_rate)

    def test_a_discount_and_a_premium_are_distinguishable(self):
        discount = PricingAgreementFactory()
        premium = PremiumAgreementFactory()
        standard = StandardRateAgreementFactory()

        self.assertTrue(discount.is_discount)
        self.assertFalse(discount.is_premium)
        self.assertTrue(premium.is_premium)
        self.assertFalse(premium.is_discount)
        self.assertFalse(standard.is_discount)
        self.assertFalse(standard.is_premium)

    def test_a_zero_rate_is_allowed(self):
        """A full scholarship is a legitimate hardship arrangement."""
        agreement = PricingAgreementFactory(agreed_rate=Decimal("0.00"))
        self.assertEqual(agreement.agreed_rate, Decimal("0.00"))

    def test_a_negative_rate_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            PricingAgreementFactory(agreed_rate=Decimal("-5.00"))
        self.assertIn("agreed_rate", ctx.exception.error_dict)

    def test_a_student_can_hold_different_rates_for_different_levels(self):
        """Pricing is per level, which is the reason ``level`` is on the row."""
        student = StudentFactory()
        qaida = LevelFactory(track=TrackFactory(name="Qaida", slug="qaida"))
        hifz = LevelFactory(track=TrackFactory(name="Hifz", slug="hifz"))

        cheap = PricingAgreementFactory(student=student, level=qaida)
        full = StandardRateAgreementFactory(student=student, level=hifz)

        self.assertTrue(cheap.active)
        self.assertTrue(full.active)
        self.assertEqual(
            PricingAgreement.objects.filter(student=student, active=True).count(), 2
        )

    def test_the_string_form_names_the_rate_and_its_state(self):
        agreement = PricingAgreementFactory()
        self.assertIn(agreement.student.username, str(agreement))
        self.assertIn("active", str(agreement))
        self.assertIn(str(HARDSHIP_RATE), str(agreement))


class SupersedeTests(TestCase):
    """Acceptance criterion 3 — a second agreement deactivates, never deletes."""

    def setUp(self):
        self.student = StudentFactory()
        self.level = LevelFactory()

    def test_a_second_agreement_deactivates_the_first(self):
        """Acceptance criterion 3, at the model layer."""
        first = PricingAgreementFactory(student=self.student, level=self.level)
        second = PremiumAgreementFactory(student=self.student, level=self.level)

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertFalse(first.active, "the old agreement must be deactivated")
        self.assertTrue(second.active, "the new agreement is the live one")

    def test_the_superseded_row_still_exists_and_is_queryable(self):
        """The other half of criterion 3: history is kept, not deleted."""
        first = PricingAgreementFactory(student=self.student, level=self.level)
        PremiumAgreementFactory(student=self.student, level=self.level)

        self.assertEqual(
            PricingAgreement.objects.filter(
                student=self.student, level=self.level
            ).count(),
            2,
            "both rows must survive — this is a financial record",
        )
        # And the old one is readable in full, notes included: the reasoning
        # behind a past rate is the thing worth keeping.
        stored = PricingAgreement.objects.get(pk=first.pk)
        self.assertEqual(stored.agreed_rate, HARDSHIP_RATE)
        self.assertTrue(stored.notes)

    def test_at_most_one_agreement_is_active_per_student_and_level(self):
        for _ in range(4):
            PricingAgreementFactory(student=self.student, level=self.level)

        self.assertEqual(
            PricingAgreement.objects.filter(
                student=self.student, level=self.level, active=True
            ).count(),
            1,
        )
        self.assertEqual(
            PricingAgreement.objects.filter(
                student=self.student, level=self.level
            ).count(),
            4,
        )

    def test_superseding_is_scoped_to_the_same_student_and_level(self):
        """Another family's rate, and another level's, are untouched."""
        other_student = PricingAgreementFactory(level=self.level)
        other_level = PricingAgreementFactory(student=self.student)

        PremiumAgreementFactory(student=self.student, level=self.level)

        other_student.refresh_from_db()
        other_level.refresh_from_db()
        self.assertTrue(other_student.active)
        self.assertTrue(other_level.active)

    def test_active_for_returns_the_live_agreement(self):
        PricingAgreementFactory(student=self.student, level=self.level)
        latest = PremiumAgreementFactory(student=self.student, level=self.level)

        self.assertEqual(
            PricingAgreement.active_for(self.student, self.level), latest
        )

    def test_active_for_is_none_when_nothing_was_agreed(self):
        self.assertIsNone(PricingAgreement.active_for(self.student, self.level))

    def test_active_for_accepts_ids_as_well_as_instances(self):
        """A future payments phase may hold ids rather than objects."""
        agreement = PricingAgreementFactory(student=self.student, level=self.level)
        self.assertEqual(
            PricingAgreement.active_for(self.student.pk, self.level.pk), agreement
        )

    def test_the_history_reads_newest_first(self):
        first = PricingAgreementFactory(student=self.student, level=self.level)
        second = PremiumAgreementFactory(student=self.student, level=self.level)

        self.assertEqual(
            list(
                PricingAgreement.objects.filter(
                    student=self.student, level=self.level
                )
            ),
            [second, first],
        )

    def test_a_hand_written_second_active_row_is_refused_by_the_database(self):
        """The partial unique constraint, proved to be more than decoration.

        ``save()`` supersedes first, so no supported path can reach this — which
        is exactly why it is worth proving the backstop exists. ``bulk_create``
        skips ``save()`` entirely, so it is the way in.

        Wrapped in ``atomic()`` per learnings.md: an ``IntegrityError`` inside a
        ``TestCase`` breaks the surrounding transaction otherwise.
        """
        PricingAgreementFactory(student=self.student, level=self.level)
        lead = LeadTeacherFactory()

        with self.assertRaises(IntegrityError), transaction.atomic():
            PricingAgreement.objects.bulk_create(
                [
                    PricingAgreement(
                        student=self.student,
                        level=self.level,
                        standard_rate=STANDARD_RATE,
                        agreed_rate=STANDARD_RATE,
                        reason=PricingReason.STANDARD,
                        approved_by=lead,
                        active=True,
                    )
                ]
            )

    def test_two_superseded_rows_may_coexist(self):
        """The constraint is scoped to ``active=True``, not to the pair itself."""
        PricingAgreementFactory(student=self.student, level=self.level)
        PricingAgreementFactory(student=self.student, level=self.level)
        PricingAgreementFactory(student=self.student, level=self.level)

        self.assertEqual(
            PricingAgreement.objects.filter(
                student=self.student, level=self.level, active=False
            ).count(),
            2,
        )


class ApprovalGateTests(TestCase):
    """Only the lead may approve a rate — the ``clean()`` half of the rule.

    The permission-class half is asserted over HTTP in test_api.py (acceptance
    criterion 2). This is the half that holds against a direct ORM write, which is
    the same belt-and-braces pattern ``PlacementResult.reviewed_by`` uses and the
    spec asks for by name.
    """

    def test_a_sub_teacher_cannot_approve_an_agreement(self):
        with self.assertRaises(ValidationError) as ctx:
            PricingAgreementFactory(approved_by=SubTeacherFactory())

        self.assertIn("approved_by", ctx.exception.error_dict)
        self.assertEqual(
            ctx.exception.error_dict["approved_by"][0].code, "invalid_approver_role"
        )
        self.assertFalse(PricingAgreement.objects.exists())

    def test_a_student_or_parent_cannot_approve_an_agreement(self):
        for approver in (StudentFactory(), ParentFactory()):
            with self.subTest(role=approver.role):
                with self.assertRaises(ValidationError) as ctx:
                    PricingAgreementFactory(approved_by=approver)
                self.assertIn("approved_by", ctx.exception.error_dict)

    def test_the_lead_can_approve_an_agreement(self):
        """The other half: the rule discriminates rather than refusing everyone."""
        agreement = PricingAgreementFactory(approved_by=LeadTeacherFactory())
        self.assertTrue(agreement.pk)

    def test_an_agreement_needs_an_approver(self):
        """``approved_by`` is not nullable: a rate is never agreed by nobody."""
        with self.assertRaises(ValidationError) as ctx:
            PricingAgreementFactory(approved_by=None)
        self.assertIn("approved_by", ctx.exception.error_dict)


class PricingAgreementRoleTests(TestCase):
    """Only a student can hold an agreement — pricing is per student."""

    def test_a_non_student_cannot_hold_an_agreement(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                with self.assertRaises(ValidationError) as ctx:
                    PricingAgreementFactory(student=user)
                self.assertIn("student", ctx.exception.error_dict)
                self.assertEqual(
                    ctx.exception.error_dict["student"][0].code,
                    "invalid_student_role",
                )

    def test_a_parent_does_not_hold_the_agreement_their_child_does(self):
        """Worth pinning: a family's rate hangs off the student being taught.

        A parent with two children at different levels would otherwise have an
        ambiguous rate, and the spec's field list says ``student`` for that reason.
        """
        parent = ParentFactory()
        with self.assertRaises(ValidationError):
            PricingAgreementFactory(student=parent)


class PricingIsSeparateFromPayoutsTests(TestCase):
    """mvp-spec section 3: a family's discount must never touch teacher pay.

    "This distinction matters and should never blur", in the spec's words. There
    is no payout code yet, so what can be asserted now is the structural fact that
    makes the blur impossible: an agreement carries no teacher at all, so nothing
    reading it could reduce anybody's rate. A future payouts phase that adds a
    teacher to this model would have to change this test on purpose.
    """

    def test_an_agreement_names_no_teacher(self):
        field_names = {field.name for field in PricingAgreement._meta.get_fields()}
        self.assertNotIn("teacher", field_names)
        self.assertNotIn("payout_rate", field_names)

    def test_a_discount_leaves_the_teachers_payout_rate_untouched(self):
        sub = SubTeacherFactory()
        from accounts.tests.factories import TeacherProfileFactory

        profile = TeacherProfileFactory(user=sub, approved=True)
        before = profile.hourly_payout_rate

        PricingAgreementFactory(agreed_rate=Decimal("1.00"))

        profile.refresh_from_db()
        self.assertEqual(profile.hourly_payout_rate, before)


class ThisPhaseChargesNobodyTests(TestCase):
    """The spec's "this phase does not touch payments", asserted rather than trusted."""

    def test_booking_gains_no_price_field(self):
        """The spec: "``Booking`` gets no new price field"."""
        from scheduling.models import Booking

        field_names = {field.name for field in Booking._meta.get_fields()}
        for forbidden in ("price", "rate", "agreed_rate", "pricing_agreement", "amount"):
            self.assertNotIn(forbidden, field_names)

    def test_an_agreement_is_not_attached_to_any_session(self):
        """A rate is a lookup for later, not a line on a booking.

        Phrased as a relation check rather than a field check because the risk is
        the reverse accessor: a ``PricingAgreement.booking`` FK would show up on
        ``Booking`` as a related name, not as a field.
        """
        related = {
            rel.get_accessor_name()
            for rel in PricingAgreement._meta.related_objects
        }
        self.assertEqual(related, set(), "nothing should point at an agreement yet")
