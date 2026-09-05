"""Admin registrations.

Teacher profiles and sub-teacher approval are admin-only for now — there is
deliberately no API for either in Phase 1. SaaS Phase 2's per-academy teacher
terms do have an API (owner/admin, under /api/accounts/organizations/), and are
registered here too so a platform operator can see both halves side by side while
the two overlap.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import OrganizationTeacherConfiguration, ParentLink, TeacherProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "timezone", "is_minor", "is_active")
    list_filter = ("role", "is_minor", "is_active", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name", "signup_code")
    readonly_fields = ("signup_code",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            "Academy",
            {"fields": ("role", "timezone", "date_of_birth", "is_minor", "signup_code")},
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Academy", {"fields": ("email", "role", "timezone", "date_of_birth")}),
    )


@admin.register(ParentLink)
class ParentLinkAdmin(admin.ModelAdmin):
    list_display = ("parent", "student")
    search_fields = ("parent__username", "student__username", "student__signup_code")
    autocomplete_fields = ("parent", "student")


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "is_lead", "approved", "max_weekly_hours", "hourly_payout_rate")
    list_filter = ("is_lead", "approved")
    list_editable = ("approved",)
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user",)


@admin.register(OrganizationTeacherConfiguration)
class OrganizationTeacherConfigurationAdmin(admin.ModelAdmin):
    """One academy's terms for one teacher. Read the academy from the membership."""

    list_display = (
        "membership",
        "approved",
        "max_weekly_hours",
        "hourly_payout_rate",
        "updated_at",
    )
    list_filter = ("approved", "membership__organization")
    list_editable = ("approved",)
    search_fields = (
        "membership__user__username",
        "membership__user__email",
        "membership__organization__name",
        "membership__organization__slug",
    )
    autocomplete_fields = ("membership",)
    readonly_fields = ("created_at", "updated_at")
