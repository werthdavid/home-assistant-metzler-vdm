"""Tests for setup, entities and the unlock event flow."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.metzler_vdm.api import (
    MetzlerAuthError,
    MetzlerCertificateError,
    MetzlerConnectionError,
    MetzlerNotSupportedError,
)
from custom_components.metzler_vdm.const import DOMAIN, EVENT_VALID_UNLOCK
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
    assert state.state == STATE_UNKNOWN
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
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "9999", 1)
    ]
    await poll(hass, freezer, 3)
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN
    assert bus == []

async def test_unconfigured_door_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "1001", 2)
    ]
    await poll(hass, freezer, 3)
    assert hass.states.get(event_id(hass, config_entry)).state == STATE_UNKNOWN
    assert bus == []

async def test_error_sets_connectivity_off_and_recovers(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    ent_id = event_id(hass, config_entry)
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError(
        "down"
    )
    await poll(hass, freezer, 15)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, ent_id)

    assert bus == []
    changes = track_events(hass, ent_id)
    mock_client.async_search_valid_card_events.side_effect = None
    mock_client.async_search_valid_card_events.return_value = [R_NEW]
    await poll(hass, freezer, 3)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
    assert len(changes) == 1
    assert len(bus) == 1

async def test_unexpected_exception_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.side_effect = RuntimeError("boom")
    await poll(hass, freezer, 2)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))
    assert bus == []

async def test_certificate_mismatch(hass, config_entry, mock_client, freezer):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.side_effect = MetzlerCertificateError(
        "mismatch"
    )
    await poll(hass, freezer)
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))
    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, f"certificate_mismatch_{config_entry.entry_id}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False

    mock_client.async_search_valid_card_events.side_effect = None
    await poll(hass, freezer)
    assert registry.async_get_issue(DOMAIN, f"certificate_mismatch_{config_entry.entry_id}") is None
    assert bus == []

async def test_auth_error_sets_connectivity_off(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.side_effect = MetzlerAuthError("bad")
    with patch.object(ConfigEntry, "async_start_reauth") as start_reauth:
        await poll(hass, freezer)
    start_reauth.assert_called_once()
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_OFF
    assert_no_event(hass, event_id(hass, config_entry))
    assert bus == []

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
    issue = registry.async_get_issue(DOMAIN, f"clock_drift_{config_entry.entry_id}")
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
    assert registry.async_get_issue(DOMAIN, f"clock_drift_{config_entry.entry_id}") is None


async def test_clock_check_not_supported_disables_check(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_get_device_time.side_effect = MetzlerNotSupportedError("404")
    await setup(hass, config_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"clock_drift_{config_entry.entry_id}") is None
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


async def test_outage_causes_no_event_state_change(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    ent_id = event_id(hass, config_entry)
    changes: list[Event] = []

    @callback
    def _flt(data) -> bool:
        return data["entity_id"] == ent_id

    hass.bus.async_listen("state_changed", changes.append, event_filter=_flt)
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError(
        "down"
    )
    await poll(hass, freezer, 5)
    mock_client.async_search_valid_card_events.side_effect = None
    await poll(hass, freezer, 3)
    assert changes == []
    assert hass.states.get(ent_id).state == STATE_UNKNOWN
    assert bus == []

async def test_recovery_without_new_unlock_causes_no_state_change(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    ent_id = event_id(hass, config_entry)
    mock_client.async_search_valid_card_events.return_value = [R]
    await poll(hass, freezer, 1)
    fired = hass.states.get(ent_id).state
    assert fired != STATE_UNKNOWN

    changes: list[Event] = []

    @callback
    def _flt(data) -> bool:
        return data["entity_id"] == ent_id

    hass.bus.async_listen("state_changed", changes.append, event_filter=_flt)
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError(
        "down"
    )
    await poll(hass, freezer, 5)
    mock_client.async_search_valid_card_events.side_effect = None
    await poll(hass, freezer, 3)
    assert changes == []
    assert hass.states.get(ent_id).state == fired


async def test_bus_event_fired_once_per_unlock(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [R]
    await poll(hass, freezer, 3)
    assert len(bus) == 1
    assert bus[0].data == {
        "entry_id": config_entry.entry_id,
        "entity_id": event_id(hass, config_entry),
        "employee_no": "1001",
        "alias": "Alias A",
        "door": 1,
        "time": "2026-11-25 08:57:25",
    }


async def test_reload_fires_no_bus_event(hass, config_entry, mock_client, freezer):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [R]
    await poll(hass, freezer, 2)
    assert len(bus) == 1
    before = hass.states.get(event_id(hass, config_entry)).state

    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    await poll(hass, freezer, 3)
    assert len(bus) == 1
    # HA restores the last event state on re-add (README warns about this).
    assert hass.states.get(event_id(hass, config_entry)).state == before


async def test_clock_query_unexpected_error_does_not_lose_unlock(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_get_device_time.side_effect = RuntimeError("boom")
    mock_client.async_search_valid_card_events.return_value = [R]
    freezer.tick(timedelta(seconds=3600))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.async_get_device_time.await_count == 2
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
    assert len(bus) == 1


async def test_unload_closes_session(hass, config_entry, mock_client):
    from custom_components.metzler_vdm import MetzlerClient

    await setup(hass, config_entry)
    session = MetzlerClient.call_args.args[0]
    assert not session.closed
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert session.closed


async def test_clock_check_runs_before_search(hass, config_entry, mock_client):
    from homeassistant.util import dt as dt_util

    order: list[str] = []

    async def device_time(tz):
        order.append("time")
        return dt_util.utcnow()

    async def search(start, end):
        order.append("search")
        return []

    mock_client.async_get_device_time.side_effect = device_time
    mock_client.async_search_valid_card_events.side_effect = search
    await setup(hass, config_entry)
    assert order == ["time", "search"]


async def test_clock_drift_computation_error_does_not_lose_unlock(
    hass, config_entry, mock_client, freezer
):
    """A naive device time makes the drift computation raise; poll still succeeds."""
    from datetime import datetime

    await setup(hass, config_entry)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_get_device_time.side_effect = lambda tz: datetime(
        2026, 11, 25, 8, 0, 0
    )
    mock_client.async_search_valid_card_events.return_value = [R]
    freezer.tick(timedelta(seconds=3600))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.async_get_device_time.await_count == 2
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
    assert len(bus) == 1


async def test_failing_clock_query_retried_after_60_s_not_every_poll(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_get_device_time.side_effect = MetzlerConnectionError("down")
    await setup(hass, config_entry)
    assert mock_client.async_get_device_time.await_count == 1
    await poll(hass, freezer, 29)  # 58 s
    assert mock_client.async_get_device_time.await_count == 1
    await poll(hass, freezer, 1)  # 60 s
    assert mock_client.async_get_device_time.await_count == 2
    await poll(hass, freezer, 29)
    assert mock_client.async_get_device_time.await_count == 2
    assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON


async def test_two_doors_fire_only_matching_entity(
    hass, config_entry, mock_client, freezer
):
    from custom_components.metzler_vdm.const import CONF_DOORS

    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_DOORS: [1, 2]}
    )
    await setup(hass, config_entry)
    door1 = event_id(hass, config_entry, 1)
    door2 = event_id(hass, config_entry, 2)
    changes1 = track_events(hass, door1)
    changes2 = track_events(hass, door2)
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [
        AcsRecord("2026-11-25 08:57:25", "1001", 2)
    ]
    await poll(hass, freezer, 3)
    assert changes1 == []
    assert len(changes2) == 1
    assert hass.states.get(door2).attributes["door"] == 2
    assert len(bus) == 1
    assert bus[0].data["door"] == 2
    assert bus[0].data["entity_id"] == door2
