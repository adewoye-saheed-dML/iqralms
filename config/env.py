"""Typed environment reading for ``config/settings.py``.

Small on purpose. The Phase 6 spec asks for environment parsing to be explicit
and typed where values are not strings, and for the settings module to stay
simple rather than grow a configuration framework — so this is four functions
and no classes, registry, or schema.

The rule every one of them follows: a variable that is *set but unparseable* is
an error, not a silent fall back to the default. ``DJANGO_DEBUG=flase`` must not
quietly mean False, because the whole point of Phase 6 is that a deployment
cannot get a development default by accident. An *unset* variable takes the
default, which is what makes a fresh clone runnable.
"""

import os

from django.core.exceptions import ImproperlyConfigured

#: Accepted spellings for a boolean. Deliberately closed: anything else raises,
#: rather than being treated as false because it is not in the true list.
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def env_str(name, *, default):
    """The raw value, stripped, or ``default`` when unset or empty."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_bool(name, *, default):
    """A real boolean. A set-but-unrecognised value raises."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ImproperlyConfigured(
        f"{name}={raw!r} is not a boolean. Use one of "
        f"{sorted(_TRUE)} or {sorted(_FALSE)}. It is not treated as false, "
        "because a typo in a security setting must fail loudly rather than "
        "quietly choose the permissive value."
    )


def env_int(name, *, default):
    """An integer. A set-but-non-numeric value raises."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ImproperlyConfigured(f"{name}={raw!r} is not an integer.") from exc


def env_list(name, *, default):
    """A comma-separated list, with blanks dropped.

    ``"a, ,b"`` is ``["a", "b"]``; an unset or all-blank value is ``default``.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    items = [item.strip() for item in raw.split(",")]
    items = [item for item in items if item]
    return items if items else default
