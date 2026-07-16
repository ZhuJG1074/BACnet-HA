"""BACnet-HA integration for Home Assistant.

BACnet-HA connects BACnet devices to Home Assistant via a BACnet router/gateway
(e.g. 讯饶 Router1001-ARM-E), providing:
  - Real-time sensor readings via COV (Change of Value) subscriptions
  - Polling fallback when COV is unavailable
  - Commandable write support with Priority Array
  - Flexible per-object domain mapping (sensor, number, switch, climate, etc.)
  - Cross-subnet connectivity through the gateway (no BBMD needed)

Integration type: hub
IoT class: local_polling
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.typing import ConfigType

from .const import (
    DEFAULT_COV_INCREMENT,
    DEFAULT_COV_LIFETIME,
    DEFAULT_DEVICE_ID,
    DEFAULT_DEVICE_INSTANCE,
    DEFAULT_DOMAIN_MAP,
    DEFAULT_ENABLE_COV,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_LOCAL_PORT,
    DEFAULT_MSRTP_NETWORK,
    DEFAULT_MSRTU_MAC,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_USE_DESCRIPTION,
    DEFAULT_WRITE_PRIORITY,
    DOMAIN,
    HONEYWELL_FT82_PRESET,
    MAX_COV_LIFETIME,
    MIN_COV_INCREMENT,
    MIN_COV_LIFETIME,
    MIN_POLLING_INTERVAL,
)
from .helpers import mask_address

_LOGGER = logging.getLogger(__name__)

# Platforms this integration provides
PLATFORMS = ["binary_sensor", "climate", "number", "select", "sensor", "switch"]


# ---------------------------------------------------------------------------
# Lazy imports — defer bacpypes3 dependency until runtime
# ---------------------------------------------------------------------------

def _import_bacnet_client() -> Any:
    """Import BACnetClient lazily."""
    try:
        from .bacnet_client import BACnetClient
        return BACnetClient
    except ImportError as exc:
        raise ConfigEntryNotReady(
            f"BACpypes3 dependency not installed. "
            f"Please restart Home Assistant to install dependencies. ({exc})"
        ) from exc


def _import_coordinator() -> Any:
    """Import BACnetCoordinator lazily."""
    try:
        from .coordinator import BACnetCoordinator
        return BACnetCoordinator
    except ImportError as exc:
        raise ConfigEntryNotReady(
            f"Cannot import BACnetCoordinator. ({exc})"
        ) from exc


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the BACnet-HA integration from YAML (not recommended)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up BACnet-HA from a config entry."""
    _LOGGER.info("Setting up BACnet-HA entry: %s", entry.entry_id)

    # Normalise the entry data (migrate old keys, set defaults)
    normalised = await _normalise_entry_data(entry)
    entry_data = dict(entry.data)
    entry_data.update(normalised)

    # Build domain overrides from the domain_map option
    domain_overrides = _parse_domain_map(entry_data.get("domain_map", ""))
    cov_overrides = _parse_cov_map(entry_data.get("cov_map", ""))

    # Determine objects to import
    objects: list[dict[str, Any]] = entry_data.get("objects", [])
    if not objects:
        # Fallback: use preset Honeywell FT-82 objects
        objects = [dict(obj) for obj in HONEYWELL_FT82_PRESET]

    # Determine gateway target address
    gateway_ip = entry_data.get("gateway_ip", "")
    gateway_port = entry_data.get("gateway_port", DEFAULT_GATEWAY_PORT)

    # Lazy-import the BACnet client
    BACnetClient = _import_bacnet_client()

    client = BACnetClient(
        local_ip=entry_data.get("local_ip", ""),
        local_port=entry_data.get("local_port", DEFAULT_LOCAL_PORT),
        device_instance=entry_data.get("device_instance", DEFAULT_DEVICE_INSTANCE),
        gateway_ip=gateway_ip,
        target_device_id=entry_data.get("device_id"),
    )

    try:
        await client.connect()
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error(
            "Failed to connect to BACnet gateway %s: %s",
            gateway_ip, exc,
        )
        raise ConfigEntryNotReady(
            f"Cannot connect to BACnet gateway at {gateway_ip}"
        ) from exc

    # Determine the device address to use for reads/writes
    # Route-aware BACnet address: <network>:<mac>@<gateway_ip>
    gateway_ip = entry_data.get("gateway_ip", "")
    ms_rtp_network = entry_data.get("ms_rtp_network", DEFAULT_MSRTP_NETWORK)
    ms_rtu_mac = entry_data.get("ms_rtu_mac", DEFAULT_MSRTU_MAC)
    device_address = f"{ms_rtp_network}:{ms_rtu_mac}"

    # Lazy-import the coordinator
    BACnetCoordinator = _import_coordinator()

    coordinator = BACnetCoordinator(
        hass=hass,
        client=client,
        objects=objects,
        enable_cov=entry_data.get("enable_cov", DEFAULT_ENABLE_COV),
        polling_interval=entry_data.get("polling_interval", DEFAULT_POLLING_INTERVAL),
        use_description=entry_data.get("use_description", DEFAULT_USE_DESCRIPTION),
        domain_overrides=domain_overrides,
        cov_overrides=cov_overrides,
        entry=entry,
        cov_increment=entry_data.get("cov_increment", DEFAULT_COV_INCREMENT),
    )
    coordinator.device_address = device_address

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "devices": {},
        "config_data": entry_data,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register unload
    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )

    _LOGGER.info(
        "BACnet-HA %s loaded (%d objects, gateway=%s)",
        entry.title or "untitled",
        len(objects),
        mask_address(device_address),
    )

    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _normalise_entry_data(
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Sanitise and fill defaults for the entry data."""
    data = dict(entry.data)
    out: dict[str, Any] = {}

    out["local_ip"] = data.get("local_ip", "")
    out["local_port"] = int(data.get("local_port", DEFAULT_LOCAL_PORT))
    out["gateway_ip"] = data.get("gateway_ip", "")
    out["gateway_port"] = int(data.get("gateway_port", DEFAULT_GATEWAY_PORT))
    out["device_id"] = int(data.get("device_id", 4))
    out["device_instance"] = int(
        data.get("device_instance", DEFAULT_DEVICE_INSTANCE)
    )
    out["polling_interval"] = int(
        data.get("polling_interval", DEFAULT_POLLING_INTERVAL)
    )
    out["enable_cov"] = bool(data.get("enable_cov", DEFAULT_ENABLE_COV))
    out["use_description"] = bool(
        data.get("use_description", DEFAULT_USE_DESCRIPTION)
    )
    out["write_priority"] = int(
        data.get("write_priority", DEFAULT_WRITE_PRIORITY)
    )
    out["cov_increment"] = float(
        data.get("cov_increment", DEFAULT_COV_INCREMENT)
    )
    out["cov_lifetime"] = int(
        data.get("cov_lifetime", DEFAULT_COV_LIFETIME)
    )
    out["domain_map"] = data.get("domain_map", "")
    out["cov_map"] = data.get("cov_map", "")

    # Validate numeric ranges
    if out["polling_interval"] < MIN_POLLING_INTERVAL:
        out["polling_interval"] = MIN_POLLING_INTERVAL
    if out["polling_interval"] > 300:
        out["polling_interval"] = 300
    if out["cov_increment"] < MIN_COV_INCREMENT:
        out["cov_increment"] = MIN_COV_INCREMENT
    if out["cov_lifetime"] > MAX_COV_LIFETIME:
        out["cov_lifetime"] = MAX_COV_LIFETIME
    if out["cov_lifetime"] < MIN_COV_LIFETIME:
        out["cov_lifetime"] = MIN_COV_LIFETIME

    # Preserve existing objects list
    objects = data.get("objects", [])
    if objects:
        out["objects"] = objects

    return out


def _parse_domain_map(raw: str) -> dict[str, str]:
    """Parse the domain_map option into a dict of {obj_key: domain}."""
    if not raw or not isinstance(raw, str):
        return {}
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, domain = line.partition(":")
        key = key.strip()
        domain = domain.strip()
        if key and domain:
            result[key] = domain
    return result


def _parse_cov_map(raw: str) -> dict[str, bool]:
    """Parse the cov_map option into a dict of {obj_key: enabled}."""
    if not raw or not isinstance(raw, str):
        return {}
    result: dict[str, bool] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, enabled = line.partition(":")
        key = key.strip()
        enabled = enabled.strip().lower()
        if key:
            result[key] = enabled not in (
                "", "0", "false", "off", "no"
            )
    return result


# ---------------------------------------------------------------------------
# Unload
# ---------------------------------------------------------------------------

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading BACnet-HA entry: %s", entry.entry_id)

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    client = data.get("client")
    coordinator = data.get("coordinator")

    # Forward unload for platforms
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    # Shutdown coordinator and client
    if coordinator is not None:
        await coordinator.async_shutdown()
    if client is not None:
        await client.disconnect()

    # Clean up hass.data
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        del hass.data[DOMAIN][entry.entry_id]
        if not hass.data[DOMAIN]:
            del hass.data[DOMAIN]

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update by reloading the entry."""
    _LOGGER.info("Options updated for entry %s — reloading", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)