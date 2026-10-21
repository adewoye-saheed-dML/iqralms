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

    def test_create_and_update_enrollment_with_teacher(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        from curriculum.models import TeacherTrack
        track = TrackFactory(organization=self.org)
        level = LevelFactory(track=track)
        student = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB, username="ustadh_ali", first_name="Ali", last_name="Hassan")
        mem = OrganizationMembershipFactory(
            organization=self.org, user=teacher, role=OrganizationRole.TEACHER
        )
        TeacherTrack.objects.create(membership=mem, track=track, active=True)

        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", args=[self.org.pk])
        response = self.client.post(url, {
            "user": student.pk,
            "track_id": track.pk,
            "level_id": level.pk,
            "teacher_id": teacher.pk,
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["teacher_id"], teacher.pk)
        self.assertEqual(response.data["teacher_name"], "Ali Hassan")

        # Now test updating (unassigning teacher)
        detail_url = reverse("organizations:student-detail", args=[self.org.pk, response.data["id"]])
        patch_res = self.client.patch(detail_url, {"teacher_id": None}, format="json")
        self.assertEqual(patch_res.status_code, status.HTTP_200_OK)
        self.assertIsNone(patch_res.data["teacher_id"])
        self.assertIsNone(patch_res.data["teacher_name"])

    def test_teacher_mine_endpoint_includes_assigned_students(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        from curriculum.models import TeacherTrack
        track = TrackFactory(organization=self.org)
        level = LevelFactory(track=track)
        student1 = UserFactory(role=Role.STUDENT)
        student2 = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB)
        mem = OrganizationMembershipFactory(
            organization=self.org, user=teacher, role=OrganizationRole.TEACHER
        )
        TeacherTrack.objects.create(membership=mem, track=track, active=True)

        # student1 is assigned to teacher, student2 is not
        StudentEnrollment.objects.create(
            organization=self.org, user=student1, track=track, level=level, teacher=teacher
        )
        StudentEnrollment.objects.create(
            organization=self.org, user=student2, track=track, level=level
        )

        self.client.force_authenticate(user=teacher)
        mine_url = reverse("organizations:student-mine-list", args=[self.org.pk])
        response = self.client.get(mine_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], student1.id)
        self.assertEqual(results[0]["teacher_id"], teacher.id)

    def test_student_can_fetch_own_enrollment(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        from curriculum.models import TeacherTrack
        track = TrackFactory(organization=self.org)
        level = LevelFactory(track=track)
        student = UserFactory(role=Role.STUDENT)
        teacher = UserFactory(role=Role.SUB, first_name="Bilal", last_name="Khan")
        teacher_mem = OrganizationMembershipFactory(
            organization=self.org, user=teacher, role=OrganizationRole.TEACHER
        )
        TeacherTrack.objects.create(membership=teacher_mem, track=track, active=True)
        OrganizationMembershipFactory(
            organization=self.org, user=student, role=OrganizationRole.STUDENT
        )
        StudentEnrollment.objects.create(
            organization=self.org, user=student, track=track, level=level, teacher=teacher
        )

        self.client.force_authenticate(user=student)
        mine_url = reverse("organizations:student-mine-list", args=[self.org.pk])
        response = self.client.get(mine_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_id"], student.id)
        self.assertEqual(results[0]["teacher_id"], teacher.id)
        self.assertEqual(results[0]["teacher_name"], "Bilal Khan")

    def test_student_can_enroll_in_multiple_subjects_with_different_teachers(self):
        from curriculum.tests.factories import TrackFactory, LevelFactory
        from curriculum.models import TeacherTrack

        # Setup 2 subjects / tracks
        quran_track = TrackFactory(organization=self.org, name="Quran Recitation", slug="quran-recitation")
        quran_level = LevelFactory(track=quran_track, name="Juz Amma", order=1)

        arabic_track = TrackFactory(organization=self.org, name="Arabic Language", slug="arabic-language")
        arabic_level = LevelFactory(track=arabic_track, name="Madinah Book 1", order=1)

        # Setup 2 teachers authorized for their respective tracks
        teacher_quran = UserFactory(role=Role.SUB, first_name="Ahmad", last_name="Reciter")
        tq_mem = OrganizationMembershipFactory(organization=self.org, user=teacher_quran, role=OrganizationRole.TEACHER)
        TeacherTrack.objects.create(membership=tq_mem, track=quran_track, active=True)

        teacher_arabic = UserFactory(role=Role.SUB, first_name="Fatima", last_name="Linguist")
        ta_mem = OrganizationMembershipFactory(organization=self.org, user=teacher_arabic, role=OrganizationRole.TEACHER)
        TeacherTrack.objects.create(membership=ta_mem, track=arabic_track, active=True)

        student = UserFactory(role=Role.STUDENT, username="zayd_learner")

        self.client.force_authenticate(user=self.owner)
        url = reverse("organizations:student-list", kwargs={"organization_pk": self.org.pk})

        # 1. Enroll in Subject 1 (Quran)
        resp1 = self.client.post(
            url,
            data={
                "user": student.id,
                "track_id": quran_track.id,
                "level_id": quran_level.id,
                "teacher_id": teacher_quran.id,
            },
        )
        self.assertEqual(resp1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp1.data["track_id"], quran_track.id)
        self.assertEqual(resp1.data["teacher_name"], "Ahmad Reciter")

        # 2. Enroll in Subject 2 (Arabic) with Teacher 2
        resp2 = self.client.post(
            url,
            data={
                "user": student.id,
                "track_id": arabic_track.id,
                "level_id": arabic_level.id,
                "teacher_id": teacher_arabic.id,
            },
        )
        self.assertEqual(resp2.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp2.data["track_id"], arabic_track.id)
        self.assertEqual(resp2.data["teacher_name"], "Fatima Linguist")

        # Verify DB has 2 active enrollments for this student
        student_enrollments = StudentEnrollment.objects.filter(organization=self.org, user=student)
        self.assertEqual(student_enrollments.count(), 2)

        # 3. Attempting to enroll in Quran track AGAIN should fail with 400
        resp_dup = self.client.post(
            url,
            data={
                "user": student.id,
                "track_id": quran_track.id,
                "level_id": quran_level.id,
            },
        )
        self.assertEqual(resp_dup.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Student sees both enrollments when listing their enrollments
        self.client.force_authenticate(user=student)
        mine_url = reverse("organizations:student-mine-list", args=[self.org.pk])
        resp_mine = self.client.get(mine_url)
        self.assertEqual(resp_mine.status_code, status.HTTP_200_OK)
        mine_results = resp_mine.data["results"] if "results" in resp_mine.data else resp_mine.data
        self.assertEqual(len(mine_results), 2)
        track_ids = {r["track_id"] for r in mine_results}
        self.assertEqual(track_ids, {quran_track.id, arabic_track.id})

        # 5. Teacher 1 (teacher_quran) ONLY sees the Quran enrollment, not the Arabic enrollment
        self.client.force_authenticate(user=teacher_quran)
        resp_t1 = self.client.get(mine_url)
        self.assertEqual(resp_t1.status_code, status.HTTP_200_OK)
        t1_results = resp_t1.data["results"] if "results" in resp_t1.data else resp_t1.data
        self.assertEqual(len(t1_results), 1)
        self.assertEqual(t1_results[0]["track_id"], quran_track.id)
        self.assertEqual(t1_results[0]["user_id"], student.id)

        # 6. Teacher 2 (teacher_arabic) ONLY sees the Arabic enrollment, not the Quran enrollment
        self.client.force_authenticate(user=teacher_arabic)
        resp_t2 = self.client.get(mine_url)
        self.assertEqual(resp_t2.status_code, status.HTTP_200_OK)
        t2_results = resp_t2.data["results"] if "results" in resp_t2.data else resp_t2.data
        self.assertEqual(len(t2_results), 1)
        self.assertEqual(t2_results[0]["track_id"], arabic_track.id)
        self.assertEqual(t2_results[0]["user_id"], student.id)

        # 7. Multiple teachers in same track isolation:
        # Create student 2 in Quran track assigned to teacher_quran_2
        teacher_quran_2 = UserFactory(role=Role.SUB, first_name="Bilal", last_name="Teacher")
        tq2_mem = OrganizationMembershipFactory(
            user=teacher_quran_2, organization=self.org, role=OrganizationRole.TEACHER
        )
        TeacherTrack.objects.create(membership=tq2_mem, track=quran_track, active=True)
        student2 = UserFactory(role=Role.STUDENT, first_name="Zayd", last_name="Learner")
        self.client.force_authenticate(user=self.owner)
        resp_s2 = self.client.post(
            url,
            data={
                "user": student2.id,
                "track_id": quran_track.id,
                "level_id": quran_level.id,
                "teacher_id": teacher_quran_2.id,
            },
        )
        self.assertEqual(resp_s2.status_code, status.HTTP_201_CREATED)

        # Teacher 1 (teacher_quran) in Quran track must ONLY see student 1, NEVER student 2
        self.client.force_authenticate(user=teacher_quran)
        resp_t1_quran = self.client.get(f"{mine_url}?track_id={quran_track.id}")
        self.assertEqual(resp_t1_quran.status_code, status.HTTP_200_OK)
        t1_quran_results = resp_t1_quran.data["results"] if "results" in resp_t1_quran.data else resp_t1_quran.data
        self.assertEqual(len(t1_quran_results), 1)
        self.assertEqual(t1_quran_results[0]["user_id"], student.id)
        self.assertNotEqual(t1_quran_results[0]["user_id"], student2.id)

        # Teacher 2 (teacher_quran_2) in Quran track must ONLY see student 2, NEVER student 1
        self.client.force_authenticate(user=teacher_quran_2)
        resp_t2_quran = self.client.get(f"{mine_url}?track_id={quran_track.id}")
        self.assertEqual(resp_t2_quran.status_code, status.HTTP_200_OK)
        t2_quran_results = resp_t2_quran.data["results"] if "results" in resp_t2_quran.data else resp_t2_quran.data
        self.assertEqual(len(t2_quran_results), 1)
        self.assertEqual(t2_quran_results[0]["user_id"], student2.id)
        self.assertNotEqual(t2_quran_results[0]["user_id"], student.id)
