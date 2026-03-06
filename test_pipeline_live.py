#!/usr/bin/env python3
"""
Test the full scoring pipeline: inject fake probes → run evaluators → verify /portfolio output.

Run locally:   python3 test_pipeline_live.py local
Run on EC2:    python3 test_pipeline_live.py ec2

'local' mode: creates temp DB, injects data, runs gate evaluator, fakes quality_judge,
              verifies portfolio output matches what dashboard expects.

'ec2' mode:   injects a test agent into the LIVE portfolio.db, then hits the live
              /portfolio endpoint to confirm dashboard would display it.
              *** Run on EC2 only — will modify your live database ***
"""

import sys
import os
import json
import asyncio

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.central_sheet import CentralSheet
from src.evaluators.gate import gate_evaluator


FAKE_AGENT_ID = "test:pipeline-verification-agent"
FAKE_AGENT_NAME = "Pipeline Test Agent (safe to delete)"
FAKE_URL = "http://fake-test-agent.local:3000"
FAKE_PLAN_ID = "did:nv:test000"


def inject_fake_data(sheet: CentralSheet):
    """Inject a fake agent with mixed probe results."""

    # 1. Write a fake agent
    sheet.write_agent(
        agent_id=FAKE_AGENT_ID,
        name=FAKE_AGENT_NAME,
        url=FAKE_URL,
        plan_id=FAKE_PLAN_ID,
        pricing={"basic": {"credits": 1}},
        tags=["test"],
        description="Test agent for pipeline verification",
        category="test",
        team_name="TestTeam",
    )
    print(f"✅ Wrote agent: {FAKE_AGENT_NAME}")

    # 2. Write some successful probes
    for i, (query, response, credits) in enumerate([
        ("What services do you offer?",
         "I provide AI-powered market analysis, sentiment tracking, and trend forecasting.",
         2),
        ("Analyze current AI market trends",
         "The AI agent marketplace is experiencing 40% quarterly growth. Key trends: multi-agent orchestration, payment protocol standardization via x402, and autonomous agent economies.",
         3),
        ("Give me data on agent adoption",
         "Based on our latest data: 2,500+ agents registered across major marketplaces, average transaction volume of $0.15 per request, 78% of transactions are agent-to-agent.",
         2),
    ]):
        pid = sheet.write_probe(
            agent_id=FAKE_AGENT_ID,
            query=query,
            response=response,
            credits_spent=credits,
            latency_ms=150 + i * 50,
            response_bytes=len(response),
            http_status=200,
            error=None,
        )
        print(f"  ✅ Probe {pid}: {query[:40]}... ({credits} credits)")

    # 3. Write one failed probe (to test gate evaluator handles mixed results)
    sheet.write_probe(
        agent_id=FAKE_AGENT_ID,
        query="Bad query",
        response="",
        credits_spent=0,
        latency_ms=5000,
        response_bytes=0,
        http_status=500,
        error="HTTP 500: Internal Server Error",
    )
    print(f"  ✅ Probe (failed): error probe injected")

    # 4. Write ledger entries
    sheet.write_ledger("out", 7, "probe", FAKE_AGENT_ID, "test probes")
    print(f"  ✅ Ledger: 7 credits spent on probes")

    return sheet


async def run_gate(sheet: CentralSheet):
    """Run gate evaluator on fake agent."""
    await gate_evaluator(FAKE_AGENT_ID, sheet)

    evals = sheet.read_evaluations(agent_id=FAKE_AGENT_ID, evaluator="gate")
    if evals:
        e = evals[0]
        print(f"\n✅ Gate evaluator ran:")
        print(f"   Metrics: {json.dumps(e['metrics'], indent=2)}")
        print(f"   Summary: {e['summary']}")
        return e["metrics"].get("passed", False)
    else:
        print("\n❌ Gate evaluator did NOT write an evaluation!")
        return False


def fake_quality_judge(sheet: CentralSheet):
    """Simulate quality_judge output (skip LLM call for testing)."""
    metrics = {
        "quality_score": 82.5,
        "credits_spent": 7,
        "roi": 82.5 / 7,  # 11.79
        "probe_count": 3,
        "avg_quality_per_probe": 27.5,
        "dimensions": {
            "relevance": 85,
            "completeness": 80,
            "accuracy": 82,
            "clarity": 83,
        }
    }

    eid = sheet.write_evaluation(
        agent_id=FAKE_AGENT_ID,
        evaluator="quality_judge",
        metrics=metrics,
        summary="Good quality responses with strong relevance. ROI of 11.8 indicates solid value.",
    )
    print(f"\n✅ Quality judge evaluation written (id={eid}):")
    print(f"   quality_score: {metrics['quality_score']}")
    print(f"   roi: {metrics['roi']:.2f}")
    print(f"   credits_spent: {metrics['credits_spent']}")


def verify_portfolio(sheet: CentralSheet):
    """Simulate exactly what /portfolio endpoint does and verify output."""
    print(f"\n{'═' * 60}")
    print("VERIFYING /portfolio OUTPUT (same logic as main.py)")
    print(f"{'═' * 60}")

    # This is EXACTLY what main.py /portfolio does:
    portfolio = sheet.read_portfolio()

    for agent in portfolio:
        evals = sheet.read_evaluations(agent_id=agent["agent_id"])
        agent["evaluators"] = [e["evaluator"] for e in evals]

        quality_eval = next((e for e in evals if e["evaluator"] == "quality_judge"), None)
        if quality_eval and quality_eval.get("metrics"):
            metrics = quality_eval["metrics"]
            agent["quality_score"] = metrics.get("quality_score", 0)
            agent["roi"] = metrics.get("roi", 0)
        else:
            agent["quality_score"] = None
            agent["roi"] = None

    # Sort by ROI
    portfolio_sorted = sorted(
        portfolio,
        key=lambda x: (x["roi"] is not None, x["roi"] or 0),
        reverse=True
    )

    # Find our test agent
    test_agent = next((a for a in portfolio_sorted if a["agent_id"] == FAKE_AGENT_ID), None)

    if not test_agent:
        print("❌ Test agent NOT found in portfolio view!")
        print(f"   Portfolio has {len(portfolio_sorted)} agents")
        return False

    print(f"\n✅ Test agent found in portfolio:")
    print(f"   name:          {test_agent['name']}")
    print(f"   status:        {test_agent['status']}")
    print(f"   probe_count:   {test_agent['probe_count']}")
    print(f"   avg_cost:      {test_agent['avg_cost']}")
    print(f"   avg_latency:   {test_agent['avg_latency']}")
    print(f"   eval_count:    {test_agent['eval_count']}")
    print(f"   evaluators:    {test_agent['evaluators']}")
    print(f"   quality_score: {test_agent['quality_score']}")
    print(f"   roi:           {test_agent['roi']}")

    # Verify dashboard fields exist
    checks = [
        ("quality_score is not None", test_agent["quality_score"] is not None),
        ("roi is not None", test_agent["roi"] is not None),
        ("roi > 0", (test_agent["roi"] or 0) > 0),
        ("probe_count > 0", test_agent["probe_count"] > 0),
        ("eval_count > 0", test_agent["eval_count"] > 0),
        ("'gate' in evaluators", "gate" in test_agent["evaluators"]),
        ("'quality_judge' in evaluators", "quality_judge" in test_agent["evaluators"]),
    ]

    all_pass = True
    print(f"\n   Dashboard field checks:")
    for label, result in checks:
        icon = "✅" if result else "❌"
        print(f"   {icon} {label}")
        if not result:
            all_pass = False

    # Check recommended_agents
    recommended = [
        a for a in portfolio_sorted[:3]
        if a["roi"] is not None and a["roi"] > 0
    ]
    in_recommended = any(a["agent_id"] == FAKE_AGENT_ID for a in recommended)
    print(f"\n   In recommended_agents (top 3 by ROI): {'✅ YES' if in_recommended else '⚠️  No (other agents have higher ROI or <3 evaluated)'}")

    if all_pass:
        print(f"\n{'═' * 60}")
        print("🎉 ALL CHECKS PASSED — Dashboard will display scoring data correctly!")
        print(f"{'═' * 60}")
    else:
        print(f"\n{'═' * 60}")
        print("⚠️  Some checks failed — review the output above")
        print(f"{'═' * 60}")

    return all_pass


def cleanup(sheet: CentralSheet):
    """Remove test data."""
    conn = sheet._conn()
    conn.execute("DELETE FROM evaluations WHERE agent_id = ?", (FAKE_AGENT_ID,))
    conn.execute("DELETE FROM probes WHERE agent_id = ?", (FAKE_AGENT_ID,))
    conn.execute("DELETE FROM ledger WHERE agent_id = ?", (FAKE_AGENT_ID,))
    conn.execute("DELETE FROM agents WHERE agent_id = ?", (FAKE_AGENT_ID,))
    conn.commit()
    print("\n🧹 Test data cleaned up")


async def run_local():
    """Full test with temp in-memory DB."""
    print("MODE: local (in-memory DB)\n")

    sheet = CentralSheet(":memory:")
    inject_fake_data(sheet)

    gate_passed = await run_gate(sheet)
    if not gate_passed:
        print("⚠️  Gate evaluator marked agent as failed — check probe data")

    fake_quality_judge(sheet)

    # Mark as evaluated (normally pipeline.run does this)
    sheet.update_agent_status(FAKE_AGENT_ID, "evaluated")

    verify_portfolio(sheet)


async def run_ec2():
    """Inject test data into live DB, verify, then clean up."""
    print("MODE: ec2 (live portfolio.db)\n")

    if not os.path.exists(DB_PATH := "portfolio.db"):
        print(f"❌ {DB_PATH} not found. Run from the nevermind-agent directory.")
        return

    sheet = CentralSheet(DB_PATH)
    inject_fake_data(sheet)

    gate_passed = await run_gate(sheet)
    fake_quality_judge(sheet)
    sheet.update_agent_status(FAKE_AGENT_ID, "evaluated")

    verify_portfolio(sheet)

    # Also test live endpoint if server is running
    try:
        import httpx
        resp = httpx.get("http://localhost:3000/portfolio", timeout=5.0)
        data = resp.json()
        test_in_live = any(
            a.get("agent_id") == FAKE_AGENT_ID
            for a in data.get("portfolio", [])
        )
        print(f"\n🌐 Live /portfolio endpoint: {'✅ Test agent visible' if test_in_live else '❌ Test agent NOT in response'}")
        if test_in_live:
            agent = next(a for a in data["portfolio"] if a["agent_id"] == FAKE_AGENT_ID)
            print(f"   ROI: {agent.get('roi')}, Quality: {agent.get('quality_score')}")
    except Exception as e:
        print(f"\n⚠️  Could not hit live endpoint: {e}")

    # Clean up
    cleanup(sheet)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"

    if mode == "local":
        asyncio.run(run_local())
    elif mode == "ec2":
        asyncio.run(run_ec2())
    else:
        print(f"Usage: python3 {sys.argv[0]} [local|ec2]")
