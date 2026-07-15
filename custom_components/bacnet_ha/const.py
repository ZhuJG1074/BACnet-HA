"""Constants for the BACnet-HA integration.

This integration connects BACnet devices to Home Assistant via a BACnet
router/gateway (e.g. 讯饶 Router1001-ARM-E), with support for:
  - Cross-subnet bridging (gateway handles routing, no BBMD needed)
  - Real-time updates via COV (Change of Value) with polling fallback
  - Outage recovery with automatic reconnect
  - Flexible per-object domain mapping
  - Preset Honeywell FT-82 object definitions (24-28)

IMPORTANT:  The original BACnet integration used BBMD (Broadcast Management
Device) for cross-subnet discovery.  In this revised architecture, the
BACnet gateway (讯饶 Router1001-ARM-E) acts as a BACnet Router, not a
BBMD.  Cross-subnet traffic is handled by the gateway natively, so BBMD
and Foreign Device Registration are removed entirely.
"""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
# ---------------------------------------------------------------------------
# Integration identity
# ---------------------------------------------------------------------------

DOMAIN = "bacnet_ha"

# ---------------------------------------------------------------------------
# Config flow step IDs
# ---------------------------------------------------------------------------

STEP_ID_USER = "user"
STEP_ID_CONNECTION = "connection"
STEP_ID_ADVANCED_OPTIONS = "advanced_options"
STEP_ID_PRESERVED_OPTIONS = "preserved_options"
STEP_ID_SELECT_OBJECTS = "select_objects"

# ---------------------------------------------------------------------------
# BACnet object type constants (IANA-registered)
# ---------------------------------------------------------------------------

OBJECT_TYPE_ANALOG_INPUT = 0
OBJECT_TYPE_ANALOG_OUTPUT = 1
OBJECT_TYPE_ANALOG_VALUE = 2
OBJECT_TYPE_BINARY_INPUT = 8
OBJECT_TYPE_BINARY_OUTPUT = 9
OBJECT_TYPE_BINARY_VALUE = 10
OBJECT_TYPE_MULTI_STATE_INPUT = 14
OBJECT_TYPE_MULTI_STATE_OUTPUT = 15
OBJECT_TYPE_MULTI_STATE_VALUE = 16

# ---------------------------------------------------------------------------
# Supported object types by HA platform
# ---------------------------------------------------------------------------

SUPPORTED_SENSOR_OBJECT_TYPES = {
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}

SUPPORTED_NUMBER_OBJECT_TYPES = {
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}

# ---------------------------------------------------------------------------
# Default HA domain mapping per BACnet object type
# ---------------------------------------------------------------------------

DEFAULT_DOMAIN_MAP: dict[int, str] = {
    OBJECT_TYPE_ANALOG_INPUT: "sensor",
    OBJECT_TYPE_ANALOG_OUTPUT: "number",
    OBJECT_TYPE_ANALOG_VALUE: "sensor",  # commandable → number (overrideable)
    OBJECT_TYPE_BINARY_INPUT: "binary_sensor",
    OBJECT_TYPE_BINARY_OUTPUT: "switch",
    OBJECT_TYPE_BINARY_VALUE: "binary_sensor",  # commandable → switch (overrideable)
    OBJECT_TYPE_MULTI_STATE_INPUT: "sensor",
    OBJECT_TYPE_MULTI_STATE_OUTPUT: "select",
    OBJECT_TYPE_MULTI_STATE_VALUE: "sensor",  # commandable → select (overrideable)
}

# ---------------------------------------------------------------------------
# Default config values
# ---------------------------------------------------------------------------

DEFAULT_LOCAL_PORT: int = 47808
DEFAULT_GATEWAY_PORT: int = 47808
DEFAULT_DEVICE_ID: int = 4
DEFAULT_DEVICE_INSTANCE: int = 3900000
DEFAULT_POLLING_INTERVAL: int = 30
DEFAULT_ENABLE_COV: bool = True
DEFAULT_USE_DESCRIPTION: bool = False
DEFAULT_WRITE_PRIORITY: int = 16
DEFAULT_COV_INCREMENT: float = 0.5
DEFAULT_COV_LIFETIME: int = 300

# ---------------------------------------------------------------------------
# Config value limits
# ---------------------------------------------------------------------------

MIN_POLLING_INTERVAL: int = 5
MAX_POLLING_INTERVAL: int = 300
MIN_COV_INCREMENT: float = 0.1
MAX_COV_INCREMENT: float = 1000.0
MIN_COV_LIFETIME: int = 60
MAX_COV_LIFETIME: int = 3600

# ---------------------------------------------------------------------------
# Outage detection
# ---------------------------------------------------------------------------

RECONNECT_THRESHOLD: int = 3
MAX_SILENT_FAILURES: int = 10

# ---------------------------------------------------------------------------
# Preset: Honeywell FT-82 (Honeywell HT9612D3100 Fan Coil Thermostat Driver)
#
# This preset maps the 5 BACnet AnalogValue objects from the Honeywell FT-82
# device behind the 讯饶 Router1001-ARM-E gateway to Home Assistant entities.
#
# Architecture:
#   HA server (110.x) ── BACnet/IP ──► 讯饶 Router1001-ARM-E (192.168.100.103:47808)
#                                                      │
#                                                      └── BACnet MS/TP ──► Honeywell HT9612D3100
#
# Objects:
#   RoomTemperature (24)  — analogValue — sensor (只读)
#   RoomSetpoint    (25)  — analogValue — climate (commandable)
#   FanSwitch       (26)  — analogValue — number (commandable)
#   SystemSwitch    (27)  — analogValue — number (commandable)
#   PowerSwitch     (28)  — analogValue — number (commandable)
# ---------------------------------------------------------------------------

HONEYWELL_FT82_PRESET: list[dict[str, Any]] = [
    {
        "object_type": OBJECT_TYPE_ANALOG_VALUE,
        "instance": 24,
        "object_name": "RoomTemperature",
        "description": "房间温度",
        "mode": "read",
        "commandable": False,
        "units": "celcius",
        "domain": "sensor",
        "device_class": SensorDeviceClass.TEMPERATURE,
    },
    {
        "object_type": OBJECT_TYPE_ANALOG_VALUE,
        "instance": 25,
        "object_name": "RoomSetpoint",
        "description": "设定温度",
        "mode": "commandable",
        "commandable": True,
        "units": "celcius",
        "domain": "climate",
        "device_class": SensorDeviceClass.TEMPERATURE,
    },
    {
        "object_type": OBJECT_TYPE_ANALOG_VALUE,
        "instance": 26,
        "object_name": "FanSwitch",
        "description": "风扇开关",
        "mode": "commandable",
        "commandable": True,
        "units": "",
        "domain": "number",
        "device_class": None,
    },
    {
        "object_type": OBJECT_TYPE_ANALOG_VALUE,
        "instance": 27,
        "object_name": "SystemSwitch",
        "description": "系统开关",
        "mode": "commandable",
        "commandable": True,
        "units": "",
        "domain": "number",
        "device_class": None,
    },
    {
        "object_type": OBJECT_TYPE_ANALOG_VALUE,
        "instance": 28,
        "object_name": "PowerSwitch",
        "description": "电源开关",
        "mode": "commandable",
        "commandable": True,
        "units": "",
        "domain": "number",
        "device_class": None,
    },
]

# ---------------------------------------------------------------------------
# Device class mapping (BACnet type → HA device_class)
# ---------------------------------------------------------------------------

DEVICE_CLASS_MAP: dict[int, str | None] = {
    OBJECT_TYPE_ANALOG_INPUT: SensorDeviceClass.TEMPERATURE,
    OBJECT_TYPE_ANALOG_OUTPUT: SensorDeviceClass.TEMPERATURE,
    OBJECT_TYPE_ANALOG_VALUE: SensorDeviceClass.TEMPERATURE,
    OBJECT_TYPE_BINARY_INPUT: BinarySensorDeviceClass.OCCUPANCY,
    OBJECT_TYPE_BINARY_OUTPUT: BinarySensorDeviceClass.OCCUPANCY,
    OBJECT_TYPE_BINARY_VALUE: BinarySensorDeviceClass.OCCUPANCY,
    OBJECT_TYPE_MULTI_STATE_INPUT: None,
    OBJECT_TYPE_MULTI_STATE_OUTPUT: None,
    OBJECT_TYPE_MULTI_STATE_VALUE: None,
}

# ---------------------------------------------------------------------------
# BACnet unit enumerations → Home Assistant native units
# ---------------------------------------------------------------------------

UNIT_SYSTEM_MAP: dict[str, str | None] = {
    "none": None,
    "percent": "%",
    "fahrenheit": "°F",
    "celcius": "°C",
    "celsius": "°C",
    "kelvin": "K",
    "meter": "m",
    "millimeter": "mm",
    "centimeter": "cm",
    "kilometer": "km",
    "square_meter": "m²",
    "cubic_meter": "m³",
    "liter": "L",
    "milliliter": "mL",
    "kilogram": "kg",
    "gram": "g",
    "pascal": "Pa",
    "kilopascal": "kPa",
    "megapascal": "MPa",
    "bar": "bar",
    "psi": "psi",
    "watt": "W",
    "kilowatt": "kW",
    "milliwatt": "mW",
    "volt": "V",
    "millivolt": "mV",
    "kilovolt": "kV",
    "ampere": "A",
    "milliampere": "mA",
    "hertz": "Hz",
    "kilohertz": "kHz",
    "megahertz": "MHz",
    "joule": "J",
    "kilocalorie": "kcal",
    "megajoule": "MJ",
    "watt_hour": "Wh",
    "kilowatt_hour": "kWh",
    "lux": "lx",
    "lumen": "lm",
    "ppm": "ppm",
    "ppb": "ppb",
    "second": "s",
    "minute": "min",
    "hour": "h",
    "day": "d",
}
