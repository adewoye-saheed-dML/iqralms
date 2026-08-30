"""Admin registration for payouts — deliberately read-mostly.

Unlike ``pricing``, where the admin is where a mistake gets corrected, a payout is
not hand-editable here:

* **No add.** A payout is only ever created by ``services.generate_payouts``,
  which is what guarantees the amount matches the rate and the session. A row
  typed in by hand would be a financial fact with no derivation behind it.
* **No change once finalized.** The model refuses it anyway
  (``TeacherPayout.clean()``); the admin declines earlier so the form is never
  offered. A generated row remains changeable through the admin only in the one
  way the model permits — ``status`` and ``finalized_at`` — which is how a lead
  finalizes without the API if they have to.
* **No delete once finalized.** Deleting historical payout records is not normal
  application behaviour (spec, section 6). A generated draft may still be removed;
  regenerating it is one request.

If a finalized payout turns out to be wrong, that is a correction, and a
correction is an explicit later phase rather than an edit to history.
"""

from django.contrib import admin

from .models import PayoutStatus, TeacherPayout


@admin.register(TeacherPayout)
class TeacherPayoutAdmin(admin.ModelAdmin):
    list_display = (
        "teacher",
        "booking",
        "minutes_paid",
        "rate_used",
        "amount",
        "currency",
        "status",
        "finalized_at",
    )
    list_filter = ("status", "currency", "teacher")
    search_fields = (
        "teacher__username",
        "teacher__email",
        "booking__student__username",
    )
    autocomplete_fields = ("teacher", "booking", "cohort")
    readonly_fields = (
        "teacher",
        "booking",
        "cohort",
        "minutes_paid",
        "rate_used",
        "amount",
        "currency",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.status == PayoutStatus.FINALIZED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.status == PayoutStatus.FINALIZED:
            return False
        return super().has_delete_permission(request, obj)
