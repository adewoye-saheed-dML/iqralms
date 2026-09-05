"""Model-level invariants for the organization foundation.

CLAUDE.md asks for automated coverage of the durable, high-risk rules rather than
of routine CRUD. For a tenant boundary those rules are: what identifies an
academy, that a user's memberships are one-per-academy, that an academy has
exactly one owner, and that an *active* membership is the only thing that means
access. Everything a later phase builds on top of tenancy assumes these four.

The API-layer half — who may call what, and cross-tenant refusal — is in
``test_api.py``.
"""

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Role
from accounts.tests.factories import StudentFactory, UserFactory

from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
    active_membership,
)
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
    OwnerMembershipFactory,
    SuspendedMembershipFactory,
    academy,
)


class OrganizationModelTests(TestCase):
    """The tenant itself: identified by its slug, active by default."""

    def test_an_organization_is_created_active(self):
        organization = OrganizationFactory(name="Al-Huda Quran Academy")
        self.assertTrue(organization.is_active)
        self.assertEqual(organization.slug, "al-huda-quran-academy")
        self.assertEqual(organization.timezone, "Africa/Lagos")
        self.assertIsNotNone(organization.created_at)
        self.assertIsNotNone(organization.updated_at)

    def test_slug_is_globally_unique(self):
        OrganizationFactory(slug="al-huda")
        with self.assertRaises(ValidationError) as caught:
            OrganizationFactory(slug="al-huda")
        self.assertIn("slug", caught.exception.message_dict)

    def test_slug_uniqueness_is_a_database_constraint(self):
        # bulk_create skips full_clean(), so this is the database refusing rather
        # than the model — the guarantee that survives a hand-written INSERT.
        OrganizationFactory(slug="noor")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Organization.objects.bulk_create(
                [Organization(name="Noor Again", slug="noor", timezone="Africa/Lagos")]
            )

    def test_two_organizations_may_share_a_name(self):
        OrganizationFactory(name="Noor Learning Centre", slug="noor-lagos")
        twin = OrganizationFactory(name="Noor Learning Centre", slug="noor-abuja")
        self.assertEqual(Organization.objects.filter(name=twin.name).count(), 2)

    def test_an_invalid_timezone_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            OrganizationFactory(timezone="Mars/Olympus")
        self.assertIn("timezone", caught.exception.message_dict)

    def test_a_name_of_spaces_is_not_a_name(self):
        with self.assertRaises(ValidationError) as caught:
            OrganizationFactory(name="   ")
        self.assertIn("name", caught.exception.message_dict)

    def test_a_name_is_stored_stripped(self):
        self.assertEqual(OrganizationFactory(name="  Taqwa  ").name, "Taqwa")


class OrganizationMembershipModelTests(TestCase):
    """One row per user per academy, exactly one owner, and roles per tenant."""

    def test_a_membership_is_created_active(self):
        membership = OrganizationMembershipFactory()
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)

    def test_duplicate_membership_is_rejected(self):
        first = OrganizationMembershipFactory()
        with self.assertRaises(ValidationError):
            OrganizationMembershipFactory(
                organization=first.organization,
                user=first.user,
                role=OrganizationRole.STAFF,
            )
        self.assertEqual(first.organization.memberships.count(), 1)

    def test_duplicate_membership_is_a_database_constraint(self):
        first = OrganizationMembershipFactory()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.bulk_create(
                [
                    OrganizationMembership(
                        organization=first.organization,
                        user=first.user,
                        role=OrganizationRole.STAFF,
                    )
                ]
            )

    def test_an_invalid_role_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            OrganizationMembershipFactory(role="principal")
        self.assertIn("role", caught.exception.message_dict)

    def test_an_invalid_status_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            OrganizationMembershipFactory(status="on-holiday")
        self.assertIn("status", caught.exception.message_dict)

    def test_a_user_may_belong_to_several_organizations(self):
        user = UserFactory()
        first = OrganizationMembershipFactory(user=user, role=OrganizationRole.TEACHER)
        second = OwnerMembershipFactory(user=user)
        self.assertNotEqual(first.organization_id, second.organization_id)
        self.assertEqual(user.organization_memberships.count(), 2)

    def test_a_role_is_per_organization(self):
        user = UserFactory()
        owned = academy(owner=user)
        taught = OrganizationMembershipFactory(
            user=user, role=OrganizationRole.TEACHER
        ).organization
        roles = {
            membership.organization_id: membership.role
            for membership in user.organization_memberships.all()
        }
        self.assertEqual(roles[owned.pk], OrganizationRole.OWNER)
        self.assertEqual(roles[taught.pk], OrganizationRole.TEACHER)

    def test_an_organization_cannot_have_a_second_owner(self):
        organization = academy()
        with self.assertRaises(ValidationError):
            OwnerMembershipFactory(organization=organization)
        self.assertEqual(
            organization.memberships.filter(role=OrganizationRole.OWNER).count(), 1
        )

    def test_single_ownership_is_a_database_constraint(self):
        organization = academy()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrganizationMembership.objects.bulk_create(
                [
                    OrganizationMembership(
                        organization=organization,
                        user=UserFactory(),
                        role=OrganizationRole.OWNER,
                    )
                ]
            )

    def test_promoting_an_existing_member_to_owner_is_refused(self):
        organization = academy()
        member = OrganizationMembershipFactory(organization=organization)
        member.role = OrganizationRole.OWNER
        with self.assertRaises(ValidationError):
            member.save()

    def test_owner_membership_reads_the_single_owner(self):
        organization = academy()
        OrganizationMembershipFactory(organization=organization)
        owner = organization.owner_membership
        self.assertEqual(owner.role, OrganizationRole.OWNER)
        self.assertEqual(organization.memberships.count(), 2)

    def test_the_account_role_is_untouched_by_membership(self):
        student = StudentFactory()
        academy(owner=student)
        student.refresh_from_db()
        # The whole point of Phase 1: organization authority and account role are
        # separate concepts, and owning an academy does not make anyone a teacher.
        self.assertEqual(student.role, Role.STUDENT)

    def test_only_owner_and_admin_may_manage_memberships(self):
        organization = academy()
        manageable = {
            role: OrganizationMembershipFactory(
                organization=organization, role=role
            ).can_manage_memberships
            for role in (OrganizationRole.ADMIN, OrganizationRole.STAFF, OrganizationRole.TEACHER)
        }
        self.assertEqual(
            manageable,
            {
                OrganizationRole.ADMIN: True,
                OrganizationRole.STAFF: False,
                OrganizationRole.TEACHER: False,
            },
        )
        self.assertTrue(organization.owner_membership.can_manage_memberships)

    def test_a_suspended_admin_manages_nothing(self):
        suspended = SuspendedMembershipFactory(role=OrganizationRole.ADMIN)
        self.assertFalse(suspended.can_manage_memberships)


class ActiveMembershipTests(TestCase):
    """The one function that decides tenant access, tested on its own.

    Every permission class and every scoped queryset in the app leans on it, so
    the three ways of *not* having access — outsider, suspended, anonymous — are
    worth pinning here rather than only through the API.
    """

    def setUp(self):
        self.organization = academy()
        self.owner = self.organization.owner_membership.user

    def test_an_active_member_has_access(self):
        membership = active_membership(user=self.owner, organization=self.organization)
        self.assertEqual(membership.role, OrganizationRole.OWNER)
        self.assertEqual(membership.organization, self.organization)

    def test_an_organization_id_works_as_well_as_an_instance(self):
        self.assertIsNotNone(
            active_membership(user=self.owner, organization=self.organization.pk)
        )

    def test_a_non_member_has_no_access(self):
        self.assertIsNone(
            active_membership(user=UserFactory(), organization=self.organization)
        )

    def test_a_suspended_member_has_no_access(self):
        suspended = SuspendedMembershipFactory(organization=self.organization)
        self.assertIsNone(
            active_membership(user=suspended.user, organization=self.organization)
        )

    def test_membership_of_another_organization_is_not_access(self):
        other = academy()
        self.assertIsNone(active_membership(user=self.owner, organization=other))

    def test_an_anonymous_user_has_no_access(self):
        self.assertIsNone(
            active_membership(user=AnonymousUser(), organization=self.organization)
        )

    def test_an_unknown_organization_is_not_access(self):
        self.assertIsNone(active_membership(user=self.owner, organization=None))
