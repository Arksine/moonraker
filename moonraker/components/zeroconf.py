# Zeroconf registration implementation for Moonraker
#
# Copyright (C) 2021  Clifford Roche <clifford.roche@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import socket
import asyncio
import logging
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
        self.service_info: AsyncServiceInfo | None = None
        self.runner = AsyncRunner(IPVersion.All)

    async def component_init(self) -> None:
        logging.info("Starting Zeroconf services")
        hi = self.server.get_host_info()
        addresses = [socket.inet_aton(hi["address"])]
        if hi["address"] == "0.0.0.0":
            addresses = self._get_ip_addresses()
        self.service_info = AsyncServiceInfo(
            "_moonraker._tcp.local.",
            f"Moonraker Instance on {hi['hostname']}._moonraker._tcp.local.",
            addresses=addresses,
            port=hi["port"],
            properties={"path": "/"},
            server=f"{hi['hostname']}.local.",
        )
        self.server.register_event_handler(
            "machine:net_state_changed", self._update_service
        )
        assert self.service_info
        await self.runner.register_services([self.service_info])

    async def close(self) -> None:
        if self.service_info:
            await self.runner.unregister_services([self.service_info])

    async def _update_service(self) -> None:
        hi = self.server.get_host_info()
        if hi["address"] == "0.0.0.0":
            assert self.service_info
            self.service_info.addresses = self._get_ip_addresses()
            await self.runner.update_services([self.service_info])

    def _get_ip_addresses(self) -> List[bytes]:
        machine: Machine = self.server.lookup_component("machine")
        network = machine.get_system_info()["network"]
        return [
            socket.inet_aton(x) for x in self._extract_ip_addresses(network)
        ]

    def _extract_ip_addresses(self, network: Dict[str, Any]) -> Iterator[str]:
        for ifname, ifinfo in network.items():
            for addr_info in ifinfo["ip_addresses"]:
                if addr_info["is_link_local"]:
                    continue
                yield addr_info["address"]


def load_component(config: ConfigHelper) -> ZeroconfRegistrar:
    return ZeroconfRegistrar(config)
