"""
Bounded CTP transactions for actuator control, without call subscriptions.

Packet codec adapted from jfmlima/comelit-vip viper/ctp.py (MIT, Jorge Lima).
See CTP_LICENSE. Modified to validate only the fields needed here, use fixed
credential-free errors, and handle one transaction without background tasks.
Actuator opcode 0x0020 / ff01 comes from madchicken/comelit-client captures.
"""

from __future__ import annotations

import secrets
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .client import InvalidResponse

if TYPE_CHECKING:
    import asyncio

    from .client import GatewayClient


def logical_address(address: str) -> bytes:
    """Encode a numeric logical address in its ten-byte field."""
    if not address.isascii() or not address.isdigit() or len(address) > 10:
        raise InvalidResponse("Unsupported logical address")
    return address.encode("ascii").ljust(10, b"\0")


@dataclass(frozen=True)
class Packet:
    """A validated CTP packet with private addresses excluded from repr."""

    flags: int
    connection: int
    sequence: int
    acknowledgement: int
    body: bytes = field(repr=False)
    # Addresses are bytes to avoid rendering arbitrary network input as text.
    source: bytes = field(repr=False)
    destination: bytes = field(repr=False)

    @classmethod
    def parse(cls, payload: bytes) -> Packet:
        """Reject malformed framing before interpreting a response."""
        if len(payload) < 32 or payload[1] != 0x18:
            raise InvalidResponse("Invalid CTP header")
        flags, _, connection, sequence, acknowledgement, length = struct.unpack_from(
            ">BBHBBH", payload
        )
        end = 8 + length
        trailer = end + (-length % 4)
        if (
            len(payload) != trailer + 24
            or payload[trailer : trailer + 4] != b"\xff" * 4
        ):
            raise InvalidResponse("Invalid CTP length or trailer")
        return cls(
            flags,
            connection,
            sequence,
            acknowledgement,
            payload[8:end],
            payload[trailer + 4 : trailer + 14],
            payload[trailer + 14 :],
        )


class Transaction:
    """A single CTP exchange; never retransmit a physical action."""

    def __init__(self, source: str, destination: str) -> None:
        """Allocate a transaction independent of other device users."""
        self.connection = secrets.randbelow(0x7FFE) + 1
        self.sequence = secrets.randbelow(256)
        self.acknowledgement = secrets.randbelow(256)
        self.source = logical_address(source)
        self.destination = logical_address(destination)

    def encode(self, flags: int, body: bytes = b"") -> bytes:
        """Build a packet and advance the sequence only for data."""
        header = struct.pack(
            ">BBHBBH",
            flags,
            0x18,
            self.connection,
            self.sequence,
            self.acknowledgement,
            len(body),
        )
        if body:
            self.sequence = (self.sequence + 1) % 256
        return (
            header
            + body
            + b"\0" * (-len(body) % 4)
            + b"\xff" * 4
            + self.source
            + self.destination
        )

    async def finish(
        self,
        client: GatewayClient,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        channel: int,
        opcode: bytes,
    ) -> bytes:
        """Wait for a matching application reply, then ACK and close our transaction."""
        for _ in range(32):
            response_channel, payload = await client._read(reader)
            if response_channel != channel:
                continue
            packet = Packet.parse(payload)
            if (
                packet.connection != self.connection ^ 0x8000
                or packet.source != self.destination
                or packet.destination != self.source
            ):
                continue
            # Never acknowledge an incoming call even if its connection ID collides.
            if packet.flags & 0x80:
                raise InvalidResponse("CTP transaction displaced")
            if packet.acknowledgement != self.sequence:
                continue
            if not packet.body:
                continue
            if not packet.body.startswith(opcode):
                raise InvalidResponse("Unexpected CTP response")
            self.acknowledgement = (packet.sequence + 1) % 256
            await client._send(writer, channel, self.encode(0))
            await client._send(writer, channel, self.encode(0x20))
            return packet.body
        raise InvalidResponse("No matching CTP response")
