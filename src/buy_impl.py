"""Buy implementation - framework-agnostic x402 payment functions."""
import base64
import json
from typing import Any

import httpx
from payments_py import Payments
from payments_py.x402.resolve_scheme import resolve_scheme
from payments_py.x402.types import CardDelegationConfig, X402TokenOptions


# Constants
_SPENDING_LIMIT_CENTS = 10_000  # $100
_DURATION_SECS = 604_800        # 7 days


def build_token_options(payments: Payments, plan_id: str) -> X402TokenOptions:
    """Resolve scheme and build X402TokenOptions."""
    scheme = resolve_scheme(payments, plan_id)
    if scheme != "nvm:card-delegation":
        return X402TokenOptions(scheme=scheme)

    methods = payments.delegation.list_payment_methods()
    if not methods:
        raise ValueError("Fiat plan requires payment method. Add at nevermined.app.")
    pm = methods[0]
    return X402TokenOptions(
        scheme=scheme,
        delegation_config=CardDelegationConfig(
            provider_payment_method_id=pm.id,
            spending_limit_cents=_SPENDING_LIMIT_CENTS,
            duration_secs=_DURATION_SECS,
            currency="usd",
        ),
    )


def _error(message: str) -> dict[str, Any]:
    """Return error response dict."""
    return {"status": "error", "content": [{"text": message}], "credits_used": 0}


def purchase_data_impl(
    payments: Payments,
    plan_id: str,
    seller_url: str,
    query: str,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Purchase data from a seller using x402 protocol.

    Returns dict with: status, content, response, credits_used
    """
    try:
        token_options = build_token_options(payments, plan_id)
        token_result = payments.x402.get_x402_access_token(
            plan_id=plan_id,
            agent_id=agent_id,
            token_options=token_options,
        )
        access_token = token_result.get("accessToken")
        if not access_token:
            return _error("Failed to generate x402 access token.")

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{seller_url}/data",
                headers={
                    "Content-Type": "application/json",
                    "payment-signature": access_token,
                },
                json={"query": query},
            )

        if response.status_code == 402:
            details = ""
            pr_header = response.headers.get("payment-required", "")
            if pr_header:
                try:
                    decoded = json.loads(base64.b64decode(pr_header).decode("utf-8"))
                    details = f"\nPayment details: {json.dumps(decoded, indent=2)}"
                except Exception:
                    pass
            return {
                "status": "payment_required",
                "content": [{"text": f"Payment required (HTTP 402).{details}"}],
                "credits_used": 0,
            }

        if response.status_code != 200:
            return _error(f"HTTP {response.status_code}: {response.text[:500]}")

        data = response.json()
        return {
            "status": "success",
            "content": [{"text": data.get("response", "")}],
            "response": data.get("response", ""),
            "credits_used": data.get("credits_used", 0),
        }

    except httpx.ConnectError:
        return _error(f"Cannot connect to {seller_url}.")
    except Exception as e:
        return _error(f"Purchase failed: {e}")


def check_balance_impl(payments: Payments, plan_id: str) -> dict[str, Any]:
    """Check credit balance for a plan."""
    try:
        result = payments.plans.get_plan_balance(plan_id)
        return {
            "status": "success",
            "balance": result.balance,
            "is_subscriber": result.is_subscriber,
        }
    except Exception as e:
        return {"status": "error", "balance": 0, "is_subscriber": False}


def discover_pricing_impl(seller_url: str) -> dict[str, Any]:
    """GET /pricing from seller."""
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{seller_url}/pricing")
        if response.status_code != 200:
            return {"status": "error", "tiers": {}}
        data = response.json()
        return {
            "status": "success",
            "plan_id": data.get("planId", ""),
            "tiers": data.get("tiers", {}),
        }
    except Exception:
        return {"status": "error", "tiers": {}}
