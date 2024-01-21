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
import random
import uuid
from itertools import cycle
from email.utils import formatdate
from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf
from ..common import RequestType, TransportType

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .application import MoonrakerApp
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
        hi = self.server.get_host_info()
        self.mdns_name = config.get("mdns_hostname", hi["hostname"])
        addr: str = hi["address"]
        self.ip_version = IPVersion.All
        if addr.lower() == "all":
            addr = "::"
        else:
            addr_obj = ipaddress.ip_address(addr)
            self.ip_version = (
                IPVersion.V4Only if addr_obj.version == 4 else IPVersion.V6Only
            )
        self.runner = AsyncRunner(self.ip_version)
        self.cfg_addr = addr
        self.bound_all = addr in ["0.0.0.0", "::"]
        if self.bound_all:
            self.server.register_event_handler(
                "machine:net_state_changed", self._update_service)
        self.ssdp_server: Optional[SSDPServer] = None
        if config.getboolean("enable_ssdp", False):
            self.ssdp_server = SSDPServer(config)

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
            "version": app_args["software_version"],
            "route_prefix": app.route_prefix
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
            addresses = [host_addr.packed]
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
        if self.ssdp_server is not None:
            addr = self.cfg_addr if not self.bound_all else machine.public_ip
            if not addr:
                addr = f"{self.mdns_name}.local"
            name = f"{instance_name} ({host})"
            if len(name) > 64:
                name = instance_name
            await self.ssdp_server.start()
            self.ssdp_server.register_service(name, addr, hi["port"])

    async def close(self) -> None:
        await self.runner.unregister_services([self.service_info])
        if self.ssdp_server is not None:
            await self.ssdp_server.stop()

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
                addr_obj = ipaddress.ip_address(addr_info["address"])
                ver = addr_obj.version
                if (
                    (self.ip_version == IPVersion.V4Only and ver == 6) or
                    (self.ip_version == IPVersion.V6Only and ver == 4)
                ):
                    continue
                yield addr_obj.packed


SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_SERVER_ID = "Moonraker SSDP/UPNP Server"
SSDP_MAX_AGE = 1800
SSDP_DEVICE_TYPE = "urn:arksine.github.io:device:Moonraker:1"
SSDP_DEVICE_XML = """
<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0" configId="{config_id}">
    <specVersion>
        <major>2</major>
        <minor>0</minor>
    </specVersion>
    <device>
        <deviceType>{device_type}</deviceType>
        <friendlyName>{friendly_name}</friendlyName>
        <manufacturer>Arksine</manufacturer>
        <manufacturerURL>https://github.com/Arksine/moonraker</manufacturerURL>
        <modelDescription>API Server for Klipper</modelDescription>
        <modelName>Moonraker</modelName>
        <modelNumber>{model_number}</modelNumber>
        <modelURL>https://github.com/Arksine/moonraker</modelURL>
        <serialNumber>{serial_number}</serialNumber>
        <UDN>uuid:{device_uuid}</UDN>
        <presentationURL>{presentation_url}</presentationURL>
    </device>
</root>
""".strip()

class SSDPServer(asyncio.protocols.DatagramProtocol):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.unique_id = uuid.UUID(self.server.get_app_args()["instance_uuid"])
        self.name: str = "Moonraker"
        self.base_url: str = ""
        self.response_headers: List[str] = []
        self.registered: bool = False
        self.running: bool = False
        self.close_fut: Optional[asyncio.Future] = None
        self.response_handle: Optional[asyncio.TimerHandle] = None
        eventloop = self.server.get_event_loop()
        self.boot_id = int(eventloop.get_loop_time())
        self.config_id = 1
        self.ad_timer = eventloop.register_timer(self._advertise_presence)
        self.server.register_endpoint(
            "/server/zeroconf/ssdp",
            RequestType.GET,
            self._handle_xml_request,
            transports=TransportType.HTTP,
            wrap_result=False,
            content_type="application/xml",
            auth_required=False
        )

    def _create_ssdp_socket(
        self,
        source_addr: Tuple[str, int] = ("0.0.0.0", 0),
        target_addr: Tuple[str, int] = SSDP_ADDR
    ) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        source_ip = socket.inet_aton(source_addr[0])
        target_ip = socket.inet_aton(target_addr[0])
        ip_combo = target_ip + source_ip
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, source_ip)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, ip_combo)
        return sock

    async def start(self) -> None:
        if self.running:
            return
        try:
            sock = self._create_ssdp_socket()
            sock.settimeout(0)
            sock.setblocking(False)
            sock.bind(("", SSDP_ADDR[1]))
            _loop = asyncio.get_running_loop()
            ret = await _loop.create_datagram_endpoint(lambda: self, sock=sock)
            self.transport, _ = ret
        except (socket.error, OSError):
            return
        self.running = True

    async def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self.ad_timer.stop()
        if self.response_handle is not None:
            self.response_handle.cancel()
            self.response_handle = None
        if self.transport.is_closing():
            logging.info("Transport already closing")
            return
        for notification in self._build_notifications("ssdp:byebye"):
            self.transport.sendto(notification, SSDP_ADDR)
        self.close_fut = self.server.get_event_loop().create_future()
        self.transport.close()
        try:
            await asyncio.wait_for(self.close_fut, 2.)
        except asyncio.TimeoutError:
            pass
        self.close_fut = None

    def register_service(
        self, name: str, host_name_or_ip: str, port: int
    ) -> None:
        if len(name) > 64:
            name = name[:64]
        self.name = name
        app: MoonrakerApp = self.server.lookup_component("application")
        self.base_url = f"http://{host_name_or_ip}:{port}{app.route_prefix}"
        self.response_headers = [
            f"USN: uuid:{self.unique_id}::upnp:rootdevice",
            f"LOCATION: {self.base_url}/server/zeroconf/ssdp",
            "ST: upnp:rootdevice",
            "EXT:",
            f"SERVER: {SSDP_SERVER_ID}",
            f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}",
            f"BOOTID.UPNP.ORG: {self.boot_id}",
            f"CONFIGID.UPNP.ORG: {self.config_id}",
        ]
        self.registered = True
        advertisements = self._build_notifications("ssdp:alive")
        if self.running:
            for ad in advertisements:
                self.transport.sendto(ad, SSDP_ADDR)
        self.advertisements = cycle(advertisements)
        self.ad_timer.start()

    async def _handle_xml_request(self, web_request: WebRequest) -> str:
        if not self.registered:
            raise self.server.error("Moonraker SSDP Device not registered", 404)
        app_args = self.server.get_app_args()
        return SSDP_DEVICE_XML.format(
            device_type=SSDP_DEVICE_TYPE,
            config_id=str(self.config_id),
            friendly_name=self.name,
            model_number=app_args["software_version"],
            serial_number=self.unique_id.hex,
            device_uuid=str(self.unique_id),
            presentation_url=self.base_url
        )

    def _advertise_presence(self, eventtime: float) -> float:
        if self.running and self.registered:
            cur_ad = next(self.advertisements)
            self.transport.sendto(cur_ad, SSDP_ADDR)
        delay = random.uniform(SSDP_MAX_AGE / 6., SSDP_MAX_AGE / 3.)
        return eventtime + delay

    def connection_made(
        self, transport: asyncio.transports.BaseTransport
    ) -> None:
        logging.debug("SSDP Server Connected")

    def connection_lost(self, exc: Exception | None) -> None:
        logging.debug("SSDP Server Disconnected")
        if self.close_fut is not None:
            self.close_fut.set_result(None)

    def pause_writing(self) -> None:
        logging.debug("SSDP Pause Writing Requested")

    def resume_writing(self) -> None:
        logging.debug("SSDP Resume Writing Requested")

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        if not self.registered:
            return
        try:
            parts = data.decode().split("\r\n\r\n", maxsplit=1)
            header = parts[0]
        except ValueError:
            logging.exception("Data Decode Error")
            return
        hlines = header.splitlines()
        ssdp_command = hlines[0].strip()
        headers = {}
        for line in hlines[1:]:
            parts = line.strip().split(":", maxsplit=1)
            if len(parts) < 2:
                continue
            headers[parts[0].upper()] = parts[1].strip()
        if (
            ssdp_command != "M-SEARCH * HTTP/1.1" or
            headers.get("MAN") != '"ssdp:discover"'
        ):
            # Not a discovery request
            return
        if headers.get("ST") not in ["upnp:rootdevice", "ssdp:all"]:
            # Service Type doesn't apply
            return
        if self.response_handle is not None:
            # response in progress
            return
        if "MX" in headers:
            delay_time = random.uniform(0, float(headers["MX"]))
            eventloop = self.server.get_event_loop()
            self.response_handle = eventloop.delay_callback(
                delay_time, self._respond_to_discovery, addr
            )
        else:
            self._respond_to_discovery(addr)

    def _respond_to_discovery(self, addr: tuple[str | Any, int]) -> None:
        if not self.running:
            return
        self.response_handle = None
        response: List[str] = ["HTTP/1.1 200 OK"]
        response.extend(self.response_headers)
        response.append(f"DATE: {formatdate(usegmt=True)}")
        response.extend(["", ""])
        self.transport.sendto("\r\n".join(response).encode(), addr)

    def _build_notifications(self, nts: str) -> List[bytes]:
        notifications: List[bytes] = []
        notify_types = [
            ("upnp:rootdevice", f"uuid:{self.unique_id}::upnp:rootdevice"),
            (f"uuid:{self.unique_id}", f"uuid:{self.unique_id}"),
            (SSDP_DEVICE_TYPE, f"uuid:{self.unique_id}::{SSDP_DEVICE_TYPE}")
        ]
        for (nt, usn) in notify_types:
            notifications.append(
                "\r\n".join([
                    "NOTIFY * HTTP/1.1",
                    f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}",
                    f"NTS: {nts}",
                    f"NT: {nt}",
                    f"USN: {usn}",
                    f"LOCATION: {self.base_url}/server/zeroconf/ssdp",
                    "EXT:",
                    f"SERVER: {SSDP_SERVER_ID}",
                    f"CACHE-CONTROL: max-age={SSDP_MAX_AGE}",
                    f"BOOTID.UPNP.ORG: {self.boot_id}",
                    f"CONFIGID.UPNP.ORG: {self.config_id}",
                    "",
                    ""
                ]).encode()
            )
        return notifications

    def error_received(self, exc: Exception) -> None:
        logging.info(f"SSDP Server Error: {exc}")


def load_component(config: ConfigHelper) -> ZeroconfRegistrar:
    return ZeroconfRegistrar(config)
