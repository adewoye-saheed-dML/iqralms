"""API tests for the accounts app.

Every endpoint gets a happy path and at least one failure case, per CLAUDE.md.
Acceptance criterion 4 (a linked parent can see their child) is covered by
ParentLinkAPITests.test_parent_sees_linked_child_after_creating_link.
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import ParentLink, Role, User

from .factories import (
    DEFAULT_PASSWORD,
    LeadTeacherProfileFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)

STRONG_PASSWORD = "Tilawah-Api-Pass-91"


def registration_payload(**overrides):
    payload = {
        "username": "newstudent",
        "email": "newstudent@example.com",
        "password": STRONG_PASSWORD,
        "role": Role.STUDENT.value,
        "timezone": "Africa/Lagos",
    }
    payload.update(overrides)
    return payload


class RegisterAPITests(APITestCase):
    url = reverse("rest_register")

    def test_adult_student_registers_as_active(self):
        adult_dob = (dj_timezone.localdate() - timedelta(days=365 * 30)).isoformat()
        response = self.client.post(
            self.url, registration_payload(date_of_birth=adult_dob), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "active")
        self.assertTrue(response.data["user"]["is_fully_active"])
        self.assertFalse(response.data["user"]["is_minor"])

        user = User.objects.get(username="newstudent")
        self.assertEqual(user.role, Role.STUDENT)
        self.assertEqual(user.timezone, "Africa/Lagos")
        self.assertTrue(user.check_password(STRONG_PASSWORD))
        self.assertNotIn("password", response.data["user"])

    def test_minor_student_registration_reports_pending_parent_link(self):
        """Acceptance criterion 3, at the API layer."""
        minor_dob = (dj_timezone.localdate() - timedelta(days=365 * 11)).isoformat()
        response = self.client.post(
            self.url, registration_payload(date_of_birth=minor_dob), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "pending_parent_link")
        self.assertTrue(response.data["user"]["is_minor"])
        self.assertFalse(response.data["user"]["is_fully_active"])
        # The student is told the code a parent will need.
        self.assertIn(response.data["user"]["signup_code"], response.data["detail"])

    def test_parent_registers_without_date_of_birth(self):
        response = self.client.post(
            self.url,
            registration_payload(
                username="newparent", email="p@example.com", role=Role.PARENT.value
            ),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "active")
        # Signup codes are a student-linking device; not surfaced for parents.
        self.assertNotIn("signup_code", response.data["user"])

    def test_timezone_is_required(self):
        payload = registration_payload()
        payload.pop("timezone")
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("timezone", response.data)

    def test_invalid_timezone_is_rejected(self):
        response = self.client.post(
            self.url, registration_payload(timezone="Not/AZone"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("timezone", response.data)

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            self.url, registration_payload(password="123"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("password", response.data)

    def test_duplicate_username_is_rejected(self):
        StudentFactory(username="taken")
        response = self.client.post(
            self.url, registration_payload(username="taken"), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("username", response.data)

    def test_future_date_of_birth_is_rejected(self):
        tomorrow = (dj_timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.client.post(
            self.url, registration_payload(date_of_birth=tomorrow), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_of_birth", response.data)

    def test_lead_role_cannot_be_self_registered(self):
        """The single lead account is created via createsuperuser/admin."""
        response = self.client.post(
            self.url, registration_payload(role=Role.LEAD.value), format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("role", response.data)
        self.assertFalse(User.objects.filter(role=Role.LEAD).exists())

    def test_client_cannot_forge_is_minor(self):
        minor_dob = (dj_timezone.localdate() - timedelta(days=365 * 9)).isoformat()
        response = self.client.post(
            self.url,
            registration_payload(date_of_birth=minor_dob, is_minor=False),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.get(username="newstudent").is_minor)


class LoginAPITests(APITestCase):
    url = reverse("rest_login")

    def test_login_returns_a_token(self):
        user = StudentFactory()
        response = self.client.post(
            self.url,
            {"username": user.username, "password": DEFAULT_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("key", response.data)
        self.assertTrue(response.data["key"])

    def test_login_with_wrong_password_is_rejected(self):
        user = StudentFactory()
        response = self.client.post(
            self.url,
            {"username": user.username, "password": "definitely-not-it"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_token_authenticates_a_subsequent_request(self):
        user = StudentFactory()
        token = self.client.post(
            self.url,
            {"username": user.username, "password": DEFAULT_PASSWORD},
            format="json",
        ).data["key"]

        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], user.username)


class MeAPITests(APITestCase):
    url = reverse("accounts:me")

    def test_returns_role_and_timezone_for_logged_in_user(self):
        user = StudentFactory(timezone="Europe/London")
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], user.id)
        self.assertEqual(response.data["role"], Role.STUDENT.value)
        self.assertEqual(response.data["timezone"], "Europe/London")
        self.assertTrue(response.data["is_fully_active"])

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_teacher_sees_their_teacher_profile(self):
        profile = LeadTeacherProfileFactory()
        self.client.force_authenticate(user=profile.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["role"], Role.LEAD.value)
        self.assertEqual(
            response.data["teacher_profile"]["max_weekly_hours"],
            profile.max_weekly_hours,
        )
        self.assertTrue(response.data["teacher_profile"]["is_lead"])
        self.assertTrue(response.data["teacher_profile"]["approved"])

    def test_non_teacher_has_no_teacher_profile_key(self):
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.get(self.url)
        self.assertNotIn("teacher_profile", response.data)

    def test_minor_student_without_link_is_not_fully_active(self):
        self.client.force_authenticate(user=MinorStudentFactory())
        response = self.client.get(self.url)
        self.assertFalse(response.data["is_fully_active"])
        self.assertTrue(response.data["is_minor"])

    def test_date_joined_is_rendered_in_the_users_own_timezone(self):
        """Stored UTC, converted at the serializer layer (CLAUDE.md)."""
        user = StudentFactory(timezone="Africa/Lagos")  # UTC+01:00, no DST
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        self.assertTrue(response.data["date_joined_local"].endswith("+01:00"))
        self.assertTrue(response.data["date_joined"].endswith("Z"))


class ParentLinkAPITests(APITestCase):
    url = reverse("accounts:parent-link-create")

    def test_parent_sees_linked_child_after_creating_link(self):
        """Acceptance criterion 4."""
        parent, student = ParentFactory(), MinorStudentFactory()
        self.client.force_authenticate(user=parent)

        response = self.client.post(
            self.url, {"student_code": student.signup_code}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["student"]["id"], student.id)
        self.assertTrue(ParentLink.objects.filter(parent=parent, student=student).exists())

        children = self.client.get(reverse("accounts:my-children"))
        self.assertEqual(children.status_code, status.HTTP_200_OK)
        self.assertEqual([c["id"] for c in children.data], [student.id])
        # The link completes the minor's account.
        self.assertTrue(children.data[0]["is_fully_active"])

    def test_code_is_accepted_with_dashes_and_lowercase(self):
        parent, student = ParentFactory(), MinorStudentFactory()
        self.client.force_authenticate(user=parent)
        messy = f"{student.signup_code[:4].lower()}-{student.signup_code[4:].lower()}"

        response = self.client.post(self.url, {"student_code": messy}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ParentLink.objects.filter(parent=parent, student=student).exists())

    def test_unknown_code_returns_404(self):
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.post(self.url, {"student_code": "ZZZZ9999"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ParentLink.objects.exists())

    def test_code_belonging_to_a_non_student_returns_404(self):
        """A parent's own code must not be linkable as a child."""
        parent, other_parent = ParentFactory(), ParentFactory()
        self.client.force_authenticate(user=parent)
        response = self.client.post(
            self.url, {"student_code": other_parent.signup_code}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ParentLink.objects.exists())

    def test_non_parent_cannot_create_a_link(self):
        student = MinorStudentFactory()
        self.client.force_authenticate(user=SubTeacherFactory())
        response = self.client.post(
            self.url, {"student_code": student.signup_code}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ParentLink.objects.exists())

    def test_duplicate_link_returns_400(self):
        link = ParentLinkFactory()
        self.client.force_authenticate(user=link.parent)
        response = self.client.post(
            self.url, {"student_code": link.student.signup_code}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(ParentLink.objects.count(), 1)

    def test_requires_authentication(self):
        student = MinorStudentFactory()
        response = self.client.post(
            self.url, {"student_code": student.signup_code}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MyChildrenAPITests(APITestCase):
    url = reverse("accounts:my-children")

    def test_lists_only_the_calling_parents_children(self):
        parent = ParentFactory()
        mine = [ParentLinkFactory(parent=parent).student for _ in range(2)]
        ParentLinkFactory()  # someone else's child

        self.client.force_authenticate(user=parent)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(c["id"] for c in response.data), sorted(s.id for s in mine)
        )
        # Another user's signup code is never exposed.
        self.assertNotIn("signup_code", response.data[0])

    def test_parent_with_no_children_gets_an_empty_list(self):
        self.client.force_authenticate(user=ParentFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_non_parent_is_forbidden(self):
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
