"""Domain services for assessment: personal learning space and teacher attachment checks."""

from decimal import Decimal
from typing import List, Optional

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied

from accounts.models import ParentLink, Role, User
from curriculum.models import Status, TeacherTrack, Track
from organizations.models import EnrollmentStatus, Organization, OrganizationRole, StudentEnrollment, active_membership
from scheduling.models import Booking

from .models import (
    AssessmentScore,
    AssignmentSubmission,
    SessionAssessment,
    StudentAssignment,
    SubmissionStatus,
    average_of,
)
from .serializers import AssignmentSubmissionSerializer


def teacher_is_attached_to_student(
    teacher: User,
    student: User,
    organization: Organization,
    track: Optional[Track] = None,
) -> bool:
    """Check if `teacher` has an active teaching relationship with `student` in `organization`.

    If `track` is specified, verifies the teacher specifically teaches that subject/track
    to the student (via completed/scheduled bookings in that track, or active enrollment in that track).
    If `track` is None, verifies whether the teacher is attached to the student in any track.
    """
    if teacher is None or student is None or organization is None:
        return False

    booking_qs = Booking.objects.filter(
        level__track__organization=organization,
        teacher=teacher,
        student=student,
    )
    enrollment_qs = StudentEnrollment.objects.filter(
        organization=organization,
        user=student,
        teacher=teacher,
        status=EnrollmentStatus.ACTIVE,
    )

    if track is not None:
        track_pk = getattr(track, "pk", track)
        booking_qs = booking_qs.filter(level__track_id=track_pk)
        enrollment_qs = enrollment_qs.filter(track_id=track_pk)

    return booking_qs.exists() or enrollment_qs.exists()


def get_teacher_tracks_for_student(
    teacher: User,
    student: User,
    organization: Organization,
) -> List[int]:
    """Return the list of track IDs that `teacher` offers/teaches to `student` in `organization`."""
    if teacher is None or student is None or organization is None:
        return []

    booking_tracks = Booking.objects.filter(
        level__track__organization=organization,
        teacher=teacher,
        student=student,
    ).values_list("level__track_id", flat=True)

    enrollment_tracks = StudentEnrollment.objects.filter(
        organization=organization,
        user=student,
        teacher=teacher,
        status=EnrollmentStatus.ACTIVE,
    ).values_list("track_id", flat=True)

    return list(set(booking_tracks) | set(enrollment_tracks))


def get_teacher_students(teacher: User, organization: Organization):
    """Return distinct students that `teacher` is attached to in `organization`."""
    booking_students = Booking.objects.filter(
        level__track__organization=organization,
        teacher=teacher,
    ).values_list("student_id", flat=True)

    enrollment_students = StudentEnrollment.objects.filter(
        organization=organization,
        teacher=teacher,
        status=EnrollmentStatus.ACTIVE,
    ).values_list("user_id", flat=True)

    student_ids = set(booking_students) | set(enrollment_students)
    return User.objects.filter(id__in=student_ids, role=Role.STUDENT)


def get_teacher_organization_tracks(teacher: User, organization: Organization) -> List[int]:
    """Return track IDs that `teacher` is authorized to teach in `organization`."""
    track_ids = set(
        TeacherTrack.objects.filter(
            membership__organization=organization,
            membership__user=teacher,
            status=Status.ACTIVE,
        ).values_list("track_id", flat=True)
    )

    booking_tracks = Booking.objects.filter(
        level__track__organization=organization,
        teacher=teacher,
    ).values_list("level__track_id", flat=True)
    track_ids.update(booking_tracks)

    enrollment_tracks = StudentEnrollment.objects.filter(
        organization=organization,
        teacher=teacher,
        status=EnrollmentStatus.ACTIVE,
    ).values_list("track_id", flat=True)
    track_ids.update(enrollment_tracks)

    return list(track_ids)


def get_student_learning_space(
    *,
    student: User,
    organization: Organization,
    caller_user: User,
    track_id: Optional[int] = None,
    serializer_context: Optional[dict] = None,
) -> dict:
    """Compile the joined personal learning space for a student in an academy.

    Joins:
    - Session assessments and rubric scores
    - Continuous assignments, submissions, and feedback
    - Overall averages and track-by-track breakdown

    Enforces that teachers who are not attached to the student cannot access this,
    and teachers who only teach certain subjects cannot see assessments or progress
    for subjects they do not offer to the student.
    """
    membership = active_membership(user=caller_user, organization=organization)
    is_manager = (
        (membership and membership.role in [OrganizationRole.OWNER, OrganizationRole.ADMIN])
        or caller_user.role == Role.LEAD
    )
    is_teacher = (
        (membership and membership.role == OrganizationRole.TEACHER)
        or caller_user.role in [Role.LEAD, Role.SUB]
    )
    is_student = (caller_user.id == student.id)
    is_parent = ParentLink.objects.filter(parent=caller_user, student=student).exists()

    if not (is_manager or is_student or is_parent or is_teacher):
        raise PermissionDenied("You do not have permission to view this student's learning space.")

    tracks_in_scope: Optional[List[int]] = None
    if is_teacher and not is_manager:
        if not teacher_is_attached_to_student(caller_user, student, organization):
            raise PermissionDenied("You do not have an assigned teaching relationship with this student.")
        allowed_tracks = get_teacher_tracks_for_student(caller_user, student, organization)
        if track_id:
            if track_id not in allowed_tracks:
                raise PermissionDenied("You do not offer this subject to this student.")
            tracks_in_scope = [track_id]
        else:
            tracks_in_scope = allowed_tracks
    elif track_id:
        tracks_in_scope = [track_id]

    # --- 1. Continuous Assignments & Homework Submissions ---
    assignments_qs = StudentAssignment.objects.in_organization(organization).filter(
        Q(assigned_student=student) | Q(assigned_student__isnull=True)
    )
    if tracks_in_scope is not None:
        assignments_qs = assignments_qs.filter(track_id__in=tracks_in_scope)
    total_assigned = assignments_qs.count()

    submissions_qs = (
        AssignmentSubmission.objects.in_organization(organization)
        .filter(student=student)
        .select_related("assignment", "graded_by")
        .order_by("-submitted_at")
    )
    if tracks_in_scope is not None:
        submissions_qs = submissions_qs.filter(assignment__track_id__in=tracks_in_scope)

    total_submitted = submissions_qs.count()
    graded_submissions = submissions_qs.filter(status=SubmissionStatus.GRADED)
    total_graded = graded_submissions.count()

    scores_list = [
        float((s.score / s.assignment.max_score) * 100)
        for s in graded_submissions
        if s.score is not None and s.assignment.max_score > 0
    ]
    avg_score_pct = round(sum(scores_list) / len(scores_list), 1) if scores_list else None

    recent_submissions_data = AssignmentSubmissionSerializer(
        submissions_qs[:10], many=True, context=serializer_context or {}
    ).data

    # --- 2. Live Session Assessments (1-on-1 recitation & rubric evaluations) ---
    session_assessments_qs = (
        SessionAssessment.objects.in_organization(organization)
        .filter(student=student)
        .select_related("booking", "booking__level", "track", "assessed_by")
        .prefetch_related("scores")
        .order_by("-assessed_at")
    )
    if tracks_in_scope is not None:
        session_assessments_qs = session_assessments_qs.filter(track_id__in=tracks_in_scope)

    total_sessions_assessed = session_assessments_qs.count()

    all_scores = AssessmentScore.objects.filter(
        assessment__in=session_assessments_qs
    ).values_list("score", flat=True)
    overall_session_avg = average_of(all_scores)

    recent_session_assessments = []
    for sa in session_assessments_qs[:10]:
        sa_scores = [
            {"criterion_name": sc.criterion_name, "score": sc.score, "comment": sc.comment}
            for sc in sa.scores.all()
        ]
        sa_avg = average_of([sc.score for sc in sa.scores.all()])
        recent_session_assessments.append({
            "id": sa.id,
            "booking_id": sa.booking_id,
            "track_id": sa.track_id,
            "track_name": sa.track.name if sa.track else "",
            "level_name": sa.booking.level.name if sa.booking and sa.booking.level else "",
            "teacher_name": sa.assessed_by.get_full_name() or sa.assessed_by.username,
            "taught_at": sa.booking.start_time_utc if sa.booking else None,
            "assessed_at": sa.assessed_at,
            "teacher_summary": sa.teacher_summary,
            "overall_score": sa_avg,
            "scores": sa_scores,
        })

    # --- 3. Track / Subject Progress Breakdown ---
    enrollments = StudentEnrollment.objects.filter(
        organization=organization, user=student
    ).select_related("track", "level", "teacher")

    enrolled_track_ids = set(enrollments.values_list("track_id", flat=True))
    assessed_track_ids = set(session_assessments_qs.values_list("track_id", flat=True))
    all_student_track_ids = enrolled_track_ids | assessed_track_ids

    if tracks_in_scope is not None:
        all_student_track_ids = all_student_track_ids.intersection(set(tracks_in_scope))

    tracks_progress_list = []
    tracks_by_id = {
        t.id: t for t in Track.objects.filter(id__in=all_student_track_ids)
    }
    enrollments_by_track = {
        e.track_id: e for e in enrollments if e.track_id in all_student_track_ids
    }

    for tid in sorted(all_student_track_ids):
        trk = tracks_by_id.get(tid)
        if not trk:
            continue
        enr = enrollments_by_track.get(tid)

        trk_sessions = session_assessments_qs.filter(track_id=tid)
        trk_session_count = trk_sessions.count()
        trk_scores = AssessmentScore.objects.filter(assessment__in=trk_sessions).values_list("score", flat=True)
        trk_session_avg = average_of(trk_scores)

        trk_assignments_count = assignments_qs.filter(track_id=tid).count()
        trk_submissions = submissions_qs.filter(assignment__track_id=tid)
        trk_submissions_count = trk_submissions.count()
        trk_graded = trk_submissions.filter(status=SubmissionStatus.GRADED)
        trk_score_pcts = [
            float((s.score / s.assignment.max_score) * 100)
            for s in trk_graded
            if s.score is not None and s.assignment.max_score > 0
        ]
        trk_avg_pct = round(sum(trk_score_pcts) / len(trk_score_pcts), 1) if trk_score_pcts else None

        level_name = enr.level.name if (enr and enr.level) else None
        teacher_name = (
            (enr.teacher.get_full_name() or enr.teacher.username)
            if (enr and enr.teacher)
            else None
        )

        tracks_progress_list.append({
            "track_id": trk.id,
            "track_name": trk.name,
            "level_name": level_name,
            "assigned_teacher_name": teacher_name,
            "session_assessments_count": trk_session_count,
            "session_average_score": trk_session_avg,
            "assignments_count": trk_assignments_count,
            "submissions_count": trk_submissions_count,
            "assignment_average_pct": trk_avg_pct,
        })

    return {
        "student": {
            "id": student.id,
            "username": student.username,
            "first_name": student.first_name,
            "last_name": student.last_name,
        },
        "total_assigned": total_assigned,
        "total_submitted": total_submitted,
        "total_graded": total_graded,
        "average_score_pct": avg_score_pct,
        "average_assignment_score_pct": avg_score_pct,
        "recent_submissions": recent_submissions_data,
        "total_sessions_assessed": total_sessions_assessed,
        "overall_session_average": overall_session_avg,
        "recent_session_assessments": recent_session_assessments,
        "tracks_progress": tracks_progress_list,
    }
