"""
Experimental inbound call transport; never initiates calls or operates relays.

CTP framing is from our MIT-licensed vendor snapshot. Inbound negotiation follows
mnestrud/comelit-man's Apache-2.0 protocol research (see CALL_PROTOCOL.md).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import struct
from typing import TYPE_CHECKING

from .vendor.ctp import CtpPacket, encode_logaddr, parse_ctp_packet
from .vendor.frames import MGMT_HANDLE, OPEN_MAGIC
from .vendor.rtp import parse_rtp_packet
from .vendor.session import CtpConnection, ViperError, ViperSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from .vendor.models import RingEvent

# Detached logger prevents upstream protocol dumps exposing credentials.
PRIVATE_LOGGER = logging.Logger("comelit_local.private_calls")  # noqa: LOG001
PRIVATE_LOGGER.disabled = True


class CallSession(ViperSession):
    """Listen passively; negotiate media only for an explicitly answered ring."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize isolated logging and an optional owned call."""
        super().__init__(host, port, logger=PRIVATE_LOGGER)
        self.media: InboundMedia | None = None

    async def _send_frame(self, handle: int, payload: bytes) -> None:
        """Enforce a deny-by-default command boundary at the wire."""
        if handle == MGMT_HANDLE:
            if payload.startswith(OPEN_MAGIC):
                name = payload[8:12]
                if name not in (b"UAUT", b"UCFG", b"CTPP", b"CSPB", b"UDPM", b"RTPC"):
                    raise ViperError("Unsupported call channel")
                if self.media and name in (b"RTPC", b"UDPM"):
                    payload = payload[:14] + b"\x01" + payload[15:]
        elif payload.startswith(b"{"):
            if json.loads(payload).get("message") not in (
                "access",
                "get-configuration",
            ):
                raise ViperError("Unsupported call request")
        elif handle == self._ctpp_handle:
            packet = parse_ctp_packet(payload)
            if packet.opcode not in (None, 0x0040):
                media = self.media
                if (
                    media is None
                    or packet.connection != media.conn.local_id
                    or packet.opcode
                    not in (0x0003, 0x000A, 0x000C, 0x000E, 0x0011, 0x0070)
                ):
                    raise ViperError("Call control is not authorized")
        elif (
            self.media is None
            or not self.media.answered
            or handle != self.media.tx_handle
            or len(payload) != 172
            or payload[:2] != b"\x80\x08"
        ):
            raise ViperError("Microphone transmission is not authorized")
        await super()._send_frame(handle, payload)

    async def _watch_inbound_call(self, conn: CtpConnection, event: RingEvent) -> None:
        """Single reader for the ring connection, including negotiation replies."""
        try:
            async with asyncio.timeout(180):
                while True:
                    packet = await conn.wait(timeout=180)
                    if packet.opcode == 0x000E or packet.is_fin:
                        break
                    media = self.media
                    if media and media.conn is conn:
                        media.receive_control(packet)
        except TimeoutError, ViperError:
            pass
        finally:
            self.release_connection(conn)
            if not conn.end_reported:
                self._report_call_end(event, None)


class InboundMedia:
    """An explicitly answered inbound connection with bounded PCMA queues."""

    def __init__(
        self,
        session: CallSession,
        conn: CtpConnection,
        audio_callback: Callable[[int, bytes], None],
    ) -> None:
        """Prepare media without sending anything."""
        self.session = session
        self.conn = conn
        self.audio_callback = audio_callback
        self.handles: set[int] = set()
        self.tx_handle: int | None = None
        self.answered = False
        self.closed = False
        self.audio_received = 0
        self.audio_sent = 0
        self._remote_ready = asyncio.Event()
        self._caps_ready = asyncio.Event()
        self._tx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=10)
        self._sender: asyncio.Task | None = None
        self._answer_task: asyncio.Task | None = None

    def receive_control(self, packet: CtpPacket) -> None:
        """Observe capabilities without racing the call monitor."""
        if packet.opcode == 0x0003:
            self._caps_ready.set()

    def _incoming(self, name: str, handle: int) -> None:
        if name == "RTPC" and not self.closed:
            self.tx_handle = handle
            self.handles.add(handle)
            self.session.set_binary_handler(handle, self._receive_audio)
            self._remote_ready.set()

    def _receive_audio(self, _handle: int, payload: bytes) -> None:
        if not self.answered or self.closed:
            return
        try:
            packet = parse_rtp_packet(payload)
        except ValueError:
            return
        if packet.payload_type in (0, 8) and len(packet.payload) <= 1600:
            self.audio_received += 1
            self.audio_callback(packet.payload_type, packet.payload)

    async def _open(self, name: str) -> int:
        handle = await self.session.open_channel(name)
        self.handles.add(handle)
        if name == "RTPC":
            self.session.set_binary_handler(handle, self._receive_audio)
        return handle

    async def answer(self) -> None:
        """Negotiate this ring's media and explicitly claim the incoming call."""
        self._answer_task = asyncio.current_task()
        self.session.media = self
        self.session.set_incoming_channel_handler(self._incoming)
        # The reference app addresses inbound responses to the apartment base.
        self.conn.destination = self.session.config.apt_address
        async with asyncio.timeout(25):
            # Channel opens and codec negotiation form one burst on inbound calls.
            opens = [asyncio.create_task(self._open(name)) for name in ("RTPC", "UDPM")]
            try:
                await asyncio.sleep(0)
                await self.conn.send(bytes.fromhex("0003490007000000"))
                audio, _udp = await asyncio.gather(*opens)
            finally:
                for task in opens:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*opens, return_exceptions=True)
            await asyncio.wait_for(self._caps_ready.wait(), 5)
            video = await self._open("RTPC")
            await self.conn.send(bytes.fromhex("0003490007000000"))
            await self.conn.send(bytes.fromhex("000a00"))
            await self.conn.send(
                bytes.fromhex("0011180200000000") + struct.pack("<H", audio)
            )
            await self.conn.send(
                bytes.fromhex("0011143200000000")
                + struct.pack("<H", video)
                + struct.pack("<HIHHHHBB", 0xFFFF, 192, 320, 240, 320, 240, 16, 0)
            )
            await asyncio.sleep(3)
            await self.conn.send(
                bytes.fromhex("0011143200000000")
                + struct.pack("<H", video)
                + struct.pack("<HIHHHHBB", 0xFFFF, 192, 320, 240, 320, 240, 16, 0)
            )
            await self.conn.send(
                b"\x00\x70" + encode_logaddr(self.session.source) + b"\x01\x00"
            )
            await self.conn.send(b"\x00\x0c")
            await asyncio.wait_for(self._remote_ready.wait(), 8)
        if self.closed or self.conn.failed:
            raise ViperError("Call ended during answer")
        self._answer_task = None
        self.answered = True
        self._sender = asyncio.create_task(self._send_audio())

    def microphone(self, payload: bytes) -> None:
        """Accept exactly 20 ms of PCMA; drop old frames rather than build latency."""
        if not self.answered or self.closed or len(payload) != 160:
            raise ViperError("No answered call for microphone audio")
        if self._tx_queue.full():
            self._tx_queue.get_nowait()
        self._tx_queue.put_nowait(payload)

    async def _send_audio(self) -> None:
        sequence, timestamp, ssrc = 0, 0, secrets.randbits(32)
        try:
            while not self.closed:
                # No silence is transmitted without actual user microphone frames.
                data = await self._tx_queue.get()
                header = struct.pack(
                    ">BBHII", 0x80, 8, sequence & 65535, timestamp & 0xFFFFFFFF, ssrc
                )
                await self.session._send_frame(self.tx_handle, header + data)
                self.audio_sent += 1
                sequence += 1
                timestamp += 160
                await asyncio.sleep(0.02)
        except ViperError, OSError:
            self.answered = False

    async def close(self, *, release: bool = True) -> None:
        """Stop microphone first and release only the call owned by this object."""
        if self.closed:
            return
        self.closed = True
        self.answered = False
        if self._answer_task and self._answer_task is not asyncio.current_task():
            self._answer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, ViperError):
                await self._answer_task
        if self._sender:
            self._sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sender
        if release and self.session.connected and not self.conn.failed:
            with contextlib.suppress(ViperError, OSError, TimeoutError):
                async with asyncio.timeout(2):
                    await self.conn.send(b"\x00\x0e\x00")
        for handle in self.handles:
            self.session.set_binary_handler(handle, None)
            with contextlib.suppress(ViperError, OSError, TimeoutError):
                async with asyncio.timeout(1):
                    await self.session.close_channel(handle)
        self.session.set_incoming_channel_handler(None)
        if self.session.media is self:
            self.session.media = None
