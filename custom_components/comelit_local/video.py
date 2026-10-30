"""On-demand, authenticated RTSP bridge hosted by Home Assistant."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import secrets
from typing import TYPE_CHECKING

from .vendor.call import VideoCall
from .vendor.ctp import encode_logaddr, parse_ctp_packet
from .vendor.rtsp import RtspRelay
from .vendor.session import ViperSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from .client import GatewayInfo

_LOGGER = logging.getLogger(__name__)
# Upstream errors/logging may contain raw replies. Never propagate them to HA.
_WIRE_LOGGER = logging.Logger("comelit_local.private_transport")  # noqa: LOG001 - private logger cannot be enabled by HA debug settings
_WIRE_LOGGER.disabled = True
_VIDEO_OPCODES = {0x0001, 0x0003, 0x000E, 0x0011, 0x0040}
_VIDEO_CHANNELS = {"UAUT", "UCFG", "CTPP", "UDPM", "RTPC"}


class VideoError(Exception):
    """A fixed, credential-free failure exposed to the RTSP relay."""


class VideoSession(ViperSession):
    """Allow receive-only entrance video; block door and actuator commands."""

    async def _send_frame(self, handle: int, payload: bytes) -> None:
        if handle == 0:
            if payload[:4] == b"\xcd\xab\x01\x00":
                if payload[8:12].decode("ascii") not in _VIDEO_CHANNELS:
                    raise VideoError("Unsupported video channel")
            elif payload[:4] not in (b"\xcd\xab\x02\x00", b"\xef\x01\x03\x00"):
                raise VideoError("Unsupported channel operation")
        elif payload.startswith(b"{"):
            # The inherited public JSON API is limited below as well.
            if json.loads(payload).get("message") not in (
                "access",
                "get-configuration",
            ):
                raise VideoError("Unsupported video request")
        elif handle == self._ctpp_handle:
            packet = parse_ctp_packet(payload)
            if packet.opcode is not None and packet.opcode not in _VIDEO_OPCODES:
                raise VideoError("Physical control and call answering are disabled")
            if packet.opcode == 0x0001 and (
                not self.config
                or not self.config.is_entrance(packet.destination)
                or packet.body[-2:] != b"II"
            ):
                raise VideoError("Only configured entrance self-view is allowed")
        else:
            raise VideoError("Outgoing media is disabled")
        await super()._send_frame(handle, payload)

    async def _accept_inbound(self, _packet: object) -> None:
        # Do not answer a ring or send a release to someone else's call.
        return

    async def start_video_transport(self) -> None:
        """Register our dedicated identity without a push/call listener."""
        self._ctpp_handle = await self.open_channel("CTPP", encode_logaddr(self.source))
        await self._register()


class OwnedCall(VideoCall):
    """Own both the video call and its short-lived authenticated session."""

    async def close(self) -> None:
        """Release our call, then close the socket even after interrupted setup."""

        async def cleanup() -> None:
            try:
                async with asyncio.timeout(5):
                    await VideoCall.close(self)
            finally:
                async with asyncio.timeout(5):
                    await self.session.close()

        task = getattr(self, "_cleanup_task", None)
        if task is None:
            task = self._cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise


class AuthenticatedRelay(RtspRelay):
    """Require separate stream credentials before any camera session can start."""

    def __init__(self, *args: object, password: str, **kwargs: object) -> None:
        """Store only the expected authorization header, never log it."""
        super().__init__(*args, **kwargs)
        self._authorization = b"Basic " + base64.b64encode(
            f"comelit:{password}".encode()
        )

    async def _handle_request(
        self, client: object, request: tuple, writer: asyncio.StreamWriter
    ) -> None:
        method, url, headers = request
        supplied = headers.get("authorization", "").encode("utf-8")
        if not secrets.compare_digest(supplied, self._authorization):
            await self._reply(
                writer,
                401,
                "Unauthorized",
                headers.get("cseq", "0"),
                {"WWW-Authenticate": 'Basic realm="Comelit video"'},
            )
            return
        await super()._handle_request(client, (method, url, headers), writer)


class VideoBridge:
    """Share one on-demand video call between local and remote RTSP viewers."""

    def __init__(  # noqa: PLR0913 - gateway connection and listener settings
        self,
        host: str,
        port: int,
        token: str,
        info: GatewayInfo,
        options: dict,
        *,
        reauth: Callable[[], None] | None = None,
    ) -> None:
        """Remember private connection data without opening any gateway session."""
        self._host, self._port, self._token = host, port, token
        self._info = info
        self._reauth = reauth
        self._retry_after = 0.0
        self._stopping = False
        self.started = False
        self.calls_started = 0
        self.last_error: str | None = None
        self.relay = AuthenticatedRelay(
            self._new_call,
            password=options["rtsp_password"],
            targets=[entrance.address for entrance in info.entrances],
            host="0.0.0.0" if options.get("rtsp_lan") else "127.0.0.1",  # noqa: S104 - explicit LAN opt-in
            port=options.get("rtsp_port", 8557),
            logger=_WIRE_LOGGER,
        )

    async def start(self) -> None:
        """Bind RTSP; wait for an authenticated viewer before requesting video."""
        await self.relay.start()
        self.started = True

    async def stop(self) -> None:
        """Release all viewers, call state, and gateway sockets on HA unload/stop."""
        self._stopping = True
        await self.relay.stop()
        self.started = False

    async def _new_call(self, target: str) -> OwnedCall:
        """Authenticate on demand and revalidate the configured entrance."""
        if self._stopping or asyncio.get_running_loop().time() < self._retry_after:
            raise VideoError("Video temporarily unavailable")
        session = VideoSession(self._host, self._port, logger=_WIRE_LOGGER)
        call = OwnedCall(session, target, logger=_WIRE_LOGGER)
        try:
            async with asyncio.timeout(30):
                await session.connect()
                await session.authenticate(self._token)
                config = await session.get_configuration()
                if not config.is_entrance(target):
                    raise VideoError("Entrance no longer configured")  # noqa: TRY301
                await session.start_video_transport()
                await call.open(timeout=5)
        except BaseException as err:
            with contextlib.suppress(Exception):
                await call.close()
            if not isinstance(err, Exception):
                raise
            self._retry_after = asyncio.get_running_loop().time() + 15
            self.last_error = type(err).__name__
            if self.last_error == "ViperAuthError" and self._reauth:
                self._reauth()
            _LOGGER.warning("Entrance video unavailable (%s)", self.last_error)
            raise VideoError("Could not start entrance video") from None
        self.calls_started += 1
        self.last_error = None
        return call

    def diagnostics(self) -> dict:
        """Return counts/state only, with no stream credentials or private addresses."""
        return {
            "listening": self.started,
            "viewers": self.relay.viewers,
            "session_active": self.relay.serving is not None,
            "calls_started": self.calls_started,
            "last_error_type": self.last_error,
        }
