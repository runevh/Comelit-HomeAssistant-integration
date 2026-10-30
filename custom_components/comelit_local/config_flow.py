"""Set up a Comelit gateway with a token or manually generated activation code."""

from __future__ import annotations

from ipaddress import ip_address
from typing import Any, Self

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers import selector

from .client import (
    ActivationRejected,
    ActivationUncertain,
    CannotConnect,
    GatewayClient,
    InvalidAuth,
    InvalidResponse,
    validate_token,
)
from .const import CONF_TOKEN, DEFAULT_PORT, DOMAIN
from .discovery import async_discover_gateways
from .options_flow import VideoOptionsFlow

_MAX_CODE_LENGTH = 64


def _connection_schema(defaults: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
        vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
    }


def _schema(
    defaults: dict[str, Any], *, activation: bool = False, connection: bool = True
) -> vol.Schema:
    fields = _connection_schema(defaults) if connection else {}
    fields[vol.Required("activation_code" if activation else CONF_TOKEN)] = (
        selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )
    )
    if activation:
        fields[
            vol.Required(
                "description", default=defaults.get("description", "Home Assistant")
            )
        ] = vol.All(str, vol.Length(min=1, max=64))
    return vol.Schema(fields)


class ComelitLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validate credentials before creating or updating a config entry."""

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,  # noqa: ARG004 - HA signature
    ) -> VideoOptionsFlow:
        """Offer the optional RTSP bridge after gateway setup."""
        return VideoOptionsFlow()

    VERSION = 1
    _connection: tuple[str, int] | None = None

    def __init__(self) -> None:
        """Keep discovered and selected gateways scoped to this flow."""
        self._gateways: list[str] = []
        self._gateway: dict[str, Any] = {}

    def is_matching(self, other_flow: Self) -> bool:
        """Prevent competing setup/activation flows for the same gateway."""
        return (
            self._connection is not None and self._connection == other_flow._connection
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - HA signature
    ) -> config_entries.ConfigFlowResult:
        """Search the local network before requesting any credentials."""
        self._gateways = await async_discover_gateways(self.hass)
        return await self.async_step_gateway()

    async def async_step_gateway(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user select a detected gateway or enter an address manually."""
        if user_input is not None:
            selected = user_input["gateway"]
            if selected == "manual":
                return await self.async_step_manual()
            self._select_gateway(selected, DEFAULT_PORT)
            return await self.async_step_authentication()
        options = [
            {"value": host, "label": f"Comelit gateway — {host}"}
            for host in self._gateways
        ]
        options.append({"value": "manual", "label": "Enter an IP address manually"})
        return self.async_show_form(
            step_id="gateway",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "gateway",
                        default=self._gateways[0] if self._gateways else "manual",
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options)
                    )
                }
            ),
            description_placeholders={"count": str(len(self._gateways))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Accept an explicit IP address and optional custom ViP port."""
        errors = {}
        if user_input is not None:
            try:
                host = str(ip_address(user_input[CONF_HOST].strip()))
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                self._select_gateway(host, user_input[CONF_PORT])
                return await self.async_step_authentication()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(_connection_schema(user_input or {})),
            errors=errors,
        )

    def _select_gateway(self, host: str, port: int) -> None:
        self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
        self._check_in_progress(host, port)
        self._gateway = {CONF_HOST: host, CONF_PORT: port}

    async def async_step_authentication(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002 - HA signature
    ) -> config_entries.ConfigFlowResult:
        """Choose token or pairing code only after a gateway has been selected."""
        return self.async_show_menu(
            step_id="authentication",
            menu_options=["token", "activation"],
            description_placeholders={"host": self._gateway[CONF_HOST]},
        )

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Set up using an existing dedicated token."""
        if user_input is not None:
            user_input = {**user_input, **self._gateway}
        return await self._async_configure("token", user_input)

    async def async_step_activation(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Exchange an activation code and immediately save the returned token."""
        if user_input is not None:
            user_input = {**user_input, **self._gateway}
        errors = {}
        if user_input is not None:
            try:
                host = str(ip_address(user_input[CONF_HOST].strip()))
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                port = user_input[CONF_PORT]
                self._async_abort_entries_match({CONF_HOST: host, CONF_PORT: port})
                self._check_in_progress(host, port)
                code = user_input["activation_code"].strip()
                description = user_input["description"].strip()
                if (
                    not code
                    or len(code) > _MAX_CODE_LENGTH
                    or not code.isascii()
                    or not code.isalnum()
                ):
                    errors["activation_code"] = "invalid_code"
                elif not description:
                    errors["description"] = "invalid_name"
                else:
                    try:
                        token = await GatewayClient(host, port).async_activate(
                            code, description
                        )
                    except CannotConnect:
                        errors["base"] = "cannot_connect"
                    except ActivationRejected:
                        errors["base"] = "activation_rejected"
                    except ActivationUncertain:
                        return self.async_abort(reason="activation_uncertain")
                    else:
                        # Save before configuration reads: the code is now consumed.
                        # Entry setup retries if the gateway is temporarily offline.
                        return self.async_create_entry(
                            title=f"Comelit ({host})",
                            data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                        )
        return self.async_show_form(
            step_id="activation",
            data_schema=_schema(user_input or {}, activation=True, connection=False),
            errors=errors,
        )

    def _check_in_progress(self, host: str, port: int) -> None:
        self._connection = (host, port)
        if self.hass.config_entries.flow.async_has_matching_flow(self):
            reason = "already_in_progress"
            raise AbortFlow(reason)

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
                self._check_in_progress(host, user_input[CONF_PORT])
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
            step_id=step_id,
            data_schema=_schema(defaults, connection=step_id != "token"),
            errors=errors,
        )
