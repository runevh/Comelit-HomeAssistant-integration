"""Exercise real framing and parsing against synthetic gateway replies."""

import asyncio
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.comelit_local.client import (
    ActivationRejected,
    ActivationUncertain,
    CannotConnect,
    GatewayClient,
    InvalidAuth,
    InvalidResponse,
)

TOKEN = "0123456789abcdef0123456789abcdef"
CONFIG = {
    "vip": {
        "apt-address": "00000001",
        "apt-subaddress": 8,
        "user-parameters": {
            "actuator-address-book": [{"name": "Gate"}] * 3,
            "opendoor-address-book": [],
            "entrance-address-book": [{"name": "Entrance", "apt-address": "00000101"}],
        },
    },
}


def frame(channel, body):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    return struct.pack("<2sHH2s", b"\x00\x06", len(body), channel, b"\0\0") + body


def channel_reply(channel):
    return frame(0, struct.pack("<HHIH", 0xABCD, 2, 4, channel))


def response(channel, code=200, **extra):
    return frame(channel, {"response-code": code, **extra})


def connection(data):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    writer = MagicMock()
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


async def test_success_routes_json_to_server_channels_and_closes():
    reader, writer = connection(
        channel_reply(71) + response(71) + channel_reply(92) + response(92, **CONFIG)
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        info = await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    assert info.directory_counts["actuator-address-book"] == 3
    assert info.directory_counts["opendoor-address-book"] == 0
    assert info.apartment == "00000001"
    writes = [call.args[0] for call in writer.write.call_args_list]
    assert len(writes) == 4
    assert writes[0][8:] == struct.pack("<HHI", 0xABCD, 1, 7) + b"UAUT\x01\x00\x00"
    assert struct.unpack_from("<H", writes[1], 4)[0] == 71
    assert writes[2][8:] == struct.pack("<HHI", 0xABCD, 1, 2) + b"UCFG\x02\x00\x00"
    assert struct.unpack_from("<H", writes[3], 4)[0] == 92
    assert json.loads(writes[3][8:])["addressbooks"] == "all"
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()


@pytest.mark.parametrize("code", [401, 403])
async def test_rejected_token_is_not_exposed(code, caplog):
    reader, writer = connection(
        channel_reply(71) + response(71, code, **{"response-string": TOKEN})
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(InvalidAuth) as exc:
            await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    assert TOKEN not in str(exc.value) + caplog.text
    writer.close.assert_called_once()
    assert writer.write.call_count == 2


async def test_invalid_token_does_not_connect():
    with patch("asyncio.open_connection", AsyncMock()) as connect:
        with pytest.raises(InvalidAuth):
            await GatewayClient("127.0.0.1").async_get_configuration("invalid")
    connect.assert_not_called()


@pytest.mark.parametrize(
    "payload", [b"not-json", b"[]", b'{"response-code":200}', b'{"response-code":500}']
)
async def test_invalid_configuration(payload):
    reader, writer = connection(
        channel_reply(71) + response(71) + channel_reply(92) + frame(92, payload)
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(InvalidResponse):
            await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    writer.close.assert_called_once()


async def test_partial_frame_is_connection_error():
    reader, writer = connection(b"\x00\x06")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(CannotConnect):
            await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    writer.close.assert_called_once()


async def test_timeout_closes_socket():
    reader = asyncio.StreamReader()
    _, writer = connection(b"")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(CannotConnect):
            await GatewayClient("127.0.0.1", timeout=0.01).async_get_configuration(
                TOKEN
            )
    writer.close.assert_called_once()


async def test_malformed_header():
    reader, writer = connection(b"invalid!")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(InvalidResponse):
            await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    writer.close.assert_called_once()


async def test_unrelated_frames_are_skipped():
    reader, writer = connection(
        channel_reply(71)
        + response(99)
        + response(71)
        + channel_reply(92)
        + response(92, **CONFIG)
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        info = await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
    assert info.subaddress == 8


async def test_connection_refused():
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError)):
        with pytest.raises(CannotConnect):
            await GatewayClient("127.0.0.1").async_get_configuration(TOKEN)


async def test_cancel_closes_socket():
    reader = asyncio.StreamReader()
    _, writer = connection(b"")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        task = asyncio.create_task(
            GatewayClient("127.0.0.1").async_get_configuration(TOKEN)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    writer.close.assert_called_once()


@pytest.mark.usefixtures("socket_enabled")
async def test_fragmented_tcp_exchange():
    """Exercise actual TCP transport against a loopback-only simulated gateway."""
    completed = asyncio.get_running_loop().create_future()

    async def serve(reader, writer):
        try:
            replies = [
                channel_reply(71),
                response(71),
                channel_reply(92),
                response(92, **CONFIG),
            ]
            for reply in replies:
                header = await reader.readexactly(8)
                await reader.readexactly(struct.unpack_from("<H", header, 2)[0])
                # TCP may split a frame anywhere, including within its header.
                for index in range(0, len(reply), 3):
                    writer.write(reply[index : index + 3])
                    await writer.drain()
                    await asyncio.sleep(0)
            assert await reader.read() == b""
            completed.set_result(None)
        except Exception as err:  # noqa: BLE001 - propagate server failures to test
            completed.set_exception(err)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        info = await GatewayClient("127.0.0.1", port).async_get_configuration(TOKEN)
        await asyncio.wait_for(completed, 2)
    assert info.directory_counts["actuator-address-book"] == 3


async def test_activation_returns_token_without_reading_config():
    reader, writer = connection(
        channel_reply(71) + response(71, **{"user-token": TOKEN})
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        token = await GatewayClient("127.0.0.1").async_activate(
            "testcode", "Home Assistant Dev"
        )
    assert token == TOKEN
    assert writer.write.call_count == 2
    packet = writer.write.call_args_list[1].args[0]
    assert json.loads(packet[8:]) == {
        "message": "user-activation",
        "activation-code": "testcode",
        "description": "Home Assistant Dev",
        "message-id": 2,
        "message-type": "request",
    }
    writer.close.assert_called_once()


@pytest.mark.parametrize("code", [400, 401, 403, 404, 500])
async def test_activation_rejected(code, caplog):
    reader, writer = connection(
        channel_reply(71) + response(71, code, **{"response-string": TOKEN})
    )
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(ActivationRejected) as error:
            await GatewayClient("127.0.0.1").async_activate("testcode", "Dev")
    assert TOKEN not in str(error.value) + caplog.text
    writer.close.assert_called_once()


@pytest.mark.parametrize(
    "reply",
    [b"", response(71), response(71, **{"user-token": "bad"}), frame(71, b"bad json")],
)
async def test_activation_unknown_outcome(reply):
    reader, writer = connection(channel_reply(71) + reply)
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(ActivationUncertain):
            await GatewayClient("127.0.0.1").async_activate("testcode", "Dev")
    writer.close.assert_called_once()


async def test_activation_connection_failure_before_submission():
    with patch("asyncio.open_connection", AsyncMock(side_effect=OSError)):
        with pytest.raises(CannotConnect):
            await GatewayClient("127.0.0.1").async_activate("testcode", "Dev")
