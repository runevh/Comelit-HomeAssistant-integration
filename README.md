# Comelit Local

A local Home Assistant custom integration for Comelit ViP gateways, initially
focused on the Comelit 1456B Multi-User Gateway.

## Current functionality

- Find a gateway on the local network, or enter its IP address and ViP TCP port.
- Connect using an existing dedicated user token.
- Redeem a manually generated activation code for a separate Home Assistant user.
- Validate authentication and read the gateway configuration before completing setup.
- Register the gateway as a device, with clear connection and authentication errors.
- Host an optional authenticated RTSP video bridge for remote viewers such as Frigate.
- View named entrance cameras and snapshots directly in Home Assistant.
- Listen for entrance rings and expose a doorbell event entity for automations.
- Show HA notifications; optionally configure mobile push notifications.
- Experiment with answering calls and microphone talk-back from a dashboard.
- Reconfigure the connection or replace a rejected token.
- Download diagnostics containing address-book counts without credentials or addresses.

This feature branch adds experimental incoming calls alongside entrance video.
The 1456B successfully supplied H.264 entrance video at 320×240 in live tests.
Gate controls remain on their separate feature branch. Incoming ring events,
HA notifications and explicit Answer/Microphone/Hang up controls are implemented,
but 1456B call answering and two-way audio still need physical validation.
Continuous viewing and coexistence during a real doorbell call remain experimental.
The call-based bridge blocked live viewing in the Comelit app during testing;
app access recovered when the bridge stopped. Do not use this bridge as an
always-on Frigate source when app viewing must remain available.

Automatic user-slot provisioning is deferred. Setup never creates user slots,
generates activation codes, or resets existing users. The activation-code option
redeems a code for the slot prepared manually in the gateway web interface.

## Installation and setup

1. Add `https://github.com/runevh/Comelit-HomeAssistant-integration` as a custom
   repository in HACS with category **Integration**.
2. Download the integration and restart Home Assistant.
3. Disable any existing integration or test instance using the same token. A separate
   dedicated development user has its own token and avoids sharing that identity.
4. Go to **Settings → Devices & services → Add integration → Comelit Local**.
5. Wait for the local search, then select the detected gateway or **Enter an IP
   address manually**. Manual setup accepts an IP address (without `http://`) and
   the ViP TCP port (normally `64100`).
6. Choose **Use an existing token** or **Use a pairing / activation code**.
7. Enter the selected credential. For activation, enter a device name too.

Discovery runs only when opening setup, without credentials or protocol commands.
It checks TCP `64100` and a matching public Comelit login page on port `8080`,
then asks the user to select a candidate. It does not claim a specific gateway model.

The search is limited to enabled private IPv4 interfaces, their local `/24` (or
smaller) subnets, at most 512 addresses and ten seconds. Devices across VLANs,
elsewhere on a larger subnet, using IPv6 or custom ports may require manual entry.
A network timeout or no results does not prevent manual setup.

The web administration port, normally `8080`, is not the ViP port.
The domain is `comelit_local`, separate from `comelit_intercom`.
This repository has not been submitted to the HACS default catalog.

## Native entrance RTSP for Frigate

The tested gateway has no confirmed native RTSP endpoint, but the entrance unit
has a separate authenticated native RTSP server. Two simultaneous native RTSP
viewers decoded video successfully, and the user confirmed simultaneous Comelit
app live viewing during a separate one-minute native stream test. An empty gateway
camera address book does not rule out a native
entrance stream. See [native stream findings](experiments/README.md#native-entrance-rtsp).

Use the entrance's native stream for Frigate on this installation. Home Assistant
does not need to run or host a bridge for this video source.

1. Merge [examples/frigate.yaml](examples/frigate.yaml) into the configuration on
   your Frigate host, preserving existing cameras and other settings.
2. Replace `ENTRANCE_IP`, `RTSP_USER`, and `RTSP_PASSWORD` privately. Use the entrance
   unit's IP and RTSP credentials, and URL-encode special characters in credentials.
   The tested path is `/live/h264/ch0` on port 554; other models may differ.
3. Validate and reload the Frigate configuration, then open the `comelit` camera.
   The example leaves recording and object detection disabled while enabling video.

Frigate's go2rtc connects directly to the entrance and shares the stream with
Frigate's camera pipeline. `127.0.0.1:8554` in the example means the Frigate host's
own restream. Home Assistant can later consume that restream as well. See the
[Frigate restream documentation](https://docs.frigate.video/configuration/restream/).
Incoming-call coexistence and continuous recording still need testing.

## Doorbell rings and experimental talk-back

1. In **Comelit Local → Configure**, enable **experimental incoming calls**.
   Keep **RTSP video bridge** disabled: it uses a competing call connection.
2. Optionally enter the entrance's **native RTSP URL** to retain the camera in HA.
   This private URL maps to the first entrance in the gateway directory; use a
   separate camera integration for other entrances. Blank keeps the saved URL;
   **Remove saved native RTSP URL** clears it. Credentials stay out of entity
   attributes and diagnostics. Frigate can continue using its own native feed.
3. Under dashboard **Resources**, add `/comelit_local/comelit-call-card.js` as a
   **JavaScript module**. Refresh the browser, then add a manual card:

   ```yaml
   type: custom:comelit-call-card
   entity: event.comelit_gateway_doorbell
   ```

   Use your actual event entity ID. Place the entrance Picture Entity card beside
   it for video. The initial call controls require a Home Assistant administrator.
4. On a ring, HA shows a persistent notification by default. Press **Answer** to
   accept, then **Enable microphone** and grant permission to talk. **Hang up**
   stops audio. Closing the card/browser also releases its owned call.

**Desktop and mobile browsers are supported in principle.** Microphone access
requires HTTPS or localhost; plain `http://192.168...` on a phone will not work.
The companion app's microphone behavior still needs a device test; a foreground
browser over HTTPS is the fallback. This is not a native phone call screen and
cannot answer in the background. The local development instance is loopback-only,
so use the laptop at `http://127.0.0.1:8123` for the first test.

The event entity emits `ring`, `answered`, `missed_call` and `call_ended`. The event
bus also publishes `comelit_local_doorbell` with `entry_id`, `event_type`, `call_id`
and `entrance`. Filter on `event_type: ring` for ring automations. For mobile push,
customize [the notification automation](examples/doorbell-notification.yaml) with
your phone's notify action; no phone is automatically selected or configured.

Native video viewing remains separate from calling. Answering in HA claims the
call from other Comelit phones, as answering on a normal indoor unit would. Normal
phone ringing, answer-elsewhere behavior and audio on the 1456B remain unverified
until the [physical test checklist](CALL_PROTOCOL.md#physical-test-checklist) passes.
HLS video can lag the call audio. See [protocol details and attribution](CALL_PROTOCOL.md).

## Experimental Home Assistant-hosted RTSP fallback

The bridge below is the call-based fallback. This
integration converts the verified Comelit video transport into RTSP **inside the
Home Assistant process**. No separate bridge service or Frigate-on-HA installation
is required. Frigate can run on another machine.

1. Open **Settings → Devices & services → Comelit Local → Configure**.
2. Enable **RTSP video bridge**, select an unused port (default **8557**), and set a
   separate stream password of at least 12 characters. Username is **comelit**.
   A blank password field preserves the existing password when editing options.
3. Enable **Allow viewers from other devices on the network** for remote Frigate.
   Otherwise the listener binds only to loopback. Ensure the HA host/container
   makes that TCP port accessible to Frigate.
4. Use this URL with an RTSP-over-TCP client, replacing the placeholders:

   ```text
   rtsp://comelit:STREAM_PASSWORD@HOME_ASSISTANT_IP:8557/comelit
   ```

   URL-encode special characters in the password. Do not use the Comelit token,
   installer password, Home Assistant login, or a cloud/reverse-proxy URL here.
   The bridge uses RTSP Basic authentication on the local network; do not expose
   the port directly to the internet.

This fallback blocked Comelit app live viewing in testing. Use the native entrance
source above for Frigate on the tested installation.

A viewer starts the camera session; enabled-but-idle HA does not. Multiple viewers
share one upstream video session. When the last viewer leaves, the bridge releases
the session after a short grace period. Integration reload, disable, and HA shutdown
also close the listener and camera connection. Authentication is required even for
local viewers, and invalid credentials cannot start video.

The gateway may end a video session while viewers are still connected. The relay
can establish another session while preserving the RTSP connection, but brief gaps
may occur. It waits for current H.264 parameter sets and a keyframe before forwarding
a new session. This is not yet a guarantee of uninterrupted 24/7 recording.

**Frigate keeps its camera input connected even when object detection is disabled.**
The example starts with recording and object detection off, but still requests
video continuously. For on-demand-only testing, configure only the `go2rtc.streams`
section and open it with a viewer. Physical doorbell-call coexistence still needs a
coordinated daytime test. This fallback never sends microphone audio, answers
incoming calls, or operates an actuator.

Home Assistant's built-in [stream integration](https://www.home-assistant.io/integrations/stream/)
proxies camera sources to HLS for browser playback; it does not replace this Comelit
RTSP bridge. The integration now creates a native **camera entity for each entrance**
when the bridge is enabled. Home Assistant reads the authenticated loopback feed,
so local viewing also works with network access disabled. Frigate and HA share the
same upstream Comelit video session.

### View the camera in Home Assistant

After enabling the RTSP bridge, open **Settings → Devices & services → Comelit Local**,
select the gateway, and open the named camera (for example **Buitenpost**). You can
also add a Picture Entity card to a dashboard:

```yaml
type: picture-entity
entity: camera.comelit_gateway_buitenpost
name: Buitenpost
camera_view: live
show_state: false
```

Use the entity ID shown in your installation if it differs. No stream password
belongs in the dashboard card. Native HA playback uses HLS, with normal buffering;
use the separate call card above for experimental talk-back.

Camera snapshots and live cards request video on demand. Opening a dashboard that
shows the camera may therefore start the entrance session. Home Assistant may keep
its stream warm briefly after the last browser viewer leaves. Removing/disabling
the integration stops its HA stream workers before closing the RTSP bridge.

Diagnostics report listener state, viewer count, active-session state, sessions
started and the last error **type**. They exclude stream credentials, gateway tokens,
addresses, camera names, raw protocol replies and images. Upstream logs that might
contain private payloads are disabled; integration errors are fixed/sanitized.

## Getting a token

The token is a 32-character hexadecimal **user token**, not the installer password
or the temporary app activation code. Use a dedicated Home Assistant app user;
reusing a phone's token can interfere with that phone's calls and notifications.

### Reuse a token from an existing Home Assistant integration

For an installation already configured with `comelit_intercom`:

1. Disable the existing integration in **Settings → Devices & services** before
   using its token elsewhere. Keep the entry so its configuration is preserved.
2. Using private file access on the Home Assistant host, open
   `/config/.storage/core.config_entries` read-only. For a Core installation, use
   `.storage/core.config_entries` inside its configuration directory instead.
3. Find the object in `data.entries` whose `domain` is `comelit_intercom` and whose
   `data.host` matches the gateway.
4. Copy its `data.token` into the Comelit Local setup form. Do not edit the storage
   file, paste its contents into issues, or include it in screenshots or logs.
5. Keep the old integration disabled while Comelit Local or a development instance
   uses that token. The regular Comelit app and indoor units use their own users
   and should remain configured.

If the dedicated token was previously saved to a private file, use that copy instead.
The activated user should be reused; there is no need to register a second one.

### Create a separate user with an activation code

This is also the recommended approach for a development instance: use a distinct
user named **Home Assistant Dev**, leaving the production Home Assistant user intact.

1. Open the gateway web interface, normally `http://<gateway IP>:8080`, and log in
   with credentials that can manage users.
2. Select the intended apartment and verify that a user slot is genuinely unused:
   device type **None** (`-`), blank name/contact metadata, and no pending activation
   or reset controls. Do not reuse slots merely because they say "Not Activated".
3. Set the unused slot to **Apps**, name it **Home Assistant Dev** (or another clear
   name), and make sure it is enabled.
4. Click **Generate activation code** for that slot.
5. In Home Assistant, add **Comelit Local**, select the detected gateway (or enter
   its address manually), then choose **Use a pairing / activation code**. Enter
   the code shown for the new slot and the same device name. Enter the code directly
   into Home Assistant, not into an issue or log.
6. The integration redeems the code over ViP and saves the returned token. The slot
   should become **Activated**. Home Assistant then reads the gateway configuration
   and registers the gateway device.

The returned token is saved before configuration retrieval so a temporary connection
failure after activation does not discard it. Home Assistant can retry loading the
entry without redeeming the code again. The activation code itself is not saved.

If the gateway rejects the code, check the code and selected gateway before retrying.
If activation times out after submission, the result may be uncertain: inspect the
new slot first. Do not repeatedly submit codes or reset existing users. A freshly
created development slot can be deliberately reset by its owner if necessary, but
this integration never performs that reset.

Existing activated users should use **Use an existing token**, following the recovery
steps above. User provisioning and backup extraction are not needed by this flow.

## Local development

Run these commands from the repository root:

```sh
scripts/setup
scripts/develop
```

Setup installs Python 3.14 and Home Assistant 2026.6.4 into `.ha-venv` using uv.
It leaves system Python unchanged. The local instance is available at
**http://127.0.0.1:8123**. Complete its onboarding to create a local test account.
The supplied configuration binds to loopback and does not enable background device
discovery. Comelit Local performs its bounded gateway search only when adding the
integration. It is separate from the household Home Assistant installation.

The first startup downloads Home Assistant frontend dependencies. On Debian/Ubuntu,
Home Assistant's snapshot pipeline needs the native TurboJPEG library (`libturbojpeg`
on Ubuntu 24.04; `libturbojpeg0` on some other releases). The dev container includes
it. The development launcher also detects a project-local copy at
`.local/native/usr/lib/<architecture>-linux-gnu/libturbojpeg.so.0`, avoiding a system
installation when necessary. HLS playback can work without it, but HA snapshots
need the library.

`scripts/develop` links the repository's `custom_components` folder into `config/`.
Restart the local instance after Python code changes. After translation or setup-menu
changes, refresh the browser with Ctrl+Shift+R and start a new setup dialog to clear
stale frontend translations. Stop the instance with Ctrl+C.
Configuration, tokens, logs, virtual environments and local account data are ignored
by Git. Do not run the production and development integrations with the same token
at the same time.

### Tests and formatting

```sh
.venv/bin/uv pip install --python .ha-venv/bin/python -r requirements_test.txt
.ha-venv/bin/python -m pytest
scripts/lint
```

Tests use synthetic gateway replies and Home Assistant's flow manager. They do not
connect to the physical gateway or operate gates. Live gateway compatibility still
requires a separate coordinated test. GitHub Actions runs tests, Ruff, hassfest and
HACS validation; brand validation is skipped until branding is added.

## Development roadmap

The [issues](https://github.com/runevh/Comelit-HomeAssistant-integration/issues)
track actuator buttons, ring automations, video/Frigate, two-way audio, notifications
and further diagnostics. Existing Comelit phones and indoor units must continue
working. Physical actuator and doorbell tests require someone on site.

## Attribution

Scaffold tooling adapted from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint)
at commit `cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd`; its MIT license and copyright
notice are retained in [LICENSE](LICENSE).

The setup client is independently implemented from the observed ViP framing and
configuration exchange. Protocol references include
[mnestrud/comelit-man](https://github.com/mnestrud/comelit-man) (Apache-2.0) and
[madchicken/comelit-client](https://github.com/madchicken/comelit-client).
The video transport is adapted from
[jfmlima/comelit-vip](https://github.com/jfmlima/comelit-vip) at commit
`4c1cba401030c705132174e60fb3886f667a442f` (MIT, Copyright 2026 Jorge Lima).
Its license, provenance and local modifications are retained in
[the vendor directory](custom_components/comelit_local/vendor/README.md).
The video wrapper restricts this transport to receive-only entrance video.
The separate call wrapper supports explicit inbound answering and microphone audio;
its Apache-2.0 protocol provenance is documented in [CALL_PROTOCOL.md](CALL_PROTOCOL.md).
