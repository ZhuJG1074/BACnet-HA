"""Select platform for the BACnet-HA integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BACnetCoordinator
from .const import (
    DEFAULT_COV_INCREMENT,
    DOMAIN,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
    UNIT_SYSTEM_MAP,
)
from .entity import CommandableBACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet select platform."""
    coordinator: BACnetCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SelectEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "select" and obj["object_type"] in {
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            if obj.get("commandable"):
                entities.append(
                    BACnetSelect(coordinator, obj_key, obj, entry.entry_id)
                )

    async_add_entities(entities)


class BACnetSelect(CommandableBACnetEntity, SelectEntity):
    """Commandable BACnet multi-state output/value as a Select entity."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the select entity."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)

        # Read the number of states from the device
        num_states = obj.get("numStates")
        if num_states is not None:
            self._num_states = num_states
        else:
            self._num_states = 3  # default

        self._options = [str(i) for i in range(self._num_states)]
        self._object_description = obj.get("description", "")
        self._bacnet_object_type = obj["object_type"]
        self._bacnet_instance = obj["instance"]

    @property
    def options(self) -> list[str]:
        """Return the available options."""
        return self._options

    @property
    def current_option(self) -> str | None:
        """Return the current selected option."""
        value = self._value()
        if value is None or self._flag_out_of_service:
            return None
        try:
            val = int(float(value))
            return str(val) if 0 <= val < self._num_states else None
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        """Select a new option and write it to the BACnet device."""
        try:
            value = int(option)
            success = await self._write_value(value)
            if not success:
                raise HomeAssistantError(
                    f"Failed to set option '{option}' on BACnet object "
                    f"{self._bacnet_object_type}:{self._bacnet_instance}"
                )
        except HomeAssistantError:
            raise
        except (ValueError, TypeError) as exc:
            _LOGGER.error("Invalid option '%s': %s", option, exc)
            raise HomeAssistantError("Invalid option") from exc
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Select option failed for %s:%d: %s",
                self._bacnet_object_type,
                self._bacnet_instance,
                exc,
            )
            raise HomeAssistantError("Failed to select option") from exc

    async def async_added_to_hass(self) -> None:
        """Restore state from coordinator on startup."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    def _handle_coordinator_update(self) -> None:
        """Update local state from coordinator data."""
        value = self._value()
        if value is not None and not self._flag_out_of_service:
            try:
                val = int(float(value))
                self._attr_current_option = str(val) if 0 <= val < self._num_states else None
            except (TypeError, ValueError):
                self._attr_current_option = None
        elif not self._flag_out_of_service:
            self._attr_current_option = None

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
