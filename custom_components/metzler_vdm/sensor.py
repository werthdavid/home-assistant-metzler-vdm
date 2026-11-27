"""Call status sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MetzlerConfigEntry, MetzlerCoordinator
from .entity import MetzlerEntity

STATE_OTHER = "other"
# Status reported by the station (lower case) -> sensor state.
_STATES = {"idle": "idle", "ring": "ring", "oncall": "on_call"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetzlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the call status sensor."""
    async_add_entities([MetzlerCallStatus(entry.runtime_data, entry)])


class MetzlerCallStatus(MetzlerEntity, SensorEntity):
    """Intercom call status as polled for the doorbell."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [*_STATES.values(), STATE_OTHER]
    _attr_translation_key = "call_status"

    def __init__(self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_call_status"

    @property
    def native_value(self) -> str | None:
        """Return the mapped status; None while it is unknown."""
        status = self.coordinator.call_status
        if status is None:
            return None
        return _STATES.get(status.lower(), STATE_OTHER)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the status exactly as the station reported it."""
        status = self.coordinator.call_status
        return None if status is None else {"raw_status": status}
