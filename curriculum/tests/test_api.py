"""API tests for the curriculum app — every endpoint, one academy at a time.

Every endpoint gets a happy path and at least one failure case, per CLAUDE.md.
Acceptance criteria from specs/phase-2-curriculum.md covered here: 2 (levels come
back ordered), 3 (audio submission is pending), 4 (a beginner skip is instantly
reviewed), 5 (audio and skip together is rejected), 6 (re-submitting updates the
existing row) and 7 (non-teachers cannot reach the review endpoint).

Placement review is lead-only — the spec left the lead-vs-sub question open and
the product owner chose lead only, so a sub-teacher is asserted forbidden
alongside students and parents.

**What SaaS Phase 3 changed about this file.** Every URL now names the academy,
and every caller now needs an active membership in it. The tests here are about
endpoint *mechanics* inside one academy — status codes, payload shapes, role gates,
the ordering and append rules. That two academies cannot reach each other's rows is
``test_tenant_isolation.py``'s subject, and is deliberately not repeated here.
"""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    MinorStudentFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
    UserFactory,
)
from curriculum.models import Level, PlacementResult, Status, TeacherTrack
from organizations.models import MembershipStatus, OrganizationRole
from organizations.tests.factories import OrganizationMembershipFactory, academy

from .factories import (
    BeginnerSkipPlacementFactory,
    LevelFactory,
    PlacementResultFactory,
    ReviewedPlacementFactory,
    TeacherTrackFactory,
    TrackFactory,
    TrackWithLevelsFactory,
    admit,
)
from .test_models import audio_upload


def _url(name, organization, **kwargs):
    return reverse(
        f"curriculum:{name}",
        kwargs={"organization_pk": getattr(organization, "pk", organization), **kwargs},
    )


def tracks_url(organization):
    return _url("academy-track-list", organization)


def track_url(organization, track):
    return _url("academy-track-detail", organization, pk=getattr(track, "pk", track))


def levels_url(organization):
    return _url("academy-level-list", organization)


def level_url(organization, level):
    return _url("academy-level-detail", organization, pk=getattr(level, "pk", level))


def teachers_url(organization):
    return _url("academy-teacher-track-list", organization)


def teacher_url(organization, assignment):
    return _url(
        "academy-teacher-track-detail",
        organization,
        pk=getattr(assignment, "pk", assignment),
    )


def my_teachers_url(organization):
    return _url("academy-teacher-track-mine", organization)


def placements_url(organization):
    return _url("academy-placement-create", organization)


def my_placements_url(organization):
    return _url("academy-placement-mine", organization)


def children_placements_url(organization):
    return _url("academy-placement-children", organization)


def pending_url(organization):
    return _url("academy-placement-pending", organization)


def review_url(organization, placement):
    return _url(
        "academy-placement-review",
        organization,
        pk=getattr(placement, "pk", placement),
    )


def audio_url(organization, placement):
    return _url(
        "academy-placement-audio-url",
        organization,
        pk=getattr(placement, "pk", placement),
    )


class AcademyAPITestCase(APITestCase):
    """One academy with the callers these endpoints distinguish between.

    ``academy()`` rather than ``OrganizationFactory()``, because the owner is a
    caller in most of these tests and the owner *is* a membership — there is no
    second representation of ownership to build instead.

    The owner, the administrator and the staff member are all built from
    ``UserFactory``, whose account role is ``student``. That is deliberate rather
    than careless: it keeps the two axes visibly independent, which is the phase's
    central claim. Organization authority decides who may author curriculum;
    ``User.role`` decides who may submit a placement or review one; and a person
    can hold one without the other in either direction.
    """

    def setUp(self):
        self.owner = UserFactory(username="academy-owner")
        self.organization = academy(owner=self.owner, name="Al-Huda", slug="al-huda")
        self.admin = admit(
            UserFactory(username="academy-admin"),
            self.organization,
            OrganizationRole.ADMIN,
        ).user
        self.staff = admit(
            UserFactory(username="academy-staff"),
            self.organization,
            OrganizationRole.STAFF,
        ).user
        self.lead = admit(LeadTeacherFactory(), self.organization).user
        self.student = admit(StudentFactory(), self.organization).user

    def track(self, **kwargs):
        kwargs.setdefault("organization", self.organization)
        return TrackFactory(**kwargs)

    def track_with_levels(self, **kwargs):
        kwargs.setdefault("organization", self.organization)
        return TrackWithLevelsFactory(**kwargs)


class AcademyTrackReadAPITests(AcademyAPITestCase):
    def test_track_returns_its_levels_in_order(self):
        """Acceptance criterion 2."""
        track = self.track(name="Tajweed", slug="tajweed")
        for name in ("Beginner", "Intermediate", "Advanced"):
            LevelFactory(track=track, name=name)

        self.client.force_authenticate(user=self.student)
        response = self.client.get(tracks_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        payload = response.data[0]
        self.assertEqual(payload["slug"], "tajweed")
        self.assertEqual(payload["organization"], self.organization.pk)
        self.assertEqual(
            [(level["order"], level["name"]) for level in payload["levels"]],
            [(1, "Beginner"), (2, "Intermediate"), (3, "Advanced")],
        )

    def test_every_active_member_may_read_the_curriculum(self):
        self.track()
        for user in (self.owner, self.admin, self.staff, self.lead, self.student):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(tracks_url(self.organization))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(len(response.data), 1)

    def test_listing_is_no_longer_public(self):
        """Phase 2's public catalogue is gone — see curriculum/urls.py."""
        self.track()
        response = self.client.get(tracks_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_non_member_is_refused(self):
        self.track()
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.get(tracks_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_suspended_member_is_refused(self):
        self.track()
        suspended = StudentFactory()
        OrganizationMembershipFactory(
            organization=self.organization,
            user=suspended,
            role=OrganizationRole.STAFF,
            status=MembershipStatus.SUSPENDED,
        )
        self.client.force_authenticate(user=suspended)
        response = self.client.get(tracks_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_level_payload_carries_min_age_and_group_eligibility(self):
        LevelFactory(track=self.track(), min_age=7, group_eligible=True)
        self.client.force_authenticate(user=self.student)
        response = self.client.get(tracks_url(self.organization))
        level = response.data[0]["levels"][0]
        self.assertEqual(level["min_age"], 7)
        self.assertTrue(level["group_eligible"])

    def test_a_track_with_no_levels_returns_an_empty_list(self):
        self.track()
        self.client.force_authenticate(user=self.student)
        response = self.client.get(tracks_url(self.organization))
        self.assertEqual(list(response.data[0]["levels"]), [])

    def test_each_track_only_nests_its_own_levels(self):
        arabic = self.track(name="Arabic", slug="arabic")
        hifz = self.track(name="Hifz", slug="hifz")
        LevelFactory(track=arabic, name="Alphabet")
        LevelFactory(track=hifz, name="Juz 30")
        LevelFactory(track=hifz, name="Juz 29")

        self.client.force_authenticate(user=self.student)
        response = self.client.get(tracks_url(self.organization))
        by_slug = {track["slug"]: track for track in response.data}
        self.assertEqual(
            [lv["name"] for lv in by_slug["arabic"]["levels"]], ["Alphabet"]
        )
        self.assertEqual(
            [lv["name"] for lv in by_slug["hifz"]["levels"]], ["Juz 30", "Juz 29"]
        )

    def test_a_member_can_read_one_track(self):
        track = self.track(name="Tajweed", slug="tajweed")
        self.client.force_authenticate(user=self.student)
        response = self.client.get(track_url(self.organization, track))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], track.pk)


class AcademyTrackWriteAPITests(AcademyAPITestCase):
    def test_the_owner_creates_a_track_in_their_own_academy(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            tracks_url(self.organization),
            {"name": "Tajweed", "slug": "tajweed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["organization"], self.organization.pk)
        self.assertEqual(response.data["levels"], [])
        self.assertEqual(
            self.organization.tracks.values_list("slug", flat=True).first(), "tajweed"
        )

    def test_an_administrator_may_create_one_too(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            tracks_url(self.organization), {"name": "Hifz", "slug": "hifz"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_staff_teachers_and_students_may_not_create_a_track(self):
        """Owner and admin only — the product owner's answer to the policy question."""
        for user in (self.staff, self.lead, self.student):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    tracks_url(self.organization),
                    {"name": "Nope", "slug": f"nope-{user.pk}"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.organization.tracks.exists())

    def test_an_organization_field_in_the_body_is_ignored(self):
        """The academy comes from the route, never from the payload."""
        elsewhere = academy(slug="elsewhere")
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            tracks_url(self.organization),
            {"name": "Tajweed", "slug": "tajweed", "organization": elsewhere.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["organization"], self.organization.pk)
        self.assertFalse(elsewhere.tracks.exists())

    def test_a_slug_this_academy_already_uses_is_rejected(self):
        self.track(slug="tajweed")
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            tracks_url(self.organization),
            {"name": "Tajweed Again", "slug": "tajweed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data)

    def test_a_slug_another_academy_uses_is_free_here(self):
        TrackFactory(organization=academy(slug="other"), slug="tajweed")
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            tracks_url(self.organization),
            {"name": "Tajweed", "slug": "tajweed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_track_can_be_renamed(self):
        track = self.track(name="Tajweed", slug="tajweed")
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            track_url(self.organization, track), {"name": "Advanced Tajweed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Advanced Tajweed")

    def test_a_patch_cannot_move_a_track_to_another_academy(self):
        track = self.track()
        elsewhere = academy(slug="elsewhere")
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            track_url(self.organization, track),
            {"organization": elsewhere.pk},
            format="json",
        )
        # 'organization' is not a field on the write shape, so the request is
        # simply empty — and an empty patch is rejected rather than silently
        # accepted.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        track.refresh_from_db()
        self.assertEqual(track.organization, self.organization)

    def test_put_is_not_offered(self):
        track = self.track()
        self.client.force_authenticate(user=self.owner)
        response = self.client.put(
            track_url(self.organization, track),
            {"name": "Replaced", "slug": "replaced"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_an_unknown_track_is_404(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(track_url(self.organization, 9999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AcademyLevelAPITests(AcademyAPITestCase):
    def test_levels_come_back_in_track_order(self):
        track = self.track(slug="tajweed")
        for name in ("Beginner", "Intermediate", "Advanced"):
            LevelFactory(track=track, name=name)

        self.client.force_authenticate(user=self.student)
        response = self.client.get(levels_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [(lv["order"], lv["name"]) for lv in response.data],
            [(1, "Beginner"), (2, "Intermediate"), (3, "Advanced")],
        )
        self.assertEqual({lv["track"] for lv in response.data}, {track.pk})

    def test_the_list_can_be_narrowed_to_one_track(self):
        wanted = self.track(slug="tajweed")
        other = self.track(slug="hifz")
        LevelFactory(track=wanted, name="Beginner")
        LevelFactory(track=other, name="Juz 30")

        self.client.force_authenticate(user=self.student)
        response = self.client.get(levels_url(self.organization), {"track": wanted.pk})
        self.assertEqual([lv["name"] for lv in response.data], ["Beginner"])

    def test_posting_appends_at_the_next_order(self):
        """Acceptance criterion: the first level is 1 and the rest append."""
        track = self.track(slug="tajweed")
        self.client.force_authenticate(user=self.owner)
        for expected, name in enumerate(("Beginner", "Intermediate"), start=1):
            response = self.client.post(
                levels_url(self.organization),
                {"track": track.pk, "name": name},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.assertEqual(response.data["order"], expected)
        self.assertEqual(
            list(track.levels.values_list("order", flat=True)), [1, 2]
        )

    def test_a_client_cannot_choose_the_order(self):
        """``order`` is not a field, so 'insert at 2' is not expressible."""
        track = self.track()
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            levels_url(self.organization),
            {"track": track.pk, "name": "Sneaky", "order": 7},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["order"], 1)

    def test_min_age_and_group_eligibility_are_stored(self):
        track = self.track()
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            levels_url(self.organization),
            {
                "track": track.pk,
                "name": "Kids beginner",
                "min_age": 7,
                "group_eligible": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["min_age"], 7)
        self.assertTrue(response.data["group_eligible"])

    def test_a_track_from_another_academy_cannot_be_used(self):
        """The spec's named attack: Academy A route + Academy B track id."""
        foreign = TrackFactory(organization=academy(slug="other"))
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            levels_url(self.organization),
            {"track": foreign.pk, "name": "Trespass"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertFalse(foreign.levels.exists())

    def test_staff_teachers_and_students_may_not_create_a_level(self):
        track = self.track()
        for user in (self.staff, self.lead, self.student):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    levels_url(self.organization),
                    {"track": track.pk, "name": "Nope"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(Level.objects.exists())

    def test_a_level_can_be_renamed_without_moving(self):
        level = LevelFactory(track=self.track(), name="Beginner")
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            level_url(self.organization, level),
            {"name": "Absolute Beginner", "group_eligible": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Absolute Beginner")
        self.assertTrue(response.data["group_eligible"])
        self.assertEqual(response.data["order"], level.order)

    def test_an_existing_levels_order_is_not_editable(self):
        track = self.track()
        LevelFactory(track=track)
        second = LevelFactory(track=track)
        self.client.force_authenticate(user=self.owner)
        response = self.client.patch(
            level_url(self.organization, second), {"order": 1}, format="json"
        )
        # ``order`` is absent from the write shape, so this is an empty patch.
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        second.refresh_from_db()
        self.assertEqual(second.order, 2)

    def test_an_unknown_level_is_404(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.get(level_url(self.organization, 9999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_authentication(self):
        response = self.client.get(levels_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyTeacherTrackAPITests(AcademyAPITestCase):
    def setUp(self):
        super().setUp()
        self.tajweed = self.track(name="Tajweed", slug="tajweed")
        self.sub = admit(SubTeacherFactory(), self.organization).user

    def test_the_owner_assigns_a_track_to_a_teacher(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            teachers_url(self.organization),
            {"user": self.lead.pk, "track": self.tajweed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["user"], self.lead.pk)
        self.assertEqual(response.data["username"], self.lead.username)
        self.assertEqual(response.data["organization"], self.organization.pk)
        self.assertEqual(response.data["track_slug"], "tajweed")
        self.assertTrue(response.data["active"])

    def test_a_sub_teacher_may_be_assigned_too(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            teachers_url(self.organization),
            {"user": self.sub.pk, "track": self.tajweed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_a_non_teaching_account_is_rejected(self):
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            teachers_url(self.organization),
            {"user": self.student.pk, "track": self.tajweed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_a_teacher_who_is_not_a_member_here_is_rejected(self):
        stranger = SubTeacherFactory()
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            teachers_url(self.organization),
            {"user": stranger.pk, "track": self.tajweed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("user", response.data)

    def test_the_same_assignment_twice_is_rejected(self):
        TeacherTrackFactory(
            membership=admit(self.lead, self.organization), track=self.tajweed
        )
        self.client.force_authenticate(user=self.owner)
        response = self.client.post(
            teachers_url(self.organization),
            {"user": self.lead.pk, "track": self.tajweed.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(TeacherTrack.objects.count(), 1)

    def test_only_the_owner_and_admin_may_read_the_roster(self):
        TeacherTrackFactory(
            membership=admit(self.lead, self.organization), track=self.tajweed
        )
        for user in (self.owner, self.admin):
            with self.subTest(allowed=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(teachers_url(self.organization))
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(len(response.data), 1)
        for user in (self.staff, self.lead, self.student):
            with self.subTest(refused=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(teachers_url(self.organization))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_eligibility_can_be_withdrawn_and_restored(self):
        assignment = TeacherTrackFactory(
            membership=admit(self.lead, self.organization), track=self.tajweed
        )
        self.client.force_authenticate(user=self.admin)

        off = self.client.patch(
            teacher_url(self.organization, assignment), {"active": False}, format="json"
        )
        self.assertEqual(off.status_code, status.HTTP_200_OK)
        self.assertFalse(off.data["active"])

        on = self.client.patch(
            teacher_url(self.organization, assignment), {"active": True}, format="json"
        )
        self.assertTrue(on.data["active"])
        # Withdrawing never deleted the row.
        self.assertEqual(TeacherTrack.objects.count(), 1)

    def test_a_teacher_reads_their_own_assignments(self):
        TeacherTrackFactory(
            membership=admit(self.lead, self.organization), track=self.tajweed
        )
        TeacherTrackFactory(
            membership=admit(self.sub, self.organization),
            track=self.track(slug="hifz"),
        )
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(my_teachers_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["track_slug"] for row in response.data], ["tajweed"])

    def test_a_non_teaching_account_has_no_assignments_endpoint(self):
        for user in (self.student, self.staff):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(my_teachers_url(self.organization))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(teachers_url(self.organization))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyPlacementSubmitAPITests(AcademyAPITestCase):
    def setUp(self):
        super().setUp()
        self.track_obj = self.track_with_levels()
        self.url = placements_url(self.organization)
        self.client.force_authenticate(user=self.student)

    def test_audio_submission_comes_back_pending(self):
        """Acceptance criterion 3."""
        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "audio_sample": audio_upload()},
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
        self.assertEqual(response.data["track"], self.track_obj.slug)
        self.assertEqual(response.data["student"]["id"], self.student.id)
        self.assertEqual(response.data["organization"], self.organization.pk)

        placement = PlacementResult.objects.get()
        self.assertEqual(placement.student, self.student)
        self.assertEqual(placement.status, Status.PENDING)

    def test_beginner_skip_is_immediately_reviewed_at_level_one(self):
        """Acceptance criterion 4."""
        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], Status.REVIEWED.value)
        self.assertEqual(response.data["recommended_level"]["order"], 1)
        self.assertEqual(
            response.data["recommended_level"]["id"],
            self.track_obj.levels.get(order=1).id,
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
                "track": self.track_obj.id,
                "audio_sample": audio_upload(),
                "skipped_as_beginner": True,
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PlacementResult.objects.exists())

    def test_neither_audio_nor_skip_is_rejected(self):
        response = self.client.post(
            self.url, {"track": self.track_obj.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PlacementResult.objects.exists())

    def test_resubmitting_for_the_same_track_updates_the_existing_row(self):
        """Acceptance criterion 6."""
        first = self.client.post(
            self.url,
            {"track": self.track_obj.id, "audio_sample": audio_upload("first.mp3")},
            format="multipart",
        )
        second = self.client.post(
            self.url,
            {"track": self.track_obj.id, "audio_sample": audio_upload("second.mp3")},
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
        reviewed = ReviewedPlacementFactory(
            student=self.student, track=self.track_obj
        )

        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "audio_sample": audio_upload()},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["id"], reviewed.id)
        self.assertEqual(response.data["status"], Status.PENDING.value)
        self.assertIsNone(response.data["recommended_level"])
        self.assertIsNone(response.data["reviewed_by"])
        self.assertEqual(PlacementResult.objects.count(), 1)

    def test_a_second_track_gets_its_own_placement(self):
        other = self.track_with_levels()
        for track in (self.track_obj, other):
            response = self.client.post(
                self.url,
                {"track": track.id, "audio_sample": audio_upload()},
                format="multipart",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PlacementResult.objects.count(), 2)

    def test_skip_into_a_track_with_no_levels_is_rejected(self):
        empty = self.track_with_levels(levels=0)
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
        minor = admit(MinorStudentFactory(), self.organization).user
        self.assertFalse(minor.is_fully_active)
        self.client.force_authenticate(user=minor)

        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_non_students_cannot_submit(self):
        parent = admit(ParentFactory(), self.organization).user
        sub = admit(SubTeacherFactory(), self.organization).user
        for user in (parent, sub, self.lead):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    self.url,
                    {"track": self.track_obj.id, "skipped_as_beginner": True},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PlacementResult.objects.exists())

    def test_a_student_who_is_not_a_member_cannot_submit(self):
        self.client.force_authenticate(user=StudentFactory())
        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(PlacementResult.objects.exists())

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.url,
            {"track": self.track_obj.id, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyPendingPlacementAPITests(AcademyAPITestCase):
    def setUp(self):
        super().setUp()
        self.pending = PlacementResultFactory(
            student=self.student, track=self.track()
        )
        self.url = pending_url(self.organization)

    def test_the_academys_lead_sees_its_pending_placements(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [self.pending.id])
        self.assertEqual(response.data[0]["student"]["id"], self.pending.student_id)

    def test_reviewed_placements_are_not_in_the_queue(self):
        ReviewedPlacementFactory(track=self.track_with_levels())
        BeginnerSkipPlacementFactory(  # auto-reviewed, never needs a human
            track=self.track_with_levels()
        )
        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.url)
        self.assertEqual([p["id"] for p in response.data], [self.pending.id])

    def test_a_suspended_students_placement_leaves_the_queue(self):
        membership = self.student.organization_memberships.get(
            organization=self.organization
        )
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        self.client.force_authenticate(user=self.lead)
        response = self.client.get(self.url)
        self.assertEqual(list(response.data), [])

    def test_non_lead_members_are_forbidden(self):
        for user in (self.owner, self.admin, self.staff, self.student):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_lead_who_is_not_a_member_is_forbidden(self):
        self.client.force_authenticate(user=LeadTeacherFactory())
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyPlacementReviewAPITests(AcademyAPITestCase):
    def setUp(self):
        super().setUp()
        self.track_obj = self.track_with_levels()
        self.placement = PlacementResultFactory(
            student=self.student, track=self.track_obj
        )
        self.level = self.track_obj.levels.get(order=2)
        self.url = review_url(self.organization, self.placement)

    def test_the_academys_lead_sets_the_level_and_is_stamped_on_the_review(self):
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
        queue = self.client.get(pending_url(self.organization))
        self.assertEqual(list(queue.data), [])

    def test_students_and_parents_cannot_review(self):
        """Acceptance criterion 7."""
        parent = admit(ParentFactory(), self.organization).user
        for user in (self.student, parent, self.owner, self.admin, self.staff):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.post(
                    self.url, {"recommended_level": self.level.id}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_a_sub_teacher_cannot_review(self):
        """Lead-only was the product owner's call; see learnings.md."""
        self.client.force_authenticate(
            user=admit(SubTeacherFactory(), self.organization).user
        )
        response = self.client.post(
            self.url, {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.status, Status.PENDING)

    def test_a_level_from_another_track_is_rejected(self):
        elsewhere = LevelFactory(track=self.track())
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            self.url, {"recommended_level": elsewhere.id}, format="json"
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
            {"recommended_level": self.track_obj.levels.get(order=3).id},
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
            review_url(self.organization, 9999),
            {"recommended_level": self.level.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_requires_authentication(self):
        response = self.client.post(
            self.url, {"recommended_level": self.level.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyMyPlacementsAPITests(AcademyAPITestCase):
    def setUp(self):
        super().setUp()
        self.url = my_placements_url(self.organization)

    def test_student_sees_their_own_placement_per_track(self):
        pending = PlacementResultFactory(student=self.student, track=self.track())
        skipped = BeginnerSkipPlacementFactory(
            student=self.student, track=self.track_with_levels()
        )

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        by_id = {p["id"]: p for p in response.data}
        self.assertEqual(set(by_id), {pending.id, skipped.id})
        self.assertEqual(by_id[pending.id]["status"], Status.PENDING.value)
        self.assertIsNone(by_id[pending.id]["recommended_level"])
        self.assertEqual(by_id[skipped.id]["status"], Status.REVIEWED.value)
        self.assertEqual(by_id[skipped.id]["recommended_level"]["order"], 1)

    def test_another_students_placement_is_not_visible(self):
        mine = PlacementResultFactory(student=self.student, track=self.track())
        classmate = admit(StudentFactory(), self.organization).user
        PlacementResultFactory(student=classmate, track=self.track())

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual([p["id"] for p in response.data], [mine.id])

    def test_a_student_with_no_placements_gets_an_empty_list(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_reviewed_at_is_rendered_in_the_students_own_timezone(self):
        """Stored UTC, converted at the serializer layer (CLAUDE.md)."""
        student = admit(
            StudentFactory(timezone="Africa/Lagos"), self.organization
        ).user  # UTC+01:00, no DST
        ReviewedPlacementFactory(student=student, track=self.track_with_levels())

        self.client.force_authenticate(user=student)
        response = self.client.get(self.url)
        self.assertTrue(response.data[0]["reviewed_at"].endswith("Z"))
        self.assertTrue(response.data[0]["reviewed_at_local"].endswith("+01:00"))

    def test_a_pending_placement_has_no_local_review_time(self):
        PlacementResultFactory(student=self.student, track=self.track())
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertIsNone(response.data[0]["reviewed_at_local"])

    def test_non_students_are_forbidden(self):
        parent = admit(ParentFactory(), self.organization).user
        sub = admit(SubTeacherFactory(), self.organization).user
        for user in (parent, sub, self.lead):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owning_the_academy_neither_grants_nor_removes_a_students_own_view(self):
        """The two axes are independent, and this is the direction that surprises.

        ``self.owner`` runs the academy *and* holds a ``student`` account, so this
        endpoint is legitimately theirs — it returns their own placements, not the
        academy's. Organization authority is not what gates it.
        """
        mine = PlacementResultFactory(student=self.owner, track=self.track())
        PlacementResultFactory(student=self.student, track=self.track())

        self.client.force_authenticate(user=self.owner)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [mine.id])

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class AcademyChildrenPlacementsAPITests(AcademyAPITestCase):
    """The parent rule: active parent + active child + a ParentLink, all three."""

    def setUp(self):
        super().setUp()
        self.parent = admit(ParentFactory(), self.organization).user
        self.child = admit(MinorStudentFactory(), self.organization).user
        ParentLinkFactory(parent=self.parent, student=self.child)
        self.placement = PlacementResultFactory(
            student=self.child, track=self.track()
        )
        self.url = children_placements_url(self.organization)

    def test_a_linked_parent_sees_their_childs_placement_here(self):
        self.client.force_authenticate(user=self.parent)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [self.placement.id])
        self.assertEqual(response.data[0]["student"]["id"], self.child.pk)

    def test_an_unrelated_parent_sees_nothing(self):
        other = admit(ParentFactory(), self.organization).user
        self.client.force_authenticate(user=other)
        response = self.client.get(self.url)
        self.assertEqual(list(response.data), [])

    def test_a_parent_link_alone_is_not_access(self):
        """The child is linked but not a member here, so there is nothing to see."""
        outside_child = MinorStudentFactory()
        ParentLinkFactory(parent=self.parent, student=outside_child)
        PlacementResultFactory(student=outside_child, track=TrackFactory())

        self.client.force_authenticate(user=self.parent)
        response = self.client.get(self.url)
        self.assertEqual([p["id"] for p in response.data], [self.placement.id])

    def test_a_suspended_child_drops_out_of_the_list(self):
        membership = self.child.organization_memberships.get(
            organization=self.organization
        )
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        self.client.force_authenticate(user=self.parent)
        response = self.client.get(self.url)
        self.assertEqual(list(response.data), [])

    def test_a_suspended_parent_is_refused_outright(self):
        membership = self.parent.organization_memberships.get(
            organization=self.organization
        )
        membership.status = MembershipStatus.SUSPENDED
        membership.save()

        self.client.force_authenticate(user=self.parent)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_parents_are_forbidden(self):
        for user in (self.student, self.lead, self.owner, self.staff):
            with self.subTest(user=user.username):
                self.client.force_authenticate(user=user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_reach_a_childs_audio(self):
        """Phase 6's rule stands: lead teacher and the student, nobody else."""
        self.client.force_authenticate(user=self.parent)
        response = self.client.get(audio_url(self.organization, self.placement))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
