"""Admin registrations for pricing.

Agreements are normally recorded through the lead-only API, but the admin is
where a mistake gets corrected — so ``active`` is editable here. Two things worth
knowing before using it:

* Creating a row here supersedes any live agreement for the same student and
  level, exactly as the API does: the rule lives in ``PricingAgreement.save()``.
* Flipping ``active`` back on by hand can collide with the partial unique
  constraint if a newer agreement is already live for that student and level.
  That is the constraint doing its job, not a bug — deactivate the newer row
  first.
"""

from django.contrib import admin

from .models import PricingAgreement


@admin.register(PricingAgreement)
class PricingAgreementAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "level",
        "standard_rate",
        "agreed_rate",
        "reason",
        "approved_by",
        "active",
    )
    list_filter = ("active", "reason", "level__track")
    search_fields = (
        "student__username",
        "student__email",
        "level__name",
        "notes",
    )
    autocomplete_fields = ("student", "level", "approved_by")
