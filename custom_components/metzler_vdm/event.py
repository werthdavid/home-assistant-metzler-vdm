"""Event entities, one per configured door."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DOORS
from .coordinator import MetzlerConfigEntry, MetzlerCoordinator
from .entity import MetzlerEntity

EVENT_VALID_UNLOCK = "valid_unlock"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetzlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one event entity per door."""
    async_add_entities(
        ValidUnlockEvent(entry.runtime_data, entry, door)
        for door in entry.options[CONF_DOORS]
    )


class ValidUnlockEvent(MetzlerEntity, EventEntity):
    """Fires when an allowlisted ID unlocks the door."""

    _attr_event_types = [EVENT_VALID_UNLOCK]
    _attr_translation_key = EVENT_VALID_UNLOCK

    def __init__(
        self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry, door: int
    ) -> None:
        """Initialize the entity for one door."""
        super().__init__(coordinator, entry)
        self._door = door
        self._attr_unique_id = f"{entry.entry_id}_door_{door}_valid_unlock"
        self._attr_translation_placeholders = {"door": str(door)}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire events for new unlocks, only after a fully successful poll."""
        if self.coordinator.last_update_success:
            for unlock in self.coordinator.data or []:
                if unlock.door_no != self._door:
                    continue
                self._trigger_event(
                    EVENT_VALID_UNLOCK,
                    {
                        "employee_no": unlock.employee_no,
                        "alias": unlock.alias,
                        "door": unlock.door_no,
                        "time": unlock.time,
                    },
                )
        super()._handle_coordinator_update()
