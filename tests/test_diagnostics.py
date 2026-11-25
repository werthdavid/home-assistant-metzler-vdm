"""Tests for diagnostics."""

import json

from custom_components.metzler_vdm.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacted(hass, config_entry, mock_client):
    """Secrets and allowlist content never appear in the dump."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
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
    assert result["tracker_primed"] is True
    assert result["last_update_success"] is True
    assert "clock_drift" in result
