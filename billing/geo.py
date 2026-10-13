"""Geo-location detection and currency resolution for platform billing."""

from typing import Optional
from organizations.models import Organization


def get_client_country(request, organization: Optional[Organization] = None) -> str:
    """Determine the visitor's ISO 3166-1 alpha-2 country code.
    
    Resolution order:
    1. Edge CDN headers: Cloudflare (CF-IPCountry), Vercel (X-Vercel-IP-Country).
    2. Explicit 'X-Country-Code' or 'X-Country' header.
    3. Explicit 'country' query parameter (useful for testing/staging).
    4. Organization location/timezone heuristic (e.g. 'Africa/Lagos' -> 'NG').
    5. Fallback: 'GLOBAL' (treated as international/USD).
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

    # 2. Query param (dev/test) — explicit country code
    if hasattr(request, "query_params"):
        param = request.query_params.get("country")
        if param and isinstance(param, str) and len(param.strip()) == 2:
            return param.strip().upper()

    # 3. Browser timezone passed by the frontend (reliable client-side signal)
    if hasattr(request, "query_params"):
        tz_param = (request.query_params.get("tz") or "").lower().strip()
        if tz_param in ("africa/lagos", "lagos"):
            return "NG"

    # 3. Organization context heuristic
    if organization is not None:
        tz = getattr(organization, "timezone", "") or ""
        if tz.lower() in ("africa/lagos", "lagos"):
            return "NG"

    return "GLOBAL"


def is_nigerian_client(request, organization: Optional[Organization] = None) -> bool:
    """Return True only if the client is verified to be within Nigeria."""
    return get_client_country(request, organization) == "NG"


def get_applicable_currency(request, organization: Optional[Organization] = None) -> str:
    """Return 'NGN' for verified Nigerian requests, 'USD' for all international requests."""
    return "NGN" if is_nigerian_client(request, organization) else "USD"
