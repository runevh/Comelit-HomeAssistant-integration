"""Run setup and error paths through Home Assistant's actual flow manager."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local.client import (
    CannotConnect,
    GatewayInfo,
    InvalidAuth,
    InvalidResponse,
)
from custom_components.comelit_local.const import DOMAIN
from custom_components.comelit_local.diagnostics import (
    async_get_config_entry_diagnostics,
)

DATA = {"host": "127.0.0.1", "port": 64100, "token": "0123456789abcdef0123456789abcdef"}
INFO = GatewayInfo("00000001", 8, {"actuator-address-book": 3})
CLIENT = "custom_components.comelit_local.client.GatewayClient.async_get_configuration"


async def test_setup_creates_entry_and_device(hass):
    with patch(CLIENT, AsyncMock(return_value=INFO)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(result["flow_id"], DATA)
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    entry = result["result"]
    assert entry.data == DATA
    assert entry.runtime_data == INFO
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert devices[0].manufacturer == "Comelit"
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (CannotConnect, "cannot_connect"),
        (InvalidAuth, "invalid_auth"),
        (InvalidResponse, "invalid_response"),
    ],
)
async def test_flow_errors(hass, error, expected, caplog):
    with patch(CLIENT, AsyncMock(side_effect=error("private upstream error"))):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=DATA
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert DATA["token"] not in caplog.text
    assert "private upstream error" not in caplog.text


async def test_invalid_ip(hass):
    with patch(CLIENT, AsyncMock()) as client:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={**DATA, "host": "http://gateway/"},
        )
    assert result["errors"] == {"host": "invalid_host"}
    client.assert_not_called()


async def test_duplicate(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(CLIENT, AsyncMock()) as client:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}, data=DATA
        )
    assert result["reason"] == "already_configured"
    client.assert_not_called()


@pytest.mark.parametrize("source", [SOURCE_RECONFIGURE, SOURCE_REAUTH])
async def test_update_existing_entry(hass, source):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    updated = {**DATA, "token": "f" * 32}
    with patch(CLIENT, AsyncMock(return_value=INFO)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source, "entry_id": entry.entry_id}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], updated
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert entry.data["token"] == updated["token"]
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize("error", [CannotConnect, InvalidAuth, InvalidResponse])
async def test_setup_error(hass, error):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(CLIENT, AsyncMock(side_effect=error)):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)


async def test_diagnostics_exclude_credentials_and_identity(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = INFO
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["directory_counts"] == INFO.directory_counts
    for private in (DATA["token"], DATA["host"], INFO.apartment):
        assert private not in str(diagnostics)


async def test_diagnostics_before_successful_setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["directory_counts"] is None


async def test_concurrent_flows_do_not_duplicate_entries(hass):
    ready = asyncio.Event()
    both_started = asyncio.Event()
    calls = 0

    async def validate(_self, _token):
        nonlocal calls
        calls += 1
        if calls == 2:
            both_started.set()
        await ready.wait()
        return INFO

    with patch(CLIENT, validate):
        first = asyncio.create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}, data=DATA
            )
        )
        second = asyncio.create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}, data=DATA
            )
        )
        await asyncio.wait_for(both_started.wait(), 2)
        ready.set()
        results = await asyncio.gather(first, second)
        await hass.async_block_till_done()
    assert {result["type"] for result in results} == {
        FlowResultType.CREATE_ENTRY,
        FlowResultType.ABORT,
    }
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
