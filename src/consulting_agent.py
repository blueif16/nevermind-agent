"""Consulting Agent — Revenue-generating seller with payment-protected tools.

This agent sells consulting services via x402 payments. It can:
1. Provide marketplace intelligence (agent rankings, quality data)
2. Fulfill data requests by buying from top agents on the client's behalf
"""

import logging
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel
from payments_py import Payments
from payments_py.x402.strands import requires_payment

from .central_sheet import CentralSheet
from .buy_impl import purchase_data_impl

logger = logging.getLogger(__name__)


def create_consulting_agent(
    model: BedrockModel,
    sheet: CentralSheet,
    payments: Payments,
    plan_id: str,
    agent_id: str | None = None,
) -> Agent:
    """Create the consulting agent with payment-protected tools.

    Args:
        model: BedrockModel instance (Claude Sonnet 4.6)
        sheet: CentralSheet instance for portfolio access
        payments: Payments SDK instance (builder key for seller side)
        plan_id: Our registered plan ID (primary, typically USDC)
        agent_id: Our registered agent ID

    Returns:
        Strands Agent with consulting tools
    """

    # ── Internal tools (no payment needed) ──────────────────────

    @tool
    def read_portfolio() -> dict:
        """Read the current portfolio: ranked agents with quality data,
        cost info, and probe counts.

        Returns:
            dict with portfolio (list of agents), pnl (revenue/spent/margin),
            and agent_count
        """
        portfolio = sheet.read_portfolio()
        pnl = sheet.get_pnl()
        return {
            "portfolio": portfolio,
            "pnl": pnl,
            "agent_count": len(portfolio),
        }

    @tool
    def get_agent_report(target_agent_id: str) -> dict:
        """Get detailed assessment of a specific agent: all probes,
        all evaluations, historical performance.

        Args:
            target_agent_id: The agent ID to report on

        Returns:
            dict with agent info, probes, and evaluations
        """
        probes = sheet.read_probes(agent_id=target_agent_id, limit=20)
        evals = sheet.read_evaluations(agent_id=target_agent_id, limit=20)
        agents = sheet.read_agents()
        agent_info = next(
            (a for a in agents if a["agent_id"] == target_agent_id), None
        )
        return {
            "agent": agent_info,
            "probes": probes,
            "evaluations": evals,
        }

    @tool
    def buy_from_agent(target_agent_id: str, query: str) -> dict:
        """Buy data from a specific agent using x402.
        Logs the purchase to the ledger.

        Args:
            target_agent_id: The agent to buy from
            query: The query to send to the agent

        Returns:
            Purchase result with status, content, response, credits_used
        """
        agents = sheet.read_agents()
        agent_info = next(
            (a for a in agents if a["agent_id"] == target_agent_id), None
        )
        if not agent_info:
            return {
                "status": "error",
                "content": [{"text": f"Agent {target_agent_id} not found"}],
            }

        result = purchase_data_impl(
            payments=payments,
            plan_id=agent_info["plan_id"],
            seller_url=agent_info["url"],
            query=query,
            agent_id=target_agent_id if target_agent_id.startswith("did:") else None,
        )

        credits_used = result.get("credits_used", 0)
        if result.get("status") == "success" and credits_used > 0:
            sheet.write_ledger(
                direction="out",
                credits=credits_used,
                purpose="consulting_upstream",
                agent_id=target_agent_id,
                detail=query[:100],
            )

        return result

    # ── Payment-protected entry point ───────────────────────────

    @tool(context=True)
    @requires_payment(
        payments=payments,
        plan_id=plan_id,
        credits=1,
        agent_id=agent_id,
    )
    def consulting_query(query: str, tool_context: Any = None) -> dict:
        """Process a consulting request. This is the billable entry point.
        After this tool runs, the agent will use read_portfolio,
        buy_from_agent, and get_agent_report to fulfill the request.

        Args:
            query: The client's consulting query
            tool_context: Injected by @tool(context=True)

        Returns:
            dict with status and query acceptance
        """
        # The payment decorator handles verify/settle automatically.
        # This tool just validates the query and returns it for the agent to process.
        return {
            "status": "accepted",
            "content": [{"text": f"Query accepted: {query}"}],
            "query": query,
        }

    SYSTEM_PROMPT = """\
You are a consulting agent in an AI agent marketplace. Clients pay you to either:

(a) Provide intelligence about the marketplace — which agents are good,
    which are overpriced, quality rankings, cost comparisons.

(b) Fulfill data requests by buying from the best available agents on
    the client's behalf and delivering a synthesized product.

Your workflow:
1. First call consulting_query with the client's request (this handles payment).
2. Call read_portfolio to see all evaluated agents.
3. For intelligence requests (a): analyze the portfolio and respond.
4. For data requests (b): identify the top 2-3 agents for the task,
   call buy_from_agent for each, synthesize their outputs, and include
   a cost breakdown.

Always include in your response:
- Which agents you consulted and why
- Quality data from your evaluation engine
- Cost breakdown (client paid X, upstream cost Y)
- Confidence level
"""

    return Agent(
        model=model,
        tools=[consulting_query, read_portfolio, get_agent_report, buy_from_agent],
        system_prompt=SYSTEM_PROMPT,
    )
