"""Switch platform for the BACnet-HA integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BACnetCoordinator
from .const import (
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from .entity import CommandableBACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet switch platform."""
    coordinator: BACnetCoordinator = hass.data[entry.entry_id]["coordinator"]

    entities: list[SwitchEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "switch" and obj["object_type"] in {
            OBJECT_TYPE_BINARY_OUTPUT,
            OBJECT_TYPE_BINARY_VALUE,
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            entities.append(
                BACnetSwitch(coordinator, obj_key, obj, entry.entry_id)
            )

    async_add_entities(entities)


class BACnetSwitch(CommandableBACnetEntity, SwitchEntity):
    """Commandable BACnet binary or multi-state output/value as a Switch."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the switch entity."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)
        self._object_description = obj.get("description", "")
        self._bacnet_instance = obj["instance"]
        self._bacnet_object_type = obj["object_type"]

    @property
    def available(self) -> bool:
        """Entity is available only when coordinator data is fresh."""
        if not super().available:
            return False
        value = self._value()
        if value is None:
            return False
        if self._flag_out_of_service:
            return False
        return True

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        value = self._value()
        if value is None:
            return False
        if self._flag_out_of_service:
            return False
        # Handle both boolean and numeric representations
        if isinstance(value, bool):
            return value
        try:
            return bool(float(value))
        except (TypeError, ValueError):
            return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        try:
            success = await self._write_value(True)
            if not success:
                raise HomeAssistantError(
                    f"Failed to turn on BACnet object "
                    f"{self._bacnet_object_type}:{self._bacnet_instance}"
                )
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Turn on failed for %s:%d: %s",
                self._bacnet_object_type,
                self._bacnet_instance,
                exc,
            )
            raise HomeAssistantError("Failed to turn on switch") from exc

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        try:
            success = await self._write_value(False)
            if not success:
                raise HomeAssistantError(
                    f"Failed to turn off BACnet object "
                    f"{self._bacnet_object_type}:{self._bacnet_instance}"
                )
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Turn off failed for %s:%d: %s",
                self._bacnet_object_type,
                self._bacnet_instance,
                exc,
            )
            raise HomeAssistantError("Failed to turn off switch") from exc

    async def async_added_to_hass(self) -> None:
        """Restore state from coordinator on startup."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    def _handle_coordinator_update(self) -> None:
        """Update local state from coordinator data."""
        value = self._value()
        if value is not None and not self._flag_out_of_service:
            if isinstance(value, bool):
                self._attr_is_on = value
            else:
                try:
                    self._attr_is_on = bool(float(value))
                except (TypeError, ValueError):
                    self._attr_is_on = False
        elif not self._flag_out_of_service:
            self._attr_is_on = False

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Additional diagnostic attributes."""
        attrs: dict[str, Any] = {
            "bacnet_object_type": self._bacnet_object_type,
            "bacnet_instance": self._bacnet_instance,
            "bacnet_update_method": self.coordinator.get_update_method(self._obj_key),
        }
        if self._object_description:
            attrs["bacnet_description"] = self._object_description
        if self._flag_fault:
            attrs["bacnet_fault"] = True
        if self._flag_out_of_service:
            attrs["bacnet_out_of_service"] = True
        return attrs
