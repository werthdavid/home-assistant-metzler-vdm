"""Config, options, reauth and reconfigure flows.

Security notes: the certificate fingerprint is fetched without sending any
credentials and must be confirmed by the user before it is pinned. Every
request that carries credentials goes through the pinned client. Flow errors
are fixed keys; the password never appears in errors or placeholders.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from zoneinfo import available_timezones

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerClient,
    MetzlerConnectionError,
    MetzlerError,
    async_fetch_fingerprint,
    create_digest_session,
    format_fingerprint,
    parse_fingerprint,
)
from .const import (
    CONF_ALLOWLIST,
    CONF_DOORS,
    CONF_FINGERPRINT,
    CONF_TIMEZONE,
    DOMAIN,
    WINDOW_SECONDS,
)
from .tracker import AllowlistError, format_allowlist, parse_allowlist

CONF_CONFIRM = "confirm"


def _parse_host(value: str) -> str | None:
    """Return the cleaned host, or None if it is not a bare host name or IPv4."""
    host = value.strip()
    if not host or any(ch in host for ch in "/:") or any(ch.isspace() for ch in host):
        return None
    return host


def _parse_doors(text: str) -> list[int] | None:
    """Parse comma-separated door numbers (>= 1); None if invalid."""
    doors: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not (part.isascii() and part.isdigit()) or int(part) < 1:
            return None
        doors.add(int(part))
    return sorted(doors)


def _access_schema(timezone: str) -> vol.Schema:
    """Return the schema shared by the access step and the options flow.

    Allowlist and doors only get suggested values (set by the caller), never
    defaults: an omitted field must not fall back to a previously stored value.
    """
    return vol.Schema(
        {
            vol.Optional(CONF_ALLOWLIST): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Required(CONF_TIMEZONE, default=timezone): SelectSelector(
                SelectSelectorConfig(
                    options=sorted(available_timezones()),
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(CONF_DOORS): str,
        }
    )


def _parse_access(
    user_input: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Validate the access form; return (options, errors)."""
    errors: dict[str, str] = {}
    allowlist: dict[str, str] = {}
    try:
        allowlist = parse_allowlist(user_input.get(CONF_ALLOWLIST, ""))
    except AllowlistError:
        errors[CONF_ALLOWLIST] = "invalid_allowlist"
    doors = _parse_doors(user_input.get(CONF_DOORS, ""))
    if doors is None:
        errors[CONF_DOORS] = "invalid_doors"
    if errors:
        return None, errors
    return {
        CONF_ALLOWLIST: allowlist,
        CONF_TIMEZONE: user_input[CONF_TIMEZONE],
        CONF_DOORS: doors,
    }, errors


async def _async_probe(
    hass: HomeAssistant, host: str, username: str, password: str, fingerprint: bytes
) -> str | None:
    """Query recent events through the pinned client; return an error key."""
    session = create_digest_session(hass, username, password, auto_cleanup=False)
    try:
        client = MetzlerClient(session, host, fingerprint)
        now = dt_util.utcnow()
        await client.async_search_valid_card_events(
            now - timedelta(seconds=WINDOW_SECONDS), now
        )
    except MetzlerAuthError:
        return "invalid_auth"
    except MetzlerCertificateError:
        return "certificate_changed"
    except MetzlerConnectionError:
        return "cannot_connect"
    except MetzlerError:
        return "unexpected_response"
    finally:
        # close() of a Home Assistant session is only a warning; detach.
        session.detach()
    return None


class MetzlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Metzler VDM."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow state."""
        self._host = ""
        self._username = ""
        self._password = ""
        self._fingerprint = b""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> MetzlerOptionsFlow:
        """Return the options flow."""
        return MetzlerOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for host and credentials, then fetch the fingerprint."""
        return await self._async_credentials("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter host and credentials (new host or new certificate)."""
        return await self._async_credentials("reconfigure", user_input)

    async def _async_credentials(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Shared handler of the user and reconfigure steps."""
        errors: dict[str, str] = {}
        defaults: Mapping[str, Any] = user_input or {}
        if step_id == "reconfigure" and not defaults:
            defaults = {
                CONF_HOST: self._get_reconfigure_entry().data[CONF_HOST],
                CONF_USERNAME: self._get_reconfigure_entry().data[CONF_USERNAME],
            }
        if user_input is not None:
            host = _parse_host(user_input[CONF_HOST])
            if host is None:
                errors["base"] = "invalid_host"
            else:
                await self.async_set_unique_id(host.lower())
                if step_id == "user":
                    self._abort_if_unique_id_configured()
                elif self._host_used_by_other_entry(host.lower()):
                    return self.async_abort(reason="already_configured")
                try:
                    raw = await async_fetch_fingerprint(host)
                except (MetzlerError, OSError):
                    errors["base"] = "cannot_connect"
                else:
                    self._host = host
                    self._username = user_input[CONF_USERNAME]
                    self._password = user_input[CONF_PASSWORD]
                    self._fingerprint = raw
                    return await self.async_step_fingerprint()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                    vol.Required(
                        CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    def _host_used_by_other_entry(self, unique_id: str) -> bool:
        """Return whether another entry already uses this host."""
        own = self._get_reconfigure_entry().entry_id
        return any(
            entry.entry_id != own and entry.unique_id == unique_id
            for entry in self._async_current_entries(include_ignore=True)
        )

    async def async_step_fingerprint(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the fingerprint; after confirmation probe with the pinned client."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CONFIRM):
                errors["base"] = "not_confirmed"
            else:
                error = await _async_probe(
                    self.hass,
                    self._host,
                    self._username,
                    self._password,
                    self._fingerprint,
                )
                if error is None:
                    return await self._async_finish_credentials()
                errors["base"] = error
        return self.async_show_form(
            step_id="fingerprint",
            data_schema=vol.Schema({vol.Required(CONF_CONFIRM, default=False): bool}),
            description_placeholders={
                "fingerprint": format_fingerprint(self._fingerprint)
            },
            errors=errors,
        )

    async def _async_finish_credentials(self) -> ConfigFlowResult:
        """Store the confirmed credentials (reconfigure) or ask for access."""
        if self.source == "reconfigure":
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(),
                unique_id=self._host.lower(),
                data=self._entry_data(),
            )
        return await self.async_step_access()

    def _entry_data(self) -> dict[str, Any]:
        """Return the config entry data."""
        return {
            CONF_HOST: self._host,
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_FINGERPRINT: self._fingerprint.hex(),
        }

    async def async_step_access(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for allowlist, time zone and doors, then create the entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _parse_access(user_input)
            if options is not None:
                return self.async_create_entry(
                    title=self._host, data=self._entry_data(), options=options
                )
        defaults = user_input or {}
        return self.async_show_form(
            step_id="access",
            data_schema=self.add_suggested_values_to_schema(
                _access_schema(
                    defaults.get(CONF_TIMEZONE, self.hass.config.time_zone)
                ),
                {
                    CONF_ALLOWLIST: defaults.get(CONF_ALLOWLIST, ""),
                    CONF_DOORS: defaults.get(CONF_DOORS, "1"),
                },
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after a rejected password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password and verify it with the pinned client."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await _async_probe(
                self.hass,
                entry.data[CONF_HOST],
                entry.data[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                parse_fingerprint(entry.data[CONF_FINGERPRINT]),
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )


class MetzlerOptionsFlow(OptionsFlow):
    """Edit allowlist, time zone and doors."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the access form pre-filled with the current options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options, errors = _parse_access(user_input)
            if options is not None:
                return self.async_create_entry(data=options)
        current = user_input or {
            CONF_ALLOWLIST: format_allowlist(self.config_entry.options[CONF_ALLOWLIST]),
            CONF_TIMEZONE: self.config_entry.options[CONF_TIMEZONE],
            CONF_DOORS: ", ".join(str(d) for d in self.config_entry.options[CONF_DOORS]),
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _access_schema(current[CONF_TIMEZONE]),
                {
                    CONF_ALLOWLIST: current.get(CONF_ALLOWLIST, ""),
                    CONF_DOORS: current.get(CONF_DOORS, ""),
                },
            ),
            errors=errors,
        )
