"""factory_boy factories for the organizations app.

Per CLAUDE.md every model gets a factory before tests are written against it.
Both factories build rows the models would actually accept — a valid IANA
timezone, a slug derived from the name — so a factory can never assert a state
the model rejects.

``academy()`` is the one helper: an organization *plus* its owner membership,
which is the only shape the creation API ever leaves behind. Tests that need a
tenant to exist should use it rather than ``OrganizationFactory`` alone, so they
are testing against the same data shape production has.

A test that wants a *rejection* builds the model directly and passes the wrong
thing deliberately — the convention ``scheduling`` and ``payouts`` follow.
"""

import factory
from django.utils.text import slugify

from accounts.tests.factories import UserFactory

from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)


class OrganizationFactory(factory.django.DjangoModelFactory):
    """An academy with no members yet. Usually you want ``academy()`` instead."""

    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Academy {n}")
    slug = factory.LazyAttribute(lambda o: slugify(o.name))
    timezone = "Africa/Lagos"


class OrganizationMembershipFactory(factory.django.DjangoModelFactory):
    """An active teacher membership — the least privileged role that is not staff.

    ``role`` and ``status`` are the two levers worth overriding; ``owner`` is
    better expressed through ``academy()`` or ``OwnerMembershipFactory``, because
    an organization may only have one.
    """

    class Meta:
        model = OrganizationMembership

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    role = OrganizationRole.TEACHER
    status = MembershipStatus.ACTIVE


class OwnerMembershipFactory(OrganizationMembershipFactory):
    role = OrganizationRole.OWNER


class SuspendedMembershipFactory(OrganizationMembershipFactory):
    """A member whose access is revoked while the record stays."""

    status = MembershipStatus.SUSPENDED


def academy(*, owner=None, **kwargs):
    """An organization and its single owner membership, as the creation API makes it.

    The owner is reachable through ``organization.owner_membership.user``, which is
    also the only representation of ownership there is — deliberately, so a test
    cannot assert against a second source of truth that production does not have.
    """
    organization = OrganizationFactory(**kwargs)
    OwnerMembershipFactory(organization=organization, user=owner or UserFactory())
    return organization
