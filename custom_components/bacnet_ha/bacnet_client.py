"""
BACnet client module — isolates all BACpypes3 interaction.

Responsibilities:
- Network connection (NormalApplication bound to local IP:port)
- Device discovery via Who-Is / I-Am
- Object list and property reads (ReadProperty / ReadPropertyMultiple)
- Property writes with Priority Array support and Null/Relinquish
- COV subscription management
- Commandability/writability detection

IMPORTANT: This client connects DIRECTLY to the BACnet gateway device
(e.g. 讯饶 Router1001-ARM-E) over BACnet/IP.  Cross-subnet routing is
handled by the gateway itself — NO BBMD / Foreign Device Registration
is used.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, Callable

from bacpypes3.apdu import ErrorRejectAbortNack
from bacpypes3.ipv4.app import NormalApplication
from bacpypes3.local.device import DeviceObject
from bacpypes3.pdu import Address, IPv4Address
from bacpypes3.primitivedata import (
    CharacterString,
    Enumerated,
    Null,
    ObjectIdentifier,
    Real,
    Unsigned,
)

from .const import (
    DEFAULT_WRITE_PRIORITY,
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
)
from .helpers import mask_address as _mask_address

_LOGGER = logging.getLogger(__name__)

# BACnet object types we support importing as HA entities
SUPPORTED_OBJECT_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_INPUT,
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_INPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_INPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}

# Object types that are inherently commandable (have a Priority Array)
COMMANDABLE_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_OUTPUT,
    OBJECT_TYPE_BINARY_OUTPUT,
    OBJECT_TYPE_MULTI_STATE_OUTPUT,
}

# Object types that *may* be writable (Values can optionally be commandable)
POTENTIALLY_WRITABLE_TYPES: set[int] = {
    OBJECT_TYPE_ANALOG_VALUE,
    OBJECT_TYPE_BINARY_VALUE,
    OBJECT_TYPE_MULTI_STATE_VALUE,
}


class BACnetClient:
    """Wrapper around BACpypes3 providing a clean async API for HA.

    This integration connects to a BACnet gateway (e.g. 讯饶 Router1001-ARM-E)
    over BACnet/IP.  All BACnet traffic — reads, writes, COV subscriptions —
    is sent directly to the gateway, which handles cross-subnet routing.

    Usage:
        client = BACnetClient(
            local_ip="192.168.1.100",  # HA server's own IP
            local_port=47808,
            gateway_ip="192.168.100.103",  # Xunr Router1001-ARM-E IP
            target_device_id=4,
        )
        await client.connect()
        devices = await client.discover_devices(timeout=5)
        objects = await client.read_object_list(device_address, device_id)
        value = await client.read_property(address, obj_type, instance, prop_id)
        await client.write_property(address, obj_type, instance, prop_id, value, priority=8)
        await client.disconnect()
    """

    def __init__(
        self,
        local_ip: str = "",
        local_port: int = 47808,
        device_instance: int | None = None,
        gateway_ip: str = "",
        target_device_id: int | None = None,
    ) -> None:
        self._local_ip = local_ip.strip()
        self._local_port = local_port
        self._gateway_ip = gateway_ip.strip()
        self._target_device_id = target_device_id

        # Derive a stable, unique device instance from the local address.
        # Range 3900000–4194302 is unlikely to collide with real BMS devices.
        self._device_instance = (
            device_instance
            if device_instance is not None
            else self._derive_device_instance(local_ip, local_port)
        )

        self._app: NormalApplication | None = None
        self._cov_tasks: dict[str, asyncio.Task] = {}
        # Per-device RPM support cache: True = supported (or untested), False = rejected
        self._rpm_supported: dict[str, bool] = {}

        # Shared BACnetClient identification (for multi-entry reference counting)
        self._gw_key = (local_port, self._gateway_ip)

        # Enable route-aware addressing so the @ syntax works
        # Without this, BACpypes3 ignores the router IP in the address
        # and tries Who-Is-Router-To-Network (broadcast, won't cross subnets)
        from bacpypes3.settings import settings as _bacpypes_settings
        _bacpypes_settings["route_aware"] = True

    def _make_addr(self, device_address: str) -> Address:
        """Create a BACpypes3 Address.

        Numeric strings (e.g. '9600') → deviceInstance Address (correct).
        IP strings (e.g. '192.168.100.103') → IPv4Address (fallback).
        """
        try:
            return Address(int(device_address))
        except (ValueError, TypeError):
            return Address(device_address)

    @staticmethod
    def _derive_device_instance(local_ip: str, local_port: int) -> int:
        """Derive a stable, unique device instance from the local address.

        Uses SHA-256 (not Python's hash()) so the result is identical across
        every process restart — Python's built-in hash() is randomised by
        PYTHONHASHSEED and changes every time HA restarts.
        """
        seed = f"{local_ip}:{local_port}".encode()
        digest = hashlib.sha256(seed).digest()
        raw = int.from_bytes(digest[:4], "big")
        return 3900000 + (raw % 294303)  # 3900000–4194302

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _build_app_args(self) -> tuple[DeviceObject, IPv4Address]:
        """Prepare IPv4Address and device object for app construction."""
        if self._local_ip:
            local_addr = IPv4Address(f"{self._local_ip}:{self._local_port}")
        else:
            local_addr = IPv4Address(f"0.0.0.0:{self._local_port}")

        device_object = DeviceObject(
            objectIdentifier=("device", self._device_instance),
            objectName="HomeAssistant-BACnet",
            vendorIdentifier=0,
            maxApduLengthAccepted=1476,
            maxSegmentsAccepted=64,
            segmentationSupported="segmented-both",
        )
        return device_object, local_addr

    async def connect(self) -> None:
        """Create the BACpypes3 NormalApplication and bind to the network.

        This integration does NOT use BBMD / Foreign Device Registration.
        The BACnet gateway (e.g. 讯饶 Router1001-ARM-E) handles cross-subnet
        routing natively, so a NormalApplication suffices.
        """
        device_object, local_addr = self._build_app_args()

        _LOGGER.debug(
            "Creating BACnet application on %s (gateway=%s, target_device_id=%s)",
            local_addr,
            _mask_address(self._gateway_ip) if self._gateway_ip else "none",
            self._target_device_id,
        )

        self._app = NormalApplication(device_object, local_addr)
        _LOGGER.info(
            "BACnet client connected on %s (gateway=%s)",
            local_addr,
            _mask_address(self._gateway_ip) if self._gateway_ip else "direct",
        )

        # Manually add the BACnet router for the MS/TP network
        # This tells BACpypes3: "to reach Network 11, send via the gateway"
        # Without this, the stack tries Who-Is-Router-To-Network (broadcast)
        if self._gateway_ip:
            try:
                from bacpypes3.pdu import Address as _Addr

                router_addr = _Addr(
                    f"{self._gateway_ip}:{self._gateway_port or 47808}"
                )
                self._app.nsap.update_router_references(
                    snet=None, address=router_addr, dnets=[11]
                )
                _LOGGER.debug(
                    "Added router for network 11: %s",
                    router_addr,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Failed to add BACnet router: %s", exc)

        # Wait for the UDP transport to be ready.
        try:
            await self._wait_for_transport()
        except Exception:
            try:
                self._app.close()
            except Exception:  # noqa: BLE001
                pass
            self._app = None
            raise

        # Discover the target device via Who-Is so BACpypes3 learns its route
        if self._target_device_id:
            try:
                _LOGGER.info(
                    "Discovering target device %d via Who-Is ...",
                    self._target_device_id,
                )
                devices = await self.discover_devices(
                    timeout=5.0,
                    target_device_id=self._target_device_id,
                    target_address=f"{self._gateway_ip}:{self._gateway_port or 47808}",
                )
                if devices:
                    _LOGGER.info(
                        "Target device %d discovered at %s",
                        self._target_device_id,
                        devices[0].get("address", "?"),
                    )
                else:
                    _LOGGER.warning(
                        "Target device %d not found via Who-Is — "
                        "will try direct addressing",
                        self._target_device_id,
                    )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "Who-Is discovery for device %d failed: %s",
                    self._target_device_id,
                    exc,
                )

    async def _wait_for_transport(self, timeout: float = 5.0) -> None:
        """Await the UDP transport tasks so the socket is actually bound."""
        server = getattr(self._app, "normal", None)
        if server is None:
            _LOGGER.warning("Cannot locate IPv4DatagramServer — skipping transport check")
            return

        server = getattr(server, "server", None)
        if server is None:
            _LOGGER.warning("Cannot locate IPv4DatagramServer — skipping transport check")
            return

        tasks = getattr(server, "_transport_tasks", [])
        if tasks:
            _LOGGER.debug("Waiting up to %.0fs for UDP transport …", timeout)
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=timeout)
                server._transport_tasks = []
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "UDP socket failed to bind within %.0fs — port %d may "
                    "already be in use. Try a different 'Local port' (e.g. 47809).",
                    timeout,
                    self._local_port,
                )
                raise RuntimeError(
                    f"UDP port {self._local_port} could not be bound "
                    f"(already in use?). Choose a different local port."
                ) from None

        # Log the actual bound address
        transport = getattr(server, "local_transport", None)
        if transport is not None:
            sock = transport.get_extra_info("socket")
            if sock is not None:
                bound = sock.getsockname()
                _LOGGER.info(
                    "UDP transport ready — actually bound to %s:%s", bound[0], bound[1]
                )

    async def reconnect(self) -> None:
        """Tear down and re-establish the network connection.

        Safe to call even when already disconnected.
        """
        _LOGGER.info("Reconnecting BACnet client (gateway=%s)", self._gateway_ip)
        await self.disconnect()
        await self.connect()

    async def disconnect(self) -> None:
        """Shut down the BACpypes3 application and release the UDP socket."""
        for task in self._cov_tasks.values():
            task.cancel()
        self._cov_tasks.clear()

        if self._app is not None:
            try:
                self._app.close()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Exception during app close (ignored)")
            self._app = None
            _LOGGER.info("BACnet client disconnected")

    # ------------------------------------------------------------------
    # Device discovery - Who-Is / I-Am
    # ------------------------------------------------------------------

    async def discover_devices(
        self,
        timeout: float = 5.0,
        target_device_id: int | None = None,
        target_address: str = "",
    ) -> list[dict[str, Any]]:
        """Send a Who-Is and collect I-Am responses.

        If *target_device_id* is provided, a targeted Who-Is is sent.
        Otherwise a global broadcast is sent.  The BACnet gateway (e.g.
        讯饶 Router1001-ARM-E) forwards the broadcast across subnets
        automatically.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        devices: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        if target_device_id:
            _LOGGER.debug(
                "Sending targeted Who-Is for device %d (timeout=%.1fs)",
                target_device_id,
                timeout,
            )
        else:
            _LOGGER.debug("Sending global Who-Is broadcast (timeout=%.1fs)", timeout)

        try:
            who_is_kwargs: dict[str, Any] = {"timeout": timeout}
            if target_device_id:
                who_is_kwargs["low_limit"] = target_device_id
                who_is_kwargs["high_limit"] = target_device_id
            if target_address:
                who_is_kwargs["address"] = Address(target_address)

            who_is_futures = await self._app.who_is(**who_is_kwargs)

            for ia in who_is_futures:
                try:
                    device_info = await self._extract_device_info(ia)
                    if device_info and device_info.get("device_id") not in seen_ids:
                        devices.append(device_info)
                        seen_ids.add(device_info["device_id"])
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Skipping device from Who-Is: %s", ia, exc_info=True)

        except asyncio.TimeoutError:
            _LOGGER.warning("Who-Is timed out after %.1fs", timeout)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Who-Is discovery failed", exc_info=True)

        _LOGGER.info("Discovery complete: %d device(s) found", len(devices))
        return devices

    async def _extract_device_info(
        self, ia_apdu: Any
    ) -> dict[str, Any] | None:
        """Extract device info from an I-Am APDU."""
        try:
            device_id = getattr(ia_apdu, "iAmDeviceID", None)
            device_name = getattr(ia_apdu, "iAmObjectName", "Unknown")
            vendor_id = getattr(ia_apdu, "iAmVendorID", 0)
            max_apdu = getattr(ia_apdu, "iAmMaxAPDU", 0)
            segmentation = getattr(ia_apdu, "iAmSegmentation", "")

            # Resolve the source address of the I-Am
            source = getattr(ia_apdu, "source", None)
            if source is None:
                source = getattr(ia_apdu, "npdu", None)
            if source is None:
                source = getattr(ia_apdu, "dst", None)
            address_str = str(source) if source else ""

            return {
                "device_id": int(device_id) if device_id else 0,
                "device_name": str(device_name),
                "address": address_str,
                "vendor_id": int(vendor_id) if vendor_id else 0,
                "max_apdu": int(max_apdu) if max_apdu else 0,
                "segmentation_supported": str(segmentation),
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Read object list
    # ------------------------------------------------------------------

    async def read_object_list(
        self,
        device_address: str,
        device_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read the list of BACnet objects from a device.

        Returns a list of dicts with keys: object_type, instance,
        object_name, description, units, status_flags, commandable.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = self._make_addr(device_address)
        _LOGGER.debug("Reading object list from %s (device_id=%s)", _mask_address(device_address), device_id)

        objects: list[dict[str, Any]] = []

        try:
            # Read list of objects on the device
            result = await asyncio.wait_for(
                self._app.read_property(
                    addr,
                    ("device", device_id) if device_id else ("device", 0),
                    "objectList",
                ),
                timeout=30.0,
            )

            if result is None:
                return objects

            for obj_ref in result:
                try:
                    obj_type_str, instance = obj_ref
                    obj_type_int = self._object_type_str_to_int(obj_type_str)
                    if obj_type_int is None:
                        continue

                    obj_id = ObjectIdentifier((obj_type_str, instance))

                    # Read objectName and description in parallel
                    name_future = self._app.read_property(addr, obj_id, "objectName")
                    desc_future = self._app.read_property(addr, obj_id, "description")
                    present_future = self._app.read_property(addr, obj_id, "presentValue")

                    try:
                        name = await asyncio.wait_for(name_future, timeout=5.0)
                    except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):
                        name = ""

                    try:
                        description = await asyncio.wait_for(desc_future, timeout=5.0)
                    except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):
                        description = ""

                    try:
                        present_value = await asyncio.wait_for(present_future, timeout=5.0)
                    except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):
                        present_value = None

                    # Determine commandability by attempting to read
                    # priorityArray — if it exists, the object is commandable.
                    commandable = False
                    try:
                        pa_result = await asyncio.wait_for(
                            self._app.read_property(addr, obj_id, "priorityArray"),
                            timeout=3.0,
                        )
                        commandable = pa_result is not None
                    except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):
                        # Also check if object is in inherently commandable types
                        commandable = obj_type_int in COMMANDABLE_TYPES

                    obj_dict: dict[str, Any] = {
                        "object_type": obj_type_int,
                        "instance": int(instance),
                        "object_name": str(name) if name else "",
                        "description": str(description) if description else "",
                        "presentValue": self._coerce_value(present_value),
                        "commandable": commandable,
                    }

                    # Try to read units for analog types
                    if obj_type_int in {
                        OBJECT_TYPE_ANALOG_INPUT,
                        OBJECT_TYPE_ANALOG_OUTPUT,
                        OBJECT_TYPE_ANALOG_VALUE,
                    }:
                        try:
                            units = await asyncio.wait_for(
                                self._app.read_property(addr, obj_id, "units"),
                                timeout=3.0,
                            )
                            if units is not None:
                                obj_dict["units"] = str(units)
                        except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception):
                            pass

                    # Determine mode (read vs commandable) based on object type
                    if commandable or obj_type_int in COMMANDABLE_TYPES:
                        obj_dict["mode"] = "commandable"
                    else:
                        obj_dict["mode"] = "read"

                    objects.append(obj_dict)

                except Exception:  # noqa: BLE001
                    _LOGGER.debug("Error reading object %s, skipping", obj_ref)

        except (asyncio.TimeoutError, ErrorRejectAbortNack, Exception) as exc:
            _LOGGER.warning("Failed to read object list from %s: %s", _mask_address(device_address), exc)

        _LOGGER.info("Read %d objects from %s", len(objects), _mask_address(device_address))
        return objects

    # ------------------------------------------------------------------
    # Read property
    # ------------------------------------------------------------------

    async def read_property(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        property_name: str,
    ) -> Any:
        """Read a single property from a BACnet object."""
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = self._make_addr(device_address)
        type_str = self._int_to_object_type_str(object_type)
        oid = ObjectIdentifier((type_str, instance))

        try:
            result = await asyncio.wait_for(
                self._app.read_property(addr, oid, property_name),
                timeout=10.0,
            )
            return self._coerce_value(result)
        except (asyncio.TimeoutError, ErrorRejectAbortNack) as exc:
            _LOGGER.debug(
                "ReadProperty timeout/reject for %s:%d.%s: %s",
                type_str,
                instance,
                property_name,
                exc,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug(
                "ReadProperty error for %s:%d.%s: %s",
                type_str,
                instance,
                property_name,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Batch poll - ReadPropertyMultiple
    # ------------------------------------------------------------------

    async def poll_objects(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Read properties for a batch of objects in one network round-trip.

        Attempts ReadPropertyMultiple (RPM) first.  Falls back to
        per-object reads if the device rejects RPM.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        if property_names is None:
            property_names = ["presentValue", "statusFlags"]

        if self._rpm_supported.get(device_address, True):
            result = await self._try_rpm_poll(device_address, objects, property_names)
            if result is not None:
                return result

        return await self._fallback_poll(device_address, objects, property_names)

    async def _try_rpm_poll(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        """Attempt one ReadPropertyMultiple request for all objects."""
        rpm_props = [self._CAMEL_TO_HYPHEN.get(p, p) for p in property_names]

        param_list: list = []
        for obj in objects:
            type_str = self._INT_TO_TYPE_STR.get(obj["object_type"])
            if type_str is None:
                continue
            param_list.append(f"{type_str},{obj['instance']}")
            param_list.append(rpm_props)

        if not param_list:
            return {}

        addr = self._make_addr(device_address)
        try:
            results = await asyncio.wait_for(
                self._app.read_property_multiple(addr, param_list),
                timeout=30.0,
            )

            data: dict[str, dict[str, Any]] = {}
            for obj_id, prop_id, _arr_idx, value in results:
                obj_type_int = self._object_type_str_to_int(str(obj_id[0]))
                instance = int(obj_id[1])
                if obj_type_int is None:
                    continue

                obj_key = f"{obj_type_int}:{instance}"
                prop_camel = self._HYPHEN_TO_CAMEL.get(str(prop_id), str(prop_id))
                coerced = None if isinstance(value, BaseException) else self._coerce_value(value)
                data.setdefault(obj_key, {})[prop_camel] = coerced

            _LOGGER.debug("RPM poll: %d objects from %s", len(data), _mask_address(device_address))
            return data

        except asyncio.TimeoutError:
            _LOGGER.debug("RPM poll timed out for %s", _mask_address(device_address))
            return None
        except ErrorRejectAbortNack as exc:
            _LOGGER.info(
                "Device %s rejected ReadPropertyMultiple (%s) — "
                "switching to individual reads",
                _mask_address(device_address),
                exc,
            )
            self._rpm_supported[device_address] = False
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("RPM poll error for %s: %s", _mask_address(device_address), exc)
            return None

    async def _fallback_poll(
        self,
        device_address: str,
        objects: list[dict[str, Any]],
        property_names: list[str],
    ) -> dict[str, dict[str, Any]]:
        """Read each object's properties individually."""
        data: dict[str, dict[str, Any]] = {}
        for obj in objects:
            obj_key = f"{obj['object_type']}:{obj['instance']}"
            obj_data: dict[str, Any] = {}
            for prop in property_names:
                value = await self.read_property(
                    device_address, obj["object_type"], obj["instance"], prop
                )
                obj_data[prop] = self._coerce_value(value)
            data[obj_key] = obj_data
        return data

    # ------------------------------------------------------------------
    # Property write with Priority Array support
    # ------------------------------------------------------------------

    async def write_property(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        property_name: str,
        value: Any,
        priority: int = DEFAULT_WRITE_PRIORITY,
    ) -> bool:
        """Write a value to a BACnet property with proper Priority Array handling.

        For commandable objects, writes go through the Priority Array at
        the specified priority level (default 16 = lowest).  To relinquish
        a commanded value, write None (Null) at the previously written
        priority level.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = self._make_addr(device_address)
        type_str = self._int_to_object_type_str(object_type)
        oid = ObjectIdentifier((type_str, instance))

        is_commandable = object_type in (COMMANDABLE_TYPES | POTENTIALLY_WRITABLE_TYPES)

        if value is None:
            bacnet_value = Null()
        else:
            bacnet_value = self._python_to_bacnet_value(value, object_type)

        try:
            _LOGGER.debug(
                "Writing %s to %s:%d.%s (priority=%s, commandable=%s)",
                value,
                type_str,
                instance,
                property_name,
                priority if is_commandable else "N/A",
                is_commandable,
            )

            if is_commandable:
                result = await self._app.write_property(
                    addr, oid, property_name, bacnet_value, priority=priority
                )
            else:
                result = await self._app.write_property(
                    addr, oid, property_name, bacnet_value
                )

            if isinstance(result, ErrorRejectAbortNack):
                _LOGGER.error(
                    "Write rejected by device for %s:%d.%s = %s: %s",
                    type_str,
                    instance,
                    property_name,
                    value,
                    result,
                )
                return False

            _LOGGER.debug("Write successful")
            return True

        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.error(
                "Write failed for %s:%d.%s = %s: %s",
                type_str,
                instance,
                property_name,
                value,
                exc,
            )
            return False

    async def relinquish(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        priority: int = DEFAULT_WRITE_PRIORITY,
    ) -> bool:
        """Send a Null write (relinquish) to release a previously commanded value."""
        return await self.write_property(
            device_address=device_address,
            object_type=object_type,
            instance=instance,
            property_name="presentValue",
            value=None,
            priority=priority,
        )

    # ------------------------------------------------------------------
    # COV (Change of Value) subscriptions
    # ------------------------------------------------------------------

    async def subscribe_cov(
        self,
        device_address: str,
        object_type: int,
        instance: int,
        callback: Callable[[str, dict[str, Any]], None],
        lifetime: int = 300,
    ) -> str | None:
        """Subscribe to Change of Value notifications for one object.

        Returns a subscription key string on success, or None on failure.
        """
        if self._app is None:
            raise RuntimeError("Client not connected")

        addr = self._make_addr(device_address)
        type_str = self._int_to_object_type_str(object_type)
        oid = ObjectIdentifier((type_str, instance))
        sub_key = f"{device_address}:{object_type}:{instance}"
        obj_key = f"{object_type}:{instance}"

        ready_event: asyncio.Event = asyncio.Event()

        try:
            _LOGGER.debug(
                "Subscribing to COV for %s:%d at %s", type_str, instance, device_address
            )

            task = asyncio.create_task(
                self._cov_reader_task(addr, oid, lifetime, sub_key, obj_key, callback, ready_event)
            )
            self._cov_tasks[sub_key] = task

            try:
                await asyncio.wait_for(ready_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                _LOGGER.warning(
                    "COV subscription timed out for %s:%d at %s. Falling back to polling.",
                    type_str,
                    instance,
                    device_address,
                )
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                self._cov_tasks.pop(sub_key, None)
                return None

            if task.done():
                _LOGGER.warning(
                    "COV subscription rejected for %s:%d at %s. Falling back to polling.",
                    type_str,
                    instance,
                    device_address,
                )
                self._cov_tasks.pop(sub_key, None)
                return None

            _LOGGER.info("COV subscription active for %s:%d", type_str, instance)
            return sub_key

        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            _LOGGER.warning(
                "COV subscription failed for %s:%d at %s: %s. Falling back to polling.",
                type_str,
                instance,
                device_address,
                exc,
            )
            self._cov_tasks.pop(sub_key, None)
            return None

    async def _cov_reader_task(
        self,
        addr: Address,
        oid: ObjectIdentifier,
        lifetime: int,
        sub_key: str,
        obj_key: str,
        callback: Callable[[str, dict[str, Any]], None],
        ready_event: asyncio.Event,
    ) -> None:
        """Long-running task that reads from a COV subscription queue."""
        try:
            scm = self._app.change_of_value(addr, oid, lifetime=lifetime)
            async with scm:
                ready_event.set()
                while True:
                    prop_id, value = await scm.get_value()
                    changes: dict[str, Any] = {str(prop_id): self._coerce_value(value)}

                    await asyncio.sleep(0)
                    try:
                        while True:
                            extra_id, extra_val = await asyncio.wait_for(
                                scm.get_value(), timeout=0.05
                            )
                            changes[str(extra_id)] = self._coerce_value(extra_val)
                    except asyncio.TimeoutError:
                        pass

                    _LOGGER.debug("COV notification %s: %s", sub_key, changes)
                    try:
                        callback(obj_key, changes)
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Error in COV callback for %s", sub_key)

        except asyncio.CancelledError:
            _LOGGER.debug("COV task cancelled for %s", sub_key)
        except (ErrorRejectAbortNack, Exception) as exc:  # noqa: BLE001
            if not ready_event.is_set():
                ready_event.set()
            self._cov_tasks.pop(sub_key, None)

            exc_str = str(exc).lower()
            _KNOWN_COV_REJECTIONS = (
                "optional-functionality-not-supported",
                "object-unknown",
                "no-space-to-add-list-element",
                "inconsistent-parameters",
            )
            if any(r in exc_str for r in _KNOWN_COV_REJECTIONS):
                _LOGGER.debug(
                    "COV not supported by device for %s (falling back to polling): %s",
                    sub_key,
                    exc,
                )
            else:
                _LOGGER.warning(
                    "COV task ended unexpectedly for %s", sub_key, exc_info=True
                )

    async def unsubscribe_cov(self, sub_key: str) -> None:
        """Cancel a COV subscription by cancelling its reader task."""
        task = self._cov_tasks.pop(sub_key, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            _LOGGER.debug("COV unsubscribed: %s", sub_key)

    async def unsubscribe_all_cov(self) -> None:
        """Cancel all COV subscriptions."""
        for sub_key in list(self._cov_tasks):
            await self.unsubscribe_cov(sub_key)

    # ------------------------------------------------------------------
    # Value conversion helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Convert a BACpypes3 value to a plain Python type for JSON storage."""
        if value is None:
            return None
        if isinstance(value, Real):
            return float(value)
        if isinstance(value, Unsigned):
            return int(value)
        if isinstance(value, CharacterString):
            return str(value)
        if isinstance(value, list):
            return [bool(x) for x in value]
        if isinstance(value, bool):
            return bool(value)
        if isinstance(value, int):
            return int(value)
        if isinstance(value, float):
            return float(value)
        if isinstance(value, str):
            return str(value)
        return str(value)

    @staticmethod
    def _python_to_bacnet_value(value: Any, object_type: int) -> Any:
        """Convert a Python value to the appropriate BACpypes3 type."""
        if value is None:
            return Null()

        if object_type in {
            OBJECT_TYPE_ANALOG_INPUT,
            OBJECT_TYPE_ANALOG_OUTPUT,
            OBJECT_TYPE_ANALOG_VALUE,
        }:
            return Real(float(value))

        if object_type in {
            OBJECT_TYPE_BINARY_INPUT,
            OBJECT_TYPE_BINARY_OUTPUT,
            OBJECT_TYPE_BINARY_VALUE,
        }:
            return Enumerated(int(bool(value)))

        if object_type in {
            OBJECT_TYPE_MULTI_STATE_INPUT,
            OBJECT_TYPE_MULTI_STATE_OUTPUT,
            OBJECT_TYPE_MULTI_STATE_VALUE,
        }:
            return Unsigned(int(value))

        return Real(float(value))

    # ------------------------------------------------------------------
    # Object type string - integer mapping
    # ------------------------------------------------------------------

    _TYPE_STR_TO_INT: dict[str, int] = {
        "analogInput": OBJECT_TYPE_ANALOG_INPUT,
        "analogOutput": OBJECT_TYPE_ANALOG_OUTPUT,
        "analogValue": OBJECT_TYPE_ANALOG_VALUE,
        "binaryInput": OBJECT_TYPE_BINARY_INPUT,
        "binaryOutput": OBJECT_TYPE_BINARY_OUTPUT,
        "binaryValue": OBJECT_TYPE_BINARY_VALUE,
        "multiStateInput": OBJECT_TYPE_MULTI_STATE_INPUT,
        "multiStateOutput": OBJECT_TYPE_MULTI_STATE_OUTPUT,
        "multiStateValue": OBJECT_TYPE_MULTI_STATE_VALUE,
        "analog-input": OBJECT_TYPE_ANALOG_INPUT,
        "analog-output": OBJECT_TYPE_ANALOG_OUTPUT,
        "analog-value": OBJECT_TYPE_ANALOG_VALUE,
        "binary-input": OBJECT_TYPE_BINARY_INPUT,
        "binary-output": OBJECT_TYPE_BINARY_OUTPUT,
        "binary-value": OBJECT_TYPE_BINARY_VALUE,
        "multi-state-input": OBJECT_TYPE_MULTI_STATE_INPUT,
        "multi-state-output": OBJECT_TYPE_MULTI_STATE_OUTPUT,
        "multi-state-value": OBJECT_TYPE_MULTI_STATE_VALUE,
    }

    _CAMEL_TO_HYPHEN: dict[str, str] = {
        "presentValue": "present-value",
        "statusFlags": "status-flags",
        "outOfService": "out-of-service",
        "priorityArray": "priority-array",
        "covIncrement": "cov-increment",
    }
    _HYPHEN_TO_CAMEL: dict[str, str] = {v: k for k, v in _CAMEL_TO_HYPHEN.items()}

    _INT_TO_TYPE_STR: dict[int, str] = {
        OBJECT_TYPE_ANALOG_INPUT: "analog-input",
        OBJECT_TYPE_ANALOG_OUTPUT: "analog-output",
        OBJECT_TYPE_ANALOG_VALUE: "analog-value",
        OBJECT_TYPE_BINARY_INPUT: "binary-input",
        OBJECT_TYPE_BINARY_OUTPUT: "binary-output",
        OBJECT_TYPE_BINARY_VALUE: "binary-value",
        OBJECT_TYPE_MULTI_STATE_INPUT: "multi-state-input",
        OBJECT_TYPE_MULTI_STATE_OUTPUT: "multi-state-output",
        OBJECT_TYPE_MULTI_STATE_VALUE: "multi-state-value",
    }

    @classmethod
    def _object_type_str_to_int(cls, type_str: str | int) -> int | None:
        """Convert BACpypes3 object type string to integer ID."""
        if isinstance(type_str, int):
            return int(type_str)
        s = str(type_str)
        result = cls._TYPE_STR_TO_INT.get(s)
        if result is not None:
            return result
        s_lower = s.lower()
        for key, val in cls._TYPE_STR_TO_INT.items():
            if key.lower() == s_lower:
                return val
        return None

    @classmethod
    def _int_to_object_type_str(cls, type_int: int) -> str:
        """Convert integer object type to BACpypes3 type string."""
        return cls._INT_TO_TYPE_STR.get(type_int, f"type-{type_int}")
