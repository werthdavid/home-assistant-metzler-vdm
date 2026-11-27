"""Tests for the doorbell event (call status polling)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import STATE_ON, STATE_UNKNOWN
from homeassistant.core import Event, callback
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.metzler_vdm.api import (
    MetzlerConnectionError,
    MetzlerNotSupportedError,
)
from custom_components.metzler_vdm.const import EVENT_VALID_UNLOCK
from custom_components.metzler_vdm.tracker import AcsRecord

from .test_init import conn_id, entity_id, poll, setup

R = AcsRecord("2026-11-25 08:57:25", "1001", 1)


def bell_id(hass, entry):
    return entity_id(hass, entry, "event", "doorbell")


def track(hass, ent_id) -> list[Event]:
    changes: list[Event] = []

    @callback
    def _flt(data) -> bool:
        return data["entity_id"] == ent_id

    hass.bus.async_listen("state_changed", changes.append, event_filter=_flt)
    return changes


async def test_doorbell_entity_exists(hass, config_entry, mock_client):
    await setup(hass, config_entry)
    state = hass.states.get(bell_id(hass, config_entry))
    assert state.state == STATE_UNKNOWN
    assert state.attributes["device_class"] == "doorbell"
    assert state.attributes["event_types"] == ["ring"]


async def test_ring_fires_once_per_ring(hass, config_entry, mock_client, freezer):
    await setup(hass, config_entry)  # first poll: idle
    changes = track(hass, bell_id(hass, config_entry))
    unlock_bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    statuses = ["idle", "ring", "ring", "ring", "idle", "onCall", "idle", "ring", "idle"]
    mock_client.async_get_call_status.side_effect = statuses
    await poll(hass, freezer, len(statuses))
    assert len(changes) == 2
    assert changes[0].data["new_state"].attributes["event_type"] == "ring"
    assert unlock_bus == []


async def test_ring_already_active_at_start_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_get_call_status.return_value = "ring"
    await setup(hass, config_entry)
    changes = track(hass, bell_id(hass, config_entry))
    await poll(hass, freezer, 3)
    assert changes == []
    mock_client.async_get_call_status.side_effect = ["idle", "ring"]
    await poll(hass, freezer, 2)
    assert len(changes) == 1


async def test_call_status_error_does_not_affect_unlocks(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    changes = track(hass, bell_id(hass, config_entry))
    unlock_bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    for error in (MetzlerConnectionError("down"), RuntimeError("boom")):
        mock_client.async_get_call_status.side_effect = error
        mock_client.async_get_call_status.reset_mock()
        mock_client.async_search_valid_card_events.return_value = [R]
        await poll(hass, freezer, 5)
        assert hass.states.get(conn_id(hass, config_entry)).state == STATE_ON
        assert len(unlock_bus) == 1
        # Backed off: not retried on every poll.
        assert mock_client.async_get_call_status.await_count == 1
        freezer.tick(timedelta(seconds=60))
        mock_client.async_get_call_status.side_effect = None
        mock_client.async_get_call_status.return_value = "idle"
        await poll(hass, freezer, 1)
    assert changes == []


async def test_ring_seen_first_after_error_fires_nothing(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    changes = track(hass, bell_id(hass, config_entry))
    mock_client.async_get_call_status.side_effect = MetzlerConnectionError("down")
    await poll(hass, freezer, 1)
    freezer.tick(timedelta(seconds=60))
    mock_client.async_get_call_status.side_effect = ["ring", "ring", "idle", "ring"]
    await poll(hass, freezer, 3)
    assert changes == []  # the ring was already running when we looked again
    await poll(hass, freezer, 1)
    assert len(changes) == 1


async def test_call_status_not_supported_disables_polling(
    hass, config_entry, mock_client, freezer
):
    mock_client.async_get_call_status.side_effect = MetzlerNotSupportedError("404")
    await setup(hass, config_entry)
    unlock_bus = async_capture_events(hass, EVENT_VALID_UNLOCK)
    mock_client.async_search_valid_card_events.return_value = [R]
    freezer.tick(timedelta(seconds=120))
    await poll(hass, freezer, 3)
    assert mock_client.async_get_call_status.await_count == 1
    assert len(unlock_bus) == 1
    assert config_entry.runtime_data.call_status_supported is False


async def test_ring_not_fired_when_search_fails(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    changes = track(hass, bell_id(hass, config_entry))
    mock_client.async_get_call_status.return_value = "ring"
    mock_client.async_search_valid_card_events.side_effect = MetzlerConnectionError("x")
    await poll(hass, freezer, 2)
    assert changes == []
