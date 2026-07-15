"""Config flow for BACnet-HA (minimal test version)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow, ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_DEVICE_ID,
    DEFAULT_MSRTP_NETWORK,
    DEFAULT_MSRTU_MAC,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_ENABLE_COV,
    HONEYWELL_FT82_PRESET,
)

_LOGGER = logging.getLogger(__name__)


class BACnetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Minimal config flow for BACnet-HA."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single-step config flow."""
        if user_input is not None:
            # Create the entry immediately with preset objects
            objects = [dict(obj) for obj in HONEYWELL_FT82_PRESET]
            entry_data = {
                "local_ip": user_input.get("local_ip", ""),
                "local_port": int(user_input.get("local_port", 47808)),
                "gateway_ip": user_input.get("gateway_ip", ""),
                "gateway_port": int(user_input.get("gateway_port", DEFAULT_GATEWAY_PORT)),
                "device_id": int(user_input.get("device_id", DEFAULT_DEVICE_ID)),
                "polling_interval": int(user_input.get("polling_interval", DEFAULT_POLLING_INTERVAL)),
                "enable_cov": user_input.get("enable_cov", DEFAULT_ENABLE_COV),
                "objects": objects,
                "name": user_input.get("name", "Honeywell FT-82"),
            }
            return self.async_create_entry(
                title=entry_data["name"],
                data=entry_data,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional("name", default="Honeywell FT-82"): str,
                    vol.Required("local_ip", default=self._find_local_ip()): str,
                    vol.Required("gateway_ip", default="192.168.100.103"): str,
                    vol.Optional("gateway_port", default=DEFAULT_GATEWAY_PORT): int,
                    vol.Optional("device_id", default=DEFAULT_DEVICE_ID): int,
                    vol.Optional("ms_rtp_network", default=DEFAULT_MSRTP_NETWORK): int,
                    vol.Optional("ms_rtu_mac", default=DEFAULT_MSRTU_MAC): int,
                }
            ),
            errors={},
            last_step=True,
        )

    @staticmethod
    def _find_local_ip() -> str:
        """Detect local IP."""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("8.8.8.8", 1))
                return s.getsockname()[0]
            except OSError:
                return "0.0.0.0"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow stub."""
        return BACnetOptionsFlow(config_entry)


class BACnetOptionsFlow(OptionsFlow):
    """Minimal options flow."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Options form with connection parameters."""
        if user_input is not None:
            # Merge with existing config
            new_data = dict(self._config_entry.data)
            for key in ("gateway_ip", "gateway_port", "device_id", "ms_rtu_mac",
                        "ms_rtp_network", "polling_interval", "enable_cov"):
                if key in user_input:
                    new_data[key] = user_input[key]
            return self.async_create_entry(title="", data=new_data)

        config = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "gateway_ip",
                        default=config.get("gateway_ip", "192.168.100.103"),
                    ): str,
                    vol.Optional(
                        "gateway_port",
                        default=config.get("gateway_port", DEFAULT_GATEWAY_PORT),
                    ): int,
                    vol.Optional(
                        "device_id",
                        default=config.get("device_id", DEFAULT_DEVICE_ID),
                    ): int,
                    vol.Optional(
                        "ms_rtp_network",
                        default=config.get("ms_rtp_network", DEFAULT_MSRTP_NETWORK),
                    ): int,
                    vol.Optional(
                        "ms_rtu_mac",
                        default=config.get("ms_rtu_mac", DEFAULT_MSRTU_MAC),
                    ): int,
                    vol.Optional(
                        "polling_interval",
                        default=config.get("polling_interval", DEFAULT_POLLING_INTERVAL),
                    ): int,
                }
            ),
            last_step=True,
        )