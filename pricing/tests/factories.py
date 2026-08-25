"""factory_boy factories for the pricing app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Two things are derived rather than hardcoded, so a factory cannot build a
state the model rejects:

* ``approved_by`` is a lead teacher, because ``PricingAgreement.clean()`` refuses
  any other role. A test that wants that rejection passes ``approved_by=`` a
  sub-teacher explicitly.
* The rates are a real discount by default — ``agreed_rate`` below
  ``standard_rate`` — so the default factory exercises the interesting case
  rather than a no-op agreement. ``StandardRateAgreementFactory`` is the equal
  case, which the ``standard`` reason exists for.
"""

from decimal import Decimal

import factory

from accounts.tests.factories import LeadTeacherFactory, StudentFactory
from curriculum.tests.factories import LevelFactory
from pricing.models import PricingAgreement, PricingReason

#: A plausible per-session rate, in whatever currency the academy bills in — the
#: model stores a bare decimal, and currency is a payments-phase question.
STANDARD_RATE = Decimal("25.00")
HARDSHIP_RATE = Decimal("15.00")
PREMIUM_RATE = Decimal("40.00")


class PricingAgreementFactory(factory.django.DjangoModelFactory):
    """An active hardship discount, approved by a lead teacher."""

    class Meta:
        model = PricingAgreement

    student = factory.SubFactory(StudentFactory)
    level = factory.SubFactory(LevelFactory)
    standard_rate = STANDARD_RATE
    agreed_rate = HARDSHIP_RATE
    reason = PricingReason.DISCOUNT_HARDSHIP
    approved_by = factory.SubFactory(LeadTeacherFactory)
    notes = "Family asked for help after a job loss; revisit in six months."
    active = True


class PremiumAgreementFactory(PricingAgreementFactory):
    """A family paying above the standard rate to have the lead teach directly.

    The reason the phase's open question was about: the product owner's answer
    (2026-08-25) is that this buys nothing on the waitlist, so this factory is
    used by the tests that assert exactly that.
    """

    agreed_rate = PREMIUM_RATE
    reason = PricingReason.PREMIUM_DIRECT
    notes = "Wants the lead specifically; agreed a premium rather than a sub."


class StandardRateAgreementFactory(PricingAgreementFactory):
    """"We discussed it and agreed no change" — a recordable outcome, not an absence."""

    agreed_rate = STANDARD_RATE
    reason = PricingReason.STANDARD
    notes = "Asked about a discount; no change agreed."


class SiblingDiscountAgreementFactory(PricingAgreementFactory):
    agreed_rate = Decimal("20.00")
    reason = PricingReason.SIBLING_DISCOUNT
    notes = "Second child enrolled; standard sibling rate."
