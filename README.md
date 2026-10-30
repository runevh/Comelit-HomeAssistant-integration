# Comelit Local

A local Home Assistant custom integration for Comelit ViP gateways, initially
focused on the Comelit 1456B Multi-User Gateway.

## Current functionality

- Add a gateway using its IP address, ViP TCP port and an existing user token.
- Validate authentication and read the gateway configuration before completing setup.
- Register the gateway as a device, with clear connection and authentication errors.
- Reconfigure the connection or replace a rejected token.
- Download diagnostics containing address-book counts without credentials or addresses.

This version adds the gateway device only. It does not yet create actuator buttons,
listen for rings, or provide video/audio. Video support on the 1456B remains
unverified. There is no persistent connection or live connection-status sensor yet;
configuration is read during setup/reload and the connection is then closed.

Automatic token creation is deferred. Setup never creates users, generates activation
codes, or changes the gateway configuration.

## Installation and setup

1. Add `https://github.com/runevh/Comelit-HomeAssistant-integration` as a custom
   repository in HACS with category **Integration**.
2. Download the integration and restart Home Assistant.
3. Disable any existing integration or test instance using the same token.
4. Go to **Settings → Devices & services → Add integration → Comelit Local**.
5. Enter the gateway IP address (without `http://`), the ViP TCP port (normally
   `64100`), and its dedicated Home Assistant user token.

The web administration port, normally `8080`, is not the ViP port.
The domain is `comelit_local`, separate from `comelit_intercom`.
This repository has not been submitted to the HACS default catalog.

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

### If no dedicated token exists yet

The gateway's normal app activation flow can issue a token. This requires manual
provisioning outside this integration; the following is the protocol-level procedure
for the 1456B, not an automatic setup feature:

1. Log in to the gateway web interface with credentials that can manage users.
2. Select the intended apartment and verify that the selected user slot is genuinely
   unused: device type **None**, blank name/contact metadata, and no pending activation
   or reset controls. Do not overwrite, reset or repurpose an existing user.
3. Set that slot to **Apps**, name it **Home Assistant**, and enable it.
4. Generate an activation code for that slot.
5. Use a ViP protocol client to open the **UAUT** channel on TCP `64100` (wire channel
   type `7`) and redeem the code with this JSON request:

   ```json
   {
     "message": "user-activation",
     "activation-code": "<activation code>",
     "description": "Home Assistant",
     "message-type": "request",
     "message-id": 2
   }
   ```

6. A successful response has `response-code: 200` and a `user-token`. Save that token
   privately and enter it in the integration. Confirm that the intended slot is
   activated in the web interface.

This is a framed binary protocol carrying JSON, not an HTTP endpoint; sending the
JSON with curl will not work. The activation exchange is documented in
[mnestrud/comelit-man's provisioning implementation](https://github.com/mnestrud/comelit-man/blob/main/custom_components/comelit_man/provisioning.py).
Its complete provisioning routine relies on backup-based slot discovery, which can
fail on the 1456B. Do not run it blindly against an existing installation. If a user
is already activated but its token is unavailable, stop and recover the existing
token rather than resetting the user or repeatedly creating new users.

## Local development

Run these commands from the repository root:

```sh
scripts/setup
scripts/develop
```

Setup installs Python 3.14 and Home Assistant 2026.6.4 into `.ha-venv` using uv.
It leaves system Python unchanged. The local instance is available at
**http://127.0.0.1:8123**. Complete its onboarding to create a local test account.
The supplied configuration binds to loopback and does not enable automatic device
discovery. It is separate from the household Home Assistant installation.

The first startup downloads Home Assistant frontend dependencies. On Debian/Ubuntu,
`libturbojpeg0` is an optional native dependency for faster camera snapshots; the
dev container includes it. No video feature depends on it in this version.

`scripts/develop` links the repository's `custom_components` folder into `config/`.
Restart the local instance after Python code changes. Stop it with Ctrl+C.
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
No implementation code from those repositories is bundled here.
