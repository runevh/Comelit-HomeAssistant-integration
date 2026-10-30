"""Home Assistant entrance cameras backed by the shared RTSP bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import ComelitConfigEntry
    from .client import Entrance


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 - platform signature
    entry: ComelitConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add one camera per configured entrance when the bridge is enabled."""
    if entry.options.get("native_rtsp_url") and entry.runtime_data.info.entrances:
        # One explicitly supplied native feed maps to the first entrance.
        async_add_entities(
            [EntranceCamera(entry, entry.runtime_data.info.entrances[0])]
        )
    elif entry.runtime_data.bridge:
        async_add_entities(
            EntranceCamera(entry, entrance)
            for entrance in entry.runtime_data.info.entrances
        )


class EntranceCamera(Camera):
    """Use HA's stream pipeline for HLS playback and JPEG snapshots."""

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_brand = "Comelit"

    def __init__(self, entry: ComelitConfigEntry, entrance: Entrance) -> None:
        """Keep stream credentials private, outside entity attributes."""
        super().__init__()
        self._entry = entry
        self._entrance = entrance
        self._attr_name = entrance.name
        self._attr_unique_id = f"{entry.entry_id}_entrance_{entrance.address}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        self.stream_options = {"rtsp_transport": "tcp"}

    @property
    def use_stream_for_stills(self) -> bool:
        """Let HA take snapshots from the same shared feed as live viewing."""
        return True

    async def stream_source(self) -> str | None:
        """Return an authenticated loopback source only to HA's stream pipeline."""
        if native := self._entry.options.get("native_rtsp_url"):
            return native
        bridge = self._entry.runtime_data.bridge
        if bridge is None or not bridge.started:
            return None
        password = quote(self._entry.options["rtsp_password"], safe="")
        port = self._entry.options.get("rtsp_port", 8557)
        # Always name the entrance so a directory reorder cannot switch cameras.
        return (
            f"rtsp://comelit:{password}@127.0.0.1:{port}"
            f"/comelit/{self._entrance.address}"
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> bytes | None:
        """Support direct image requests as well as HA's standard still pipeline."""
        if stream := await self.async_create_stream():
            return await stream.async_get_image(width=width, height=height)
        return None

    async def async_will_remove_from_hass(self) -> None:
        """Stop the HA viewer before the RTSP listener is shut down."""
        if self.stream:
            await self.stream.stop()
        await super().async_will_remove_from_hass()
