"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations in all tests."""
    yield


@pytest.fixture
def fake_session():
    """Return a fake aiohttp session recording calls."""
    from .fixtures import FakeSession

    return FakeSession()


@pytest.fixture
def config_entry(hass):
    """Return a loaded-ready config entry (not yet set up)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.metzler_vdm.const import (
        CONF_ALLOWLIST,
        CONF_DOORS,
        CONF_FINGERPRINT,
        CONF_TIMEZONE,
        DOMAIN,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Metzler VDM",
        unique_id="192.0.2.10",
        data={
            "host": "192.0.2.10",
            "username": "test-user",
            "password": "test-password",
            CONF_FINGERPRINT: "01" * 32,
        },
        options={
            CONF_ALLOWLIST: {"1001": "Alias A"},
            CONF_TIMEZONE: "Europe/Berlin",
            CONF_DOORS: [1],
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_client(freezer):
    """Patch MetzlerClient where __init__ looks it up."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.util import dt as dt_util

    with patch(
        "custom_components.metzler_vdm.MetzlerClient", autospec=True
    ) as client_cls:
        client = client_cls.return_value
        client.async_search_valid_card_events = AsyncMock(return_value=[])
        client.async_get_device_time = AsyncMock(
            side_effect=lambda tz: dt_util.utcnow()
        )
        yield client
