"""Set up Comelit Local using validated, short-lived gateway sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


type ComelitConfigEntry = ConfigEntry[GatewayInfo]


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
    entry.runtime_data = info
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Comelit gateway",
        manufacturer="Comelit",
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant,  # noqa: ARG001 - Home Assistant signature
    entry: ComelitConfigEntry,  # noqa: ARG001 - Home Assistant signature
) -> bool:
    """Unload the integration; setup leaves no sockets or background tasks."""
    return True
