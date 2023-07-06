# DBus Connection Management
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import os
import asyncio
import pathlib
import logging
import dbus_next
from dbus_next.aio import MessageBus, ProxyInterface
from dbus_next.constants import BusType

# Annotation imports
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    Any,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

STAT_PATH = "/proc/self/stat"
DOC_URL = (
    "https://moonraker.readthedocs.io/en/latest/"
    "installation/#policykit-permissions"
)

class DbusManager:
    Variant = dbus_next.Variant
    DbusError = dbus_next.errors.DBusError
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.bus: Optional[MessageBus] = None
        self.polkit: Optional[ProxyInterface] = None
        self.warned: bool = False
        st_path = pathlib.Path(STAT_PATH)
        self.polkit_subject: List[Any] = []
        if not st_path.is_file():
            return
        proc_data = st_path.read_text()
        start_clk_ticks = int(proc_data.split()[21])
        self.polkit_subject = [
            "unix-process",
            {
                "pid": dbus_next.Variant("u", os.getpid()),
                "start-time": dbus_next.Variant("t", start_clk_ticks)
            }
        ]

    def is_connected(self) -> bool:
        return self.bus is not None and self.bus.connected

    async def component_init(self) -> None:
        try:
            self.bus = MessageBus(bus_type=BusType.SYSTEM)
            await self.bus.connect()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.info("Unable to Connect to D-Bus")
            return
        # Make sure that all required actions are register
        try:
            self.polkit = await self.get_interface(
                "org.freedesktop.PolicyKit1",
                "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if self.server.is_debug_enabled():
                logging.exception("Failed to get PolKit interface")
            else:
                logging.info(f"Failed to get PolKit interface: {e}")
            self.polkit = None

    async def check_permission(self,
                               action: str,
                               err_msg: str = ""
                               ) -> bool:
        if self.polkit is None:
            self.server.add_warning(
                "Unable to find DBus PolKit Interface, this suggests PolKit "
                "is not installed on your OS.",
                "dbus_polkit"
            )
            return False
        try:
            ret = await self.polkit.call_check_authorization(  # type: ignore
                self.polkit_subject, action, {}, 0, "")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._check_warned()
            self.server.add_warning(
                f"Error checking authorization for action [{action}]: {e}. "
                "This suggests that a dependency is not installed or "
                f"up to date. {err_msg}.")
            return False
        if not ret[0]:
            self._check_warned()
            self.server.add_warning(
                "Moonraker not authorized for PolicyKit action: "
                f"[{action}], {err_msg}")
        return ret[0]

    def _check_warned(self):
        if not self.warned:
            self.server.add_warning(
                f"PolKit warnings detected. See {DOC_URL} for instructions "
                "on how to resolve.")
            self.warned = True

    async def get_interface(self,
                            bus_name: str,
                            bus_path: str,
                            interface_name: str
                            ) -> ProxyInterface:
        ret = await self.get_interfaces(bus_name, bus_path,
                                        [interface_name])
        return ret[0]

    async def get_interfaces(self,
                             bus_name: str,
                             bus_path: str,
                             interface_names: List[str]
                             ) -> List[ProxyInterface]:
        if self.bus is None:
            raise self.server.error("Bus not avaialable")
        interfaces: List[ProxyInterface] = []
        introspection = await self.bus.introspect(bus_name, bus_path)
        proxy_obj = self.bus.get_proxy_object(bus_name, bus_path,
                                              introspection)
        for ifname in interface_names:
            intf = proxy_obj.get_interface(ifname)
            interfaces.append(intf)
        return interfaces

    async def close(self):
        if self.bus is not None and self.bus.connected:
            self.bus.disconnect()
            await self.bus.wait_for_disconnect()


def load_component(config: ConfigHelper) -> DbusManager:
    return DbusManager(config)
