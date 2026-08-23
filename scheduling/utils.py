"""Weekly-recurring time helpers.

``Availability`` is a weekly rule — a weekday plus a start and end *time*, both
UTC per CLAUDE.md. A bare time carries no date, so converting one between zones
needs a reference date; these helpers use the next occurrence of the weekday.

Two consequences of that shape, both deliberate and both recorded in
learnings.md:

* A weekly rule cannot be exactly right across a DST boundary — the same local
  09:00 is a different UTC time in winter and summer. The reference date makes
  the conversion correct *now*, which is the best a weekday+time model can do.
* Converting a local window to UTC can move it onto a different weekday, and
  can split it across two UTC days (a Lagos 00:30-02:30 Monday window is
  Sunday 23:30-00:00 plus Monday 00:00-01:30 in UTC). ``local_window_to_utc``
  therefore returns a *list* of segments, not one window.
"""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone as dj_timezone

DAYS_IN_WEEK = 7

UTC = ZoneInfo("UTC")

#: A segment that runs to the end of its UTC day stops here rather than at
#: 00:00 of the next one, so that ``end_time > start_time`` always holds.
END_OF_DAY = time.max


def next_date_for_weekday(weekday: int, on_or_after=None):
    """The first date on or after ``on_or_after`` falling on ``weekday``.

    ``weekday`` is Monday=0 .. Sunday=6, matching ``date.weekday()``. Defaults
    to today in UTC, which is the storage zone.
    """
    start = on_or_after or dj_timezone.now().astimezone(UTC).date()
    return start + timedelta(days=(weekday - start.weekday()) % DAYS_IN_WEEK)


def split_utc_interval(start: datetime, end: datetime):
    """Break an aware interval into one ``(weekday, start_time, end_time)`` per UTC day.

    Yields nothing for an empty or backwards interval.
    """
    cursor = start.astimezone(UTC)
    end = end.astimezone(UTC)
    while cursor < end:
        midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=UTC
        )
        stop = min(midnight, end)
        yield (
            cursor.weekday(),
            cursor.time(),
            END_OF_DAY if stop == midnight else stop.time(),
        )
        cursor = midnight


def local_window_to_utc(weekday: int, start_local: time, end_local: time, tz_name: str):
    """Convert one weekly local window into the UTC segments that represent it.

    This is the "point of entry" conversion CLAUDE.md calls for: a teacher
    states a window in their own zone and it is stored in UTC, never local. An
    ``end_local`` at or before ``start_local`` is read as running past local
    midnight (22:00-01:00), not as an error.
    """
    zone = ZoneInfo(tz_name)
    reference = next_date_for_weekday(weekday)
    start = datetime.combine(reference, start_local, tzinfo=zone)
    end = datetime.combine(reference, end_local, tzinfo=zone)
    if end <= start:
        end += timedelta(days=1)
    return list(split_utc_interval(start, end))


def utc_time_to_local(weekday: int, value: time, tz_name: str):
    """Render a stored UTC weekday+time in ``tz_name`` as ``(weekday, time)``.

    Display-layer only. A bad stored timezone returns the value untouched
    rather than turning a listing into a 500, matching
    ``accounts.utils.to_user_timezone``.
    """
    if value is None:
        return (weekday, None)
    try:
        zone = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return (weekday, value)
    moment = datetime.combine(
        next_date_for_weekday(weekday), value, tzinfo=UTC
    ).astimezone(zone)
    return (moment.weekday(), moment.time())
