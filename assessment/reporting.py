"""Read-side aggregation: teacher quality, family progress.

Nothing here writes. It exists so that the lead's report, a family's progress
view and a stored snapshot cannot end up with three different ideas of what an
average is — every one of them runs through ``models.average_of`` and
``models.criterion_averages_for``, which is CLAUDE.md's "use one score definition
everywhere" made structural rather than aspirational.

Two rules this module is careful about, both from CLAUDE.md:

* **Missing assessments are missing data.** A teacher with nothing assessed in a
  window is absent from the report rather than present with a zero; a student with
  an unassessed month gets ``overall_average: null``. Absence and zero say
  opposite things about a person's teaching.
* **No leaderboards.** ``teacher_report`` orders by username. Sorting teachers by
  score is the beginning of ranking them, which the phase spec rules out and a
  later phase gets to decide on with real data in front of it.
"""

from curriculum.models import PlacementResult, Status
from scheduling.models import Booking

from .models import (
    AssessmentScore,
    SessionAssessment,
    assessments_in_period,
    average_of,
    completed_bookings_in_period,
    criterion_averages_for,
)

#: How many teacher summaries a progress response carries. A family wants the
#: recent picture, not a transcript; the full history is the assessment list.
RECENT_SUMMARY_LIMIT = 5


def scores_for(assessments):
    """Every criterion score belonging to ``assessments``."""
    return AssessmentScore.objects.filter(assessment__in=assessments)


def teacher_report(*, start=None, end=None, track=None, organization=None):
    """One row per teacher who assessed a session in scope. Lead-only data.

    ``[{teacher, assessed_sessions, overall_average, by_track, flagged,
    awaiting_lead_review}]``, ordered by username.

    A teacher who taught in the window but recorded nothing does not appear.
    That is deliberate and it is the honest shape: the report's subject is
    assessment data, and inventing a row of zeroes for a teacher with none would
    put a 0.00 next to their name for work they may have done perfectly well.
    """
    if organization is not None:
        base_qs = SessionAssessment.objects.in_organization(organization)
    elif track is not None and getattr(track, "organization", None) is not None:
        base_qs = SessionAssessment.objects.in_organization(track.organization)
    else:
        base_qs = SessionAssessment.objects.all()

    assessments = assessments_in_period(
        base_qs, start=start, end=end
    )
    if track is not None:
        assessments = assessments.filter(track=getattr(track, "pk", track))
    assessments = assessments.select_related(
        "assessed_by", "track", "booking", "booking__level"
    )

    rows = {}
    for assessment in assessments:
        row = rows.setdefault(
            assessment.assessed_by_id,
            {
                "teacher": assessment.assessed_by,
                "assessment_ids": [],
                "tracks": {},
                "flagged": 0,
                "awaiting_lead_review": 0,
            },
        )
        row["assessment_ids"].append(assessment.pk)
        row["tracks"].setdefault(
            assessment.track_id, {"track": assessment.track, "assessment_ids": []}
        )["assessment_ids"].append(assessment.pk)
        if assessment.flagged_for_review:
            row["flagged"] += 1
            if assessment.lead_reviewed_at is None:
                row["awaiting_lead_review"] += 1

    # One query for every score in scope, bucketed in Python. The alternative is
    # a per-teacher and per-track aggregate query each, which is the same
    # arithmetic at N+1 the cost — and the average has to be computed by
    # average_of either way, so pushing AVG() into SQL would introduce a second
    # definition of the thing this module exists to keep singular.
    by_assessment = {}
    for assessment_id, score in AssessmentScore.objects.filter(
        assessment__in=assessments
    ).values_list("assessment_id", "score"):
        by_assessment.setdefault(assessment_id, []).append(score)

    def values_for(assessment_ids):
        return [
            score
            for assessment_id in assessment_ids
            for score in by_assessment.get(assessment_id, [])
        ]

    report = []
    for row in rows.values():
        report.append(
            {
                "teacher": row["teacher"],
                "assessed_sessions": len(row["assessment_ids"]),
                "overall_average": average_of(values_for(row["assessment_ids"])),
                "by_track": sorted(
                    (
                        {
                            "track": bucket["track"],
                            "assessed_sessions": len(bucket["assessment_ids"]),
                            "overall_average": average_of(
                                values_for(bucket["assessment_ids"])
                            ),
                        }
                        for bucket in row["tracks"].values()
                    ),
                    key=lambda entry: entry["track"].name,
                ),
                "flagged": row["flagged"],
                "awaiting_lead_review": row["awaiting_lead_review"],
            }
        )
    # By name. Not by average — see the module docstring.
    report.sort(key=lambda entry: entry["teacher"].username)
    return report


def recommended_level_for(student, track):
    """The student's placed level in this track, or None if never reviewed.

    Read from Phase 2's ``PlacementResult`` and nothing else. Assessment does not
    write this field and must not: moving a student's level on the strength of
    rubric scores is a product decision the phase spec explicitly defers.
    """
    placement = (
        PlacementResult.objects.filter(
            student=student, track=track, status=Status.REVIEWED
        )
        .select_related("recommended_level", "recommended_level__track")
        .first()
    )
    return placement.recommended_level if placement else None


def student_progress(*, student, track, start=None, end=None, summary_limit=None):
    """One student's progress in one track — the family-facing shape.

    Carries no internal quality-control field: no flag, no flag reason, no lead
    review note, nothing about who else the teacher has taught. That is enforced
    by what this function *builds*, not only by what a serializer renders, so a
    future serializer change cannot leak a field this never produced.
    """
    limit = RECENT_SUMMARY_LIMIT if summary_limit is None else summary_limit

    assessments = assessments_in_period(
        SessionAssessment.objects.filter(student=student, track=track),
        start=start,
        end=end,
    ).select_related("booking", "assessed_by")
    scores = scores_for(assessments)
    completed = completed_bookings_in_period(
        Booking.objects.filter(student=student, level__track=track),
        start=start,
        end=end,
    )

    recent = [
        {
            "assessment_id": assessment.pk,
            "taught_at": assessment.booking.start_time_utc,
            "assessed_at": assessment.assessed_at,
            "teacher_summary": assessment.teacher_summary,
        }
        for assessment in assessments.exclude(teacher_summary="")[:limit]
    ]

    return {
        "track": track,
        "recommended_level": recommended_level_for(student, track),
        "period_start": start,
        "period_end": end,
        "completed_sessions": completed.count(),
        "assessed_sessions": assessments.count(),
        "overall_average": average_of(scores.values_list("score", flat=True)),
        "criterion_averages": criterion_averages_for(scores),
        "last_assessed_at": (
            assessments.order_by("-assessed_at").values_list("assessed_at", flat=True).first()
        ),
        "recent_summaries": recent,
    }
