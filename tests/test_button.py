"""Exercise button discovery and presses through Home Assistant's service API."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local.client import CannotConnect, CommandUncertain
from custom_components.comelit_local.const import DOMAIN

from .test_actuator import INFO
from .test_config_flow import CLIENT, DATA

PRESS = "custom_components.comelit_local.button.async_press_actuator"


async def setup(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(CLIENT, AsyncMock(return_value=INFO)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    return entry, entities


async def test_buttons_grouped_named_and_unloaded(hass):
    with patch(PRESS, AsyncMock()) as press:
        entry, entities = await setup(hass)
        press.assert_not_called()
        assert len(entities) == 3
        assert len({entity.device_id for entity in entities}) == 1
        assert {entity.original_name for entity in entities} == {
            a.name for a in INFO.actuators
        }
        entity = entities[0]
        await hass.services.async_call(
            "button", "press", {"entity_id": entity.entity_id}, blocking=True
        )
        assert press.await_count == 1
        args = press.call_args.args
        assert args[1:] == (DATA["token"], INFO, INFO.actuators[0])
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert hass.states.get(entity.entity_id).state == "unavailable"


@pytest.mark.parametrize("error", [CannotConnect, CommandUncertain])
async def test_service_errors_are_redacted_and_not_retried(hass, error, caplog):
    _, entities = await setup(hass)
    with patch(PRESS, AsyncMock(side_effect=error(DATA["token"]))) as press:
        with pytest.raises(HomeAssistantError) as caught:
            await hass.services.async_call(
                "button", "press", {"entity_id": entities[0].entity_id}, blocking=True
            )
    press.assert_awaited_once()
    assert DATA["token"] not in str(caught.value) + caplog.text


async def test_overlapping_buttons_reject_instead_of_queue(hass):
    _, entities = await setup(hass)
    started = asyncio.Event()
    release = asyncio.Event()

    async def command(*_args):
        started.set()
        await release.wait()

    with patch(PRESS, command):
        first = asyncio.create_task(
            hass.services.async_call(
                "button", "press", {"entity_id": entities[0].entity_id}, blocking=True
            )
        )
        await started.wait()
        with pytest.raises(HomeAssistantError) as caught:
            await hass.services.async_call(
                "button", "press", {"entity_id": entities[1].entity_id}, blocking=True
            )
        assert caught.value.translation_key == "actuator_busy"
        release.set()
        await first
