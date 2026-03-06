"""Minimal FastAPI seller with Nevermined x402 payment integration.

This is a smoke test harness to validate Nevermined plumbing before Slice 6.
Uses manual verify + settle pattern from Nevermined docs.
"""
import os
import json
import base64
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from payments_py import Payments, PaymentOptions
from payments_py.plans import get_erc20_price_config, get_fixed_credits_config
from payments_py.x402.helpers import build_payment_required

from src.config import NVM_API_KEY, NVM_ENVIRONMENT, NVM_AGENT_ID, NVM_PLAN_ID, OUR_HOST
from src.smoke.pricing import PRICING

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# USDC on Base Sepolia (sandbox)
USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

# Global variables for agent/plan IDs and payments client
AGENT_ID = NVM_AGENT_ID
PLAN_ID = NVM_PLAN_ID
payments = None

app = FastAPI()


def get_payments():
    """Lazy initialize Payments client."""
    global payments
    if payments is None:
        payments = Payments(
            PaymentOptions(
                nvm_api_key=NVM_API_KEY,
                environment=NVM_ENVIRONMENT,
            )
        )
    return payments


def register_if_needed():
    """Register agent and plan if not already configured."""
    global AGENT_ID, PLAN_ID

    if AGENT_ID and PLAN_ID:
        logger.info(f"Using existing agent: {AGENT_ID}, plan: {PLAN_ID}")
        return

    logger.info("Registering smoke test agent and plan with Nevermined...")

    payments = get_payments()
    result = payments.agents.register_agent_and_plan(
        agent_metadata={
            "name": "Smoke Test Seller",
            "description": "Temporary smoke test for x402 payment validation",
            "tags": ["test", "smoke", "consulting"],
        },
        agent_api={
            "endpoints": [{"POST": f"{OUR_HOST}/data"}],
            "agentDefinitionUrl": f"{OUR_HOST}/openapi.json",
        },
        plan_metadata={
            "name": "Smoke Test Plan",
            "description": "100 credits for testing",
        },
        price_config=get_erc20_price_config(
            10_000_000,  # 10 USDC (6 decimals)
            USDC_ADDRESS,
            payments.account_address,
        ),
        credits_config=get_fixed_credits_config(100, 1),  # 100 credits, 1 per request
        access_limit="credits",
    )

    AGENT_ID = result["agentId"]
    PLAN_ID = result["planId"]

    logger.info(f"✅ Registered agent: {AGENT_ID}")
    logger.info(f"✅ Registered plan: {PLAN_ID}")
    logger.info(f"💡 Save these to .env: NVM_AGENT_ID={AGENT_ID} NVM_PLAN_ID={PLAN_ID}")


def process_consulting_query(query: str) -> dict:
    """Process a consulting query - hardcoded response for smoke test."""
    logger.info(f"💰 Processing paid query: {query}")

    return {
        "status": "success",
        "query": query,
        "advice": "This is a smoke test response. In production, this would be real consulting advice.",
        "confidence": 0.95,
    }


class DataRequest(BaseModel):
    query: str


@app.on_event("startup")
async def startup():
    """Register agent/plan on startup if needed."""
    register_if_needed()


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "smoke-seller"}


@app.get("/pricing")
async def pricing():
    """Return pricing information."""
    return {
        "planId": PLAN_ID,
        "agentId": AGENT_ID,
        "tiers": PRICING,
    }


@app.post("/data")
async def data(request: Request, body: DataRequest):
    """Main data endpoint - requires x402 payment."""
    payments = get_payments()

    # Build payment required specification
    payment_required = build_payment_required(
        plan_id=PLAN_ID,
        endpoint=str(request.url),
        agent_id=AGENT_ID,
        http_verb=request.method,
    )

    # Get token from payment-signature header
    x402_token = request.headers.get("payment-signature")

    if not x402_token:
        # Return 402 with payment-required header
        pr_base64 = base64.b64encode(
            payment_required.model_dump_json(by_alias=True).encode()
        ).decode()

        return JSONResponse(
            status_code=402,
            content={"error": "Payment Required"},
            headers={"payment-required": pr_base64},
        )

    # 1. Verify permissions (does NOT burn credits)
    verification = payments.facilitator.verify_permissions(
        payment_required=payment_required,
        x402_access_token=x402_token,
        max_amount="1",
    )

    if not verification.is_valid:
        return JSONResponse(
            status_code=402,
            content={"error": verification.invalid_reason},
        )

    # 2. Execute business logic - payment is verified
    result = process_consulting_query(body.query)

    # 3. Settle (burn credits) after successful processing
    settlement = payments.facilitator.settle_permissions(
        payment_required=payment_required,
        x402_access_token=x402_token,
        max_amount="1",
    )

    credits_redeemed = int(settlement.credits_redeemed) if hasattr(settlement, "credits_redeemed") else 1
    logger.info(f"✅ Payment settled: {credits_redeemed} credits redeemed")

    return {
        "result": result,
        "credits_used": credits_redeemed,
    }


def main():
    """Entry point for smoke-seller script."""
    import uvicorn

    logger.info("🚀 Starting smoke test seller on http://localhost:3000")
    uvicorn.run(app, host="0.0.0.0", port=3000)


if __name__ == "__main__":
    main()
