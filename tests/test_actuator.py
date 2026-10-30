"""Verify actuator-only wire commands without contacting any real gateway."""

import asyncio
import copy
import struct
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.comelit_local.actuator import async_press_actuator
from custom_components.comelit_local.client import (
    Actuator,
    CannotConnect,
    CommandRejected,
    CommandUncertain,
    GatewayClient,
    InvalidAuth,
    InvalidResponse,
    UnsupportedActuator,
    parse_configuration,
)
from custom_components.comelit_local.ctp import Packet, Transaction

from .test_client import CONFIG, TOKEN, channel_reply, connection, frame, response

INFO = parse_configuration(CONFIG)


def test_discover_actuators_separately_and_deduplicate():
    config = copy.deepcopy(CONFIG)
    book = config["vip"]["user-parameters"]["actuator-address-book"]
    book.append(book[0].copy())
    info = parse_configuration(config)
    assert len(info.actuators) == 3
    assert info.actuators[0] == Actuator("Gate 1", "00001001", 255, 1)
    book.reverse()
    book[0]["name"] = "Renamed"
    book.pop()  # remove the old name for the duplicate target
    renamed = parse_configuration(config)
    assert {a.key for a in info.actuators} == {a.key for a in renamed.actuators}


@pytest.mark.parametrize(
    "update",
    [
        {"apt-address": "bad"},
        {"module-index": True},
        {"module-index": 256},
        {"output-index": -1},
        {"output-index": "1"},
        {"name": " "},
    ],
)
def test_invalid_directory_rejected(update):
    config = copy.deepcopy(CONFIG)
    config["vip"]["user-parameters"]["actuator-address-book"][0].update(update)
    with pytest.raises(InvalidResponse):
        parse_configuration(config)


def test_conflicting_targets_rejected():
    config = copy.deepcopy(CONFIG)
    book = config["vip"]["user-parameters"]["actuator-address-book"]
    book.append({**book[0], "name": "Conflicting name"})
    with pytest.raises(InvalidResponse):
        parse_configuration(config)


def test_ctp_matches_captured_actuator_request():
    transaction = Transaction("000000011", "00001001")
    transaction.connection = 0x45BE
    transaction.sequence = 0x8F
    transaction.acknowledgement = 0x5C
    packet = transaction.encode(0xC0, b"\x00\x20\xff\x01")
    assert packet == (
        bytes.fromhex("c01845be8f5c00040020ff01ffffffff")
        + b"000000011\0"
        + b"00001001\0\0"
    )
    assert Packet.parse(packet).body == b"\x00\x20\xff\x01"
    assert transaction.sequence == 0x90
    assert Packet.parse(transaction.encode(0)).body == b""


@pytest.mark.parametrize(
    "payload", [b"", b"\0" * 32, bytes.fromhex("001800010102ffff") + b"\0" * 24]
)
def test_bad_ctp_packets(payload):
    with pytest.raises(InvalidResponse):
        Packet.parse(payload)


def simulated_gateway(outcome="ok"):
    """React to outgoing frames independently; capture requests to assert no retry."""
    reader, writer = connection(b"")
    reader = asyncio.StreamReader()
    requests = []

    def receive(wire):
        channel = struct.unpack_from("<H", wire, 4)[0]
        payload = wire[8:]
        if channel == 0:
            name = payload[8:12]
            reader.feed_data(channel_reply(71 if name == b"UAUT" else 92))
        elif channel == 71:
            reader.feed_data(response(71, 403 if outcome == "auth" else 200))
        else:
            flags, _, connection_id, sequence, _, size = struct.unpack_from(
                ">BBHBBH", payload
            )
            if flags != 0xC0:
                return
            body = payload[8 : 8 + size]
            requests.append(body)
            is_action = body[:2] == b"\x00\x20"
            if (
                is_action and outcome in ("lost", "malformed")
            ) or outcome == "registration_lost":
                if outcome == "malformed":
                    reader.feed_data(frame(92, b"invalid"))
                else:
                    reader.feed_eof()
                return
            reply_body = (
                (b"\x00\x0e" + bytes([1 if outcome == "rejected" else 0]))
                if is_action
                else b"\x00\x41"
            )
            # Response addresses reverse the outgoing packet; independent encoder.
            header = struct.pack(
                ">BBHBBH",
                0x40,
                0x18,
                connection_id ^ 0x8000,
                55,
                (sequence + 1) % 256,
                len(reply_body),
            )
            reply = header + reply_body + b"\0" * (-len(reply_body) % 4)
            reply += b"\xff" * 4 + payload[-10:] + payload[-20:-10]
            reader.feed_data(frame(92, reply))

    writer.write.side_effect = receive
    return reader, writer, requests


async def test_authenticated_actuator_exchange_closes_and_never_uses_door_opcode():
    reader, writer, requests = simulated_gateway()
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        await async_press_actuator(
            GatewayClient("127.0.0.1"), TOKEN, INFO, INFO.actuators[0]
        )
    assert [body[:2] for body in requests] == [b"\x00\x40", b"\x00\x20"]
    assert requests[1] == b"\x00\x20\xff\x01"
    ctpp_open = writer.write.call_args_list[2].args[0][8:]
    assert ctpp_open[8:12] == b"CTPP"
    assert ctpp_open[20:] == b"000000018\0"
    writer.close.assert_called_once()


@pytest.mark.parametrize(
    ("outcome", "error", "commands"),
    [
        ("auth", InvalidAuth, 0),
        ("registration_lost", CannotConnect, 1),
        ("lost", CommandUncertain, 2),
        ("malformed", CommandUncertain, 2),
        ("rejected", CommandRejected, 2),
    ],
)
async def test_failures_are_not_retried(outcome, error, commands, caplog):
    reader, writer, requests = simulated_gateway(outcome)
    with patch(
        "asyncio.open_connection", AsyncMock(return_value=(reader, writer))
    ) as connect:
        with pytest.raises(error):
            await async_press_actuator(
                GatewayClient("127.0.0.1"), TOKEN, INFO, INFO.actuators[0]
            )
    assert len(requests) == commands
    connect.assert_awaited_once()
    writer.close.assert_called_once()
    assert TOKEN not in caplog.text


async def test_unsupported_target_never_connects():
    with patch("asyncio.open_connection", AsyncMock()) as connect:
        with pytest.raises(UnsupportedActuator):
            await async_press_actuator(
                GatewayClient("127.0.0.1"),
                TOKEN,
                INFO,
                Actuator("Other", "00001001", 1, 2),
            )
    connect.assert_not_called()


async def test_timeout_after_submission_is_uncertain_and_closes():
    reader, writer, requests = simulated_gateway()
    receive = writer.write.side_effect

    def withhold_action(wire):
        if wire[8:9] == b"\xc0" and wire[16:18] == b"\x00\x20":
            requests.append(wire[16:20])
            return
        receive(wire)

    writer.write.side_effect = withhold_action
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        with pytest.raises(CommandUncertain):
            await async_press_actuator(
                GatewayClient("127.0.0.1", timeout=0.01), TOKEN, INFO, INFO.actuators[0]
            )
    assert len(requests) == 2
    writer.close.assert_called_once()


async def test_cancellation_closes_connection_without_resending():
    reader = asyncio.StreamReader()
    _, writer = connection(b"")
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        task = asyncio.create_task(
            async_press_actuator(
                GatewayClient("127.0.0.1"), TOKEN, INFO, INFO.actuators[0]
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    writer.close.assert_called_once()
    assert writer.write.call_count == 1


async def test_unrelated_channel_or_transaction_does_not_confirm_action():
    reader, writer, requests = simulated_gateway()
    original_feed = reader.feed_data

    def unrelated_first(data):
        if data[8:10] == b"\x40\x18":
            wrong_channel = bytearray(data)
            struct.pack_into("<H", wrong_channel, 4, 123)
            original_feed(wrong_channel)
            wrong_connection = bytearray(data)
            wrong_connection[10] ^= 1
            original_feed(wrong_connection)
        original_feed(data)

    reader.feed_data = unrelated_first
    with patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))):
        await async_press_actuator(
            GatewayClient("127.0.0.1"), TOKEN, INFO, INFO.actuators[0]
        )
    assert len(requests) == 2
    writer.close.assert_called_once()
