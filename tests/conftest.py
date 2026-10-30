"""Home Assistant fixtures for custom integration tests."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow loading the integration under test."""


@pytest.fixture(autouse=True)
def _mock_discovery():
    """Never probe the household network from flow tests."""
    with patch(
        "custom_components.comelit_local.config_flow.async_discover_gateways",
        AsyncMock(return_value=[]),
    ):
        yield
