"""Tests for buy_impl module."""
import base64
import json
from unittest.mock import Mock, patch

import httpx
import pytest

from src.buy_impl import (
    build_token_options,
    check_balance_impl,
    discover_pricing_impl,
    purchase_data_impl,
)


# Tier 2: Test against smoke seller from Slice 2 (manual)


class TestPurchaseDataImpl:
    """Tests for purchase_data_impl function."""

    @patch("src.buy_impl.build_token_options")
    @patch("httpx.Client")
    def test_success_case(self, mock_client_class, mock_build_token):
        """Test successful data purchase with HTTP 200."""
        # Setup mocks
        mock_payments = Mock()
        mock_payments.x402.get_x402_access_token.return_value = {
            "accessToken": "test-token-123"
        }
        mock_build_token.return_value = Mock()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "response": "Portfolio analysis complete",
            "credits_used": 5,
        }

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = purchase_data_impl(
            payments=mock_payments,
            plan_id="plan-123",
            seller_url="http://localhost:3000",
            query="What is the best agent?",
            agent_id="agent-456",
        )

        # Verify
        assert result["status"] == "success"
        assert result["response"] == "Portfolio analysis complete"
        assert result["credits_used"] == 5
        assert len(result["content"]) == 1
        assert result["content"][0]["text"] == "Portfolio analysis complete"

        mock_payments.x402.get_x402_access_token.assert_called_once_with(
            plan_id="plan-123",
            agent_id="agent-456",
            token_options=mock_build_token.return_value,
        )
        mock_client.post.assert_called_once()

    @patch("src.buy_impl.build_token_options")
    @patch("httpx.Client")
    def test_payment_required_with_header(self, mock_client_class, mock_build_token):
        """Test HTTP 402 response with payment-required header."""
        # Setup mocks
        mock_payments = Mock()
        mock_payments.x402.get_x402_access_token.return_value = {
            "accessToken": "test-token-123"
        }
        mock_build_token.return_value = Mock()

        payment_details = {
            "planId": "plan-123",
            "creditsRequired": 10,
            "reason": "Insufficient balance",
        }
        encoded_header = base64.b64encode(json.dumps(payment_details).encode()).decode()

        mock_response = Mock()
        mock_response.status_code = 402
        mock_response.headers = {"payment-required": encoded_header}

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = purchase_data_impl(
            payments=mock_payments,
            plan_id="plan-123",
            seller_url="http://localhost:3000",
            query="Test query",
        )

        # Verify
        assert result["status"] == "payment_required"
        assert result["credits_used"] == 0
        assert len(result["content"]) == 1
        assert "Payment required (HTTP 402)" in result["content"][0]["text"]
        assert "planId" in result["content"][0]["text"]

    @patch("src.buy_impl.build_token_options")
    @patch("httpx.Client")
    def test_connection_error(self, mock_client_class, mock_build_token):
        """Test httpx.ConnectError handling."""
        # Setup mocks
        mock_payments = Mock()
        mock_payments.x402.get_x402_access_token.return_value = {
            "accessToken": "test-token-123"
        }
        mock_build_token.return_value = Mock()

        mock_client = Mock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = purchase_data_impl(
            payments=mock_payments,
            plan_id="plan-123",
            seller_url="http://localhost:3000",
            query="Test query",
        )

        # Verify
        assert result["status"] == "error"
        assert result["credits_used"] == 0
        assert "Cannot connect to http://localhost:3000" in result["content"][0]["text"]

    @patch("src.buy_impl.build_token_options")
    @patch("httpx.Client")
    def test_http_500_error(self, mock_client_class, mock_build_token):
        """Test HTTP 500 error handling."""
        # Setup mocks
        mock_payments = Mock()
        mock_payments.x402.get_x402_access_token.return_value = {
            "accessToken": "test-token-123"
        }
        mock_build_token.return_value = Mock()

        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"

        mock_client = Mock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = purchase_data_impl(
            payments=mock_payments,
            plan_id="plan-123",
            seller_url="http://localhost:3000",
            query="Test query",
        )

        # Verify
        assert result["status"] == "error"
        assert "HTTP 500" in result["content"][0]["text"]

    @patch("src.buy_impl.build_token_options")
    def test_token_generation_failure(self, mock_build_token):
        """Test when access token generation fails."""
        # Setup mocks
        mock_payments = Mock()
        mock_payments.x402.get_x402_access_token.return_value = {}
        mock_build_token.return_value = Mock()

        # Execute
        result = purchase_data_impl(
            payments=mock_payments,
            plan_id="plan-123",
            seller_url="http://localhost:3000",
            query="Test query",
        )

        # Verify
        assert result["status"] == "error"
        assert "Failed to generate x402 access token" in result["content"][0]["text"]


class TestCheckBalanceImpl:
    """Tests for check_balance_impl function."""

    def test_success_case(self):
        """Test successful balance check."""
        # Setup mock
        mock_payments = Mock()
        mock_result = Mock()
        mock_result.balance = 1000
        mock_result.is_subscriber = True
        mock_payments.plans.get_plan_balance.return_value = mock_result

        # Execute
        result = check_balance_impl(payments=mock_payments, plan_id="plan-123")

        # Verify
        assert result["status"] == "success"
        assert result["balance"] == 1000
        assert result["is_subscriber"] is True
        mock_payments.plans.get_plan_balance.assert_called_once_with("plan-123")

    def test_error_case(self):
        """Test exception handling in balance check."""
        # Setup mock
        mock_payments = Mock()
        mock_payments.plans.get_plan_balance.side_effect = Exception("API error")

        # Execute
        result = check_balance_impl(payments=mock_payments, plan_id="plan-123")

        # Verify
        assert result["status"] == "error"
        assert result["balance"] == 0
        assert result["is_subscriber"] is False


class TestDiscoverPricingImpl:
    """Tests for discover_pricing_impl function."""

    @patch("httpx.Client")
    def test_success_case(self, mock_client_class):
        """Test successful pricing discovery with HTTP 200."""
        # Setup mock
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "planId": "plan-123",
            "tiers": {
                "consulting": {"credits": 5, "description": "Portfolio consulting"},
                "analysis": {"credits": 10, "description": "Deep analysis"},
            },
        }

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = discover_pricing_impl(seller_url="http://localhost:3000")

        # Verify
        assert result["status"] == "success"
        assert result["plan_id"] == "plan-123"
        assert "consulting" in result["tiers"]
        assert "analysis" in result["tiers"]
        mock_client.get.assert_called_once_with("http://localhost:3000/pricing")

    @patch("httpx.Client")
    def test_connection_failure(self, mock_client_class):
        """Test connection error handling."""
        # Setup mock
        mock_client = Mock()
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = discover_pricing_impl(seller_url="http://localhost:3000")

        # Verify
        assert result["status"] == "error"
        assert result["tiers"] == {}

    @patch("httpx.Client")
    def test_non_200_status(self, mock_client_class):
        """Test non-200 HTTP status handling."""
        # Setup mock
        mock_response = Mock()
        mock_response.status_code = 404

        mock_client = Mock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = Mock(return_value=mock_client)
        mock_client.__exit__ = Mock(return_value=False)
        mock_client_class.return_value = mock_client

        # Execute
        result = discover_pricing_impl(seller_url="http://localhost:3000")

        # Verify
        assert result["status"] == "error"
        assert result["tiers"] == {}


class TestBuildTokenOptions:
    """Tests for build_token_options function."""

    @patch("src.buy_impl.resolve_scheme")
    def test_non_card_delegation_scheme(self, mock_resolve_scheme):
        """Test token options for non-card-delegation scheme."""
        # Setup mock
        mock_payments = Mock()
        mock_resolve_scheme.return_value = "nvm:credits"

        # Execute
        result = build_token_options(payments=mock_payments, plan_id="plan-123")

        # Verify
        assert result.scheme == "nvm:credits"
        assert result.delegation_config is None
        mock_resolve_scheme.assert_called_once_with(mock_payments, "plan-123")

    @patch("src.buy_impl.resolve_scheme")
    def test_card_delegation_with_payment_methods(self, mock_resolve_scheme):
        """Test token options for card delegation scheme with payment methods."""
        # Setup mock
        mock_payments = Mock()
        mock_resolve_scheme.return_value = "nvm:card-delegation"

        mock_payment_method = Mock()
        mock_payment_method.id = "pm-123"
        mock_payments.delegation.list_payment_methods.return_value = [mock_payment_method]

        # Execute
        result = build_token_options(payments=mock_payments, plan_id="plan-123")

        # Verify
        assert result.scheme == "nvm:card-delegation"
        assert result.delegation_config is not None
        assert result.delegation_config.provider_payment_method_id == "pm-123"
        assert result.delegation_config.spending_limit_cents == 10_000
        assert result.delegation_config.duration_secs == 604_800
        assert result.delegation_config.currency == "usd"

    @patch("src.buy_impl.resolve_scheme")
    def test_card_delegation_without_payment_methods(self, mock_resolve_scheme):
        """Test card delegation scheme raises error when no payment methods."""
        # Setup mock
        mock_payments = Mock()
        mock_resolve_scheme.return_value = "nvm:card-delegation"
        mock_payments.delegation.list_payment_methods.return_value = []

        # Execute and verify
        with pytest.raises(ValueError, match="Fiat plan requires payment method"):
            build_token_options(payments=mock_payments, plan_id="plan-123")
