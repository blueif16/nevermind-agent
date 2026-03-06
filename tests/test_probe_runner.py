"""Comprehensive tests for probe_runner module."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.central_sheet import CentralSheet
from src.probe_runner import DEFAULT_QUERIES, run_probe


@pytest.fixture
def sheet():
    """Create an in-memory CentralSheet for testing."""
    return CentralSheet(":memory:")


@pytest.fixture
def sample_agents():
    """Load sample agents from fixtures."""
    with open("tests/fixtures/sample_agents.json") as f:
        return json.load(f)


@pytest.fixture
def mock_payments():
    """Create a mock Payments SDK instance."""
    return Mock()


# ── Tier 1 Tests (Mocked) ──────────────────────────────────────


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_success(mock_purchase, sheet, sample_agents, mock_payments):
    """Test successful probe execution with mocked payment."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "We provide data analysis services starting at 100 credits",
        "credits_used": 100,
        "content": [{"text": "We provide data analysis services starting at 100 credits"}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["What services do you provide?"],
    )

    # Verify sheet.write_probe was called
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    assert probes[0]["query"] == "What services do you provide?"
    assert probes[0]["response"] == "We provide data analysis services starting at 100 credits"
    assert probes[0]["credits_spent"] == 100
    assert probes[0]["http_status"] == 200
    assert probes[0]["error"] is None


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_payment_required(mock_purchase, sheet, sample_agents, mock_payments):
    """Test probe with HTTP 402 payment required response."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "payment_required",
        "credits_used": 0,
        "content": [{"text": "Payment required (HTTP 402): Insufficient balance"}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify error recorded
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    assert probes[0]["http_status"] == 402
    assert probes[0]["error"] == "Payment required (HTTP 402): Insufficient balance"
    assert probes[0]["credits_spent"] == 0
    assert probes[0]["response"] == ""


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_error(mock_purchase, sheet, sample_agents, mock_payments):
    """Test probe with error status from purchase_data_impl."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "error",
        "credits_used": 0,
        "content": [{"text": "HTTP 500: Internal server error"}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify error handling
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    assert probes[0]["http_status"] == 0
    assert probes[0]["error"] == "HTTP 500: Internal server error"
    assert probes[0]["credits_spent"] == 0


@pytest.mark.asyncio
async def test_run_probe_missing_url(sheet, sample_agents, mock_payments):
    """Test probe skips when url is missing."""
    # Setup - agent without url
    agent_info = {
        "agent_id": "did:nv:test",
        "name": "Test Agent",
        "plan_id": "plan_001",
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
    )

    # Verify no probes were written
    probes = sheet.read_probes()
    assert len(probes) == 0


@pytest.mark.asyncio
async def test_run_probe_missing_plan_id(sheet, sample_agents, mock_payments):
    """Test probe skips when plan_id is missing."""
    # Setup - agent without plan_id
    agent_info = {
        "agent_id": "did:nv:test",
        "name": "Test Agent",
        "url": "https://test.com",
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
    )

    # Verify no probes were written
    probes = sheet.read_probes()
    assert len(probes) == 0


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_ledger_write(mock_purchase, sheet, sample_agents, mock_payments):
    """Test ledger entry is written on successful probe."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 150,
        "content": [{"text": "Test response"}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify ledger entry
    pnl = sheet.get_pnl()
    assert pnl["spent"] == 150
    assert pnl["revenue"] == 0
    assert pnl["margin"] == -150


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_status_update(mock_purchase, sheet, sample_agents, mock_payments):
    """Test agent status is updated to 'probed' on success."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 100,
        "content": [{"text": "Test response"}],
    }

    # Verify initial status
    agents = sheet.read_agents()
    assert agents[0]["status"] == "new"

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify status updated
    agents = sheet.read_agents()
    assert agents[0]["status"] == "probed"


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_status_dead_on_all_failures(mock_purchase, sheet, sample_agents, mock_payments):
    """Test agent status is updated to 'dead' when all probes fail."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "error",
        "credits_used": 0,
        "content": [{"text": "Connection failed"}],
    }

    # Execute with multiple queries (all will fail)
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Query 1", "Query 2", "Query 3"],
    )

    # Verify status updated to 'dead'
    agents = sheet.read_agents()
    assert agents[0]["status"] == "dead"


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_eval_callback(mock_purchase, sheet, sample_agents, mock_payments):
    """Test eval_callback is invoked after probes complete."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 100,
        "content": [{"text": "Test response"}],
    }

    callback_invoked = []

    def eval_callback(agent_id, sheet_instance):
        callback_invoked.append((agent_id, sheet_instance))

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
        eval_callback=eval_callback,
    )

    # Verify callback was invoked
    assert len(callback_invoked) == 1
    assert callback_invoked[0][0] == agent_info["agent_id"]
    assert callback_invoked[0][1] is sheet


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_multiple_queries(mock_purchase, sheet, sample_agents, mock_payments):
    """Test all queries are executed."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 50,
        "content": [{"text": "Test response"}],
    }

    queries = ["Query 1", "Query 2", "Query 3"]

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=queries,
    )

    # Verify all queries were executed
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 3
    # Probes are returned in reverse order (newest first)
    assert probes[2]["query"] == "Query 1"
    assert probes[1]["query"] == "Query 2"
    assert probes[0]["query"] == "Query 3"

    # Verify total credits spent
    pnl = sheet.get_pnl()
    assert pnl["spent"] == 150  # 50 * 3


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_latency_tracking(mock_purchase, sheet, sample_agents, mock_payments):
    """Test latency_ms is recorded for each probe."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 100,
        "content": [{"text": "Test response"}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify latency was recorded
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    assert probes[0]["latency_ms"] is not None
    assert probes[0]["latency_ms"] > 0


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_exception_handling(mock_purchase, sheet, sample_agents, mock_payments):
    """Test exception during probe execution is handled."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.side_effect = Exception("Unexpected error")

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify exception was recorded
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    assert probes[0]["error"] == "Exception: Unexpected error"
    assert probes[0]["http_status"] == 0
    assert probes[0]["credits_spent"] == 0


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_uses_default_queries(mock_purchase, sheet, sample_agents, mock_payments):
    """Test DEFAULT_QUERIES are used when queries parameter is None."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    mock_purchase.return_value = {
        "status": "success",
        "response": "Test response",
        "credits_used": 100,
        "content": [{"text": "Test response"}],
    }

    # Execute without queries parameter
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
    )

    # Verify DEFAULT_QUERIES were used
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == len(DEFAULT_QUERIES)
    # Probes are returned in reverse order (newest first)
    for i, probe in enumerate(reversed(probes)):
        assert probe["query"] == DEFAULT_QUERIES[i]


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_response_bytes_calculation(mock_purchase, sheet, sample_agents, mock_payments):
    """Test response_bytes is calculated correctly."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    response_text = "This is a test response with some content"
    mock_purchase.return_value = {
        "status": "success",
        "response": response_text,
        "credits_used": 100,
        "content": [{"text": response_text}],
    }

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Test query"],
    )

    # Verify response_bytes
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 1
    expected_bytes = len(response_text.encode("utf-8"))
    assert probes[0]["response_bytes"] == expected_bytes


@pytest.mark.asyncio
@patch("src.probe_runner.purchase_data_impl")
async def test_run_probe_mixed_success_and_failure(mock_purchase, sheet, sample_agents, mock_payments):
    """Test probe with mixed success and failure results."""
    # Setup
    agent_info = sample_agents[0]
    sheet.write_agent(
        agent_id=agent_info["agent_id"],
        name=agent_info["name"],
        url=agent_info["url"],
        plan_id=agent_info["plan_id"],
    )

    # Mock returns success for first call, error for second
    mock_purchase.side_effect = [
        {
            "status": "success",
            "response": "Success response",
            "credits_used": 100,
            "content": [{"text": "Success response"}],
        },
        {
            "status": "error",
            "credits_used": 0,
            "content": [{"text": "Error occurred"}],
        },
    ]

    # Execute
    await run_probe(
        agent_info=agent_info,
        sheet=sheet,
        payments=mock_payments,
        queries=["Query 1", "Query 2"],
    )

    # Verify status is 'probed' (at least one success)
    agents = sheet.read_agents()
    assert agents[0]["status"] == "probed"

    # Verify both probes recorded (returned in reverse order)
    probes = sheet.read_probes(agent_id=agent_info["agent_id"])
    assert len(probes) == 2
    assert probes[1]["error"] is None  # First query (success)
    assert probes[0]["error"] == "Error occurred"  # Second query (error)
