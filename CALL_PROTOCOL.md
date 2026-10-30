# Experimental incoming calls

The 1456B ring listener uses the existing MIT-licensed `jfmlima/comelit-vip`
transport snapshot. Registration, incoming INVITEs, acknowledgements and connection
IDs are parsed structurally; CTP body length is not treated as an opcode.

The inbound media sequence in `call_transport.py` is adapted from the protocol
research in [mnestrud/comelit-man](https://github.com/mnestrud/comelit-man), revision
`2af72a6dfd2178a468602da9d7c5f2ac2d6b3211`, specifically `video_call.py` and
`protocol.py`. That project is Apache-2.0 licensed; the full license is retained
in [licenses/comelit-man-APACHE-2.0.txt](licenses/comelit-man-APACHE-2.0.txt).
The adapted inbound negotiation portion retains those Apache-2.0 terms.
Changes here replace the reference connection, timestamp and queue management
with the existing MIT transport's parsed CTP connections; they add explicit browser
ownership, a wire command guard, bounded microphone queues and teardown supervision.
The reference reports call testing on a 6701W; this is not proof of 1456B support.

## Behavior

- One dedicated token registers a persistent CTP listener. No media channels are
  opened when idle or merely ringing. Reconnect uses bounded exponential backoff;
  rejected authentication starts HA reauthentication.
- Only entrance-directory calls create doorbell events. Floor/internal calls are
  ignored. Repeated INVITEs are deduplicated for two minutes.
- Answer must name the current random call ID. One authenticated administrator's
  websocket subscription owns the call; other browsers cannot send audio or hang up.
- Answer opens RTPC/UDPM and negotiates inbound codecs, audio and video offers,
  then sends peer/accept and waits for the device audio channel. The negotiated
  video is discarded; the dashboard uses the independently verified native RTSP
  feed. Video may have HLS buffering and is not synchronized with call audio.
- The browser decodes G.711 A-law or mu-law audio. After explicit microphone
  permission it downsamples to 8 kHz PCMA, sending 160-byte frames over HA's
  authenticated websocket. The gateway receives RTP on the answered call's RTPC
  channel. No audio or silence is transmitted before microphone input.
- Microphone access requires a secure browser context (HTTPS or localhost), on
  desktop as well as mobile. The first version uses foreground browser controls,
  not a native iOS/Android incoming-call screen or background microphone service.
- Browser disconnect, an eight-second missing heartbeat, remote termination,
  integration unload, or shutdown releases an owned call. Calls are bounded by a
  three-minute inbound lifetime. The listener never sends relay/actuator commands.
- Idle native RTSP viewing has worked alongside the Comelit app. A real incoming
  call, answering elsewhere and two-way audio still require physical validation.
  Answering in HA intentionally claims the call from the other phones.

## Physical test checklist

1. Keep the call-based RTSP bridge disabled. Open the local Comelit dashboard.
2. Press the physical entrance button. Confirm HA notification and `ring` event,
   while the normal Comelit phones/indoor units also ring.
3. Answer first in the Comelit app. Confirm HA clears the pending call.
4. Ring again. Press **Answer** in HA, verify visitor audio, then **Enable
   microphone** and allow permission. Verify the visitor hears speech.
5. Hang up; verify microphone permission indicator stops and the Comelit app can
   view the entrance again. Repeat by closing the browser during an answered call.
6. Check missed-call expiry and a fresh ring after reconnect/reload.

Automated tests use synthetic rings and mocked media. A passive live registration
and dashboard loading do not validate steps 2–6. No gate operation is part of this
checklist. Diagnostics expose state/error codes and media counters, never tokens,
addresses, raw call packets, microphone recordings or stream credentials.
