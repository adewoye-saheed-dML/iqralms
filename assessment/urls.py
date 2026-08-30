"""Assessment URLs, mounted at /api/assessment/.

Order matters here in a way it did not in the other apps: ``<int:pk>/`` for the
lead's assessment detail would happily swallow ``mine/`` or ``review/`` if it came
first, so every literal path is declared above it. Django stops at the first match,
so the numeric route is last on purpose.
"""

from django.urls import path

from .views import (
    AssessmentRubricDetailView,
    AssessmentRubricListCreateView,
    ChildProgressView,
    ChildSnapshotListView,
    LeadAssessmentDetailView,
    LeadReviewQueueView,
    LeadReviewView,
    LeadSnapshotListView,
    MyAssessmentListView,
    MyChildAssessmentListView,
    MyProgressView,
    MySnapshotListView,
    ProgressSnapshotCreateView,
    SessionAssessmentCreateView,
    TeacherAssessmentListView,
    TeacherQualityReportView,
)

app_name = "assessment"

urlpatterns = [
    # Rubric configuration — lead only.
    path("rubrics/", AssessmentRubricListCreateView.as_view(), name="rubric-list"),
    path(
        "rubrics/<int:pk>/",
        AssessmentRubricDetailView.as_view(),
        name="rubric-detail",
    ),
    # Submission — the teacher who taught the session.
    path(
        "bookings/<int:booking_id>/",
        SessionAssessmentCreateView.as_view(),
        name="assessment-create",
    ),
    # Reading assessments, one audience per path.
    path("mine/", MyAssessmentListView.as_view(), name="assessment-mine"),
    path("child/", MyChildAssessmentListView.as_view(), name="assessment-child"),
    path(
        "teacher/mine/",
        TeacherAssessmentListView.as_view(),
        name="assessment-teacher-mine",
    ),
    # Lead review.
    path("review/queue/", LeadReviewQueueView.as_view(), name="review-queue"),
    # Reporting and progress.
    path(
        "reports/teachers/",
        TeacherQualityReportView.as_view(),
        name="report-teachers",
    ),
    path("progress/mine/", MyProgressView.as_view(), name="progress-mine"),
    path("progress/child/", ChildProgressView.as_view(), name="progress-child"),
    # Snapshots.
    path("snapshots/", ProgressSnapshotCreateView.as_view(), name="snapshot-create"),
    path("snapshots/all/", LeadSnapshotListView.as_view(), name="snapshot-list"),
    path("snapshots/mine/", MySnapshotListView.as_view(), name="snapshot-mine"),
    path("snapshots/child/", ChildSnapshotListView.as_view(), name="snapshot-child"),
    # Numeric routes last: they would otherwise shadow the literals above.
    path("<int:pk>/review/", LeadReviewView.as_view(), name="assessment-review"),
    path("<int:pk>/", LeadAssessmentDetailView.as_view(), name="assessment-detail"),
]
