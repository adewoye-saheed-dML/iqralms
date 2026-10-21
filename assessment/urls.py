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
    StudentWardAssessmentsView,
    SubmissionAttachmentFileView,
    SubmissionAudioStreamView,
    SubmissionGradeView,
    TeacherAssessmentListView,
    TeacherQualityReportView,
    TeachingProgressView,
    SessionalExamListCreateView,
    SessionalExamDetailView,
    SessionalExamQuestionManageView,
    SessionalExamSubmissionsListView,
    SessionalExamTakeSubmitView,
    SessionalExamGradeView,
    ParentWardSessionalExamsView,
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
    # Reporting and progress.
    path(
        f"{ACADEMY}reports/teachers/",
        TeacherQualityReportView.as_view(),
        name="report-teachers",
    ),
    path(
        f"{ACADEMY}progress/mine/",
        MyProgressView.as_view(),
        name="progress-mine",
    ),
    path(
        f"{ACADEMY}progress/child/",
        ChildProgressView.as_view(),
        name="progress-child",
    ),
    path(
        f"{ACADEMY}progress/teaching/",
        TeachingProgressView.as_view(),
        name="progress-teaching",
    ),
    # Snapshots.
    path(
        f"{ACADEMY}snapshots/",
        ProgressSnapshotCreateView.as_view(),
        name="snapshot-create",
    ),
    path(
        f"{ACADEMY}snapshots/all/",
        LeadSnapshotListView.as_view(),
        name="snapshot-list",
    ),
    path(
        f"{ACADEMY}snapshots/mine/",
        MySnapshotListView.as_view(),
        name="snapshot-mine",
    ),
    path(
        f"{ACADEMY}snapshots/child/",
        ChildSnapshotListView.as_view(),
        name="snapshot-child",
    ),
    # Sessional Examinations & Question-by-Question Grading
    path(
        f"{ACADEMY}sessional-exams/",
        SessionalExamListCreateView.as_view(),
        name="sessional-exam-list",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/",
        SessionalExamDetailView.as_view(),
        name="sessional-exam-detail",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/questions/",
        SessionalExamQuestionManageView.as_view(),
        name="sessional-exam-questions",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/questions/<int:question_pk>/",
        SessionalExamQuestionManageView.as_view(),
        name="sessional-exam-question-detail",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/submissions/",
        SessionalExamSubmissionsListView.as_view(),
        name="sessional-exam-submissions",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/submit/",
        SessionalExamTakeSubmitView.as_view(),
        name="sessional-exam-take-submit",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/my-submission/",
        SessionalExamTakeSubmitView.as_view(),
        name="sessional-exam-my-submission",
    ),
    path(
        f"{ACADEMY}sessional-exams/<int:pk>/submissions/<int:submission_pk>/grade/",
        SessionalExamGradeView.as_view(),
        name="sessional-exam-grade",
    ),
    path(
        f"{ACADEMY}sessional-exams/ward/<int:student_id>/",
        ParentWardSessionalExamsView.as_view(),
        name="sessional-exam-ward",
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

