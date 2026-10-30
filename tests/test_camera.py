"""Native HA camera discovery, playback sources, privacy, and unload behavior."""

from unittest.mock import AsyncMock, patch
from urllib.parse import unquote, urlsplit

from homeassistant.components.camera import async_get_stream_source
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local.camera import EntranceCamera
from custom_components.comelit_local.const import DOMAIN

from .test_config_flow import CLIENT, DATA
from .test_video_options import INFO, OPTIONS


async def test_camera_registered_and_uses_private_loopback_source(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=DATA,
        options={**OPTIONS, "rtsp_password": "p@ss:word/with?symbols"},
    )
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, AsyncMock(return_value=INFO)),
        patch("custom_components.comelit_local.VideoBridge") as bridge_class,
    ):
        bridge = bridge_class.return_value
        bridge.start = AsyncMock()
        bridge.stop = AsyncMock()
        bridge.started = True
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        assert len(entities) == 1
        entity = entities[0]
        assert entity.domain == "camera"
        assert entity.original_name == "Entrance"
        assert entity.unique_id == f"{entry.entry_id}_entrance_00000101"
        source = await async_get_stream_source(hass, entity.entity_id)
        url = urlsplit(source)
        assert url.hostname == "127.0.0.1"
        assert url.port == 8557
        assert unquote(url.password) == entry.options["rtsp_password"]
        assert url.path == "/comelit/00000101"
        state = hass.states.get(entity.entity_id)
        assert state.attributes["supported_features"] == 2
        for secret in (entry.options["rtsp_password"], DATA["token"], source):
            assert secret not in str(state.attributes)
        bridge._new_call.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)
        bridge.stop.assert_awaited_once()


async def test_bridge_disabled_does_not_create_cameras(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    with patch(CLIENT, AsyncMock(return_value=INFO)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_snapshot_uses_ha_stream_and_unload_stops_it(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=OPTIONS)
    camera = EntranceCamera(entry, INFO.entrances[0])
    camera.hass = hass
    stream = AsyncMock()
    stream.async_get_image.return_value = b"jpeg"
    with patch.object(camera, "async_create_stream", AsyncMock(return_value=stream)):
        assert await camera.async_camera_image(320, 240) == b"jpeg"
    stream.async_get_image.assert_awaited_once_with(width=320, height=240)
    camera.stream = stream
    await camera.async_will_remove_from_hass()
    stream.stop.assert_awaited_once()
    assert camera.use_stream_for_stills


async def test_native_camera_without_bridge_keeps_credentials_private(hass):
    source = "rtsp://viewer:private-test-secret@192.0.2.20/live/h264/ch0"
    entry = MockConfigEntry(
        domain=DOMAIN, data=DATA, options={"native_rtsp_url": source}
    )
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, AsyncMock(return_value=INFO)),
        patch("custom_components.comelit_local.VideoBridge") as bridge,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[
            0
        ]
        assert await async_get_stream_source(hass, entity.entity_id) == source
        assert source not in str(hass.states.get(entity.entity_id).attributes)
        bridge.assert_not_called()
        assert await hass.config_entries.async_unload(entry.entry_id)
