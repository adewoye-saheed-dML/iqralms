"""API views for the assessment app — the Phase 7 surface, nothing more.

The wall this app is built around: **who is allowed to see what**. Four roles read
the same rows through four different windows, and the phase spec is explicit that
serializer field omission is not a permission boundary — so every queryset below
is narrowed to the caller's own teacher, student or family context *before* a
serializer chooses fields. A sub-teacher's own submissions come from a queryset
filtered on ``assessed_by=request.user``; a family's progress comes from a student
id the view has checked against ``ParentLink``.

Nothing here changes routing, a booking's status, or a placement's
``recommended_level``. Submitting an assessment writes assessment rows and
nothing else — CLAUDE.md's Phase 7 boundary, and the tests assert it.
"""

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import ParentLink, User
from accounts.tenancy import children_in_organization
from curriculum.models import Track
from organizations.permissions import IsOrganizationMember
from organizations.views import OrganizationScopedMixin
from scheduling.models import Booking

from .models import AssessmentRubric, ProgressSnapshot, SessionAssessment
from .permissions import IsLeadTeacher, IsParent, IsStudent, IsTeacher
from .reporting import student_progress, teacher_report
from .serializers import (
    AssessmentRubricCreateSerializer,
    AssessmentRubricSerializer,
    AssessmentRubricUpdateSerializer,
    FamilyAssessmentSerializer,
    FamilyProgressSnapshotSerializer,
    LeadAssessmentSerializer,
    LeadReviewSerializer,
    ProgressSnapshotCreateSerializer,
    ProgressSnapshotSerializer,
    SessionAssessmentCreateSerializer,
    StudentProgressSerializer,
    TeacherAssessmentSerializer,
    TeacherReportSerializer,
)

#: One query for an assessment plus everything the read serializers touch.
ASSESSMENT_RELATED = (
    "booking",
    "booking__level",
    "booking__level__track",
    "student",
    "track",
    "assessed_by",
    "lead_reviewed_by",
)

#: Scores are always rendered with their assessment, so prefetch them together.
ASSESSMENT_PREFETCH = ("scores",)


def optional_int_param(request, name):
    """Read an optional integer query parameter, or 400 if it is not one."""
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ["Must be an integer."]})


def required_int_param(request, name):
    """Read a required integer query parameter, or 400 explaining why.

    The local copy ``scheduling.views`` and ``pricing.views`` each keep. Here the
    stakes are the reason it is required rather than defaulted: without
    ``student_id`` the honest behaviour of the parent endpoints would be answering
    for somebody's child at random.
    """
    value = optional_int_param(request, name)
    if value is None:
        raise ValidationError({name: ["This query parameter is required."]})
    return value


def optional_datetime_param(request, name):
    """Read an optional ISO-8601 datetime query parameter, or 400 if unparseable.

    A period bound that cannot be parsed is refused rather than ignored. Silently
    dropping it would widen the window the caller asked for, and a report over the
    wrong period is worse than an error message.
    """
    raw = request.query_params.get(name)
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValidationError(
            {name: ["Must be an ISO-8601 datetime, e.g. 2026-08-01T00:00:00Z."]}
        )
    return parsed


def report_period(request):
    """The ``from``/``to`` window a report or progress request asked for."""
    start = optional_datetime_param(request, "from")
    end = optional_datetime_param(request, "to")
    if start and end and end <= start:
        raise ValidationError({"to": ["A period ends after it starts."]})
    return start, end


def requested_track(request, organization=None, *, required=True):
    """The ``track_id`` a progress request is about.

    An unknown id is a 400 rather than an empty progress response: "how is my
    child doing in track 99" is a question about a track that does not exist, and
    answering it with zero sessions would look like a real answer.
    """
    track_id = (
        required_int_param(request, "track_id")
        if required
        else optional_int_param(request, "track_id")
    )
    if track_id is None:
        return None
    qs = Track.objects.filter(pk=track_id)
    if organization is not None:
        qs = qs.filter(organization=organization)
    track = qs.first()
    if track is None:
        raise ValidationError({"track_id": ["No such track."]})
    return track


def linked_child(parent, student_id, organization=None):
    """The parent's own child with that id, or 403.

    A 403 rather than the 404 this project prefers elsewhere, because there is
    nothing to conceal: the caller supplied the id, so a refusal tells them only
    that this child is not theirs — which they already knew. When an organization is
    passed, the child must also have an active student membership in that academy.
    """
    if organization is not None:
        student = (
            children_in_organization(parent=parent, organization=organization)
            .filter(pk=student_id)
            .first()
        )
    else:
        student = (
            User.objects.filter(pk=student_id, parent_links__parent=parent)
            .distinct()
            .first()
        )
    if student is None:
        raise PermissionDenied("No linked student found for that id.")
    return student


class AcademyScopedView(OrganizationScopedMixin):
    """Shared plumbing for the academy-scoped assessment views.

    The organization comes from the URL kwarg ``organization_pk`` and is resolved
    to the caller's membership by the parent mixin; and it is put into the
    serializer context, which is how write serializers narrow their querysets
    to one tenant.
    """

    organization_url_kwarg = "organization_pk"

    def get_serializer_context(self):
        context = super().get_serializer_context()
        if self.caller_membership:
            context["organization"] = self.organization
        return context


# --- Rubric configuration ----------------------------------------------------


class AssessmentRubricListCreateView(AcademyScopedView, generics.ListCreateAPIView):
    """/api/assessment/organizations/<organization_pk>/rubrics/ — the lead configures what teachers score against.

    * **GET ?track_id=** lists rubrics, newest first, superseded ones included:
      "what do we score Tajweed on, and what did we score it on before" is one
      question. Without ``track_id`` it lists every track's.
    * **POST** creates a rubric with its criteria. If the track already has an
      active rubric, that one is superseded rather than edited — see
      ``AssessmentRubricCreateSerializer``.

    Lead-only on both sides. A rubric decides what every teacher in the academy is
    measured on, so it is not something a sub-teacher configures for themselves.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return AssessmentRubricCreateSerializer
        return AssessmentRubricSerializer

    def get_queryset(self):
        queryset = (
            AssessmentRubric.objects.in_organization(self.organization)
            .select_related("track")
            .prefetch_related("criteria")
        )
        track = requested_track(self.request, organization=self.organization, required=False)
        if track is not None:
            queryset = queryset.filter(track=track)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="track_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to one track's rubrics.",
            )
        ],
        responses={
            200: OpenApiResponse(response=AssessmentRubricSerializer(many=True)),
            403: OpenApiResponse(description="Not the lead teacher."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=AssessmentRubricSerializer,
                description=(
                    "Created and active. Any previous rubric for this track is "
                    "now active=False, still queryable, and its criteria still "
                    "back the assessments submitted against them."
                ),
            ),
            400: OpenApiResponse(
                description="Unknown track, no criteria, or a repeated order."
            ),
            403: OpenApiResponse(description="Not the lead teacher."),
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rubric = serializer.save()
        body = AssessmentRubricSerializer(
            rubric, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class AssessmentRubricDetailView(AcademyScopedView, generics.RetrieveUpdateAPIView):
    """/api/assessment/organizations/<organization_pk>/rubrics/{id}/ — read one rubric, or edit it in place.

    PATCH is the operation the historical-data rule is about: renaming or retiring
    a criterion changes what *future* assessments look like and leaves every
    existing one exactly as it was, because each score carries the criterion name
    it was submitted under. Nothing here rewrites an ``AssessmentScore``, and the
    model would refuse if it tried.

    PUT is not offered. A whole-object replace on live configuration whose
    criteria are referenced by historical records is not a coherent operation;
    replacing a rubric wholesale is a POST to the collection, which supersedes.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        return (
            AssessmentRubric.objects.in_organization(self.organization)
            .select_related("track")
            .prefetch_related("criteria")
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return AssessmentRubricUpdateSerializer
        return AssessmentRubricSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(response=AssessmentRubricSerializer),
            400: OpenApiResponse(
                description="A repeated order, or a criterion id from another rubric."
            ),
            403: OpenApiResponse(description="Not the lead teacher."),
        }
    )
    def patch(self, request, *args, **kwargs):
        rubric = self.get_object()
        serializer = self.get_serializer(rubric, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        rubric = serializer.save()
        rubric.refresh_from_db()
        body = AssessmentRubricSerializer(
            rubric, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)


# --- Submitting and reading assessments --------------------------------------


class SessionAssessmentCreateView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/assessment/organizations/<organization_pk>/bookings/{booking_id}/ — the teacher who taught it scores it.

    Two layers keep this to the right teacher and the right booking. The queryset
    is scoped to ``teacher=request.user``, so another teacher's session is a **404**
    rather than a 403 — a 403 would confirm the booking exists. And
    ``SessionAssessment.clean()`` re-checks ``assessed_by == booking.teacher``
    anyway, so a future caller that skips this view cannot get it wrong either.

    A booking that is scheduled, cancelled or a no-show is a **400** carrying the
    model's own message: it exists and belongs to this teacher, it is simply not a
    session that happened. Submitting an assessment does not mark a booking
    completed — that stays a separate act.
    """

    serializer_class = SessionAssessmentCreateSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_booking(self):
        booking = (
            Booking.objects.filter(
                pk=self.kwargs["booking_id"],
                teacher=self.request.user,
                level__track__organization=self.organization,
            )
            .select_related("student", "level", "level__track", "teacher")
            .first()
        )
        if booking is None:
            raise NotFound("No such session for this teacher.")
        return booking

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["booking"] = getattr(self, "_booking", None)
        return context

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=TeacherAssessmentSerializer,
                description="Submitted, with the rubric and criterion names snapshotted.",
            ),
            400: OpenApiResponse(
                description=(
                    "The booking is not completed, the track has no active "
                    "rubric, a criterion is missing/unknown/duplicated, a score "
                    "is outside 1-5, or this booking is already assessed."
                )
            ),
            403: OpenApiResponse(description="Not a teacher account."),
            404: OpenApiResponse(description="No such session for this teacher."),
        }
    )
    def post(self, request, *args, **kwargs):
        self._booking = self.get_booking()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assessment = serializer.save()
        from notifications.services import notify_progress_ready
        notify_progress_ready(assessment)
        body = TeacherAssessmentSerializer(
            assessment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_201_CREATED)


class MyAssessmentListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/assessment/organizations/<organization_pk>/mine/ — the student's own assessment history.

    Family shape: the teacher's summary, the criterion scores and comments, and no
    internal quality-control field at all. Scoped to ``student=request.user``, so
    this endpoint cannot be pointed at anybody else.
    """

    serializer_class = FamilyAssessmentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def get_queryset(self):
        queryset = (
            SessionAssessment.objects.in_organization(self.organization)
            .filter(student=self.request.user)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )
        track = requested_track(self.request, organization=self.organization, required=False)
        if track is not None:
            queryset = queryset.filter(track=track)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="track_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to one track.",
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class MyChildAssessmentListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/assessment/organizations/<organization_pk>/child/?student_id= — a linked child's assessment history.

    Same family shape as ``/mine/``, reached through the ``ParentLink`` check.
    Not in the spec's endpoint list, but the spec's visibility table gives a parent
    "linked-child assessments/progress" and progress alone would not be that.
    """

    serializer_class = FamilyAssessmentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsParent]

    def get_queryset(self):
        student = linked_child(
            self.request.user,
            required_int_param(self.request, "student_id"),
            organization=self.organization,
        )
        queryset = (
            SessionAssessment.objects.in_organization(self.organization)
            .filter(student=student)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )
        track = requested_track(self.request, organization=self.organization, required=False)
        if track is not None:
            queryset = queryset.filter(track=track)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id", type=int, location=OpenApiParameter.QUERY, required=True
            ),
            OpenApiParameter(
                name="track_id", type=int, location=OpenApiParameter.QUERY, required=False
            ),
        ],
        responses={
            200: OpenApiResponse(response=FamilyAssessmentSerializer(many=True)),
            403: OpenApiResponse(description="Not a parent, or not this parent's child."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TeacherAssessmentListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/assessment/organizations/<organization_pk>/teacher/mine/ — what this teacher has submitted.

    Their own rows only, including their own flags. Not the lead's review notes:
    the spec gives a sub-teacher no access to those in this phase. The lead sees
    their own submissions here too — for academy-wide data they use the review
    queue and the report.
    """

    serializer_class = TeacherAssessmentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsTeacher]

    def get_queryset(self):
        return (
            SessionAssessment.objects.in_organization(self.organization)
            .filter(assessed_by=self.request.user)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )


class LeadAssessmentDetailView(AcademyScopedView, generics.RetrieveAPIView):
    """GET /api/assessment/organizations/<organization_pk>/{id}/ — the lead opens one assessment in full.

    The drill-down from the review queue: booking, student, teacher, rubric
    snapshot, scores, flag reason and any review note. Lead-only, and reading it
    does **not** mark it reviewed — the spec is explicit that clearing a flag is an
    explicit action, so the review stamp only ever comes from the POST below.
    """

    serializer_class = LeadAssessmentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        return (
            SessionAssessment.objects.in_organization(self.organization)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )


class LeadReviewQueueView(AcademyScopedView, generics.ListAPIView):
    """GET /api/assessment/organizations/<organization_pk>/review/queue/ — flagged and not yet reviewed.

    One definition of the queue, ``SessionAssessment.pending_lead_review()``, so
    the count in the teacher report and the list here cannot disagree. A reviewed
    assessment leaves it; the flag itself stays on the record, because that the
    teacher raised one is history.
    """

    serializer_class = LeadAssessmentSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        return (
            SessionAssessment.pending_lead_review(organization=self.organization)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )


class LeadReviewView(AcademyScopedView, generics.GenericAPIView):
    """POST /api/assessment/organizations/<organization_pk>/{id}/review/ — the lead annotates and marks reviewed.

    Writes three fields and no others. The teacher's scores, summary and flag are
    untouched, and the model refuses to change them on an existing row, so "review
    never rewrites historical teacher data" holds even for a caller that tries.
    """

    serializer_class = LeadReviewSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        return (
            SessionAssessment.objects.in_organization(self.organization)
            .select_related(*ASSESSMENT_RELATED)
            .prefetch_related(*ASSESSMENT_PREFETCH)
        )

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=LeadAssessmentSerializer,
                description="Reviewed. The assessment has left the pending queue.",
            ),
            403: OpenApiResponse(description="Not the lead teacher."),
            404: OpenApiResponse(description="No such assessment."),
        }
    )
    def post(self, request, *args, **kwargs):
        assessment = self.get_object()
        serializer = self.get_serializer(assessment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        assessment = serializer.save()
        body = LeadAssessmentSerializer(
            assessment, context=self.get_serializer_context()
        ).data
        return Response(body, status=status.HTTP_200_OK)


# --- Reporting, progress, snapshots ------------------------------------------


PERIOD_PARAMS = [
    OpenApiParameter(
        name="from",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Inclusive ISO-8601 start of the period, by session date.",
    ),
    OpenApiParameter(
        name="to",
        type=str,
        location=OpenApiParameter.QUERY,
        required=False,
        description="Exclusive ISO-8601 end of the period, by session date.",
    ),
]


class TeacherQualityReportView(AcademyScopedView, generics.GenericAPIView):
    """GET /api/assessment/organizations/<organization_pk>/reports/teachers/ — lead-only quality visibility.

    Optionally narrowed by ``from``/``to`` and ``track_id``. Per teacher: how many
    sessions they assessed, the overall average, the average per track, how many
    they flagged, and how many of those flags are still waiting on the lead.

    Two things it is not. It is **not** a leaderboard: rows come back ordered by
    username and carry no rank. And it does **not** invent rows — a teacher who
    assessed nothing in the window is absent rather than present with 0.00, because
    a missing assessment is missing data and a zero next to someone's name is an
    accusation.
    """

    serializer_class = TeacherReportSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="track_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Restrict to one track.",
            )
        ],
        responses={
            200: OpenApiResponse(response=TeacherReportSerializer(many=True)),
            400: OpenApiResponse(description="An unparseable period or track_id."),
            403: OpenApiResponse(
                description="Not the lead teacher — a sub-teacher has no academy-wide view."
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        start, end = report_period(request)
        track = requested_track(request, organization=self.organization, required=False)
        rows = teacher_report(
            start=start, end=end, track=track, organization=self.organization
        )
        return Response(
            TeacherReportSerializer(
                rows, many=True, context=self.get_serializer_context()
            ).data
        )


class ProgressView(AcademyScopedView, generics.GenericAPIView):
    """Shared body of the two progress endpoints, so one shape serves both.

    Subclasses differ only in *whose* progress they resolve. Everything about what
    a family may see lives in ``reporting.student_progress``, which never builds an
    internal field in the first place.
    """

    serializer_class = StudentProgressSerializer

    def resolve_student(self, request):
        raise NotImplementedError

    def get(self, request, *args, **kwargs):
        student = self.resolve_student(request)
        track = requested_track(request, organization=self.organization)
        start, end = report_period(request)
        progress = student_progress(
            student=student, track=track, start=start, end=end
        )
        return Response(
            StudentProgressSerializer(
                progress, context=self.get_serializer_context()
            ).data
        )


class MyProgressView(ProgressView):
    """GET /api/assessment/organizations/<organization_pk>/progress/mine/?track_id= — the student's own progress."""

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def resolve_student(self, request):
        return request.user

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="track_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Which track's progress. Progress is per track.",
            )
        ],
        responses={
            200: OpenApiResponse(response=StudentProgressSerializer),
            400: OpenApiResponse(description="track_id missing or unknown."),
            403: OpenApiResponse(description="Not a student account."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ChildProgressView(ProgressView):
    """GET /api/assessment/organizations/<organization_pk>/progress/child/?student_id=&track_id= — a linked child's.

    The ``ParentLink`` check is the whole endpoint: an unrelated parent asking about
    somebody else's child is refused, and the refusal happens before any progress is
    computed, not by omitting fields from a response that was built anyway.
    """

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsParent]

    def resolve_student(self, request):
        return linked_child(
            request.user,
            required_int_param(request, "student_id"),
            organization=self.organization,
        )

    @extend_schema(
        parameters=PERIOD_PARAMS
        + [
            OpenApiParameter(
                name="student_id",
                type=int,
                location=OpenApiParameter.QUERY,
                required=True,
                description="Which of your children.",
            ),
            OpenApiParameter(
                name="track_id", type=int, location=OpenApiParameter.QUERY, required=True
            ),
        ],
        responses={
            200: OpenApiResponse(response=StudentProgressSerializer),
            400: OpenApiResponse(description="A missing or unknown parameter."),
            403: OpenApiResponse(
                description="Not a parent, or not a parent of that student."
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ProgressSnapshotCreateView(AcademyScopedView, generics.CreateAPIView):
    """POST /api/assessment/organizations/<organization_pk>/snapshots/ — the lead freezes a period.

    An explicit lead action, deliberately: automatic snapshot jobs are out of scope
    for this phase. Repeating a request for the same student, track and period
    returns the **existing** row with a **200** and changes nothing, which is the
    spec's duplicate rule — a snapshot that moved when you asked for it twice would
    not be a snapshot.
    """

    serializer_class = ProgressSnapshotCreateSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    @extend_schema(
        responses={
            201: OpenApiResponse(
                response=ProgressSnapshotSerializer, description="Generated."
            ),
            200: OpenApiResponse(
                response=ProgressSnapshotSerializer,
                description=(
                    "This period was already snapshotted. The existing row is "
                    "returned untouched — no duplicate, and no recomputation."
                ),
            ),
            400: OpenApiResponse(
                description="Not a student, an inverted period, or an unknown track."
            ),
            403: OpenApiResponse(description="Not the lead teacher."),
        }
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snapshot = serializer.save()
        if getattr(snapshot, "visible_to_family", False):
            from notifications.services import notify_progress_ready
            notify_progress_ready(snapshot)
        body = ProgressSnapshotSerializer(
            snapshot, context=self.get_serializer_context()
        ).data
        return Response(
            body,
            status=(
                status.HTTP_201_CREATED
                if getattr(serializer, "created", True)
                else status.HTTP_200_OK
            ),
        )


class LeadSnapshotListView(AcademyScopedView, generics.ListAPIView):
    """GET /api/assessment/organizations/<organization_pk>/snapshots/?student_id=&track_id= — the lead's own view.

    Unpublished rows included: the lead generated them, and needs to see what is
    waiting to be released.
    """

    serializer_class = ProgressSnapshotSerializer
    permission_classes = [IsAuthenticated, IsOrganizationMember, IsLeadTeacher]

    def get_queryset(self):
        queryset = (
            ProgressSnapshot.objects.in_organization(self.organization)
            .select_related("student", "track", "generated_by")
        )
        student_id = optional_int_param(self.request, "student_id")
        if student_id is not None:
            queryset = queryset.filter(student_id=student_id)
        track = requested_track(self.request, organization=self.organization, required=False)
        if track is not None:
            queryset = queryset.filter(track=track)
        return queryset

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id", type=int, location=OpenApiParameter.QUERY, required=False
            ),
            OpenApiParameter(
                name="track_id", type=int, location=OpenApiParameter.QUERY, required=False
            ),
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class FamilySnapshotListView(AcademyScopedView, generics.ListAPIView):
    """Shared body of the two family snapshot endpoints.

    ``visible_to_family=True`` is applied in the queryset, not by a serializer
    field: an unpublished snapshot must be unreachable, not merely unrendered.
    """

    serializer_class = FamilyProgressSnapshotSerializer

    def resolve_student(self, request):
        raise NotImplementedError

    def get_queryset(self):
        student = self.resolve_student(self.request)
        queryset = (
            ProgressSnapshot.objects.in_organization(self.organization)
            .filter(student=student, visible_to_family=True)
            .select_related("track")
        )
        track = requested_track(self.request, organization=self.organization, required=False)
        if track is not None:
            queryset = queryset.filter(track=track)
        return queryset


class MySnapshotListView(FamilySnapshotListView):
    """GET /api/assessment/organizations/<organization_pk>/snapshots/mine/?track_id= — the student's published snapshots."""

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsStudent]

    def resolve_student(self, request):
        return request.user

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="track_id", type=int, location=OpenApiParameter.QUERY, required=False
            )
        ]
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ChildSnapshotListView(FamilySnapshotListView):
    """GET /api/assessment/organizations/<organization_pk>/snapshots/child/?student_id=&track_id= — a linked child's."""

    permission_classes = [IsAuthenticated, IsOrganizationMember, IsParent]

    def resolve_student(self, request):
        return linked_child(
            request.user,
            required_int_param(request, "student_id"),
            organization=self.organization,
        )

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="student_id", type=int, location=OpenApiParameter.QUERY, required=True
            ),
            OpenApiParameter(
                name="track_id", type=int, location=OpenApiParameter.QUERY, required=False
            ),
        ],
        responses={
            200: OpenApiResponse(response=FamilyProgressSnapshotSerializer(many=True)),
            403: OpenApiResponse(description="Not a parent of that student."),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

