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
    DEFAULT_LOCAL_PORT,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_DEVICE_ID,
    DEFAULT_MSRTP_NETWORK,
    DEFAULT_MSRTU_MAC,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_ENABLE_COV,
    HONEYWELL_FT82_PRESET,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chinese label constants (keys used in vol.Schema as display labels)
# ---------------------------------------------------------------------------
_LBL_NAME = "设备名称"
_LBL_LOCAL_IP = "本地IP地址"
_LBL_LOCAL_PORT = "本地端口"
_LBL_GATEWAY_IP = "网关IP地址"
_LBL_GATEWAY_PORT = "网关端口"
_LBL_DEVICE_ID = "设备ID"
_LBL_MS_RTP_NETWORK = "MS/TP 网络号"
_LBL_MS_RTU_MAC = "MS/TP MAC地址"
_LBL_POLLING_INTERVAL = "轮询间隔(秒)"
_LBL_ENABLE_COV = "启用COV"


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
            # Map Chinese labels back to snake_case keys
            data = {
                "name": user_input.get(_LBL_NAME, "Honeywell FT-82"),
                "local_ip": user_input.get(_LBL_LOCAL_IP, ""),
                "local_port": int(user_input.get(_LBL_LOCAL_PORT, DEFAULT_LOCAL_PORT)),
                "gateway_ip": user_input.get(_LBL_GATEWAY_IP, ""),
                "gateway_port": int(user_input.get(_LBL_GATEWAY_PORT, DEFAULT_GATEWAY_PORT)),
                "device_id": int(user_input.get(_LBL_DEVICE_ID, DEFAULT_DEVICE_ID)),
                "ms_rtp_network": int(user_input.get(_LBL_MS_RTP_NETWORK, DEFAULT_MSRTP_NETWORK)),
                "ms_rtu_mac": int(user_input.get(_LBL_MS_RTU_MAC, DEFAULT_MSRTU_MAC)),
                "polling_interval": int(
                    user_input.get(_LBL_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
                ),
                "enable_cov": user_input.get(_LBL_ENABLE_COV, DEFAULT_ENABLE_COV),
            }
            # Create the entry with preset objects
            objects = [dict(obj) for obj in HONEYWELL_FT82_PRESET]
            entry_data = {**data, "objects": objects}
            return self.async_create_entry(
                title=entry_data["name"],
                data=entry_data,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(_LBL_NAME, default="Honeywell FT-82"): str,
                    vol.Required(_LBL_LOCAL_IP, default=self._find_local_ip()): str,
                    vol.Optional(_LBL_LOCAL_PORT, default=DEFAULT_LOCAL_PORT): int,
                    vol.Required(_LBL_GATEWAY_IP, default="192.168.100.103"): str,
                    vol.Optional(_LBL_GATEWAY_PORT, default=DEFAULT_GATEWAY_PORT): int,
                    vol.Optional(_LBL_DEVICE_ID, default=DEFAULT_DEVICE_ID): int,
                    vol.Optional(_LBL_MS_RTP_NETWORK, default=DEFAULT_MSRTP_NETWORK): int,
                    vol.Optional(_LBL_MS_RTU_MAC, default=DEFAULT_MSRTU_MAC): int,
                    vol.Optional(_LBL_POLLING_INTERVAL, default=DEFAULT_POLLING_INTERVAL): int,
                    vol.Optional(_LBL_ENABLE_COV, default=DEFAULT_ENABLE_COV): bool,
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
            # Map Chinese labels back to snake_case keys
            new_data = dict(self._config_entry.data)
            label_to_key = {
                _LBL_LOCAL_IP: "local_ip",
                _LBL_LOCAL_PORT: "local_port",
                _LBL_GATEWAY_IP: "gateway_ip",
                _LBL_GATEWAY_PORT: "gateway_port",
                _LBL_DEVICE_ID: "device_id",
                _LBL_MS_RTP_NETWORK: "ms_rtp_network",
                _LBL_MS_RTU_MAC: "ms_rtu_mac",
                _LBL_POLLING_INTERVAL: "polling_interval",
                _LBL_ENABLE_COV: "enable_cov",
            }
            for lbl, key in label_to_key.items():
                if lbl in user_input:
                    new_data[key] = user_input[lbl]
            return self.async_create_entry(title="", data=new_data)

        # Read defaults from existing config
        config = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        _LBL_LOCAL_IP,
                        default=config.get("local_ip", "0.0.0.0"),
                    ): str,
                    vol.Optional(
                        _LBL_LOCAL_PORT,
                        default=config.get("local_port", DEFAULT_LOCAL_PORT),
                    ): int,
                    vol.Required(
                        _LBL_GATEWAY_IP,
                        default=config.get("gateway_ip", "192.168.100.103"),
                    ): str,
                    vol.Optional(
                        _LBL_GATEWAY_PORT,
                        default=config.get("gateway_port", DEFAULT_GATEWAY_PORT),
                    ): int,
                    vol.Optional(
                        _LBL_DEVICE_ID,
                        default=config.get("device_id", DEFAULT_DEVICE_ID),
                    ): int,
                    vol.Optional(
                        _LBL_MS_RTP_NETWORK,
                        default=config.get("ms_rtp_network", DEFAULT_MSRTP_NETWORK),
                    ): int,
                    vol.Optional(
                        _LBL_MS_RTU_MAC,
                        default=config.get("ms_rtu_mac", DEFAULT_MSRTU_MAC),
                    ): int,
                    vol.Optional(
                        _LBL_POLLING_INTERVAL,
                        default=config.get("polling_interval", DEFAULT_POLLING_INTERVAL),
                    ): int,
                    vol.Optional(
                        _LBL_ENABLE_COV,
                        default=config.get("enable_cov", DEFAULT_ENABLE_COV),
                    ): bool,
                }
            ),
            last_step=True,
        )
