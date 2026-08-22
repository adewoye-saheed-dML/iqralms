"""Admin registrations.

Teacher profiles and sub-teacher approval are admin-only for now — there is
deliberately no API for either in Phase 1.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ParentLink, TeacherProfile, User


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
