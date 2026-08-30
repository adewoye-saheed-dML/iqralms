"""Admin registrations for assessment.

The rubric side is genuinely editable here — it is live configuration, and the
admin is a reasonable place for the lead to maintain it. The assessment side is
deliberately close to read-only:

* ``SessionAssessment`` allows the three lead-review fields and nothing else. The
  model refuses the rest anyway (teacher data is immutable after submission), so
  making them editable would only produce validation errors that look like bugs.
* ``AssessmentScore`` is not editable at all, and is shown inline read-only.
  ``AssessmentScore.save()`` raises on any update, so there is no form that could
  succeed.
* ``ProgressSnapshot`` allows ``visible_to_family`` only. That is also the sole
  way to publish a snapshot generated unpublished — the phase's API has no
  endpoint for it (see tech-debt.md).
"""

from django.contrib import admin

from .models import (
    AssessmentCriterion,
    AssessmentRubric,
    AssessmentScore,
    ProgressSnapshot,
    SessionAssessment,
)


class AssessmentCriterionInline(admin.TabularInline):
    model = AssessmentCriterion
    extra = 1
    fields = ("order", "name", "description", "active")


@admin.register(AssessmentRubric)
class AssessmentRubricAdmin(admin.ModelAdmin):
    list_display = ("name", "track", "active")
    list_filter = ("active", "track")
    search_fields = ("name", "description", "track__name")
    autocomplete_fields = ("track",)
    inlines = [AssessmentCriterionInline]


@admin.register(AssessmentCriterion)
class AssessmentCriterionAdmin(admin.ModelAdmin):
    list_display = ("name", "rubric", "order", "active")
    list_filter = ("active", "rubric__track")
    search_fields = ("name", "description", "rubric__name")
    autocomplete_fields = ("rubric",)


class AssessmentScoreInline(admin.TabularInline):
    """Read-only: a submitted score cannot be changed in this phase."""

    model = AssessmentScore
    extra = 0
    can_delete = False
    fields = ("criterion_name", "score", "comment")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SessionAssessment)
class SessionAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "track",
        "assessed_by",
        "assessed_at",
        "flagged_for_review",
        "lead_reviewed_at",
    )
    list_filter = ("flagged_for_review", "track", "assessed_by")
    search_fields = (
        "student__username",
        "assessed_by__username",
        "teacher_summary",
        "rubric_name",
    )
    date_hierarchy = "assessed_at"
    inlines = [AssessmentScoreInline]
    #: Everything the teacher submitted. Immutable by model rule, so read-only here.
    readonly_fields = (
        "booking",
        "student",
        "track",
        "assessed_by",
        "assessed_at",
        "teacher_summary",
        "flagged_for_review",
        "flag_reason",
        "rubric_name",
        "criteria_snapshot",
    )
    autocomplete_fields = ("lead_reviewed_by",)

    def has_add_permission(self, request):
        # An assessment is submitted by the teacher who taught the session, over
        # the API, against a completed booking. There is no meaningful admin form
        # for that — the criterion set has to come from the live rubric.
        return False


@admin.register(ProgressSnapshot)
class ProgressSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "track",
        "period_start",
        "period_end",
        "assessed_sessions",
        "overall_average",
        "visible_to_family",
    )
    list_filter = ("visible_to_family", "track")
    list_editable = ("visible_to_family",)
    search_fields = ("student__username", "track__name", "summary")
    readonly_fields = (
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
    )

    def has_add_permission(self, request):
        # Snapshots are computed, never typed in: every number would otherwise be
        # a claim rather than a measurement.
        return False
