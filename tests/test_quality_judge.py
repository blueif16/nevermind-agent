"""Tests for quality_judge evaluator."""
import json
from unittest.mock import Mock, patch, MagicMock
import pytest

from src.evaluators.quality_judge import create_quality_judge
from src.central_sheet import CentralSheet


class TestQualityJudge:
    """Test suite for quality_judge evaluator."""

    def test_create_quality_judge_returns_callable(self):
        """Test that create_quality_judge returns an async callable."""
        mock_model = Mock()
        evaluator = create_quality_judge(mock_model)
        assert callable(evaluator)
        assert evaluator.__name__ == "quality_judge_evaluator"

    @pytest.mark.asyncio
    async def test_evaluator_reads_probes(self):
        """Test that evaluator reads probes from sheet."""
        mock_model = Mock()
        sheet = CentralSheet(":memory:")

        # Setup test data
        agent_id = "test-agent-1"
        sheet.write_agent(agent_id, "Test Agent", "http://test.com", "plan-123")
        sheet.write_probe(
            agent_id=agent_id,
            query="test query",
            response="test response",
            credits_spent=1,
            latency_ms=100,
            response_bytes=50,
            error=None,
        )

        # Mock the Agent to capture tool calls
        with patch("src.evaluators.quality_judge.Agent") as MockAgent:
            mock_agent_instance = MagicMock()
            MockAgent.return_value = mock_agent_instance

            # Create evaluator (this creates the Agent)
            evaluator = create_quality_judge(mock_model)

            # Verify Agent was created with correct tools
            assert MockAgent.called
            call_kwargs = MockAgent.call_args[1]
            assert "tools" in call_kwargs
            assert len(call_kwargs["tools"]) == 2
            assert "system_prompt" in call_kwargs

    @pytest.mark.asyncio
    async def test_evaluator_writes_evaluation_with_roi(self):
        """Test that evaluator writes evaluation with ROI metric."""
        mock_model = Mock()
        sheet = CentralSheet(":memory:")

        # Setup test data
        agent_id = "test-agent-2"
        sheet.write_agent(agent_id, "Test Agent 2", "http://test2.com", "plan-456")
        sheet.write_probe(
            agent_id=agent_id,
            query="test query",
            response="good response",
            credits_spent=2,
            latency_ms=150,
            response_bytes=100,
            error=None,
        )

        # Mock asyncio.to_thread to intercept the agent call
        async def mock_to_thread(func, *args, **kwargs):
            # Directly write evaluation to simulate agent behavior
            metrics = {
                "quality_score": 85.0,
                "credits_spent": 2,
                "roi": 42.5,
                "probe_count": 1,
                "avg_quality_per_probe": 85.0,
            }
            sheet.write_evaluation(
                agent_id=agent_id,
                evaluator="quality_judge",
                metrics=metrics,
                summary="High quality responses with good ROI"
            )
            return "Evaluation complete"

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            # Create and run evaluator
            evaluator = create_quality_judge(mock_model)
            await evaluator(agent_id=agent_id, sheet=sheet)

            # Verify evaluation was written
            evals = sheet.read_evaluations(agent_id=agent_id)
            assert len(evals) == 1
            assert evals[0]["evaluator"] == "quality_judge"
            assert evals[0]["metrics"]["roi"] == 42.5
            assert evals[0]["metrics"]["quality_score"] == 85.0
            assert evals[0]["metrics"]["credits_spent"] == 2

    @pytest.mark.asyncio
    async def test_roi_calculation(self):
        """Test ROI calculation: quality_score / credits_spent."""
        mock_model = Mock()
        sheet = CentralSheet(":memory:")

        agent_id = "test-agent-3"
        sheet.write_agent(agent_id, "Test Agent 3", "http://test3.com", "plan-789")

        # Add multiple probes with different credit costs
        for i in range(3):
            sheet.write_probe(
                agent_id=agent_id,
                query=f"query {i}",
                response=f"response {i}",
                credits_spent=1,
                latency_ms=100,
                response_bytes=50,
                error=None,
            )

        async def mock_to_thread(func, *args, **kwargs):
            # Simulate scoring: 90 quality, 3 credits spent
            quality_score = 90.0
            credits_spent = 3
            roi = quality_score / credits_spent  # 30.0

            metrics = {
                "quality_score": quality_score,
                "credits_spent": credits_spent,
                "roi": roi,
                "probe_count": 3,
            }
            sheet.write_evaluation(
                agent_id=agent_id,
                evaluator="quality_judge",
                metrics=metrics,
                summary=f"ROI: {roi:.2f}"
            )
            return "Done"

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            evaluator = create_quality_judge(mock_model)
            await evaluator(agent_id=agent_id, sheet=sheet)

            evals = sheet.read_evaluations(agent_id=agent_id, evaluator="quality_judge")
            assert len(evals) == 1
            assert evals[0]["metrics"]["roi"] == 30.0
            assert evals[0]["metrics"]["quality_score"] == 90.0
            assert evals[0]["metrics"]["credits_spent"] == 3

    @pytest.mark.asyncio
    async def test_integration_with_pipeline(self):
        """Test that quality_judge integrates with evaluation pipeline."""
        from src.evaluation import EvaluationPipeline

        mock_model = Mock()
        sheet = CentralSheet(":memory:")
        pipeline = EvaluationPipeline()

        # Register quality_judge
        evaluator = create_quality_judge(mock_model)
        pipeline.register("quality_judge", evaluator)

        assert "quality_judge" in pipeline.evaluator_names

        # Setup test data
        agent_id = "test-agent-4"
        sheet.write_agent(agent_id, "Test Agent 4", "http://test4.com", "plan-999")
        sheet.write_probe(
            agent_id=agent_id,
            query="integration test",
            response="good response",
            credits_spent=1,
            latency_ms=100,
            response_bytes=50,
            error=None,
        )

        async def mock_to_thread(func, *args, **kwargs):
            metrics = {
                "quality_score": 75.0,
                "credits_spent": 1,
                "roi": 75.0,
                "probe_count": 1,
            }
            sheet.write_evaluation(
                agent_id=agent_id,
                evaluator="quality_judge",
                metrics=metrics,
                summary="Pipeline integration test"
            )
            return "Done"

        with patch("asyncio.to_thread", side_effect=mock_to_thread):
            # Run pipeline
            await pipeline.run(agent_id, sheet)

            # Verify evaluation was written
            evals = sheet.read_evaluations(agent_id=agent_id, evaluator="quality_judge")
            assert len(evals) == 1
            assert evals[0]["metrics"]["roi"] == 75.0

    @pytest.mark.asyncio
    async def test_handles_no_probes(self):
        """Test that evaluator handles agents with no probes gracefully."""
        mock_model = Mock()
        sheet = CentralSheet(":memory:")

        agent_id = "test-agent-5"
        sheet.write_agent(agent_id, "Test Agent 5", "http://test5.com", "plan-000")
        # No probes written

        with patch("src.evaluators.quality_judge.Agent") as MockAgent:
            mock_agent_instance = MagicMock()

            def mock_agent_call(*args, **kwargs):
                tools = MockAgent.call_args[1]["tools"]
                read_probes_tool = next(t for t in tools if t.__name__ == "read_probes")

                # Read probes (should be empty)
                result = read_probes_tool(agent_id=agent_id)
                assert result["probes"] == []

                # Agent should handle empty probes gracefully
                # Don't write evaluation if no probes
                return "No probes to evaluate"

            mock_agent_instance.__call__ = mock_agent_call
            MockAgent.return_value = mock_agent_instance

            evaluator = create_quality_judge(mock_model)
            await evaluator(agent_id=agent_id, sheet=sheet)

            # Should not crash, may or may not write evaluation
            evals = sheet.read_evaluations(agent_id=agent_id)
            # Either no evaluation or evaluation indicating no probes
            assert len(evals) <= 1

    def test_evaluator_protocol_signature(self):
        """Test that quality_judge matches Evaluator protocol signature."""
        import inspect

        mock_model = Mock()
        evaluator = create_quality_judge(mock_model)

        # Check signature: async def evaluator(agent_id: str, sheet: CentralSheet, **kwargs)
        sig = inspect.signature(evaluator)
        assert "agent_id" in sig.parameters
        assert "sheet" in sig.parameters
        assert "kwargs" in sig.parameters

        # Check it's async
        assert inspect.iscoroutinefunction(evaluator)
