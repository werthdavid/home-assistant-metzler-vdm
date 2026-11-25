"""Config flow stub; replaced by the real flow in a later task."""

from __future__ import annotations

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class MetzlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Placeholder config flow so config entries can load."""

    VERSION = 1
