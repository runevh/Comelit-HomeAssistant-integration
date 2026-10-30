"""
Minimal, short-lived ViP sessions for authentication and configuration.

Wire format references: mnestrud/comelit-man and madchicken/comelit-client.
This client never opens call, push, video, or actuator channels.
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
from dataclasses import dataclass, field
from typing import Any

from .const import DEFAULT_PORT, REQUEST_TIMEOUT

_HEADER = struct.Struct("<2sHH2s")
_MAGIC = b"\x00\x06"
_TOKEN = re.compile(r"[0-9a-fA-F]{32}")
_ADDRESS = re.compile(r"[0-9]{8}")
_DIRECTORY_KEYS = (
    "opendoor-address-book",
    "actuator-address-book",
    "entrance-address-book",
    "camera-address-book",
    "rtsp-camera-address-book",
)


class GatewayError(Exception):
    """A gateway failure with a fixed, credential-free message."""


class CannotConnect(GatewayError):
    """The gateway could not be reached or timed out."""


class InvalidAuth(GatewayError):
    """The gateway rejected the token."""


class InvalidResponse(GatewayError):
    """The gateway returned an unsupported or malformed response."""


@dataclass(frozen=True)
class GatewayInfo:
    """Validated configuration summary; no raw payloads or credentials."""

    apartment: str = field(repr=False)
    subaddress: int = field(repr=False)
    directory_counts: dict[str, int]


def validate_token(token: str) -> str:
    """Check token syntax without including the supplied token in errors."""
    token = token.strip()
    if not _TOKEN.fullmatch(token):
        raise InvalidAuth("Token must contain 32 hexadecimal characters")
    return token


def parse_configuration(response: dict[str, Any]) -> GatewayInfo:
    """Validate the configuration without retaining its private fields."""
    vip = response.get("vip")
    if not isinstance(vip, dict):
        raise InvalidResponse("Missing ViP configuration")
    apartment = vip.get("apt-address")
    subaddress = vip.get("apt-subaddress")
    parameters = vip.get("user-parameters")
    if (
        not isinstance(apartment, str)
        or not _ADDRESS.fullmatch(apartment)
        or type(subaddress) is not int
        or not 0 <= subaddress <= 255
        or not isinstance(parameters, dict)
    ):
        raise InvalidResponse("Invalid ViP configuration")
    counts = {}
    for key in _DIRECTORY_KEYS:
        directory = parameters.get(key, [])
        if not isinstance(directory, list) or any(
            not isinstance(item, dict) for item in directory
        ):
            raise InvalidResponse("Invalid address book")
        counts[key] = len(directory)
    return GatewayInfo(apartment, subaddress, counts)


class GatewayClient:
    """Read configuration using a bounded, disposable TCP session."""

    def __init__(
        self, host: str, port: int = DEFAULT_PORT, timeout: float = REQUEST_TIMEOUT
    ) -> None:
        """Store connection settings, never a token."""
        self.host = host
        self.port = port
        self.timeout = timeout

    async def async_get_configuration(self, token: str) -> GatewayInfo:
        """Authenticate, fetch configuration, and always close the socket."""
        token = validate_token(token)
        writer = None
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                auth_channel = await self._open_channel(reader, writer, "UAUT", 7, 1)
                await self._request(
                    reader,
                    writer,
                    auth_channel,
                    {"message": "access", "user-token": token, "message-id": 2},
                )
                config_channel = await self._open_channel(reader, writer, "UCFG", 2, 2)
                response = await self._request(
                    reader,
                    writer,
                    config_channel,
                    {
                        "message": "get-configuration",
                        "addressbooks": "all",
                        "message-id": 3,
                    },
                )
                return parse_configuration(response)
        except TimeoutError, OSError, asyncio.IncompleteReadError:
            raise CannotConnect("Cannot communicate with the gateway") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
                except TimeoutError, OSError:
                    pass

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, channel: int, body: bytes) -> None:
        writer.write(_HEADER.pack(_MAGIC, len(body), channel, b"\0\0") + body)
        await writer.drain()

    @staticmethod
    async def _read(reader: asyncio.StreamReader) -> tuple[int, bytes]:
        magic, length, channel, padding = _HEADER.unpack(await reader.readexactly(8))
        if magic != _MAGIC or padding != b"\0\0" or length == 0:
            raise InvalidResponse("Invalid frame header")
        return channel, await reader.readexactly(length)

    async def _open_channel(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        name: str,
        channel_type: int,
        request_id: int,
    ) -> int:
        body = struct.pack("<HHI", 0xABCD, 1, channel_type)
        body += name.encode("ascii") + struct.pack("<HB", request_id, 0)
        await self._send(writer, 0, body)
        for _ in range(32):
            channel, response = await self._read(reader)
            if channel != 0:
                continue
            if len(response) < 10 or struct.unpack_from("<HH", response) != (0xABCD, 2):
                raise InvalidResponse("Invalid channel response")
            server_channel = struct.unpack_from("<H", response, 8)[0]
            if server_channel == 0:
                raise InvalidResponse("Gateway rejected the channel")
            return server_channel
        raise InvalidResponse("No matching channel response")

    async def _request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        channel: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        body = json.dumps({**payload, "message-type": "request"}).encode()
        await self._send(writer, channel, body)
        for _ in range(32):
            response_channel, body = await self._read(reader)
            if response_channel != channel:
                continue
            try:
                response = json.loads(body)
            except ValueError, UnicodeError:
                raise InvalidResponse("Invalid JSON response") from None
            if not isinstance(response, dict):
                raise InvalidResponse("Invalid response object")
            if response.get("message-type") not in (None, "response"):
                continue
            if response.get("message-id") not in (None, payload["message-id"]):
                continue
            code = response.get("response-code")
            if code in (401, 403):
                raise InvalidAuth("Gateway rejected authentication")
            if code != 200:
                # Never include a gateway response-string: it may echo credentials.
                raise InvalidResponse("Gateway rejected the request")
            return response
        raise InvalidResponse("No matching request response")
