"""Verify bounded discovery and conservative identification without network access."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_local.discovery import (
    async_discover_gateways,
    async_probe_gateway,
    discovery_targets,
)

PREFIX = "custom_components.comelit_local.discovery."


def adapter(address="192.168.1.10", prefix=24, *, enabled=True, default=True):
    return {
        "name": "test",
        "index": 1,
        "enabled": enabled,
        "auto": True,
        "default": default,
        "ipv4": [{"address": address, "network_prefix": prefix}],
        "ipv6": [],
    }


def test_targets_exclude_self_and_broadcast():
    targets = discovery_targets([adapter()])
    assert "192.168.1.200" in targets
    assert "192.168.1.10" not in targets
    assert "192.168.1.0" not in targets
    assert "192.168.1.255" not in targets
    assert len(targets) == 253


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.1.2", "8.8.8.8"])
def test_skip_non_private_networks(address):
    assert discovery_targets([adapter(address)]) == []


def test_skip_disabled_adapter():
    assert discovery_targets([adapter(enabled=False)]) == []


def test_large_network_is_limited_to_local_slash24():
    targets = discovery_targets([adapter("10.1.2.3", 8)])
    assert len(targets) == 253
    assert all(host.startswith("10.1.2.") for host in targets)


def test_small_network_retains_prefix():
    assert discovery_targets([adapter("192.168.1.1", 30)]) == ["192.168.1.2"]


def test_total_limit_and_deduplication():
    targets = discovery_targets(
        [adapter(), adapter(), adapter("10.0.1.2"), adapter("10.0.2.2")]
    )
    assert len(targets) == 512
    assert len(targets) == len(set(targets))


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (b"<title>Extender</title>Comelit <input name='l-pwd'>", True),
        (b"Generic web server", False),
        (b"Comelit camera", False),
        (b"Comelit Extender l-pwd" + b"x" * 65536, False),
    ],
)
async def test_probe_requires_matching_web_page(page, expected):
    writer = MagicMock()
    writer.wait_closed = AsyncMock()
    response = MagicMock()
    response.status = 200

    async def chunks(_size):
        yield page

    response.content.iter_chunked = chunks
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock()
    session = MagicMock()
    session.get.return_value = context
    with patch("asyncio.open_connection", AsyncMock(return_value=(None, writer))):
        assert await async_probe_gateway(session, "192.168.1.200") is expected
    writer.write.assert_not_called()
    session.get.assert_called_once_with(
        "http://192.168.1.200:8080/", allow_redirects=False
    )
    writer.close.assert_called()


async def test_closed_vip_port_skips_http():
    session = MagicMock()
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError)):
        assert not await async_probe_gateway(session, "192.168.1.200")
    session.get.assert_not_called()


async def test_discovery_returns_only_verified_hosts(hass):
    with (
        patch(
            PREFIX + "async_get_adapters",
            AsyncMock(return_value=[adapter("192.168.1.1", 30)]),
        ),
        patch(PREFIX + "async_probe_gateway", AsyncMock(return_value=True)) as probe,
    ):
        assert await async_discover_gateways(hass) == ["192.168.1.2"]
    assert probe.await_count == 1


async def test_adapter_failure_allows_manual_fallback(hass):
    with patch(PREFIX + "async_get_adapters", AsyncMock(side_effect=OSError)):
        assert await async_discover_gateways(hass) == []


async def test_timeout_cancels_probes(hass):
    active = 0

    async def probe(_session, _host):
        nonlocal active
        active += 1
        try:
            await asyncio.sleep(10)
        finally:
            active -= 1

    with (
        patch(PREFIX + "async_get_adapters", AsyncMock(return_value=[adapter()])),
        patch(PREFIX + "async_probe_gateway", probe),
        patch(PREFIX + "_SCAN_TIMEOUT", 0.02),
    ):
        assert await async_discover_gateways(hass) == []
    assert active == 0
