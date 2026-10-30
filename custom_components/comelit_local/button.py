"""Momentary actuator buttons; no inferred gate position."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .actuator import async_press_actuator
from .client import (
    CannotConnect,
    CommandRejected,
    CommandUncertain,
    GatewayClient,
    GatewayError,
    InvalidAuth,
    UnsupportedActuator,
)
from .const import CONF_TOKEN, DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComelitConfigEntry
    from .client import Actuator

_ERRORS = {
    CannotConnect: "actuator_cannot_connect",
    InvalidAuth: "actuator_invalid_auth",
    CommandRejected: "actuator_rejected",
    CommandUncertain: "actuator_uncertain",
    UnsupportedActuator: "actuator_unsupported",
}


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: ComelitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Discover only the actuator directory, sharing one command guard."""
    lock = asyncio.Lock()
    async_add_entities(
        ActuatorButton(entry, actuator, lock)
        for actuator in entry.runtime_data.actuators
    )


class ActuatorButton(ButtonEntity):
    """Send a single actuator request without retries or queued presses."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:gesture-tap-button"

    def __init__(
        self,
        entry: ComelitConfigEntry,
        actuator: Actuator,
        lock: asyncio.Lock,
    ) -> None:
        """Keep the configured target and shared gateway guard."""
        self._entry = entry
        self._actuator = actuator
        self._lock = lock
        self._attr_name = actuator.name
        self._attr_unique_id = f"{entry.entry_id}_actuator_{actuator.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_press(self) -> None:
        """Operate the configured output; report failures without private payloads."""
        if self._lock.locked():
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="actuator_busy"
            )
        async with self._lock:
            try:
                await async_press_actuator(
                    GatewayClient(
                        self._entry.data[CONF_HOST], self._entry.data[CONF_PORT]
                    ),
                    self._entry.data[CONF_TOKEN],
                    self._entry.runtime_data,
                    self._actuator,
                )
            except GatewayError as err:
                if isinstance(err, InvalidAuth):
                    self._entry.async_start_reauth(self.hass)
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key=_ERRORS.get(type(err), "actuator_failed"),
                ) from None
