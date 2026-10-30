"""Home Assistant options and bridge lifecycle tests."""

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.translation import async_get_translations
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local.client import Entrance, GatewayInfo
from custom_components.comelit_local.const import DOMAIN
from custom_components.comelit_local.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_config_flow import CLIENT, DATA

INFO = GatewayInfo("00000001", 9, {}, (Entrance("Entrance", "00000101"),))
OPTIONS = {
    "calls_enabled": False,
    "call_notifications": True,
    "rtsp_enabled": True,
    "rtsp_lan": False,
    "rtsp_port": 8557,
    "rtsp_password": "test-stream-password",
}


async def test_options_require_stream_password_and_never_return_saved_password(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "rtsp_password": "short"}
    )
    assert result["errors"] == {"rtsp_password": "password_required"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], OPTIONS
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options == OPTIONS
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert OPTIONS["rtsp_password"] not in str(result["data_schema"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "rtsp_password": ""}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options["rtsp_password"] == OPTIONS["rtsp_password"]


async def test_bridge_lifecycle_and_redacted_diagnostics(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, AsyncMock(return_value=INFO)),
        patch("custom_components.comelit_local.VideoBridge") as bridge_class,
    ):
        bridge = bridge_class.return_value
        bridge.start = AsyncMock()
        bridge.stop = AsyncMock()
        bridge.diagnostics.return_value = {"listening": True, "viewers": 0}
        assert await hass.config_entries.async_setup(entry.entry_id)
        bridge.start.assert_awaited_once()
        report = await async_get_config_entry_diagnostics(hass, entry)
        assert report["rtsp"]["listening"]
        assert DATA["token"] not in str(report)
        assert OPTIONS["rtsp_password"] not in str(report)
        assert await hass.config_entries.async_unload(entry.entry_id)
        bridge.stop.assert_awaited_once()


async def test_bind_failure_reports_not_ready(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options=OPTIONS)
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, AsyncMock(return_value=INFO)),
        patch(
            "custom_components.comelit_local.VideoBridge.start",
            AsyncMock(side_effect=OSError),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state.value == "setup_retry"


async def test_options_translations_are_available(hass):
    translations = await async_get_translations(hass, "en", "options", {DOMAIN})
    prefix = "component.comelit_local.options.step.init.data."
    assert translations[prefix + "rtsp_enabled"] == "Enable RTSP video bridge"
    assert translations[prefix + "rtsp_password"] == "Stream password"


async def test_calls_conflict_with_bridge_but_accept_native_video(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "calls_enabled": True}
    )
    assert result["errors"]["base"] == "conflicting_connections"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            **OPTIONS,
            "calls_enabled": True,
            "rtsp_enabled": False,
            "native_rtsp_url": "rtsp://user:private@192.0.2.1/live",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "private" not in str(result["data_schema"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**OPTIONS, "rtsp_enabled": False, "clear_native_rtsp": True}
    )
    assert "native_rtsp_url" not in entry.options
