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

from .models import Availability, Booking, Cohort, TeacherWaitlist, Weekday


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
        return (
            f"{Weekday(weekday).label} {start:%H:%M}-{end:%H:%M} "
            f"({obj.teacher.timezone})"
        )


@admin.register(Cohort)
class CohortAdmin(admin.ModelAdmin):
    """Cohorts are opened here or through the lead-only API.

    ``students`` is editable here as an escape hatch, but note that seating a
    student from this page creates *membership only* — no seat ``Booking``, so no
    video room and nothing for the student to attend. Routing (or
    ``Cohort.add_student`` alongside a booking) is the complete path; the seat
    cap still holds either way, enforced by the m2m receiver in signals.py.
    """

    list_display = (
        "level",
        "teacher",
        "schedule_start_utc",
        "max_students",
        "seats_taken",
    )
    list_filter = ("level__track", "teacher")
    search_fields = ("teacher__username", "level__name")
    autocomplete_fields = ("teacher", "level")
    filter_horizontal = ("students",)
    readonly_fields = ("seats_taken", "seats_available")

    @admin.display(description="Seats taken")
    def seats_taken(self, obj):
        return obj.seats_taken if obj.pk else 0

    @admin.display(description="Seats available")
    def seats_available(self, obj):
        return obj.seats_available if obj.pk else "—"


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "teacher",
        "level",
        "start_time_utc",
        "duration_minutes",
        "status",
        "routed_reason",
    )
    list_filter = ("status", "routed_reason", "teacher", "level__track")
    search_fields = (
        "student__username",
        "student__email",
        "teacher__username",
        "video_room_name",
    )
    autocomplete_fields = ("student", "teacher", "level", "cohort")
    # The room name is generated once and never changes (the model enforces it),
    # so it is never typed in. The join URL is derived from it.
    readonly_fields = ("video_room_name", "video_join_url")

    @admin.display(description="Jitsi join URL")
    def video_join_url(self, obj):
        return obj.video_join_url if obj.pk else "—"


@admin.register(TeacherWaitlist)
class TeacherWaitlistAdmin(admin.ModelAdmin):
    """Where a lead adjusts ``priority`` — the only field meant to be set by hand.

    Entries are created by the routing endpoint, not here: being on a waitlist is
    the outcome of asking for a teacher who was full. Promotion has its own
    endpoint too, because it must go through ``Booking.save()`` for the teacher's
    lock — stamping ``fulfilled_booking`` on this page would record a fulfilment
    without creating the session that fulfils it, so the field is read-only.
    """

    list_display = (
        "student",
        "requested_teacher",
        "level",
        "requested_start_utc",
        "priority",
        "notified",
        "fulfilled_booking",
    )
    list_filter = ("notified", "requested_teacher", "level__track")
    search_fields = (
        "student__username",
        "student__email",
        "requested_teacher__username",
        "level__name",
    )
    autocomplete_fields = ("student", "requested_teacher", "level")
    readonly_fields = ("requested_at", "fulfilled_booking")
