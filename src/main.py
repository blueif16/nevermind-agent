"""FastAPI server for portfolio manager agent with consulting endpoints."""
import asyncio
import base64
import json
import logging

from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from strands.models import BedrockModel

from payments_py import Payments, PaymentOptions
from payments_py.common.types import PlanMetadata
from payments_py.x402.strands import extract_payment_required
from payments_py.plans import (
    get_erc20_price_config,
    get_fixed_credits_config,
    get_fiat_price_config,
)

from src import config
from src.central_sheet import CentralSheet
from src.scanner import scan_loop
from src.probe_runner import run_probe
from src.evaluation import pipeline
from src.evaluators.gate import gate_evaluator
from src.consulting_agent import create_consulting_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Shared resources ────────────────────────────────────────

sheet = CentralSheet("portfolio.db")

payments = Payments.get_instance(
    PaymentOptions(
        nvm_api_key=config.NVM_API_KEY,
        environment=config.NVM_ENVIRONMENT,
    )
)

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    streaming=True,
)

# ── Agent registration ──────────────────────────────────────

USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia
OUR_HOST = config.OUR_HOST

# Registration outputs — set via env or auto-registered at startup
OUR_AGENT_ID = config.NVM_AGENT_ID or ""
OUR_PLAN_ID_USDC = getattr(config, "NVM_PLAN_ID_USDC", "") or ""
OUR_PLAN_ID_FIAT = getattr(config, "NVM_PLAN_ID_FIAT", "") or ""
OUR_PLAN_ID = config.NVM_PLAN_ID or ""


def register_if_needed():
    """Register our agent + DUAL payment plans (USDC + Fiat) with Nevermined.

    Hackathon tip: create both rails so every buyer can pay,
    whether they have crypto wallets or just credit cards.
    Stripe test card: 4242 4242 4242 4242, any future expiry, any CVC.
    """
    global OUR_AGENT_ID, OUR_PLAN_ID, OUR_PLAN_ID_USDC, OUR_PLAN_ID_FIAT
    if OUR_AGENT_ID and OUR_PLAN_ID:
        logger.info(f"Using existing agent: {OUR_AGENT_ID}, plan: {OUR_PLAN_ID}")
        return

    logger.info("Registering agent and DUAL payment plans with Nevermined...")

    # ── 1. Register agent + USDC plan (crypto rail) ─────────
    result = payments.agents.register_agent_and_plan(
        agent_metadata={
            "name": "Portfolio Manager — Agent Rating & Consulting",
            "description": (
                "Evaluates every agent in the marketplace. "
                "Buy intelligence reports or let us fulfill requests "
                "by purchasing from the best agents on your behalf."
            ),
            "tags": ["consulting", "ratings", "portfolio", "meta-agent"],
            "category": "consulting",
            "services_offered": ["marketplace intelligence", "agent evaluation", "data fulfillment"],
            "services_per_request": 1,
        },
        agent_api={
            "endpoints": [{"POST": f"{OUR_HOST}/data"}],
            "agentDefinitionUrl": f"{OUR_HOST}/openapi.json",
        },
        plan_metadata={
            "name": "Consulting Credits (USDC)",
            "description": "1 credit per consulting query — pay with USDC",
        },
        price_config=get_erc20_price_config(
            10_000_000,  # 10 USDC (6 decimals)
            USDC_ADDRESS,
            payments.account_address,
        ),
        credits_config=get_fixed_credits_config(100, 1),  # 100 credits, 1 per request
        access_limit="credits",
    )
    OUR_AGENT_ID = result["agentId"]
    OUR_PLAN_ID_USDC = result["planId"]
    OUR_PLAN_ID = OUR_PLAN_ID_USDC  # Primary plan for @requires_payment
    logger.info(f"Registered USDC plan: agent={OUR_AGENT_ID} plan={OUR_PLAN_ID_USDC}")

    # ── 2. Register fiat/Stripe plan (credit card rail) ─────
    try:
        fiat_plan = payments.plans.register_credits_plan(
            plan_metadata=PlanMetadata(
                name="Consulting Credits (Card)",
                description="1 credit per consulting query — pay with credit card",
            ),
            price_config=get_fiat_price_config(
                999,  # $9.99 in cents
                payments.account_address,
            ),
            credits_config=get_fixed_credits_config(100, 1),
        )
        OUR_PLAN_ID_FIAT = fiat_plan.get("planId", "")
        logger.info(f"Registered fiat plan: {OUR_PLAN_ID_FIAT}")
    except Exception as e:
        logger.warning(f"Fiat plan registration failed (non-fatal): {e}")
        # USDC plan still works — fiat is a bonus


# ── Evaluation pipeline setup ───────────────────────────────

pipeline.register("gate", gate_evaluator)


async def eval_callback(agent_id: str, sheet_instance: CentralSheet):
    """Triggered after probes complete. Runs all registered evaluators."""
    await pipeline.run(agent_id, sheet_instance)


# ── Consulting agent ────────────────────────────────────────

# Serialize concurrent requests (Strands Agent is not thread-safe)
agent_lock = asyncio.Lock()
consulting_agent = None  # Created after registration


# ── FastAPI app ─────────────────────────────────────────────

app = FastAPI(title="Portfolio Manager Agent")


class DataRequest(BaseModel):
    query: str


@app.on_event("startup")
async def startup():
    register_if_needed()

    # Create consulting agent (needs plan_id from registration)
    global consulting_agent
    consulting_agent = create_consulting_agent(
        model=model,
        sheet=sheet,
        payments=payments,
        plan_id=OUR_PLAN_ID,
        agent_id=OUR_AGENT_ID,
    )

    # Start scanner background task
    asyncio.create_task(
        scan_loop(
            sheet=sheet,
            payments=payments,
            probe_callback=lambda a, s, p: run_probe(
                a, s, p, eval_callback=eval_callback
            ),
            interval=config.SCAN_INTERVAL,
            nvm_api_key=config.NVM_API_KEY,
        )
    )
    logger.info(f"Scanner started (interval={config.SCAN_INTERVAL}s)")


@app.post("/data")
async def data_endpoint(request: Request, body: DataRequest):
    """Client-facing consulting endpoint. x402 payment required.

    Pattern matches seller-simple-agent/src/agent.py exactly:
    1. Extract payment-signature header
    2. Pass as invocation_state
    3. Run agent
    4. Check for payment_required / settlement
    """
    try:
        payment_token = request.headers.get("payment-signature", "")
        state = {"payment_token": payment_token} if payment_token else {}

        async with agent_lock:
            result = await asyncio.to_thread(
                consulting_agent, body.query, invocation_state=state
            )

        # Check if payment was required but not fulfilled
        payment_required = extract_payment_required(consulting_agent.messages)
        if payment_required and not state.get("payment_settlement"):
            encoded = base64.b64encode(
                json.dumps(payment_required).encode()
            ).decode()
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment Required",
                    "message": str(result),
                },
                headers={"payment-required": encoded},
            )

        # Success — record revenue
        settlement = state.get("payment_settlement")
        credits = int(settlement.credits_redeemed) if settlement else 0
        if credits > 0:
            sheet.write_ledger(
                direction="in",
                credits=credits,
                purpose="consulting_revenue",
                detail=body.query[:100],
            )

        return JSONResponse(content={
            "response": str(result),
            "credits_used": credits,
        })

    except Exception as e:
        logger.error(f"Error in /data: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


@app.get("/pricing")
async def pricing():
    """Pricing info (unprotected). Exposes BOTH payment rails."""
    plans = {"usdc": OUR_PLAN_ID_USDC}
    if OUR_PLAN_ID_FIAT:
        plans["fiat"] = OUR_PLAN_ID_FIAT
    return JSONResponse(content={
        "planId": OUR_PLAN_ID,  # Primary plan (for x402 buyers)
        "agentId": OUR_AGENT_ID,
        "plans": plans,  # Both rails
        "tiers": {
            "consulting": {
                "credits": 1,
                "description": "Intelligence query or upstream fulfillment",
            },
        },
        "payment_rails": {
            "usdc": "Pay with USDC on Base Sepolia",
            "fiat": "Pay with credit card via Stripe (test: 4242 4242 4242 4242)",
        },
    })


@app.get("/portfolio")
async def portfolio_view():
    """Public dashboard — ranked agents, evaluation data, P&L."""
    return JSONResponse(content={
        "portfolio": sheet.read_portfolio(),
        "pnl": sheet.get_pnl(),
        "evaluators": pipeline.evaluator_names,
    })


@app.get("/health")
async def health():
    agents = sheet.read_agents()
    return JSONResponse(content={
        "status": "ok",
        "agents_tracked": len(agents),
        "agents_evaluated": len([a for a in agents if a["status"] == "evaluated"]),
        "total_probes": len(sheet.read_probes(limit=99999)),
        "pnl": sheet.get_pnl(),
    })


def main():
    logger.info(f"Portfolio Manager on http://0.0.0.0:{config.PORT}")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)


if __name__ == "__main__":
    main()
