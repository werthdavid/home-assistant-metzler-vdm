"""Connectivity binary sensor."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MetzlerConfigEntry, MetzlerCoordinator
from .entity import MetzlerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetzlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the connectivity sensor."""
    async_add_entities([MetzlerConnectivity(entry.runtime_data, entry)])


class MetzlerConnectivity(MetzlerEntity, BinarySensorEntity):
    """On while the last poll succeeded."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connectivity"

    @property
    def available(self) -> bool:
        """Always available, so a failure shows as off, not unavailable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether the last poll succeeded."""
        return self.coordinator.last_update_success
