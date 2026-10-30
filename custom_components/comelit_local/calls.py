"""Ring lifecycle, single-browser call ownership, and reconnect supervision."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .call_transport import CallSession, InboundMedia
from .const import DOMAIN
from .vendor.session import ViperAuthError, ViperError

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from . import ComelitConfigEntry
    from .vendor.models import RingEvent


class CallController:
    """One dedicated gateway connection and at most one user-owned incoming call."""

    def __init__(self, hass: HomeAssistant, entry: ComelitConfigEntry) -> None:
        """Initialize without connecting or answering."""
        self.hass, self.entry = hass, entry
        self.signal = f"{DOMAIN}_call_{entry.entry_id}"
        self.state = "disconnected"
        self.call_id: str | None = None
        self.entrance: str | None = None
        self.last_error: str | None = None
        self.rings = 0
        self.session: CallSession | None = None
        self.media: InboundMedia | None = None
        self.event: RingEvent | None = None
        self.owner: object | None = None
        self.audio_callback: Callable[[int, bytes], None] | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = False
        self._last_heartbeat = 0.0
        self._seen: dict[tuple, float] = {}

    def snapshot(self) -> dict:
        """Return UI state without addresses, tokens, or raw protocol data."""
        return {
            "state": self.state,
            "call_id": self.call_id,
            "entrance": self.entrance,
            "error": self.last_error,
        }

    def diagnostics(self) -> dict:
        """Expose safe connection and media counters."""
        return {
            "state": self.state,
            "rings": self.rings,
            "last_error": self.last_error,
            "audio_received": self.media.audio_received if self.media else 0,
            "audio_sent": self.media.audio_sent if self.media else 0,
        }

    def _publish(
        self, event_type: str | None = None, event_data: dict | None = None
    ) -> None:
        async_dispatcher_send(self.hass, self.signal, self.snapshot(), event_type)
        if event_type:
            self.hass.bus.async_fire(
                f"{DOMAIN}_doorbell",
                {
                    "entry_id": self.entry.entry_id,
                    "event_type": event_type,
                    **(event_data or self.snapshot()),
                },
            )

    def start(self) -> None:
        """Start a supervised listener; no media is requested while idle."""
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(), "Comelit call listener"
        )

    async def stop(self) -> None:
        """Close owned media before stopping the listener."""
        self._stopping = True
        await self.hangup()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.state = "disconnected"
        self._publish()

    async def _run(self) -> None:
        retry = 2
        while not self._stopping:
            disconnected = asyncio.Event()
            session = self.session = CallSession(
                self.entry.data["host"], self.entry.data["port"]
            )
            session.on_ring = self._ring
            session.on_call_end = self._ended
            session.on_disconnect = lambda _error, signal=disconnected: signal.set()
            try:
                await session.connect()
                await session.authenticate(self.entry.data["token"])
                await session.get_configuration()
                await session.start_ctp()
                self.last_error, retry = None, 2
                if self.call_id is None:
                    self.state = "idle"
                self._publish()
                while not disconnected.is_set():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(disconnected.wait(), 2)
                    if (
                        self.owner
                        and asyncio.get_running_loop().time() - self._last_heartbeat > 8
                    ):
                        await self.hangup()
                    if (
                        session.registration_age
                        and session.registration_age > 2400
                        and self.call_id is None
                    ):
                        await session.refresh_registration()
            except ViperAuthError:
                self.last_error = "authentication_failed"
                self.entry.async_start_reauth(self.hass)
                return
            except ViperError, OSError, ValueError, TimeoutError:
                self.last_error = "connection_failed"
            finally:
                await self.hangup(release=False)
                await session.close()
                self.state = "disconnected"
                self._publish()
            await asyncio.sleep(retry)
            retry = min(retry * 2, 60)

    def _ring(self, event: RingEvent) -> None:
        if self._stopping:
            return
        now = asyncio.get_running_loop().time()
        key = (event.call_id, event.connection, event.origin)
        self._seen = {k: t for k, t in self._seen.items() if now - t < 120}
        if key in self._seen:
            return
        self._seen[key] = now
        if self.call_id is not None:
            return
        entrance = next(
            (
                e
                for e in self.entry.runtime_data.info.entrances
                if e.address == (event.origin or event.caller)[:8]
            ),
            None,
        )
        if entrance is None or event.tag == b"FF":
            return  # Internal/floor calls must not trigger entrance automations.
        self.event = event
        self.call_id = secrets.token_hex(12)
        self.entrance = entrance.name
        self.state, self.last_error = "ringing", None
        self.rings += 1
        self._publish("ring")
        if self.entry.options.get("call_notifications", True):
            persistent_notification.async_create(
                self.hass,
                "Someone is ringing. Open the Comelit call card to answer.",
                title="Doorbell",
                notification_id=f"{DOMAIN}_{self.entry.entry_id}",
            )

    def _ended(self, event: RingEvent, _cause: int | None) -> None:
        if (
            self.event is not None
            and event.connection == self.event.connection
            and event.call_id == self.event.call_id
        ):
            self.entry.async_create_background_task(
                self.hass, self.hangup(release=False), "Comelit call ended"
            )

    async def answer(
        self, call_id: str, owner: object, audio: Callable[[int, bytes], None]
    ) -> None:
        """Claim an exact current ring; reject stale or competing browser actions."""
        async with self._lock:
            if (
                self.state != "ringing"
                or call_id != self.call_id
                or self.owner is not None
                or self.event is None
                or self.session is None
            ):
                raise ViperError("No matching ring available")
            conn = self.session._connections.get(self.event.connection)
            if conn is None:
                raise ViperError("Ring connection ended")
            self.owner, self.audio_callback = owner, audio
            self._last_heartbeat = asyncio.get_running_loop().time()
            self.state = "answering"
            self._publish()
            media = self.media = InboundMedia(self.session, conn, audio)
        try:
            await media.answer()
            if self.media is not media or self.call_id != call_id:
                raise ViperError("Call ended while answering")
            self.state = "connected"
            self._publish("answered")
        except ViperError, OSError, TimeoutError, asyncio.CancelledError:
            if self.media is media:
                self.last_error = "answer_failed"
                await self.hangup()
            raise

    def heartbeat(self, call_id: str, owner: object) -> None:
        """Refresh the browser lease only for the current call owner."""
        if owner is not self.owner or call_id != self.call_id:
            raise ViperError("This browser does not own the call")
        self._last_heartbeat = asyncio.get_running_loop().time()

    def microphone(self, call_id: str, owner: object, data: bytes) -> None:
        """Forward only the owner's current microphone frames."""
        self.heartbeat(call_id, owner)
        if self.media is None:
            raise ViperError("No media session")
        self.media.microphone(data)

    async def hangup(self, *, release: bool = True) -> None:
        """Idempotently clear state and close only explicitly claimed media."""
        async with self._lock:
            previous = self.snapshot()
            had_call, missed = self.call_id is not None, self.state == "ringing"
            media, self.media = self.media, None
            self.owner, self.audio_callback, self.event = None, None, None
            self.call_id, self.entrance = None, None
            self.state = (
                "idle" if self.session and self.session.connected else "disconnected"
            )
            if media:
                await media.close(release=release)
            if had_call:
                persistent_notification.async_dismiss(
                    self.hass, f"{DOMAIN}_{self.entry.entry_id}"
                )
                self._publish("missed_call" if missed else "call_ended", previous)
