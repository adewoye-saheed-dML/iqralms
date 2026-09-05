"""SaaS Phase 3's tenant-isolation suite — knowing an id is not access.

.. code-block:: text

    1  a curriculum object belongs to exactly one academy, and only that one sees it
    2  a valid id from another academy is a 404, never a 403 and never a 200
    3  a lead teacher's authority stops at the academy that admitted them
    4  a student's placements do not follow them into an academy they joined
    5  one teacher holds independent, non-transferable eligibility per academy
    6  an academy administrator has no reach into another academy's configuration
    7  private placement audio is isolated as strictly as the placement itself

Written at the API layer, because that is the surface an attacker has. Endpoint
*mechanics* — status codes, payload shapes, ordering, append rules — are
``test_api.py``'s subject; this file builds two academies and checks that nothing
crosses between them.

The 404-not-403 distinction is asserted deliberately and repeatedly. Every
academy-scoped queryset is filtered to the tenant in the URL, so a foreign object
is *absent* rather than forbidden — and a 403 would confirm to an outsider that the
row exists, which is itself the leak. The one place 403 is correct is the caller's
own membership: refusing someone who is not in this academy at all says nothing
about what the academy contains.
"""

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
from curriculum.models import Level, PlacementResult, TeacherTrack, Track
from organizations.models import OrganizationRole
from organizations.tests.factories import academy

from .factories import (
    PlacementResultFactory,
    TeacherTrackFactory,
    TrackFactory,
    TrackWithLevelsFactory,
    admit,
)
from .test_api import (
    audio_url,
    children_placements_url,
    level_url,
    levels_url,
    my_placements_url,
    my_teachers_url,
    pending_url,
    placements_url,
    review_url,
    teacher_url,
    teachers_url,
    track_url,
    tracks_url,
)


class TwoAcademies(APITestCase):
    """The fixture the phase spec asks for, built once.

    Two academies, each with an owner, an admin, a lead teacher and a student; a
    track with levels in each; a pending placement in each; and one teacher who
    belongs to both. Every test below is a single crossing attempted against it.
    """

    def setUp(self):
        self.here = academy(owner=UserFactory(username="owner-a"), slug="academy-a")
        self.there = academy(owner=UserFactory(username="owner-b"), slug="academy-b")
        self.owner_here = self.here.owner_membership.user
        self.owner_there = self.there.owner_membership.user

        self.admin_here = admit(
            UserFactory(username="admin-a"), self.here, OrganizationRole.ADMIN
        ).user
        self.admin_there = admit(
            UserFactory(username="admin-b"), self.there, OrganizationRole.ADMIN
        ).user

        self.lead_here = admit(LeadTeacherFactory(username="lead-a"), self.here).user
        self.lead_there = admit(LeadTeacherFactory(username="lead-b"), self.there).user

        self.student_here = admit(
            StudentFactory(username="student-a"), self.here
        ).user
        self.student_there = admit(
            StudentFactory(username="student-b"), self.there
        ).user

        # Deliberately the same slug in both, which the old global unique index
        # made impossible and which every cross-tenant lookup below must survive.
        self.track_here = TrackWithLevelsFactory(
            organization=self.here, name="Tajweed", slug="tajweed"
        )
        self.track_there = TrackWithLevelsFactory(
            organization=self.there, name="Tajweed", slug="tajweed"
        )
        self.level_here = self.track_here.levels.get(order=1)
        self.level_there = self.track_there.levels.get(order=1)

        self.placement_here = PlacementResultFactory(
            student=self.student_here, track=self.track_here
        )
        self.placement_there = PlacementResultFactory(
            student=self.student_there, track=self.track_there
        )


class TrackIsolationTests(TwoAcademies):
    """Invariants 1 and 2, for tracks."""

    def test_a_listing_returns_only_the_academys_own_tracks(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(tracks_url(self.here))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([t["id"] for t in response.data], [self.track_here.pk])
        self.assertEqual({t["organization"] for t in response.data}, {self.here.pk})

    def test_a_member_here_reading_a_track_there_gets_404(self):
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.get(track_url(self.here, self.track_there))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_student_here_reading_a_track_there_gets_404(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(track_url(self.here, self.track_there))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_admin_here_cannot_rename_a_track_there(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.patch(
            track_url(self.here, self.track_there), {"name": "Mine now"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.track_there.refresh_from_db()
        self.assertEqual(self.track_there.name, "Tajweed")

    def test_addressing_another_academys_route_is_refused_outright(self):
        """The URL is input. Being an owner *somewhere* authorizes nothing here."""
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.get(tracks_url(self.there))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_owner_cannot_create_a_track_in_another_academy(self):
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.post(
            tracks_url(self.there), {"name": "Hifz", "slug": "hifz"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.there.tracks.filter(slug="hifz").exists())


class LevelIsolationTests(TwoAcademies):
    """Invariants 1 and 2, for levels — which have no organization column at all."""

    def test_a_listing_returns_only_this_academys_levels(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(levels_url(self.here))
        self.assertEqual(
            {lv["track"] for lv in response.data}, {self.track_here.pk}
        )

    def test_reading_a_level_from_another_academy_gets_404(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.get(level_url(self.here, self.level_there))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_patching_a_level_in_another_academy_gets_404(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.patch(
            level_url(self.here, self.level_there), {"name": "Mine now"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.level_there.refresh_from_db()
        self.assertNotEqual(self.level_there.name, "Mine now")

    def test_a_level_cannot_be_appended_to_another_academys_track(self):
        """The spec's named combination: Academy A route + Academy B track id."""
        before = self.track_there.levels.count()
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.post(
            levels_url(self.here),
            {"track": self.track_there.pk, "name": "Trespass"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertEqual(self.track_there.levels.count(), before)

    def test_filtering_by_a_foreign_track_id_returns_nothing(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(
            levels_url(self.here), {"track": self.track_there.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])


class PlacementIsolationTests(TwoAcademies):
    """Invariants 2, 3 and 4 — the placement surface, one crossing per test."""

    def test_a_student_cannot_submit_into_an_academy_they_do_not_belong_to(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            placements_url(self.there),
            {"track": self.track_there.pk, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(PlacementResult.objects.count(), 2)

    def test_a_student_cannot_submit_a_foreign_track_through_their_own_academy(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.post(
            placements_url(self.here),
            {"track": self.track_there.pk, "skipped_as_beginner": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertEqual(PlacementResult.objects.count(), 2)

    def test_mine_returns_only_this_academys_placements(self):
        """Invariant 4, for a student who genuinely belongs to both."""
        traveller = StudentFactory(username="traveller")
        admit(traveller, self.here)
        admit(traveller, self.there)
        mine = PlacementResultFactory(student=traveller, track=self.track_here)
        theirs = PlacementResultFactory(student=traveller, track=self.track_there)

        self.client.force_authenticate(user=traveller)
        self.assertEqual(
            [p["id"] for p in self.client.get(my_placements_url(self.here)).data],
            [mine.pk],
        )
        self.assertEqual(
            [p["id"] for p in self.client.get(my_placements_url(self.there)).data],
            [theirs.pk],
        )

    def test_a_review_queue_holds_only_this_academys_placements(self):
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.get(pending_url(self.here))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [self.placement_here.pk])

    def test_a_lead_cannot_read_another_academys_queue(self):
        """Invariant 3. Phase 2 handed every lead every pending placement."""
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.get(pending_url(self.there))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_lead_cannot_review_a_placement_in_another_academy(self):
        """``Lead A + Placement B -> denied``, and as a 404 rather than a 403."""
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.post(
            review_url(self.here, self.placement_there),
            {"recommended_level": self.level_here.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.placement_there.refresh_from_db()
        self.assertIsNone(self.placement_there.recommended_level)

    def test_a_lead_cannot_review_through_the_other_academys_route_either(self):
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.post(
            review_url(self.there, self.placement_there),
            {"recommended_level": self.level_there.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.placement_there.refresh_from_db()
        self.assertIsNone(self.placement_there.recommended_level)

    def test_a_review_cannot_place_a_student_at_another_academys_level(self):
        """Academy A student + Academy A track + Academy B level — must fail."""
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.post(
            review_url(self.here, self.placement_here),
            {"recommended_level": self.level_there.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("recommended_level", response.data)
        self.placement_here.refresh_from_db()
        self.assertIsNone(self.placement_here.recommended_level)

    def test_a_student_cannot_read_a_foreign_placement_through_any_route(self):
        for organization in (self.here, self.there):
            with self.subTest(route=organization.slug):
                self.client.force_authenticate(user=self.student_here)
                response = self.client.get(
                    audio_url(organization, self.placement_there)
                )
                self.assertIn(
                    response.status_code,
                    (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
                )


class ParentIsolationTests(TwoAcademies):
    """The parent rule: a global family link is not a key to an academy."""

    def setUp(self):
        super().setUp()
        self.parent = ParentFactory(username="parent-both")
        self.child_here = admit(MinorStudentFactory(username="child-a"), self.here).user
        self.child_there = admit(
            MinorStudentFactory(username="child-b"), self.there
        ).user
        ParentLinkFactory(parent=self.parent, student=self.child_here)
        ParentLinkFactory(parent=self.parent, student=self.child_there)
        self.here_placement = PlacementResultFactory(
            student=self.child_here, track=self.track_here
        )
        self.there_placement = PlacementResultFactory(
            student=self.child_there, track=self.track_there
        )

    def test_a_parent_in_one_academy_sees_only_that_academys_child(self):
        admit(self.parent, self.here)
        self.client.force_authenticate(user=self.parent)

        response = self.client.get(children_placements_url(self.here))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([p["id"] for p in response.data], [self.here_placement.pk])

    def test_the_same_parent_gets_a_different_answer_in_each_academy(self):
        admit(self.parent, self.here)
        admit(self.parent, self.there)
        self.client.force_authenticate(user=self.parent)

        self.assertEqual(
            [p["id"] for p in self.client.get(children_placements_url(self.here)).data],
            [self.here_placement.pk],
        )
        self.assertEqual(
            [
                p["id"]
                for p in self.client.get(children_placements_url(self.there)).data
            ],
            [self.there_placement.pk],
        )

    def test_a_parent_link_alone_reaches_nothing(self):
        """No membership anywhere, two real children — and no access."""
        self.client.force_authenticate(user=self.parent)
        for organization in (self.here, self.there):
            with self.subTest(academy=organization.slug):
                response = self.client.get(children_placements_url(organization))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class CrossAcademyTeacherTests(TwoAcademies):
    """Invariants 5 and 6 — the phase spec's cross-academy teacher section.

    .. code-block:: text

        Teacher T
            Academy A  ->  Tajweed
            Academy B  ->  Arabic

    One ``User``, two memberships, two independent sets of eligibility. This is the
    thing ``TeacherProfile.specialties`` could not express, and the reason
    ``TeacherTrack`` hangs off the membership rather than the profile.
    """

    def setUp(self):
        super().setUp()
        self.teacher = SubTeacherFactory(username="teacher-both")
        self.membership_here = admit(self.teacher, self.here)
        self.membership_there = admit(self.teacher, self.there)

        self.arabic_there = TrackFactory(
            organization=self.there, name="Arabic", slug="arabic"
        )
        self.teaches_here = TeacherTrackFactory(
            membership=self.membership_here, track=self.track_here
        )
        self.teaches_there = TeacherTrackFactory(
            membership=self.membership_there, track=self.arabic_there
        )

    def test_one_account_holds_two_independent_memberships(self):
        self.assertEqual(self.teacher.organization_memberships.count(), 2)
        self.assertEqual(TeacherTrack.objects.filter(membership__user=self.teacher).count(), 2)

    def test_the_teacher_sees_only_this_academys_tracks_through_this_route(self):
        self.client.force_authenticate(user=self.teacher)

        here = self.client.get(my_teachers_url(self.here))
        self.assertEqual(here.status_code, status.HTTP_200_OK)
        self.assertEqual([row["track_slug"] for row in here.data], ["tajweed"])
        self.assertEqual({row["organization"] for row in here.data}, {self.here.pk})

        there = self.client.get(my_teachers_url(self.there))
        self.assertEqual([row["track_slug"] for row in there.data], ["arabic"])
        self.assertEqual({row["organization"] for row in there.data}, {self.there.pk})

    def test_the_academys_roster_holds_only_its_own_assignments(self):
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.get(teachers_url(self.here))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.teaches_here.pk])

    def test_an_admin_here_cannot_read_an_assignment_there(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.get(teacher_url(self.here, self.teaches_there))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_an_admin_here_cannot_withdraw_eligibility_there(self):
        """The spec's explicit requirement, and the one worth stating as a test."""
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.patch(
            teacher_url(self.here, self.teaches_there), {"active": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.teaches_there.refresh_from_db()
        self.assertTrue(self.teaches_there.active)

    def test_an_admin_here_cannot_grant_a_foreign_track(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.post(
            teachers_url(self.here),
            {"user": self.teacher.pk, "track": self.arabic_there.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)
        self.assertEqual(TeacherTrack.objects.count(), 2)

    def test_an_admin_cannot_configure_a_teacher_through_another_academys_route(self):
        self.client.force_authenticate(user=self.admin_here)
        response = self.client.post(
            teachers_url(self.there),
            {"user": self.teacher.pk, "track": self.arabic_there.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_eligibility_here_says_nothing_about_eligibility_there(self):
        """Withdraw in A; B is untouched. Two rows, two decisions."""
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.patch(
            teacher_url(self.here, self.teaches_here), {"active": False}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.teaches_there.refresh_from_db()
        self.assertTrue(self.teaches_there.active)

    def test_a_teacher_of_one_academy_is_a_stranger_to_the_other(self):
        outsider = SubTeacherFactory(username="teacher-a-only")
        admit(outsider, self.here)
        self.client.force_authenticate(user=outsider)
        response = self.client.get(my_teachers_url(self.there))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class PlacementAudioIsolationTests(TwoAcademies):
    """Invariant 7 — Phase 6's private audio, now with an academy in front of it."""

    def test_the_academys_own_lead_can_mint_a_url(self):
        self.client.force_authenticate(user=self.lead_here)
        response = self.client.get(audio_url(self.here, self.placement_here))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["url"])

    def test_a_foreign_lead_cannot_mint_a_url_for_a_placement_here(self):
        """Lead B, Placement A. A 404 from this academy's queryset."""
        self.client.force_authenticate(user=self.lead_there)
        response = self.client.get(audio_url(self.there, self.placement_here))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_foreign_lead_cannot_use_this_academys_route_either(self):
        self.client.force_authenticate(user=self.lead_there)
        response = self.client.get(audio_url(self.here, self.placement_here))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_mint_a_url_for_another_academys_student(self):
        self.client.force_authenticate(user=self.student_here)
        response = self.client.get(audio_url(self.here, self.placement_there))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_lead_who_belongs_to_both_academies_is_scoped_per_route(self):
        """The honest case: real authority in both, and still no crossing.

        A lead teacher admitted to A and B may mint URLs for A's placements through
        A's route and B's through B's — and each route refuses the other's rows, so
        the membership never becomes a platform-wide key.
        """
        both = LeadTeacherFactory(username="lead-both")
        admit(both, self.here)
        admit(both, self.there)
        self.client.force_authenticate(user=both)

        self.assertEqual(
            self.client.get(audio_url(self.here, self.placement_here)).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(audio_url(self.there, self.placement_there)).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.client.get(audio_url(self.here, self.placement_there)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.get(audio_url(self.there, self.placement_here)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_minted_url_still_only_serves_its_own_sample(self):
        """Phase 6's token scoping, re-asserted across a tenant boundary.

        The download route is global by design, so this is the test that the token
        rather than the path is what protects it: a token minted in Academy A
        cannot be pointed at Academy B's placement.
        """
        from curriculum.audio import AUDIO_TOKEN_PARAM
        from django.urls import reverse

        self.client.force_authenticate(user=self.lead_here)
        minted = self.client.get(audio_url(self.here, self.placement_here)).data["url"]
        token = minted.split(f"{AUDIO_TOKEN_PARAM}=")[1]

        foreign = reverse(
            "curriculum:placement-audio-download", args=[self.placement_there.pk]
        )
        self.client.force_authenticate(user=None)
        response = self.client.get(f"{foreign}?{AUDIO_TOKEN_PARAM}={token}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class SameSlugAcrossAcademiesTests(TwoAcademies):
    """The uniqueness change, proved from both directions."""

    def test_both_academies_hold_a_track_with_the_same_slug(self):
        self.assertEqual(Track.objects.filter(slug="tajweed").count(), 2)
        self.assertNotEqual(self.track_here.pk, self.track_there.pk)

    def test_a_second_track_with_that_slug_is_refused_within_one_academy(self):
        self.client.force_authenticate(user=self.owner_here)
        response = self.client.post(
            tracks_url(self.here),
            {"name": "Tajweed Again", "slug": "tajweed"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data)

    def test_each_academy_reads_its_own_track_for_that_slug(self):
        for organization, expected in ((self.here, self.track_here), (self.there, self.track_there)):
            with self.subTest(academy=organization.slug):
                caller = (
                    self.student_here if organization == self.here else self.student_there
                )
                self.client.force_authenticate(user=caller)
                response = self.client.get(tracks_url(organization))
                self.assertEqual([t["id"] for t in response.data], [expected.pk])


class QuerysetIsolationTests(TwoAcademies):
    """The querysets themselves, below the API.

    Every academy-scoped view is only as safe as the queryset it starts from, so
    the three that carry the boundary are asserted directly. A view that forgot its
    filter would still pass an API test written against one academy; this is what
    catches the definition rather than the usage.
    """

    def test_tracks_are_scoped_by_their_own_column(self):
        self.assertEqual(
            list(Track.objects.filter(organization=self.here)), [self.track_here]
        )

    def test_levels_are_scoped_through_their_track(self):
        self.assertEqual(
            set(Level.objects.filter(track__organization=self.here)),
            set(self.track_here.levels.all()),
        )

    def test_placements_are_scoped_by_track_and_membership_together(self):
        self.assertEqual(
            list(PlacementResult.objects.in_organization(self.here)),
            [self.placement_here],
        )
        self.assertEqual(
            list(PlacementResult.objects.in_organization(self.there)),
            [self.placement_there],
        )

    def test_teacher_tracks_are_scoped_from_both_ends(self):
        teacher = SubTeacherFactory()
        here = TeacherTrackFactory(
            membership=admit(teacher, self.here), track=self.track_here
        )
        TeacherTrackFactory(
            membership=admit(teacher, self.there), track=self.track_there
        )
        self.assertEqual(
            list(TeacherTrack.objects.in_organization(self.here)), [here]
        )
