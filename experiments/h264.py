"""Minimal H.264 RTP depacketizer for a bounded camera compatibility probe."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class AnnexB:
    """Assemble single NAL, STAP-A and FU-A packets; drop damaged fragments."""

    def __init__(self) -> None:
        """Start without a pending fragmented NAL."""
        self.fragment = bytearray()
        self.sequence: int | None = None
        self.timestamp: int | None = None

    def feed(self, payload: bytes, sequence: int, timestamp: int) -> list[bytes]:  # noqa: PLR0911 - packet formats
        """Return complete Annex-B NAL units for an H.264 decoder."""
        discontinuity = (
            self.sequence is not None and sequence != (self.sequence + 1) % 65536
        ) or (self.timestamp is not None and timestamp != self.timestamp)
        self.sequence, self.timestamp = sequence, timestamp
        if discontinuity:
            self.fragment.clear()
        if not payload:
            return []
        kind = payload[0] & 0x1F
        if 1 <= kind <= 23:
            self.fragment.clear()
            return [b"\0\0\0\1" + payload]
        if kind == 24:
            self.fragment.clear()
            units = []
            offset = 1
            while offset + 2 <= len(payload):
                size = int.from_bytes(payload[offset : offset + 2], "big")
                offset += 2
                if not size or offset + size > len(payload):
                    return []
                units.append(b"\0\0\0\1" + payload[offset : offset + size])
                offset += size
            return units if offset == len(payload) else []
        if kind != 28 or len(payload) < 3:
            self.fragment.clear()
            return []
        start, end = bool(payload[1] & 0x80), bool(payload[1] & 0x40)
        if start and end:
            self.fragment.clear()
            return []
        if start:
            self.fragment = bytearray(
                b"\0\0\0\1" + bytes([(payload[0] & 0xE0) | (payload[1] & 0x1F)])
            )
        if not self.fragment:
            return []
        self.fragment.extend(payload[2:])
        if len(self.fragment) > 4 * 1024 * 1024:
            self.fragment.clear()
            return []
        if end:
            result = bytes(self.fragment)
            self.fragment.clear()
            return [result]
        return []


class SnapshotDecoder:
    """Decode live packets and save only the first frame to a private file."""

    def __init__(self, path: Path) -> None:
        """Load PyAV only when an image was explicitly requested."""
        import av  # noqa: PLC0415 - optional experiment dependency

        self.decode_error = av.error.InvalidDataError
        self.errors = 0
        self.path = path
        self.codec = av.CodecContext.create("h264", "r")
        self.assembler = AnnexB()
        self.frames = 0
        self.resolution: tuple[int, int] | None = None
        self.saved = False

    def feed(self, payload: bytes, sequence: int, timestamp: int) -> None:
        """Decode complete NALs; never persist raw packets or audio."""
        for nal in self.assembler.feed(payload, sequence, timestamp):
            for packet in self.codec.parse(nal):
                try:
                    frames = self.codec.decode(packet)
                except self.decode_error:
                    self.errors += 1
                    continue
                for frame in frames:
                    self.frames += 1
                    self.resolution = (frame.width, frame.height)
                    if not self.saved:
                        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with self.path.open("wb") as image_file:
                            os.fchmod(image_file.fileno(), 0o600)
                            frame.to_image().save(image_file, format="JPEG")
                        self.saved = True
