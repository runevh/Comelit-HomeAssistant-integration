"""
Bounded, receive-only video experiment using a pinned external reference client.

No integration code or persistent camera service is installed. Only safe summary
fields are printed. Credentials are read privately from the development HA entry.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import subprocess
import sys
from collections import Counter
from pathlib import Path

from experiments.h264 import SnapshotDecoder

REFERENCE_REVISION = "4c1cba401030c705132174e60fb3886f667a442f"
ALLOWED_OPCODES = {0x0001, 0x0003, 0x000E, 0x0011, 0x0040}
ALLOWED_CHANNELS = {"UAUT", "UCFG", "CTPP", "UDPM", "RTPC"}


class ProbeBlockedError(Exception):
    """The experiment attempted a command outside its explicit scope."""


def check_ctp(packet, entrance):
    """Allow camera signaling only; no door release or actuator opcode."""
    if packet.opcode is None:
        return
    if packet.opcode not in ALLOWED_OPCODES:
        raise ProbeBlockedError("non-video opcode")
    if packet.opcode == 0x0001 and (
        packet.destination != entrance or packet.body[-2:] != b"II"
    ):
        raise ProbeBlockedError("not an entrance self-view request")


def load_reference(path):
    """Require the reviewed revision; import without installing another integration."""
    revision = subprocess.check_output(  # noqa: S603 - fixed git command
        ["git", "-C", str(path), "rev-parse", "HEAD"],  # noqa: S607
        text=True,
    ).strip()
    if revision != REFERENCE_REVISION:
        raise ProbeBlockedError("unreviewed reference revision")
    sys.path.insert(0, str(path / "custom_components" / "comelit_vip"))
    return (
        importlib.import_module("viper.session"),
        importlib.import_module("viper.call"),
        importlib.import_module("viper.ctp"),
    )


def private_entry(config_path, expected_subaddress):
    """Select only the existing integration entry, never create or reset a user."""
    entries = json.loads((config_path / ".storage/core.config_entries").read_text())[
        "data"
    ]["entries"]
    entries = [
        e
        for e in entries
        if e["domain"] == "comelit_local" and not e.get("disabled_by")
    ]
    if len(entries) != 1 or expected_subaddress == 8:
        raise ProbeBlockedError("select a dedicated development entry")
    return entries[0]["data"]


def guarded_session(session_module, ctp_module, report):
    """Guard the lowest send path, including calls made inside the reference client."""

    class ReceiveOnlySession(session_module.ViperSession):
        async def _send_frame(self, handle, payload):
            if handle == 0:
                if payload[:4] == b"\xcd\xab\x01\x00":
                    if payload[8:12].decode("ascii") not in ALLOWED_CHANNELS:
                        raise ProbeBlockedError("channel outside video scope")
                elif payload[:4] not in (b"\xcd\xab\x02\x00", b"\xef\x01\x03\x00"):
                    raise ProbeBlockedError("unsupported management message")
            elif payload.startswith(b"{"):
                if json.loads(payload).get("message") not in (
                    "access",
                    "get-configuration",
                ):
                    raise ProbeBlockedError("configuration mutation")
            elif handle == self._ctpp_handle:
                packet = ctp_module.parse_ctp_packet(payload)
                check_ctp(packet, self.config.entrance if self.config else None)
                if packet.opcode is not None:
                    report["sent_opcodes"][f"0x{packet.opcode:04x}"] += 1
            else:
                # RTP transmit/audio data can never pass this boundary.
                raise ProbeBlockedError("outbound media")
            await super()._send_frame(handle, payload)

        async def _accept_inbound(self, _packet):
            report["incoming_calls_ignored"] += 1

        async def _dispatch_ctp(self, payload):
            try:
                packet = ctp_module.parse_ctp_packet(payload)
                if packet.opcode is not None:
                    report["received_opcodes"][f"0x{packet.opcode:04x}"] += 1
                if packet.release_cause is not None:
                    report["release_causes"].append(packet.release_cause)
            except ValueError:
                report["malformed_ctp_packets"] += 1
            await super()._dispatch_ctp(payload)

        async def start_video_transport(self):
            # Do not open CSPB or install an inbound call listener.
            self._ctpp_handle = await self.open_channel(
                "CTPP", ctp_module.encode_logaddr(self.source)
            )
            await self._register()

    return ReceiveOnlySession


async def run(args):  # noqa: PLR0915 - bounded experiment lifecycle
    """Attempt one video session, collect counts, and always close it."""
    report = {
        "stage": "initializing",
        "result": "not_started",
        "video_packets": 0,
        "video_bytes": 0,
        "payload_types": Counter(),
        "nal_types": Counter(),
        "sent_opcodes": Counter(),
        "received_opcodes": Counter(),
        "release_causes": [],
        "incoming_calls_ignored": 0,
        "malformed_ctp_packets": 0,
    }
    session = call = None
    decoder = None
    # The upstream logger can print raw gateway errors. Never enable it here.
    logging.disable(logging.CRITICAL)
    try:
        decoder = SnapshotDecoder(args.snapshot) if args.snapshot else None
        session_module, call_module, ctp_module = load_reference(args.reference)
        data = private_entry(args.ha_config, args.expected_subaddress)
        session_class = guarded_session(session_module, ctp_module, report)
        session = session_class(data["host"], data["port"])
        async with asyncio.timeout(45):
            report["stage"] = "connect"
            await session.connect()
            report["stage"] = "authenticate"
            await session.authenticate(data["token"])
            report["stage"] = "configuration"
            config = await session.get_configuration()
            if (
                config.apt_subaddress != args.expected_subaddress
                or len(config.entrances) != 1
            ):
                raise ProbeBlockedError("unexpected user slot or entrance selection")  # noqa: TRY301
            report["entrance_count"] = len(config.entrances)
            report["stage"] = "register_video_transport"
            await session.start_video_transport()
            report["stage"] = "request_video"
            call = call_module.VideoCall(session, config.entrance)
            await call.open(timeout=5)
            report["stage"] = "receive_video"
            try:
                async with asyncio.timeout(args.seconds):
                    async for packet in call.packets():
                        report["video_packets"] += 1
                        report["video_bytes"] += len(packet.payload)
                        report["payload_types"][packet.payload_type] += 1
                        if decoder is not None:
                            decoder.feed(
                                packet.payload, packet.sequence, packet.timestamp
                            )
                        if packet.payload:
                            report["nal_types"][packet.payload[0] & 0x1F] += 1
            except TimeoutError:
                pass
            report["result"] = (
                "media_received" if report["video_packets"] else "no_media"
            )
    except Exception as err:  # noqa: BLE001 - suppress private upstream errors
        # No str(err), traceback, raw response, address, or credential output.
        report["result"] = "failed"
        report["error_type"] = type(err).__name__
    finally:
        if call is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await call.close()
        if session is not None:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(5):
                    await session.close()
        report["session_closed"] = session is None or not session.connected
    if decoder:
        report.update(
            decoded_frames=decoder.frames,
            decode_errors=decoder.errors,
            resolution=decoder.resolution,
            snapshot_saved=decoder.saved,
        )
    print(json.dumps(report, indent=2))  # noqa: T201 - redacted CLI result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--ha-config", type=Path, required=True)
    parser.add_argument("--expected-subaddress", type=int, required=True)
    parser.add_argument("--snapshot", type=Path, help="Optional private JPEG path")
    parser.add_argument("--seconds", type=int, choices=range(1, 16), default=8)
    asyncio.run(run(parser.parse_args()))
