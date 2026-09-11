"""API tests for the assessment app.

The model tests prove the rules; these prove the *surface* — that each endpoint
answers the right person with the right shape, and refuses everyone else. That
split matters here more than in earlier phases because CLAUDE.md is explicit
about it: "Progress endpoints must enforce family scoping server-side.
Serializer omission alone is not a permission boundary." So the assertions below
come in pairs — a role that may call an endpoint gets a useful answer, and a role
that may not gets a 403/404 *before* any data is built, with nothing written.

Every endpoint in ``assessment/urls.py`` is covered by a happy path, at least one
authorization failure and at least one domain failure, per CLAUDE.md's testing
requirements. Phase 7 acceptance criteria are cited by number where a test is the
proof of one.

Two conventions inherited from the earlier phases' API tests:

* ``format="json"`` on every write. The payloads here nest (a list of criterion
  scores), which multipart cannot express.
* another teacher's booking is a **404**, not a 403 — the view scopes its lookup
  to the caller's own sessions, and a 403 would confirm that somebody else's
  booking exists.
"""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone as dj_timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.tests.factories import (
    LeadTeacherFactory,
    ParentFactory,
    ParentLinkFactory,
    StudentFactory,
    SubTeacherFactory,
)
from assessment.models import (
    AssessmentCriterion,
    AssessmentRubric,
    ProgressSnapshot,
    SessionAssessment,
)
from curriculum.models import PlacementResult
from curriculum.tests.factories import (
    LevelFactory,
    ReviewedPlacementFactory,
    TrackFactory,
    admit,
)
from scheduling.models import BookingStatus
from scheduling.tests.factories import (
    AvailabilityFactory,
    BookableTeacherFactory,
    BookingFactory,
    CancelledBookingFactory,
    ensure_teacher_configured,
    slot_at,
)

from .factories import (
    DEFAULT_SCORE,
    AssessmentCriterionFactory,
    AssessmentRubricFactory,
    FlaggedAssessmentFactory,
    ProgressSnapshotFactory,
    PublishedSnapshotFactory,
    RubricWithCriteriaFactory,
    SessionAssessmentFactory,
    past_completed_booking,
)


def _url(name, organization, **kwargs):
    return reverse(
        f"assessment:{name}",
        kwargs={"organization_pk": getattr(organization, "pk", organization), **kwargs},
    )


def rubrics_url(organization):
    return _url("rubric-list", organization)


def rubric_url(rubric_or_pk, organization=None):
    if hasattr(rubric_or_pk, "organization"):
        org = rubric_or_pk.organization
        pk = rubric_or_pk.pk
    else:
        org = organization
        pk = rubric_or_pk
    return _url("rubric-detail", org, pk=pk)


def submit_url(booking_or_pk, organization=None):
    if hasattr(booking_or_pk, "organization"):
        org = booking_or_pk.organization
        pk = booking_or_pk.pk
    elif hasattr(booking_or_pk, "level"):
        org = booking_or_pk.level.track.organization
        pk = booking_or_pk.pk
    else:
        org = organization
        pk = booking_or_pk
    return _url("assessment-create", org, booking_id=pk)


def my_assessments_url(organization):
    return _url("assessment-mine", organization)


def child_assessments_url(organization):
    return _url("assessment-child", organization)


def teacher_assessments_url(organization):
    return _url("assessment-teacher-mine", organization)


def review_queue_url(organization):
    return _url("review-queue", organization)


def teacher_report_url(organization):
    return _url("report-teachers", organization)


def my_progress_url(organization):
    return _url("progress-mine", organization)


def child_progress_url(organization):
    return _url("progress-child", organization)


def snapshots_url(organization):
    return _url("snapshot-create", organization)


def lead_snapshots_url(organization):
    return _url("snapshot-list", organization)


def my_snapshots_url(organization):
    return _url("snapshot-mine", organization)


def child_snapshots_url(organization):
    return _url("snapshot-child", organization)


def detail_url(assessment_or_pk, organization=None):
    if hasattr(assessment_or_pk, "organization"):
        org = assessment_or_pk.organization
        pk = assessment_or_pk.pk
    else:
        org = organization
        pk = assessment_or_pk
    return _url("assessment-detail", org, pk=pk)


def review_url(assessment_or_pk, organization=None):
    if hasattr(assessment_or_pk, "organization"):
        org = assessment_or_pk.organization
        pk = assessment_or_pk.pk
    else:
        org = organization
        pk = assessment_or_pk
    return _url("assessment-review", org, pk=pk)


def iso(moment):
    """A period bound as the API takes it — always offset-aware."""
    return moment.isoformat()


class AssessmentAPIWorld(APITestCase):
    """One teacher with declared hours, one completed session, one rubric.

    The four roles are built once so every subclass can point an endpoint at the
    wrong one. ``self.teacher`` comes from the availability window rather than
    being built separately, because a booking's teacher always does.
    """

    @property
    def rubrics_url(self):
        return rubrics_url(self.organization)

    @property
    def my_assessments_url(self):
        return my_assessments_url(self.organization)

    @property
    def child_assessments_url(self):
        return child_assessments_url(self.organization)

    @property
    def teacher_assessments_url(self):
        return teacher_assessments_url(self.organization)

    @property
    def review_queue_url(self):
        return review_queue_url(self.organization)

    @property
    def teacher_report_url(self):
        return teacher_report_url(self.organization)

    @property
    def my_progress_url(self):
        return my_progress_url(self.organization)

    @property
    def child_progress_url(self):
        return child_progress_url(self.organization)

    @property
    def snapshots_url(self):
        return snapshots_url(self.organization)

    @property
    def lead_snapshots_url(self):
        return lead_snapshots_url(self.organization)

    @property
    def my_snapshots_url(self):
        return my_snapshots_url(self.organization)

    @property
    def child_snapshots_url(self):
        return child_snapshots_url(self.organization)

    def link(self, parent=None, student=None):
        link = ParentLinkFactory(
            **({"parent": parent} if parent else {}),
            **({"student": student} if student else {}),
        )
        admit(link.parent, self.organization)
        admit(link.student, self.organization)
        return link

    def setUp(self):
        self.level = LevelFactory()
        self.track = self.level.track
        self.organization = self.track.organization

        self.window = AvailabilityFactory(
            organization=self.organization,
            teacher=BookableTeacherFactory(),
        )
        self.teacher = self.window.teacher
        ensure_teacher_configured(self.teacher, self.organization)

        self.lead = LeadTeacherFactory()
        admit(self.lead, self.organization)
        ensure_teacher_configured(self.lead, self.organization)

        self.sub = SubTeacherFactory()
        admit(self.sub, self.organization)
        ensure_teacher_configured(self.sub, self.organization)

        self.student = StudentFactory()
        admit(self.student, self.organization)

        self.parent = ParentFactory()
        admit(self.parent, self.organization)

        self.rubric = RubricWithCriteriaFactory(track=self.track)
        self.criteria = list(self.rubric.active_criteria())

    # --- helpers ------------------------------------------------------------

    def as_(self, user):
        self.client.force_authenticate(user=user)
        return self.client

    def taught(self, *, student=None, weeks_ago=1, level=None):
        """A completed session of ``self.teacher``'s that can be assessed."""
        return past_completed_booking(
            availability=self.window,
            level=level or self.level,
            weeks_ago=weeks_ago,
            **({"student": student} if student is not None else {}),
        )

    def score_payload(self, value=DEFAULT_SCORE, criteria=None):
        return [
            {"criterion": criterion.pk, "score": value}
            for criterion in (criteria if criteria is not None else self.criteria)
        ]

    def submit(self, booking, *, user=None, **overrides):
        body = {"scores": self.score_payload(), "teacher_summary": "Steady work."}
        body.update(overrides)
        return self.as_(user or booking.teacher).post(
            submit_url(booking), body, format="json"
        )


class RubricConfigurationAPITests(AssessmentAPIWorld):
    """``/rubrics/`` — the lead decides what every teacher is measured on."""

    def payload(self, **overrides):
        body = {
            "track": overrides.pop("track", None) or TrackFactory(organization=self.organization).pk,
            "name": "Tajweed sheet 2026",
            "description": "What we score a tajweed lesson on.",
            "criteria": [
                {"name": "Makhraj accuracy", "order": 1, "description": "Points of articulation."},
                {"name": "Madd rules", "order": 2},
                {"name": "Fluency", "order": 3},
            ],
        }
        body.update(overrides)
        return body

    def post_rubric(self, user=None, **overrides):
        return self.as_(user or self.lead).post(
            self.rubrics_url, self.payload(**overrides), format="json"
        )

    def test_the_lead_creates_a_rubric_with_ordered_criteria(self):
        """Acceptance criterion 1."""
        response = self.post_rubric()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Tajweed sheet 2026")
        self.assertTrue(response.data["active"])
        self.assertEqual(
            [row["name"] for row in response.data["criteria"]],
            ["Makhraj accuracy", "Madd rules", "Fluency"],
        )
        self.assertEqual([row["order"] for row in response.data["criteria"]], [1, 2, 3])
        self.assertEqual(len(response.data["active_criteria"]), 3)

    def test_the_created_rubric_is_the_one_a_new_assessment_would_use(self):
        response = self.post_rubric()
        rubric = AssessmentRubric.objects.get(pk=response.data["id"])

        self.assertEqual(AssessmentRubric.active_for_track(rubric.track), rubric)

    def test_a_sub_teacher_cannot_configure_a_rubric(self):
        """Acceptance criterion 2."""
        response = self.post_rubric(user=self.sub)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(AssessmentRubric.objects.count(), 1, "only setUp's rubric")

    def test_an_approved_teacher_is_still_not_a_lead(self):
        """The gate is the role, not approval — see permissions.py."""
        response = self.post_rubric(user=self.teacher)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_configure_a_rubric(self):
        """Acceptance criterion 2."""
        self.assertEqual(
            self.post_rubric(user=self.student).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_configure_a_rubric(self):
        """Acceptance criterion 2."""
        self.assertEqual(
            self.post_rubric(user=self.parent).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_an_anonymous_caller_cannot_configure_a_rubric(self):
        response = self.client.post(self.rubrics_url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(AssessmentRubric.objects.count(), 1)

    def test_a_repeated_order_in_one_request_is_rejected(self):
        """Acceptance criterion 3."""
        response = self.post_rubric(
            criteria=[
                {"name": "Makhraj accuracy", "order": 1},
                {"name": "Fluency", "order": 1},
            ]
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("criteria", response.data)
        self.assertEqual(AssessmentRubric.objects.count(), 1, "nothing was created")

    def test_a_rubric_needs_at_least_one_criterion(self):
        """A sheet with nothing on it is not something a teacher could score."""
        response = self.post_rubric(criteria=[])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("criteria", response.data)

    def test_a_new_criterion_needs_a_name_and_an_order(self):
        response = self.post_rubric(criteria=[{"description": "Nameless."}])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("criteria", response.data)

    def test_order_starts_at_one(self):
        response = self.post_rubric(criteria=[{"name": "Zeroth", "order": 0}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_track_is_rejected(self):
        response = self.post_rubric(track=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)

    def test_a_second_rubric_for_a_track_supersedes_the_first(self):
        """The ``PricingAgreement`` pattern: superseded, never deleted."""
        replacement = self.post_rubric(track=self.track.pk, name="2027 sheet")

        self.assertEqual(replacement.status_code, status.HTTP_201_CREATED)
        self.rubric.refresh_from_db()
        self.assertFalse(self.rubric.active)
        self.assertTrue(AssessmentRubric.objects.filter(pk=self.rubric.pk).exists())
        self.assertEqual(
            AssessmentRubric.active_for_track(self.track).pk, replacement.data["id"]
        )

    def test_the_lead_lists_a_tracks_rubrics_newest_first(self):
        self.post_rubric(track=self.track.pk, name="2027 sheet")

        response = self.as_(self.lead).get(self.rubrics_url, {"track_id": self.track.pk})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["name"] for row in response.data][0], "2027 sheet")
        self.assertEqual(len(response.data), 2, "the superseded sheet is still listed")

    def test_the_list_can_be_narrowed_to_one_track(self):
        elsewhere = RubricWithCriteriaFactory()

        response = self.as_(self.lead).get(self.rubrics_url, {"track_id": self.track.pk})

        self.assertEqual([row["id"] for row in response.data], [self.rubric.pk])
        self.assertNotIn(elsewhere.pk, [row["id"] for row in response.data])

    def test_a_non_numeric_track_id_is_a_400(self):
        response = self.as_(self.lead).get(self.rubrics_url, {"track_id": "tajweed"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track_id", response.data)

    def test_a_sub_teacher_cannot_read_rubric_configuration(self):
        """Acceptance criterion 2 — reading configuration is gated too."""
        response = self.as_(self.sub).get(self.rubrics_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_lead_reads_one_rubric(self):
        response = self.as_(self.lead).get(rubric_url(self.rubric))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.rubric.pk)
        self.assertEqual(response.data["track"], self.track.slug)
        self.assertEqual(len(response.data["criteria"]), 3)

    def test_a_retired_criterion_is_listed_but_not_active(self):
        retired = self.criteria[-1]
        retired.active = False
        retired.save()

        response = self.as_(self.lead).get(rubric_url(self.rubric))

        self.assertEqual(len(response.data["criteria"]), 3)
        self.assertEqual(len(response.data["active_criteria"]), 2)
        self.assertNotIn(
            retired.pk, [row["id"] for row in response.data["active_criteria"]]
        )

    def test_a_student_cannot_read_one_rubric(self):
        response = self.as_(self.student).get(rubric_url(self.rubric))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_rubric_is_a_404(self):
        response = self.as_(self.lead).get(
            rubric_url(999999, organization=self.organization)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RubricEditAPITests(AssessmentAPIWorld):
    """``PATCH /rubrics/{id}/`` — live configuration moves, history does not.

    CLAUDE.md's historical-data rule, asserted over HTTP: a rubric edit changes
    what the *next* assessment records and leaves every submitted one exactly as
    the teacher left it.
    """

    def setUp(self):
        super().setUp()
        self.booking = self.taught(weeks_ago=1)
        self.assessment = SessionAssessmentFactory(booking=self.booking)

    def patch_rubric(self, user=None, **body):
        return self.as_(user or self.lead).patch(
            rubric_url(self.rubric), body, format="json"
        )

    def test_renaming_a_criterion_leaves_a_submitted_assessment_alone(self):
        """Acceptance criterion 4."""
        criterion = self.criteria[0]
        original = criterion.name

        response = self.patch_rubric(
            criteria=[{"id": criterion.pk, "name": "Makhraj (2027 wording)"}]
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.assessment.scores.get(criterion=criterion).criterion_name, original
        )
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.criteria_snapshot[0]["name"], original)

    def test_renaming_a_criterion_changes_the_next_assessment(self):
        """The other half of criterion 4: the edit is not a no-op either."""
        criterion = self.criteria[0]
        self.patch_rubric(criteria=[{"id": criterion.pk, "name": "Makhraj (2027 wording)"}])

        response = self.submit(self.taught(weeks_ago=2))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        names = {row["criterion_name"] for row in response.data["scores"]}
        self.assertIn("Makhraj (2027 wording)", names)

    def test_the_renamed_criterion_reads_as_one_criterion_not_two(self):
        """A rename must not split a criterion's history — see criterion_averages_for."""
        criterion = self.criteria[0]
        self.patch_rubric(criteria=[{"id": criterion.pk, "name": "Makhraj (2027 wording)"}])
        self.submit(self.taught(weeks_ago=2))

        self.assertEqual(
            AssessmentCriterion.objects.filter(rubric=self.rubric).count(), 3
        )

    def test_retiring_a_criterion_leaves_the_old_score_in_place(self):
        """Acceptance criterion 4/5: deactivating is not deleting."""
        retired = self.criteria[-1]

        response = self.patch_rubric(criteria=[{"id": retired.pk, "active": False}])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.assessment.scores.count(), 3)
        self.assertTrue(self.assessment.scores.filter(criterion=retired).exists())

    def test_a_new_assessment_scores_only_the_active_criteria(self):
        """Acceptance criterion 5."""
        retired = self.criteria[-1]
        self.patch_rubric(criteria=[{"id": retired.pk, "active": False}])

        response = self.submit(
            self.taught(weeks_ago=2),
            scores=[
                {"criterion": c.pk, "score": 4} for c in self.criteria if c != retired
            ],
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data["scores"]), 2)
        self.assertEqual(len(response.data["criteria_snapshot"]), 2)

    def test_scoring_a_retired_criterion_is_rejected(self):
        retired = self.criteria[-1]
        self.patch_rubric(criteria=[{"id": retired.pk, "active": False}])

        response = self.submit(self.taught(weeks_ago=2))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scores", response.data)

    def test_a_criterion_can_be_appended(self):
        response = self.patch_rubric(criteria=[{"name": "Waqf and ibtida", "order": 4}])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["active_criteria"]), 4)
        self.assertEqual(response.data["active_criteria"][-1]["name"], "Waqf and ibtida")

    def test_a_repeated_order_is_rejected(self):
        """Acceptance criterion 3, on an edit rather than a create."""
        response = self.patch_rubric(criteria=[{"id": self.criteria[0].pk, "order": 2}])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.criteria[0].refresh_from_db()
        self.assertEqual(self.criteria[0].order, 1)

    def test_a_criterion_from_another_rubric_is_rejected(self):
        stranger = AssessmentCriterionFactory()

        response = self.patch_rubric(criteria=[{"id": stranger.pk, "name": "Hijacked"}])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("criteria", response.data)
        stranger.refresh_from_db()
        self.assertNotEqual(stranger.name, "Hijacked")

    def test_the_rubric_itself_can_be_renamed(self):
        response = self.patch_rubric(name="Tajweed sheet, revised")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Tajweed sheet, revised")

    def test_renaming_the_rubric_leaves_the_submitted_snapshot_alone(self):
        """Acceptance criterion 4, for ``rubric_name``."""
        original = self.assessment.rubric_name
        self.patch_rubric(name="Tajweed sheet, revised")

        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.rubric_name, original)

    def test_deactivating_the_rubric_stops_new_assessments(self):
        self.patch_rubric(active=False)

        response = self.submit(self.taught(weeks_ago=2))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SessionAssessment.objects.count(), 1)

    def test_a_sub_teacher_cannot_edit_a_rubric(self):
        """Acceptance criterion 2."""
        response = self.patch_rubric(user=self.sub, name="Mine now")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.rubric.refresh_from_db()
        self.assertNotEqual(self.rubric.name, "Mine now")

    def test_a_student_cannot_edit_a_rubric(self):
        response = self.patch_rubric(user=self.student, name="Mine now")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_edit_a_rubric(self):
        response = self.client.patch(
            rubric_url(self.rubric), {"name": "Mine now"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_whole_object_replace_is_not_offered(self):
        """PUT is deliberately absent — see the view's docstring."""
        response = self.as_(self.lead).put(
            rubric_url(self.rubric), {"name": "Replaced"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_a_rubric_cannot_be_deleted(self):
        """Historical assessments point at its criteria."""
        response = self.as_(self.lead).delete(rubric_url(self.rubric))
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_a_refused_edit_leaves_the_whole_rubric_as_it_was(self):
        """A 400 must not commit the half of the request that was valid.

        Both reachable failure modes are here, because they fail in different
        layers and only a transaction covers both: a criterion id from another
        rubric is refused by the serializer, and a criterion moved onto an
        occupied position is refused by the model. Either way the rename that
        travelled in the same request must not survive.
        """
        stranger = AssessmentCriterionFactory()
        cases = {
            "another rubric's criterion": [{"id": stranger.pk, "name": "Hijacked"}],
            "an occupied position": [{"id": self.criteria[0].pk, "order": 2}],
        }
        for label, criteria in cases.items():
            with self.subTest(refused_by=label):
                original = self.rubric.name

                response = self.patch_rubric(name="Renamed", criteria=criteria)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.rubric.refresh_from_db()
                self.assertEqual(self.rubric.name, original)

    def test_a_refused_edit_does_not_move_a_criterion(self):
        stranger = AssessmentCriterionFactory()

        self.patch_rubric(
            criteria=[
                {"id": self.criteria[0].pk, "name": "Renamed first"},
                {"id": stranger.pk, "name": "Hijacked"},
            ]
        )

        self.criteria[0].refresh_from_db()
        self.assertNotEqual(self.criteria[0].name, "Renamed first")


class AssessmentSubmissionAPITests(AssessmentAPIWorld):
    """``POST /bookings/{id}/`` — the teacher who taught the session scores it."""

    def setUp(self):
        super().setUp()
        self.booking = self.taught(weeks_ago=1)

    def test_the_assigned_teacher_assesses_a_completed_booking(self):
        """Acceptance criterion 6."""
        response = self.submit(self.booking, teacher_summary="Held the madd well.")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["teacher_summary"], "Held the madd well.")
        self.assertEqual(len(response.data["scores"]), 3)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("4.00"))
        self.assertEqual(response.data["booking"]["id"], self.booking.pk)
        self.assertEqual(response.data["student"]["id"], self.booking.student_id)

    def test_the_submission_snapshots_the_rubric_and_criterion_names(self):
        response = self.submit(self.booking)

        self.assertEqual(response.data["rubric_name"], self.rubric.name)
        self.assertEqual(
            [row["name"] for row in response.data["criteria_snapshot"]],
            [c.name for c in self.criteria],
        )
        self.assertEqual(
            [row["criterion_name"] for row in response.data["scores"]],
            [c.name for c in self.criteria],
        )

    def test_the_assessor_is_the_requesting_teacher_not_a_client_field(self):
        other = BookableTeacherFactory()

        response = self.submit(self.booking, assessed_by=other.pk)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            SessionAssessment.objects.get(pk=response.data["id"]).assessed_by,
            self.teacher,
        )

    def test_criterion_comments_are_stored(self):
        scores = self.score_payload()
        scores[0]["comment"] = "Qalqalah still soft on qaf."

        response = self.submit(self.booking, scores=scores)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["scores"][0]["comment"], "Qalqalah still soft on qaf.")

    def test_the_teacher_can_flag_the_session_for_the_lead(self):
        response = self.submit(
            self.booking,
            flagged_for_review=True,
            flag_reason="Second opinion on the madd, please.",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["flagged_for_review"])
        self.assertIn(
            response.data["id"],
            SessionAssessment.pending_lead_review().values_list("pk", flat=True),
        )

    def test_a_flag_reason_without_a_flag_is_rejected(self):
        response = self.submit(self.booking, flag_reason="Worried, but not flagging.")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("flag_reason", response.data)
        self.assertFalse(SessionAssessment.objects.exists())

    # --- authorization ------------------------------------------------------

    def test_another_teachers_session_is_a_404_not_a_403(self):
        """Acceptance criterion 7. A 403 would confirm the booking exists."""
        stranger = BookableTeacherFactory()
        ensure_teacher_configured(stranger, self.organization)

        response = self.submit(self.booking, user=stranger)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_student_cannot_assess(self):
        """Acceptance criterion 7."""
        response = self.submit(self.booking, user=self.booking.student)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_parent_cannot_assess(self):
        """Acceptance criterion 7."""
        link = self.link()
        booking = self.taught(student=link.student, weeks_ago=2)

        response = self.submit(booking, user=link.parent)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_an_anonymous_caller_cannot_assess(self):
        response = self.client.post(
            submit_url(self.booking), {"scores": self.score_payload()}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_an_unknown_booking_is_a_404(self):
        response = self.as_(self.teacher).post(
            submit_url(999999, organization=self.organization),
            {"scores": self.score_payload()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- domain failures ----------------------------------------------------

    def test_a_scheduled_booking_cannot_be_assessed(self):
        """Acceptance criterion 8."""
        scheduled = BookingFactory(availability=self.window, level=self.level)

        response = self.submit(scheduled)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_cancelled_booking_cannot_be_assessed(self):
        """Acceptance criterion 8."""
        cancelled = CancelledBookingFactory(availability=self.window, level=self.level)

        response = self.submit(cancelled)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)

    def test_a_no_show_booking_cannot_be_assessed(self):
        """Acceptance criterion 8."""
        no_show = BookingFactory(availability=self.window, level=self.level)
        no_show.status = BookingStatus.NO_SHOW
        no_show.save()

        response = self.submit(no_show)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)

    def test_submitting_does_not_mark_a_booking_completed(self):
        """The spec is explicit that this stays a separate act."""
        scheduled = BookingFactory(availability=self.window, level=self.level)

        self.submit(scheduled)

        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, BookingStatus.SCHEDULED)

    def test_a_successful_submission_does_not_change_the_booking(self):
        before = (self.booking.status, self.booking.start_time_utc)

        self.submit(self.booking)

        self.booking.refresh_from_db()
        self.assertEqual((self.booking.status, self.booking.start_time_utc), before)

    def test_a_missing_criterion_is_rejected_not_scored_zero(self):
        """Acceptance criterion 9."""
        response = self.submit(
            self.booking, scores=self.score_payload(criteria=self.criteria[:-1])
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scores", response.data)
        self.assertIn(self.criteria[-1].name, str(response.data["scores"]))
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_duplicated_criterion_is_rejected(self):
        """Acceptance criterion 10."""
        scores = self.score_payload()
        scores.append({"criterion": self.criteria[0].pk, "score": 2})

        response = self.submit(self.booking, scores=scores)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scores", response.data)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_an_unknown_criterion_is_rejected(self):
        scores = self.score_payload()
        scores.append({"criterion": AssessmentCriterionFactory().pk, "score": 4})

        response = self.submit(self.booking, scores=scores)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scores", response.data)

    def test_a_score_above_the_scale_is_rejected(self):
        """Acceptance criterion 11."""
        scores = self.score_payload()
        scores[0]["score"] = 6

        response = self.submit(self.booking, scores=scores)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_a_score_below_the_scale_is_rejected(self):
        """Acceptance criterion 11."""
        scores = self.score_payload()
        scores[0]["score"] = 0

        response = self.submit(self.booking, scores=scores)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_an_empty_score_list_is_rejected(self):
        response = self.submit(self.booking, scores=[])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scores", response.data)

    def test_a_second_assessment_for_the_same_booking_is_rejected(self):
        """Acceptance criterion 12."""
        self.submit(self.booking)

        response = self.submit(self.booking, teacher_summary="Second thoughts.")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("booking", response.data)
        self.assertEqual(SessionAssessment.objects.count(), 1)

    def test_a_track_with_no_active_rubric_cannot_be_assessed(self):
        elsewhere = LevelFactory(track__organization=self.organization)
        booking = self.taught(level=elsewhere, weeks_ago=3)

        response = self.submit(booking, scores=self.score_payload())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("__all__", response.data)
        self.assertFalse(SessionAssessment.objects.exists())

    def test_submitting_does_not_touch_the_placement_recommended_level(self):
        """Acceptance criterion 24 — Phase 7 does not move a student's level."""
        placement = ReviewedPlacementFactory(track__organization=self.organization)
        RubricWithCriteriaFactory(track=placement.track)
        booking = self.taught(
            student=placement.student, level=placement.recommended_level, weeks_ago=2
        )
        rubric = AssessmentRubric.active_for_track(placement.track)

        response = self.submit(
            booking,
            scores=[
                {"criterion": c.pk, "score": 1} for c in rubric.active_criteria()
            ],
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        placement.refresh_from_db()
        self.assertEqual(placement.recommended_level, booking.level)
        self.assertEqual(
            PlacementResult.objects.filter(student=placement.student).count(), 1
        )


class FamilyAssessmentReadAPITests(AssessmentAPIWorld):
    """``/mine/`` and ``/child/`` — teaching content, and nothing internal.

    Acceptance criterion 13: the teacher summary is family-visible; the flag and
    review fields are not. They are absent from the shape rather than blanked,
    which is why these tests use ``assertNotIn`` on the payload keys.
    """

    def setUp(self):
        super().setUp()
        self.link = self.link()
        self.child = self.link.student
        self.assessment = FlaggedAssessmentFactory(
            booking=self.taught(student=self.student, weeks_ago=1),
            teacher_summary="Fluency is coming along; keep the daily revision.",
        )
        self.assessment.record_lead_review(
            reviewed_by=self.lead, note="Internal: teacher needs support here."
        )

    def test_a_student_reads_their_own_assessments(self):
        response = self.as_(self.student).get(self.my_assessments_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.assessment.pk])
        self.assertEqual(
            response.data[0]["teacher_summary"],
            "Fluency is coming along; keep the daily revision.",
        )

    def test_the_family_shape_carries_the_scores_and_the_average(self):
        """Acceptance criterion 13 — they see the teaching content."""
        payload = self.as_(self.student).get(self.my_assessments_url).data[0]

        self.assertEqual(len(payload["scores"]), 3)
        self.assertEqual(Decimal(payload["overall_average"]), Decimal("4.00"))
        self.assertEqual(payload["rubric_name"], self.rubric.name)
        self.assertEqual(payload["assessed_by"], self.teacher.username)

    def test_no_internal_quality_control_field_reaches_a_student(self):
        """Acceptance criterion 13."""
        payload = self.as_(self.student).get(self.my_assessments_url).data[0]

        for field in (
            "flagged_for_review",
            "flag_reason",
            "lead_review_note",
            "lead_reviewed_at",
            "lead_reviewed_by",
        ):
            with self.subTest(field=field):
                self.assertNotIn(field, payload)

    def test_the_flag_reason_text_appears_nowhere_in_the_family_response(self):
        """Belt and braces: not merely a missing key, but absent content."""
        response = self.as_(self.student).get(self.my_assessments_url)

        body = str(response.data)
        self.assertNotIn(self.assessment.flag_reason, body)
        self.assertNotIn("Internal: teacher needs support here.", body)

    def test_another_students_assessment_is_not_listed(self):
        SessionAssessmentFactory(booking=self.taught(weeks_ago=3))

        response = self.as_(self.student).get(self.my_assessments_url)
        self.assertEqual([row["id"] for row in response.data], [self.assessment.pk])

    def test_track_id_narrows_a_students_list(self):
        response = self.as_(self.student).get(
            self.my_assessments_url, {"track_id": TrackFactory(organization=self.organization).pk}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    def test_a_teacher_cannot_use_the_student_endpoint(self):
        for user in (self.teacher, self.lead):
            with self.subTest(role=user.role):
                response = self.as_(user).get(self.my_assessments_url)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_use_the_student_endpoint(self):
        response = self.as_(self.link.parent).get(self.my_assessments_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_read_assessments(self):
        self.assertEqual(
            self.client.get(self.my_assessments_url).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    # --- the parent side ----------------------------------------------------

    def test_a_linked_parent_reads_their_childs_assessments(self):
        """Acceptance criterion 21."""
        child_assessment = SessionAssessmentFactory(
            booking=self.taught(student=self.child, weeks_ago=2)
        )

        response = self.as_(self.link.parent).get(
            self.child_assessments_url, {"student_id": self.child.pk}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [child_assessment.pk])

    def test_the_parent_shape_hides_the_internal_fields_too(self):
        """Acceptance criterion 13."""
        FlaggedAssessmentFactory(booking=self.taught(student=self.child, weeks_ago=2))

        payload = self.as_(self.link.parent).get(
            self.child_assessments_url, {"student_id": self.child.pk}
        ).data[0]

        self.assertNotIn("flagged_for_review", payload)
        self.assertNotIn("flag_reason", payload)
        self.assertIn("teacher_summary", payload)

    def test_an_unrelated_parent_is_denied(self):
        """Acceptance criterion 22."""
        response = self.as_(self.parent).get(
            self.child_assessments_url, {"student_id": self.child.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_ask_about_a_student_who_is_not_theirs(self):
        """Acceptance criterion 22 — the link, not merely the role, is checked."""
        response = self.as_(self.link.parent).get(
            self.child_assessments_url, {"student_id": self.student.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_id_is_required_on_the_child_endpoint(self):
        response = self.as_(self.link.parent).get(self.child_assessments_url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", response.data)

    def test_a_student_cannot_use_the_child_endpoint(self):
        response = self.as_(self.student).get(
            self.child_assessments_url, {"student_id": self.child.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_teacher_cannot_use_the_child_endpoint(self):
        response = self.as_(self.teacher).get(
            self.child_assessments_url, {"student_id": self.child.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class TeacherAssessmentReadAPITests(AssessmentAPIWorld):
    """``/teacher/mine/`` — a teacher's own submissions, and only those."""

    def setUp(self):
        super().setUp()
        self.assessment = FlaggedAssessmentFactory(booking=self.taught(weeks_ago=1))
        self.assessment.record_lead_review(
            reviewed_by=self.lead, note="Internal: discussed at the weekly call."
        )

    def test_a_teacher_reads_their_own_submissions(self):
        response = self.as_(self.teacher).get(self.teacher_assessments_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.assessment.pk])
        self.assertEqual(len(response.data[0]["scores"]), 3)

    def test_a_teacher_sees_their_own_flag_and_reason(self):
        payload = self.as_(self.teacher).get(self.teacher_assessments_url).data[0]

        self.assertTrue(payload["flagged_for_review"])
        self.assertEqual(payload["flag_reason"], self.assessment.flag_reason)

    def test_a_teacher_can_see_that_the_lead_reviewed_but_not_what_they_wrote(self):
        """The spec's visibility table: no lead review notes for a sub-teacher."""
        payload = self.as_(self.teacher).get(self.teacher_assessments_url).data[0]

        self.assertIsNotNone(payload["lead_reviewed_at"])
        self.assertNotIn("lead_review_note", payload)
        self.assertNotIn("lead_reviewed_by", payload)
        self.assertNotIn("Internal: discussed at the weekly call.", str(payload))

    def test_another_teachers_submission_is_not_listed(self):
        other_window = AvailabilityFactory(teacher=BookableTeacherFactory())
        SessionAssessmentFactory(
            booking=past_completed_booking(
                availability=other_window, level=self.level, weeks_ago=2
            )
        )

        response = self.as_(self.teacher).get(self.teacher_assessments_url)
        self.assertEqual([row["id"] for row in response.data], [self.assessment.pk])

    def test_a_student_cannot_read_the_teacher_list(self):
        response = self.as_(self.student).get(self.teacher_assessments_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_read_the_teacher_list(self):
        response = self.as_(self.parent).get(self.teacher_assessments_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_read_the_teacher_list(self):
        self.assertEqual(
            self.client.get(self.teacher_assessments_url).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_a_teacher_with_nothing_submitted_gets_an_empty_list(self):
        response = self.as_(self.sub).get(self.teacher_assessments_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])


class LeadReviewAPITests(AssessmentAPIWorld):
    """The review queue, the drill-down, and the annotation.

    Acceptance criteria 14-16: a flagged assessment reaches the lead, review adds
    an annotation without overwriting the teacher's scores, and a reviewed
    assessment leaves the pending queue.
    """

    def setUp(self):
        super().setUp()
        self.flagged = FlaggedAssessmentFactory(booking=self.taught(weeks_ago=1))
        self.unflagged = SessionAssessmentFactory(booking=self.taught(weeks_ago=2))

    def test_a_flagged_assessment_appears_in_the_lead_queue(self):
        """Acceptance criterion 14."""
        response = self.as_(self.lead).get(self.review_queue_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [self.flagged.pk])

    def test_the_queue_carries_the_context_the_lead_needs(self):
        payload = self.as_(self.lead).get(self.review_queue_url).data[0]

        self.assertEqual(payload["student"]["id"], self.flagged.student_id)
        self.assertEqual(payload["assessed_by"]["id"], self.teacher.pk)
        self.assertEqual(payload["booking"]["id"], self.flagged.booking_id)
        self.assertEqual(payload["rubric_name"], self.rubric.name)
        self.assertEqual(payload["flag_reason"], self.flagged.flag_reason)

    def test_an_unflagged_assessment_is_not_in_the_queue(self):
        ids = [row["id"] for row in self.as_(self.lead).get(self.review_queue_url).data]
        self.assertNotIn(self.unflagged.pk, ids)

    def test_a_sub_teacher_cannot_read_the_review_queue(self):
        response = self.as_(self.teacher).get(self.review_queue_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_read_the_review_queue(self):
        response = self.as_(self.student).get(self.review_queue_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_read_the_review_queue(self):
        self.assertEqual(
            self.client.get(self.review_queue_url).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    # --- the drill-down -----------------------------------------------------

    def test_the_lead_opens_one_assessment_in_full(self):
        response = self.as_(self.lead).get(detail_url(self.flagged))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.flagged.pk)
        self.assertIn("flag_reason", response.data)
        self.assertIn("lead_review_note", response.data)
        self.assertEqual(len(response.data["criteria_snapshot"]), 3)

    def test_reading_an_assessment_does_not_review_it(self):
        """The spec: clearing a flag is an explicit action, not a side effect."""
        self.as_(self.lead).get(detail_url(self.flagged))

        self.flagged.refresh_from_db()
        self.assertIsNone(self.flagged.lead_reviewed_at)
        self.assertIn(self.flagged, SessionAssessment.pending_lead_review())

    def test_a_sub_teacher_cannot_open_an_assessment_detail(self):
        response = self.as_(self.teacher).get(detail_url(self.flagged))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_open_the_lead_detail_of_their_own_assessment(self):
        """Their own view is ``/mine/``, which has no internal fields."""
        response = self.as_(self.flagged.student).get(detail_url(self.flagged))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_assessment_detail_is_a_404(self):
        response = self.as_(self.lead).get(
            detail_url(999999, organization=self.organization)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # --- the annotation -----------------------------------------------------

    def review(self, assessment, user=None, **body):
        return self.as_(user or self.lead).post(
            review_url(assessment), body, format="json"
        )

    def test_the_lead_records_a_review_note(self):
        """Acceptance criterion 15."""
        response = self.review(
            self.flagged, lead_review_note="Spoke to the teacher; plan agreed."
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["lead_review_note"], "Spoke to the teacher; plan agreed."
        )
        self.assertEqual(response.data["lead_reviewed_by"], self.lead.username)
        self.assertIsNotNone(response.data["lead_reviewed_at"])

    def test_review_does_not_overwrite_the_teachers_scores(self):
        """Acceptance criterion 15."""
        before = list(self.flagged.scores.values_list("criterion_id", "score"))
        summary_before = self.flagged.teacher_summary

        self.review(self.flagged, lead_review_note="Noted.")

        self.flagged.refresh_from_db()
        self.assertEqual(
            list(self.flagged.scores.values_list("criterion_id", "score")), before
        )
        self.assertEqual(self.flagged.teacher_summary, summary_before)
        self.assertEqual(self.flagged.overall_average, Decimal("4.00"))

    def test_a_reviewed_assessment_leaves_the_pending_queue(self):
        """Acceptance criterion 16."""
        self.review(self.flagged, lead_review_note="Done.")

        response = self.as_(self.lead).get(self.review_queue_url)
        self.assertEqual(list(response.data), [])

    def test_review_does_not_clear_the_flag(self):
        """That the teacher raised one is part of the record."""
        self.review(self.flagged, lead_review_note="Done.")

        self.flagged.refresh_from_db()
        self.assertTrue(self.flagged.flagged_for_review)
        self.assertTrue(self.flagged.flag_reason)

    def test_the_note_is_optional(self):
        response = self.review(self.flagged)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lead_review_note"], "")
        self.assertIsNotNone(response.data["lead_reviewed_at"])

    def test_an_unflagged_assessment_may_still_be_reviewed(self):
        """The spec lets the lead monitor; only a flag *requires* review state."""
        response = self.review(self.unflagged, lead_review_note="Spot check.")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.unflagged.refresh_from_db()
        self.assertTrue(self.unflagged.is_lead_reviewed)
        self.assertFalse(self.unflagged.flagged_for_review)

    def test_a_sub_teacher_cannot_review(self):
        response = self.review(self.flagged, user=self.teacher, lead_review_note="Mine.")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.flagged.refresh_from_db()
        self.assertIsNone(self.flagged.lead_reviewed_at)

    def test_a_student_cannot_review_their_own_assessment(self):
        response = self.review(
            self.flagged, user=self.flagged.student, lead_review_note="All good!"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.flagged.refresh_from_db()
        self.assertEqual(self.flagged.lead_review_note, "")

    def test_a_parent_cannot_review(self):
        response = self.review(self.flagged, user=self.parent, lead_review_note="Hm.")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_review(self):
        response = self.client.post(
            review_url(self.flagged), {"lead_review_note": "Anon."}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_reviewing_an_unknown_assessment_is_a_404(self):
        response = self.as_(self.lead).post(
            review_url(999999, organization=self.organization),
            {"lead_review_note": "Nobody."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_review_endpoint_cannot_be_used_to_edit_teacher_data(self):
        """Only a note is writable — the serializer ignores anything else."""
        response = self.review(
            self.flagged,
            lead_review_note="Reviewed.",
            teacher_summary="Rewritten by the lead",
            flagged_for_review=False,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.flagged.refresh_from_db()
        self.assertNotEqual(self.flagged.teacher_summary, "Rewritten by the lead")
        self.assertTrue(self.flagged.flagged_for_review)


class TeacherQualityReportAPITests(AssessmentAPIWorld):
    """``/reports/teachers/`` — lead-only quality visibility, no leaderboard.

    Acceptance criteria 17-19: counts and averages for date/track filters, missing
    assessments never counted as zero, and no academy-wide reporting for a
    sub-teacher.
    """

    def setUp(self):
        super().setUp()
        self.other_window = AvailabilityFactory(teacher=BookableTeacherFactory())
        self.other_teacher = self.other_window.teacher

    def report(self, user=None, **params):
        return self.as_(user or self.lead).get(self.teacher_report_url, params)

    def rows_by_username(self, response):
        return {row["teacher"]["username"]: row for row in response.data}

    def assessed(self, *, window=None, score=DEFAULT_SCORE, weeks_ago=1, level=None, flagged=False):
        booking = past_completed_booking(
            availability=window or self.window,
            level=level or self.level,
            weeks_ago=weeks_ago,
        )
        factory = FlaggedAssessmentFactory if flagged else SessionAssessmentFactory
        return factory(booking=booking, score_value=score)

    def test_the_lead_reads_counts_and_averages(self):
        """Acceptance criterion 17."""
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=3, weeks_ago=2)

        response = self.report()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = self.rows_by_username(response)[self.teacher.username]
        self.assertEqual(row["assessed_sessions"], 2)
        self.assertEqual(Decimal(row["overall_average"]), Decimal("4.00"))

    def test_the_report_breaks_down_by_track(self):
        """Acceptance criterion 17."""
        elsewhere = LevelFactory(track__organization=self.organization)
        RubricWithCriteriaFactory(track=elsewhere.track)
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=1, weeks_ago=2, level=elsewhere)

        row = self.rows_by_username(self.report())[self.teacher.username]

        by_track = {entry["track"]["slug"]: entry for entry in row["by_track"]}
        self.assertEqual(
            Decimal(by_track[self.track.slug]["overall_average"]), Decimal("5.00")
        )
        self.assertEqual(
            Decimal(by_track[elsewhere.track.slug]["overall_average"]), Decimal("1.00")
        )
        self.assertEqual(by_track[self.track.slug]["assessed_sessions"], 1)

    def test_the_report_can_be_narrowed_to_a_period(self):
        """Acceptance criterion 17."""
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=1, weeks_ago=10)

        row = self.rows_by_username(
            self.report(**{"from": iso(dj_timezone.now() - timedelta(weeks=5))})
        )[self.teacher.username]

        self.assertEqual(row["assessed_sessions"], 1)
        self.assertEqual(Decimal(row["overall_average"]), Decimal("5.00"))

    def test_the_period_excludes_its_end(self):
        """Half-open, like every other period in this app."""
        self.assessed(score=5, weeks_ago=1)

        response = self.report(
            **{
                "from": iso(dj_timezone.now() - timedelta(weeks=10)),
                "to": iso(dj_timezone.now() - timedelta(weeks=2)),
            }
        )
        self.assertEqual(list(response.data), [])

    def test_the_report_can_be_narrowed_to_a_track(self):
        """Acceptance criterion 17."""
        elsewhere = LevelFactory(track__organization=self.organization)
        RubricWithCriteriaFactory(track=elsewhere.track)
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=1, weeks_ago=2, level=elsewhere)

        row = self.rows_by_username(self.report(track_id=self.track.pk))[
            self.teacher.username
        ]

        self.assertEqual(row["assessed_sessions"], 1)
        self.assertEqual(Decimal(row["overall_average"]), Decimal("5.00"))
        self.assertEqual([e["track"]["slug"] for e in row["by_track"]], [self.track.slug])

    def test_a_teacher_who_assessed_nothing_is_absent_not_zero(self):
        """Acceptance criterion 18."""
        self.assessed(score=4, weeks_ago=1)
        past_completed_booking(
            availability=self.other_window, level=self.level, weeks_ago=1
        )

        response = self.report()

        self.assertNotIn(self.other_teacher.username, self.rows_by_username(response))
        self.assertEqual(len(response.data), 1)

    def test_an_unassessed_session_does_not_drag_an_average_down(self):
        """Acceptance criterion 18 — missing data, not a zero."""
        self.assessed(score=5, weeks_ago=1)
        past_completed_booking(availability=self.window, level=self.level, weeks_ago=3)

        row = self.rows_by_username(self.report())[self.teacher.username]

        self.assertEqual(row["assessed_sessions"], 1)
        self.assertEqual(Decimal(row["overall_average"]), Decimal("5.00"))

    def test_the_report_counts_flags_and_what_is_still_awaiting_the_lead(self):
        flagged = self.assessed(weeks_ago=1, flagged=True)
        self.assessed(weeks_ago=2)

        row = self.rows_by_username(self.report())[self.teacher.username]
        self.assertEqual(row["flagged"], 1)
        self.assertEqual(row["awaiting_lead_review"], 1)

        flagged.record_lead_review(reviewed_by=self.lead, note="Handled.")

        row = self.rows_by_username(self.report())[self.teacher.username]
        self.assertEqual(row["flagged"], 1, "the flag stays on the record")
        self.assertEqual(row["awaiting_lead_review"], 0)

    def test_rows_come_back_by_username_not_by_score(self):
        """The spec forbids a leaderboard; ordering by name is the point."""
        self.assessed(score=1, weeks_ago=1)
        self.assessed(window=self.other_window, score=5, weeks_ago=2)

        response = self.report()

        usernames = [row["teacher"]["username"] for row in response.data]
        self.assertEqual(usernames, sorted(usernames))
        for row in response.data:
            self.assertNotIn("rank", row)

    def test_the_report_is_empty_when_nothing_has_been_assessed(self):
        response = self.report()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(list(response.data), [])

    # --- authorization and bad input ---------------------------------------

    def test_a_sub_teacher_cannot_read_the_academy_wide_report(self):
        """Acceptance criterion 19."""
        self.assessed(weeks_ago=1)

        response = self.report(user=self.teacher)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_read_the_report(self):
        self.assertEqual(
            self.report(user=self.student).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_read_the_report(self):
        self.assertEqual(
            self.report(user=self.parent).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_an_anonymous_caller_cannot_read_the_report(self):
        self.assertEqual(
            self.client.get(self.teacher_report_url).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_an_unparseable_period_is_a_400(self):
        """Refused rather than ignored — a silently widened window is worse."""
        response = self.report(**{"from": "last Tuesday"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("from", response.data)

    def test_an_inverted_period_is_a_400(self):
        response = self.report(
            **{
                "from": iso(dj_timezone.now()),
                "to": iso(dj_timezone.now() - timedelta(weeks=1)),
            }
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("to", response.data)

    def test_an_unknown_track_id_is_a_400(self):
        response = self.report(track_id=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track_id", response.data)


class ProgressAPITests(AssessmentAPIWorld):
    """``/progress/mine/`` and ``/progress/child/`` — what a family may read.

    Acceptance criteria 20-23: a student reads their own progress by track, a
    linked parent reads only their own child's, an unrelated parent is denied, and
    the response carries criterion averages and session counts with no internal
    review field anywhere in it.
    """

    def setUp(self):
        super().setUp()
        self.placement = ReviewedPlacementFactory(student=self.student, track__organization=self.organization)
        self.placed_level = self.placement.recommended_level
        self.placed_track = self.placement.track
        self.placed_rubric = RubricWithCriteriaFactory(track=self.placed_track)
        self.placed_criteria = list(self.placed_rubric.active_criteria())

    def progress(self, user=None, url=None, **params):
        url = url or self.my_progress_url
        params.setdefault("track_id", self.placed_track.pk)
        return self.as_(user or self.student).get(url, params)

    def assessed(self, *, student=None, score=DEFAULT_SCORE, weeks_ago=1, summary=None):
        booking = past_completed_booking(
            availability=self.window,
            level=self.placed_level,
            student=student or self.student,
            weeks_ago=weeks_ago,
        )
        return SessionAssessmentFactory(
            booking=booking,
            score_value=score,
            **({"teacher_summary": summary} if summary is not None else {}),
        )

    def test_a_student_reads_their_own_progress_by_track(self):
        """Acceptance criterion 20."""
        self.assessed(score=4, weeks_ago=1)

        response = self.progress()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["track"]["slug"], self.placed_track.slug)
        self.assertEqual(response.data["completed_sessions"], 1)
        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("4.00"))
        self.assertIsNotNone(response.data["last_assessed_at"])

    def test_progress_carries_per_criterion_averages(self):
        """Acceptance criterion 23."""
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=3, weeks_ago=2)

        response = self.progress()

        rows = response.data["criterion_averages"]
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["criterion_name"] for row in rows],
            [c.name for c in self.placed_criteria],
        )
        self.assertEqual(Decimal(rows[0]["average"]), Decimal("4.00"))
        self.assertEqual(rows[0]["score_count"], 2)

    def test_progress_carries_the_placed_level_it_never_writes(self):
        """Acceptance criterion 24: read from Phase 2's placement, not derived."""
        self.assessed(score=1, weeks_ago=1)

        response = self.progress()

        self.assertEqual(response.data["recommended_level"]["id"], self.placed_level.pk)
        self.placement.refresh_from_db()
        self.assertEqual(self.placement.recommended_level, self.placed_level)

    def test_progress_has_no_placed_level_when_there_is_no_placement(self):
        elsewhere = LevelFactory(track__organization=self.organization)
        RubricWithCriteriaFactory(track=elsewhere.track)

        response = self.progress(track_id=elsewhere.track.pk)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["recommended_level"])

    def test_no_internal_review_field_reaches_a_student(self):
        """Acceptance criterion 23."""
        flagged = FlaggedAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window,
                level=self.placed_level,
                student=self.student,
                weeks_ago=1,
            )
        )
        flagged.record_lead_review(reviewed_by=self.lead, note="Internal only.")

        response = self.progress()

        body = str(response.data)
        for field in ("flagged_for_review", "flag_reason", "lead_review_note"):
            with self.subTest(field=field):
                self.assertNotIn(field, response.data)
        self.assertNotIn("Internal only.", body)
        self.assertNotIn(flagged.flag_reason, body)

    def test_a_teacher_summary_is_family_visible(self):
        """Acceptance criterion 13, on the progress endpoint."""
        self.assessed(summary="Ready to move on after one more revision week.")

        response = self.progress()

        self.assertEqual(
            [row["teacher_summary"] for row in response.data["recent_summaries"]],
            ["Ready to move on after one more revision week."],
        )

    def test_an_unassessed_session_is_counted_but_not_scored(self):
        """Acceptance criterion 18."""
        self.assessed(score=5, weeks_ago=1)
        past_completed_booking(
            availability=self.window,
            level=self.placed_level,
            student=self.student,
            weeks_ago=2,
        )

        response = self.progress()

        self.assertEqual(response.data["completed_sessions"], 2)
        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("5.00"))

    def test_a_period_with_nothing_assessed_has_a_null_average(self):
        """Acceptance criterion 18 — never 0.00."""
        past_completed_booking(
            availability=self.window,
            level=self.placed_level,
            student=self.student,
            weeks_ago=1,
        )

        response = self.progress()

        self.assertEqual(response.data["assessed_sessions"], 0)
        self.assertIsNone(response.data["overall_average"])
        self.assertEqual(list(response.data["criterion_averages"]), [])

    def test_progress_can_be_narrowed_to_a_period(self):
        self.assessed(score=5, weeks_ago=1)
        self.assessed(score=1, weeks_ago=10)

        response = self.progress(**{"from": iso(dj_timezone.now() - timedelta(weeks=5))})

        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("5.00"))

    def test_another_students_sessions_are_not_counted(self):
        self.assessed(score=5, weeks_ago=1)
        self.assessed(student=StudentFactory(), score=1, weeks_ago=2)

        response = self.progress()

        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("5.00"))

    def test_another_tracks_sessions_are_not_counted(self):
        self.assessed(score=5, weeks_ago=1)
        elsewhere = LevelFactory(track__organization=self.organization)
        RubricWithCriteriaFactory(track=elsewhere.track)
        SessionAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window,
                level=elsewhere,
                student=self.student,
                weeks_ago=2,
            ),
            score_value=1,
        )

        response = self.progress()

        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("5.00"))

    def test_track_id_is_required(self):
        response = self.as_(self.student).get(self.my_progress_url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track_id", response.data)

    def test_an_unknown_track_is_a_400_not_an_empty_answer(self):
        response = self.progress(track_id=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track_id", response.data)

    def test_a_teacher_cannot_read_student_progress_here(self):
        response = self.progress(user=self.teacher)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_use_the_student_progress_endpoint(self):
        response = self.progress(user=self.parent)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_an_anonymous_caller_cannot_read_progress(self):
        response = self.client.get(self.my_progress_url, {"track_id": self.placed_track.pk})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- the parent side ----------------------------------------------------

    def test_a_linked_parent_reads_their_childs_progress(self):
        """Acceptance criterion 21."""
        link = self.link()
        self.assessed(student=link.student, score=4, weeks_ago=1)

        response = self.progress(
            user=link.parent, url=self.child_progress_url, student_id=link.student.pk
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("4.00"))

    def test_an_unrelated_parent_is_denied(self):
        """Acceptance criterion 22."""
        link = self.link()
        self.assessed(student=link.student, weeks_ago=1)

        response = self.progress(
            user=self.parent, url=self.child_progress_url, student_id=link.student.pk
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_parent_cannot_read_a_student_who_is_not_their_child(self):
        """Acceptance criterion 22 — the link is checked, not just the role."""
        link = self.link()
        self.assessed(weeks_ago=1)

        response = self.progress(
            user=link.parent, url=self.child_progress_url, student_id=self.student.pk
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_child_endpoint_requires_a_student_id(self):
        link = self.link()

        response = self.as_(link.parent).get(
            self.child_progress_url, {"track_id": self.placed_track.pk}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", response.data)

    def test_a_student_cannot_use_the_child_progress_endpoint(self):
        link = self.link()

        response = self.progress(
            user=self.student, url=self.child_progress_url, student_id=link.student.pk
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_teacher_cannot_use_the_child_progress_endpoint(self):
        link = self.link()

        response = self.progress(
            user=self.teacher, url=self.child_progress_url, student_id=link.student.pk
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_childs_progress_hides_the_internal_fields_too(self):
        """Acceptance criterion 23."""
        link = self.link()
        flagged = FlaggedAssessmentFactory(
            booking=past_completed_booking(
                availability=self.window,
                level=self.placed_level,
                student=link.student,
                weeks_ago=1,
            )
        )
        flagged.record_lead_review(reviewed_by=self.lead, note="Internal only.")

        response = self.progress(
            user=link.parent, url=self.child_progress_url, student_id=link.student.pk
        )

        self.assertNotIn("flag_reason", response.data)
        self.assertNotIn("lead_review_note", response.data)
        self.assertNotIn("Internal only.", str(response.data))


class ProgressSnapshotAPITests(AssessmentAPIWorld):
    """``/snapshots/`` and its three read endpoints.

    Acceptance criteria 25-28: the lead generates a period snapshot, it is
    immutable once generated, an identical period does not duplicate it, and a
    family reads only the published rows inside their own scope.
    """

    def setUp(self):
        super().setUp()
        self.period_start = dj_timezone.now() - timedelta(weeks=4)
        self.period_end = dj_timezone.now()

    def payload(self, **overrides):
        body = {
            "student": self.student.pk,
            "track": self.track.pk,
            "period_start": iso(self.period_start),
            "period_end": iso(self.period_end),
        }
        body.update(overrides)
        return body

    def generate(self, user=None, **overrides):
        return self.as_(user or self.lead).post(
            self.snapshots_url, self.payload(**overrides), format="json"
        )

    def assessed(self, *, student=None, score=DEFAULT_SCORE, weeks_ago=1):
        return SessionAssessmentFactory(
            booking=self.taught(student=student or self.student, weeks_ago=weeks_ago),
            score_value=score,
        )

    def test_the_lead_generates_a_snapshot(self):
        """Acceptance criterion 25."""
        self.assessed(score=4, weeks_ago=1)
        self.taught(student=self.student, weeks_ago=2)

        response = self.generate()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["completed_sessions"], 2)
        self.assertEqual(response.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("4.00"))
        self.assertEqual(response.data["generated_by"], self.lead.username)
        self.assertEqual(len(response.data["criterion_averages"]), 3)

    def test_a_period_with_nothing_assessed_snapshots_a_null_average(self):
        """Acceptance criterion 18 — missing assessments are not zeroes."""
        self.taught(student=self.student, weeks_ago=1)

        response = self.generate()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["assessed_sessions"], 0)
        self.assertIsNone(response.data["overall_average"])

    def test_the_numbers_are_computed_not_supplied(self):
        """A snapshot a caller could type values into would not be evidence."""
        self.assessed(score=2, weeks_ago=1)

        response = self.generate(
            completed_sessions=99, assessed_sessions=99, overall_average="5.00"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["completed_sessions"], 1)
        self.assertEqual(Decimal(response.data["overall_average"]), Decimal("2.00"))

    def test_the_summary_is_composed_unless_the_lead_writes_one(self):
        self.assessed(score=4, weeks_ago=1)

        composed = self.generate()
        self.assertIn("1 of 1", composed.data["summary"])

        other_student = StudentFactory()
        admit(other_student, self.organization)
        written = self.generate(
            student=other_student.pk, summary="Ready to move up after Ramadan."
        )
        self.assertEqual(written.data["summary"], "Ready to move up after Ramadan.")

    def test_repeating_the_request_returns_the_existing_snapshot(self):
        """Acceptance criterion 27."""
        self.assessed(score=4, weeks_ago=1)
        first = self.generate()

        second = self.generate()

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(ProgressSnapshot.objects.count(), 1)

    def test_a_repeated_request_does_not_recompute(self):
        """Acceptance criterion 26 — the whole point of a snapshot."""
        self.assessed(score=5, weeks_ago=1)
        first = self.generate()

        self.assessed(score=1, weeks_ago=2)
        second = self.generate()

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["assessed_sessions"], 1)
        self.assertEqual(Decimal(second.data["overall_average"]), Decimal("5.00"))
        self.assertEqual(first.data["summary"], second.data["summary"])

    def test_a_later_assessment_does_not_move_a_stored_snapshot(self):
        """Acceptance criterion 26, read back through the lead's list."""
        self.assessed(score=5, weeks_ago=1)
        created = self.generate()

        self.assessed(score=1, weeks_ago=2)

        row = self.as_(self.lead).get(
            self.lead_snapshots_url, {"student_id": self.student.pk}
        ).data[0]
        self.assertEqual(row["id"], created.data["id"])
        self.assertEqual(Decimal(row["overall_average"]), Decimal("5.00"))

    def test_a_different_period_is_a_different_snapshot(self):
        self.assessed(score=4, weeks_ago=1)
        self.generate()

        response = self.generate(
            period_start=iso(self.period_start - timedelta(weeks=8)),
            period_end=iso(self.period_start),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ProgressSnapshot.objects.count(), 2)

    def test_there_is_no_endpoint_that_edits_a_snapshot(self):
        """Acceptance criterion 26: immutability is the absence of a write path."""
        self.assessed(weeks_ago=1)
        self.generate()

        for method in ("get", "patch", "put", "delete"):
            with self.subTest(method=method):
                response = getattr(self.as_(self.lead), method)(self.snapshots_url)
                self.assertEqual(
                    response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED
                )

    # --- authorization and bad input ---------------------------------------

    def test_a_sub_teacher_cannot_generate_a_snapshot(self):
        response = self.generate(user=self.teacher)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ProgressSnapshot.objects.exists())

    def test_a_student_cannot_generate_their_own_snapshot(self):
        self.assertEqual(
            self.generate(user=self.student).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_a_parent_cannot_generate_a_snapshot(self):
        self.assertEqual(
            self.generate(user=self.parent).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_an_anonymous_caller_cannot_generate_a_snapshot(self):
        response = self.client.post(self.snapshots_url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertFalse(ProgressSnapshot.objects.exists())

    def test_an_inverted_period_is_refused(self):
        response = self.generate(
            period_start=iso(self.period_end), period_end=iso(self.period_start)
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("period_end", response.data)
        self.assertFalse(ProgressSnapshot.objects.exists())

    def test_only_a_student_has_progress_to_snapshot(self):
        response = self.generate(student=self.teacher.pk)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_an_unknown_student_is_refused(self):
        response = self.generate(student=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student", response.data)

    def test_an_unknown_track_is_refused(self):
        response = self.generate(track=999999)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("track", response.data)

    # --- reading snapshots --------------------------------------------------

    def test_a_snapshot_is_unpublished_until_the_lead_says_otherwise(self):
        self.assessed(weeks_ago=1)
        response = self.generate()

        self.assertFalse(response.data["visible_to_family"])

    def test_the_lead_can_publish_at_generation(self):
        self.assessed(weeks_ago=1)
        response = self.generate(visible_to_family=True)

        self.assertTrue(response.data["visible_to_family"])

    def test_a_student_reads_only_published_snapshots(self):
        """Acceptance criterion 28."""
        published = PublishedSnapshotFactory(student=self.student, track=self.track)
        unpublished = ProgressSnapshotFactory(
            student=self.student,
            track=self.track,
            period_start=self.period_start - timedelta(weeks=8),
            period_end=self.period_start,
        )

        response = self.as_(self.student).get(self.my_snapshots_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [published.pk])
        self.assertNotIn(unpublished.pk, [row["id"] for row in response.data])

    def test_another_students_snapshot_is_not_listed(self):
        mine = PublishedSnapshotFactory(student=self.student, track=self.track)
        PublishedSnapshotFactory(track=self.track)

        response = self.as_(self.student).get(self.my_snapshots_url)
        self.assertEqual([row["id"] for row in response.data], [mine.pk])

    def test_the_family_snapshot_shape_omits_the_lead_only_fields(self):
        PublishedSnapshotFactory(student=self.student, track=self.track)

        payload = self.as_(self.student).get(self.my_snapshots_url).data[0]

        self.assertNotIn("generated_by", payload)
        self.assertNotIn("visible_to_family", payload)
        self.assertIn("summary", payload)
        self.assertIn("criterion_averages", payload)

    def test_a_student_snapshot_list_can_be_narrowed_to_a_track(self):
        mine = PublishedSnapshotFactory(student=self.student, track=self.track)
        elsewhere = TrackFactory(organization=self.organization)
        PublishedSnapshotFactory(student=self.student, track=elsewhere)

        response = self.as_(self.student).get(self.my_snapshots_url, {"track_id": self.track.pk})
        self.assertEqual([row["id"] for row in response.data], [mine.pk])

    def test_a_teacher_cannot_read_the_student_snapshot_list(self):
        response = self.as_(self.teacher).get(self.my_snapshots_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_linked_parent_reads_their_childs_published_snapshots(self):
        """Acceptance criterion 28."""
        link = self.link()
        published = PublishedSnapshotFactory(student=link.student, track=self.track)
        ProgressSnapshotFactory(
            student=link.student,
            track=self.track,
            period_start=self.period_start - timedelta(weeks=8),
            period_end=self.period_start,
        )

        response = self.as_(link.parent).get(
            self.child_snapshots_url, {"student_id": link.student.pk}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [published.pk])

    def test_an_unrelated_parent_cannot_read_a_childs_snapshots(self):
        """Acceptance criteria 22 and 28."""
        link = self.link()
        PublishedSnapshotFactory(student=link.student, track=self.track)

        response = self.as_(self.parent).get(
            self.child_snapshots_url, {"student_id": link.student.pk}
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_the_child_snapshot_endpoint_requires_a_student_id(self):
        link = self.link()

        response = self.as_(link.parent).get(self.child_snapshots_url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("student_id", response.data)

    def test_the_lead_list_includes_unpublished_rows(self):
        unpublished = ProgressSnapshotFactory(student=self.student, track=self.track)

        response = self.as_(self.lead).get(self.lead_snapshots_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [unpublished.pk])
        self.assertFalse(response.data[0]["visible_to_family"])

    def test_the_lead_list_can_be_narrowed_by_student_and_track(self):
        mine = ProgressSnapshotFactory(student=self.student, track=self.track)
        ProgressSnapshotFactory(track=self.track)

        response = self.as_(self.lead).get(
            self.lead_snapshots_url, {"student_id": self.student.pk, "track_id": self.track.pk}
        )
        self.assertEqual([row["id"] for row in response.data], [mine.pk])

    def test_a_sub_teacher_cannot_read_the_lead_snapshot_list(self):
        ProgressSnapshotFactory(student=self.student, track=self.track)

        response = self.as_(self.teacher).get(self.lead_snapshots_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_cannot_read_the_lead_snapshot_list(self):
        response = self.as_(self.student).get(self.lead_snapshots_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AssessmentSchemaTests(APITestCase):
    """The Phase 7 surface is describable — ``spectacular`` builds without raising."""

    def test_every_assessment_endpoint_appears_in_the_generated_schema(self):
        from drf_spectacular.generators import SchemaGenerator

        paths = SchemaGenerator().get_schema(request=None, public=True)["paths"]

        for path in (
            "/api/assessment/organizations/{organization_pk}/rubrics/",
            "/api/assessment/organizations/{organization_pk}/rubrics/{id}/",
            "/api/assessment/organizations/{organization_pk}/bookings/{booking_id}/",
            "/api/assessment/organizations/{organization_pk}/mine/",
            "/api/assessment/organizations/{organization_pk}/child/",
            "/api/assessment/organizations/{organization_pk}/teacher/mine/",
            "/api/assessment/organizations/{organization_pk}/review/queue/",
            "/api/assessment/organizations/{organization_pk}/reports/teachers/",
            "/api/assessment/organizations/{organization_pk}/progress/mine/",
            "/api/assessment/organizations/{organization_pk}/progress/child/",
            "/api/assessment/organizations/{organization_pk}/snapshots/",
            "/api/assessment/organizations/{organization_pk}/snapshots/all/",
            "/api/assessment/organizations/{organization_pk}/snapshots/mine/",
            "/api/assessment/organizations/{organization_pk}/snapshots/child/",
            "/api/assessment/organizations/{organization_pk}/{id}/",
            "/api/assessment/organizations/{organization_pk}/{id}/review/",
        ):
            with self.subTest(path=path):
                self.assertIn(path, paths)
