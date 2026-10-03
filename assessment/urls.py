"""Assessment URLs, mounted at /api/assessment/.

Order matters here in a way it did not in the other apps: ``<int:pk>/`` for the
lead's assessment detail would happily swallow ``mine/`` or ``review/`` if it came
first, so every literal path is declared above it. Django stops at the first match,
so the numeric route is last on purpose.
"""

from django.urls import path

from .views import (
    AcademySubmissionsListView,
    AssessmentRubricDetailView,
    AssessmentRubricListCreateView,
    AssignmentDetailView,
    AssignmentListCreateView,
    AssignmentResourceFileView,
    AssignmentSubmissionCreateView,
    LeadAssessmentDetailView,
    LeadReviewQueueView,
    LeadReviewView,
    MyAssessmentListView,
    MyChildAssessmentListView,
    SessionAssessmentCreateView,
    StudentWardAssessmentsView,
    SubmissionAttachmentFileView,
    SubmissionAudioStreamView,
    SubmissionGradeView,
    TeacherAssessmentListView,
)

app_name = "assessment"

ACADEMY = "organizations/<int:organization_pk>/"

urlpatterns = [
    # Continuous Assignments & Homework Assessment
    path(
        f"{ACADEMY}assignments/",
        AssignmentListCreateView.as_view(),
        name="assignment-list",
    ),
    path(
        f"{ACADEMY}assignments/<int:pk>/",
        AssignmentDetailView.as_view(),
        name="assignment-detail",
    ),
    path(
        f"{ACADEMY}assignments/<int:pk>/resource/",
        AssignmentResourceFileView.as_view(),
        name="assignment-resource",
    ),
    path(
        f"{ACADEMY}assignments/<int:assignment_id>/submit/",
        AssignmentSubmissionCreateView.as_view(),
        name="assignment-submit",
    ),
    path(
        f"{ACADEMY}submissions/",
        AcademySubmissionsListView.as_view(),
        name="submission-list",
    ),
    path(
        f"{ACADEMY}submissions/<int:pk>/audio/",
        SubmissionAudioStreamView.as_view(),
        name="submission-audio",
    ),
    path(
        f"{ACADEMY}submissions/<int:pk>/attachment/",
        SubmissionAttachmentFileView.as_view(),
        name="submission-attachment",
    ),
    path(
        f"{ACADEMY}submissions/<int:pk>/grade/",
        SubmissionGradeView.as_view(),
        name="submission-grade",
    ),
    path(
        f"{ACADEMY}ward-progress/",
        StudentWardAssessmentsView.as_view(),
        name="ward-progress",
    ),
    path(
        f"{ACADEMY}learning-space/",
        StudentWardAssessmentsView.as_view(),
        name="learning-space",
    ),

    # Rubric configuration — lead only.
    path(
        f"{ACADEMY}rubrics/",
        AssessmentRubricListCreateView.as_view(),
        name="rubric-list",
    ),
    path(
        f"{ACADEMY}rubrics/<int:pk>/",
        AssessmentRubricDetailView.as_view(),
        name="rubric-detail",
    ),
    # Submission — the teacher who taught the session.
    path(
        f"{ACADEMY}bookings/<int:booking_id>/",
        SessionAssessmentCreateView.as_view(),
        name="assessment-create",
    ),
    # Reading assessments, one audience per path.
    path(
        f"{ACADEMY}mine/",
        MyAssessmentListView.as_view(),
        name="assessment-mine",
    ),
    path(
        f"{ACADEMY}child/",
        MyChildAssessmentListView.as_view(),
        name="assessment-child",
    ),
    path(
        f"{ACADEMY}teacher/mine/",
        TeacherAssessmentListView.as_view(),
        name="assessment-teacher-mine",
    ),
    # Lead review.
    path(
        f"{ACADEMY}review/queue/",
        LeadReviewQueueView.as_view(),
        name="review-queue",
    ),
    # Numeric routes last: they would otherwise shadow the literals above.
    path(
        f"{ACADEMY}<int:pk>/review/",
        LeadReviewView.as_view(),
        name="assessment-review",
    ),
    path(
        f"{ACADEMY}<int:pk>/",
        LeadAssessmentDetailView.as_view(),
        name="assessment-detail",
    ),
]

