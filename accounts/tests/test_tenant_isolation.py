"""SaaS Phase 2's tenant-isolation suite — the six security invariants.

.. code-block:: text

    1  a global account does not imply access to every academy
    2  a parent-child relationship does not bypass organization membership
    3  a teacher membership grants nothing in another academy
    4  a suspended membership grants no organization-specific access
    5  one academy's teacher configuration is unreadable and unwritable from another
    6  a client-supplied organization or user id never overrides a membership check

Written at the API layer wherever an endpoint exists to prove the invariant, because
that is the surface an attacker has. The endpoint *mechanics* — status codes, payload
shapes, empty patches — are ``test_organization_api.py``'s subject; this file only
constructs two academies and checks that nothing crosses between them.

``BELONGS`` is the stand-in ``test_tenancy.py`` explains: Phase 1's
``OrganizationRole`` has no value meaning "a student of this academy", and these
rules key off membership status plus the account role rather than the organization
role (see tech-debt.md).
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import OrganizationTeacherConfiguration, Role
from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    OrganizationTeacherConfigurationFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    UserFactory,
)
from organizations.models import (
    MembershipStatus,
    OrganizationMembership,
    OrganizationRole,
)
from organizations.tests.factories import OrganizationMembershipFactory, academy

BELONGS = OrganizationRole.STAFF


def admit(organization, user, role=BELONGS, **kwargs):
    return OrganizationMembershipFactory(
        organization=organization, user=user, role=role, **kwargs
    )


def children_url(organization):
    return reverse(
        "accounts:organization-children", kwargs={"organization_pk": organization.pk}
    )


def configurations_url(organization):
    return reverse(
        "accounts:organization-teacher-configuration-list",
        kwargs={"organization_pk": organization.pk},
    )


def configuration_url(organization, configuration):
    return reverse(
        "accounts:organization-teacher-configuration-detail",
        kwargs={"organization_pk": organization.pk, "pk": configuration.pk},
    )


class MultipleOrganizationsTests(APITestCase):
    """Invariant 1, and the reason the account tenancy migration exists at all."""

    def test_one_teacher_holds_independent_memberships_in_two_academies(self):
        teacher = SubTeacherFactory()
        here, there = academy(), academy()
        admit(here, teacher, role=OrganizationRole.TEACHER)
        admit(there, teacher, role=OrganizationRole.TEACHER)

        self.assertEqual(
            OrganizationMembership.objects.filter(user=teacher).count(), 2
        )
        # One account, not two. The whole point of not putting an organization
        # foreign key on User.
        self.assertEqual(teacher.organization_memberships.count(), 2)

    def test_suspension_in_one_academy_leaves_the_other_membership_alone(self):
        teacher = SubTeacherFactory()
        here, there = academy(), academy()
        suspended_here = admit(here, teacher, role=OrganizationRole.TEACHER)
        active_there = admit(there, teacher, role=OrganizationRole.TEACHER)

        suspended_here.status = MembershipStatus.SUSPENDED
        suspended_here.save()

        active_there.refresh_from_db()
        self.assertTrue(active_there.is_active)

    def test_a_user_in_one_academy_is_a_stranger_to_another(self):
        member = ParentFactory()
        here, there = academy(), academy()
        admit(here, member)

        self.client.force_authenticate(user=member)
        self.assertEqual(
            self.client.get(children_url(here)).status_code, status.HTTP_200_OK
        )
        self.assertEqual(
            self.client.get(children_url(there)).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class CrossTenantParentAccessTests(APITestCase):
    """Invariant 2 — the phase spec's mandatory parent test.

    .. code-block:: text

        Parent P  in A and in B
        Student S in B only, linked to P

        P inside A  ->  cannot reach S
        P inside B  ->  reaches S
    """

    def setUp(self):
        self.a, self.b = academy(), academy()
        link = ParentLinkFactory()
        self.parent, self.student = link.parent, link.student
        admit(self.a, self.parent)
        admit(self.b, self.parent)
        admit(self.b, self.student)
        self.client.force_authenticate(user=self.parent)

    def test_the_academy_the_student_does_not_belong_to_cannot_see_them(self):
        response = self.client.get(children_url(self.a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_the_academy_the_student_belongs_to_can(self):
        response = self.client.get(children_url(self.b))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.student.pk])

    def test_the_global_relationship_is_untouched_by_either_answer(self):
        """The family fact is real. It is simply not an academy's authorization."""
        response = self.client.get(reverse("accounts:my-children"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.student.pk])

    def test_suspending_the_students_membership_withdraws_the_academys_view(self):
        membership = OrganizationMembership.objects.get(
            organization=self.b, user=self.student
        )
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        response = self.client.get(children_url(self.b))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_parent_of_neither_academy_reaches_nothing(self):
        stranger = ParentFactory()
        ParentLinkFactory(parent=stranger, student=self.student)
        self.client.force_authenticate(user=stranger)

        for organization in (self.a, self.b):
            self.assertEqual(
                self.client.get(children_url(organization)).status_code,
                status.HTTP_403_FORBIDDEN,
            )


class CrossTenantTeacherConfigurationTests(APITestCase):
    """Invariants 3 and 5 — one teacher, two academies, two sets of terms."""

    def setUp(self):
        self.teacher = SubTeacherFactory()
        self.a_owner, self.b_owner = UserFactory(), UserFactory()
        self.a, self.b = academy(owner=self.a_owner), academy(owner=self.b_owner)
        self.a_membership = admit(self.a, self.teacher, role=OrganizationRole.TEACHER)
        self.b_membership = admit(self.b, self.teacher, role=OrganizationRole.TEACHER)

        self.a_terms = OrganizationTeacherConfigurationFactory(
            membership=self.a_membership, max_weekly_hours=10, approved=True
        )
        self.b_terms = OrganizationTeacherConfigurationFactory(
            membership=self.b_membership, max_weekly_hours=20, approved=False
        )

    def test_the_same_teacher_is_approved_by_one_academy_and_not_the_other(self):
        self.assertTrue(self.a_terms.approved)
        self.assertFalse(self.b_terms.approved)
        self.assertEqual(self.a_terms.user, self.b_terms.user)

    def test_changing_one_academys_terms_does_not_change_the_others(self):
        self.client.force_authenticate(user=self.a_owner)
        response = self.client.patch(
            configuration_url(self.a, self.a_terms),
            {"max_weekly_hours": 35, "approved": False},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.b_terms.refresh_from_db()
        self.assertEqual(self.b_terms.max_weekly_hours, 20)
        self.assertFalse(self.b_terms.approved)
        self.a_terms.refresh_from_db()
        self.assertEqual(self.a_terms.max_weekly_hours, 35)

    def test_one_academy_cannot_list_anothers_terms(self):
        self.client.force_authenticate(user=self.a_owner)
        response = self.client.get(configurations_url(self.a))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in response.data], [self.a_terms.pk])

    def test_one_academys_owner_cannot_read_the_others_terms(self):
        self.client.force_authenticate(user=self.a_owner)
        self.assertEqual(
            self.client.get(configuration_url(self.a, self.b_terms)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_one_academys_owner_cannot_write_the_others_terms(self):
        self.client.force_authenticate(user=self.a_owner)
        response = self.client.patch(
            configuration_url(self.a, self.b_terms),
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.b_terms.refresh_from_db()
        self.assertFalse(self.b_terms.approved)

    def test_an_owner_of_neither_academy_reaches_neither(self):
        self.client.force_authenticate(user=UserFactory())
        for organization in (self.a, self.b):
            self.assertEqual(
                self.client.get(configurations_url(organization)).status_code,
                status.HTTP_403_FORBIDDEN,
            )

    def test_a_teachers_global_profile_is_not_created_by_either_academy(self):
        """Phase 2 stores academy terms; it does not touch the global TeacherProfile."""
        self.assertFalse(hasattr(self.teacher, "teacher_profile"))


class SuspendedMembershipAccessTests(APITestCase):
    """Invariant 4 — suspension revokes one academy, not the account."""

    def setUp(self):
        self.owner = UserFactory()
        self.a = academy(owner=self.owner)
        self.b = academy(owner=UserFactory())
        self.admin_membership = admit(
            self.b, self.owner, role=OrganizationRole.ADMIN
        )

    def test_an_active_admin_reaches_the_academy(self):
        self.client.force_authenticate(user=self.owner)
        self.assertEqual(
            self.client.get(configurations_url(self.b)).status_code, status.HTTP_200_OK
        )

    def test_a_suspended_admin_does_not(self):
        self.admin_membership.status = MembershipStatus.SUSPENDED
        self.admin_membership.save()

        self.client.force_authenticate(user=self.owner)
        self.assertEqual(
            self.client.get(configurations_url(self.b)).status_code,
            status.HTTP_403_FORBIDDEN,
        )

    def test_suspension_in_one_academy_leaves_the_account_and_the_other_intact(self):
        self.admin_membership.status = MembershipStatus.SUSPENDED
        self.admin_membership.save()

        self.client.force_authenticate(user=self.owner)
        # The global account still works.
        self.assertEqual(
            self.client.get(reverse("accounts:me")).status_code, status.HTTP_200_OK
        )
        # And so does the academy they still belong to.
        self.assertEqual(
            self.client.get(configurations_url(self.a)).status_code, status.HTTP_200_OK
        )

    def test_terms_recorded_for_a_suspended_member_grant_them_no_access(self):
        """An academy may write down its terms; the row is a record, not a key."""
        teacher = SubTeacherFactory()
        admit(
            self.a,
            teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            configurations_url(self.a),
            {"user": teacher.pk, "max_weekly_hours": 5},
            format="json",
        )
        # The membership exists, so the academy may record its terms — what the
        # suspension withdraws is the teacher's own access, which
        # organizations.active_membership() decides and this row does not override.
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.client.force_authenticate(user=teacher)
        self.assertEqual(
            self.client.get(configurations_url(self.a)).status_code,
            status.HTTP_403_FORBIDDEN,
        )


class RoleSeparationTests(APITestCase):
    """Account role and organization role stay separate — the spec's role tests."""

    def test_founding_an_academy_does_not_change_the_founders_account_role(self):
        student = StudentFactory()
        self.client.force_authenticate(user=student)
        response = self.client.post(
            reverse("organizations:organization-create"),
            {"name": "Al-Huda Academy", "slug": "al-huda", "timezone": "Africa/Lagos"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        student.refresh_from_db()
        self.assertEqual(student.role, Role.STUDENT)
        membership = OrganizationMembership.objects.get(user=student)
        self.assertEqual(membership.role, OrganizationRole.OWNER)

    def test_a_teacher_membership_does_not_change_the_account_role(self):
        student = StudentFactory()
        admit(academy(), student, role=OrganizationRole.TEACHER)
        student.refresh_from_db()
        self.assertEqual(student.role, Role.STUDENT)

    def test_a_teacher_membership_does_not_create_a_teacher_profile(self):
        student = StudentFactory()
        admit(academy(), student, role=OrganizationRole.TEACHER)
        self.assertFalse(hasattr(student, "teacher_profile"))
        self.assertFalse(OrganizationTeacherConfiguration.objects.exists())

    def test_an_organization_role_cannot_manufacture_a_teaching_identity(self):
        """A 'teacher' membership is authority here, not a licence to teach."""
        owner = UserFactory()
        organization = academy(owner=owner)
        student = StudentFactory()
        admit(organization, student, role=OrganizationRole.TEACHER)

        self.client.force_authenticate(user=owner)
        response = self.client.post(
            configurations_url(organization),
            {"user": student.pk, "max_weekly_hours": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_lead_teacher_who_owns_their_academy_may_still_teach_in_it(self):
        founder = LeadTeacherFactory()
        organization = academy(owner=founder)

        self.client.force_authenticate(user=founder)
        response = self.client.post(
            configurations_url(organization),
            {"user": founder.pk, "max_weekly_hours": 15},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["membership"], organization.owner_membership.pk
        )


class ClientSuppliedIdentifiersTests(APITestCase):
    """Invariant 6 — the request body is input, never a statement about the caller."""

    def test_a_body_naming_another_academys_member_is_refused(self):
        owner = UserFactory()
        mine = academy(owner=owner)
        theirs = academy()
        their_teacher = SubTeacherFactory()
        admit(theirs, their_teacher, role=OrganizationRole.TEACHER)

        self.client.force_authenticate(user=owner)
        response = self.client.post(
            configurations_url(mine),
            {"user": their_teacher.pk, "max_weekly_hours": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(OrganizationTeacherConfiguration.objects.exists())

    def test_the_url_decides_the_academy_not_the_body(self):
        """Extra keys naming an organization or membership are simply ignored."""
        owner = UserFactory()
        mine = academy(owner=owner)
        theirs = academy()
        teacher = SubTeacherFactory()
        membership = admit(mine, teacher, role=OrganizationRole.TEACHER)

        self.client.force_authenticate(user=owner)
        response = self.client.post(
            configurations_url(mine),
            {
                "user": teacher.pk,
                "max_weekly_hours": 5,
                "organization": theirs.pk,
                "membership": 999999,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["organization"], mine.pk)
        self.assertEqual(response.data["membership"], membership.pk)

    def test_a_parent_cannot_name_a_child_into_an_academy_that_lacks_them(self):
        """There is no request parameter that widens the children listing."""
        organization = academy()
        link = ParentLinkFactory()
        admit(organization, link.parent)

        self.client.force_authenticate(user=link.parent)
        response = self.client.get(
            children_url(organization),
            {"student": link.student.pk, "organization": organization.pk},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_minor_student_is_not_exposed_by_the_signup_code_alone(self):
        """Knowing a code identifies a student; it authorizes no academy (CLAUDE.md)."""
        organization = academy()
        student = MinorStudentFactory()
        admit(organization, student)
        outsider = ParentFactory()
        admit(organization, outsider)

        self.client.force_authenticate(user=outsider)
        response = self.client.get(children_url(organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])
