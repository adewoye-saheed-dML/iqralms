"""Geo-location detection and currency resolution for platform billing."""

from typing import Optional
from organizations.models import Organization


def get_client_country(request, organization: Optional[Organization] = None) -> str:
    """Determine the visitor's ISO 3166-1 alpha-2 country code.
    
    Resolution order:
    1. Edge CDN headers: Cloudflare (CF-IPCountry), Vercel (X-Vercel-IP-Country).
    2. Explicit 'X-Country-Code' or 'X-Country' header.
    3. Explicit 'country' query parameter or request body parameter (useful for testing/staging).
    4. Browser timezone passed by query parameter or request body ('tz' / 'timezone').
    5. Organization location/timezone/currency heuristic (e.g. 'Africa/Lagos' -> 'NG', 'NGN' -> 'NG').
    6. Authenticated user timezone heuristic (e.g. owner registered with 'Africa/Lagos').
    7. Fallback: 'GLOBAL' (treated as international/USD).
    """
    meta = getattr(request, "META", {}) or {}

    # 1. Edge CDN headers
    for header in (
        "HTTP_CF_IPCOUNTRY",
        "HTTP_X_VERCEL_IP_COUNTRY",
        "HTTP_X_COUNTRY_CODE",
        "HTTP_X_COUNTRY",
    ):
        val = meta.get(header)
        if val and isinstance(val, str) and len(val.strip()) == 2:
            return val.strip().upper()

    # 2. Query param or request body (dev/test) — explicit country code
    country_param = None
    if hasattr(request, "query_params"):
        country_param = request.query_params.get("country")
    if not country_param and hasattr(request, "data") and isinstance(request.data, dict):
        country_param = request.data.get("country")

    if country_param and isinstance(country_param, str) and len(country_param.strip()) == 2:
        return country_param.strip().upper()

    # 3. Browser timezone passed by query param or request body (reliable client-side signal)
    tz_param = ""
    if hasattr(request, "query_params"):
        tz_param = (request.query_params.get("tz") or request.query_params.get("timezone") or "").lower().strip()
    if not tz_param and hasattr(request, "data") and isinstance(request.data, dict):
        tz_param = str(request.data.get("tz") or request.data.get("timezone") or "").lower().strip()

    nigerian_timezones = (
        "africa/lagos",
        "lagos",
        "wat",
        "west africa time",
        "africa/porto-novo",
        "africa/niamey",
        "africa/douala",
        "africa/malabo",
        "africa/bangui",
        "africa/ndjamena",
    )

    if tz_param:
        if tz_param in nigerian_timezones:
            return "NG"
        # Explicit foreign browser timezone passed (e.g. America/New_York)
        if tz_param.startswith(("america/", "europe/", "asia/", "australia/")) or tz_param in ("us", "uk"):
            return "GLOBAL"

    # 4. Organization context heuristic
    if organization is not None:
        tz = (getattr(organization, "timezone", "") or "").lower().strip()
        if tz in nigerian_timezones:
            return "NG"
        if getattr(organization, "currency", "").upper() == "NGN":
            return "NG"

    # 5. Authenticated user context heuristic
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        user_tz = (getattr(user, "timezone", "") or "").lower().strip()
        if user_tz in nigerian_timezones:
            return "NG"

    return "GLOBAL"


def is_nigerian_client(request, organization: Optional[Organization] = None) -> bool:
    """Return True only if the client is verified to be within Nigeria."""
    return get_client_country(request, organization) == "NG"


def get_applicable_currency(request, organization: Optional[Organization] = None) -> str:
    """Return 'NGN' for verified Nigerian requests, 'USD' for all international requests."""
    return "NGN" if is_nigerian_client(request, organization) else "USD"
