"""Doorbell event entity for Home Assistant automations."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComelitConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: ComelitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose the ring listener when explicitly enabled."""
    if entry.runtime_data.calls:
        async_add_entities([DoorbellEvent(entry)])


class DoorbellEvent(EventEntity):
    """An event for each ring, answer, missed call, and call termination."""

    _attr_has_entity_name = True
    _attr_name = "Doorbell"
    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = [
        "ring",
        "answered",
        "missed_call",
        "call_ended",
    ]
    _attr_should_poll = False

    def __init__(self, entry: ComelitConfigEntry) -> None:
        """Associate with the existing gateway device."""
        self._entry = entry
        self._calls = entry.runtime_data.calls
        self._attr_unique_id = f"{entry.entry_id}_doorbell"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def available(self) -> bool:
        """Reflect listener connectivity."""
        return self._calls.state != "disconnected"

    @property
    def extra_state_attributes(self) -> dict:
        """Expose safe call state for dashboard cards and automations."""
        return {"call_state": self._calls.state, "entry_id": self._entry.entry_id}

    async def async_added_to_hass(self) -> None:
        """Subscribe only while the entity is loaded."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._calls.signal, self._updated)
        )

    @callback
    def _updated(self, data: dict, event_type: str | None) -> None:
        if event_type:
            self._trigger_event(event_type, {"entrance": data.get("entrance")})
        self.async_write_ha_state()
