"""Tests for the remote unlock event (door opened from the indoor station)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, callback
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.metzler_vdm.api import MetzlerConnectionError
from custom_components.metzler_vdm.const import EVENT_VALID_UNLOCK
from custom_components.metzler_vdm.tracker import AcsRecord

from .test_init import conn_id, entity_id, event_id, poll, setup

REMOTE = AcsRecord("2026-11-25 08:57:24", "", 1)
CARD = AcsRecord("2026-11-25 08:57:25", "1001", 1)


def remote_id(hass, entry, door=1):
    return entity_id(hass, entry, "event", f"door_{door}_remote_unlock")


def track(hass, ent_id) -> list[Event]:
    changes: list[Event] = []

    @callback
    def _flt(data) -> bool:
        return data["entity_id"] == ent_id

    hass.bus.async_listen("state_changed", changes.append, event_filter=_flt)
    return changes


async def test_remote_unlock_fires_once_and_never_as_valid_unlock(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    assert hass.states.get(remote_id(hass, config_entry)).state == STATE_UNKNOWN
    changes = track(hass, remote_id(hass, config_entry))
    card_changes = track(hass, event_id(hass, config_entry))
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_remote_unlock_events.return_value = [REMOTE]
    await poll(hass, freezer, 4)
    assert len(changes) == 1
    attrs = changes[0].data["new_state"].attributes
    assert attrs["event_type"] == "remote_unlock"
    assert attrs["door"] == 1
    assert attrs["time"] == "2026-11-25 08:57:24"
    assert "employee_no" not in attrs and "alias" not in attrs
    # The alarm-relevant signals stay silent.
    assert card_changes == []
    assert bus == []


async def test_remote_unlock_first_poll_only_primes(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_search_remote_unlock_events.return_value = [REMOTE]
    await setup(hass, config_entry)
    changes = track(hass, remote_id(hass, config_entry))
    await poll(hass, freezer, 3)
    assert changes == []


async def test_remote_unlock_query_uses_the_poll_window(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    await poll(hass, freezer, 1)
    card_args = mock_client.async_search_valid_card_events.await_args.args
    remote_args = mock_client.async_search_remote_unlock_events.await_args.args
    assert remote_args[1] - remote_args[0] == card_args[1] - card_args[0]
    assert abs((remote_args[0] - card_args[0]).total_seconds()) < 1


async def test_remote_unlock_error_does_not_affect_unlocks(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    changes = track(hass, remote_id(hass, config_entry))
    bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    for error in (MetzlerConnectionError("down"), RuntimeError("boom")):
        mock_client.async_search_remote_unlock_events.side_effect = error
        mock_client.async_search_remote_unlock_events.reset_mock()
        mock_client.async_search_valid_card_events.return_value = [CARD]
        await poll(hass, freezer, 5)
        assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
        assert len(bus) == 1
        assert mock_client.async_search_remote_unlock_events.await_count == 1
        freezer.tick(timedelta(seconds=60))
        mock_client.async_search_remote_unlock_events.side_effect = None
        mock_client.async_search_remote_unlock_events.return_value = []
        await poll(hass, freezer, 1)
    assert changes == []


async def test_remote_unlock_survives_a_failed_card_search(
    hass, config_entry, mock_client, freezer
):
    """A record seen while the main search fails fires once it succeeds."""
    await setup(hass, config_entry)
    changes = track(hass, remote_id(hass, config_entry))
    mock_client.async_search_remote_unlock_events.return_value = [REMOTE]
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError("x")
    await poll(hass, freezer, 2)
    assert changes == []
    mock_client.async_search_valid_card_events.side_effect = None
    await poll(hass, freezer, 2)
    assert len(changes) == 1


async def test_remote_unlock_at_unconfigured_door_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    changes = track(hass, remote_id(hass, config_entry))
    mock_client.async_search_remote_unlock_events.return_value = [
        AcsRecord("2026-11-25 08:57:24", "", 2)
    ]
    await poll(hass, freezer, 2)
    assert changes == []
