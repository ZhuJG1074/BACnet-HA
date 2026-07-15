"""Sensor platform for the BACnet-HA integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util.unit_system import UnitOfTemperature

from .coordinator import BACnetCoordinator
from .const import (
    DEVICE_CLASS_MAP,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
    SUPPORTED_SENSOR_OBJECT_TYPES,
    UNIT_SYSTEM_MAP,
)
from .entity import BACnetEntity, ReadBACnetEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet sensor platform."""
    coordinator: BACnetCoordinator = hass.data[entry.entry_id]["coordinator"]

    entities: list[SensorEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "sensor" and obj["object_type"] in SUPPORTED_SENSOR_OBJECT_TYPES:
            entities.append(
                BACnetSensor(coordinator, obj_key, obj, entry.entry_id)
            )

    async_add_entities(entities)


class BACnetSensor(ReadBACnetEntity, SensorEntity):
    """BACnet analog/multi-state input or value as a sensor."""

    _attr_should_poll = False
    _attr_entity_category: str | None = None

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        obj_type = obj["object_type"]
        self._attr_device_class = DEVICE_CLASS_MAP.get(obj_type)
        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)
        self._attr_native_unit_of_measurement = self._resolve_unit(obj)
        self._attr_state_class = SensorStateClass.MEASUREMENT

        # Object description (BACnet description property)
        self._object_description = obj.get("description", "")

        # Units display
        raw_units = obj.get("units")
        if raw_units is not None and str(raw_units).strip():
            self._attr_translation_key = "units"
            self._attr_translation_placeholders = {"units": str(raw_units)}
            self._attr_native_unit_of_measurement = None

        # BACnet object reference for diagnostics
        self._bacnet_object_type = obj_type
        self._bacnet_instance = obj["instance"]

        # Diagnostics attributes
        if obj.get("commandable"):
            self._attr_entity_category = "diagnostic"

    def _resolve_unit(self, obj: dict[str, Any]) -> str | None:
        """Resolve the native unit of measurement for an analog sensor."""
        obj_type = obj["object_type"]
        raw_units = obj.get("units")

        if self._attr_device_class == SensorDeviceClass.TEMPERATURE:
            return UnitOfTemperature.CELSIUS

        if raw_units is None or str(raw_units).strip() == "":
            return None

        raw_str = str(raw_units).strip()

        # Handle legacy BACpypes enum string representations
        if "enum" in raw_str.lower() or "Enum" in raw_str:
            unit_name = raw_str.split("(")[-1].rstrip(")").strip()
            return UNIT_SYSTEM_MAP.get(unit_name, None)

        # Direct lookup in unit mapping
        return UNIT_SYSTEM_MAP.get(raw_str, None)

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
    def native_value(self) -> StateType:
        """Return the sensor reading from coordinator data."""
        value = self._value()
        if value is None:
            return None
        if self._flag_out_of_service:
            return None
        return round(float(value), 2) if isinstance(value, (int, float)) else value

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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator update - state is auto-pulled via native_value."""
        pass
