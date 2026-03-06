"""Comprehensive tests for scanner module with mocked HTTP calls."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from src.central_sheet import CentralSheet
from src.scanner import (
    _merge_agents,
    discover_from_hackathon_api,
    discover_from_sdk,
    load_known_agents,
    probe_pricing,
)


@pytest.fixture
def sheet():
    """Create an in-memory CentralSheet for testing."""
    return CentralSheet(":memory:")


@pytest.fixture
def discovery_api_response():
    """Load discovery API response fixture."""
    with open("tests/fixtures/discovery_api_response.json") as f:
        return json.load(f)


@pytest.fixture
def mock_payments():
    """Create a mock Payments SDK instance."""
    payments = MagicMock()
    payments.agents = MagicMock()
    return payments


# ── Discovery API Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_from_hackathon_api_success(discovery_api_response):
    """Test successful discovery from Hackathon API with agent parsing."""
    mock_response = MagicMock()
    mock_response.json.return_value = discovery_api_response
    mock_response.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        agents = await discover_from_hackathon_api("test_api_key")

    assert len(agents) == 3
    assert agents[0]["agent_id"] == "did:nv:weather_agent_123"
    assert agents[0]["name"] == "Weather Insights Agent"
    assert agents[0]["url"] == "https://weather.example.com"
    assert agents[0]["plan_id"] == "plan_weather_001"
    assert agents[0]["category"] == "Data Services"
    assert agents[0]["team_name"] == "Climate Analytics Team"


@pytest.mark.asyncio
async def test_discover_from_hackathon_api_missing_endpoint():
    """Test that agents without endpointUrl and nvmAgentId are skipped."""
    api_response = [
        {
            "name": "Valid Agent",
            "nvmAgentId": "did:nv:valid",
            "endpointUrl": "https://valid.example.com",
            "planIds": ["plan_001"],
        },
        {
            "name": "Invalid Agent",
            "planIds": ["plan_002"],
            # Missing both nvmAgentId and endpointUrl
        },
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = api_response
    mock_response.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        agents = await discover_from_hackathon_api("test_api_key")

    assert len(agents) == 1
    assert agents[0]["agent_id"] == "did:nv:valid"


@pytest.mark.asyncio
async def test_discover_from_hackathon_api_fallback_agent_id():
    """Test that endpointUrl is used as fallback agent ID when nvmAgentId is missing."""
    api_response = [
        {
            "name": "Agent Without ID",
            "endpointUrl": "https://fallback.example.com",
            "planIds": ["plan_001"],
        }
    ]

    mock_response = MagicMock()
    mock_response.json.return_value = api_response
    mock_response.raise_for_status = MagicMock()

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        agents = await discover_from_hackathon_api("test_api_key")

    assert len(agents) == 1
    assert agents[0]["agent_id"] == "https://fallback.example.com"


@pytest.mark.asyncio
async def test_discover_from_hackathon_api_error_handling():
    """Test error handling when API call fails."""
    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        agents = await discover_from_hackathon_api("test_api_key")

    assert agents == []


# ── SDK Discovery Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_from_sdk(mock_payments):
    """Test SDK enrichment with endpoint URL extraction."""
    agent_data = {
        "name": "SDK Agent",
        "description": "Agent from SDK",
        "plan_id": "plan_sdk_001",
        "endpoints": [
            {"method": "GET", "url": "https://sdk.example.com/get"},
            {"method": "POST", "url": "https://sdk.example.com/post"},
        ],
    }

    mock_payments.agents.get_agent.return_value = agent_data

    agents = await discover_from_sdk(mock_payments, ["did:nv:sdk_agent"])

    assert len(agents) == 1
    assert agents[0]["agent_id"] == "did:nv:sdk_agent"
    assert agents[0]["name"] == "SDK Agent"
    assert agents[0]["url"] == "https://sdk.example.com/post"
    assert agents[0]["plan_id"] == "plan_sdk_001"


@pytest.mark.asyncio
async def test_discover_from_sdk_no_post_endpoint(mock_payments):
    """Test SDK enrichment when no POST endpoint is available."""
    agent_data = {
        "name": "SDK Agent",
        "endpoints": [
            {"method": "GET", "url": "https://sdk.example.com/get"},
        ],
    }

    mock_payments.agents.get_agent.return_value = agent_data

    agents = await discover_from_sdk(mock_payments, ["did:nv:sdk_agent"])

    assert len(agents) == 1
    assert agents[0]["url"] == ""


@pytest.mark.asyncio
async def test_discover_from_sdk_error_handling(mock_payments):
    """Test SDK enrichment error handling."""
    mock_payments.agents.get_agent.side_effect = Exception("SDK error")

    agents = await discover_from_sdk(mock_payments, ["did:nv:agent1", "did:nv:agent2"])

    assert agents == []


# ── Config Loading Tests ────────────────────────────────────────


def test_load_known_agents(tmp_path):
    """Test loading agents from config file."""
    config_file = tmp_path / "agents_config.json"
    config_data = [
        {
            "agent_id": "did:nv:config1",
            "name": "Config Agent 1",
            "url": "https://config1.example.com",
            "plan_id": "plan_config_001",
        },
        {
            "agent_id": "did:nv:config2",
            "name": "Config Agent 2",
            "url": "https://config2.example.com",
            "plan_id": "plan_config_002",
        },
    ]
    config_file.write_text(json.dumps(config_data))

    agents = load_known_agents(str(config_file))

    assert len(agents) == 2
    assert agents[0]["agent_id"] == "did:nv:config1"
    assert agents[1]["agent_id"] == "did:nv:config2"


def test_load_known_agents_missing_file():
    """Test that missing config file returns empty list."""
    agents = load_known_agents("/nonexistent/path/agents_config.json")
    assert agents == []


def test_load_known_agents_invalid_json(tmp_path):
    """Test that invalid JSON returns empty list."""
    config_file = tmp_path / "invalid.json"
    config_file.write_text("not valid json")

    agents = load_known_agents(str(config_file))
    assert agents == []


def test_load_known_agents_non_array(tmp_path):
    """Test that non-array JSON returns empty list."""
    config_file = tmp_path / "non_array.json"
    config_file.write_text('{"key": "value"}')

    agents = load_known_agents(str(config_file))
    assert agents == []


# ── Pricing Probe Tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_pricing_success():
    """Test successful pricing probe."""
    pricing_data = {
        "planId": "plan_pricing_001",
        "tiers": {
            "basic": {"credits": 100, "description": "Basic tier"},
            "pro": {"credits": 500, "description": "Pro tier"},
        },
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = pricing_data

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        result = await probe_pricing("https://test.example.com")

    assert result["status"] == "success"
    assert result["plan_id"] == "plan_pricing_001"
    assert result["tiers"] == pricing_data["tiers"]


@pytest.mark.asyncio
async def test_probe_pricing_http_error():
    """Test pricing probe with HTTP error."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        result = await probe_pricing("https://test.example.com")

    assert result["status"] == "error"
    assert result["tiers"] == {}


@pytest.mark.asyncio
async def test_probe_pricing_exception():
    """Test pricing probe with exception."""
    mock_client_instance = AsyncMock()
    mock_client_instance.get = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value = mock_client_instance

        result = await probe_pricing("https://test.example.com")

    assert result["status"] == "error"
    assert result["tiers"] == {}


# ── Merge Logic Tests ────────────────────────────────────────


def test_merge_agents_priority_config_lowest():
    """Test merge with config as lowest priority."""
    config = [
        {
            "agent_id": "did:nv:agent1",
            "name": "Config Name",
            "url": "https://config.example.com",
            "plan_id": "plan_config",
        }
    ]
    sdk = [
        {
            "agent_id": "did:nv:agent1",
            "name": "SDK Name",
            "url": "https://sdk.example.com",
        }
    ]
    api = []

    merged = _merge_agents(config, sdk, api)

    assert len(merged) == 1
    assert merged[0]["name"] == "SDK Name"
    assert merged[0]["url"] == "https://sdk.example.com"
    assert merged[0]["plan_id"] == "plan_config"  # Preserved from config


def test_merge_agents_priority_api_highest():
    """Test merge with API as highest priority."""
    config = [
        {
            "agent_id": "did:nv:agent1",
            "name": "Config Name",
            "url": "https://config.example.com",
        }
    ]
    sdk = [
        {
            "agent_id": "did:nv:agent1",
            "name": "SDK Name",
            "url": "https://sdk.example.com",
        }
    ]
    api = [
        {
            "agent_id": "did:nv:agent1",
            "name": "API Name",
            "url": "https://api.example.com",
            "category": "AI/ML",
        }
    ]

    merged = _merge_agents(config, sdk, api)

    assert len(merged) == 1
    assert merged[0]["name"] == "API Name"
    assert merged[0]["url"] == "https://api.example.com"
    assert merged[0]["category"] == "AI/ML"


def test_merge_agents_multiple_sources():
    """Test merge with agents from different sources."""
    config = [{"agent_id": "did:nv:agent1", "name": "Agent 1"}]
    sdk = [{"agent_id": "did:nv:agent2", "name": "Agent 2"}]
    api = [{"agent_id": "did:nv:agent3", "name": "Agent 3"}]

    merged = _merge_agents(config, sdk, api)

    assert len(merged) == 3
    agent_ids = {a["agent_id"] for a in merged}
    assert agent_ids == {"did:nv:agent1", "did:nv:agent2", "did:nv:agent3"}


def test_merge_agents_empty_values_not_overwritten():
    """Test that empty values from higher priority don't overwrite existing values."""
    config = [
        {
            "agent_id": "did:nv:agent1",
            "name": "Config Name",
            "url": "https://config.example.com",
        }
    ]
    sdk = [
        {
            "agent_id": "did:nv:agent1",
            "name": "",  # Empty name
            "description": "SDK Description",
        }
    ]
    api = []

    merged = _merge_agents(config, sdk, api)

    assert len(merged) == 1
    assert merged[0]["name"] == "Config Name"  # Not overwritten by empty SDK value
    assert merged[0]["description"] == "SDK Description"
