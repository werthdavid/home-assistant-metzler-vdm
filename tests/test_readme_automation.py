"""The README's origin guard accepts only the integration's own bus event."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import re

from homeassistant.core import Context, EventOrigin
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.metzler_vdm.const import EVENT_VALID_UNLOCK
from custom_components.metzler_vdm.tracker import AcsRecord

README = Path(__file__).parent.parent / "README.md"


def readme_guard() -> str:
    """Return the guard template exactly as written in the README."""
    matches = re.findall(
        r'value_template: "(\{\{[^"]*trigger\.event\.context\.user_id[^"]*\}\})"',
        README.read_text(encoding="utf-8"),
    )
    assert len(matches) == 1
    return matches[0]


def render(hass, event) -> object:
    return Template(readme_guard(), hass).async_render({"trigger": {"event": event}})


async def captured_real_event(hass, config_entry, mock_client, freezer):
    """Poll one allowlisted unlock through the event entity; return the bus event."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "1001", 1)
    ]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(bus) == 1
    return bus[0]


async def test_guard_accepts_integration_event(
    hass, config_entry, mock_client, freezer
):
    event = await captured_real_event(hass, config_entry, mock_client, freezer)
    assert render(hass, event) is True


async def test_guard_rejects_mobile_app_style_event(
    hass, config_entry, mock_client, freezer, hass_admin_user
):
    real = await captured_real_event(hass, config_entry, mock_client, freezer)
    forged = async_capture_events(hass, EVENT_VALID_UNLOCK)
    # Exactly how mobile_app's fire_event webhook fires.
    hass.bus.async_fire(
        EVENT_VALID_UNLOCK,
        dict(real.data),
        EventOrigin.remote,
        context=Context(user_id=hass_admin_user.id),
    )
    await hass.async_block_till_done()
    assert render(hass, forged[-1]) is False


async def test_guard_rejects_local_event_with_user_context(
    hass, config_entry, mock_client, freezer, hass_admin_user
):
    real = await captured_real_event(hass, config_entry, mock_client, freezer)
    forged = async_capture_events(hass, EVENT_VALID_UNLOCK)
    hass.bus.async_fire(
        EVENT_VALID_UNLOCK,
        dict(real.data),
        EventOrigin.local,
        context=Context(user_id=hass_admin_user.id),
    )
    await hass.async_block_till_done()
    assert render(hass, forged[-1]) is False


async def test_guard_rejects_remote_event_without_user(
    hass, config_entry, mock_client, freezer
):
    real = await captured_real_event(hass, config_entry, mock_client, freezer)
    forged = async_capture_events(hass, EVENT_VALID_UNLOCK)
    hass.bus.async_fire(EVENT_VALID_UNLOCK, dict(real.data), EventOrigin.remote)
    await hass.async_block_till_done()
    assert render(hass, forged[-1]) is False
