"""Opt-in settings for the Home Assistant-hosted RTSP bridge."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector


class VideoOptionsFlow(config_entries.OptionsFlow):
    """Keep stream credentials independent from the Comelit user token."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Enable or disable the relay and select its network exposure."""
        current = dict(self.config_entry.options)
        errors = {}
        if user_input is not None:
            user_input = dict(user_input)
            password = user_input.pop("rtsp_password", "") or current.get(
                "rtsp_password", ""
            )
            native = user_input.pop("native_rtsp_url", "").strip() or current.get(
                "native_rtsp_url", ""
            )
            if user_input.pop("clear_native_rtsp", False):
                native = ""
            try:
                url = urlsplit(native)
                valid = not native or (
                    url.scheme == "rtsp" and bool(url.hostname) and url.port != 0
                )
            except ValueError:
                valid = False
            if not valid:
                errors["native_rtsp_url"] = "invalid_rtsp"
            elif user_input.get("calls_enabled") and user_input["rtsp_enabled"]:
                errors["base"] = "conflicting_connections"
            elif user_input["rtsp_enabled"] and len(password) < 12:  # noqa: PLR2004
                errors["rtsp_password"] = "password_required"  # noqa: S105 - translation key
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        **user_input,
                        "rtsp_password": password,
                        **({"native_rtsp_url": native} if native else {}),
                    },
                )
            current.update(user_input)
        return self.async_show_form(
            step_id="init",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "calls_enabled", default=current.get("calls_enabled", False)
                    ): bool,
                    vol.Optional(
                        "call_notifications",
                        default=current.get("call_notifications", True),
                    ): bool,
                    vol.Required(
                        "rtsp_enabled", default=current.get("rtsp_enabled", False)
                    ): bool,
                    vol.Required(
                        "rtsp_lan", default=current.get("rtsp_lan", False)
                    ): bool,
                    vol.Required(
                        "rtsp_port", default=current.get("rtsp_port", 8557)
                    ): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
                    vol.Optional("native_rtsp_url"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                    vol.Optional("clear_native_rtsp", default=False): bool,
                    vol.Optional("rtsp_password"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    ),
                }
            ),
        )
