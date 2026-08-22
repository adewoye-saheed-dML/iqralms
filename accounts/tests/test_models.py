"""Model-layer tests for the accounts app.

Acceptance criteria from specs/phase-1-accounts.md covered here: 2 (roles
stick), 3 (minor without parent link is not fully active), 5 (parent cannot
hold a TeacherProfile).
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone as dj_timezone

from accounts.models import ParentLink, Role, TeacherProfile, User

from .factories import (
    LeadTeacherFactory,
    LeadTeacherProfileFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    TeacherProfileFactory,
    UserFactory,
)


class UserModelTests(TestCase):
    def test_one_user_of_each_role_keeps_its_role(self):
        """Acceptance criterion 2."""
        factories = {
            Role.LEAD: LeadTeacherFactory,
            Role.SUB: SubTeacherFactory,
            Role.STUDENT: StudentFactory,
            Role.PARENT: ParentFactory,
        }
        for role, factory_cls in factories.items():
            with self.subTest(role=role):
                user = factory_cls()
                user.refresh_from_db()
                self.assertEqual(user.role, role)

        self.assertEqual(User.objects.count(), len(factories))

    def test_timezone_must_be_a_real_iana_zone(self):
        user = UserFactory.build(timezone="Mars/Olympus_Mons")
        with self.assertRaises(ValidationError) as ctx:
            user.full_clean(exclude=["signup_code"])
        self.assertIn("timezone", ctx.exception.message_dict)

    def test_valid_iana_timezone_passes_validation(self):
        user = UserFactory.build(timezone="America/New_York")
        user.full_clean(exclude=["signup_code"])  # must not raise

    def test_signup_code_is_generated_and_unique(self):
        first, second = StudentFactory(), StudentFactory()
        self.assertEqual(len(first.signup_code), 8)
        self.assertNotEqual(first.signup_code, second.signup_code)

    def test_explicit_signup_code_is_not_overwritten(self):
        user = StudentFactory(signup_code="ABCD2345")
        user.refresh_from_db()
        self.assertEqual(user.signup_code, "ABCD2345")

    def test_is_minor_computed_from_date_of_birth(self):
        today = date(2026, 8, 22)
        cases = [
            (None, False),
            (date(2008, 8, 22), False),  # exactly 18 today
            (date(2008, 8, 23), True),  # 18 tomorrow
            (date(2016, 1, 1), True),
            (date(1990, 5, 5), False),
        ]
        for dob, expected in cases:
            with self.subTest(dob=dob):
                self.assertIs(User.minor_from_date_of_birth(dob, today=today), expected)

    def test_is_minor_defaults_to_today_when_no_date_given(self):
        dob = dj_timezone.localdate() - timedelta(days=365 * 9)
        self.assertTrue(User.minor_from_date_of_birth(dob))

    def test_is_teacher_only_for_lead_and_sub(self):
        self.assertTrue(LeadTeacherFactory().is_teacher)
        self.assertTrue(SubTeacherFactory().is_teacher)
        self.assertFalse(StudentFactory().is_teacher)
        self.assertFalse(ParentFactory().is_teacher)


class IsFullyActiveTests(TestCase):
    def test_minor_student_without_parent_link_is_not_fully_active(self):
        """Acceptance criterion 3."""
        student = MinorStudentFactory()
        self.assertTrue(student.is_minor)
        self.assertFalse(student.parent_links.exists())
        self.assertFalse(student.is_fully_active)
        # Django-level is_active is untouched: the account can still log in.
        self.assertTrue(student.is_active)

    def test_minor_student_is_fully_active_once_a_parent_is_linked(self):
        student = MinorStudentFactory()
        ParentLinkFactory(student=student)
        student.refresh_from_db()
        self.assertTrue(student.is_fully_active)

    def test_adult_student_is_fully_active_without_a_parent_link(self):
        self.assertTrue(StudentFactory().is_fully_active)

    def test_non_student_roles_are_always_fully_active(self):
        for user in (LeadTeacherFactory(), SubTeacherFactory(), ParentFactory()):
            with self.subTest(role=user.role):
                self.assertTrue(user.is_fully_active)


class ParentLinkModelTests(TestCase):
    def test_parent_link_can_be_created(self):
        parent, student = ParentFactory(), MinorStudentFactory()
        link = ParentLink.objects.create(parent=parent, student=student)
        link.refresh_from_db()
        self.assertEqual(link.parent, parent)
        self.assertEqual(link.student, student)

    def test_student_may_have_several_parents(self):
        student = MinorStudentFactory()
        ParentLinkFactory(parent=ParentFactory(), student=student)
        ParentLinkFactory(parent=ParentFactory(), student=student)
        self.assertEqual(student.parent_links.count(), 2)

    def test_parent_may_have_several_children(self):
        parent = ParentFactory()
        ParentLinkFactory(parent=parent, student=MinorStudentFactory())
        ParentLinkFactory(parent=parent, student=MinorStudentFactory())
        self.assertEqual(parent.child_links.count(), 2)

    def test_parent_side_must_have_role_parent(self):
        with self.assertRaises(ValidationError) as ctx:
            ParentLink.objects.create(parent=StudentFactory(), student=MinorStudentFactory())
        self.assertIn("parent", ctx.exception.message_dict)

    def test_student_side_must_have_role_student(self):
        """Also covers the rule that a parent cannot be the student side."""
        with self.assertRaises(ValidationError) as ctx:
            ParentLink.objects.create(parent=ParentFactory(), student=ParentFactory())
        self.assertIn("student", ctx.exception.message_dict)

    def test_user_cannot_be_linked_to_themselves(self):
        parent = ParentFactory()
        with self.assertRaises(ValidationError):
            ParentLink.objects.create(parent=parent, student=parent)

    def test_duplicate_link_is_rejected(self):
        link = ParentLinkFactory()
        with self.assertRaises(ValidationError):
            ParentLink.objects.create(parent=link.parent, student=link.student)
        self.assertEqual(ParentLink.objects.count(), 1)


class TeacherProfileModelTests(TestCase):
    def test_sub_teacher_profile_can_be_created_and_defaults_unapproved(self):
        profile = TeacherProfileFactory()
        profile.refresh_from_db()
        self.assertEqual(profile.max_weekly_hours, 20)
        self.assertEqual(profile.hourly_payout_rate, Decimal("12.50"))
        self.assertFalse(profile.is_lead)
        self.assertFalse(profile.approved)

    def test_lead_teacher_profile_needs_no_payout_rate(self):
        profile = LeadTeacherProfileFactory()
        profile.refresh_from_db()
        self.assertTrue(profile.is_lead)
        self.assertIsNone(profile.hourly_payout_rate)

    def test_parent_cannot_have_a_teacher_profile(self):
        """Acceptance criterion 5."""
        parent = ParentFactory()
        with self.assertRaises(ValidationError) as ctx:
            TeacherProfile.objects.create(user=parent, max_weekly_hours=10)
        self.assertIn("user", ctx.exception.message_dict)
        self.assertFalse(TeacherProfile.objects.filter(user=parent).exists())

    def test_student_cannot_have_a_teacher_profile(self):
        with self.assertRaises(ValidationError) as ctx:
            TeacherProfile.objects.create(user=StudentFactory(), max_weekly_hours=10)
        self.assertIn("user", ctx.exception.message_dict)

    def test_is_lead_must_agree_with_user_role(self):
        with self.assertRaises(ValidationError) as ctx:
            TeacherProfile.objects.create(
                user=SubTeacherFactory(), max_weekly_hours=10, is_lead=True
            )
        self.assertIn("is_lead", ctx.exception.message_dict)

        with self.assertRaises(ValidationError) as ctx:
            TeacherProfile.objects.create(
                user=LeadTeacherFactory(), max_weekly_hours=10, is_lead=False
            )
        self.assertIn("is_lead", ctx.exception.message_dict)

    def test_max_weekly_hours_must_be_positive(self):
        with self.assertRaises(ValidationError) as ctx:
            TeacherProfile.objects.create(user=SubTeacherFactory(), max_weekly_hours=0)
        self.assertIn("max_weekly_hours", ctx.exception.message_dict)
