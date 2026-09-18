from rest_framework import status
from rest_framework.test import APITestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import UserFactory
from organizations.models import StudentEnrollment, EnrollmentStatus, OrganizationRole
from organizations.tests.factories import OrganizationFactory, OrganizationMembershipFactory

class StudentEnrollmentAPITests(APITestCase):
    def setUp(self):
        self.owner = UserFactory()
        self.org = OrganizationFactory()
        OrganizationMembershipFactory(
            user=self.owner, organization=self.org, role=OrganizationRole.OWNER
        )

    def test_list_students(self):
        student1 = UserFactory(role=Role.STUDENT)
        student2 = UserFactory(role=Role.STUDENT)
        
        StudentEnrollment.objects.create(organization=self.org, user=student1)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Handle DRF pagination. Result is likely inside 'results'
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], student1.id)

    def test_cross_tenant_access(self):
        org2 = OrganizationFactory()
        student2 = UserFactory(role=Role.STUDENT)
        StudentEnrollment.objects.create(organization=org2, user=student2)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": org2.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_attach_existing_student(self):
        student = UserFactory(role=Role.STUDENT)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(url, data={"user": student.id})
        
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(StudentEnrollment.objects.filter(organization=self.org, user=student).exists())

    def test_attach_non_student(self):
        parent = UserFactory(role=Role.PARENT)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(url, data={"user": parent.id})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_attach_duplicate_student(self):
        student = UserFactory(role=Role.STUDENT)
        StudentEnrollment.objects.create(organization=self.org, user=student)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(url, data={"user": student.id})
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_enrollment_status(self):
        student = UserFactory(role=Role.STUDENT)
        enrollment = StudentEnrollment.objects.create(organization=self.org, user=student)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-detail", kwargs={"organization_pk": self.org.pk, "pk": enrollment.pk})
        response = self.client.patch(url, data={"status": EnrollmentStatus.INACTIVE})
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, EnrollmentStatus.INACTIVE)

    def test_teacher_cannot_manage_students(self):
        teacher = UserFactory(role=Role.LEAD)
        OrganizationMembershipFactory(
            user=teacher, organization=self.org, role=OrganizationRole.TEACHER
        )
        student = UserFactory(role=Role.STUDENT)
        
        self.client.force_authenticate(user=teacher)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})
        response = self.client.post(url, data={"user": student.id})
        
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_cross_tenant_detail_access(self):
        org2 = OrganizationFactory()
        student = UserFactory(role=Role.STUDENT)
        enrollment2 = StudentEnrollment.objects.create(organization=org2, user=student)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-detail", kwargs={"organization_pk": self.org.pk, "pk": enrollment2.pk})
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_enrollment_with_track_and_level(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        track = TrackFactory(organization=self.org)
        level = LevelFactory(track=track)
        student = UserFactory(role=Role.STUDENT)
        from organizations.models import OrganizationMembership, OrganizationRole; OrganizationMembership.objects.create(organization=self.org, user=student, role=OrganizationRole.STAFF)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", args=[self.org.pk])
        response = self.client.post(url, {"user": student.pk, "track_id": track.pk, "level_id": level.pk})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["track_id"], track.pk)
        self.assertEqual(response.data["level_id"], level.pk)

    def test_update_enrollment_track_and_level(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        track = TrackFactory(organization=self.org)
        level = LevelFactory(track=track)
        student = UserFactory(role=Role.STUDENT)
        from organizations.models import OrganizationMembership, OrganizationRole; OrganizationMembership.objects.create(organization=self.org, user=student, role=OrganizationRole.STAFF)
        enrollment = StudentEnrollment.objects.create(organization=self.org, user=student)
        
        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-detail", args=[self.org.pk, enrollment.pk])
        response = self.client.patch(url, {"track_id": track.pk, "level_id": level.pk})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["track_id"], track.pk)
        self.assertEqual(response.data["level_id"], level.pk)
