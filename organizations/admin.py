"""Admin registration for organizations — plain Django admin, no dashboard.

The developer's job here is to be able to see a tenant and everyone in it while
the API is being verified, so ``Organization`` carries its memberships inline and
``OrganizationMembership`` is also registered on its own for cross-tenant
searching. Nothing clever beyond that.

Two rules the admin keeps rather than relaxes:

* ``role`` and ``status`` are ordinary editable fields here, because the admin is
  where a developer or platform operator fixes a tenant by hand. The models still
  validate every write (``save()`` calls ``full_clean()``), so the admin cannot
  create a duplicate membership or a second owner either.
* No ``list_editable`` on ``role``. Changing organization authority from a list
  page is one mis-click away from handing someone the wrong academy's admin seat.
"""

from django.contrib import admin

from .models import Organization, OrganizationMembership, StudentEnrollment


class OrganizationMembershipInline(admin.TabularInline):
    """Who is in this academy, on the academy's own page."""

    model = OrganizationMembership
    extra = 0
    fields = ("user", "role", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)


class StudentEnrollmentInline(admin.TabularInline):
    """Which students are enrolled in this academy, on the academy's own page."""

    model = StudentEnrollment
    extra = 0
    fields = ("user", "track", "level", "status", "created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "timezone", "is_active", "created_at")
    list_filter = ("is_active", "timezone")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [OrganizationMembershipInline, StudentEnrollmentInline]


@admin.register(OrganizationMembership)
class OrganizationMembershipAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "role", "status", "created_at")
    list_filter = ("role", "status", "organization")
    search_fields = (
        "organization__name",
        "organization__slug",
        "user__username",
        "user__email",
    )
    autocomplete_fields = ("organization", "user")
    readonly_fields = ("created_at", "updated_at")


@admin.register(StudentEnrollment)
class StudentEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("organization", "user", "track", "level", "status", "created_at")
    list_filter = ("status", "organization")
    search_fields = (
        "organization__name",
        "organization__slug",
        "user__username",
        "user__email",
    )
    autocomplete_fields = ("organization", "user")
    readonly_fields = ("created_at", "updated_at")
