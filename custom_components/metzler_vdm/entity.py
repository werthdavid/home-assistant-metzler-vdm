"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MetzlerConfigEntry, MetzlerCoordinator


class MetzlerEntity(CoordinatorEntity[MetzlerCoordinator]):
    """Base entity attached to the door station device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry) -> None:
        """Initialize with the shared device info."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="Metzler",
            model="VDM10",
            name="Metzler VDM",
        )
