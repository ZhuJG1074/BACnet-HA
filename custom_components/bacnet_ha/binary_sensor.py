"""Binary sensor platform for the BACnet-HA integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_system import UnitOfTemperature

from .coordinator import BACnetCoordinator
from .const import (
    DEVICE_CLASS_MAP,
    DOMAIN,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from .entity import BACnetEntity, CommandableBACnetEntity, ReadBACnetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet binary sensor platform."""
    coordinator: BACnetCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[BinarySensorEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "binary_sensor" and obj["object_type"] in {
            OBJECT_TYPE_BINARY_INPUT,
            OBJECT_TYPE_BINARY_OUTPUT,
            OBJECT_TYPE_BINARY_VALUE,
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
            OBJECT_TYPE_MULTI_STATE_INPUT,
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            if obj["object_type"] in {
                OBJECT_TYPE_BINARY_OUTPUT,
                OBJECT_TYPE_BINARY_VALUE,
                OBJECT_TYPE_MULTI_STATE_OUTPUT,
                OBJECT_TYPE_MULTI_STATE_VALUE,
            }:
                entities.append(
                    BACnetBinarySensor(
                        coordinator, obj_key, obj, entry.entry_id, commandable=True
                    )
                )
            else:
                entities.append(
                    BACnetBinarySensor(
                        coordinator, obj_key, obj, entry.entry_id, commandable=False
                    )
                )

    async_add_entities(entities)


class BACnetBinarySensor(BACnetEntity, BinarySensorEntity):
    """Read-only BACnet binary sensor for analog/multi-state input/value.

    Reads the current `presentValue` from coordinator data and converts it
    to a boolean using the appropriate threshold.
    """

    _attr_should_poll = False
    _attr_entity_category: str | None = None

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
        commandable: bool = False,
    ) -> None:
        """Initialize the binary sensor entity."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        obj_type = obj["object_type"]
        self._attr_device_class = DEVICE_CLASS_MAP.get(obj_type)
        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)

        # Default to temperature for analog types
        if obj_type in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            self._attr_device_class = BinarySensorDeviceClass.TEMPERATURE

        self._object_description = obj.get("description", "")
        self._bacnet_object_type = obj_type
        self._bacnet_instance = obj["instance"]
        self._commandable = commandable

        if commandable:
            self._attr_entity_category = "diagnostic"

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
        """Return True if the sensor is on."""
        value = self._value()
        if value is None:
            return False
        if self._flag_out_of_service:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(float(value))
        return False

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

    def _handle_coordinator_update(self) -> None:
        """Update state on coordinator update."""
        pass


class BACnetBinarySensorCommandable(CommandableBACnetEntity, BinarySensorEntity):
    """Commandable BACnet binary sensor for binary output/value."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the commandable binary sensor."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        obj_type = obj["object_type"]
        self._attr_device_class = DEVICE_CLASS_MAP.get(obj_type)
        if obj_type in {
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            self._attr_device_class = BinarySensorDeviceClass.TEMPERATURE

        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)
        self._object_description = obj.get("description", "")
        self._bacnet_object_type = obj_type
        self._bacnet_instance = obj["instance"]

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
        """Return True if the sensor is on."""
        value = self._value()
        if value is None:
            return False
        if self._flag_out_of_service:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(float(value))
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the binary sensor on."""
        await self._write_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the binary sensor off."""
        await self._write_value(False)

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
