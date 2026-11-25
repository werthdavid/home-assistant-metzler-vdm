"""Tests for the coordinator's query window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.metzler_vdm.const import EVENT_VALID_UNLOCK
from custom_components.metzler_vdm.tracker import AcsRecord

BERLIN = ZoneInfo("Europe/Berlin")


async def test_query_window(hass, config_entry, mock_client, freezer):
    freezer.move_to("2026-11-25 08:00:00+00:00")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_search_valid_card_events.reset_mock()

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    start, end = mock_client.async_search_valid_card_events.call_args.args
    now = datetime(2026, 11, 25, 8, 0, 2, tzinfo=UTC)
    assert start == now - timedelta(seconds=60)
    assert end == now + timedelta(hours=24)
    assert start.tzinfo == BERLIN
    assert end.tzinfo == BERLIN


@pytest.mark.parametrize(
    ("now_utc", "start_iso"),
    [
        # Fall back 2026-10-25: 02:30 local exists twice.
        ("2026-10-25 00:30:00+00:00", "2026-10-25T02:29:00+02:00"),  # first pass
        ("2026-10-25 01:30:00+00:00", "2026-10-25T02:29:00+01:00"),  # second pass
        ("2026-10-25 01:00:30+00:00", "2026-10-25T02:59:30+02:00"),  # across switch
        # Spring forward 2026-03-29: 02:00 CET jumps to 03:00 CEST.
        ("2026-03-29 01:00:30+00:00", "2026-03-29T01:59:30+01:00"),
        ("2026-03-29 01:02:00+00:00", "2026-03-29T03:01:00+02:00"),
    ],
)
async def test_query_window_across_dst(
    hass, config_entry, mock_client, freezer, now_utc, start_iso
):
    """The window is exactly 60 s of absolute time and carries the right offset."""
    now = datetime.fromisoformat(now_utc)
    freezer.move_to(now - timedelta(seconds=2))
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_search_valid_card_events.reset_mock()

    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    start, end = mock_client.async_search_valid_card_events.call_args.args
    # Compare in UTC: same-tzinfo comparison would ignore the offset.
    assert start.astimezone(UTC) == now - timedelta(seconds=60)
    assert end.astimezone(UTC) == now + timedelta(hours=24)
    assert start.isoformat() == start_iso
    assert start.utcoffset() == datetime.fromisoformat(start_iso).utcoffset()
    assert end.utcoffset() == (now + timedelta(hours=24)).astimezone(BERLIN).utcoffset()


async def test_fall_back_repeated_hour_fires_once(
    hass, config_entry, mock_client, freezer
):
    """A station that ignores offsets returns a record in both passes: one event."""
    record_time = "2026-10-25 02:29:30"
    naive_record = datetime.fromisoformat(record_time)
    happened = datetime(2026, 10, 25, 0, 29, 30, tzinfo=UTC)  # first pass
    record = AcsRecord(record_time, "1001", 1)

    async def search(start: datetime, end: datetime) -> list[AcsRecord]:
        exists = datetime.now(UTC) >= happened
        in_window = (
            start.replace(tzinfo=None) <= naive_record <= end.replace(tzinfo=None)
        )
        return [record] if exists and in_window else []

    mock_client.async_search_valid_card_events.side_effect = search
    freezer.move_to("2026-10-25 00:29:00+00:00")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)

    async def run_until(stop: datetime) -> None:
        while datetime.now(UTC) < stop:
            freezer.tick(timedelta(seconds=10))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()

    await run_until(datetime(2026, 10, 25, 0, 32, tzinfo=UTC))
    assert len(bus) == 1  # fired in the first pass

    returned = []
    original = search

    async def recording_search(start, end):
        result = await original(start, end)
        returned.append(bool(result))
        return result

    mock_client.async_search_valid_card_events.side_effect = recording_search
    await run_until(datetime(2026, 10, 25, 1, 32, tzinfo=UTC))
    # The station did report the record again in the second pass ...
    assert any(returned)
    # ... but it is not signalled a second time.
    assert len(bus) == 1
