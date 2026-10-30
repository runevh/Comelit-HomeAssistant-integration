"""Exercise RTSP auth, shared sessions, and teardown with a fake video source."""

import asyncio
import base64
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.comelit_local.vendor.ctp import build_ctp_packet
from custom_components.comelit_local.vendor.rtp import RtpPacket
from custom_components.comelit_local.vendor.rtsp import RtspRelay
from custom_components.comelit_local.video import (
    AuthenticatedRelay,
    OwnedCall,
    VideoError,
    VideoSession,
)

PASSWORD = "test-stream-password"
AUTH = base64.b64encode(f"comelit:{PASSWORD}".encode()).decode()


class FakeCall:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False
        self.ended = asyncio.Event()

    async def packets(self):
        while (packet := await self.queue.get()) is not None:
            yield packet

    async def close(self):
        self.closed = True
        self.ended.set()
        self.queue.put_nowait(None)


async def request(reader, writer, method, *, auth=True, path="/comelit", headers=""):  # noqa: PLR0913
    authorization = f"Authorization: Basic {AUTH}\r\n" if auth else ""
    writer.write(
        (
            f"{method} rtsp://127.0.0.1{path} RTSP/1.0\r\n"
            f"CSeq: 1\r\n{authorization}{headers}\r\n"
        ).encode()
    )
    await writer.drain()
    async with asyncio.timeout(2):
        raw = await reader.readuntil(b"\r\n\r\n")
        for line in raw.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                await reader.readexactly(int(line.split(b":", 1)[1]))
    return int(raw.split()[1])


@pytest.mark.usefixtures("socket_enabled")
async def test_authenticated_viewers_share_one_call_and_stop_when_gone():
    call = FakeCall()
    factory = AsyncMock(return_value=call)
    relay = AuthenticatedRelay(factory, password=PASSWORD, targets=["00000101"], port=0)
    with patch("custom_components.comelit_local.vendor.rtsp.IDLE_GRACE", 0.01):
        await relay.start()
        port = relay._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        try:
            assert await request(reader, writer, "DESCRIBE", auth=False) == 401
            factory.assert_not_called()
            assert (
                await request(reader, writer, "DESCRIBE", path="/not-configured") == 404
            )
            factory.assert_not_called()
            for r, w in [(reader, writer), (reader2, writer2)]:
                assert await request(r, w, "DESCRIBE") == 200
                assert (
                    await request(
                        r,
                        w,
                        "SETUP",
                        headers="Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n",
                    )
                    == 200
                )
                assert await request(r, w, "PLAY") == 200
            factory.assert_awaited_once_with("00000101")
            assert relay.viewers == 2
            for nal in (b"\x67sps", b"\x68pps", b"\x65frame"):
                await call.queue.put(
                    RtpPacket(
                        marker=True,
                        payload_type=99,
                        sequence=1,
                        timestamp=100,
                        ssrc=1,
                        payload=nal,
                    )
                )
            for r in (reader, reader2):
                for nal in (b"\x67sps", b"\x68pps", b"\x65frame"):
                    header = await asyncio.wait_for(r.readexactly(4), 2)
                    assert header[:2] == b"$\0"
                    packet = await r.readexactly(int.from_bytes(header[2:], "big"))
                    assert packet[1] & 127 == 96
                    assert packet[12:] == nal
            # Real RTSP clients send interleaved RTCP during playback.
            writer.write(b"$\1\0\4RTCP")
            assert await request(reader, writer, "GET_PARAMETER") == 200
            assert await request(reader, writer, "TEARDOWN") == 200
            assert relay.viewers == 1
            assert not call.closed
            assert await request(reader2, writer2, "TEARDOWN") == 200
            await asyncio.wait_for(call.ended.wait(), 2)
            assert relay.viewers == 0
        finally:
            writer.close()
            writer2.close()
            await writer.wait_closed()
            await writer2.wait_closed()
            await relay.stop()
        assert relay._server is None


async def test_transport_rejects_physical_commands_and_audio():
    session = VideoSession("127.0.0.1")
    session._ctpp_handle = 12
    for payload in (b"payload", b'{"message":"user-activation"}'):
        with pytest.raises(VideoError):
            await session._send_frame(13, payload)


async def test_cancelling_close_still_closes_gateway_session():
    session = AsyncMock(spec=VideoSession)
    call = OwnedCall(session, "00000101")
    started, finish = asyncio.Event(), asyncio.Event()

    async def slow_close():
        started.set()
        await finish.wait()

    session.close.side_effect = slow_close
    task = asyncio.create_task(call.close())
    await started.wait()
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    session.close.assert_awaited_once()


async def test_rtsp_parser_skips_interleaved_rtcp():
    reader = asyncio.StreamReader()
    reader.feed_data(
        b"$\1\0\4RTCPGET_PARAMETER rtsp://localhost/comelit RTSP/1.0\r\nCSeq: 3\r\n\r\n"
    )
    reader.feed_eof()
    assert await RtspRelay._read_request(reader) == (
        "GET_PARAMETER",
        "rtsp://localhost/comelit",
        {"cseq": "3"},
    )


@pytest.mark.parametrize("opcode", [0x000D, 0x0020, 0x002D])
async def test_real_video_session_blocks_physical_control_packets(opcode):
    session = VideoSession("127.0.0.1")
    session._ctpp_handle = 12
    payload = build_ctp_packet(
        flags=0xC0,
        connection=b"\x01\x02",
        sequence=1,
        acknowledgement=1,
        body=opcode.to_bytes(2, "big"),
        source="000000019",
        destination="00001001",
    )
    with pytest.raises(VideoError):
        await session._send_frame(12, payload)


async def test_gateway_error_is_sanitized_and_retry_is_bounded(caplog):
    from custom_components.comelit_local.client import (  # noqa: PLC0415
        Entrance,
        GatewayInfo,
    )
    from custom_components.comelit_local.video import VideoBridge  # noqa: PLC0415

    info = GatewayInfo("00000001", 9, {}, (Entrance("Entrance", "00000101"),))
    bridge = VideoBridge(
        "127.0.0.1", 64100, "private-token", info, {"rtsp_password": PASSWORD}
    )
    with (
        patch.object(
            VideoSession,
            "authenticate",
            AsyncMock(side_effect=ValueError("private-token")),
        ),
        patch.object(VideoSession, "connect", AsyncMock()) as connect,
    ):
        for _ in range(2):
            with pytest.raises(VideoError) as caught:
                await bridge._new_call("00000101")
            assert "private-token" not in str(caught.value) + caplog.text
        connect.assert_awaited_once()
    assert bridge.last_error == "ValueError"
