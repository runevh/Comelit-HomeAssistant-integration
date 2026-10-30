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
    runtime = getattr(entry, "runtime_data", None)
    info = runtime.info if runtime else None
    bridge = runtime.bridge if runtime else None
    return {
        "entry_state": entry.state.value,
        "connection_mode": "on-demand video" if bridge else "configuration only",
        "rtsp": bridge.diagnostics() if bridge else {"enabled": False},
        "directory_counts": info.directory_counts if info else None,
        "features": {
            "actuators": False,
            "ring_events": False,
            "video": bool(bridge),
            "audio": False,
        },
    }
