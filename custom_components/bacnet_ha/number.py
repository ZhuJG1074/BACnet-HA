"""Number platform for the BACnet-HA integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_system import UnitOfTemperature

from .coordinator import BACnetCoordinator
from .const import (
    DEFAULT_COV_INCREMENT,
    DEFAULT_WRITE_PRIORITY,
    DEVICE_CLASS_MAP,
    DOMAIN,
    MAX_COV_INCREMENT,
    MIN_COV_INCREMENT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    SUPPORTED_NUMBER_OBJECT_TYPES,
    UNIT_SYSTEM_MAP,
)
from .entity import CommandableBACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet number platform."""
    coordinator: BACnetCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[NumberEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "number" and obj["object_type"] in SUPPORTED_NUMBER_OBJECT_TYPES:
            if obj.get("commandable"):
                entities.append(
                    BACnetNumber(coordinator, obj_key, obj, entry.entry_id)
                )

    async_add_entities(entities)


class BACnetNumber(CommandableBACnetEntity, NumberEntity):
    """Commandable BACnet analog output/value as a Number entity."""

    _attr_should_poll = False
    _attr_mode: NumberMode = NumberMode.BOX

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)
        self._attr_native_unit_of_measurement = self._resolve_unit(obj)
        self._attr_native_value = None

        # Resolve numeric range from coordinator cache
        self._object_type = obj["object_type"]
        obj_type = obj["object_type"]
        self._attr_device_class = DEVICE_CLASS_MAP.get(obj_type)

        if self._attr_device_class == DEVICE_CLASS_MAP.get(
            OBJECT_TYPE_ANALOG_OUTPUT, "temperature"
        ):
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

        # Object description (BACnet description property)
        self._object_description = obj.get("description", "")
        # BACnet object reference
        self._bacnet_instance = obj["instance"]
        self._bacnet_object_type = obj_type

    def _resolve_unit(self, obj: dict[str, Any]) -> str | None:
        """Resolve native unit from BACnet units property."""
        raw_units = obj.get("units")
        if raw_units is None or str(raw_units).strip() == "":
            return None
        raw_str = str(raw_units).strip()
        if "enum" in raw_str.lower() or "Enum" in raw_str:
            unit_name = raw_str.split("(")[-1].rstrip(")").strip()
            return UNIT_SYSTEM_MAP.get(unit_name, None)
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
    def native_value(self) -> float | None:
        """Return the current number value."""
        return self._attr_native_value

    async def async_set_native_value(self, value: float) -> None:
        """Send a new value to the BACnet device."""
        try:
            success = await self._write_value(value)
            if success:
                self._attr_native_value = value
            else:
                raise HomeAssistantError(
                    f"Failed to write value {value} to "
                    f"BACnet object {self._bacnet_object_type}:{self._bacnet_instance}"
                )
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Write failed for %s:%d: %s",
                self._bacnet_object_type,
                self._bacnet_instance,
                exc,
            )
            raise HomeAssistantError("Failed to set value") from exc

    async def async_added_to_hass(self) -> None:
        """Restore state from coordinator on startup."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    def _handle_coordinator_update(self) -> None:
        """Update local state from coordinator data."""
        raw = self._value()
        if raw is not None and not self._flag_out_of_service:
            self._attr_native_value = round(float(raw), 2)
        elif not self._flag_out_of_service:
            self._attr_native_value = None

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
