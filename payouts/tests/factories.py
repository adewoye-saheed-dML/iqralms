"""factory_boy factories for the payouts app.

Per CLAUDE.md every model gets a factory before tests are written against it.
There is one model, and the factory's whole job is to build a payout the model
would actually accept:

* the booking is **completed**, because nothing else earns a payout;
* the teacher is the booking's teacher, because a payout is owed to whoever
  taught;
* ``minutes_paid``, ``rate_used`` and ``amount`` are derived from the booking and
  the teacher's profile rather than hardcoded, so ``amount`` always satisfies
  ``TeacherPayout.clean()``'s formula check.

A test that wants a *rejection* builds its ``TeacherPayout`` directly and passes
the wrong thing deliberately — the same convention ``scheduling`` follows for
bookings it wants refused.
"""

from datetime import timedelta

import factory
from django.utils import timezone as dj_timezone

from scheduling.tests.factories import CompletedBookingFactory

from payouts.models import PayoutStatus, TeacherPayout, payout_amount


def past_session(*, weeks_ago=1, **kwargs):
    """A completed session that happened ``weeks_ago``.

    The same helper ``assessment.tests.factories`` keeps, and past-dating is
    legitimate for the same reason: ``Booking.clean()`` runs its time-based rules
    only while a booking is ``scheduled``, so an already-taught session stays
    saveable. A payout is always about a session that has happened, so this is the
    honest default for one.
    """
    kwargs.setdefault("start_time_utc", dj_timezone.now() - timedelta(weeks=weeks_ago))
    return CompletedBookingFactory(**kwargs)


def _rate_for(booking):
    """The booking teacher's hourly rate. Factories build rated teachers."""
    from payouts.services import applicable_rate
    rate = applicable_rate(booking.teacher, organization=booking.organization)
    if rate is not None:
        return rate
    profile = getattr(booking.teacher, "teacher_profile", None)
    return getattr(profile, "hourly_payout_rate", None)


class TeacherPayoutFactory(factory.django.DjangoModelFactory):
    """A generated payout for a completed 1:1 session.

    ``TeacherPayoutFactory()`` builds its own completed booking;
    ``TeacherPayoutFactory(booking=booking)`` pays one you already have. The
    teacher, minutes, rate and amount all follow from the booking, so overriding
    them individually is the wrong lever — pass a different ``booking`` instead.
    """

    class Meta:
        model = TeacherPayout

    booking = factory.LazyFunction(past_session)
    teacher = factory.SelfAttribute("booking.teacher")
    cohort = factory.SelfAttribute("booking.cohort")
    minutes_paid = factory.SelfAttribute("booking.duration_minutes")
    rate_used = factory.LazyAttribute(lambda o: _rate_for(o.booking))
    amount = factory.LazyAttribute(
        lambda o: payout_amount(o.booking.duration_minutes, _rate_for(o.booking))
    )


class FinalizedPayoutFactory(TeacherPayoutFactory):
    """Already history: immutable, and the thing a rate change must not touch."""

    status = PayoutStatus.FINALIZED
    finalized_at = factory.LazyFunction(dj_timezone.now)
