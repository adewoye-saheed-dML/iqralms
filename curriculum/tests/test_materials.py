from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.tests.factories import StudentFactory, UserFactory
from curriculum.models import LearningMaterial, MaterialType
from curriculum.tests.factories import LevelFactory, TrackFactory, admit
from organizations.models import OrganizationRole
from organizations.tests.factories import OrganizationFactory, academy


class LearningMaterialAPITests(APITestCase):
    def setUp(self):
        self.owner = UserFactory(role=Role.LEAD)
        self.organization = academy(owner=self.owner, name="Iqra Academy")
        self.other_org = OrganizationFactory(name="Al-Azhar Academy")

        self.teacher = UserFactory(role=Role.SUB)
        admit(self.teacher, self.organization, role=OrganizationRole.TEACHER)

        self.student = StudentFactory()
        admit(self.student, self.organization)

        self.outsider = StudentFactory()
        admit(self.outsider, self.other_org)

        self.track = TrackFactory(organization=self.organization, name="Quran Recitation")
        self.level1 = LevelFactory(track=self.track, name="Level 1 - Qaida")
        self.level2 = LevelFactory(track=self.track, name="Level 2 - Surah Al-Fatihah")

        self.url = reverse("curriculum:academy-material-list", kwargs={"organization_pk": self.organization.pk})

    def test_owner_can_upload_pdf_material(self):
        self.client.force_authenticate(user=self.owner)
        pdf_file = SimpleUploadedFile("tajweed_guide.pdf", b"%PDF-1.4 test content", content_type="application/pdf")
        payload = {
            "title": "Tajweed Rules Handbook",
            "description": "Essential pronunciation rules",
            "material_type": MaterialType.PDF.value,
            "track": self.track.pk,
            "level": self.level1.pk,
            "file": pdf_file,
        }
        response = self.client.post(self.url, payload, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Tajweed Rules Handbook")
        self.assertEqual(response.data["track_name"], "Quran Recitation")
        self.assertEqual(response.data["level_name"], "Level 1 - Qaida")
        self.assertIsNotNone(response.data["file_url"])

    def test_owner_can_create_web_link_or_text_material(self):
        self.client.force_authenticate(user=self.owner)
        payload = {
            "title": "Surah Al-Baqarah Ayah 1-5 Reading Notes",
            "material_type": MaterialType.TEXT,
            "content_text": "الم * ذَٰلِكَ الْكِتَابُ لَا رَيْبَ ۛ فِيهِ",
            "track": self.track.pk,
        }
        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["title"], "Surah Al-Baqarah Ayah 1-5 Reading Notes")
        self.assertEqual(response.data["content_text"], "الم * ذَٰلِكَ الْكِتَابُ لَا رَيْبَ ۛ فِيهِ")

    def test_student_and_teacher_can_list_and_read_materials(self):
        # Create an academy-wide material and a level-specific material
        m1 = LearningMaterial.objects.create(
            organization=self.organization,
            title="General Academy Syllabus",
            material_type=MaterialType.PDF,
            uploaded_by=self.owner,
        )
        m2 = LearningMaterial.objects.create(
            organization=self.organization,
            track=self.track,
            level=self.level1,
            title="Qaida Lesson 1",
            material_type=MaterialType.BOOK,
            uploaded_by=self.owner,
        )

        # Student listing materials
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

        # Student filtering by level
        response = self.client.get(f"{self.url}?level_id={self.level1.pk}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = [item["title"] for item in response.data]
        self.assertIn("Qaida Lesson 1", titles)
        self.assertIn("General Academy Syllabus", titles)

    def test_student_cannot_upload_materials(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, {"title": "Student Upload"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_outsider_is_forbidden(self):
        self.client.force_authenticate(user=self.outsider)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_delete_material(self):
        m = LearningMaterial.objects.create(
            organization=self.organization,
            title="Old Worksheet",
            material_type=MaterialType.WORKSHEET,
            uploaded_by=self.owner,
        )
        detail_url = reverse(
            "curriculum:academy-material-detail",
            kwargs={"organization_pk": self.organization.pk, "pk": m.pk},
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(LearningMaterial.objects.filter(pk=m.pk).exists())

    def test_download_material_file_with_query_param_token(self):
        from rest_framework.authtoken.models import Token

        pdf_file = SimpleUploadedFile("guide.pdf", b"%PDF-1.4 sample content", content_type="application/pdf")
        m = LearningMaterial.objects.create(
            organization=self.organization,
            title="Syllabus Guide",
            material_type=MaterialType.PDF,
            file=pdf_file,
            uploaded_by=self.owner,
        )
        file_url = reverse(
            "curriculum:academy-material-file",
            kwargs={"organization_pk": self.organization.pk, "pk": m.pk},
        )

        # Unauthenticated request without token -> 401
        self.client.force_authenticate(user=None)
        res_unauth = self.client.get(file_url)
        self.assertEqual(res_unauth.status_code, status.HTTP_401_UNAUTHORIZED)

        # Request with valid query param token -> 200 FileResponse
        token, _ = Token.objects.get_or_create(user=self.student)
        res_token = self.client.get(f"{file_url}?token={token.key}")
        self.assertEqual(res_token.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(res_token.streaming_content), b"%PDF-1.4 sample content")
