# Zeroconf registration implementation for Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import socket
import asyncio

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from typing import TYPE_CHECKING, List, Optional
if TYPE_CHECKING:
    from confighelper import ConfigHelper

class AsyncRunner:
    def __init__(self, ip_version: IPVersion) -> None:
        self.ip_version = ip_version
        self.aiozc: Optional[AsyncZeroconf] = None

    async def register_services(self, infos: List[AsyncServiceInfo]) -> None:
        self.aiozc = AsyncZeroconf(ip_version=self.ip_version)
        tasks = [self.aiozc.async_register_service(info) for info in infos]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)

    async def unregister_services(self, infos: List[AsyncServiceInfo]) -> None:
        assert self.aiozc is not None
        tasks = [self.aiozc.async_unregister_service(info) for info in infos]
        background_tasks = await asyncio.gather(*tasks)
        await asyncio.gather(*background_tasks)
        await self.aiozc.async_close()

class ZeroconfRegistrar():
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()

        hi = self.server.get_host_info()
        host_name = hi['hostname']
        addresses = [socket.gethostbyname(host_name)]
        self.service_info = AsyncServiceInfo(
            "_moonraker._tcp.local.",
            f"{host_name}._moonraker._tcp.local.",
            addresses=addresses,
            port=hi['port'],
            properties={'path': '/'},
            server=f"{host_name}.local."
        )
        self.runner = AsyncRunner(IPVersion.All)

    async def component_init(self):
        await self.runner.register_services([self.service_info])

    async def close(self) -> None:
        await self.runner.unregister_services([self.service_info])

def load_component(config: ConfigHelper) -> ZeroconfRegistrar:
    return ZeroconfRegistrar(config)
