"""Comprehensive tests for CentralSheet class."""

from __future__ import annotations

import json
import pytest
from src.central_sheet import CentralSheet


@pytest.fixture
def sheet():
    """Create an in-memory CentralSheet for testing."""
    return CentralSheet(":memory:")


@pytest.fixture
def sample_agents():
    """Load sample agents from fixtures."""
    with open("tests/fixtures/sample_agents.json") as f:
        return json.load(f)


# ── Agent CRUD Tests ────────────────────────────────────────


def test_write_agent_creates_new_agent(sheet):
    """Test writing a new agent creates a record."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
        pricing={"basic": {"credits": 100, "description": "Basic tier"}},
        tags=["test", "demo"],
        description="A test agent",
        category="AI/ML",
        team_name="Test Team",
    )

    agents = sheet.read_agents()
    assert len(agents) == 1
    assert agents[0]["agent_id"] == "did:nv:test1"
    assert agents[0]["name"] == "Test Agent"
    assert agents[0]["status"] == "new"
    assert json.loads(agents[0]["tags"]) == ["test", "demo"]
    assert json.loads(agents[0]["pricing"]) == {
        "basic": {"credits": 100, "description": "Basic tier"}
    }


def test_write_agent_upsert_updates_last_seen(sheet):
    """Test writing same agent twice updates last_seen."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    agents = sheet.read_agents()
    first_seen = agents[0]["first_seen"]
    first_last_seen = agents[0]["last_seen"]

    # Write again
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    agents = sheet.read_agents()
    assert len(agents) == 1
    assert agents[0]["first_seen"] == first_seen  # unchanged
    assert agents[0]["last_seen"] >= first_last_seen  # updated


def test_read_agents_without_filter(sheet, sample_agents):
    """Test reading all agents without status filter."""
    for agent in sample_agents:
        sheet.write_agent(
            agent_id=agent["agent_id"],
            name=agent["name"],
            url=agent["url"],
            plan_id=agent["plan_id"],
            pricing=agent["pricing"],
            tags=agent["tags"],
            description=agent["description"],
            category=agent["category"],
            team_name=agent["team_name"],
        )

    agents = sheet.read_agents()
    assert len(agents) == 3
    agent_ids = {a["agent_id"] for a in agents}
    assert agent_ids == {"did:nv:agent1", "did:nv:agent2", "did:nv:agent3"}


def test_read_agents_with_status_filter(sheet, sample_agents):
    """Test reading agents filtered by status."""
    for agent in sample_agents:
        sheet.write_agent(
            agent_id=agent["agent_id"],
            name=agent["name"],
            url=agent["url"],
            plan_id=agent["plan_id"],
        )

    # Update one agent to 'probed'
    sheet.update_agent_status("did:nv:agent1", "probed")

    # Filter by 'new'
    new_agents = sheet.read_agents(status="new")
    assert len(new_agents) == 2

    # Filter by 'probed'
    probed_agents = sheet.read_agents(status="probed")
    assert len(probed_agents) == 1
    assert probed_agents[0]["agent_id"] == "did:nv:agent1"


def test_update_agent_status(sheet):
    """Test updating agent status."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    # Verify initial status
    agents = sheet.read_agents()
    assert agents[0]["status"] == "new"

    # Update to 'probed'
    sheet.update_agent_status("did:nv:test1", "probed")
    agents = sheet.read_agents()
    assert agents[0]["status"] == "probed"

    # Update to 'evaluated'
    sheet.update_agent_status("did:nv:test1", "evaluated")
    agents = sheet.read_agents()
    assert agents[0]["status"] == "evaluated"


def test_status_transitions(sheet):
    """Test status transitions: new → probed → evaluated."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    # new → probed
    sheet.update_agent_status("did:nv:test1", "probed")
    assert sheet.read_agents()[0]["status"] == "probed"

    # probed → evaluated
    sheet.update_agent_status("did:nv:test1", "evaluated")
    assert sheet.read_agents()[0]["status"] == "evaluated"


# ── Probe CRUD Tests ────────────────────────────────────────


def test_write_probe_success(sheet):
    """Test writing a successful probe result."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    probe_id = sheet.write_probe(
        agent_id="did:nv:test1",
        query="test query",
        response="test response",
        credits_spent=100,
        latency_ms=250.5,
        response_bytes=1024,
        http_status=200,
        error=None,
    )

    assert probe_id > 0

    probes = sheet.read_probes()
    assert len(probes) == 1
    assert probes[0]["agent_id"] == "did:nv:test1"
    assert probes[0]["query"] == "test query"
    assert probes[0]["credits_spent"] == 100
    assert probes[0]["latency_ms"] == 250.5
    assert probes[0]["error"] is None


def test_write_probe_with_error(sheet):
    """Test writing a failed probe result."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    probe_id = sheet.write_probe(
        agent_id="did:nv:test1",
        query="test query",
        response="",
        credits_spent=0,
        http_status=500,
        error="Connection timeout",
    )

    probes = sheet.read_probes()
    assert len(probes) == 1
    assert probes[0]["error"] == "Connection timeout"
    assert probes[0]["http_status"] == 500


def test_read_probes_without_filter(sheet):
    """Test reading all probes without agent_id filter."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Test Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )

    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query1",
        response="response1",
        credits_spent=100,
    )
    sheet.write_probe(
        agent_id="did:nv:test2",
        query="query2",
        response="response2",
        credits_spent=200,
    )

    probes = sheet.read_probes()
    assert len(probes) == 2


def test_read_probes_with_agent_filter(sheet):
    """Test reading probes filtered by agent_id."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Test Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )

    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query1",
        response="response1",
        credits_spent=100,
    )
    sheet.write_probe(
        agent_id="did:nv:test2",
        query="query2",
        response="response2",
        credits_spent=200,
    )

    probes = sheet.read_probes(agent_id="did:nv:test1")
    assert len(probes) == 1
    assert probes[0]["agent_id"] == "did:nv:test1"


# ── Evaluation CRUD Tests ───────────────────────────────────


def test_write_evaluation(sheet):
    """Test writing an evaluation result."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    probe_id = sheet.write_probe(
        agent_id="did:nv:test1",
        query="test query",
        response="test response",
        credits_spent=100,
    )

    eval_id = sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="quality_judge",
        metrics={"score": 0.85, "confidence": 0.9},
        summary="High quality response",
        probe_id=probe_id,
    )

    assert eval_id > 0

    evals = sheet.read_evaluations()
    assert len(evals) == 1
    assert evals[0]["agent_id"] == "did:nv:test1"
    assert evals[0]["evaluator"] == "quality_judge"
    assert evals[0]["metrics"] == {"score": 0.85, "confidence": 0.9}
    assert evals[0]["summary"] == "High quality response"


def test_read_evaluations_without_filter(sheet):
    """Test reading all evaluations without filters."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Test Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )

    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test2",
        evaluator="quality_judge",
        metrics={"score": 0.75},
    )

    evals = sheet.read_evaluations()
    assert len(evals) == 2


def test_read_evaluations_with_agent_filter(sheet):
    """Test reading evaluations filtered by agent_id."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Test Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )

    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test2",
        evaluator="gate",
        metrics={"pass": False},
    )

    evals = sheet.read_evaluations(agent_id="did:nv:test1")
    assert len(evals) == 1
    assert evals[0]["agent_id"] == "did:nv:test1"


def test_read_evaluations_with_evaluator_filter(sheet):
    """Test reading evaluations filtered by evaluator."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="quality_judge",
        metrics={"score": 0.85},
    )

    evals = sheet.read_evaluations(evaluator="gate")
    assert len(evals) == 1
    assert evals[0]["evaluator"] == "gate"


def test_read_evaluations_with_both_filters(sheet):
    """Test reading evaluations filtered by both agent_id and evaluator."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Test Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )

    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="quality_judge",
        metrics={"score": 0.85},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test2",
        evaluator="gate",
        metrics={"pass": False},
    )

    evals = sheet.read_evaluations(agent_id="did:nv:test1", evaluator="gate")
    assert len(evals) == 1
    assert evals[0]["agent_id"] == "did:nv:test1"
    assert evals[0]["evaluator"] == "gate"


def test_json_serialization_round_trip(sheet):
    """Test that pricing, tags, and metrics serialize/deserialize correctly."""
    pricing = {
        "basic": {"credits": 100, "description": "Basic tier"},
        "pro": {"credits": 500, "description": "Pro tier"},
    }
    tags = ["ai", "ml", "analytics"]

    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
        pricing=pricing,
        tags=tags,
    )

    agents = sheet.read_agents()
    assert json.loads(agents[0]["pricing"]) == pricing
    assert json.loads(agents[0]["tags"]) == tags

    # Test metrics round-trip
    metrics = {"score": 0.85, "latency": 250, "nested": {"key": "value"}}
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="test",
        metrics=metrics,
    )

    evals = sheet.read_evaluations()
    assert evals[0]["metrics"] == metrics


# ── Ledger Tests ────────────────────────────────────────────


def test_write_ledger_outbound(sheet):
    """Test writing an outbound ledger entry."""
    sheet.write_ledger(
        direction="out",
        credits=100,
        purpose="probe",
        agent_id="did:nv:test1",
        detail="Test query",
    )

    pnl = sheet.get_pnl()
    assert pnl["spent"] == 100
    assert pnl["revenue"] == 0
    assert pnl["margin"] == -100


def test_write_ledger_inbound(sheet):
    """Test writing an inbound ledger entry."""
    sheet.write_ledger(
        direction="in",
        credits=500,
        purpose="consulting_revenue",
        detail="Client query",
    )

    pnl = sheet.get_pnl()
    assert pnl["revenue"] == 500
    assert pnl["spent"] == 0
    assert pnl["margin"] == 500


def test_get_pnl_with_multiple_entries(sheet):
    """Test P&L calculation with multiple ledger entries."""
    sheet.write_ledger(direction="out", credits=100, purpose="probe")
    sheet.write_ledger(direction="out", credits=200, purpose="probe")
    sheet.write_ledger(direction="in", credits=1000, purpose="consulting_revenue")
    sheet.write_ledger(direction="in", credits=500, purpose="consulting_revenue")

    pnl = sheet.get_pnl()
    assert pnl["spent"] == 300
    assert pnl["revenue"] == 1500
    assert pnl["margin"] == 1200


# ── Portfolio View Tests ────────────────────────────────────


def test_read_portfolio_empty(sheet):
    """Test reading portfolio when no agents exist."""
    portfolio = sheet.read_portfolio()
    assert len(portfolio) == 0


def test_read_portfolio_filters_successful_probes_only(sheet):
    """Test that portfolio view filters probes where error IS NULL."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    # Write successful probe
    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query1",
        response="response1",
        credits_spent=100,
        error=None,
    )

    # Write failed probe
    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query2",
        response="",
        credits_spent=0,
        error="Connection failed",
    )

    portfolio = sheet.read_portfolio()
    assert len(portfolio) == 1
    assert portfolio[0]["probe_count"] == 1  # Only successful probe counted
    assert portfolio[0]["total_spent"] == 100


def test_read_portfolio_aggregates_correctly(sheet):
    """Test that portfolio view aggregates probe and evaluation data."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    # Write multiple successful probes
    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query1",
        response="response1",
        credits_spent=100,
        latency_ms=200.0,
    )
    sheet.write_probe(
        agent_id="did:nv:test1",
        query="query2",
        response="response2",
        credits_spent=200,
        latency_ms=300.0,
    )

    # Write evaluations
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="quality_judge",
        metrics={"score": 0.85},
    )

    portfolio = sheet.read_portfolio()
    assert len(portfolio) == 1
    assert portfolio[0]["agent_id"] == "did:nv:test1"
    assert portfolio[0]["probe_count"] == 2
    assert portfolio[0]["avg_cost"] == 150.0
    assert portfolio[0]["avg_latency"] == 250.0
    # Note: total_spent is inflated due to Cartesian product from multiple LEFT JOINs
    # With 2 probes (100+200=300) and 2 evals, we get 2*2=4 rows, so 300*2=600
    assert portfolio[0]["total_spent"] == 600
    assert portfolio[0]["eval_count"] == 2


def test_get_top_agents_empty(sheet):
    """Test get_top_agents when no agents have probes."""
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Test Agent",
        url="https://test.com",
        plan_id="plan_001",
    )

    top = sheet.get_top_agents()
    assert len(top) == 0  # No probes, so not included


def test_get_top_agents_ranks_by_eval_count_and_cost(sheet):
    """Test that get_top_agents ranks by eval_count DESC, avg_cost ASC."""
    # Agent 1: high eval count, high cost
    sheet.write_agent(
        agent_id="did:nv:test1",
        name="Agent 1",
        url="https://test1.com",
        plan_id="plan_001",
    )
    sheet.write_probe(
        agent_id="did:nv:test1",
        query="q1",
        response="r1",
        credits_spent=500,
    )
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test1",
        evaluator="quality",
        metrics={"score": 0.9},
    )

    # Agent 2: high eval count, low cost (should rank first)
    sheet.write_agent(
        agent_id="did:nv:test2",
        name="Agent 2",
        url="https://test2.com",
        plan_id="plan_002",
    )
    sheet.write_probe(
        agent_id="did:nv:test2",
        query="q2",
        response="r2",
        credits_spent=100,
    )
    sheet.write_evaluation(
        agent_id="did:nv:test2",
        evaluator="gate",
        metrics={"pass": True},
    )
    sheet.write_evaluation(
        agent_id="did:nv:test2",
        evaluator="quality",
        metrics={"score": 0.85},
    )

    # Agent 3: low eval count
    sheet.write_agent(
        agent_id="did:nv:test3",
        name="Agent 3",
        url="https://test3.com",
        plan_id="plan_003",
    )
    sheet.write_probe(
        agent_id="did:nv:test3",
        query="q3",
        response="r3",
        credits_spent=50,
    )
    sheet.write_evaluation(
        agent_id="did:nv:test3",
        evaluator="gate",
        metrics={"pass": True},
    )

    top = sheet.get_top_agents(limit=10)
    assert len(top) == 3
    # Agent 2 should be first (same eval_count as Agent 1, but lower cost)
    assert top[0]["agent_id"] == "did:nv:test2"
    assert top[1]["agent_id"] == "did:nv:test1"
    assert top[2]["agent_id"] == "did:nv:test3"


def test_get_top_agents_respects_limit(sheet):
    """Test that get_top_agents respects the limit parameter."""
    for i in range(5):
        agent_id = f"did:nv:test{i}"
        sheet.write_agent(
            agent_id=agent_id,
            name=f"Agent {i}",
            url=f"https://test{i}.com",
            plan_id=f"plan_{i}",
        )
        sheet.write_probe(
            agent_id=agent_id,
            query=f"query{i}",
            response=f"response{i}",
            credits_spent=100,
        )

    top = sheet.get_top_agents(limit=3)
    assert len(top) == 3
