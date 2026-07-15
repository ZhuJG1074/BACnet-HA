"""
BACnet base entity classes for the BACnet-HA integration.

All BACnet entity platforms derive from one of these base classes to avoid
duplicating the common HA device/coordinate boilerplate across 5+ platforms.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .coordinator import BACnetCoordinator

_LOGGER = logging.getLogger(__name__)


class BACnetEntity(Entity):
    """Base entity for the BACnet-HA integration.

    All BACnet entities extend this class.  It provides:
      - Access to the shared BACnetCoordinator and its data cache
      - Automatic state updates when COV notifications or polling complete
      - BACnet-derived unique_id construction
    """

    _attr_should_poll = False
    coordinator: BACnetCoordinator
    _attr_object_type: int
    _attr_instance: int

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj_type: int,
        instance: int,
        entry_id: str,
    ) -> None:
        """Initialize a BACnet entity."""
        self.coordinator = coordinator
        self._attr_unique_id = self._unique_id(obj_type, instance, entry_id)
        self._obj_key = obj_key
        self._attr_object_type = obj_type
        self._attr_instance = instance
        self.entry_id = entry_id

        # Register for updates from the coordinator (COV / polling)
        coordinator.async_add_listener(self._on_coordinator_update)

    @staticmethod
    def _unique_id(obj_type: int, instance: int, entry_id: str) -> str:
        """Build a globally unique entity_id."""
        return f"{entry_id}-{obj_type}-{instance}"

    @callback
    def _on_coordinator_update(self) -> None:
        """Handle incoming updates from the coordinator."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Entity is available only when coordinator data is fresh."""
        # The entity is always 'available' as long as the coordinator has
        # produced at least one successful update.  The coordinator's native
        # backoff + UpdateFailed handling makes repeated outages visible.
        return self.coordinator.data is not None

    def _value(self) -> Any:
        """Return the current presentValue from coordinator data, or None."""
        return self.coordinator.get_object_value(self._obj_key, "presentValue")

    def _status_flags(self) -> list[bool] | None:
        """Return the current statusFlags from coordinator data, or None."""
        return self.coordinator.get_object_value(self._obj_key, "statusFlags")

    @property
    def _flag_out_of_service(self) -> bool:
        """Read the out-of-service flag (index 3) from statusFlags."""
        flags = self._status_flags()
        if flags is None:
            return False
        try:
            return flags[3]
        except (IndexError, TypeError):
            return False

    @property
    def _flag_fault(self) -> bool:
        """Read the fault flag (index 0) from statusFlags."""
        flags = self._status_flags()
        if flags is None:
            return False
        try:
            return flags[0]
        except (IndexError, TypeError):
            return False


class ReadBACnetEntity(BACnetEntity):
    """Base for read-only sensor/binary_sensor/number entities.

    Reads the current `presentValue` directly from the coordinator data cache.
    """

    def _update_from_coordinator(self) -> None:
        """Called from async_added_to_hass and _on_coordinator_update."""
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Subclasses override to update local attributes from coordinator."""
        pass

    async def async_added_to_hass(self) -> None:
        """Restore the entity's state on startup from coordinator cache."""
        self._update_from_coordinator()
        await super().async_added_to_hass()


class CommandableBACnetEntity(BACnetEntity):
    """Base for commandable number/switch/binary_sensor/select entities.

    Subclasses override `commandable_property`, `coerce_command()` and
    `coerce_state()`.
    """

    commandable_property: str = "presentValue"
    write_priority: int = 16

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj_type: int,
        instance: int,
        entry_id: str,
    ) -> None:
        """Initialize a commandable entity."""
        super().__init__(coordinator, obj_key, obj_type, instance, entry_id)
        if coordinator.entry is not None:
            self.write_priority = coordinator.entry.data.get(
                "write_priority", coordinator.write_priority
            )

    def _commandable_value(self) -> Any:
        """Return the current value, None if not available."""
        return self._value()

    def coerce_command(self, value: Any) -> Any:
        """Convert a user command to a BACnet-writable value."""
        return value

    def coerce_state(self, raw: Any) -> Any:
        """Convert a BACnet value to a HA-friendly state."""
        return raw

    async def _write_value(self, value: Any) -> bool:
        """Write a value to the device and update local state optimistically."""
        success = await self.coordinator.client.write_property(
            device_address=self.coordinator.device_address,
            object_type=self._attr_object_type,
            instance=self._attr_instance,
            property_name=self.commandable_property,
            value=self.coerce_command(value),
            priority=self.write_priority,
        )
        return success

    async def async_added_to_hass(self) -> None:
        """Restore the entity's state on startup from coordinator cache."""
        self._update_from_coordinator()
        await super().async_added_to_hass()

    def _update_from_coordinator(self) -> None:
        """Called from async_added_to_hass and _on_coordinator_update."""
        self._handle_coordinator_update()

    def _handle_coordinator_update(self) -> None:
        """Subclasses override to update local attributes from coordinator."""
        pass
