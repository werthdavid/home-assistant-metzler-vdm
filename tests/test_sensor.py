"""Tests for the call status sensor."""

from __future__ import annotations

from homeassistant.const import STATE_UNKNOWN
import pytest

from custom_components.metzler_vdm.api import (
    MetzlerConnectionError,
    MetzlerNotSupportedError,
)

from .test_init import entity_id, poll, setup


def status_id(hass, entry):
    return entity_id(hass, entry, "sensor", "call_status")


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        ("idle", "idle"),
        ("ring", "ring"),
        ("onCall", "on_call"),
        ("ONCALL", "on_call"),
        ("somethingNew", "other"),
    ],
)
async def test_call_status_sensor_maps_status(
    hass, config_entry, mock_client, freezer, raw, state
):
    await setup(hass, config_entry)
    sensor = hass.states.get(status_id(hass, config_entry))
    assert sensor.state == "idle"
    assert sensor.attributes["device_class"] == "enum"
    assert sensor.attributes["options"] == ["idle", "ring", "on_call", "other"]

    mock_client.async_get_call_status.return_value = raw
    await poll(hass, freezer, 1)
    sensor = hass.states.get(status_id(hass, config_entry))
    assert sensor.state == state
    assert sensor.attributes["raw_status"] == raw


async def test_call_status_sensor_unknown_on_query_error(
    hass, config_entry, mock_client, freezer
):
    await setup(hass, config_entry)
    mock_client.async_get_call_status.side_effect = MetzlerConnectionError("down")
    await poll(hass, freezer, 1)
    sensor = hass.states.get(status_id(hass, config_entry))
    assert sensor.state == STATE_UNKNOWN
    assert "raw_status" not in sensor.attributes


async def test_call_status_sensor_unknown_when_not_supported(
    hass, config_entry, mock_client
):
    mock_client.async_get_call_status.side_effect = MetzlerNotSupportedError("404")
    await setup(hass, config_entry)
    assert hass.states.get(status_id(hass, config_entry)).state == STATE_UNKNOWN
