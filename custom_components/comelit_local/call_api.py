"""Authenticated HA websocket interface for the experimental call card."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN
from .vendor.session import ViperError

if TYPE_CHECKING:
    from .calls import CallController

KEY = f"{DOMAIN}_call_api"


async def async_register(hass: HomeAssistant) -> None:
    """Register one static card resource and websocket command set."""
    if KEY in hass.data:
        return
    hass.data[KEY] = {}
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/comelit_local/comelit-call-card.js",
                str(Path(__file__).parent / "www" / "comelit-call-card.js"),
                cache_headers=False,
            ),
            StaticPathConfig(
                "/comelit_local/microphone.js",
                str(Path(__file__).parent / "www" / "microphone.js"),
                cache_headers=False,
            ),
        ]
    )
    websocket_api.async_register_command(hass, subscribe)
    websocket_api.async_register_command(hass, control)


def controller(hass: HomeAssistant, entry_id: str) -> CallController:
    """Resolve a loaded call controller without exposing entry credentials."""
    entry = hass.config_entries.async_get_entry(entry_id)
    runtime = getattr(entry, "runtime_data", None)
    if (
        entry is None
        or entry.domain != DOMAIN
        or runtime is None
        or runtime.calls is None
    ):
        raise ViperError("Call listener is not enabled")
    return runtime.calls


@websocket_api.websocket_command(
    {
        vol.Required("type"): "comelit_local/call_subscribe",
        vol.Required("entry_id"): str,
    }
)
@websocket_api.async_response
async def subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Subscribe to state; audio is delivered only to the explicit call owner."""
    if not connection.user.is_admin:
        connection.send_error(
            msg["id"],
            "unauthorized",
            "Call controls currently require an administrator",
        )
        return
    try:
        calls = controller(hass, msg["entry_id"])
    except ViperError:
        connection.send_error(msg["id"], "not_ready", "Call listener is not enabled")
        return
    key = (id(connection), msg["entry_id"])
    registry = hass.data[KEY]
    if key in registry:
        connection.send_error(
            msg["id"],
            "already_subscribed",
            "This connection already has a call card subscription",
        )
        return
    owner = object()

    @callback
    def state(data: dict, _event_type: str | None = None) -> None:
        connection.send_event(msg["id"], {"kind": "state", **data})

    @callback
    def audio(codec: int, data: bytes) -> None:
        connection.send_event(
            msg["id"],
            {"kind": "audio", "codec": codec, "data": base64.b64encode(data).decode()},
        )

    cancel = async_dispatcher_connect(hass, calls.signal, state)
    registry[key] = (owner, audio)

    @callback
    def unsubscribe() -> None:
        cancel()
        registry.pop(key, None)
        try:
            active = controller(hass, msg["entry_id"])
        except ViperError:
            return
        if active.owner is owner:
            active.entry.async_create_background_task(
                hass, active.hangup(), "Comelit browser disconnected"
            )

    connection.subscriptions[msg["id"]] = unsubscribe
    connection.send_result(msg["id"])
    state(calls.snapshot())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "comelit_local/call_control",
        vol.Required("entry_id"): str,
        vol.Required("call_id"): str,
        vol.Required("action"): vol.In(("answer", "hangup", "heartbeat", "microphone")),
        vol.Optional("data"): vol.All(str, vol.Length(max=216)),
    }
)
@websocket_api.async_response
async def control(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Reject stale call IDs, competing browsers, and unbounded audio payloads."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator required")
        return
    try:
        calls = controller(hass, msg["entry_id"])
        owner, audio = hass.data[KEY][(id(connection), msg["entry_id"])]
        action = msg["action"]
        if action == "answer":
            await calls.answer(msg["call_id"], owner, audio)
        else:
            calls.heartbeat(msg["call_id"], owner)
            if action == "hangup":
                await calls.hangup()
            elif action == "microphone":
                calls.microphone(
                    msg["call_id"],
                    owner,
                    base64.b64decode(msg.get("data", ""), validate=True),
                )
        connection.send_result(msg["id"])
    except ViperError, OSError, TimeoutError, KeyError, binascii.Error, ValueError:
        connection.send_error(
            msg["id"],
            "call_failed",
            "Call unavailable or request rejected; check call state and diagnostics",
        )
