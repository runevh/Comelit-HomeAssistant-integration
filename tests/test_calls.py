"""Ring lifecycle, ownership, and wire boundaries without contacting hardware."""

import asyncio
import struct
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.comelit_local import RuntimeData
from custom_components.comelit_local.call_api import async_register
from custom_components.comelit_local.call_transport import CallSession, InboundMedia
from custom_components.comelit_local.calls import CallController
from custom_components.comelit_local.const import DOMAIN
from custom_components.comelit_local.vendor.ctp import FLAG_DATA, build_ctp_packet
from custom_components.comelit_local.vendor.frames import encode_open_channel
from custom_components.comelit_local.vendor.models import RingEvent
from custom_components.comelit_local.vendor.session import ViperError, ViperSession

from .test_config_flow import CLIENT, DATA
from .test_video_options import INFO


def ring(**kwargs):
    return RingEvent(
        **{
            "caller": "000001010",
            "callee": "000000019",
            "connection": b"\x01\x02",
            "call_id": b"abcd",
            "received_at": datetime.now(UTC),
            "origin": "00000101",
            "tag": b"PP",
            **kwargs,
        }
    )


def controller(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=DATA)
    entry.add_to_hass(hass)
    entry.runtime_data = RuntimeData(INFO)
    calls = CallController(hass, entry)
    entry.runtime_data.calls = calls
    calls.session = Mock(connected=True)
    calls.state = "idle"
    return calls


async def test_ring_dedup_notification_and_correlated_end(hass):
    await async_setup_component(hass, "persistent_notification", {})
    calls = controller(hass)
    events = []

    @callback
    def record(event):
        events.append(event.data)

    hass.bus.async_listen(f"{DOMAIN}_doorbell", record)
    with patch("custom_components.comelit_local.calls.InboundMedia") as media:
        calls._ring(ring())
        call_id = calls.call_id
        calls._ring(ring())
        assert calls.rings == 1
        assert calls.state == "ringing"
        assert hass.data["persistent_notification"]
        media.assert_not_called()
        await calls.hangup()
    await hass.async_block_till_done()
    assert [e["event_type"] for e in events] == ["ring", "missed_call"]
    assert events[1]["call_id"] == call_id
    assert events[1]["entrance"] == "Entrance"
    assert DATA["token"] not in str(events)


async def test_ignore_floor_and_unknown_calls(hass):
    calls = controller(hass)
    calls._ring(ring(tag=b"FF"))
    calls._ring(ring(origin="00000999", call_id=b"efgh"))
    assert calls.state == "idle"
    assert calls.rings == 0


async def test_only_one_browser_can_answer_and_transmit(hass):
    await async_setup_component(hass, "persistent_notification", {})
    calls = controller(hass)
    calls._ring(ring())
    calls.session._connections = {calls.event.connection: Mock()}
    owner, other = object(), object()
    with patch("custom_components.comelit_local.calls.InboundMedia") as factory:
        media = factory.return_value
        media.answer = AsyncMock()
        media.close = AsyncMock()
        with pytest.raises(ViperError):
            await calls.answer("stale", owner, Mock())
        await calls.answer(calls.call_id, owner, Mock())
        assert calls.state == "connected"
        with pytest.raises(ViperError):
            await calls.answer(calls.call_id, other, Mock())
        with pytest.raises(ViperError):
            calls.microphone(calls.call_id, other, bytes(160))
        calls.microphone(calls.call_id, owner, bytes(160))
        media.microphone.assert_called_once_with(bytes(160))
        await calls.hangup()
        await calls.hangup()
        media.close.assert_awaited_once()
        assert calls.owner is None


async def test_answer_failure_releases_owned_media(hass):
    await async_setup_component(hass, "persistent_notification", {})
    calls = controller(hass)
    calls._ring(ring())
    calls.session._connections = {calls.event.connection: Mock()}
    with patch("custom_components.comelit_local.calls.InboundMedia") as factory:
        media = factory.return_value
        media.answer = AsyncMock(side_effect=TimeoutError)
        media.close = AsyncMock()
        with pytest.raises(TimeoutError):
            await calls.answer(calls.call_id, object(), Mock())
        assert calls.last_error == "answer_failed"
        assert calls.state == "idle"
        media.close.assert_awaited_once()


async def test_wire_guard_blocks_registration_shortcuts_relays_and_unowned_audio():
    session = CallSession("unused", 64100)
    session._ctpp_handle = 10
    with patch.object(ViperSession, "_send_frame", AsyncMock()) as wire:
        await session._send_frame(0, encode_open_channel("UAUT", 1))
        with pytest.raises(ViperError):
            await session._send_frame(0, encode_open_channel("FACT", 2))
        for body in (b"\x00\x2d\x00", b"\x00\x11\x00", b"\x00\x70\x00"):
            packet = build_ctp_packet(
                flags=FLAG_DATA,
                connection=b"\x01\x02",
                sequence=1,
                acknowledgement=0,
                body=body,
                source="000000019",
                destination="00000101",
            )
            with pytest.raises(ViperError):
                await session._send_frame(10, packet)
        with pytest.raises(ViperError):
            await session._send_frame(11, b"\x80\x08" + bytes(170))
        with pytest.raises(ViperError):
            await session._send_frame(12, b'{"message":"user-activation"}')
        assert wire.await_count == 1


async def test_answer_negotiates_then_sends_only_explicit_microphone_frames():
    session = Mock(
        source="000000019",
        config=SimpleNamespace(apt_address="00000001"),
        connected=True,
    )
    session.open_channel = AsyncMock(side_effect=[20, 21, 22])
    session.close_channel = AsyncMock()
    session._send_frame = AsyncMock()
    conn = Mock(failed=False)
    conn.send = AsyncMock()
    received = Mock()
    media = InboundMedia(session, conn, received)
    media._caps_ready.set()
    media._incoming("RTPC", 23)
    await media.answer()
    assert media.answered
    session._send_frame.assert_not_called()
    bodies = [call.args[0] for call in conn.send.await_args_list]
    assert bodies[0] == bytes.fromhex("0003490007000000")
    assert bodies[-1] == b"\x00\x0c"
    assert len(bodies[3]) == 10  # Audio offer excludes wire alignment padding.
    media.microphone(b"\xd5" * 160)
    await asyncio.sleep(0)
    packet = session._send_frame.call_args.args[1]
    assert packet[:2] == b"\x80\x08"
    assert len(packet) == 172
    media._receive_audio(20, struct.pack(">BBHII", 0x80, 8, 1, 160, 1) + bytes(160))
    received.assert_called_once_with(8, bytes(160))
    await media.close()
    with pytest.raises(ViperError):
        media.microphone(bytes(160))
    assert session._send_frame.await_count == 1
    conn.send.assert_awaited_with(b"\x00\x0e\x00")


async def test_media_ignores_audio_before_answer_and_bad_frames():
    received = Mock()
    media = InboundMedia(Mock(), Mock(), received)
    packet = struct.pack(">BBHII", 0x80, 8, 1, 160, 1) + bytes(160)
    media._receive_audio(1, packet)
    media.answered = True
    media._receive_audio(1, b"broken")
    media._receive_audio(1, struct.pack(">BBHII", 0x80, 96, 1, 160, 1) + bytes(160))
    received.assert_not_called()
    with pytest.raises(ViperError):
        media.microphone(bytes(159))


async def test_websocket_subscription_authorization_and_disconnect(
    hass, hass_ws_client
):

    calls = controller(hass)
    await async_setup_component(hass, "websocket_api", {})
    await async_register(hass)
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "comelit_local/call_subscribe",
            "entry_id": calls.entry.entry_id,
        }
    )
    assert (await client.receive_json())["success"]
    assert (await client.receive_json())["event"]["state"] == "idle"
    await client.send_json(
        {
            "id": 2,
            "type": "comelit_local/call_control",
            "entry_id": calls.entry.entry_id,
            "call_id": "stale",
            "action": "microphone",
            "data": "invalid",
        }
    )
    assert (await client.receive_json())["error"]["code"] == "call_failed"
    await client.send_json(
        {
            "id": 3,
            "type": "comelit_local/call_subscribe",
            "entry_id": calls.entry.entry_id,
        }
    )
    assert (await client.receive_json())["error"]["code"] == "already_subscribed"
    await client.send_json({"id": 4, "type": "unsubscribe_events", "subscription": 1})
    assert (await client.receive_json())["success"]
    assert not hass.data["comelit_local_call_api"]


async def test_event_entity_reports_ring_and_listener_availability(hass):

    entry = MockConfigEntry(domain=DOMAIN, data=DATA, options={"calls_enabled": True})
    entry.add_to_hass(hass)
    with (
        patch(CLIENT, AsyncMock(return_value=INFO)),
        patch.object(CallController, "start"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity = next(
            e
            for e in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
            if e.domain == "event"
        )
        assert hass.states.get(entity.entity_id).state == "unavailable"
        calls = entry.runtime_data.calls
        calls.session = Mock(connected=True)
        calls._ring(ring())
        await hass.async_block_till_done()
        state = hass.states.get(entity.entity_id)
        assert state.attributes["event_type"] == "ring"
        assert state.attributes["call_state"] == "ringing"
        assert state.attributes["entry_id"] == entry.entry_id
        assert await hass.config_entries.async_unload(entry.entry_id)
