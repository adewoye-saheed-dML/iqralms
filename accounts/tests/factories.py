"""factory_boy factories for the accounts app.

Per CLAUDE.md every model gets a factory here before tests are written against
it. Factories deliberately derive ``is_minor`` and ``is_lead`` rather than
hardcoding them, so a factory can never assert a state the model would reject.
"""

from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone as dj_timezone

from accounts.models import ParentLink, Role, TeacherProfile, User

#: Shared password for factory-built users, so tests can log them in.
DEFAULT_PASSWORD = "Tilawah-Test-Pass-42"


def years_ago(years: int):
    """A date roughly ``years`` before today — good enough for age fixtures."""
    return dj_timezone.localdate() - timedelta(days=int(years * 365.25))


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")
    first_name = "Test"
    last_name = factory.Sequence(lambda n: f"User{n}")
    role = Role.STUDENT
    timezone = "Africa/Lagos"
    date_of_birth = None
    is_minor = factory.LazyAttribute(
        lambda o: User.minor_from_date_of_birth(o.date_of_birth)
    )
    password = factory.django.Password(DEFAULT_PASSWORD)


class LeadTeacherFactory(UserFactory):
    role = Role.LEAD
    username = factory.Sequence(lambda n: f"lead{n}")
    date_of_birth = factory.LazyFunction(lambda: years_ago(40))


class SubTeacherFactory(UserFactory):
    role = Role.SUB
    username = factory.Sequence(lambda n: f"sub{n}")
    date_of_birth = factory.LazyFunction(lambda: years_ago(30))


class StudentFactory(UserFactory):
    """An adult student — no parent link required."""

    role = Role.STUDENT
    username = factory.Sequence(lambda n: f"student{n}")
    date_of_birth = factory.LazyFunction(lambda: years_ago(25))


class MinorStudentFactory(StudentFactory):
    """A student under 18 — not fully active until a parent links to them."""

    username = factory.Sequence(lambda n: f"minor{n}")
    date_of_birth = factory.LazyFunction(lambda: years_ago(10))


class ParentFactory(UserFactory):
    role = Role.PARENT
    username = factory.Sequence(lambda n: f"parent{n}")
    date_of_birth = factory.LazyFunction(lambda: years_ago(38))


class ParentLinkFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ParentLink

    parent = factory.SubFactory(ParentFactory)
    student = factory.SubFactory(MinorStudentFactory)


class TeacherProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = TeacherProfile

    user = factory.SubFactory(SubTeacherFactory)
    bio = "Teaches tajweed and beginner Arabic."
    max_weekly_hours = 20
    hourly_payout_rate = Decimal("12.50")
    is_lead = factory.LazyAttribute(lambda o: o.user.role == Role.LEAD)
    approved = False


class LeadTeacherProfileFactory(TeacherProfileFactory):
    """The single lead-teacher profile: approved, no per-hour payout rate."""

    user = factory.SubFactory(LeadTeacherFactory)
    hourly_payout_rate = None
    approved = True
