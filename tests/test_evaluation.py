"""Tests for evaluation pipeline."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.central_sheet import CentralSheet
from src.evaluation import EvaluationPipeline, pipeline
from src.evaluators.gate import gate_evaluator


@pytest.fixture
def mock_sheet():
    """Create a mock CentralSheet."""
    sheet = MagicMock(spec=CentralSheet)
    sheet.read_probes = MagicMock()
    sheet.write_evaluation = MagicMock()
    sheet.update_agent_status = MagicMock()
    return sheet


@pytest.fixture
def sample_probes_success():
    """Sample probes with all successes."""
    return [
        {
            "probe_id": 1,
            "agent_id": "test-agent",
            "query": "test query 1",
            "response": "response 1",
            "credits_spent": 10,
            "latency_ms": 100,
            "error": None,
        },
        {
            "probe_id": 2,
            "agent_id": "test-agent",
            "query": "test query 2",
            "response": "response 2",
            "credits_spent": 15,
            "latency_ms": 150,
            "error": None,
        },
    ]


@pytest.fixture
def sample_probes_mixed():
    """Sample probes with 50/50 success/failure."""
    return [
        {
            "probe_id": 1,
            "agent_id": "test-agent",
            "query": "test query 1",
            "response": "response 1",
            "credits_spent": 10,
            "latency_ms": 100,
            "error": None,
        },
        {
            "probe_id": 2,
            "agent_id": "test-agent",
            "query": "test query 2",
            "response": "",
            "credits_spent": 0,
            "latency_ms": 50,
            "error": "Connection timeout",
        },
    ]


@pytest.fixture
def sample_probes_all_fail():
    """Sample probes with all failures."""
    return [
        {
            "probe_id": 1,
            "agent_id": "test-agent",
            "query": "test query 1",
            "response": "",
            "credits_spent": 0,
            "latency_ms": 50,
            "error": "Connection timeout",
        },
        {
            "probe_id": 2,
            "agent_id": "test-agent",
            "query": "test query 2",
            "response": "",
            "credits_spent": 0,
            "latency_ms": 50,
            "error": "HTTP 500",
        },
    ]


class TestEvaluationPipeline:
    """Tests for EvaluationPipeline class."""

    def test_init(self):
        """Test pipeline initialization."""
        p = EvaluationPipeline()
        assert p.evaluator_names == []

    def test_register(self):
        """Test registering an evaluator."""
        p = EvaluationPipeline()

        async def dummy_evaluator(agent_id: str, sheet: CentralSheet, **kwargs):
            pass

        p.register("dummy", dummy_evaluator)
        assert "dummy" in p.evaluator_names
        assert len(p.evaluator_names) == 1

    def test_unregister(self):
        """Test unregistering an evaluator."""
        p = EvaluationPipeline()

        async def dummy_evaluator(agent_id: str, sheet: CentralSheet, **kwargs):
            pass

        p.register("dummy", dummy_evaluator)
        assert "dummy" in p.evaluator_names

        p.unregister("dummy")
        assert "dummy" not in p.evaluator_names

    @pytest.mark.asyncio
    async def test_run_no_successful_probes(self, mock_sheet):
        """Test run with no successful probes marks agent as dead."""
        mock_sheet.read_probes.return_value = [
            {"error": "timeout"},
            {"error": "connection failed"},
        ]

        p = EvaluationPipeline()
        await p.run("test-agent", mock_sheet)

        mock_sheet.update_agent_status.assert_called_once_with("test-agent", "dead")

    @pytest.mark.asyncio
    async def test_run_with_evaluators(self, mock_sheet, sample_probes_success):
        """Test run dispatches to all registered evaluators."""
        mock_sheet.read_probes.return_value = sample_probes_success

        call_count = {"eval1": 0, "eval2": 0}

        async def eval1(agent_id: str, sheet: CentralSheet, **kwargs):
            call_count["eval1"] += 1

        async def eval2(agent_id: str, sheet: CentralSheet, **kwargs):
            call_count["eval2"] += 1

        p = EvaluationPipeline()
        p.register("eval1", eval1)
        p.register("eval2", eval2)

        await p.run("test-agent", mock_sheet)

        assert call_count["eval1"] == 1
        assert call_count["eval2"] == 1
        mock_sheet.update_agent_status.assert_called_once_with("test-agent", "evaluated")

    @pytest.mark.asyncio
    async def test_run_evaluator_failure_doesnt_block_others(self, mock_sheet, sample_probes_success):
        """Test that one evaluator failing doesn't block others."""
        mock_sheet.read_probes.return_value = sample_probes_success

        call_count = {"good": 0}

        async def failing_eval(agent_id: str, sheet: CentralSheet, **kwargs):
            raise ValueError("Intentional failure")

        async def good_eval(agent_id: str, sheet: CentralSheet, **kwargs):
            call_count["good"] += 1

        p = EvaluationPipeline()
        p.register("failing", failing_eval)
        p.register("good", good_eval)

        await p.run("test-agent", mock_sheet)

        # Good evaluator should still run
        assert call_count["good"] == 1
        mock_sheet.update_agent_status.assert_called_once_with("test-agent", "evaluated")


class TestGateEvaluator:
    """Tests for gate evaluator."""

    @pytest.mark.asyncio
    async def test_gate_all_success(self, mock_sheet, sample_probes_success):
        """Test gate evaluator with 100% success rate."""
        mock_sheet.read_probes.return_value = sample_probes_success

        await gate_evaluator("test-agent", mock_sheet)

        # Should write evaluation with passed=True
        mock_sheet.write_evaluation.assert_called_once()
        call_args = mock_sheet.write_evaluation.call_args
        assert call_args[1]["agent_id"] == "test-agent"
        assert call_args[1]["evaluator"] == "gate"
        assert call_args[1]["metrics"]["passed"] is True
        assert call_args[1]["metrics"]["success_rate"] == 1.0
        assert call_args[1]["metrics"]["total_probes"] == 2
        assert call_args[1]["metrics"]["errors"] == 0

        # Should NOT mark as dead
        mock_sheet.update_agent_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_gate_50_percent(self, mock_sheet, sample_probes_mixed):
        """Test gate evaluator with 50% success rate (boundary case)."""
        mock_sheet.read_probes.return_value = sample_probes_mixed

        await gate_evaluator("test-agent", mock_sheet)

        # Should write evaluation with passed=False (50% is not > 50%)
        mock_sheet.write_evaluation.assert_called_once()
        call_args = mock_sheet.write_evaluation.call_args
        assert call_args[1]["metrics"]["passed"] is False
        assert call_args[1]["metrics"]["success_rate"] == 0.5
        assert call_args[1]["metrics"]["total_probes"] == 2
        assert call_args[1]["metrics"]["errors"] == 1

        # Should mark as dead
        mock_sheet.update_agent_status.assert_called_once_with("test-agent", "dead")

    @pytest.mark.asyncio
    async def test_gate_all_fail(self, mock_sheet, sample_probes_all_fail):
        """Test gate evaluator with 0% success rate."""
        mock_sheet.read_probes.return_value = sample_probes_all_fail

        await gate_evaluator("test-agent", mock_sheet)

        # Should write evaluation with passed=False
        mock_sheet.write_evaluation.assert_called_once()
        call_args = mock_sheet.write_evaluation.call_args
        assert call_args[1]["metrics"]["passed"] is False
        assert call_args[1]["metrics"]["success_rate"] == 0.0
        assert call_args[1]["metrics"]["total_probes"] == 2
        assert call_args[1]["metrics"]["errors"] == 2

        # Should mark as dead
        mock_sheet.update_agent_status.assert_called_once_with("test-agent", "dead")

    @pytest.mark.asyncio
    async def test_gate_no_probes(self, mock_sheet):
        """Test gate evaluator with no probes."""
        mock_sheet.read_probes.return_value = []

        await gate_evaluator("test-agent", mock_sheet)

        # Should not write evaluation or update status
        mock_sheet.write_evaluation.assert_not_called()
        mock_sheet.update_agent_status.assert_not_called()


class TestGlobalPipeline:
    """Tests for global pipeline instance."""

    def test_global_pipeline_exists(self):
        """Test that global pipeline instance exists."""
        assert pipeline is not None
        assert isinstance(pipeline, EvaluationPipeline)
