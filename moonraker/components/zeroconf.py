# Zeroconf registration implementation for Moonraker
#
# Copyright (C) 2021  Clifford Roche <clifford.roche@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import socket
import asyncio
import logging
import ipaddress
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..app import MoonrakerApp
    from .machine import Machine

ZC_SERVICE_TYPE = "_moonraker._tcp.local."

class AsyncRunner:
    def __init__(self, ip_version: IPVersion) -> None:
        self.ip_version = ip_version
        self.aiozc: Optional[AsyncZeroconf] = None

    async def register_services(self, infos: List[AsyncServiceInfo]) -> None:
        self.aiozc = AsyncZeroconf(ip_version=self.ip_version)
        tasks = [
            self.aiozc.async_register_service(info, allow_name_change=True)
            for info in infos
        ]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)

    async def unregister_services(self, infos: List[AsyncServiceInfo]) -> None:
        assert self.aiozc is not None
        tasks = [self.aiozc.async_unregister_service(info) for info in infos]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)
        await self.aiozc.async_close()

    async def update_services(self, infos: List[AsyncServiceInfo]) -> None:
        assert self.aiozc is not None
        tasks = [self.aiozc.async_update_service(info) for info in infos]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)


class ZeroconfRegistrar:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.runner = AsyncRunner(IPVersion.All)
        hi = self.server.get_host_info()
        self.mdns_name = config.get("mdns_hostname", hi["hostname"])
        addr: str = hi["address"]
        if addr.lower() == "all":
            addr = "::"
        self.cfg_addr = addr
        self.bound_all = addr in ["0.0.0.0", "::"]
        if self.bound_all:
            self.server.register_event_handler(
                "machine:net_state_changed", self._update_service)

    async def component_init(self) -> None:
        logging.info("Starting Zeroconf services")
        app: MoonrakerApp = self.server.lookup_component("application")
        machine: Machine = self.server.lookup_component("machine")
        app_args = self.server.get_app_args()
        instance_uuid: str = app_args["instance_uuid"]
        if (
            machine.get_provider_type().startswith("systemd") and
            "unit_name" in machine.get_moonraker_service_info()
        ):
            # Use the name of the systemd service unit to identify service
            instance_name = machine.unit_name.capitalize()
        else:
            # Use the UUID.  First 8 hex digits should be unique enough
            instance_name = f"Moonraker-{instance_uuid[:8]}"
        hi = self.server.get_host_info()
        host = self.mdns_name
        zc_service_props = {
            "uuid": instance_uuid,
            "https_port": hi["ssl_port"] if app.https_enabled() else "",
            "version": app_args["software_version"]
        }
        if self.bound_all:
            if not host:
                host = machine.public_ip
            network = machine.get_system_info()["network"]
            addresses: List[bytes] = [x for x in self._extract_ip_addresses(network)]
        else:
            if not host:
                host = self.cfg_addr
            host_addr = ipaddress.ip_address(self.cfg_addr)
            fam = socket.AF_INET6 if host_addr.version == 6 else socket.AF_INET
            addresses = [(socket.inet_pton(fam, str(self.cfg_addr)))]
        zc_service_name = f"{instance_name} @ {host}.{ZC_SERVICE_TYPE}"
        server_name = self.mdns_name or instance_name.lower()
        self.service_info = AsyncServiceInfo(
            ZC_SERVICE_TYPE,
            zc_service_name,
            addresses=addresses,
            port=hi["port"],
            properties=zc_service_props,
            server=f"{server_name}.local.",
        )
        await self.runner.register_services([self.service_info])

    async def close(self) -> None:
        await self.runner.unregister_services([self.service_info])

    async def _update_service(self, network: Dict[str, Any]) -> None:
        if self.bound_all:
            addresses = [x for x in self._extract_ip_addresses(network)]
            self.service_info.addresses = addresses
            await self.runner.update_services([self.service_info])

    def _extract_ip_addresses(self, network: Dict[str, Any]) -> Iterator[bytes]:
        for ifname, ifinfo in network.items():
            for addr_info in ifinfo["ip_addresses"]:
                if addr_info["is_link_local"]:
                    continue
                is_ipv6 = addr_info['family'] == "ipv6"
                family = socket.AF_INET6 if is_ipv6 else socket.AF_INET
                yield socket.inet_pton(family, addr_info["address"])


def load_component(config: ConfigHelper) -> ZeroconfRegistrar:
    return ZeroconfRegistrar(config)
