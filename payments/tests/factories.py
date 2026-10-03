"""factories for payments app."""

from decimal import Decimal
import factory

from accounts.tests.factories import StudentFactory
from payments.models import FamilyPayment, FamilyPaymentStatus
from pricing.tests.factories import PricingAgreementFactory


class FamilyPaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FamilyPayment

    pricing_agreement = factory.SubFactory(PricingAgreementFactory)
    student = factory.LazyAttribute(lambda o: o.pricing_agreement.student)
    organization = factory.LazyAttribute(lambda o: o.pricing_agreement.organization)
    initiated_by = factory.LazyAttribute(lambda o: o.student)
    amount = factory.LazyAttribute(lambda o: o.pricing_agreement.agreed_rate)
    currency = "NGN"
    paystack_reference = factory.Sequence(lambda n: f"ref_test_{n}")
    status = FamilyPaymentStatus.PENDING
