"""API tests for the organization foundation — the tenant boundary, proved.

The authorization half of the SaaS Phase 1 list. This app holds no money and no
recitation recordings yet, but it decides who can reach an academy at all, so the
tests that matter most are the negative ones: an outsider must not read a tenant
by knowing its id, a member of one academy must not touch another's members, a
staff or teacher member must not manage memberships, nobody must be able to create
a second owner, and a suspended member must lose access while keeping their row.

The organization/user data rules are in ``test_models.py``; here the model layer
only appears where a response has to agree with it.
"""

from unittest.mock import patch

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role, User
from accounts.tests.factories import StudentFactory, SubTeacherFactory, UserFactory

from organizations.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import (
    OrganizationMembershipFactory,
    SuspendedMembershipFactory,
    academy,
)

CREATE_URL = reverse("organizations:organization-create")
MINE_URL = reverse("organizations:organization-mine")

NEW_ACADEMY = {
    "name": "Al-Huda Quran Academy",
    "slug": "al-huda-quran-academy",
    "timezone": "Africa/Lagos",
}


def detail_url(organization):
    return reverse("organizations:organization-detail", args=[organization.pk])


def memberships_url(organization):
    return reverse("organizations:membership-list", args=[organization.pk])


def membership_url(membership):
    return reverse(
        "organizations:membership-detail",
        args=[membership.organization_id, membership.pk],
    )


class OrganizationCreationAPITests(APITestCase):
    """POST /api/organizations/ — one request, one tenant, one owner."""

    def setUp(self):
        self.user = StudentFactory()

    def create(self, user=None, **overrides):
        if user is not False:
            self.client.force_authenticate(user=user or self.user)
        return self.client.post(CREATE_URL, {**NEW_ACADEMY, **overrides})

    def test_an_unauthenticated_caller_cannot_create_an_organization(self):
        response = self.client.post(CREATE_URL, NEW_ACADEMY)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(Organization.objects.exists())

    def test_an_authenticated_user_creates_an_organization(self):
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["slug"], NEW_ACADEMY["slug"])
        self.assertTrue(response.data["is_active"])
        organization = Organization.objects.get(slug=NEW_ACADEMY["slug"])
        self.assertEqual(organization.name, NEW_ACADEMY["name"])

    def test_the_creator_becomes_the_active_owner(self):
        self.create()
        membership = OrganizationMembership.objects.get(user=self.user)
        self.assertEqual(membership.role, OrganizationRole.OWNER)
        self.assertEqual(membership.status, MembershipStatus.ACTIVE)
        self.assertEqual(membership.organization.slug, NEW_ACADEMY["slug"])

    def test_the_creators_account_role_is_unchanged(self):
        self.create()
        self.user.refresh_from_db()
        # A student who founds an academy owns it and is still a student. Phase 1
        # keeps organization authority and account role apart on purpose.
        self.assertEqual(self.user.role, Role.STUDENT)

    def test_a_teacher_creating_an_academy_is_not_made_its_admin(self):
        teacher = SubTeacherFactory()
        self.create(user=teacher)
        teacher.refresh_from_db()
        self.assertEqual(teacher.role, Role.SUB)
        self.assertEqual(
            OrganizationMembership.objects.get(user=teacher).role,
            OrganizationRole.OWNER,
        )

    def test_a_taken_slug_is_refused(self):
        self.create()
        self.client.force_authenticate(user=UserFactory())
        response = self.client.post(CREATE_URL, NEW_ACADEMY)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data)
        self.assertEqual(Organization.objects.count(), 1)

    def test_an_invalid_timezone_is_refused(self):
        response = self.create(timezone="Mars/Olympus")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("timezone", response.data)
        self.assertFalse(Organization.objects.exists())

    def test_a_blank_name_is_refused(self):
        response = self.create(name="")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", response.data)

    def test_the_caller_cannot_ask_to_be_something_other_than_owner(self):
        # Extra fields are ignored rather than honoured: ownership is not an input.
        response = self.create(role="teacher", is_active=False)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Organization.objects.get(slug=NEW_ACADEMY["slug"]).is_active)
        self.assertEqual(
            OrganizationMembership.objects.get(user=self.user).role,
            OrganizationRole.OWNER,
        )

    def test_a_refused_owner_membership_leaves_no_organization(self):
        with patch.object(
            OrganizationMembership,
            "save",
            side_effect=DjangoValidationError("no membership for you"),
        ):
            response = self.create()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The whole transaction rolled back: a tenant nobody can administer must
        # not survive its own creation request.
        self.assertFalse(Organization.objects.exists())
        self.assertFalse(OrganizationMembership.objects.exists())

    def test_an_unexpected_failure_also_leaves_no_organization(self):
        # Not a ValidationError, so nothing catches it — the rollback has to come
        # from the transaction rather than from the except clause.
        with patch.object(
            OrganizationMembership, "save", side_effect=RuntimeError("database gone")
        ):
            with self.assertRaises(RuntimeError):
                self.create()
        self.assertFalse(Organization.objects.exists())

    def test_the_new_organization_appears_in_mine_as_owner(self):
        self.create()
        response = self.client.get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["role"], OrganizationRole.OWNER)
        self.assertEqual(entry["status"], MembershipStatus.ACTIVE)
        self.assertEqual(entry["organization"]["slug"], NEW_ACADEMY["slug"])


class TenantWorld(APITestCase):
    """Two independent academies, and every role represented in the first.

    Organization A has an owner, an admin, a staff member, a teacher and a
    suspended teacher. Organization B has its own owner. Nobody belongs to both,
    and ``self.outsider`` belongs to neither — which is what makes every
    cross-tenant assertion below meaningful.
    """

    def setUp(self):
        self.org_a = academy(name="Academy A", slug="academy-a")
        self.owner_a = self.org_a.owner_membership.user
        self.admin_membership_a = OrganizationMembershipFactory(
            organization=self.org_a, role=OrganizationRole.ADMIN
        )
        self.admin_a = self.admin_membership_a.user
        self.staff_membership_a = OrganizationMembershipFactory(
            organization=self.org_a, role=OrganizationRole.STAFF
        )
        self.staff_a = self.staff_membership_a.user
        self.teacher_membership_a = OrganizationMembershipFactory(
            organization=self.org_a, role=OrganizationRole.TEACHER
        )
        self.teacher_a = self.teacher_membership_a.user
        self.suspended_membership_a = SuspendedMembershipFactory(
            organization=self.org_a, role=OrganizationRole.TEACHER
        )
        self.suspended_a = self.suspended_membership_a.user

        self.org_b = academy(name="Academy B", slug="academy-b")
        self.owner_b = self.org_b.owner_membership.user

        self.outsider = UserFactory()

    def as_user(self, user):
        self.client.force_authenticate(user=user)
        return self.client


class OrganizationAccessAPITests(TenantWorld):
    """GET /api/organizations/{id}/ — membership is access; an id is not."""

    def test_every_active_role_can_read_its_own_organization(self):
        for user in (self.owner_a, self.admin_a, self.staff_a, self.teacher_a):
            with self.subTest(user=user.username):
                response = self.as_user(user).get(detail_url(self.org_a))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["slug"], "academy-a")

    def test_a_member_of_a_cannot_read_b(self):
        response = self.as_user(self.owner_a).get(detail_url(self.org_b))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_member_of_b_cannot_read_a(self):
        response = self.as_user(self.owner_b).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_each_owner_reads_their_own_organization(self):
        self.assertEqual(
            self.as_user(self.owner_b).get(detail_url(self.org_b)).data["slug"],
            "academy-b",
        )

    def test_an_outsider_cannot_read_an_organization(self):
        response = self.as_user(self.outsider).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_suspended_member_cannot_read_the_organization(self):
        response = self.as_user(self.suspended_a).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        # The record stays; only the access is gone.
        self.assertTrue(
            OrganizationMembership.objects.filter(
                pk=self.suspended_membership_a.pk, status=MembershipStatus.SUSPENDED
            ).exists()
        )

    def test_an_unauthenticated_caller_cannot_read_an_organization(self):
        response = self.client.get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


    def test_an_inactive_organization_denies_suspended_member(self):
        self.org_a.is_active = False
        self.org_a.save()
        response = self.as_user(self.suspended_a).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_inactive_organization_cannot_be_read(self):
        self.org_a.is_active = False
        self.org_a.save()
        response = self.as_user(self.owner_a).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_organization_answers_the_same_as_someone_elses(self):
        # 403 either way, so the response cannot be used to discover which
        # organization ids exist.
        unknown = self.as_user(self.owner_a).get(
            reverse("organizations:organization-detail", args=[999999])
        )
        self.assertEqual(unknown.status_code, status.HTTP_403_FORBIDDEN)


class MyOrganizationsAPITests(TenantWorld):
    """GET /api/organizations/mine/ — the caller's memberships and no one else's."""

    def test_a_member_sees_only_their_own_organization(self):
        response = self.as_user(self.teacher_a).get(MINE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry["id"], self.teacher_membership_a.pk)
        self.assertEqual(entry["organization"]["slug"], "academy-a")
        self.assertEqual(entry["role"], OrganizationRole.TEACHER)

    def test_a_user_in_two_academies_sees_both_with_the_right_role(self):
        both = OrganizationMembershipFactory(
            organization=self.org_b, user=self.owner_a, role=OrganizationRole.TEACHER
        )
        response = self.as_user(self.owner_a).get(MINE_URL)
        self.assertEqual(len(response.data), 2)
        roles = {
            entry["organization"]["slug"]: entry["role"] for entry in response.data
        }
        self.assertEqual(
            roles, {"academy-a": OrganizationRole.OWNER, "academy-b": both.role}
        )

    def test_an_outsider_belongs_to_nothing(self):
        self.assertEqual(self.as_user(self.outsider).get(MINE_URL).data, [])

    def test_a_suspended_membership_is_not_listed(self):
        self.assertEqual(self.as_user(self.suspended_a).get(MINE_URL).data, [])

    def test_an_unauthenticated_caller_gets_nothing(self):
        self.assertEqual(
            self.client.get(MINE_URL).status_code, status.HTTP_401_UNAUTHORIZED
        )


class MembershipListingAPITests(TenantWorld):
    """GET /api/organizations/{id}/memberships/ — the directory is owner/admin only."""

    def test_the_owner_reads_the_membership_list(self):
        response = self.as_user(self.owner_a).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), self.org_a.memberships.count())
        self.assertEqual(response.data[0]["role"], OrganizationRole.OWNER)

    def test_an_admin_reads_the_membership_list(self):
        response = self.as_user(self.admin_a).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        usernames = {entry["username"] for entry in response.data}
        self.assertIn(self.teacher_a.username, usernames)

    def test_a_staff_member_is_refused_the_directory(self):
        response = self.as_user(self.staff_a).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_teacher_is_refused_the_directory(self):
        response = self.as_user(self.teacher_a).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_suspended_admin_is_refused_the_directory(self):
        suspended_admin = SuspendedMembershipFactory(
            organization=self.org_a, role=OrganizationRole.ADMIN
        )
        response = self.as_user(suspended_admin.user).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_another_organizations_owner_is_refused(self):
        response = self.as_user(self.owner_b).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_outsider_is_refused(self):
        response = self.as_user(self.outsider).get(memberships_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unauthenticated_caller_is_refused(self):
        self.assertEqual(
            self.client.get(memberships_url(self.org_a)).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_the_listing_contains_only_this_organizations_rows(self):
        response = self.as_user(self.owner_a).get(memberships_url(self.org_a))
        organizations = {entry["organization"] for entry in response.data}
        self.assertEqual(organizations, {self.org_a.pk})
        self.assertNotIn(
            self.owner_b.username, {entry["username"] for entry in response.data}
        )


class MembershipCreationAPITests(TenantWorld):
    """POST /api/organizations/{id}/memberships/ — who may add whom, and as what."""

    def setUp(self):
        super().setUp()
        self.newcomer = UserFactory()

    def add(self, caller, organization=None, **body):
        payload = {"user": self.newcomer.pk, "role": OrganizationRole.TEACHER, **body}
        return self.as_user(caller).post(
            memberships_url(organization or self.org_a), payload
        )

    def test_the_owner_adds_a_teacher(self):
        response = self.add(self.owner_a)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["username"], self.newcomer.username)
        self.assertEqual(response.data["status"], MembershipStatus.ACTIVE)
        membership = OrganizationMembership.objects.get(user=self.newcomer)
        self.assertEqual(membership.organization, self.org_a)
        self.assertEqual(membership.role, OrganizationRole.TEACHER)

    def test_an_admin_adds_a_teacher(self):
        self.assertEqual(
            self.add(self.admin_a).status_code, status.HTTP_201_CREATED
        )

    def test_an_admin_may_add_another_admin(self):
        response = self.add(self.admin_a, role=OrganizationRole.ADMIN)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["role"], OrganizationRole.ADMIN)

    def test_a_new_member_can_then_read_the_organization(self):
        self.add(self.owner_a)
        response = self.as_user(self.newcomer).get(detail_url(self.org_a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_adding_a_member_does_not_touch_their_account_role(self):
        self.add(self.owner_a)
        self.newcomer.refresh_from_db()
        self.assertEqual(self.newcomer.role, Role.STUDENT)

    def test_a_staff_member_cannot_add_anyone(self):
        response = self.add(self.staff_a)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            OrganizationMembership.objects.filter(user=self.newcomer).exists()
        )

    def test_a_teacher_cannot_add_anyone(self):
        self.assertEqual(self.add(self.teacher_a).status_code, status.HTTP_403_FORBIDDEN)

    def test_a_suspended_member_cannot_add_anyone(self):
        self.assertEqual(
            self.add(self.suspended_a).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_an_outsider_cannot_add_anyone(self):
        self.assertEqual(self.add(self.outsider).status_code, status.HTTP_403_FORBIDDEN)

    def test_another_organizations_owner_cannot_add_to_this_one(self):
        response = self.add(self.owner_b)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            OrganizationMembership.objects.filter(user=self.newcomer).exists()
        )

    def test_an_admin_cannot_create_an_owner(self):
        response = self.add(self.admin_a, role=OrganizationRole.OWNER)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", response.data)
        self.assertEqual(
            self.org_a.memberships.filter(role=OrganizationRole.OWNER).count(), 1
        )

    def test_the_owner_cannot_create_a_second_owner_either(self):
        response = self.add(self.owner_a, role=OrganizationRole.OWNER)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.org_a.memberships.filter(role=OrganizationRole.OWNER).count(), 1
        )

    def test_an_unsupported_role_is_refused(self):
        response = self.add(self.owner_a, role="principal")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", response.data)

    def test_an_existing_member_cannot_be_added_twice(self):
        response = self.add(self.owner_a, user=self.teacher_a.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)
        self.assertEqual(self.org_a.memberships.filter(user=self.teacher_a).count(), 1)

    def test_a_suspended_member_cannot_be_added_again(self):
        # Reactivation is a change to the row that exists, not a second row.
        response = self.add(self.owner_a, user=self.suspended_a.pk)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_an_unknown_user_is_refused(self):
        response = self.add(self.owner_a, user=999999)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_no_user_is_created_by_this_endpoint(self):
        # Onboarding someone the platform has not met is an invitation flow, which
        # is a later phase. Naming a user who does not exist is a 400, not a signup.
        before = OrganizationMembership.objects.count()
        response = self.as_user(self.owner_a).post(
            memberships_url(self.org_a),
            {"username": "brand-new", "role": OrganizationRole.TEACHER},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)
        self.assertFalse(User.objects.filter(username="brand-new").exists())
        self.assertEqual(OrganizationMembership.objects.count(), before)

    def test_the_tenant_comes_from_the_url_not_the_body(self):
        # A caller who names another organization in the body still adds to the
        # one they were authorized for.
        response = self.as_user(self.owner_a).post(
            memberships_url(self.org_a),
            {
                "user": self.newcomer.pk,
                "role": OrganizationRole.TEACHER,
                "organization": self.org_b.pk,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            OrganizationMembership.objects.get(user=self.newcomer).organization,
            self.org_a,
        )

    def test_a_membership_cannot_be_added_to_an_unknown_organization(self):
        response = self.as_user(self.owner_a).post(
            reverse("organizations:membership-list", args=[999999]),
            {"user": self.newcomer.pk, "role": OrganizationRole.TEACHER},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MembershipUpdateAPITests(TenantWorld):
    """PATCH a membership — suspend, reactivate, re-role, and what stays untouchable."""

    def patch_membership(self, caller, membership, **body):
        return self.as_user(caller).patch(membership_url(membership), body)

    def test_the_owner_suspends_a_teacher(self):
        response = self.patch_membership(
            self.owner_a,
            self.teacher_membership_a,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], MembershipStatus.SUSPENDED)
        self.teacher_membership_a.refresh_from_db()
        self.assertFalse(self.teacher_membership_a.is_active)

    def test_a_suspended_member_loses_organization_access(self):
        self.patch_membership(
            self.owner_a,
            self.teacher_membership_a,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertEqual(
            self.as_user(self.teacher_a).get(detail_url(self.org_a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(self.as_user(self.teacher_a).get(MINE_URL).data, [])
        # And the row is still there — suspension is not deletion.
        self.assertTrue(
            OrganizationMembership.objects.filter(pk=self.teacher_membership_a.pk).exists()
        )

    def test_the_owner_reactivates_a_suspended_member(self):
        response = self.patch_membership(
            self.owner_a,
            self.suspended_membership_a,
            status=MembershipStatus.ACTIVE,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.as_user(self.suspended_a).get(detail_url(self.org_a)).status_code,
            status.HTTP_200_OK,
        )

    def test_an_admin_suspends_a_teacher(self):
        response = self.patch_membership(
            self.admin_a,
            self.teacher_membership_a,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_an_admin_changes_a_members_role(self):
        response = self.patch_membership(
            self.admin_a, self.teacher_membership_a, role=OrganizationRole.STAFF
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.teacher_membership_a.refresh_from_db()
        self.assertEqual(self.teacher_membership_a.role, OrganizationRole.STAFF)

    def test_a_staff_member_cannot_change_a_membership(self):
        response = self.patch_membership(
            self.staff_a, self.teacher_membership_a, role=OrganizationRole.ADMIN
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.teacher_membership_a.refresh_from_db()
        self.assertEqual(self.teacher_membership_a.role, OrganizationRole.TEACHER)

    def test_a_teacher_cannot_change_a_membership(self):
        self.assertEqual(
            self.patch_membership(
                self.teacher_a, self.staff_membership_a, role=OrganizationRole.ADMIN
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_member_cannot_promote_themselves(self):
        response = self.patch_membership(
            self.teacher_a, self.teacher_membership_a, role=OrganizationRole.ADMIN
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_outsider_cannot_change_a_membership(self):
        self.assertEqual(
            self.patch_membership(
                self.outsider, self.teacher_membership_a, role=OrganizationRole.STAFF
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_another_organizations_owner_cannot_change_this_ones_membership(self):
        self.assertEqual(
            self.patch_membership(
                self.owner_b, self.teacher_membership_a, role=OrganizationRole.STAFF
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_membership_of_another_organization_is_not_found_here(self):
        # Addressed through Academy A's URL, which is the only tenant this route
        # can see — so B's membership is a 404, not a 403 that confirms it exists.
        foreign = self.org_b.owner_membership
        response = self.as_user(self.owner_a).patch(
            reverse(
                "organizations:membership-detail", args=[self.org_a.pk, foreign.pk]
            ),
            {"status": MembershipStatus.SUSPENDED},
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        foreign.refresh_from_db()
        self.assertTrue(foreign.is_active)

    def test_the_owners_membership_is_not_editable_by_an_admin(self):
        response = self.patch_membership(
            self.admin_a,
            self.org_a.owner_membership,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(self.org_a.owner_membership.is_active)

    def test_the_owners_membership_is_not_editable_by_the_owner_either(self):
        response = self.patch_membership(
            self.owner_a, self.org_a.owner_membership, role=OrganizationRole.ADMIN
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.org_a.owner_membership.role, OrganizationRole.OWNER
        )

    def test_no_one_can_be_promoted_to_owner(self):
        response = self.patch_membership(
            self.owner_a, self.teacher_membership_a, role=OrganizationRole.OWNER
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", response.data)
        self.assertEqual(
            self.org_a.memberships.filter(role=OrganizationRole.OWNER).count(), 1
        )

    def test_an_empty_patch_is_refused(self):
        response = self.patch_membership(self.owner_a, self.teacher_membership_a)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_organization_and_user_of_a_membership_are_not_editable(self):
        response = self.patch_membership(
            self.owner_a,
            self.teacher_membership_a,
            organization=self.org_b.pk,
            user=self.outsider.pk,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.teacher_membership_a.refresh_from_db()
        self.assertEqual(self.teacher_membership_a.organization, self.org_a)
        self.assertEqual(self.teacher_membership_a.user, self.teacher_a)

    def test_a_whole_object_replace_is_not_offered(self):
        response = self.as_user(self.owner_a).put(
            membership_url(self.teacher_membership_a),
            {"role": OrganizationRole.STAFF},
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_an_unauthenticated_caller_cannot_change_a_membership(self):
        response = self.client.patch(
            membership_url(self.teacher_membership_a),
            {"status": MembershipStatus.SUSPENDED},
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
