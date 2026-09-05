"""The organization-aware account helpers — ``accounts.tenancy``.

These tests are about one question in three shapes: given a global account and an
academy, is this person an active *teacher*, *student* or *parent* of it. Both
halves of the answer are exercised — the account role and the membership's status
— because either one alone is a hole.

``BELONGS`` below is the phase's one honest awkwardness. Phase 1's
``OrganizationRole`` vocabulary is ``owner``/``admin``/``staff``/``teacher``, with
no value meaning "a student of this academy" or "a parent of one", and inventing
those was not Phase 2's decision to make (see tech-debt.md). The rules under test
key off *membership status* plus the account role and never off the organization
role, so which authority row admitted a student is deliberately irrelevant here —
that is what these tests prove, and it is why one constant can stand in for all of
them.
"""

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from accounts.tenancy import (
    active_parent_membership,
    active_student_membership,
    active_teaching_membership,
    children_in_organization,
)
from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import (
    OrganizationFactory,
    OrganizationMembershipFactory,
    academy,
)

#: The organization role used when the only thing that matters is that the person
#: belongs. See the module docstring.
BELONGS = OrganizationRole.STAFF


class ActiveTeachingMembershipTests(TestCase):
    def test_teaching_account_with_an_active_membership_is_a_teacher_here(self):
        teacher, organization = SubTeacherFactory(), OrganizationFactory()
        membership = OrganizationMembershipFactory(
            organization=organization, user=teacher, role=OrganizationRole.TEACHER
        )
        self.assertEqual(
            active_teaching_membership(user=teacher, organization=organization),
            membership,
        )

    def test_a_suspended_teacher_is_not_a_teacher_here(self):
        teacher, organization = SubTeacherFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=organization,
            user=teacher,
            role=OrganizationRole.TEACHER,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertIsNone(
            active_teaching_membership(user=teacher, organization=organization)
        )

    def test_a_teaching_account_with_no_membership_is_not_a_teacher_here(self):
        self.assertIsNone(
            active_teaching_membership(
                user=SubTeacherFactory(), organization=OrganizationFactory()
            )
        )

    def test_a_student_account_is_not_a_teacher_even_with_a_teacher_membership(self):
        """The account role is half the answer. A membership cannot supply the other."""
        student, organization = StudentFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=organization, user=student, role=OrganizationRole.TEACHER
        )
        self.assertIsNone(
            active_teaching_membership(user=student, organization=organization)
        )

    def test_a_lead_who_founded_the_academy_teaches_in_it(self):
        """The owner of an academy may also be its teacher — the single-academy shape."""
        founder = LeadTeacherFactory()
        organization = academy(owner=founder)
        membership = active_teaching_membership(
            user=founder, organization=organization
        )
        self.assertIsNotNone(membership)
        self.assertEqual(membership.role, OrganizationRole.OWNER)

    def test_an_anonymous_caller_is_nobody_here(self):
        self.assertIsNone(
            active_teaching_membership(
                user=AnonymousUser(), organization=OrganizationFactory()
            )
        )

    def test_a_membership_in_another_academy_does_not_answer_for_this_one(self):
        teacher = SubTeacherFactory()
        theirs, other = OrganizationFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=theirs, user=teacher, role=OrganizationRole.TEACHER
        )
        self.assertIsNone(active_teaching_membership(user=teacher, organization=other))


class ActiveStudentAndParentMembershipTests(TestCase):
    def test_student_account_with_an_active_membership_is_a_student_here(self):
        student, organization = StudentFactory(), OrganizationFactory()
        membership = OrganizationMembershipFactory(
            organization=organization, user=student, role=BELONGS
        )
        self.assertEqual(
            active_student_membership(user=student, organization=organization),
            membership,
        )

    def test_a_parent_account_is_not_a_student_here(self):
        parent, organization = ParentFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=organization, user=parent, role=BELONGS
        )
        self.assertIsNone(
            active_student_membership(user=parent, organization=organization)
        )

    def test_parent_account_with_an_active_membership_is_a_parent_here(self):
        parent, organization = ParentFactory(), OrganizationFactory()
        membership = OrganizationMembershipFactory(
            organization=organization, user=parent, role=BELONGS
        )
        self.assertEqual(
            active_parent_membership(user=parent, organization=organization), membership
        )

    def test_a_suspended_parent_is_not_a_parent_here(self):
        parent, organization = ParentFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=organization,
            user=parent,
            role=BELONGS,
            status=MembershipStatus.SUSPENDED,
        )
        self.assertIsNone(
            active_parent_membership(user=parent, organization=organization)
        )

    def test_a_student_account_is_not_a_parent_here(self):
        student, organization = StudentFactory(), OrganizationFactory()
        OrganizationMembershipFactory(
            organization=organization, user=student, role=BELONGS
        )
        self.assertIsNone(
            active_parent_membership(user=student, organization=organization)
        )


class ChildrenInOrganizationTests(TestCase):
    """``parent active here AND student active here`` — the phase's isolation rule."""

    def setUp(self):
        self.organization = OrganizationFactory()
        self.link = ParentLinkFactory()
        self.parent, self.student = self.link.parent, self.link.student

    def admit(self, user, organization=None, **kwargs):
        return OrganizationMembershipFactory(
            organization=organization or self.organization,
            user=user,
            role=BELONGS,
            **kwargs,
        )

    def test_a_child_admitted_alongside_their_parent_is_listed(self):
        self.admit(self.parent)
        self.admit(self.student)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [self.student],
        )

    def test_a_child_the_academy_has_not_admitted_is_absent(self):
        """The global ParentLink exists. It is not access to this academy."""
        self.admit(self.parent)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [],
        )

    def test_a_suspended_child_is_absent(self):
        self.admit(self.parent)
        self.admit(self.student, status=MembershipStatus.SUSPENDED)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [],
        )

    def test_a_suspended_parent_sees_nothing_here(self):
        self.admit(self.parent, status=MembershipStatus.SUSPENDED)
        self.admit(self.student)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [],
        )

    def test_a_parent_who_is_not_a_member_sees_nothing_here(self):
        self.admit(self.student)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [],
        )

    def test_an_unlinked_student_of_the_same_academy_is_not_a_child(self):
        self.admit(self.parent)
        self.admit(self.student)
        self.admit(MinorStudentFactory())
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization
            )),
            [self.student],
        )

    def test_one_parent_in_two_academies_gets_two_different_answers(self):
        """The same ParentLink rows, filtered by who each academy has admitted."""
        here, there = self.organization, OrganizationFactory()
        second_child = ParentLinkFactory(parent=self.parent).student
        self.admit(self.parent, organization=here)
        self.admit(self.parent, organization=there)
        self.admit(self.student, organization=here)
        self.admit(second_child, organization=there)

        self.assertEqual(
            list(children_in_organization(parent=self.parent, organization=here)),
            [self.student],
        )
        self.assertEqual(
            list(children_in_organization(parent=self.parent, organization=there)),
            [second_child],
        )

    def test_a_bare_organization_id_is_accepted(self):
        """Views hold the URL's id, not the object."""
        self.admit(self.parent)
        self.admit(self.student)
        self.assertEqual(
            list(children_in_organization(
                parent=self.parent, organization=self.organization.pk
            )),
            [self.student],
        )

    def test_a_non_parent_account_has_no_children_here(self):
        teacher = SubTeacherFactory()
        self.admit(teacher)
        self.assertEqual(
            list(children_in_organization(
                parent=teacher, organization=self.organization
            )),
            [],
        )
