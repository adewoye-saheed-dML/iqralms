"""Constants and configuration for platform subscription tiers (Phase 9)."""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.conf import settings

SUBSCRIPTION_TIERS: Dict[str, Dict[str, Any]] = {
    "starter": {
        "tier": "starter",
        "name": "Starter",
        "student_cap": 25,
        "price_ngn": 15000,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_STARTER_PLAN_CODE",
        "default_plan_code": "PLN_starter_tier",
    },
    "growth": {
        "tier": "growth",
        "name": "Growth",
        "student_cap": 100,
        "price_ngn": 35000,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_GROWTH_PLAN_CODE",
        "default_plan_code": "PLN_growth_tier",
    },
    "professional": {
        "tier": "professional",
        "name": "Professional",
        "student_cap": 250,
        "price_ngn": 75000,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_PROFESSIONAL_PLAN_CODE",
        "default_plan_code": "PLN_professional_tier",
    },
    "institutional": {
        "tier": "institutional",
        "name": "Institutional",
        "student_cap": None,
        "price_ngn": None,
        "self_serve": False,
        "plan_code_setting": None,
        "default_plan_code": None,
    },
}


def get_tier_plan_code(tier: str) -> Optional[str]:
    """Retrieve the Paystack plan code for a given tier."""
    tier_info = SUBSCRIPTION_TIERS.get(tier)
    if not tier_info or not tier_info.get("self_serve"):
        return None
    setting_name = tier_info.get("plan_code_setting")
    default_code = tier_info.get("default_plan_code")
    if setting_name:
        return getattr(settings, setting_name, default_code)
    return default_code


def get_tiers_display() -> List[Dict[str, Any]]:
    """Return all four tiers formatted for the public /api/billing/tiers/ endpoint.
    
    Computes price_usd_display server-side from settings.USD_DISPLAY_RATE.
    """
    usd_rate = getattr(settings, "USD_DISPLAY_RATE", Decimal("750.00"))
    result = []
    for tier_key, data in SUBSCRIPTION_TIERS.items():
        price_ngn = data["price_ngn"]
        if price_ngn is not None and usd_rate and usd_rate > 0:
            price_usd_display = int(round(Decimal(price_ngn) / Decimal(usd_rate)))
        else:
            price_usd_display = None

        result.append({
            "tier": data["tier"],
            "student_cap": data["student_cap"],
            "price_ngn": price_ngn,
            "price_usd_display": price_usd_display,
            "self_serve": data["self_serve"],
        })
    return result
