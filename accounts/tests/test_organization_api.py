"""The organization-scoped account endpoints — status codes, shapes and scoping.

This file is about how the three endpoints behave: who they refuse, what a body
looks like, and that an id from another academy is a 404 rather than a 403. The
cross-tenant *scenarios* the phase spec makes mandatory — one parent in two
academies, one teacher configured differently in each — live in
``test_tenant_isolation.py``, so neither file repeats the other.

``BELONGS`` is the same stand-in ``test_tenancy.py`` explains: Phase 1's
``OrganizationRole`` has no value meaning "a student of this academy", and these
rules key off membership status plus the account role rather than the organization
role (see tech-debt.md).
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import OrganizationTeacherConfiguration
from accounts.tests.factories import (
    OrganizationTeacherConfigurationFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    UserFactory,
)
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
    academy,
)

BELONGS = OrganizationRole.STAFF


def admit(organization, user, role=BELONGS, **kwargs):
    membership = OrganizationMembershipFactory(
        organization=organization,
        user=user,
        role=role,
        **kwargs,
    )
    if getattr(user, "is_student", False) or getattr(user, "role", None) == "student":
        from organizations.models import StudentEnrollment, EnrollmentStatus
        
        status_val = str(kwargs.get("status", ""))
        status = EnrollmentStatus.INACTIVE if "suspended" in status_val else EnrollmentStatus.ACTIVE
        StudentEnrollment.objects.get_or_create(
            organization=organization, 
            user=user,
            defaults={"status": status}
        )
    return membership


def children_url(organization):
    return reverse(
        "accounts:organization-children",
        kwargs={"organization_pk": organization.pk},
    )


def configurations_url(organization):
    return reverse(
        "accounts:organization-teacher-configuration-list",
        kwargs={"organization_pk": organization.pk},
    )


def configuration_url(organization, configuration):
    return reverse(
        "accounts:organization-teacher-configuration-detail",
        kwargs={
            "organization_pk": organization.pk,
            "pk": getattr(configuration, "pk", configuration),
        },
    )


class OrganizationChildrenAPITests(APITestCase):
    def setUp(self):
        self.organization = OrganizationFactory()
        link = ParentLinkFactory()
        self.parent, self.student = link.parent, link.student

    def test_a_parent_sees_the_child_this_academy_has_admitted(self):
        admit(self.organization, self.parent)
        admit(self.organization, self.student)
        self.client.force_authenticate(user=self.parent)

        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.student.pk])
        # The same global fields /my-children/ returns, and no signup code.
        self.assertNotIn("signup_code", response.data[0])
        self.assertEqual(response.data[0]["username"], self.student.username)

    def test_a_child_this_academy_has_not_admitted_is_absent(self):
        admit(self.organization, self.parent)
        self.client.force_authenticate(user=self.parent)

        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_suspended_child_is_absent(self):
        admit(self.organization, self.parent)
        admit(self.organization, self.student, status=MembershipStatus.SUSPENDED)
        self.client.force_authenticate(user=self.parent)

        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_suspended_parent_is_refused_outright(self):
        """Not an empty list — a suspended member has no access to this academy."""
        admit(self.organization, self.parent, status=MembershipStatus.SUSPENDED)
        admit(self.organization, self.student)
        self.client.force_authenticate(user=self.parent)

        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_non_member_is_refused(self):
        admit(self.organization, self.student)
        self.client.force_authenticate(user=self.parent)
        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_member_who_is_not_a_parent_account_is_refused(self):
        teacher = SubTeacherFactory()
        admit(self.organization, teacher)
        self.client.force_authenticate(user=teacher)
        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_organization_is_refused_rather_than_confirmed(self):
        self.client.force_authenticate(user=self.parent)
        response = self.client.get(
            reverse("accounts:organization-children", kwargs={"organization_pk": 999999})
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_it_requires_authentication(self):
        response = self.client.get(children_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TeacherConfigurationCreateAPITests(APITestCase):
    def setUp(self):
        self.owner = UserFactory()
        self.organization = academy(owner=self.owner)
        self.teacher = SubTeacherFactory()
        self.membership = admit(
            self.organization, self.teacher, role=OrganizationRole.TEACHER
        )
        self.client.force_authenticate(user=self.owner)

    def payload(self, **overrides):
        body = {
            "user": self.teacher.pk,
            "max_weekly_hours": 12,
            "hourly_payout_rate": "40.00",
            "approved": True,
        }
        body.update(overrides)
        return body

    def test_an_owner_sets_a_teachers_terms(self):
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"], self.teacher.pk)
        self.assertEqual(response.data["username"], self.teacher.username)
        self.assertEqual(response.data["organization"], self.organization.pk)
        self.assertEqual(response.data["membership"], self.membership.pk)
        self.assertEqual(response.data["max_weekly_hours"], 12)
        self.assertEqual(Decimal(response.data["hourly_payout_rate"]), Decimal("40.00"))
        self.assertTrue(response.data["approved"])

        configuration = OrganizationTeacherConfiguration.objects.get(
            pk=response.data["id"]
        )
        self.assertEqual(configuration.membership, self.membership)

    def test_an_admin_may_set_terms_too(self):
        admin = UserFactory()
        admit(self.organization, admin, role=OrganizationRole.ADMIN)
        self.client.force_authenticate(user=admin)
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_staff_member_cannot(self):
        staff = UserFactory()
        admit(self.organization, staff, role=OrganizationRole.STAFF)
        self.client.force_authenticate(user=staff)
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(OrganizationTeacherConfiguration.objects.exists())

    def test_the_teacher_themselves_cannot(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_non_member_cannot(self):
        self.client.force_authenticate(user=UserFactory())
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_it_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_user_who_is_not_a_member_here_is_rejected(self):
        outsider = SubTeacherFactory()
        response = self.client.post(
            configurations_url(self.organization),
            self.payload(user=outsider.pk),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)
        self.assertFalse(OrganizationTeacherConfiguration.objects.exists())

    def test_a_teacher_of_another_academy_cannot_be_configured_from_here(self):
        """Invariant 6: a client-supplied id does not reach another tenant's rows."""
        elsewhere = OrganizationFactory()
        their_teacher = SubTeacherFactory()
        admit(elsewhere, their_teacher, role=OrganizationRole.TEACHER)

        response = self.client.post(
            configurations_url(self.organization),
            self.payload(user=their_teacher.pk),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(OrganizationTeacherConfiguration.objects.exists())

    def test_a_second_set_of_terms_for_the_same_member_is_rejected(self):
        OrganizationTeacherConfigurationFactory(membership=self.membership)
        response = self.client.post(
            configurations_url(self.organization), self.payload(), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(OrganizationTeacherConfiguration.objects.count(), 1)

    def test_a_student_account_cannot_be_given_teaching_terms(self):
        student = StudentFactory()
        admit(self.organization, student, role=OrganizationRole.TEACHER)
        response = self.client.post(
            configurations_url(self.organization),
            self.payload(user=student.pk),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_a_parent_account_cannot_be_given_teaching_terms(self):
        parent = ParentFactory()
        admit(self.organization, parent, role=OrganizationRole.TEACHER)
        response = self.client.post(
            configurations_url(self.organization),
            self.payload(user=parent.pk),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_zero_hour_cap_is_rejected(self):
        response = self.client.post(
            configurations_url(self.organization),
            self.payload(max_weekly_hours=0),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("max_weekly_hours", response.data)

    def test_terms_default_to_unapproved_with_no_rate(self):
        response = self.client.post(
            configurations_url(self.organization),
            {"user": self.teacher.pk, "max_weekly_hours": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["approved"])
        self.assertIsNone(response.data["hourly_payout_rate"])


class TeacherConfigurationReadAndUpdateAPITests(APITestCase):
    def setUp(self):
        self.owner = UserFactory()
        self.organization = academy(owner=self.owner)
        self.teacher = SubTeacherFactory()
        self.configuration = OrganizationTeacherConfigurationFactory(
            membership=admit(
                self.organization, self.teacher, role=OrganizationRole.TEACHER
            )
        )
        self.client.force_authenticate(user=self.owner)

    def test_the_list_holds_only_this_academys_terms(self):
        elsewhere = OrganizationFactory()
        OrganizationTeacherConfigurationFactory(
            membership=admit(
                elsewhere, SubTeacherFactory(), role=OrganizationRole.TEACHER
            )
        )
        response = self.client.get(configurations_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.configuration.pk])

    def test_an_owner_reads_one_set_of_terms(self):
        response = self.client.get(
            configuration_url(self.organization, self.configuration)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.configuration.pk)
        self.assertEqual(response.data["user"], self.teacher.pk)

    def test_terms_belonging_to_another_academy_are_a_404_not_a_403(self):
        """A 403 would confirm the row exists — the repository's existing rule."""
        elsewhere = OrganizationFactory()
        theirs = OrganizationTeacherConfigurationFactory(
            membership=admit(
                elsewhere, SubTeacherFactory(), role=OrganizationRole.TEACHER
            )
        )
        response = self.client.get(configuration_url(self.organization, theirs))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.patch(
            configuration_url(self.organization, theirs),
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        theirs.refresh_from_db()
        self.assertFalse(theirs.approved)

    def test_an_owner_approves_a_teacher(self):
        response = self.client.patch(
            configuration_url(self.organization, self.configuration),
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["approved"])
        self.configuration.refresh_from_db()
        self.assertTrue(self.configuration.approved)

    def test_an_owner_re_caps_and_re_rates(self):
        response = self.client.patch(
            configuration_url(self.organization, self.configuration),
            {"max_weekly_hours": 25, "hourly_payout_rate": "55.50"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.max_weekly_hours, 25)
        self.assertEqual(self.configuration.hourly_payout_rate, Decimal("55.50"))

    def test_a_rate_can_be_cleared(self):
        response = self.client.patch(
            configuration_url(self.organization, self.configuration),
            {"hourly_payout_rate": None},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.configuration.refresh_from_db()
        self.assertIsNone(self.configuration.hourly_payout_rate)

    def test_an_empty_patch_is_rejected(self):
        response = self.client.patch(
            configuration_url(self.organization, self.configuration), {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_zero_hour_cap_is_rejected(self):
        response = self.client.patch(
            configuration_url(self.organization, self.configuration),
            {"max_weekly_hours": 0},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.configuration.refresh_from_db()
        self.assertEqual(self.configuration.max_weekly_hours, 8)

    def test_put_is_not_offered(self):
        response = self.client.put(
            configuration_url(self.organization, self.configuration),
            {"max_weekly_hours": 3},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_a_teacher_cannot_read_or_change_their_own_terms(self):
        """Narrow by default. Widening this is a recorded product decision."""
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(
            self.client.get(configurations_url(self.organization)).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.client.patch(
                configuration_url(self.organization, self.configuration),
                {"approved": True},
                format="json",
            ).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_a_suspended_admin_loses_access(self):
        admin = UserFactory()
        membership = admit(self.organization, admin, role=OrganizationRole.ADMIN)
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        self.client.force_authenticate(user=admin)
        response = self.client.get(configurations_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
