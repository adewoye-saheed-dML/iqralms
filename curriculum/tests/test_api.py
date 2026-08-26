"""API tests for the curriculum app.

Every endpoint gets a happy path and at least one failure case, per CLAUDE.md.
Acceptance criteria from specs/phase-2-curriculum.md covered here: 2 (levels come
back ordered), 3 (audio submission is pending), 4 (a beginner skip is instantly
reviewed), 5 (audio and skip together is rejected), 6 (re-submitting updates the
existing row) and 7 (non-teachers cannot reach the review endpoint).

Placement review is lead-only — the spec left the lead-vs-sub question open and
the product owner chose lead only, so a sub-teacher is asserted forbidden
alongside students and parents.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    StudentFactory,
    SubTeacherFactory,
)
from curriculum.models import PlacementResult, Status

from .factories import (
    BeginnerSkipPlacementFactory,
    LevelFactory,
    PlacementResultFactory,
    ReviewedPlacementFactory,
    TrackFactory,
    TrackWithLevelsFactory,
)
from .test_models import audio_upload


def review_url(placement_or_pk):
    pk = getattr(placement_or_pk, "pk", placement_or_pk)
    return reverse("curriculum:placement-review", args=[pk])


class TrackListAPITests(APITestCase):
    url = reverse("curriculum:track-list")

    def test_track_returns_its_levels_in_order(self):
        """Acceptance criterion 2."""
        track = TrackFactory(name="Tajweed", slug="tajweed")
        for name in ("Beginner", "Intermediate", "Advanced"):
            LevelFactory(track=track, name=name)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        payload = response.data[0]
        self.assertEqual(payload["slug"], "tajweed")
        self.assertEqual(
            [(level["order"], level["name"]) for level in payload["levels"]],
            [(1, "Beginner"), (2, "Intermediate"), (3, "Advanced")],
        )

    def test_listing_is_public(self):
        TrackFactory()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_level_payload_carries_min_age_and_group_eligibility(self):
        LevelFactory(min_age=7, group_eligible=True)
        response = self.client.get(self.url)
        level = response.data[0]["levels"][0]
        self.assertEqual(level["min_age"], 7)
        self.assertTrue(level["group_eligible"])

    def test_a_track_with_no_levels_returns_an_empty_list(self):
        TrackFactory()
        response = self.client.get(self.url)
        self.assertEqual(list(response.data[0]["levels"]), [])

    def test_each_track_only_nests_its_own_levels(self):
        arabic = TrackFactory(name="Arabic", slug="arabic")
        hifz = TrackFactory(name="Hifz", slug="hifz")
        LevelFactory(track=arabic, name="Alphabet")
        LevelFactory(track=hifz, name="Juz 30")
        LevelFactory(track=hifz, name="Juz 29")

        response = self.client.get(self.url)
        by_slug = {track["slug"]: track for track in response.data}
        self.assertEqual([lv["name"] for lv in by_slug["arabic"]["levels"]], ["Alphabet"])
        self.assertEqual(
            [lv["name"] for lv in by_slug["hifz"]["levels"]], ["Juz 30", "Juz 29"]
        )


class PlacementSubmitAPITests(APITestCase):
    url = reverse("curriculum:placement-create")

    def setUp(self):
        self.student = StudentFactory()
        self.track = TrackWithLevelsFactory()
        self.client.force_authenticate(user=self.student)

    def test_audio_submission_comes_back_pending(self):
        """Acceptance criterion 3."""
        response = self.client.post(
            self.url,
            {"track": self.track.id, "audio_sample": audio_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Status.PENDING.value)
        self.assertIsNone(response.data["recommended_level"])
        self.assertIsNone(response.data["reviewed_by"])
        self.assertIsNone(response.data["reviewed_at"])
        self.assertFalse(response.data["skipped_as_beginner"])
        # Phase 6: presence and filename, never a URL. Fetching the audio is a
        # separate, authorised, expiring request — see test_audio_access.py.
        self.assertTrue(response.data["has_audio_sample"])
        self.assertNotIn("audio_sample", response.data)
        self.assertEqual(response.data["track"], self.track.slug)
        self.assertEqual(response.data["student"]["id"], self.student.id)

        placement = PlacementResult.objects.get()
        self.assertEqual(placement.student, self.student)
        self.assertEqual(placement.status, Status.PENDING)

    def test_beginner_skip_is_immediately_reviewed_at_level_one(self):
        """Acceptance criterion 4."""
        response = self.client.post(
            self.url,
            {"track": self.track.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Status.REVIEWED.value)
        self.assertEqual(response.data["recommended_level"]["order"], 1)
        self.assertEqual(
            response.data["recommended_level"]["id"],
            self.track.levels.get(order=1).id,
        )
        # A system decision: no teacher is credited with it.
        self.assertIsNone(response.data["reviewed_by"])
        self.assertIsNotNone(response.data["reviewed_at"])
        self.assertTrue(response.data["skipped_as_beginner"])

    def test_audio_and_skip_together_is_rejected(self):
        """Acceptance criterion 5."""
        response = self.client.post(
            self.url,
            {
                "track": self.track.id,
                "audio_sample": audio_upload(),
                "skipped_as_beginner": True,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PlacementResult.objects.exists())

    def test_neither_audio_nor_skip_is_rejected(self):
        response = self.client.post(
            self.url, {"track": self.track.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PlacementResult.objects.exists())

    def test_resubmitting_for_the_same_track_updates_the_existing_row(self):
        """Acceptance criterion 6."""
        first = self.client.post(
            self.url,
            {"track": self.track.id, "audio_sample": audio_upload("first.mp3")},
            format="multipart",
        )
        second = self.client.post(
            self.url,
            {"track": self.track.id, "audio_sample": audio_upload("second.mp3")},
            format="multipart",
        )
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(PlacementResult.objects.count(), 1)
        # The row now points at the second recording. Asserted through the
        # filename rather than a media URL, which Phase 6 stopped publishing.
        self.assertIn("second", second.data["audio_filename"])

    def test_resubmitting_after_a_review_clears_the_review(self):
        """Acceptance criterion 6, for a track already reviewed."""
        reviewed = ReviewedPlacementFactory(student=self.student, track=self.track)

        response = self.client.post(
            self.url,
            {"track": self.track.id, "audio_sample": audio_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["id"], reviewed.id)
        self.assertEqual(response.data["status"], Status.PENDING.value)
        self.assertIsNone(response.data["recommended_level"])
        self.assertIsNone(response.data["reviewed_by"])
        self.assertEqual(PlacementResult.objects.count(), 1)

    def test_a_second_track_gets_its_own_placement(self):
        other = TrackWithLevelsFactory()
        for track in (self.track, other):
            response = self.client.post(
                self.url,
                {"track": track.id, "audio_sample": audio_upload()},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PlacementResult.objects.count(), 2)

    def test_skip_into_a_track_with_no_levels_is_rejected(self):
        empty = TrackWithLevelsFactory(levels=0)
        response = self.client.post(
            self.url, {"track": empty.id, "skipped_as_beginner": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertFalse(PlacementResult.objects.exists())

    def test_unknown_track_is_rejected(self):
        response = self.client.post(
            self.url, {"track": 9999, "skipped_as_beginner": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)

    def test_a_minor_without_a_parent_link_can_still_submit(self):
        """Placement is pre-enrolment; is_fully_active gates booking, not this."""
        minor = MinorStudentFactory()
        self.assertFalse(minor.is_fully_active)
        self.client.force_authenticate(user=minor)

        response = self.client.post(
            self.url, {"track": self.track.id, "skipped_as_beginner": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_non_students_cannot_submit(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    self.url,
                    {"track": self.track.id, "skipped_as_beginner": True},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PlacementResult.objects.exists())

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.url, {"track": self.track.id, "skipped_as_beginner": True}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PendingPlacementAPITests(APITestCase):
    url = reverse("curriculum:placement-pending")

    def setUp(self):
        self.lead = LeadTeacherFactory()
        self.pending = PlacementResultFactory()

    def test_lead_sees_pending_placements(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [self.pending.id])
        self.assertEqual(
            response.data[0]["student"]["id"], self.pending.student_id
        )

    def test_reviewed_placements_are_not_in_the_queue(self):
        ReviewedPlacementFactory()
        BeginnerSkipPlacementFactory()  # auto-reviewed, never needs a human
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.url)
        self.assertEqual([p["id"] for p in response.data], [self.pending.id])

    def test_non_lead_users_are_forbidden(self):
        for user in (StudentFactory(), ParentFactory(), SubTeacherFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class PlacementReviewAPITests(APITestCase):
    def setUp(self):
        self.lead = LeadTeacherFactory()
        self.track = TrackWithLevelsFactory()
        self.placement = PlacementResultFactory(track=self.track)
        self.level = self.track.levels.get(order=2)
        self.url = review_url(self.placement)

    def test_lead_sets_the_level_and_is_stamped_on_the_review(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            self.url, {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Status.REVIEWED.value)
        self.assertEqual(response.data["recommended_level"]["id"], self.level.id)
        self.assertEqual(response.data["reviewed_by"], self.lead.username)
        self.assertIsNotNone(response.data["reviewed_at"])

        self.placement.refresh_from_db()
        self.assertEqual(self.placement.recommended_level, self.level)
        self.assertEqual(self.placement.reviewed_by, self.lead)

    def test_a_reviewed_placement_leaves_the_pending_queue(self):
        self.client.force_authenticate(user=self.lead)
        self.client.post(self.url, {"recommended_level": self.level.id}, format="json")
        queue = self.client.get(reverse("curriculum:placement-pending"))
        self.assertEqual(list(queue.data), [])

    def test_students_and_parents_cannot_review(self):
        """Acceptance criterion 7."""
        for user in (StudentFactory(), self.placement.student, ParentFactory()):
            with self.subTest(role=user.role, user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    self.url, {"recommended_level": self.level.id}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_a_sub_teacher_cannot_review(self):
        """Lead-only was the product owner's call; see learnings.md."""
        self.client.force_authenticate(user=SubTeacherFactory())
        response = self.client.post(
            self.url, {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_a_level_from_another_track_is_rejected(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            self.url, {"recommended_level": LevelFactory().id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("recommended_level", response.data)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_reviewing_twice_is_a_conflict(self):
        self.client.force_authenticate(user=self.lead)
        self.client.post(self.url, {"recommended_level": self.level.id}, format="json")
        again = self.client.post(
            self.url,
            {"recommended_level": self.track.levels.get(order=3).id},
            format="json",
        )
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.recommended_level, self.level)

    def test_missing_level_is_rejected(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("recommended_level", response.data)

    def test_unknown_placement_returns_404(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            review_url(9999), {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_authentication(self):
        response = self.client.post(
            self.url, {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MyPlacementsAPITests(APITestCase):
    url = reverse("curriculum:placement-mine")

    def test_student_sees_their_own_placement_per_track(self):
        student = StudentFactory()
        pending = PlacementResultFactory(student=student)
        skipped = BeginnerSkipPlacementFactory(student=student)

        self.client.force_authenticate(user=student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        by_id = {p["id"]: p for p in response.data}
        self.assertEqual(set(by_id), {pending.id, skipped.id})
        self.assertEqual(by_id[pending.id]["status"], Status.PENDING.value)
        self.assertIsNone(by_id[pending.id]["recommended_level"])
        self.assertEqual(by_id[skipped.id]["status"], Status.REVIEWED.value)
        self.assertEqual(by_id[skipped.id]["recommended_level"]["order"], 1)

    def test_another_students_placement_is_not_visible(self):
        mine = PlacementResultFactory()
        PlacementResultFactory()  # someone else's
        self.client.force_authenticate(user=mine.student)
        response = self.client.get(self.url)
        self.assertEqual([p["id"] for p in response.data], [mine.id])

    def test_a_student_with_no_placements_gets_an_empty_list(self):
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_reviewed_at_is_rendered_in_the_students_own_timezone(self):
        """Stored UTC, converted at the serializer layer (CLAUDE.md)."""
        student = StudentFactory(timezone="Africa/Lagos")  # UTC+01:00, no DST
        ReviewedPlacementFactory(student=student)

        self.client.force_authenticate(user=student)
        response = self.client.get(self.url)
        self.assertTrue(response.data[0]["reviewed_at"].endswith("Z"))
        self.assertTrue(response.data[0]["reviewed_at_local"].endswith("+01:00"))

    def test_a_pending_placement_has_no_local_review_time(self):
        placement = PlacementResultFactory()
        self.client.force_authenticate(user=placement.student)
        response = self.client.get(self.url)
        self.assertIsNone(response.data[0]["reviewed_at_local"])

    def test_non_students_are_forbidden(self):
        for user in (ParentFactory(), SubTeacherFactory(), LeadTeacherFactory()):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
