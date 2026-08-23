"""Admin registrations.

Tracks and levels are maintained here — there is deliberately no write API for
curriculum data in Phase 2, only the public read endpoint.
"""

from django.contrib import admin

from .models import Level, PlacementResult, Track


class LevelInline(admin.TabularInline):
    model = Level
    extra = 1
    fields = ("order", "name", "min_age", "group_eligible")


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "level_count")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [LevelInline]

    @admin.display(description="Levels")
    def level_count(self, obj):
        return obj.levels.count()


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ("track", "order", "name", "min_age", "group_eligible")
    list_filter = ("track", "group_eligible")
    search_fields = ("name", "track__name")
    autocomplete_fields = ("track",)


@admin.register(PlacementResult)
class PlacementResultAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "track",
        "status",
        "skipped_as_beginner",
        "recommended_level",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "track", "skipped_as_beginner")
    search_fields = ("student__username", "student__email", "track__name")
    autocomplete_fields = ("student", "track", "recommended_level", "reviewed_by")
    # status is always derived from reviewed_at by the model, so it is never
    # typed in. reviewed_at stays editable: correcting a review is an admin
    # operation (the API's review endpoint is one-way), and the model requires
    # recommended_level and reviewed_at to be set together.
    readonly_fields = ("status",)
