# Entrance video experiment

This branch tests the 1456B entrance feed independently of the actuator branch.
Nothing in this directory is loaded automatically by Home Assistant. The branch
now also includes an opt-in RTSP bridge in the integration; see the main README.
This probe remains useful independently of that bridge.

`video_probe.py` opens a bounded receive-only self-view session for the single
configured entrance, counts H.264 RTP packets, optionally decodes one JPEG, and
closes the session. It reads the existing development token privately from a
local Home Assistant config entry. It never creates a user or redeems a code.

The probe guards the outgoing transport: door and actuator commands, microphone
media, configuration changes, and non-video channels are rejected. Incoming calls
are ignored rather than answered. Only a separate development user may be used;
production slot 8 is explicitly excluded. Stop other clients sharing the same
token before running it. The setup-only local integration leaves no persistent
connection between requests.

## Run manually

Use Python 3.14 with the project's development dependencies. To decode images,
install `av==16.0.1` (the version pinned by this development HA release) and Pillow.

The signaling experiment imports the external MIT-licensed
[jfmlima/comelit-vip](https://github.com/jfmlima/comelit-vip) client at the reviewed
commit `4c1cba401030c705132174e60fb3886f667a442f`. Clone it outside this repository,
check out that revision, and pass the path with `--reference`. The probe checks
the revision before importing; it does not install that integration into HA.
Upstream source and its MIT notice remain in that external checkout.

From this branch's repository root, using the Python executable in the local
Home Assistant development environment:

```sh
python -m experiments.video_probe \
  --reference /path/to/comelit-vip \
  --ha-config /path/to/local/home-assistant/config \
  --expected-subaddress 9 \
  --seconds 8 \
  --snapshot .local/doorbell-first-frame.jpg
```

The subaddress must match the dedicated development user. Do not put a token on
the command line. Omit `--snapshot` to count packets without saving an image.
Snapshots are mode 0600 and `.local/` is ignored by Git. The summary contains
counts, protocol opcodes, phase, and exception class only; it never prints raw
responses, credentials, or addresses. The overall operation is bounded to 45
seconds plus bounded cleanup, with no automatic reconnects.

A packet count proves transport reception, not a working picture. A saved frame
and nonzero `decoded_frames` establish basic decoding. This experiment does not
establish continuous-stream reliability, maximum resolution, Frigate integration,
two-way audio, or coexistence during an actual doorbell call.

## Next steps

Once decoding is verified, build an on-demand local stream adapter and a Home
Assistant camera entity. Test Frigate access separately, including session time
limits and the effect of keeping a viewer connected. Keep gate-position analysis
from other cameras outside the Comelit protocol integration.

## Verified on the development gateway

On 2026-10-30, firmware 2.10.0 on the 1456B successfully completed registration,
self-view setup, and receive-only video negotiation through the development user.
The final eight-second probe received 329 RTP packets (payload type 99), decoded
118 frames at **320×240**, and saved one visually checked nighttime image. One
initial decode error occurred before a usable keyframe; the decoder recovered.
The session closed cleanly. No actuator/door-release opcode or microphone media
was sent. The private image is deliberately excluded from the repository.

The HA-hosted RTSP bridge was subsequently tested with a separate FFmpeg client
connecting through the laptop's LAN address using RTSP/TCP and stream authentication.
The final 90-second run decoded 1,440 frames with no FFmpeg errors, including one
upstream video-session renewal. After the client left, HA diagnostics reported zero
viewers and no active camera session. This is a short compatibility test, not a
24/7 stability or physical doorbell-call coexistence result.

## Native entrance RTSP

The call-based bridge prevented Comelit app live viewing in a user test. Stopping
the development HA instance restored app viewing.

Read-only SCAN/INFO discovery identified the separate entrance unit as hardware
`D529`, application `Port`, model code `UviV`, firmware `1.9.3`. The retail model
has not been confirmed. Its native RTSP server requires Digest authentication.
Read-only SNMP configuration inspection located the stream URI at
`1.3.6.1.4.1.35522.3.1.145.0`; the configured path was `/live/h264/ch0` on port 554.
Do not assume that path or credentials are identical on other installations.

With the development HA instance stopped, two simultaneous RTSP/TCP clients each
decoded 238 H.264 frames at 320×240 over 15 seconds. The entrance image was checked
visually. Both clients closed afterward. No ViP call, microphone transmission,
configuration write, or actuator command was used. Credentials, configuration
responses, and test images remain in ignored private local files.

A subsequent single-viewer test decoded 2,877 frames over 180 seconds and closed
normally. In a subsequent one-minute native RTSP test, the user opened live video
in the Comelit app and confirmed it worked while the RTSP viewer was active.

Concurrent Comelit app live viewing is therefore verified for this installation.
Actual incoming calls and long-running recording still require validation.
Native RTSP is not yet wired into this integration's
camera configuration; the existing camera entities still use the call-based bridge.
