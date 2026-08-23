"""Admin registrations for scheduling.

Availability is maintained here — Phase 3 has no teacher-facing write API, the
same call Phase 2 made for curriculum data. Two notes on why the form takes UTC
rather than the teacher's local time, despite local being what a teacher thinks
in:

* One local window can convert into *two* UTC rows (a window that runs past
  midnight UTC), and a single ModelForm save cannot produce two rows. A local
  entry form would therefore have to silently drop half of some windows.
* ``Availability.create_from_local()`` is the supported local-time entry point,
  and the local reading of every stored row is shown here as a column so the
  UTC numbers can be sanity-checked at a glance.

A proper teacher-facing hours editor is a known gap (tech-debt.md).
"""

from django.contrib import admin

from .models import Availability, Booking


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        "teacher",
        "weekday",
        "start_time_utc",
        "end_time_utc",
        "teacher_local_window",
    )
    list_filter = ("weekday", "teacher")
    search_fields = ("teacher__username", "teacher__email")
    autocomplete_fields = ("teacher",)

    @admin.display(description="Teacher's local time")
    def teacher_local_window(self, obj):
        weekday, start, end = obj.local_window(obj.teacher.timezone)
        day = Availability(weekday=weekday).get_weekday_display()
        return f"{day} {start:%H:%M}-{end:%H:%M} ({obj.teacher.timezone})"


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "teacher",
        "level",
        "start_time_utc",
        "duration_minutes",
        "status",
    )
    list_filter = ("status", "teacher", "level__track")
    search_fields = (
        "student__username",
        "student__email",
        "teacher__username",
        "video_room_name",
    )
    autocomplete_fields = ("student", "teacher", "level")
    # The room name is generated once and never changes (the model enforces it),
    # so it is never typed in. The join URL is derived from it.
    readonly_fields = ("video_room_name", "video_join_url")

    @admin.display(description="Jitsi join URL")
    def video_join_url(self, obj):
        return obj.video_join_url if obj.pk else "—"
