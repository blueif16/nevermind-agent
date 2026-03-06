"""Tests for config module."""
import pytest
from src.config import NVM_ENVIRONMENT


def test_config_loads():
    """Verify config loads and env vars resolve."""
    # Should have a default value even if .env doesn't exist
    assert NVM_ENVIRONMENT in ["sandbox", "staging_sandbox", "live", ""]
    assert isinstance(NVM_ENVIRONMENT, str)
