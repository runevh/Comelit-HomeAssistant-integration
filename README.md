# Comelit Local

A Home Assistant custom integration for the Comelit 1456B Multi-User Gateway.

**Status: initial HACS development scaffold.** Gateway communication, entities,
doorbell events, and video are not implemented. Adding the integration currently
shows a development-status message and does not collect credentials or connect
to a gateway. Video support on the 1456B remains unverified.

## Planned direction

- Three gate/garage actuator buttons using actuator-specific commands.
- Doorbell ring events that can trigger Home Assistant automations.
- Investigate entrance-panel video for Home Assistant and Frigate.
- Useful redacted diagnostics and clear connection/authentication errors.
- Build toward a smart doorbell experience.

## HACS development installation

Add `https://github.com/runevh/Comelit-HomeAssistant-integration` as a custom
repository in HACS with category **Integration**, download it, and restart Home
Assistant. This is a scaffold for development, not a working doorbell integration.
It has not been submitted to the HACS default catalog.

The integration domain is `comelit_local`, separate from `comelit_intercom`.

## Development

Open this repository in the included VS Code dev container, or use Python 3.14
in a virtual environment and run:

```sh
scripts/setup
scripts/lint
scripts/develop
```

The template development baseline is Home Assistant 2026.6.4. Compatibility with
the target Home Assistant installation has not yet been checked. The development
server uses `config/`; runtime files and credentials there are ignored by Git.
GitHub Actions runs Ruff, hassfest, and HACS validation. Brand validation is
skipped until branding is added.

Use the existing dedicated Home Assistant user for future gateway tests. Keep
tokens, activation codes, backups, and raw configuration out of Git and issues.
Disable the old integration before testing a connection with the same token.
Physical gate commands and doorbell tests must be coordinated with someone on site.

## Attribution

Scaffold tooling adapted from [ludeeus/integration_blueprint](https://github.com/ludeeus/integration_blueprint)
at commit `cd399dac2a7e88d899dd62b3f5d8bbf91f1adadd`. Its MIT license and copyright
notice are retained in [LICENSE](LICENSE). No Comelit protocol implementation has
been copied into this repository yet.
