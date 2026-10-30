"""Set up Comelit Local using validated, short-lived gateway sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, EVENT_HOMEASSISTANT_STOP
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .client import (
    CannotConnect,
    GatewayClient,
    GatewayInfo,
    InvalidAuth,
    InvalidResponse,
)
from .const import CONF_TOKEN, DOMAIN
from .video import VideoBridge

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant


@dataclass
class RuntimeData:
    """Keep gateway metadata and optional video lifecycle together."""

    info: GatewayInfo
    bridge: VideoBridge | None = None


type ComelitConfigEntry = ConfigEntry[RuntimeData]


async def _async_options_updated(
    hass: HomeAssistant, entry: ComelitConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ComelitConfigEntry) -> bool:
    """Read the gateway configuration and register its device."""
    try:
        info = await GatewayClient(
            entry.data[CONF_HOST], entry.data[CONF_PORT]
        ).async_get_configuration(entry.data[CONF_TOKEN])
    except InvalidAuth:
        raise ConfigEntryAuthFailed("Gateway rejected the token") from None
    except CannotConnect, InvalidResponse:
        raise ConfigEntryNotReady("Cannot read the gateway configuration") from None
    bridge = None
    if entry.options.get("rtsp_enabled"):
        if not info.entrances:
            raise ConfigEntryNotReady("No entrance is configured for video")
        if len(entry.options.get("rtsp_password", "")) < 12:  # noqa: PLR2004
            raise ConfigEntryNotReady(
                "Set a separate RTSP password in integration options"
            )
        bridge = VideoBridge(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_TOKEN],
            info,
            dict(entry.options),
            reauth=lambda: entry.async_start_reauth(hass),
        )
        try:
            await bridge.start()
        except OSError:
            raise ConfigEntryNotReady(
                "RTSP port is unavailable; change it in integration options"
            ) from None
    entry.runtime_data = RuntimeData(info, bridge)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    if bridge:

        async def stop_video(_event: Event) -> None:
            await bridge.stop()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_video)
        )
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Comelit gateway",
        manufacturer="Comelit",
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ComelitConfigEntry) -> bool:  # noqa: ARG001
    """Close RTSP listeners and release any active video session."""
    if entry.runtime_data.bridge:
        await entry.runtime_data.bridge.stop()
    return True
