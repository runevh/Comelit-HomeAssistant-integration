"""Diagnostics limited to non-identifying configuration counts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import ComelitConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,  # noqa: ARG001 - Home Assistant signature
    entry: ComelitConfigEntry,
) -> dict[str, Any]:
    """Return only explicit safe fields, never config entry data or raw replies."""
    info = getattr(entry, "runtime_data", None)
    return {
        "entry_state": entry.state.value,
        "connection_mode": "configuration read on setup; short-lived actuator sessions",
        "directory_counts": info.directory_counts if info else None,
        "features": {
            "actuators": True,
            "ring_events": False,
            "video": False,
            "audio": False,
        },
    }
