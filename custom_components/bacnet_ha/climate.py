"""Climate platform for the BACnet-HA integration."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.unit_system import UnitOfTemperature

from .coordinator import BACnetCoordinator
from .const import (
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
)
from .entity import BACnetEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BACnet climate platform."""
    coordinator: BACnetCoordinator = hass.data[entry.entry_id]["coordinator"]

    entities: list[ClimateEntity] = []
    for obj in coordinator.objects:
        obj_key = f"{obj['object_type']}:{obj['instance']}"
        domain = coordinator.get_domain_for_object(obj)
        if domain == "climate" and obj["object_type"] in {
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            entities.append(
                BACnetClimate(coordinator, obj_key, obj, entry.entry_id)
            )

    async_add_entities(entities)


class BACnetClimate(BACnetEntity, ClimateEntity):
    """BACnet climate entity using an analog output/value as the setpoint."""

    _attr_should_poll = False
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes: list[HVACMode] = [HVACMode.HEAT, HVACMode.OFF]
    _attr_target_temperature_step: float = 0.5

    def __init__(
        self,
        coordinator: BACnetCoordinator,
        obj_key: str,
        obj: dict[str, Any],
        entry_id: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, obj_key, obj["object_type"], obj["instance"], entry_id)

        self._attr_entity_registry_enabled_default = True
        self._attr_has_entity_name = True
        self._attr_name = coordinator.get_entity_name(obj)

        # Resolution for temperature (default 0.5°C)
        self._attr_target_temperature_step = 0.5

        # Commandability - climate can write to the setpoint
        self._commandable = obj.get("commandable", False)
        self.write_priority: int = coordinator.write_priority if coordinator.entry is not None else 16

        self._object_description = obj.get("description", "")
        self._bacnet_object_type = obj["object_type"]
        self._bacnet_instance = obj["instance"]

        # If commandable, add temperature range support
        if self._commandable:
            # Default reasonable HVAC temperature range (can be overridden per-entry)
            self._attr_min_temp = 10.0
            self._attr_max_temp = 35.0

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
    def current_temperature(self) -> float | None:
        """Return the current setpoint value as temperature."""
        value = self._value()
        if value is None:
            return None
        try:
            return round(float(value), 1)
        except (TypeError, ValueError):
            return None

    @property
    def hvac_action(self) -> HVACAction:
        """Return the current HVAC action."""
        if not self.available:
            return HVACAction.OFF
        if self._flag_out_of_service:
            return HVACAction.OFF
        # Default action when available
        return HVACAction.IDLE

    @property
    def hvac_mode(self) -> HVACMode:
        """Return the current HVAC mode."""
        if not self.available:
            return HVACMode.OFF
        if self._flag_out_of_service:
            return HVACMode.OFF
        # If the setpoint is above a threshold, treat as heating
        value = self._value()
        if value is not None:
            try:
                if float(value) >= self._attr_min_temp:
                    return HVACMode.HEAT
            except (TypeError, ValueError):
                pass
        return HVACMode.OFF

    @property
    def min_temp(self) -> float:
        """Return the minimum temperature."""
        return self._attr_min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum temperature."""
        return self._attr_max_temp

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get("temperature")
        if temperature is None:
            return

        try:
            success = await self.coordinator.client.write_property(
                device_address=self.coordinator.device_address,
                object_type=self._attr_object_type,
                instance=self._attr_instance,
                property_name="presentValue",
                value=float(temperature),
                priority=self.write_priority,
            )
            if not success:
                raise HomeAssistantError(
                    f"Failed to set temperature to {temperature}°C "
                    f"on BACnet object {self._bacnet_object_type}:{self._bacnet_instance}"
                )
        except HomeAssistantError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Set temperature failed for %s:%d: %s",
                self._bacnet_object_type,
                self._bacnet_instance,
                exc,
            )
            raise HomeAssistantError("Failed to set temperature") from exc

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            # Writing 0 (or a value below min_temp) to signal OFF
            try:
                await self.coordinator.client.write_property(
                    device_address=self.coordinator.device_address,
                    object_type=self._attr_object_type,
                    instance=self._attr_instance,
                    property_name="presentValue",
                    value=0.0,
                    priority=self.write_priority,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Turn off failed for %s:%d: %s", self._bacnet_object_type, self._bacnet_instance, exc)
        elif hvac_mode == HVACMode.HEAT:
            # Set a default temperature when turning on
            current = self.current_temperature
            default_temp = current if current is not None else 21.0
            try:
                await self.coordinator.client.write_property(
                    device_address=self.coordinator.device_address,
                    object_type=self._attr_object_type,
                    instance=self._attr_instance,
                    property_name="presentValue",
                    value=float(default_temp),
                    priority=self.write_priority,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Set heating failed for %s:%d: %s", self._bacnet_object_type, self._bacnet_instance, exc)

    async def async_turn_on(self) -> None:
        """Turn the climate entity on (set to HEAT mode)."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the climate entity off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_added_to_hass(self) -> None:
        """Restore state from coordinator on startup."""
        self._handle_coordinator_update()
        await super().async_added_to_hass()

    def _handle_coordinator_update(self) -> None:
        """Update local state from coordinator data."""
        pass

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
