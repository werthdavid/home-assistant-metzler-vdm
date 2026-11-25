"""Tests for setup, entities and the unlock event flow."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.metzler_vdm.api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerConnectionError,
    MetzlerNotSupportedError,
)
from custom_components.metzler_vdm.const import DOMAIN
from custom_components.metzler_vdm.tracker import AcsRecord

R = AcsRecord("2026-11-25 08:57:25", "1001", 1)
R_NEW = AcsRecord("2026-11-25 08:59:00", "1001", 1)


async def setup(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def poll(hass, freezer, count=1):
    for _ in range(count):
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def entity_id(hass, entry, domain, suffix):
    ent_id = er.async_get(hass).async_get_entity_id(
        domain, DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert ent_id is not None
    return ent_id


def event_id(hass, entry, door=1):
    return entity_id(hass, entry, "event", f"door_{door}_valid_unlock")


def conn_id(hass, entry):
    return entity_id(hass, entry, "binary_sensor", "connectivity")


def track_events(hass, ent_id) -> list[Event]:
    """Collect state changes of the entity that carry a fired unlock."""
    changes: list[Event] = []

    @callback
    def _filter(data) -> bool:
        new_state = data["new_state"]
        return (
            data["entity_id"] == ent_id
            and new_state is not None
            and "employee_no" in new_state.attributes
        )

    hass.bus.async_listen("state_changed", changes.append, event_filter=_filter)
    return changes


def assert_no_event(hass, ent_id) -> None:
    """The event entity never fired (state unknown, or unavailable on errors)."""
    state = hass.states.get(ent_id)
    assert state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
    assert "employee_no" not in state.attributes


async def test_setup_creates_entities(hass, config_entry, mock_client):
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON


async def test_restart_does_not_refire(hass, config_entry, mock_client, freezer):
    mock_client.async_search_valid_card_events.return_value = [R]
    await setup(hass, config_entry)
    await poll(hass, freezer, 3)
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN


async def test_allowed_unlock_fires_exactly_once(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    ent_id = event_id(hass, config_entry)
    changes = track_events(hass, ent_id)
    mock_client.async_search_valid_card_events.return_value = [R]
    await poll(hass, freezer, 3)

    assert len(changes) == 1
    attrs = hass.states.get(ent_id).attributes
    assert attrs["event_type"] == "valid_unlock"
    assert attrs["employee_no"] == "1001"
    assert attrs["alias"] == "Alias A"
    assert attrs["door"] == 1
    assert attrs["time"] == "2026-11-25 08:57:25"
    assert "PLACEHOLDER" not in str(attrs)


async def test_unlisted_id_fires_nothing(hass, config_entry, mock_client, freezer):
    await setup(hass, config_entry)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "9999", 1)
    ]
    await poll(hass, freezer, 3)
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN


async def test_unconfigured_door_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "1001", 2)
    ]
    await poll(hass, freezer, 3)
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN


async def test_error_sets_connectivity_off_and_recovers(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    ent_id = event_id(hass, config_entry)
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError(
        "down"
    )
    await poll(hass, freezer, 15)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, ent_id)

    changes = track_events(hass, ent_id)
    mock_client.async_search_valid_card_events.side_effect = None
    mock_client.async_search_valid_card_events.return_value = [R_NEW]
    await poll(hass, freezer, 3)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
    assert len(changes) == 1


async def test_unexpected_exception_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    mock_client.async_search_valid_card_events.side_effect = RuntimeError("boom")
    await poll(hass, freezer, 2)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))


async def test_certificate_mismatch(hass, config_entry, mock_client, freezer):
    await setup(hass, config_entry)
    mock_client.async_search_valid_card_events.side_effect = MetzlerCertificateError(
        "mismatch"
    )
    await poll(hass, freezer)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, "certificate_mismatch")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False

    mock_client.async_search_valid_card_events.side_effect = None
    await poll(hass, freezer)
    assert registry.async_get_issue(DOMAIN, "certificate_mismatch") is None


async def test_auth_error_sets_connectivity_off(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    mock_client.async_search_valid_card_events.side_effect = MetzlerAuthError("bad")
    with patch.object(ConfigEntry, "async_start_reauth") as start_reauth:
        await poll(hass, freezer)
    start_reauth.assert_called_once()
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))


async def test_setup_with_station_offline_still_loads(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError(
        "down"
    )
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF

    # First successful poll only primes: no event even though R is present.
    mock_client.async_search_valid_card_events.side_effect = None
    mock_client.async_search_valid_card_events.return_value = [R]
    await poll(hass, freezer, 3)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN


async def test_clock_drift_issue(hass, config_entry, mock_client, freezer):
    from homeassistant.util import dt as dt_util

    registry = ir.async_get(hass)
    mock_client.async_get_device_time.side_effect = lambda tz: (
        dt_util.utcnow() + timedelta(seconds=30)
    )
    await setup(hass, config_entry)
    issue = registry.async_get_issue(DOMAIN, "clock_drift")
    assert issue is not None
    assert issue.translation_placeholders == {"seconds": "30"}
    assert config_entry.runtime_data.clock_drift == pytest.approx(30)

    # Re-checked only after the interval; a small drift clears the issue.
    mock_client.async_get_device_time.side_effect = lambda tz: (
        dt_util.utcnow() + timedelta(seconds=2)
    )
    freezer.tick(timedelta(seconds=3600))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert registry.async_get_issue(DOMAIN, "clock_drift") is None


async def test_clock_check_not_supported_disables_check(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_get_device_time.side_effect = MetzlerNotSupportedError("404")
    await setup(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "clock_drift") is None
    assert mock_client.async_get_device_time.await_count == 1

    freezer.tick(timedelta(seconds=3600))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    await poll(hass, freezer, 2)
    assert mock_client.async_get_device_time.await_count == 1


async def test_clock_query_error_keeps_connectivity_on(
    hass, config_entry, mock_client
):
    mock_client.async_get_device_time.side_effect = MetzlerConnectionError("down")
    await setup(hass, config_entry)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
