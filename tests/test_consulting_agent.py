"""Tests for consulting agent."""
import pytest
from unittest.mock import Mock, patch, MagicMock

from src.consulting_agent import create_consulting_agent


class TestConsultingAgent:
    """Test suite for consulting agent with mocked dependencies."""

    @pytest.fixture
    def mock_model(self):
        """Mock BedrockModel."""
        model = Mock()
        model.model_id = "us.anthropic.claude-sonnet-4-6"
        return model

    @pytest.fixture
    def mock_sheet(self):
        """Mock CentralSheet."""
        sheet = Mock()
        sheet.read_portfolio.return_value = [
            {
                "agent_id": "agent1",
                "name": "Test Agent 1",
                "url": "http://agent1.test",
                "plan_id": "plan1",
                "probe_count": 5,
                "avg_cost": 2.0,
            }
        ]
        sheet.get_pnl.return_value = {
            "revenue": 100,
            "spent": 50,
            "margin": 50,
        }
        sheet.read_probes.return_value = [
            {
                "probe_id": 1,
                "agent_id": "agent1",
                "query": "test query",
                "response": "test response",
                "credits_spent": 2,
                "error": None,
            }
        ]
        sheet.read_evaluations.return_value = [
            {
                "eval_id": 1,
                "agent_id": "agent1",
                "evaluator": "gate",
                "metrics": {"pass": True},
                "summary": "Passed",
            }
        ]
        sheet.read_agents.return_value = [
            {
                "agent_id": "agent1",
                "name": "Test Agent 1",
                "url": "http://agent1.test",
                "plan_id": "plan1",
            }
        ]
        return sheet

    @pytest.fixture
    def mock_payments(self):
        """Mock Payments SDK."""
        payments = Mock()
        payments.account_address = "0x123"
        return payments

    def test_create_consulting_agent(self, mock_model, mock_sheet, mock_payments):
        """Test that consulting agent is created with correct tools."""
        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
            agent_id="test_agent",
        )

        assert agent is not None
        assert len(agent.tool_names) == 4
        assert "consulting_query" in agent.tool_names
        assert "read_portfolio" in agent.tool_names
        assert "get_agent_report" in agent.tool_names
        assert "buy_from_agent" in agent.tool_names

    def test_read_portfolio_tool(self, mock_model, mock_sheet, mock_payments):
        """Test read_portfolio tool returns portfolio data."""
        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        # Access tool via tool_registry.registry
        read_portfolio = agent.tool_registry.registry["read_portfolio"]
        result = read_portfolio()

        assert result["agent_count"] == 1
        assert result["pnl"]["revenue"] == 100
        assert len(result["portfolio"]) == 1
        mock_sheet.read_portfolio.assert_called_once()
        mock_sheet.get_pnl.assert_called_once()

    def test_get_agent_report_tool(self, mock_model, mock_sheet, mock_payments):
        """Test get_agent_report tool returns agent details."""
        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        get_agent_report = agent.tool_registry.registry["get_agent_report"]
        result = get_agent_report("agent1")

        assert result["agent"]["agent_id"] == "agent1"
        assert len(result["probes"]) == 1
        assert len(result["evaluations"]) == 1
        mock_sheet.read_probes.assert_called_once_with(agent_id="agent1", limit=20)
        mock_sheet.read_evaluations.assert_called_once_with(
            agent_id="agent1", limit=20
        )

    @patch("src.consulting_agent.purchase_data_impl")
    def test_buy_from_agent_success(
        self, mock_purchase, mock_model, mock_sheet, mock_payments
    ):
        """Test buy_from_agent tool with successful purchase."""
        mock_purchase.return_value = {
            "status": "success",
            "content": [{"text": "response"}],
            "response": "response",
            "credits_used": 2,
        }

        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        buy_from_agent = agent.tool_registry.registry["buy_from_agent"]
        result = buy_from_agent("agent1", "test query")

        assert result["status"] == "success"
        assert result["credits_used"] == 2
        mock_purchase.assert_called_once()
        mock_sheet.write_ledger.assert_called_once_with(
            direction="out",
            credits=2,
            purpose="consulting_upstream",
            agent_id="agent1",
            detail="test query",
        )

    @patch("src.consulting_agent.purchase_data_impl")
    def test_buy_from_agent_not_found(
        self, mock_purchase, mock_model, mock_sheet, mock_payments
    ):
        """Test buy_from_agent tool with agent not found."""
        mock_sheet.read_agents.return_value = []

        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        buy_from_agent = agent.tool_registry.registry["buy_from_agent"]
        result = buy_from_agent("nonexistent", "test query")

        assert result["status"] == "error"
        assert "not found" in result["content"][0]["text"]
        mock_purchase.assert_not_called()
        mock_sheet.write_ledger.assert_not_called()

    @patch("src.consulting_agent.purchase_data_impl")
    def test_buy_from_agent_no_ledger_on_failure(
        self, mock_purchase, mock_model, mock_sheet, mock_payments
    ):
        """Test buy_from_agent doesn't write ledger on purchase failure."""
        mock_purchase.return_value = {
            "status": "error",
            "content": [{"text": "error"}],
            "credits_used": 0,
        }

        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        buy_from_agent = agent.tool_registry.registry["buy_from_agent"]
        result = buy_from_agent("agent1", "test query")

        assert result["status"] == "error"
        mock_sheet.write_ledger.assert_not_called()

    def test_consulting_query_tool_exists(self, mock_model, mock_sheet, mock_payments):
        """Test that consulting_query tool is present with payment decorator."""
        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
            agent_id="test_agent",
        )

        assert "consulting_query" in agent.tool_names
        # The @requires_payment decorator wraps the function, so we can't easily
        # test it without a full integration test. Just verify it exists.

    def test_agent_has_system_prompt(self, mock_model, mock_sheet, mock_payments):
        """Test that agent has a system prompt configured."""
        agent = create_consulting_agent(
            model=mock_model,
            sheet=mock_sheet,
            payments=mock_payments,
            plan_id="test_plan",
        )

        assert agent.system_prompt is not None
        assert len(agent.system_prompt) > 0
        assert "consulting agent" in agent.system_prompt.lower()
        assert "marketplace" in agent.system_prompt.lower()
