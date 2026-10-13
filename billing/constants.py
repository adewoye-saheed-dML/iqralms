"""Constants and configuration for platform subscription tiers (Phase 9)."""

import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.conf import settings

SUBSCRIPTION_TIERS: Dict[str, Dict[str, Any]] = {
    "starter": {
        "tier": "starter",
        "name": "Starter",
        "student_cap": 25,
        "price_ngn": 15000,
        "price_usd": 20,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_STARTER_PLAN_CODE",
        "default_plan_code": "PLN_starter_tier",
        "plan_code_usd_setting": "PAYSTACK_STARTER_PLAN_CODE_USD",
        "default_plan_code_usd": "PLN_starter_tier_usd",
    },
    "growth": {
        "tier": "growth",
        "name": "Growth",
        "student_cap": 100,
        "price_ngn": 35000,
        "price_usd": 50,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_GROWTH_PLAN_CODE",
        "default_plan_code": "PLN_growth_tier",
        "plan_code_usd_setting": "PAYSTACK_GROWTH_PLAN_CODE_USD",
        "default_plan_code_usd": "PLN_growth_tier_usd",
    },
    "professional": {
        "tier": "professional",
        "name": "Professional",
        "student_cap": 250,
        "price_ngn": 75000,
        "price_usd": 100,
        "self_serve": True,
        "plan_code_setting": "PAYSTACK_PROFESSIONAL_PLAN_CODE",
        "default_plan_code": "PLN_professional_tier",
        "plan_code_usd_setting": "PAYSTACK_PROFESSIONAL_PLAN_CODE_USD",
        "default_plan_code_usd": "PLN_professional_tier_usd",
    },
    "institutional": {
        "tier": "institutional",
        "name": "Institutional",
        "student_cap": None,
        "price_ngn": None,
        "price_usd": None,
        "self_serve": False,
        "plan_code_setting": None,
        "default_plan_code": None,
        "plan_code_usd_setting": None,
        "default_plan_code_usd": None,
    },
}


def get_tier_plan_code(tier: str, currency: str = "NGN") -> Optional[str]:
    """Retrieve the Paystack plan code for a given tier and currency (NGN or USD).

    In production (when not in DEBUG/TEST), ensures a real configured plan code is provided
    and fails if default placeholders like 'PLN_*_tier' are detected.
    """
    tier_info = SUBSCRIPTION_TIERS.get(tier)
    if not tier_info or not tier_info.get("self_serve"):
        return None

    is_usd = str(currency).upper() == "USD"
    if is_usd:
        setting_name = tier_info.get("plan_code_usd_setting")
        default_code = tier_info.get("default_plan_code_usd")
    else:
        setting_name = tier_info.get("plan_code_setting")
        default_code = tier_info.get("default_plan_code")

    code = getattr(settings, setting_name, default_code) if setting_name else default_code

    is_production = getattr(settings, "PRODUCTION", False)
    if is_production and "pytest" not in sys.modules:
        if not code or (isinstance(code, str) and code.startswith("PLN_") and code.endswith("_tier")):
            raise ValueError(
                f"Paystack plan code for tier '{tier}' in {currency} is not configured in production settings."
            )
    return code


def get_tiers_display(currency: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return all four tiers formatted for the /api/billing/tiers/ endpoint.
    
    Includes authoritative price_ngn and price_usd_display.
    """
    result = []
    for tier_key, data in SUBSCRIPTION_TIERS.items():
        price_ngn = data["price_ngn"]
        price_usd = data.get("price_usd")
        if price_usd is None and price_ngn is not None:
            usd_rate = getattr(settings, "USD_DISPLAY_RATE", Decimal("750.00"))
            if usd_rate and usd_rate > 0:
                price_usd = int(round(Decimal(price_ngn) / Decimal(usd_rate)))

        result.append({
            "tier": data["tier"],
            "student_cap": data["student_cap"],
            "price_ngn": price_ngn,
            "price_usd_display": price_usd,
            "self_serve": data["self_serve"],
        })
    return result
