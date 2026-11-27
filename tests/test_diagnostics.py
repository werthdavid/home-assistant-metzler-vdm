"""Tests for diagnostics."""

from datetime import timedelta
import json

from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.metzler_vdm.const import EVENT_VALID_UNLOCK
from custom_components.metzler_vdm.tracker import AcsRecord

from custom_components.metzler_vdm.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacted(hass, config_entry, mock_client, freezer):
    """Secrets, allowlist content and polled unlocks never appear in the dump."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "1001", 1)
    ]
    freezer.tick(timedelta(seconds=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(bus) == 1  # the record was really polled and signalled
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    dump = json.dumps(result)
    for secret in (
        "192.0.2.10",
        "test-user",
        "test-password",
        "01" * 32,
        "1001",
        "Alias A",
    ):
        assert secret not in dump
    assert '"allowlist_size": 1' in dump
    assert result["allow_all"] is False
    assert result["call_status"] == "idle"
    assert result["call_status_supported"] is True
    assert result["tracker_primed"] is True
    assert result["last_update_success"] is True
    assert "clock_drift" in result
