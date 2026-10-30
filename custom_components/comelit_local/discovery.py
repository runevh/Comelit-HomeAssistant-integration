"""Bounded, read-only discovery on enabled local IPv4 networks."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from ipaddress import IPv4Address, IPv4Network, ip_interface
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.components.network import async_get_adapters

from .const import DEFAULT_PORT

if TYPE_CHECKING:
    from homeassistant.components.network.models import Adapter
    from homeassistant.core import HomeAssistant

_PRIVATE_NETWORKS = tuple(
    IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_MAX_TARGETS = 512
_MAX_PAGE_BYTES = 65536
_CONCURRENCY = 32
_PROBE_TIMEOUT = 0.6
_SCAN_TIMEOUT = 10


def discovery_targets(adapters: list[Adapter]) -> list[str]:
    """Limit each enabled private interface to its local /24 or smaller subnet."""
    targets: set[IPv4Address] = set()
    own_addresses = {
        IPv4Address(info["address"]) for adapter in adapters for info in adapter["ipv4"]
    }
    for adapter in sorted(adapters, key=lambda item: not item["default"]):
        if not adapter["enabled"]:
            continue
        for info in adapter["ipv4"]:
            interface = ip_interface(
                f"{info['address']}/{max(info['network_prefix'], 24)}"
            )
            if not isinstance(interface.ip, IPv4Address) or not any(
                interface.ip in network for network in _PRIVATE_NETWORKS
            ):
                continue
            for host in interface.network.hosts():
                if host not in own_addresses:
                    targets.add(host)
                if len(targets) >= _MAX_TARGETS:
                    return [str(host) for host in sorted(targets)]
    return [str(host) for host in sorted(targets)]


async def async_probe_gateway(session: aiohttp.ClientSession, host: str) -> bool:
    """Require a ViP listener and a matching public Comelit web login page."""
    writer = None
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT):
            _, writer = await asyncio.open_connection(host, DEFAULT_PORT)
            # Do not authenticate or send protocol commands while discovering.
            writer.close()
            await writer.wait_closed()
        async with session.get(
            f"http://{host}:8080/", allow_redirects=False
        ) as response:
            if response.status != HTTPStatus.OK:
                return False
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(4096):
                chunks.extend(chunk)
                if len(chunks) > _MAX_PAGE_BYTES:
                    return False
            page = chunks.decode("utf-8", errors="replace").lower()
            return "comelit" in page and "l-pwd" in page and "extender" in page
    except TimeoutError, OSError, aiohttp.ClientError:
        return False
    finally:
        if writer is not None:
            writer.close()


async def async_discover_gateways(hass: HomeAssistant) -> list[str]:
    """Search only during setup; return candidates without claiming model identity."""
    found: list[str] = []
    try:
        async with asyncio.timeout(_SCAN_TIMEOUT):
            targets = discovery_targets(await async_get_adapters(hass))
            semaphore = asyncio.Semaphore(_CONCURRENCY)
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=1.5),
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:

                async def probe(host: str) -> None:
                    async with semaphore:
                        if await async_probe_gateway(session, host):
                            found.append(host)

                async with asyncio.TaskGroup() as group:
                    for host in targets:
                        group.create_task(probe(host))
    except TimeoutError, OSError:
        # A limited/isolated network must not prevent manual setup.
        pass
    return sorted(found, key=IPv4Address)
