"""Configuration flow for the Comelit Local scaffold."""

from __future__ import annotations

from homeassistant import config_entries

from .const import DOMAIN


class ComelitLocalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Explain development status without collecting credentials."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict | None = None,  # noqa: ARG002 - Home Assistant flow signature
    ) -> config_entries.ConfigFlowResult:
        """Prevent setup until the gateway client is implemented."""
        return self.async_abort(reason="not_implemented")
