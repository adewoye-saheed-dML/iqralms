"""Paystack API client and signature verification for Phase 9.

Supports:
- HMAC-SHA512 webhook signature verification.
- Subaccount onboarding (resolve account number, create subaccount with 0% platform fee).
- Family tuition transaction initialization and verification.
- Platform subscription operations (customer, subscription).
Built on Python standard library urllib to avoid unlisted dependencies.
"""

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

PAYSTACK_API_BASE = "https://api.paystack.co"


class PaystackAPIError(Exception):
    """Raised when a Paystack API call fails."""

    def __init__(self, message: str, status_code: int = 400, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.data = data or {}


def verify_paystack_signature(raw_body: bytes, signature_header: Optional[str], secret_key: Optional[str] = None) -> bool:
    """Verify Paystack's HMAC-SHA512 webhook signature.

    Returns True if valid, False otherwise.
    """
    if not signature_header:
        return False
    key = (secret_key or getattr(settings, "PAYSTACK_SECRET_KEY", "")).strip().encode("utf-8")
    if not key:
        logger.error("PAYSTACK_SECRET_KEY is not configured.")
        return False
    expected = hmac.new(key, raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature_header.strip())


class PaystackClient:
    """HTTP client for interacting with Paystack's REST API using urllib."""

    def __init__(self, secret_key: Optional[str] = None, timeout: int = 15):
        self.secret_key = (secret_key or getattr(settings, "PAYSTACK_SECRET_KEY", "")).strip()
        self.timeout = timeout

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "IqraLMS/1.0",
        }

    def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{PAYSTACK_API_BASE.rstrip('/')}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"

        data_bytes = None
        if json_data is not None:
            data_bytes = json.dumps(json_data).encode("utf-8")

        req = urllib.request.Request(
            url=url,
            data=data_bytes,
            headers=self.headers,
            method=method.upper(),
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                status_code = resp.status
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            try:
                resp_text = exc.read().decode("utf-8")
            except Exception:
                resp_text = str(exc)
        except urllib.error.URLError as exc:
            logger.error("Paystack network request failed for %s %s: %s", method, path, exc)
            raise PaystackAPIError(f"Paystack network error: {exc}", status_code=502) from exc

        try:
            payload = json.loads(resp_text)
        except ValueError:
            payload = {"status": False, "message": resp_text}

        if status_code >= 400 or not payload.get("status"):
            error_message = payload.get("message", f"HTTP {status_code}")
            logger.warning("Paystack API call failed (%s %s): %s", method, path, error_message)
            raise PaystackAPIError(error_message, status_code=status_code, data=payload)

        return payload

    # --- Subaccount endpoints (§9b onboarding) -------------------------------

    def resolve_account_number(self, account_number: str, bank_code: str) -> Dict[str, Any]:
        """Resolve account number and bank code to verify account name."""
        res = self._request("GET", "bank/resolve", params={"account_number": account_number, "bank_code": bank_code})
        return res.get("data", {})

    def create_subaccount(
        self,
        business_name: str,
        settlement_bank: str,
        account_number: str,
        percentage_charge: float = 0.0,
        description: str = "",
    ) -> Dict[str, Any]:
        """Create a Paystack Subaccount for an academy.

        percentage_charge is 0.0 per Decision D-011 (IqraLMS takes 0% cut).
        """
        body = {
            "business_name": business_name,
            "settlement_bank": settlement_bank,
            "account_number": account_number,
            "percentage_charge": percentage_charge,
            "description": description or f"Subaccount for {business_name}",
        }
        res = self._request("POST", "subaccount", json_data=body)
        return res.get("data", {})

    # --- Tuition payment endpoints (§9b) ------------------------------------

    def initialize_transaction(
        self,
        email: str,
        amount_kobo: int,
        reference: str,
        subaccount: Optional[str] = None,
        bearer: str = "subaccount",
        callback_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Initialize a tuition payment transaction."""
        body: Dict[str, Any] = {
            "email": email,
            "amount": amount_kobo,
            "reference": reference,
            "currency": "NGN",
        }
        if subaccount:
            body["subaccount"] = subaccount
            body["bearer"] = bearer
        if callback_url:
            body["callback_url"] = callback_url
        if metadata:
            body["metadata"] = metadata

        res = self._request("POST", "transaction/initialize", json_data=body)
        return res.get("data", {})

    def verify_transaction(self, reference: str) -> Dict[str, Any]:
        """Verify the status of a transaction on Paystack."""
        res = self._request("GET", f"transaction/verify/{reference}")
        return res.get("data", {})

    # --- Subscription endpoints (§9a owner billing) --------------------------

    def create_customer(self, email: str, first_name: str = "", last_name: str = "") -> Dict[str, Any]:
        """Create or fetch customer on Paystack."""
        body = {"email": email, "first_name": first_name, "last_name": last_name}
        res = self._request("POST", "customer", json_data=body)
        return res.get("data", {})

    def create_subscription(
        self,
        customer_code_or_email: str,
        plan_code: str,
        start_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a recurring subscription for an academy owner."""
        body: Dict[str, Any] = {
            "customer": customer_code_or_email,
            "plan": plan_code,
        }
        if start_date:
            body["start_date"] = start_date
        res = self._request("POST", "subscription", json_data=body)
        return res.get("data", {})

    def disable_subscription(self, code: str, token: str) -> Dict[str, Any]:
        """Disable a subscription."""
        body = {"code": code, "token": token}
        res = self._request("POST", "subscription/disable", json_data=body)
        return res.get("data", {})
