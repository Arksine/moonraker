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
    from confighelper import ConfigHelper
    from .machine import Machine


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
        addr: str = hi["address"]
        if addr.lower() == "all":
            addr = "::"
        host_ip = ipaddress.ip_address(addr)
        fam = socket.AF_INET6 if host_ip.version == 6 else socket.AF_INET
        addresses: List[bytes] = [(socket.inet_pton(fam, addr))]
        self.bound_all = addr in ["0.0.0.0", "::"]
        self.service_info = self._build_service_info(addresses)
        if self.bound_all:
            self.server.register_event_handler(
                "machine:net_state_changed", self._update_service)

    async def component_init(self) -> None:
        logging.info("Starting Zeroconf services")
        if self.bound_all:
            machine: Machine = self.server.lookup_component("machine")
            network = machine.get_system_info()["network"]
            addresses = [x for x in self._extract_ip_addresses(network)]
            self.service_info = self._build_service_info(addresses)
        await self.runner.register_services([self.service_info])

    async def close(self) -> None:
        await self.runner.unregister_services([self.service_info])

    async def _update_service(self, network: Dict[str, Any]) -> None:
        if self.bound_all:
            addresses = [x for x in self._extract_ip_addresses(network)]
            self.service_info = self._build_service_info(addresses)
            await self.runner.update_services([self.service_info])

    def _build_service_info(self,
                            addresses: Optional[List[bytes]] = None
                            ) -> AsyncServiceInfo:
        hi = self.server.get_host_info()
        return AsyncServiceInfo(
            "_moonraker._tcp.local.",
            f"Moonraker Instance on {hi['hostname']}._moonraker._tcp.local.",
            addresses=addresses,
            port=hi["port"],
            properties={"path": "/"},
            server=f"{hi['hostname']}.local.",
        )

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
