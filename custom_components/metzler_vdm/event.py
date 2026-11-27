"""Event entities: valid unlock and remote unlock per door, and the doorbell."""

from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DOORS, EVENT_VALID_UNLOCK
from .coordinator import MetzlerConfigEntry, MetzlerCoordinator
from .entity import MetzlerEntity

# Entity event type; EVENT_VALID_UNLOCK is the bus event type.
EVENT_TYPE_VALID_UNLOCK = "valid_unlock"
EVENT_TYPE_RING = "ring"
EVENT_TYPE_REMOTE_UNLOCK = "remote_unlock"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetzlerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one unlock entity per door and the doorbell entity."""
    async_add_entities(
        [
            *(
                ValidUnlockEvent(entry.runtime_data, entry, door)
                for door in entry.options[CONF_DOORS]
            ),
            *(
                RemoteUnlockEvent(entry.runtime_data, entry, door)
                for door in entry.options[CONF_DOORS]
            ),
            DoorbellEvent(entry.runtime_data, entry),
        ]
    )


class ValidUnlockEvent(MetzlerEntity, EventEntity):
    """Fires when an allowlisted ID unlocks the door."""

    _attr_event_types = [EVENT_TYPE_VALID_UNLOCK]
    _attr_translation_key = EVENT_TYPE_VALID_UNLOCK

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
                self._trigger_event(EVENT_TYPE_VALID_UNLOCK, payload)
                # Restore-proof signal: entity state is restored on reload.
                self.hass.bus.async_fire(
                    EVENT_VALID_UNLOCK,
                    {
                        "entry_id": self.coordinator.config_entry.entry_id,
                        "entity_id": self.entity_id,
                        **payload,
                    },
                )
        super()._handle_coordinator_update()


class DoorbellEvent(MetzlerEntity, EventEntity):
    """Fires when the station starts ringing (call status changes to "ring")."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = [EVENT_TYPE_RING]
    _attr_translation_key = "doorbell"

    def __init__(
        self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry
    ) -> None:
        """Initialize the doorbell entity."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_doorbell"

    @property
    def available(self) -> bool:
        """Stay available, like the unlock entities."""
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire once per started ring, only after a successful poll."""
        if self.coordinator.last_update_success and self.coordinator.ring_started:
            self._trigger_event(EVENT_TYPE_RING)
        super()._handle_coordinator_update()


class RemoteUnlockEvent(MetzlerEntity, EventEntity):
    """Fires when the door is opened remotely, e.g. from the indoor station.

    Purely informational: it never fires the valid-unlock entity or bus event.
    """

    _attr_event_types = [EVENT_TYPE_REMOTE_UNLOCK]
    _attr_translation_key = EVENT_TYPE_REMOTE_UNLOCK

    def __init__(
        self, coordinator: MetzlerCoordinator, entry: MetzlerConfigEntry, door: int
    ) -> None:
        """Initialize the entity for one door."""
        super().__init__(coordinator, entry)
        self._door = door
        self._attr_unique_id = f"{entry.entry_id}_door_{door}_remote_unlock"
        self._attr_translation_placeholders = {"door": str(door)}

    @property
    def available(self) -> bool:
        """Stay available, like the other event entities."""
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire for new remote unlocks at this door after a successful poll."""
        if self.coordinator.last_update_success:
            for unlock in self.coordinator.remote_unlocks:
                if unlock.door_no == self._door:
                    self._trigger_event(
                        EVENT_TYPE_REMOTE_UNLOCK,
                        {"door": unlock.door_no, "time": unlock.time},
                    )
        super()._handle_coordinator_update()
