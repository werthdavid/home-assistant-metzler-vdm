"""Tests for the coordinator's query window."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pytest_homeassistant_custom_component.common import async_fire_time_changed


async def test_query_window(hass, config_entry, mock_client, freezer):
    freezer.move_to("2026-11-25 08:00:00+00:00")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_search_valid_card_events.reset_mock()

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    start, end = mock_client.async_search_valid_card_events.call_args.args
    tz = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 11, 25, 8, 0, 2, tzinfo=ZoneInfo("UTC"))
    assert start == now - timedelta(seconds=60)
    assert end == now + timedelta(hours=24)
    assert start.tzinfo == tz
    assert end.tzinfo == tz
