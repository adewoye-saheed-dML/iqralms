"""Admin registrations.

The curriculum is academy-owned from SaaS Phase 3, and the admin is where a
platform operator sees across academies — so every list here leads with, or can be
filtered by, the owning organization. Without that the admin would show two
academies' ``tajweed`` tracks as two identical rows.

``TeacherTrack`` is registered on its own rather than only inlined, because the
question an operator actually asks is "what does this teacher teach, and where",
which spans academies and so cannot be answered from one academy's page.
"""

from django.contrib import admin

from .models import Level, PlacementResult, TeacherTrack, Track


class LevelInline(admin.TabularInline):
    model = Level
    extra = 1
    fields = ("order", "name", "min_age", "group_eligible")


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "organization", "level_count")
    list_filter = ("organization",)
    search_fields = ("name", "slug", "organization__name", "organization__slug")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("organization",)
    inlines = [LevelInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("organization")

    @admin.display(description="Levels")
    def level_count(self, obj):
        return obj.levels.count()


@admin.register(Level)
class LevelAdmin(admin.ModelAdmin):
    list_display = ("track", "academy", "order", "name", "min_age", "group_eligible")
    list_filter = ("track__organization", "track", "group_eligible")
    search_fields = ("name", "track__name", "track__organization__name")
    autocomplete_fields = ("track",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("track__organization")

    @admin.display(description="Academy", ordering="track__organization__name")
    def academy(self, obj):
        # Level has no organization column — the academy is its track's, which is
        # exactly what this displays rather than storing a second copy of.
        return obj.track.organization


@admin.register(TeacherTrack)
class TeacherTrackAdmin(admin.ModelAdmin):
    list_display = ("teacher", "academy", "track", "active", "updated_at")
    list_filter = ("active", "membership__organization")
    search_fields = (
        "membership__user__username",
        "membership__user__email",
        "track__name",
        "track__slug",
        "membership__organization__name",
    )
    autocomplete_fields = ("membership", "track")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("membership__user", "membership__organization", "track")
        )

    @admin.display(description="Teacher", ordering="membership__user__username")
    def teacher(self, obj):
        return obj.membership.user

    @admin.display(description="Academy", ordering="membership__organization__name")
    def academy(self, obj):
        return obj.membership.organization


@admin.register(PlacementResult)
class PlacementResultAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "academy",
        "track",
        "status",
        "skipped_as_beginner",
        "recommended_level",
        "reviewed_by",
        "reviewed_at",
    )
    list_filter = ("status", "track__organization", "track", "skipped_as_beginner")
    search_fields = (
        "student__username",
        "student__email",
        "track__name",
        "track__organization__name",
    )
    autocomplete_fields = ("student", "track", "recommended_level", "reviewed_by")
    # status is always derived from reviewed_at by the model, so it is never
    # typed in. reviewed_at stays editable: correcting a review is an admin
    # operation (the API's review endpoint is one-way), and the model requires
    # recommended_level and reviewed_at to be set together.
    readonly_fields = ("status",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("track__organization")

    @admin.display(description="Academy", ordering="track__organization__name")
    def academy(self, obj):
        return obj.track.organization
