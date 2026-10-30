"""Home Assistant fixtures for custom integration tests."""

import pytest


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations):
    """Allow loading the integration under test."""
