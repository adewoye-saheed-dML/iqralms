"""Field-level validators for the accounts app."""

from functools import lru_cache
from zoneinfo import available_timezones

from django.core.exceptions import ValidationError


@lru_cache(maxsize=1)
def _known_timezones() -> frozenset:
    # available_timezones() walks the tz database; cache it for the process.
    return frozenset(available_timezones())


def validate_iana_timezone(value):
    """Require a real IANA zone name, e.g. 'Africa/Lagos'.

    Students and teachers span timezones, so a typo here silently misplaces
    every future session. Fail at write time instead.
    """
    if value not in _known_timezones():
        raise ValidationError(
            "%(value)s is not a valid IANA timezone name (e.g. 'Africa/Lagos').",
            code="invalid_timezone",
            params={"value": value},
        )
