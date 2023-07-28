# Centralized webcam configuration
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import re
import ipaddress
import socket
import uuid
import logging
from typing import (
    TYPE_CHECKING,
    Optional,
    Dict,
    List,
    Any,
    Tuple
)

if TYPE_CHECKING:
    from ..server import Server
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .database import MoonrakerDatabase
    from .machine import Machine
    from .shell_command import ShellCommandFactory
    from .http_client import HttpClient

# This provides a mapping of fields defined by Moonraker to fields
# defined by the database.
CAM_FIELDS = {
    "name": "name", "service": "service", "target_fps": "targetFps",
    "stream_url": "urlStream", "snapshot_url": "urlSnapshot",
    "flip_horizontal": "flipX", "flip_vertical": "flipY",
    "enabled": "enabled", "target_fps_idle": "targetFpsIdle",
    "aspect_ratio": "aspectRatio", "icon": "icon"
}

class WebcamManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.webcams: Dict[str, WebCam] = {}
        # parse user configured webcams
        prefix_sections = config.get_prefix_sections("webcam ")
        for section in prefix_sections:
            cam_cfg = config[section]
            webcam = WebCam.from_config(cam_cfg)
            self.webcams[webcam.name] = webcam

        self.server.register_endpoint(
            "/server/webcams/list", ["GET"], self._handle_webcam_list
        )
        self.server.register_endpoint(
            "/server/webcams/item", ["GET", "POST", "DELETE"],
            self._handle_webcam_request
        )
        self.server.register_endpoint(
            "/server/webcams/test", ["POST"], self._handle_webcam_test
        )
        self.server.register_notification("webcam:webcams_changed")
        self.server.register_event_handler(
            "machine:public_ip_changed", self._set_default_host_ip
        )

    async def component_init(self) -> None:
        machine: Machine = self.server.lookup_component("machine")
        if machine.public_ip:
            self._set_default_host_ip(machine.public_ip)
        db: MoonrakerDatabase = self.server.lookup_component("database")
        saved_cams: Dict[str, Any] = await db.get_item("webcams", default={})
        for cam_data in saved_cams.values():
            try:
                webcam = WebCam.from_database(self.server, cam_data)
                if webcam.name in self.webcams:
                    continue
                self.webcams[webcam.name] = webcam
            except Exception:
                logging.exception("Failed to process webcam from db")
                continue

    def _set_default_host_ip(self, ip: str) -> None:
        default_host = "http://127.0.0.1"
        if ip:
            try:
                addr = ipaddress.ip_address(ip)
            except Exception:
                logging.debug(f"Invalid IP Recd: {ip}")
            else:
                if addr.version == 6:
                    default_host = f"http://[{addr}]"
                else:
                    default_host = f"http://{addr}"
        WebCam.set_default_host(default_host)
        logging.info(f"Default public webcam address set: {default_host}")

    def get_webcams(self) -> Dict[str, WebCam]:
        return self.webcams

    def _list_webcams(self) -> List[Dict[str, Any]]:
        return [wc.as_dict() for wc in self.webcams.values()]

    async def _find_dbcam_by_uuid(
        self, name: str
    ) -> Tuple[str, Dict[str, Any]]:
        db: MoonrakerDatabase = self.server.lookup_component("database")
        saved_cams: Dict[str, Dict[str, Any]]
        saved_cams = await db.get_item("webcams", default={})
        for uid, cam_data in saved_cams.items():
            if name == cam_data["name"]:
                return uid, cam_data
        return "", {}

    async def _save_cam(self, webcam: WebCam) -> None:
        uid, cam_data = await self._find_dbcam_by_uuid(webcam.name)
        if not uid:
            uid = str(uuid.uuid4())
        for mfield, dbfield in CAM_FIELDS.items():
            cam_data[dbfield] = getattr(webcam, mfield)
        cam_data["location"] = webcam.location
        cam_data["rotation"] = webcam.rotation
        cam_data["extra_data"] = webcam.extra_data
        db: MoonrakerDatabase = self.server.lookup_component("database")
        db.insert_item("webcams", uid, cam_data)

    async def _delete_cam(self, webcam: WebCam) -> None:
        uid, cam = await self._find_dbcam_by_uuid(webcam.name)
        if not uid:
            return
        db: MoonrakerDatabase = self.server.lookup_component("database")
        db.delete_item("webcams", uid)

    async def _handle_webcam_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        action = web_request.get_action()
        name = web_request.get_str("name")
        webcam_data: Dict[str, Any] = {}
        if action == "GET":
            if name not in self.webcams:
                raise self.server.error(f"Webcam {name} not found", 404)
            webcam_data = self.webcams[name].as_dict()
        elif action == "POST":
            webcam = self.webcams.get(name, None)
            if webcam is not None:
                if webcam.source == "config":
                    raise self.server.error(
                        f"Cannot overwrite webcam '{name}' sourced from "
                        "Moonraker configuration"
                    )
                webcam.update(web_request)
            else:
                webcam = WebCam.from_web_request(self.server, web_request)
                self.webcams[name] = webcam
            webcam_data = webcam.as_dict()
            await self._save_cam(webcam)
        elif action == "DELETE":
            if name not in self.webcams:
                raise self.server.error(f"Webcam {name} not found", 404)
            elif self.webcams[name].source == "config":
                raise self.server.error(
                    f"Cannot delete webcam '{name}' sourced from "
                    "Moonraker configuration"
                )
            webcam = self.webcams.pop(name)
            webcam_data = webcam.as_dict()
            await self._delete_cam(webcam)
        if action != "GET":
            self.server.send_event(
                "webcam:webcams_changed", {"webcams": self._list_webcams()}
            )
        return {"webcam": webcam_data}

    async def _handle_webcam_list(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        return {"webcams": self._list_webcams()}

    async def _handle_webcam_test(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        name = web_request.get_str("name")
        if name not in self.webcams:
            raise self.server.error(f"Webcam '{name}' not found", 404)
        client: HttpClient = self.server.lookup_component("http_client")
        cam = self.webcams[name]
        result: Dict[str, Any] = {
            "name": name,
            "snapshot_reachable": False
        }
        for img_type in ["snapshot", "stream"]:
            try:
                func = getattr(cam, f"get_{img_type}_url")
                result[f"{img_type}_url"] = await func(True)
            except Exception:
                logging.exception(f"Error Processing {img_type} url")
                result[f"{img_type}_url"] = ""
        url: str = result["snapshot_url"]
        if result.get("snapshot_url", "").startswith("http"):
            ret = await client.get(url, connect_timeout=1., request_timeout=1.)
            result["snapshot_reachable"] = not ret.has_error()
        return result


class WebCam:
    _default_host: str = "http://127.0.0.1"
    def __init__(self, server: Server, **kwargs) -> None:
        self._server = server
        self.enabled: bool = kwargs["enabled"]
        self.icon: str = kwargs["icon"]
        self.aspect_ratio: str = kwargs["aspect_ratio"]
        self.target_fps_idle: int = kwargs["target_fps_idle"]
        self.name: str = kwargs["name"]
        self.location: str = kwargs["location"]
        self.service: str = kwargs["service"]
        self.target_fps: int = kwargs["target_fps"]
        self.stream_url: str = kwargs["stream_url"]
        self.snapshot_url: str = kwargs["snapshot_url"]
        self.flip_horizontal: bool = kwargs["flip_horizontal"]
        self.flip_vertical: bool = kwargs["flip_vertical"]
        self.rotation: int = kwargs["rotation"]
        self.source: str = kwargs["source"]
        self.extra_data: Dict[str, Any] = kwargs.get("extra_data", {})
        if self.rotation not in [0, 90, 180, 270]:
            raise server.error(f"Invalid value for 'rotation': {self.rotation}")
        prefix, sep, postfix = self.aspect_ratio.partition(":")
        if not (prefix.isdigit() and sep == ":" and postfix.isdigit()):
            raise server.error(
                f"Invalid value for 'aspect_ratio': {self.aspect_ratio}"
            )

    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}

    async def get_stream_url(self, convert_local: bool = False) -> str:
        return await self._get_url(self.stream_url, convert_local)

    async def get_snapshot_url(self, convert_local: bool = False) -> str:
        return await self._get_url(self.snapshot_url, convert_local)

    async def _get_url(self, url: str, convert_local: bool) -> str:
        if not url:
            raise self._server.error("Empty URL Provided")
        match = re.match(r"\w+://[^/]+", url)
        if match is None:
            # assume a partial URL on the default host
            url = f"{self._default_host}/{url.lstrip('/')}"
        if not convert_local:
            return url
        return await self.convert_local(url)

    def _get_local_ips(self) -> List[str]:
        all_ips: List[str] = []
        machine: Machine = self._server.lookup_component("machine")
        sys_info = machine.get_system_info()
        network = sys_info.get("network", {})
        iface: Dict[str, Any]
        for iface in network.values():
            addresses: List[Dict[str, Any]] = iface["ip_addresses"]
            for addr_info in addresses:
                all_ips.append(addr_info["address"])
        return all_ips

    async def convert_local(self, url: str) -> str:
        match = re.match(r"(\w+)://([^/]+)(/.*)?", url)
        if match is None:
            return url
        scheme = match.group(1)
        addr = match.group(2)
        fragment = match.group(3)
        if fragment is None:
            fragment = ""
        if addr[0] == "[":
            # ipv6 address
            addr_match = re.match(r"\[(.+)\](:\d+)?", addr)
        else:
            # ipv4 address or hostname
            addr_match = re.match(r"([^:]+)(:\d+)?", addr)
        if addr_match is None:
            return url
        addr = addr_match.group(1)
        port: Optional[str] = addr_match.group(2)
        default_ports = {"http": "80", "https": "443", "rtsp": "554"}
        if port is None:
            if scheme not in default_ports:
                return url
            port = default_ports[scheme]
        else:
            port = port.lstrip(":")
        # attempt to convert hostname to IP
        try:
            eventloop = self._server.get_event_loop()
            addr_info = await eventloop.run_in_thread(
                socket.getaddrinfo, addr, int(port)
            )
            if addr_info:
                addr = addr_info[0][4][0]
        except Exception:
            pass
        try:
            ip = ipaddress.ip_address(addr)
        except Exception:
            # Invalid IP, can't convert.
            return url
        else:
            if ip.is_loopback:
                return url
            # Check to see if this ip address is on the local machine
            if addr not in self._get_local_ips():
                return url
        scmd: ShellCommandFactory
        scmd = self._server.lookup_component("shell_command")
        try:
            # Use the ss command to list all tcp ports
            resp: str = await scmd.exec_cmd("ss -ltn")
            lines = resp.split("\n")[1:]
            for line in lines:
                parts = line.strip().split()
                if len(parts) < 5:
                    continue
                laddr, lport = parts[3].split(":")
                if lport == port:
                    if laddr == "[::]":
                        return f"{scheme}://[::1]:{port}{fragment}"
                    elif laddr == "0.0.0.0":
                        return f"{scheme}://127.0.0.1:{port}{fragment}"
        except scmd.error:
            pass
        return url

    def update(self, web_request: WebRequest) -> None:
        for field in web_request.get_args().keys():
            try:
                attr = getattr(self, field)
            except AttributeError:
                continue
            if isinstance(attr, bool):
                val: Any = web_request.get_boolean(field)
            elif isinstance(attr, int):
                val = web_request.get_int(field)
            elif isinstance(attr, float):
                val = web_request.get_float(field)
            elif isinstance(attr, str):
                val = web_request.get_str(field)
            else:
                val = web_request.get(field)
            setattr(self, field, val)

    @staticmethod
    def set_default_host(host: str) -> None:
        WebCam._default_host = host

    @classmethod
    def from_config(cls, config: ConfigHelper) -> WebCam:
        webcam: Dict[str, Any] = {}
        webcam["name"] = config.get_name().split(maxsplit=1)[-1]
        webcam["enabled"] = config.getboolean("enabled", True)
        webcam["icon"] = config.get("icon", "mdiWebcam")
        webcam["aspect_ratio"] = config.get("aspect_ratio", "4:3")
        webcam["location"] = config.get("location", "printer")
        webcam["service"] = config.get("service", "mjpegstreamer")
        webcam["target_fps"] = config.getint("target_fps", 15)
        webcam["target_fps_idle"] = config.getint("target_fps_idle", 5)
        webcam["stream_url"] = config.get("stream_url")
        webcam["snapshot_url"] = config.get("snapshot_url", "")
        webcam["flip_horizontal"] = config.getboolean("flip_horizontal", False)
        webcam["flip_vertical"] = config.getboolean("flip_vertical", False)
        webcam["rotation"] = config.getint("rotation", 0)
        webcam["source"] = "config"
        server = config.get_server()
        try:
            return cls(config.get_server(), **webcam)
        except server.error as err:
            raise config.error(str(err)) from err

    @classmethod
    def from_web_request(
        cls, server: Server, web_request: WebRequest
    ) -> WebCam:
        webcam: Dict[str, Any] = {}
        webcam["name"] = web_request.get_str("name")
        webcam["enabled"] = web_request.get_boolean("enabled", True)
        webcam["icon"] = web_request.get_str("icon", "mdiWebcam")
        webcam["aspect_ratio"] = web_request.get_str("aspect_ratio", "4:3")
        webcam["location"] = web_request.get_str("location", "printer")
        webcam["service"] = web_request.get_str("service", "mjpegstreamer")
        webcam["target_fps"] = web_request.get_int("target_fps", 15)
        webcam["target_fps_idle"] = web_request.get_int("target_fps_idle", 5)
        webcam["stream_url"] = web_request.get_str("stream_url")
        webcam["snapshot_url"] = web_request.get_str("snapshot_url", "")
        webcam["flip_horizontal"] = web_request.get_boolean("flip_horizontal", False)
        webcam["flip_vertical"] = web_request.get_boolean("flip_vertical", False)
        webcam["rotation"] = web_request.get_int("rotation", 0)
        webcam["extra_data"] = web_request.get("extra_data", {})
        webcam["source"] = "database"
        return cls(server, **webcam)

    @classmethod
    def from_database(cls, server: Server, cam_data: Dict[str, Any]) -> WebCam:
        webcam: Dict[str, Any] = {}
        webcam["name"] = str(cam_data["name"])
        webcam["enabled"] = bool(cam_data.get("enabled", True))
        webcam["icon"] = str(cam_data.get("icon", "mdiWebcam"))
        webcam["aspect_ratio"] = str(cam_data.get("aspectRatio", "4:3"))
        webcam["location"] = str(cam_data.get("location", "printer"))
        webcam["service"] = str(cam_data.get("service", "mjpegstreamer"))
        webcam["target_fps"] = int(cam_data.get("targetFps", 15))
        webcam["target_fps_idle"] = int(cam_data.get("targetFpsIdle", 5))
        webcam["stream_url"] = str(cam_data.get("urlStream", ""))
        webcam["snapshot_url"] = str(cam_data.get("urlSnapshot", ""))
        webcam["flip_horizontal"] = bool(cam_data.get("flipX", False))
        webcam["flip_vertical"] = bool(cam_data.get("flipY", False))
        webcam["rotation"] = int(cam_data.get("rotation", webcam.get("rotate", 0)))
        webcam["extra_data"] = cam_data.get("extra_data", {})
        webcam["source"] = "database"
        return cls(server, **webcam)

def load_component(config: ConfigHelper) -> WebcamManager:
    return WebcamManager(config)
