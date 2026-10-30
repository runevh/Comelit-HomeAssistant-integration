"""Run setup and error paths through Home Assistant's actual flow manager."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local.client import (
    ActivationRejected,
    ActivationUncertain,
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


async def start_token_flow(hass, data=None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    if data is not None:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    return result


async def test_setup_creates_entry_and_device(hass):
    with patch(CLIENT, AsyncMock(return_value=INFO)):
        result = await start_token_flow(hass)
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
        result = await start_token_flow(hass, DATA)
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    assert not hass.config_entries.async_entries(DOMAIN)
    assert DATA["token"] not in caplog.text
    assert "private upstream error" not in caplog.text


async def test_invalid_ip(hass):
    with patch(CLIENT, AsyncMock()) as client:
        result = await start_token_flow(hass, {**DATA, "host": "http://gateway/"})
    assert result["errors"] == {"host": "invalid_host"}
    client.assert_not_called()


async def test_duplicate(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(CLIENT, AsyncMock()) as client:
        result = await start_token_flow(hass, DATA)
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
    started = asyncio.Event()

    async def validate(_self, _token):
        started.set()
        await ready.wait()
        return INFO

    with patch(CLIENT, validate):
        first = asyncio.create_task(start_token_flow(hass, DATA))
        await asyncio.wait_for(started.wait(), 2)
        second = await start_token_flow(hass, DATA)
        assert second["reason"] == "already_in_progress"
        ready.set()
        result = await first
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


ACTIVATE = "custom_components.comelit_local.client.GatewayClient.async_activate"
ACTIVATION_DATA = {
    "host": "127.0.0.1",
    "port": 64100,
    "activation_code": "testcode",
    "description": "Home Assistant Dev",
}


async def start_activation_flow(hass, data=None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "activation"}
    )
    if data is not None:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], data)
    return result


async def test_activation_saves_token_and_registers_device(hass):
    with (
        patch(ACTIVATE, AsyncMock(return_value=DATA["token"])) as activate,
        patch(CLIENT, AsyncMock(return_value=INFO)),
    ):
        result = await start_activation_flow(hass, ACTIVATION_DATA)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].data == DATA
    assert "activation_code" not in result["result"].data
    activate.assert_awaited_once_with("testcode", "Home Assistant Dev")
    assert dr.async_entries_for_config_entry(
        dr.async_get(hass), result["result"].entry_id
    )


async def test_activation_token_survives_config_connection_failure(hass):
    with (
        patch(ACTIVATE, AsyncMock(return_value=DATA["token"])) as activate,
        patch(CLIENT, AsyncMock(side_effect=CannotConnect)),
    ):
        result = await start_activation_flow(hass, ACTIVATION_DATA)
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["result"].data["token"] == DATA["token"]
    activate.assert_awaited_once()


@pytest.mark.parametrize(
    ("failure", "error"),
    [(CannotConnect, "cannot_connect"), (ActivationRejected, "activation_rejected")],
)
async def test_activation_failure(hass, failure, error):
    with patch(ACTIVATE, AsyncMock(side_effect=failure)):
        result = await start_activation_flow(hass, ACTIVATION_DATA)
    assert result["errors"] == {"base": error}
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_activation_uncertain_stops_without_retry(hass):
    with patch(ACTIVATE, AsyncMock(side_effect=ActivationUncertain)) as activate:
        result = await start_activation_flow(hass, ACTIVATION_DATA)
    assert result["reason"] == "activation_uncertain"
    activate.assert_awaited_once()


async def test_activation_duplicate_does_not_redeem_code(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(ACTIVATE, AsyncMock()) as activate:
        result = await start_activation_flow(hass, ACTIVATION_DATA)
    assert result["reason"] == "already_configured"
    activate.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "field", "error"),
    [
        ({"host": "not an IP"}, "host", "invalid_host"),
        ({"activation_code": ""}, "activation_code", "invalid_code"),
        ({"description": "   "}, "description", "invalid_name"),
    ],
)
async def test_activation_input_validation(hass, updates, field, error):
    with patch(ACTIVATE, AsyncMock()) as activate:
        result = await start_activation_flow(hass, {**ACTIVATION_DATA, **updates})
    assert result["errors"] == {field: error}
    activate.assert_not_called()
