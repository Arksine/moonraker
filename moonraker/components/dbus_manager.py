# DBus Connection Management
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import os
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
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper

class DbusManager:
    Variant = dbus_next.Variant
    DbusError = dbus_next.errors.DBusError
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.bus: Optional[MessageBus] = None
        self.polkit: Optional[ProxyInterface] = None
        self.warned: bool = False
        proc_data = pathlib.Path(f"/proc/self/stat").read_text()
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
        except Exception:
            logging.info("Unable to Connect to D-Bus")
            return
        # Make sure that all required actions are register
        try:
            self.polkit = await self.get_interface(
                "org.freedesktop.PolicyKit1",
                "/org/freedesktop/PolicyKit1/Authority",
                "org.freedesktop.PolicyKit1.Authority")
        except self.DbusError:
            self.server.add_warning(
                "Unable to find DBus PolicyKit Interface")

    async def check_permission(self,
                               action: str,
                               err_msg: str = ""
                               ) -> bool:
        if self.polkit is None:
            return False
        try:
            ret = await self.polkit.call_check_authorization(  # type: ignore
                self.polkit_subject, action, {}, 0, "")
        except Exception as e:
            self.server.add_warning(
                f"Error checking authorization for action [{action}]: {e}"
                ", This may indicate that PolicyKit is not installed, "
                f"{err_msg}")
            return False
        if not ret[0]:
            if not self.warned:
                self.server.add_warning(
                    "Missing PolicyKit permisions detected. See the "
                    "PolicyKit Permissions section of the install "
                    "documentation at https://moonraker.readthedocs.io/ "
                    "for details.")
                self.warned = True
            self.server.add_warning(
                "Moonraker not authorized for PolicyKit action: "
                f"[{action}], {err_msg}")
        return ret[0]

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
