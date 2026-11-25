"""Event entities, one per configured door."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DOORS, EVENT_VALID_UNLOCK as BUS_EVENT_VALID_UNLOCK
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

    @property
    def available(self) -> bool:
        """Stay available: unavailable -> last event would look like a new unlock."""
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire events for new unlocks, only after a fully successful poll."""
        if self.coordinator.last_update_success:
            for unlock in self.coordinator.data or []:
                if unlock.door_no != self._door:
                    continue
                payload = {
                    "employee_no": unlock.employee_no,
                    "alias": unlock.alias,
                    "door": unlock.door_no,
                    "time": unlock.time,
                }
                self._trigger_event(EVENT_VALID_UNLOCK, payload)
                # Restore-proof signal: entity state is restored on reload.
                self.hass.bus.async_fire(
                    BUS_EVENT_VALID_UNLOCK,
                    {
                        "entry_id": self.coordinator.config_entry.entry_id,
                        "entity_id": self.entity_id,
                        **payload,
                    },
                )
        super()._handle_coordinator_update()
