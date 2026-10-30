# Comelit Local

A local Home Assistant custom integration for Comelit ViP gateways, initially
focused on the Comelit 1456B Multi-User Gateway.

## Current functionality

- Find a gateway on the local network, or enter its IP address and ViP TCP port.
- Connect using an existing dedicated user token.
- Redeem a manually generated activation code for a separate Home Assistant user.
- Validate authentication and read the gateway configuration before completing setup.
- Register the gateway as a device, with clear connection and authentication errors.
- Reconfigure the connection or replace a rejected token.
- Download diagnostics containing address-book counts without credentials or addresses.

This version adds the gateway device only. It does not yet create actuator buttons,
listen for rings, or provide video/audio. Video support on the 1456B remains
unverified. There is no persistent connection or live connection-status sensor yet;
configuration is read during setup/reload and the connection is then closed.

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
`libturbojpeg0` is an optional native dependency for faster camera snapshots; the
dev container includes it. No video feature depends on it in this version.

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
No implementation code from those repositories is bundled here.
