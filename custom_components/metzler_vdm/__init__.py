"""The Metzler VDM integration."""

from __future__ import annotations

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import MetzlerClient, parse_fingerprint
from .const import CONF_FINGERPRINT, DOMAIN
from .coordinator import (
    ISSUE_CERTIFICATE,
    ISSUE_CLOCK_DRIFT,
    MetzlerConfigEntry,
    MetzlerCoordinator,
)

PLATFORMS = [Platform.EVENT, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: MetzlerConfigEntry) -> bool:
    """Set up Metzler VDM from a config entry."""
    session = async_create_clientsession(
        hass,
        middlewares=(
            aiohttp.DigestAuthMiddleware(
                entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            ),
        ),
    )
    client = MetzlerClient(
        session, entry.data[CONF_HOST], parse_fingerprint(entry.data[CONF_FINGERPRINT])
    )
    coordinator = MetzlerCoordinator(hass, entry, client)
    # Not async_config_entry_first_refresh: an offline station must not block setup.
    await coordinator.async_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MetzlerConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CERTIFICATE)
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CLOCK_DRIFT)
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: MetzlerConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
