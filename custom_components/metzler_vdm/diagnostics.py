"""Diagnostics for Metzler VDM (credentials, host, fingerprint, allowlist redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import CONF_ALLOWLIST, CONF_FINGERPRINT
from .coordinator import MetzlerConfigEntry

TO_REDACT = {
    CONF_HOST,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_FINGERPRINT,
    CONF_ALLOWLIST,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MetzlerConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(entry.data, TO_REDACT),
            "options": async_redact_data(entry.options, TO_REDACT),
        },
        "last_update_success": coordinator.last_update_success,
        "clock_drift": coordinator.clock_drift,
        "tracker_primed": coordinator.tracker_primed,
        "allowlist_size": len(entry.options[CONF_ALLOWLIST]),
    }
