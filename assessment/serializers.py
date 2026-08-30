"""Serializers for the assessment app.

**There are four read shapes for one assessment, and the differences are the
phase.** Each one exists because a different person is looking:

* ``LeadAssessmentSerializer`` — everything, review note included. The lead.
* ``TeacherAssessmentSerializer`` — the teacher's own submission, plus *whether*
  it has been reviewed but not what the lead wrote. The spec's visibility section
  says a sub-teacher gets no lead review notes.
* ``FamilyAssessmentSerializer`` — teaching content only. No flag, no flag reason,
  no review fields at all. Not "the fields blanked out" — absent.
* ``AssessmentScoreSerializer`` is shared, because a score is the same fact to
  everyone; the criterion *comment* is teaching feedback, so families read it.

Separate classes rather than one with conditional fields, for the reason
``pricing.serializers`` gives about ``notes``: a field that is private only when
somebody remembers to hide it eventually leaks. The views do not rely on this
layer alone either — every queryset is scoped server-side first.

Whether an assessment is *legal* is never decided here. The completed-booking
rule, ``assessed_by == booking.teacher``, one-per-booking, the full set of active
criteria and the 1-5 scale all live in ``SessionAssessment``/``AssessmentScore``
and are surfaced from there, so the API and a direct ORM write cannot disagree.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from accounts.models import User
from accounts.utils import to_user_timezone
from curriculum.models import Track
from curriculum.serializers import LevelSerializer

from .models import (
    MAX_SCORE,
    MIN_SCORE,
    AssessmentCriterion,
    AssessmentRubric,
    AssessmentScore,
    ProgressSnapshot,
    SessionAssessment,
)


def as_drf_error(exc):
    """Re-raise a model ``ValidationError`` as a DRF one, so it lands as a 400.

    A local copy of the helper ``scheduling.serializers`` and
    ``pricing.serializers`` each keep: four lines, and the apps are otherwise
    independent of one another.
    """
    return serializers.ValidationError(
        getattr(exc, "message_dict", None) or {"detail": exc.messages}
    )


def viewer_timezone(context, fallback_user):
    """The zone to render in: the caller's own, else ``fallback_user``'s."""
    request = context.get("request")
    user = getattr(request, "user", None) if request else None
    if user is not None and user.is_authenticated:
        return user.timezone
    return fallback_user.timezone


class AssessmentPartySerializer(serializers.ModelSerializer):
    """A person on an assessment. No signup_code, no PII sprawl."""

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "role", "timezone"]
        read_only_fields = fields


# --- Rubric configuration ----------------------------------------------------


class AssessmentCriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssessmentCriterion
        fields = ["id", "name", "description", "order", "active"]
        read_only_fields = ["id"]


class AssessmentRubricSerializer(serializers.ModelSerializer):
    """A rubric and its criteria, as the lead reads them.

    ``criteria`` is every criterion including the retired ones, because a lead
    editing configuration needs to see what they switched off. ``active_criteria``
    is the subset a new assessment would have to score, which is the operationally
    interesting list and is worth not making a client derive.
    """

    track = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    track_id = serializers.IntegerField(read_only=True)
    criteria = AssessmentCriterionSerializer(many=True, read_only=True)
    active_criteria = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentRubric
        fields = [
            "id",
            "track",
            "track_id",
            "name",
            "description",
            "active",
            "criteria",
            "active_criteria",
        ]
        read_only_fields = fields

    @extend_schema_field(AssessmentCriterionSerializer(many=True))
    def get_active_criteria(self, rubric):
        return AssessmentCriterionSerializer(
            rubric.active_criteria(), many=True
        ).data


class AssessmentCriterionWriteSerializer(serializers.Serializer):
    """One criterion in a rubric write. ``id`` present means "edit that one"."""

    id = serializers.IntegerField(required=False)
    name = serializers.CharField(max_length=120, required=False)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )
    order = serializers.IntegerField(required=False, min_value=1)
    active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "id" not in attrs:
            missing = [field for field in ("name", "order") if field not in attrs]
            if missing:
                raise serializers.ValidationError(
                    {field: ["Required for a new criterion."] for field in missing}
                )
        return attrs


class AssessmentRubricCreateSerializer(serializers.Serializer):
    """The lead configures a track's rubric.

    Creating one for a track that already has a live rubric **supersedes** the old
    row rather than editing or deleting it — the ``PricingAgreement`` pattern, and
    for the same reason: assessments already submitted point at the old rubric's
    criteria, so it has to survive. Renaming or retiring individual criteria is the
    *other* operation, and it is a PATCH on the rubric detail.
    """

    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.all())
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )
    criteria = AssessmentCriterionWriteSerializer(many=True, allow_empty=False)

    def validate_criteria(self, criteria):
        orders = [entry["order"] for entry in criteria]
        if len(set(orders)) != len(orders):
            raise serializers.ValidationError(
                "Criterion order is unique within a rubric."
            )
        return criteria

    def create(self, validated_data):
        criteria = validated_data.pop("criteria")
        rubric = AssessmentRubric(
            track=validated_data["track"],
            name=validated_data["name"],
            description=validated_data.get("description", ""),
        )
        try:
            # One transaction: saving the rubric supersedes the track's previous
            # one, so a criterion that failed halfway would otherwise retire a
            # live sheet and leave a half-built replacement behind it.
            with transaction.atomic():
                rubric.save()
                for entry in criteria:
                    AssessmentCriterion(
                        rubric=rubric,
                        name=entry["name"],
                        description=entry.get("description", ""),
                        order=entry["order"],
                        active=entry.get("active", True),
                    ).save()
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return rubric


class AssessmentRubricUpdateSerializer(serializers.Serializer):
    """Editing live configuration in place: rename, retire, append.

    This is the operation the historical-data rule is about. Nothing here touches
    an existing ``AssessmentScore`` — a renamed criterion keeps its old name on
    every assessment already submitted, because that name was snapshotted onto the
    score. Retiring a criterion stops *new* assessments scoring it and leaves old
    ones intact.
    """

    name = serializers.CharField(max_length=80, required=False)
    description = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )
    active = serializers.BooleanField(required=False)
    criteria = AssessmentCriterionWriteSerializer(many=True, required=False)

    def update(self, rubric, validated_data):
        criteria = validated_data.pop("criteria", None)
        for field in ("name", "description", "active"):
            if field in validated_data:
                setattr(rubric, field, validated_data[field])
        try:
            # One transaction, so a 400 leaves nothing behind: a request that
            # renames the sheet *and* moves a criterion to an occupied position
            # would otherwise be refused with the rename already committed.
            with transaction.atomic():
                rubric.save()
                for entry in criteria or []:
                    self._apply_criterion(rubric, entry)
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        return rubric

    @staticmethod
    def _apply_criterion(rubric, entry):
        criterion_id = entry.get("id")
        if criterion_id is None:
            AssessmentCriterion(
                rubric=rubric,
                name=entry["name"],
                description=entry.get("description", ""),
                order=entry["order"],
                active=entry.get("active", True),
            ).save()
            return
        criterion = rubric.criteria.filter(pk=criterion_id).first()
        if criterion is None:
            raise serializers.ValidationError(
                {"criteria": [f"{criterion_id} is not a criterion of this rubric."]}
            )
        for field in ("name", "description", "order", "active"):
            if field in entry:
                setattr(criterion, field, entry[field])
        criterion.save()


# --- Assessments -------------------------------------------------------------


class AssessmentScoreSerializer(serializers.ModelSerializer):
    """One criterion's mark. Shared by every read shape.

    ``criterion_name`` is the snapshot, not a live lookup, so an old assessment
    reads the way the teacher submitted it however the rubric has moved since. The
    live criterion id travels alongside it for anyone aggregating.
    """

    class Meta:
        model = AssessmentScore
        fields = ["id", "criterion", "criterion_name", "score", "comment"]
        read_only_fields = fields


class AssessmentBookingSerializer(serializers.Serializer):
    """The session an assessment is about, in the shape every reader needs of it."""

    id = serializers.IntegerField(read_only=True)
    level = LevelSerializer(read_only=True)
    start_time_utc = serializers.DateTimeField(read_only=True)
    duration_minutes = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)


class BaseAssessmentSerializer(serializers.ModelSerializer):
    """The fields common to all three audiences: what was scored, and how well."""

    booking = AssessmentBookingSerializer(read_only=True)
    track = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    scores = AssessmentScoreSerializer(many=True, read_only=True)
    overall_average = serializers.DecimalField(
        max_digits=3, decimal_places=2, read_only=True
    )
    assessed_at_local = serializers.SerializerMethodField()

    def get_assessed_at_local(self, assessment) -> str | None:
        return to_user_timezone(
            assessment.assessed_at, viewer_timezone(self.context, assessment.student)
        )


class LeadAssessmentSerializer(BaseAssessmentSerializer):
    """The lead's view: everything, including the flag reason and the review note."""

    student = AssessmentPartySerializer(read_only=True)
    assessed_by = AssessmentPartySerializer(read_only=True)
    lead_reviewed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = SessionAssessment
        fields = [
            "id",
            "booking",
            "student",
            "track",
            "assessed_by",
            "assessed_at",
            "assessed_at_local",
            "rubric_name",
            "criteria_snapshot",
            "scores",
            "overall_average",
            "teacher_summary",
            "flagged_for_review",
            "flag_reason",
            "lead_reviewed_at",
            "lead_reviewed_by",
            "lead_review_note",
        ]
        read_only_fields = fields


class TeacherAssessmentSerializer(BaseAssessmentSerializer):
    """The assessing teacher's view of their own submission.

    Carries their own flag and its reason — they wrote both — and
    ``lead_reviewed_at`` so they can see the lead has looked. It does **not**
    carry ``lead_review_note`` or ``lead_reviewed_by``: the spec's visibility
    section gives a sub-teacher no access to lead review notes in this phase, and
    widening that is a decision, not a serializer tweak.
    """

    student = AssessmentPartySerializer(read_only=True)

    class Meta:
        model = SessionAssessment
        fields = [
            "id",
            "booking",
            "student",
            "track",
            "assessed_at",
            "assessed_at_local",
            "rubric_name",
            "criteria_snapshot",
            "scores",
            "overall_average",
            "teacher_summary",
            "flagged_for_review",
            "flag_reason",
            "lead_reviewed_at",
        ]
        read_only_fields = fields


class FamilyAssessmentSerializer(BaseAssessmentSerializer):
    """What a student or their linked parent reads.

    Teaching content and nothing else: the summary the teacher wrote for them, the
    criterion scores and comments, who taught it. Every internal quality-control
    field is absent from the shape rather than blanked — no ``flagged_for_review``,
    no ``flag_reason``, no ``lead_*`` at all.
    """

    assessed_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = SessionAssessment
        fields = [
            "id",
            "booking",
            "track",
            "assessed_by",
            "assessed_at",
            "assessed_at_local",
            "rubric_name",
            "scores",
            "overall_average",
            "teacher_summary",
        ]
        read_only_fields = fields


class AssessmentScoreWriteSerializer(serializers.Serializer):
    """One submitted mark. The criterion set is checked by the model, not here.

    ``criterion`` is a bare integer rather than a ``PrimaryKeyRelatedField``
    against every criterion in the academy on purpose: whether an id is a *live
    criterion of this booking's rubric* is exactly the question
    ``SessionAssessment.submit`` answers, and a queryset check here would answer a
    weaker version of it first and report the failure in the wrong words.
    """

    criterion = serializers.IntegerField()
    score = serializers.IntegerField(min_value=MIN_SCORE, max_value=MAX_SCORE)
    comment = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )


class SessionAssessmentCreateSerializer(serializers.Serializer):
    """A teacher submits one completed session's assessment.

    ``booking`` and ``assessed_by`` are not client fields. The booking comes from
    the URL, scoped by the view to sessions this teacher taught; the assessor is
    the requesting user, stamped the same way ``PlacementResult.reviewed_by`` and
    ``PricingAgreement.approved_by`` are. A teacher naming somebody else as the
    assessor is not a request this API can express.
    """

    scores = AssessmentScoreWriteSerializer(many=True, allow_empty=False)
    teacher_summary = serializers.CharField(
        max_length=4000, required=False, allow_blank=True
    )
    flagged_for_review = serializers.BooleanField(required=False, default=False)
    flag_reason = serializers.CharField(
        max_length=2000, required=False, allow_blank=True
    )

    def create(self, validated_data):
        try:
            return SessionAssessment.submit(
                booking=self.context["booking"],
                assessed_by=self.context["request"].user,
                scores=validated_data["scores"],
                teacher_summary=validated_data.get("teacher_summary", ""),
                flagged_for_review=validated_data.get("flagged_for_review", False),
                flag_reason=validated_data.get("flag_reason", ""),
            )
        except DjangoValidationError as exc:
            # A non-completed booking, a wrong teacher, a missing or duplicated
            # criterion, a track with no rubric — all model rules, surfaced as
            # 400s carrying the model's own wording rather than as 500s.
            raise as_drf_error(exc) from exc


class LeadReviewSerializer(serializers.Serializer):
    """The lead annotates an assessment and marks it reviewed.

    A note and nothing else is writable, which is the whole point: review is an
    annotation beside the teacher's scores, never an edit of them. The model
    refuses the edit too, so this is the polite layer rather than the only one.
    """

    lead_review_note = serializers.CharField(
        max_length=4000, required=False, allow_blank=True
    )

    def update(self, assessment, validated_data):
        try:
            return assessment.record_lead_review(
                reviewed_by=self.context["request"].user,
                note=validated_data.get("lead_review_note", ""),
            )
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc


# --- Reporting, progress, snapshots ------------------------------------------


class TrackBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class CriterionAverageSerializer(serializers.Serializer):
    """One criterion's average over a period. ``average`` is null when unscored."""

    criterion_id = serializers.IntegerField(read_only=True)
    criterion_name = serializers.CharField(read_only=True)
    average = serializers.DecimalField(
        max_digits=3, decimal_places=2, read_only=True, allow_null=True
    )
    score_count = serializers.IntegerField(read_only=True)


class TeacherTrackReportSerializer(serializers.Serializer):
    track = TrackBriefSerializer(read_only=True)
    assessed_sessions = serializers.IntegerField(read_only=True)
    overall_average = serializers.DecimalField(
        max_digits=3, decimal_places=2, read_only=True, allow_null=True
    )


class TeacherReportSerializer(serializers.Serializer):
    """One teacher's row in the lead's quality report.

    Deliberately not sortable by score and deliberately without a rank field. The
    rows arrive ordered by username; a client that wants a league table has to
    build one itself, and the phase spec says not to.
    """

    teacher = AssessmentPartySerializer(read_only=True)
    assessed_sessions = serializers.IntegerField(read_only=True)
    overall_average = serializers.DecimalField(
        max_digits=3, decimal_places=2, read_only=True, allow_null=True
    )
    by_track = TeacherTrackReportSerializer(many=True, read_only=True)
    flagged = serializers.IntegerField(read_only=True)
    awaiting_lead_review = serializers.IntegerField(read_only=True)


class RecentSummarySerializer(serializers.Serializer):
    """A teacher summary as a family reads it — the one free-text field they see."""

    assessment_id = serializers.IntegerField(read_only=True)
    taught_at = serializers.DateTimeField(read_only=True)
    assessed_at = serializers.DateTimeField(read_only=True)
    teacher_summary = serializers.CharField(read_only=True)


class StudentProgressSerializer(serializers.Serializer):
    """Progress in one track, for the student or their linked parent.

    ``overall_average`` and every criterion average are nullable, and null means
    "nothing assessed in this period" rather than "scored zero". ``recommended_level``
    is read from Phase 2's placement result and is never written by this app.
    """

    track = TrackBriefSerializer(read_only=True)
    recommended_level = LevelSerializer(read_only=True, allow_null=True)
    period_start = serializers.DateTimeField(read_only=True, allow_null=True)
    period_end = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_sessions = serializers.IntegerField(read_only=True)
    assessed_sessions = serializers.IntegerField(read_only=True)
    overall_average = serializers.DecimalField(
        max_digits=3, decimal_places=2, read_only=True, allow_null=True
    )
    criterion_averages = CriterionAverageSerializer(many=True, read_only=True)
    last_assessed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    recent_summaries = RecentSummarySerializer(many=True, read_only=True)


class ProgressSnapshotSerializer(serializers.ModelSerializer):
    """The lead's view of a snapshot, including who generated it."""

    student = AssessmentPartySerializer(read_only=True)
    track = TrackBriefSerializer(read_only=True)
    generated_by = serializers.SlugRelatedField(slug_field="username", read_only=True)

    class Meta:
        model = ProgressSnapshot
        fields = [
            "id",
            "student",
            "track",
            "period_start",
            "period_end",
            "generated_at",
            "completed_sessions",
            "assessed_sessions",
            "overall_average",
            "criterion_averages",
            "summary",
            "generated_by",
            "visible_to_family",
        ]
        read_only_fields = fields


class FamilyProgressSnapshotSerializer(serializers.ModelSerializer):
    """The family's view. No ``generated_by``, and only published rows reach it.

    ``visible_to_family`` is absent from the shape because the endpoint already
    filters on it: a row a family can read is published by definition, so
    publishing it again as a field would be noise. Which rows those are is the
    view's decision, not this class's.
    """

    track = TrackBriefSerializer(read_only=True)

    class Meta:
        model = ProgressSnapshot
        fields = [
            "id",
            "track",
            "period_start",
            "period_end",
            "generated_at",
            "completed_sessions",
            "assessed_sessions",
            "overall_average",
            "criterion_averages",
            "summary",
        ]
        read_only_fields = fields


class ProgressSnapshotCreateSerializer(serializers.Serializer):
    """The lead generates a snapshot for one student, track and period.

    Every number in the result is computed here, not supplied: a snapshot a caller
    could hand values to would not be evidence of anything. Repeating the same
    request returns the existing row and a 200 rather than creating a second one.
    """

    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        help_text="Must be a student account; the model enforces the role.",
    )
    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.all())
    period_start = serializers.DateTimeField()
    period_end = serializers.DateTimeField()
    summary = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    visible_to_family = serializers.BooleanField(required=False, default=False)

    def create(self, validated_data):
        try:
            snapshot, created = ProgressSnapshot.generate(
                student=validated_data["student"],
                track=validated_data["track"],
                period_start=validated_data["period_start"],
                period_end=validated_data["period_end"],
                generated_by=self.context["request"].user,
                summary=validated_data.get("summary", ""),
                visible_to_family=validated_data.get("visible_to_family", False),
            )
        except DjangoValidationError as exc:
            raise as_drf_error(exc) from exc
        # The view needs to know which it was, to answer 201 or 200.
        self.created = created
        return snapshot
