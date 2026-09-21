"""Small helpers for the accounts app."""

import secrets
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Unambiguous alphabet: no 0/O or 1/I, because signup codes get read aloud
# over the phone and typed by parents.
SIGNUP_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SIGNUP_CODE_LENGTH = 8


def generate_signup_code(length: int = SIGNUP_CODE_LENGTH) -> str:
    return "".join(secrets.choice(SIGNUP_CODE_ALPHABET) for _ in range(length))


def generate_unique_signup_code(model, *, attempts: int = 10) -> str:
    """Generate a code not already used by ``model``.

    ``model`` is passed in rather than imported to keep utils import-free of
    models. Collisions are vanishingly unlikely (32**8 space) but a unique
    column deserves a real check rather than optimism.
    """
    for _ in range(attempts):
        code = generate_signup_code()
        if not model.objects.filter(signup_code=code).exists():
            return code
    raise RuntimeError("Could not generate a unique signup code")


def normalize_signup_code(raw) -> str:
    """Canonicalise user-entered codes: '7qk4-m2xr' -> '7QK4M2XR'.

    Parents will type the code with dashes, spaces or lowercase; we store and
    compare a single canonical form.
    """
    if raw is None:
        return ""
    return "".join(ch for ch in str(raw).upper() if ch in SIGNUP_CODE_ALPHABET)


def to_user_timezone(dt, tz_name):
    """Render an aware UTC datetime in the user's stored IANA timezone.

    Storage stays UTC everywhere (see CLAUDE.md); this is the display-layer
    conversion later phases will reuse for session times.
    """
    if dt is None:
        return None
    try:
        return dt.astimezone(ZoneInfo(tz_name))
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        # A bad stored timezone should not turn a profile read into a 500.
        return dt


def generate_unique_username_from_email(email: str, user_model=None) -> str:
    """Generate a deterministic, unique username from an email's local part.

    Example:
    'amina.yusuf@example.com' -> 'amina.yusuf' (or 'amina.yusuf2', etc.)
    """
    import re

    if user_model is None:
        from accounts.models import User
        user_model = User

    local_part = email.split("@")[0].lower() if email else ""
    # Strip characters disallowed by Django's username validator (keep alphanum, @/./+/-/_)
    base = re.sub(r"[^\w.@+-]", "", local_part)
    base = base.strip(".-_")
    if not base:
        base = "user"
    base = base[:140]

    candidate = base
    counter = 2
    while user_model.objects.filter(username__iexact=candidate).exists():
        candidate = f"{base}{counter}"
        counter += 1

    return candidate
