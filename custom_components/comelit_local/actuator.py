"""Short-lived, authenticated actuator transactions."""

from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING

from .client import (
    CannotConnect,
    CommandRejected,
    CommandUncertain,
    InvalidResponse,
    UnsupportedActuator,
    validate_token,
)
from .ctp import Transaction

if TYPE_CHECKING:
    from .client import Actuator, GatewayClient, GatewayInfo


async def async_press_actuator(
    client: GatewayClient,
    token: str,
    info: GatewayInfo,
    actuator: Actuator,
) -> None:
    """Submit once; an interrupted exchange after submission has an unknown outcome."""
    token = validate_token(token)
    # Only the ff01 actuator profile is evidenced by the reference captures.
    # Do not guess how other modules/outputs map to the actuator opcode.
    if (actuator.module, actuator.output) != (255, 1) or actuator not in info.actuators:
        raise UnsupportedActuator("Unsupported actuator profile")
    source = f"{info.apartment}{info.subaddress}"
    registration = Transaction(source, info.apartment)
    # The observed actuator source suffix is the output index, not the app slot.
    action = Transaction(f"{info.apartment}{actuator.output}", actuator.address)
    writer = None
    submitted = False
    try:
        async with asyncio.timeout(client.timeout):
            reader, writer = await asyncio.open_connection(client.host, client.port)
            auth = await client._open_channel(reader, writer, "UAUT", 7, 1)
            await client._request(
                reader,
                writer,
                auth,
                {
                    "message": "access",
                    "user-token": token,
                    "message-id": 2,
                },
            )
            channel = await client._open_channel(
                reader, writer, "CTPP", 16, 2, extra=source.encode("ascii")
            )
            body = (
                b"\x00\x40"
                + struct.pack(">H", registration.connection)
                + registration.source
                + b"\x10\x0e\x00"
            )
            await client._send(writer, channel, registration.encode(0xC0, body))
            await registration.finish(client, reader, writer, channel, b"\x00\x41")
            submitted = True
            await client._send(
                writer, channel, action.encode(0xC0, b"\x00\x20\xff\x01")
            )
            reply = await action.finish(client, reader, writer, channel, b"\x00\x0e")
            if len(reply) < 3:
                raise InvalidResponse("Missing actuator result")  # noqa: TRY301
            if reply[2] != 0:
                raise CommandRejected("Gateway rejected the actuator command")
    except TimeoutError, OSError, asyncio.IncompleteReadError, InvalidResponse:
        if submitted:
            raise CommandUncertain("Check the gate before retrying") from None
        raise CannotConnect("Could not prepare the actuator connection") from None
    finally:
        if writer is not None:
            await client._close(writer)
