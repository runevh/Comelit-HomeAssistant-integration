"""Set up a Comelit gateway with an existing dedicated user token."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers import selector

from .client import (
    CannotConnect,
    GatewayClient,
    InvalidAuth,
    InvalidResponse,
    validate_token,
)
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(CONF_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


class ComelitLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validate credentials before creating or updating a config entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Set up a gateway using an IP address and token."""
        return await self._async_configure("user", user_input)

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],  # noqa: ARG002 - Home Assistant signature
    ) -> config_entries.ConfigFlowResult:
        """Replace a rejected token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate replacement credentials."""
        return await self._async_configure("reauth_confirm", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow changes to the gateway IP, port or token."""
        return await self._async_configure("reconfigure", user_input)

    async def _async_configure(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> config_entries.ConfigFlowResult:
        entry = None
        if step_id == "reauth_confirm":
            entry = self._get_reauth_entry()
        elif step_id == "reconfigure":
            entry = self._get_reconfigure_entry()
        defaults = dict(entry.data) if entry else {}
        errors = {}
        if user_input is not None:
            defaults = user_input
            try:
                host = str(ip_address(user_input[CONF_HOST].strip()))
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                if any(
                    existing.entry_id != (entry.entry_id if entry else None)
                    and existing.data[CONF_HOST] == host
                    and existing.data[CONF_PORT] == user_input[CONF_PORT]
                    for existing in self._async_current_entries()
                ):
                    return self.async_abort(reason="already_configured")
                try:
                    token = validate_token(user_input[CONF_TOKEN])
                    await GatewayClient(
                        host, user_input[CONF_PORT]
                    ).async_get_configuration(token)
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except InvalidResponse:
                    errors["base"] = "invalid_response"
                else:
                    data = {
                        CONF_HOST: host,
                        CONF_PORT: user_input[CONF_PORT],
                        CONF_TOKEN: token,
                    }
                    if entry:
                        return self.async_update_reload_and_abort(
                            entry, data_updates=data
                        )
                    self._async_abort_entries_match(
                        {CONF_HOST: host, CONF_PORT: user_input[CONF_PORT]}
                    )
                    # This protocol does not yet provide a verified hardware identifier.
                    # Do not turn an IP address or a secret token into a unique ID.
                    return self.async_create_entry(title=f"Comelit ({host})", data=data)
        return self.async_show_form(
            step_id=step_id, data_schema=_schema(defaults), errors=errors
        )
