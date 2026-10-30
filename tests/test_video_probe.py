"""Ensure the temporary video probe cannot send physical-control opcodes."""

from types import SimpleNamespace

import pytest

from experiments.video_probe import ProbeBlockedError, check_ctp


@pytest.mark.parametrize("opcode", [0x000D, 0x0020, 0x002D, 0xFFFF])
def test_actuator_door_and_unknown_opcodes_blocked(opcode):
    with pytest.raises(ProbeBlockedError):
        check_ctp(SimpleNamespace(opcode=opcode), "00000101")


def test_only_self_view_invite_to_configured_entrance_allowed():
    check_ctp(SimpleNamespace(opcode=1, destination="00000101", body=b"II"), "00000101")
    for destination, tag in [("00000001", b"II"), ("00000101", b"PP")]:
        with pytest.raises(ProbeBlockedError):
            check_ctp(
                SimpleNamespace(opcode=1, destination=destination, body=tag), "00000101"
            )


def test_h264_fragment_assembly_and_lost_packet():
    from experiments.h264 import AnnexB  # noqa: PLC0415

    depacketizer = AnnexB()
    assert depacketizer.feed(b"\x7c\x85first", 10, 100) == []
    assert depacketizer.feed(b"\x7c\x45last", 11, 100) == [b"\0\0\0\1\x65firstlast"]
    assert depacketizer.feed(b"\x7c\x85first", 12, 200) == []
    assert depacketizer.feed(b"\x7c\x45last", 14, 200) == []


def test_h264_single_and_aggregate_packets():
    from experiments.h264 import AnnexB  # noqa: PLC0415

    depacketizer = AnnexB()
    assert depacketizer.feed(b"\x67sps", 1, 100) == [b"\0\0\0\1\x67sps"]
    assert depacketizer.feed(b"\x78\0\2\x67a\0\2\x68b", 2, 100) == [
        b"\0\0\0\1\x67a",
        b"\0\0\0\1\x68b",
    ]
    assert depacketizer.feed(b"\x78\0\xffbad", 3, 100) == []


async def test_transport_guard_blocks_actuator_and_outgoing_audio():
    from collections import Counter  # noqa: PLC0415

    from experiments.video_probe import guarded_session  # noqa: PLC0415

    class Base:
        _ctpp_handle = 12
        config = SimpleNamespace(entrance="00000101")
        sent = False

        async def _send_frame(self, _handle, _payload):
            self.sent = True

    session_class = guarded_session(
        SimpleNamespace(ViperSession=Base),
        SimpleNamespace(parse_ctp_packet=lambda _: SimpleNamespace(opcode=0x0020)),
        {"sent_opcodes": Counter()},
    )
    session = session_class()
    for handle, payload in [
        (12, b"actuator"),
        (13, b"audio"),
        (11, b'{"message":"user-activation"}'),
    ]:
        with pytest.raises(ProbeBlockedError):
            await session._send_frame(handle, payload)
    assert not session.sent


def test_decoder_waits_through_missing_keyframe(monkeypatch, tmp_path):
    import sys  # noqa: PLC0415

    from experiments.h264 import SnapshotDecoder  # noqa: PLC0415

    class MissingKeyframeError(Exception):
        pass

    class Codec:
        def parse(self, _nal):
            return [b"packet"]

        def decode(self, _packet):
            raise MissingKeyframeError

    monkeypatch.setitem(
        sys.modules,
        "av",
        SimpleNamespace(
            error=SimpleNamespace(InvalidDataError=MissingKeyframeError),
            CodecContext=SimpleNamespace(create=lambda *_args: Codec()),
        ),
    )
    decoder = SnapshotDecoder(tmp_path / "snapshot.jpg")
    decoder.feed(b"\x61data", 1, 100)
    assert decoder.errors == 1
    assert decoder.frames == 0
    assert not decoder.saved
