# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import sys
import os
import re
import json
import pathlib
import logging
import asyncio
import platform
import socket
import ipaddress
import time
import shutil
import distro
import tempfile
import getpass
import configparser
from ..confighelper import FileSourceWrapper
from ..utils import source_info

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    cast
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from ..app import MoonrakerApp
    from ..klippy_connection import KlippyConnection
    from .shell_command import ShellCommandFactory as SCMDComp
    from .database import MoonrakerDatabase
    from .file_manager.file_manager import FileManager
    from .authorization import Authorization
    from .announcements import Announcements
    from .proc_stats import ProcStats
    from .dbus_manager import DbusManager
    from dbus_next.aio import ProxyInterface
    from dbus_next import Variant
    SudoReturn = Union[Awaitable[Tuple[str, bool]], Tuple[str, bool]]
    SudoCallback = Callable[[], SudoReturn]

DEFAULT_ALLOWED_SERVICES = [
    "klipper_mcu",
    "webcamd",
    "MoonCord",
    "KlipperScreen",
    "moonraker-telegram-bot",
    "moonraker-obico",
    "sonar",
    "crowsnest",
    "octoeverywhere",
    "ratos-configurator"
]
CGROUP_PATH = "/proc/1/cgroup"
SCHED_PATH = "/proc/1/sched"
SYSTEMD_PATH = "/etc/systemd/system"
SD_CID_PATH = "/sys/block/mmcblk0/device/cid"
SD_CSD_PATH = "/sys/block/mmcblk0/device/csd"
SD_MFGRS = {
    '1b': "Samsung",
    '03': "Sandisk",
    '74': "PNY"
}
IP_FAMILIES = {'inet': 'ipv4', 'inet6': 'ipv6'}
NETWORK_UPDATE_SEQUENCE = 10
SERVICE_PROPERTIES = [
    "Requires", "After", "SupplementaryGroups", "EnvironmentFiles",
    "ExecStart", "WorkingDirectory", "FragmentPath", "Description",
    "User"
]

class Machine:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self._allowed_services: List[str] = []
        self._init_allowed_services()
        dist_info: Dict[str, Any]
        dist_info = {'name': distro.name(pretty=True)}
        dist_info.update(distro.info())
        dist_info['release_info'] = distro.distro_release_info()
        self.inside_container = False
        self.moonraker_service_info: Dict[str, Any] = {}
        self.sudo_req_lock = asyncio.Lock()
        self._sudo_password: Optional[str] = None
        sudo_template = config.gettemplate("sudo_password", None)
        if sudo_template is not None:
            self._sudo_password = sudo_template.render()
        self._public_ip = ""
        self.system_info: Dict[str, Any] = {
            'python': {
                "version": sys.version_info,
                "version_string": sys.version.replace("\n", " ")
            },
            'cpu_info': self._get_cpu_info(),
            'sd_info': self._get_sdcard_info(),
            'distribution': dist_info,
            'virtualization': self._check_inside_container(),
            'network': {},
            'canbus': {}
        }
        self._update_log_rollover(log=True)
        providers: Dict[str, type] = {
            "none": BaseProvider,
            "systemd_cli": SystemdCliProvider,
            "systemd_dbus": SystemdDbusProvider,
            "supervisord_cli": SupervisordCliProvider
        }
        self.provider_type = config.get('provider', 'systemd_dbus')
        pclass = providers.get(self.provider_type)
        if pclass is None:
            raise config.error(f"Invalid Provider: {self.provider_type}")
        self.sys_provider: BaseProvider = pclass(config)
        self.system_info["provider"] = self.provider_type
        logging.info(f"Using System Provider: {self.provider_type}")
        self.validator = InstallValidator(config)
        self.sudo_requests: List[Tuple[SudoCallback, str]] = []

        self.server.register_endpoint(
            "/machine/reboot", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/shutdown", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/services/restart", ['POST'],
            self._handle_service_request)
        self.server.register_endpoint(
            "/machine/services/stop", ['POST'],
            self._handle_service_request)
        self.server.register_endpoint(
            "/machine/services/start", ['POST'],
            self._handle_service_request)
        self.server.register_endpoint(
            "/machine/system_info", ['GET'],
            self._handle_sysinfo_request)
        self.server.register_endpoint(
            "/machine/sudo/info", ["GET"], self._handle_sudo_info)
        self.server.register_endpoint(
            "/machine/sudo/password", ["POST"],
            self._set_sudo_password)

        self.server.register_notification("machine:service_state_changed")
        self.server.register_notification("machine:sudo_alert")

        # Register remote methods
        self.server.register_remote_method(
            "shutdown_machine", self.sys_provider.shutdown)
        self.server.register_remote_method(
            "reboot_machine", self.sys_provider.reboot)

        # IP network shell commands
        shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')
        self.addr_cmd = shell_cmd.build_shell_command("ip -json -det address")
        iwgetbin = "/sbin/iwgetid"
        if not pathlib.Path(iwgetbin).exists():
            iwgetbin = "iwgetid"
        self.iwgetid_cmd = shell_cmd.build_shell_command(iwgetbin)
        self.init_evt = asyncio.Event()

    def _init_allowed_services(self) -> None:
        app_args = self.server.get_app_args()
        data_path = app_args["data_path"]
        fpath = pathlib.Path(data_path).joinpath("moonraker.asvc")
        fm: FileManager = self.server.lookup_component("file_manager")
        fm.add_reserved_path("allowed_services", fpath, False)
        try:
            if not fpath.exists():
                fpath.write_text("\n".join(DEFAULT_ALLOWED_SERVICES))
            data = fpath.read_text()
        except Exception:
            logging.exception("Failed to read allowed_services.txt")
            self._allowed_services = DEFAULT_ALLOWED_SERVICES
        else:
            svcs = [svc.strip() for svc in data.split("\n") if svc.strip()]
            for svc in svcs:
                if svc.endswith(".service"):
                    svc = svc.rsplit(".", 1)[0]
                if svc not in self._allowed_services:
                    self._allowed_services.append(svc)

    def _update_log_rollover(self, log: bool = False) -> None:
        sys_info_msg = "\nSystem Info:"
        for header, info in self.system_info.items():
            sys_info_msg += f"\n\n***{header}***"
            if not isinstance(info, dict):
                sys_info_msg += f"\n  {repr(info)}"
            else:
                for key, val in info.items():
                    sys_info_msg += f"\n  {key}: {val}"
        sys_info_msg += f"\n\n***Allowed Services***"
        for svc in self._allowed_services:
            sys_info_msg += f"\n  {svc}"
        self.server.add_log_rollover_item('system_info', sys_info_msg, log=log)

    @property
    def public_ip(self) -> str:
        return self._public_ip

    @property
    def unit_name(self) -> str:
        svc_info = self.moonraker_service_info
        unit_name = svc_info.get("unit_name", "moonraker.service")
        return unit_name.split(".", 1)[0]

    def is_service_allowed(self, service: str) -> bool:
        return (
            service in self._allowed_services or
            re.match(r"moonraker[_-]?\d*", service) is not None or
            re.match(r"klipper[_-]?\d*", service) is not None
        )

    def validation_enabled(self) -> bool:
        return self.validator.validation_enabled

    def get_system_provider(self):
        return self.sys_provider

    def is_inside_container(self):
        return self.inside_container

    def get_provider_type(self):
        return self.provider_type

    def get_moonraker_service_info(self):
        return dict(self.moonraker_service_info)

    async def wait_for_init(
        self, timeout: Optional[float] = None
    ) -> None:
        try:
            await asyncio.wait_for(self.init_evt.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def component_init(self) -> None:
        await self.validator.validation_init()
        await self.sys_provider.initialize()
        if not self.inside_container:
            virt_info = await self.sys_provider.check_virt_status()
            self.system_info['virtualization'] = virt_info
        await self._parse_network_interfaces(0, notify=False)
        pstats: ProcStats = self.server.lookup_component('proc_stats')
        pstats.register_stat_callback(self._parse_network_interfaces)
        available_svcs = self.sys_provider.get_available_services()
        avail_list = list(available_svcs.keys())
        self.system_info['available_services'] = avail_list
        self.system_info['service_state'] = available_svcs
        svc_info = await self.sys_provider.extract_service_info(
            "moonraker", os.getpid()
        )
        self.moonraker_service_info = svc_info
        self.log_service_info(svc_info)
        self.init_evt.set()

    async def validate_installation(self) -> bool:
        return await self.validator.perform_validation()

    async def on_exit(self) -> None:
        await self.validator.remove_announcement()

    async def _handle_machine_request(self, web_request: WebRequest) -> str:
        ep = web_request.get_endpoint()
        if self.inside_container:
            virt_id = self.system_info['virtualization'].get(
                'virt_identifier', "none")
            raise self.server.error(
                f"Cannot {ep.split('/')[-1]} from within a "
                f"{virt_id} container")
        if ep == "/machine/shutdown":
            await self.sys_provider.shutdown()
        elif ep == "/machine/reboot":
            await self.sys_provider.reboot()
        else:
            raise self.server.error("Unsupported machine request")
        return "ok"

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        await self.sys_provider.do_service_action(action, service_name)

    def restart_moonraker_service(self):
        async def wrapper():
            try:
                await self.do_service_action("restart", self.unit_name)
            except Exception:
                pass
        self.server.get_event_loop().create_task(wrapper())

    async def _handle_service_request(self, web_request: WebRequest) -> str:
        name: str = web_request.get_str('service')
        action = web_request.get_endpoint().split('/')[-1]
        if name == self.unit_name:
            if action != "restart":
                raise self.server.error(
                    f"Service action '{action}' not available for moonraker")
            self.restart_moonraker_service()
        elif self.sys_provider.is_service_available(name):
            await self.do_service_action(action, name)
        else:
            if name in self._allowed_services:
                raise self.server.error(f"Service '{name}' not installed")
            raise self.server.error(
                f"Service '{name}' not allowed")
        return "ok"

    async def _handle_sysinfo_request(self,
                                      web_request: WebRequest
                                      ) -> Dict[str, Any]:
        kconn: KlippyConnection
        kconn = self.server.lookup_component("klippy_connection")
        sys_info = self.system_info.copy()
        sys_info["instance_ids"] = {
            "moonraker": self.unit_name,
            "klipper": kconn.unit_name
        }
        return {"system_info": sys_info}

    async def _set_sudo_password(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        async with self.sudo_req_lock:
            self._sudo_password = web_request.get_str("password")
            if not await self.check_sudo_access():
                self._sudo_password = None
                raise self.server.error(
                    "Invalid password, sudo access was denied"
                )
            sudo_responses = ["Sudo password successfully set."]
            restart: bool = False
            failed: List[Tuple[SudoCallback, str]] = []
            failed_msgs: List[str] = []
            if self.sudo_requests:
                while self.sudo_requests:
                    cb, msg = self.sudo_requests.pop(0)
                    try:
                        ret = cb()
                        if isinstance(ret, Awaitable):
                            ret = await ret
                        msg, need_restart = ret
                        sudo_responses.append(msg)
                        restart |= need_restart
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        failed.append((cb, msg))
                        failed_msgs.append(str(e))
                restart = False if len(failed) > 0 else restart
                self.sudo_requests = failed
                if not restart and len(sudo_responses) > 1:
                    # at least one successful response and not restarting
                    eventloop = self.server.get_event_loop()
                    eventloop.delay_callback(
                        .05, self.server.send_event,
                        "machine:sudo_alert",
                        {
                            "sudo_requested": self.sudo_requested,
                            "request_messages": self.sudo_request_messages
                        }
                    )
                if failed_msgs:
                    err_msg = "\n".join(failed_msgs)
                    raise self.server.error(err_msg, 500)
                if restart:
                    self.restart_moonraker_service()
                    sudo_responses.append(
                        "Moonraker is currently in the process of restarting."
                    )
        return {
            "sudo_responses": sudo_responses,
            "is_restarting": restart
        }

    async def _handle_sudo_info(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        check_access = web_request.get_boolean("check_access", False)
        has_sudo: Optional[bool] = None
        if check_access:
            has_sudo = await self.check_sudo_access()
        return {
            "sudo_access": has_sudo,
            "linux_user": self.linux_user,
            "sudo_requested": self.sudo_requested,
            "request_messages": self.sudo_request_messages
        }

    def get_system_info(self) -> Dict[str, Any]:
        return self.system_info

    @property
    def sudo_password(self) -> Optional[str]:
        return self._sudo_password

    @sudo_password.setter
    def sudo_password(self, pwd: Optional[str]) -> None:
        self._sudo_password = pwd

    @property
    def sudo_requested(self) -> bool:
        return len(self.sudo_requests) > 0

    @property
    def linux_user(self) -> str:
        return getpass.getuser()

    @property
    def sudo_request_messages(self) -> List[str]:
        return [req[1] for req in self.sudo_requests]

    def register_sudo_request(
        self, callback: SudoCallback, message: str
    ) -> None:
        self.sudo_requests.append((callback, message))
        self.server.send_event(
            "machine:sudo_alert",
            {
                "sudo_requested": True,
                "request_messages": self.sudo_request_messages
            }
        )

    async def check_sudo_access(self, cmds: List[str] = []) -> bool:
        if not cmds:
            cmds = ["systemctl --version", "ls /root"]
        shell_cmd: SCMDComp = self.server.lookup_component("shell_command")
        for cmd in cmds:
            try:
                await self.exec_sudo_command(cmd, timeout=10.)
            except shell_cmd.error:
                return False
        return True

    async def exec_sudo_command(
        self, command: str, tries: int = 1, timeout=2.
    ) -> str:
        proc_input = None
        full_cmd = f"sudo {command}"
        if self._sudo_password is not None:
            proc_input = self._sudo_password
            full_cmd = f"sudo -S {command}"
        shell_cmd: SCMDComp = self.server.lookup_component("shell_command")
        return await shell_cmd.exec_cmd(
            full_cmd, proc_input=proc_input, log_complete=False, retries=tries,
            timeout=timeout
        )

    def _get_sdcard_info(self) -> Dict[str, Any]:
        sd_info: Dict[str, Any] = {}
        cid_file = pathlib.Path(SD_CID_PATH)
        if not cid_file.exists():
            # No SDCard detected at mmcblk0
            return {}
        try:
            cid_text = cid_file.read_text().strip().lower()
            mid = cid_text[:2]
            sd_info['manufacturer_id'] = mid
            sd_info['manufacturer'] = SD_MFGRS.get(mid, "Unknown")
            sd_info['oem_id'] = cid_text[2:6]
            sd_info['product_name'] = bytes.fromhex(cid_text[6:16]).decode(
                encoding="ascii", errors="ignore")
            sd_info['product_revision'] = \
                f"{int(cid_text[16], 16)}.{int(cid_text[17], 16)}"
            sd_info['serial_number'] = cid_text[18:26]
            mfg_year = int(cid_text[27:29], 16) + 2000
            mfg_month = int(cid_text[29], 16)
            sd_info['manufacturer_date'] = f"{mfg_month}/{mfg_year}"
        except Exception:
            logging.info("Error reading SDCard CID Register")
            return {}
        sd_info['capacity'] = "Unknown"
        sd_info['total_bytes'] = 0
        csd_file = pathlib.Path(SD_CSD_PATH)
        # Read CSD Register
        try:
            csd_reg = bytes.fromhex(csd_file.read_text().strip())
            csd_type = (csd_reg[0] >> 6) & 0x3
            if csd_type == 0:
                # Standard Capacity (CSD Version 1.0)
                max_block_len: int = 2**(csd_reg[5] & 0xF)
                c_size = ((csd_reg[6] & 0x3) << 10) | (csd_reg[7] << 2) | \
                    ((csd_reg[8] >> 6) & 0x3)
                c_mult_reg = ((csd_reg[9] & 0x3) << 1) | (csd_reg[10] >> 7)
                c_mult = 2**(c_mult_reg + 2)
                total_bytes: int = (c_size + 1) * c_mult * max_block_len
                sd_info['capacity'] = f"{(total_bytes / (1024.0**2)):.1f} MiB"
            elif csd_type == 1:
                # High Capacity (CSD Version 2.0)
                c_size = ((csd_reg[7] & 0x3F) << 16) | (csd_reg[8] << 8) | \
                    csd_reg[9]
                total_bytes = (c_size + 1) * 512 * 1024
                sd_info['capacity'] = f"{(total_bytes / (1024.0**3)):.1f} GiB"
            elif csd_type == 2:
                # Ultra Capacity (CSD Version 3.0)
                c_size = ((csd_reg[6]) & 0xF) << 24 | (csd_reg[7] << 16) | \
                    (csd_reg[8] << 8) | csd_reg[9]
                total_bytes = (c_size + 1) * 512 * 1024
                sd_info['capacity'] = f"{(total_bytes / (1024.0**4)):.1f} TiB"
            else:
                # Invalid CSD, skip capacity check
                return sd_info
            sd_info['total_bytes'] = total_bytes
        except Exception:
            logging.info("Error Reading SDCard CSD Register")
        return sd_info

    def _get_cpu_info(self) -> Dict[str, Any]:
        cpu_file = pathlib.Path("/proc/cpuinfo")
        mem_file = pathlib.Path("/proc/meminfo")
        cpu_info = {
            'cpu_count': os.cpu_count(),
            'bits': platform.architecture()[0],
            'processor': platform.processor() or platform.machine(),
            'cpu_desc': "",
            'serial_number': "",
            'hardware_desc': "",
            'model': "",
            'total_memory': None,
            'memory_units': ""
        }
        if cpu_file.exists():
            try:
                cpu_text = cpu_file.read_text().strip()
                cpu_items = [item.strip() for item in cpu_text.split("\n\n")
                             if item.strip()]
                for item in cpu_items:
                    cpu_desc_match = re.search(r"model name\s+:\s+(.+)", item)
                    if cpu_desc_match is not None:
                        cpu_info['cpu_desc'] = cpu_desc_match.group(1).strip()
                        break
                hw_match = re.search(r"Hardware\s+:\s+(.+)", cpu_items[-1])
                if hw_match is not None:
                    cpu_info['hardware_desc'] = hw_match.group(1).strip()
                sn_match = re.search(r"Serial\s+:\s+0*(.+)", cpu_items[-1])
                if sn_match is not None:
                    cpu_info['serial_number'] = sn_match.group(1).strip()
                model_match = re.search(r"Model\s+:\s+(.+)", cpu_items[-1])
                if model_match is not None:
                    cpu_info['model'] = model_match.group(1).strip()
            except Exception:
                logging.info("Error Reading /proc/cpuinfo")
        if mem_file.exists():
            try:
                mem_text = mem_file.read_text().strip()
                for line in mem_text.split('\n'):
                    line = line.strip()
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        cpu_info['total_memory'] = int(parts[1])
                        cpu_info['memory_units'] = parts[2]
                        break
            except Exception:
                logging.info("Error Reading /proc/meminfo")
        return cpu_info

    def _check_inside_container(self) -> Dict[str, Any]:
        cgroup_file = pathlib.Path(CGROUP_PATH)
        virt_type = virt_id = "none"
        if cgroup_file.exists():
            try:
                data = cgroup_file.read_text()
                container_types = ["docker", "lxc"]
                for ct in container_types:
                    if ct in data:
                        self.inside_container = True
                        virt_type = "container"
                        virt_id = ct
                        logging.info(
                            f"Container detected via cgroup: {ct}"
                        )
                        break
            except Exception:
                logging.exception(f"Error reading {CGROUP_PATH}")

        # Fall back to process schedule check
        if not self.inside_container:
            sched_file = pathlib.Path(SCHED_PATH)
            if sched_file.exists():
                try:
                    data = sched_file.read_text().strip()
                    proc_name = data.split('\n')[0].split()[0]
                    if proc_name not in ["init", "systemd"]:
                        self.inside_container = True
                        virt_type = "container"
                        virt_id = "lxc"
                        if (
                            os.path.exists("/.dockerenv") or
                            os.path.exists("/.dockerinit")
                        ):
                            virt_id = "docker"
                        logging.info(
                            f"Container detected via sched: {virt_id}"
                        )
                except Exception:
                    logging.exception(f"Error reading {SCHED_PATH}")
        return {
            'virt_type': virt_type,
            'virt_identifier': virt_id
        }

    async def _parse_network_interfaces(self,
                                        sequence: int,
                                        notify: bool = True
                                        ) -> None:
        if sequence % NETWORK_UPDATE_SEQUENCE:
            return
        network: Dict[str, Any] = {}
        canbus: Dict[str, Any] = {}
        try:
            # get network interfaces
            resp = await self.addr_cmd.run_with_response(log_complete=False)
            decoded: List[Dict[str, Any]] = json.loads(resp)
            for interface in decoded:
                if interface['operstate'] != "UP":
                    continue
                if interface['link_type'] == "can":
                    infodata: dict = interface.get(
                        "linkinfo", {}).get("info_data", {})
                    canbus[interface['ifname']] = {
                        'tx_queue_len': interface['txqlen'],
                        'bitrate': infodata.get("bittiming", {}).get(
                            "bitrate", -1
                        ),
                        'driver': infodata.get("bittiming_const", {}).get(
                            "name", "unknown"
                        )
                    }
                elif (
                    interface['link_type'] == "ether" and
                    'address' in interface
                ):
                    addresses: List[Dict[str, Any]] = [
                        {
                            'family': IP_FAMILIES[addr['family']],
                            'address': addr['local'],
                            'is_link_local': addr.get('scope', "") == "link"
                        }
                        for addr in interface.get('addr_info', [])
                        if 'family' in addr and 'local' in addr
                    ]
                    if not addresses:
                        continue
                    network[interface['ifname']] = {
                        'mac_address': interface['address'],
                        'ip_addresses': addresses
                    }
        except Exception:
            logging.exception("Error processing network update")
            return
        prev_network = self.system_info.get('network', {})
        if network != prev_network:
            self._find_public_ip()
            if notify:
                self.server.send_event("machine:net_state_changed", network)
        self.system_info['network'] = network
        self.system_info['canbus'] = canbus

    async def get_public_network(self) -> Dict[str, Any]:
        wifis = await self._get_wifi_interfaces()
        public_intf = self._find_public_interface()
        ifname = public_intf["ifname"]
        is_wifi = ifname in wifis
        public_intf["is_wifi"] = is_wifi
        if is_wifi:
            public_intf["ssid"] = wifis[ifname]
        # TODO: Can  we detect the private top level domain? That
        # would be ideal
        public_intf["hostname"] = socket.gethostname()
        return public_intf

    def _find_public_interface(self) -> Dict[str, Any]:
        src_ip = self._find_public_ip()
        networks = self.system_info.get("network", {})
        for ifname, ifinfo in networks.items():
            for addrinfo in ifinfo["ip_addresses"]:
                if addrinfo["is_link_local"]:
                    continue
                fam = addrinfo["family"]
                addr = addrinfo["address"]
                if fam == "ipv6" and not src_ip:
                    ip = ipaddress.ip_address(addr)
                    if ip.is_global:
                        return {
                            "ifname": ifname,
                            "address": addr,
                            "family": fam
                        }
                elif src_ip == addr:
                    return {
                        "ifname": ifname,
                        "address": addr,
                        "family": fam
                    }
        return {
            "ifname": "",
            "address": src_ip or "",
            "family": ""
        }

    def _find_public_ip(self) -> str:
        #  Check for an IPv4 Source IP
        # NOTE: It should also be possible to extract this from
        # the routing table, ie: ip -json route
        # It would be an entry with a "gateway" with the lowest
        # metric.  Might also be able to get IPv6 info from this.
        # However, it would be better to use NETLINK for this rather
        # than run another shell command
        src_ip: str = ""
        # First attempt: use "broadcast" to find the local IP
        addr_info = [
            ("<broadcast>", 0, socket.AF_INET),
            ("10.255.255.255", 1, socket.AF_INET),
            ("2001:db8::1234", 1, socket.AF_INET6),
        ]
        for (addr, port, fam) in addr_info:
            s = socket.socket(fam, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)
            try:
                if addr == "<broadcast>":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.connect((addr, port))
                src_ip = s.getsockname()[0]
            except Exception:
                continue
            logging.info(f"Detected Local IP: {src_ip}")
            break
        if src_ip != self._public_ip:
            self._public_ip = src_ip
            self.server.send_event("machine:public_ip_changed", src_ip)
        if not src_ip:
            logging.info("Failed to detect local IP address")
        return src_ip

    async def _get_wifi_interfaces(self) -> Dict[str, Any]:
        # get wifi interfaces
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        wifi_intfs: Dict[str, Any] = {}
        try:
            resp = await self.iwgetid_cmd.run_with_response(log_complete=False)
        except shell_cmd.error:
            logging.info("Failed to run 'iwgetid' command")
            return {}
        if resp:
            for line in resp.split("\n"):
                parts = line.strip().split(maxsplit=1)
                wifi_intfs[parts[0]] = parts[1].split(":")[-1].strip('"')
        return wifi_intfs

    def log_service_info(self, svc_info: Dict[str, Any]) -> None:
        if not svc_info:
            return
        name = svc_info.get("unit_name", "unknown")
        manager = svc_info.get("manager", "systemd").capitalize()
        msg = f"\n{manager} unit {name}:"
        for key, val in svc_info.items():
            if key == "properties":
                msg += "\nProperties:"
                for prop_key, prop in val.items():
                    msg += f"\n**{prop_key}={prop}"
            else:
                msg += f"\n{key}: {val}"
        self.server.add_log_rollover_item(name, msg)

class BaseProvider:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.shutdown_action = config.get("shutdown_action", "poweroff")
        self.shutdown_action = self.shutdown_action.lower()
        if self.shutdown_action not in ["halt", "poweroff"]:
            raise config.error(
                "Section [machine], Option 'shutdown_action':"
                f"Invalid value '{self.shutdown_action}', must be "
                "'halt' or 'poweroff'"
            )
        self.available_services: Dict[str, Dict[str, str]] = {}
        self.shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')

    async def initialize(self) -> None:
        pass

    async def _exec_sudo_command(self, command: str):
        machine: Machine = self.server.lookup_component("machine")
        return await machine.exec_sudo_command(command)

    async def shutdown(self) -> None:
        await self._exec_sudo_command(f"systemctl {self.shutdown_action}")

    async def reboot(self) -> None:
        await self._exec_sudo_command("systemctl reboot")

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        raise self.server.error("Service Actions Not Available", 503)

    async def check_virt_status(self) -> Dict[str, Any]:
        return {
            'virt_type': "unknown",
            'virt_identifier': "unknown"
        }

    def is_service_available(self, service: str) -> bool:
        return service in self.available_services

    def get_available_services(self) -> Dict[str, Dict[str, str]]:
        return self.available_services

    async def extract_service_info(
        self,
        service: str,
        pid: int,
        properties: Optional[List[str]] = None,
        raw: bool = False
    ) -> Dict[str, Any]:
        return {}

class SystemdCliProvider(BaseProvider):
    async def initialize(self) -> None:
        await self._detect_active_services()
        if self.available_services:
            svcs = list(self.available_services.keys())
            self.svc_cmd = self.shell_cmd.build_shell_command(
                "systemctl show -p ActiveState,SubState --value "
                f"{' '.join(svcs)}")
            await self._update_service_status(0, notify=True)
            pstats: ProcStats = self.server.lookup_component('proc_stats')
            pstats.register_stat_callback(self._update_service_status)

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        await self._exec_sudo_command(f"systemctl {action} {service_name}")

    async def check_virt_status(self) -> Dict[str, Any]:
        # Fallback virtualization check
        virt_id = virt_type = "none"

        # Check for any form of virtualization.  This will report the innermost
        # virtualization type in the event that nested virtualization is used
        try:
            resp: str = await self.shell_cmd.exec_cmd("systemd-detect-virt")
        except self.shell_cmd.error:
            pass
        else:
            virt_id = resp.strip()

        if virt_id != "none":
            # Check explicitly for container virtualization
            try:
                resp = await self.shell_cmd.exec_cmd(
                    "systemd-detect-virt --container")
            except self.shell_cmd.error:
                virt_type = "vm"
            else:
                if virt_id == resp.strip():
                    virt_type = "container"
                else:
                    # Moonraker is run from within a VM inside a container
                    virt_type = "vm"
            logging.info(
                f"Virtualized Environment Detected, Type: {virt_type} "
                f"id: {virt_id}")
        else:
            logging.info("No Virtualization Detected")
        return {
            'virt_type': virt_type,
            'virt_identifier': virt_id
        }

    async def _detect_active_services(self) -> None:
        machine: Machine = self.server.lookup_component("machine")
        try:
            resp: str = await self.shell_cmd.exec_cmd(
                "systemctl list-units --all --type=service --plain"
                " --no-legend")
            lines = resp.split('\n')
            services = [line.split()[0].strip() for line in lines
                        if ".service" in line.strip()]
        except Exception:
            services = []
        for svc in services:
            sname = svc.rsplit('.', 1)[0]
            if machine.is_service_allowed(sname):
                self.available_services[sname] = {
                    'active_state': "unknown",
                    'sub_state': "unknown"
                }

    async def _update_service_status(self,
                                     sequence: int,
                                     notify: bool = True
                                     ) -> None:
        if sequence % 2:
            # Update every other sequence
            return
        svcs = list(self.available_services.keys())
        try:
            resp = await self.svc_cmd.run_with_response(log_complete=False)
            for svc, state in zip(svcs, resp.strip().split('\n\n')):
                active_state, sub_state = state.split('\n', 1)
                new_state: Dict[str, str] = {
                    'active_state': active_state,
                    'sub_state': sub_state
                }
                if self.available_services[svc] != new_state:
                    self.available_services[svc] = new_state
                    if notify:
                        self.server.send_event(
                            "machine:service_state_changed",
                            {svc: new_state})
        except Exception:
            logging.exception("Error processing service state update")

    async def extract_service_info(
        self,
        service_name: str,
        pid: int,
        properties: Optional[List[str]] = None,
        raw: bool = False
    ) -> Dict[str, Any]:
        service_info: Dict[str, Any] = {}
        expected_name = f"{service_name}.service"
        if properties is None:
            properties = SERVICE_PROPERTIES
        try:
            resp: str = await self.shell_cmd.exec_cmd(
                f"systemctl status {pid}"
            )
            unit_name = resp.split(maxsplit=2)[1]
            service_info["unit_name"] = unit_name
            service_info["is_default"] = True
            service_info["manager"] = "systemd"
            if unit_name != expected_name:
                service_info["is_default"] = False
                logging.info(
                    f"Detected alternate unit name for {service_name}: "
                    f"{unit_name}"
                )
            prop_args = ",".join(properties)
            props: str = await self.shell_cmd.exec_cmd(
                f"systemctl show -p {prop_args} {unit_name}", retries=5,
                timeout=10.
            )
            raw_props: Dict[str, Any] = {}
            lines = [p.strip() for p in props.split("\n") if p.strip()]
            for line in lines:
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip()
                    raw_props[key] = val
            if raw:
                service_info["properties"] = raw_props
            else:
                processed = self._process_raw_properties(raw_props)
                service_info["properties"] = processed
        except Exception:
            logging.exception("Error extracting service info")
            return {}
        return service_info

    def _process_raw_properties(
        self, raw_props: Dict[str, str]
    ) -> Dict[str, Any]:
        processed: Dict[str, Any] = {}
        for key, val in raw_props.items():
            processed[key] = val
            if key == "ExecStart":
                # this is a struct, we need to deconstruct it
                match = re.search(r"argv\[\]=([^;]+);", val)
                if match is not None:
                    processed[key] = match.group(1).strip()
            elif key == "EnvironmentFiles":
                if val:
                    processed[key] = val.split()[0]
            elif key in ["Requires", "After", "SupplementaryGroups"]:
                vals = [v.strip() for v in val.split() if v.strip()]
                processed[key] = vals
        return processed

class SystemdDbusProvider(BaseProvider):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.dbus_mgr: DbusManager = self.server.lookup_component(
            "dbus_manager")
        self.login_mgr: Optional[ProxyInterface] = None
        self.props: List[Tuple[ProxyInterface, Callable]] = []

    async def initialize(self) -> None:
        if not self.dbus_mgr.is_connected():
            self.server.add_warning(
                "[machine]: DBus Connection Not available, systemd "
                " service tracking and actions are disabled")
            return
        # Get the systemd manager interface
        self.systemd_mgr = await self.dbus_mgr.get_interface(
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager"
        )
        # Check for systemd PolicyKit Permissions
        await self.dbus_mgr.check_permission(
            "org.freedesktop.systemd1.manage-units",
            "System Service Management (start, stop, restart) "
            "will be disabled")
        if self.shutdown_action == "poweroff":
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.power-off",
                "The shutdown API will be disabled"
            )
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.power-off-multiple-sessions",
                "The shutdown API will be disabled if multiple user "
                "sessions are open."
            )
        else:
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.halt",
                "The shutdown API will be disabled"
            )
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.halt-multiple-sessions",
                "The shutdown API will be disabled if multiple user "
                "sessions are open."
            )
        try:
            # Get the login manaager interface
            self.login_mgr = await self.dbus_mgr.get_interface(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager"
            )
        except self.dbus_mgr.DbusError as e:
            logging.info(
                "Unable to acquire the systemd-logind D-Bus interface, "
                f"falling back to CLI Reboot and Shutdown APIs. {e}")
            self.login_mgr = None
        else:
            # Check for logind permissions
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.reboot",
                "The reboot API will be disabled"
            )
            await self.dbus_mgr.check_permission(
                "org.freedesktop.login1.reboot-multiple-sessions",
                "The reboot API will be disabled if multiple user "
                "sessions are open."
            )
        await self._detect_active_services()

    async def reboot(self) -> None:
        if self.login_mgr is None:
            await super().reboot()
        await self.login_mgr.call_reboot(False)  # type: ignore

    async def shutdown(self) -> None:
        if self.login_mgr is None:
            await super().shutdown()
        if self.shutdown_action == "poweroff":
            await self.login_mgr.call_power_off(False)  # type: ignore
        else:
            await self.login_mgr.call_halt(False)  # type: ignore

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        if not self.dbus_mgr.is_connected():
            raise self.server.error("DBus Not Connected, ", 503)
        mgr = self.systemd_mgr
        if not service_name.endswith(".service"):
            service_name += ".service"
        if action == "start":
            await mgr.call_start_unit(service_name, "replace")  # type: ignore
        elif action == "stop":
            await mgr.call_stop_unit(service_name, "replace")   # type: ignore
        elif action == "restart":
            await mgr.call_restart_unit(                        # type: ignore
                service_name, "replace")
        else:
            raise self.server.error(f"Invalid service action: {action}")

    async def check_virt_status(self) -> Dict[str, Any]:
        if not self.dbus_mgr.is_connected():
            return {
                'virt_type': "unknown",
                'virt_identifier': "unknown"
            }
        mgr = self.systemd_mgr
        virt_id = virt_type = "none"
        virt: str = await mgr.get_virtualization()  # type: ignore
        virt = virt.strip()
        if virt:
            virt_id = virt
            container_types = [
                "openvz", "lxc", "lxc-libvirt", "systemd-nspawn",
                "docker", "podman", "rkt", "wsl", "proot", "pouch"]
            if virt_id in container_types:
                virt_type = "container"
            else:
                virt_type = "vm"
            logging.info(
                f"Virtualized Environment Detected, Type: {virt_type} "
                f"id: {virt_id}")
        else:
            logging.info("No Virtualization Detected")
        return {
            'virt_type': virt_type,
            'virt_identifier': virt_id
        }

    async def _detect_active_services(self) -> None:
        # Get loaded service
        mgr = self.systemd_mgr
        machine: Machine = self.server.lookup_component("machine")
        units: List[str]
        units = await mgr.call_list_units_filtered(["loaded"])  # type: ignore
        for unit in units:
            name: str = unit[0].split('.')[0]
            if not machine.is_service_allowed(name):
                continue
            state: str = unit[3]
            substate: str = unit[4]
            dbus_path: str = unit[6]
            if name in self.available_services:
                continue
            self.available_services[name] = {
                'active_state': state,
                'sub_state': substate
            }
            # setup state monitoring
            props = await self.dbus_mgr.get_interface(
                "org.freedesktop.systemd1", dbus_path,
                "org.freedesktop.DBus.Properties"
            )
            prop_callback = self._create_properties_callback(name)
            self.props.append((props, prop_callback))
            props.on_properties_changed(  # type: ignore
                prop_callback)

    def _create_properties_callback(self, name) -> Callable:
        def prop_wrapper(dbus_obj: str,
                         changed_props: Dict[str, Variant],
                         invalid_props: Dict[str, Variant]
                         ) -> None:
            if dbus_obj != 'org.freedesktop.systemd1.Unit':
                return
            self._on_service_update(name, changed_props)
        return prop_wrapper

    def _on_service_update(self,
                           service_name: str,
                           changed_props: Dict[str, Variant]
                           ) -> None:
        if service_name not in self.available_services:
            return
        svc = self.available_services[service_name]
        notify = False
        if "ActiveState" in changed_props:
            state: str = changed_props['ActiveState'].value
            if state != svc['active_state']:
                notify = True
                svc['active_state'] = state
        if "SubState" in changed_props:
            state = changed_props['SubState'].value
            if state != svc['sub_state']:
                notify = True
                svc['sub_state'] = state
        if notify:
            self.server.send_event("machine:service_state_changed",
                                   {service_name: dict(svc)})

    async def extract_service_info(
        self,
        service_name: str,
        pid: int,
        properties: Optional[List[str]] = None,
        raw: bool = False
    ) -> Dict[str, Any]:
        if not hasattr(self, "systemd_mgr"):
            return {}
        mgr = self.systemd_mgr
        service_info: Dict[str, Any] = {}
        expected_name = f"{service_name}.service"
        if properties is None:
            properties = SERVICE_PROPERTIES
        try:
            dbus_path: str
            dbus_path = await mgr.call_get_unit_by_pid(pid)  # type: ignore
            bus = "org.freedesktop.systemd1"
            unit_intf, svc_intf = await self.dbus_mgr.get_interfaces(
                "org.freedesktop.systemd1", dbus_path,
                [f"{bus}.Unit", f"{bus}.Service"]
            )
            unit_name = await unit_intf.get_id()  # type: ignore
            service_info["unit_name"] = unit_name
            service_info["is_default"] = True
            service_info["manager"] = "systemd"
            if unit_name != expected_name:
                service_info["is_default"] = False
                logging.info(
                    f"Detected alternate unit name for {service_name}: "
                    f"{unit_name}"
                )
            raw_props: Dict[str, Any] = {}
            for key in properties:
                snake_key = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key).lower()
                func = getattr(unit_intf, f"get_{snake_key}", None)
                if func is None:
                    func = getattr(svc_intf, f"get_{snake_key}", None)
                    if func is None:
                        continue
                val = await func()
                raw_props[key] = val
            if raw:
                service_info["properties"] = raw_props
            else:
                processed = self._process_raw_properties(raw_props)
                service_info["properties"] = processed
        except Exception:
            logging.exception("Error Extracting Service Info")
            return {}
        return service_info

    def _process_raw_properties(
        self, raw_props: Dict[str, Any]
    ) -> Dict[str, Any]:
        processed: Dict[str, Any] = {}
        for key, val in raw_props.items():
            if key == "ExecStart":
                try:
                    val = " ".join(val[0][1])
                except Exception:
                    pass
            elif key == "EnvironmentFiles":
                try:
                    val = val[0][0]
                except Exception:
                    pass
            processed[key] = val
        return processed

# for docker klipper-moonraker image multi-service managing
# since in container, all command is launched by normal user,
# sudo_cmd is not needed.
class SupervisordCliProvider(BaseProvider):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.spv_conf: str = config.get("supervisord_config_path", "")

    async def initialize(self) -> None:
        await self._detect_active_services()
        keys = ' '.join(list(self.available_services.keys()))
        if self.spv_conf:
            cmd = f"supervisorctl -c {self.spv_conf} status {keys}"
        else:
            cmd = f"supervisorctl status {keys}"
        self.svc_cmd = self.shell_cmd.build_shell_command(cmd)
        await self._update_service_status(0, notify=True)
        pstats: ProcStats = self.server.lookup_component('proc_stats')
        pstats.register_stat_callback(self._update_service_status)

    async def do_service_action(
        self, action: str, service_name: str
    ) -> None:
        # slow reaction for supervisord, timeout set to 6.0
        await self._exec_supervisorctl_command(
            f"{action} {service_name}", timeout=6.
        )

    async def _exec_supervisorctl_command(
        self,
        args: str,
        tries: int = 1,
        timeout: float = 2.,
        success_codes: Optional[List[int]] = None
    ) -> str:
        if self.spv_conf:
            cmd = f"supervisorctl -c {self.spv_conf} {args}"
        else:
            cmd = f"supervisorctl {args}"
        return await self.shell_cmd.exec_cmd(
            cmd, proc_input=None, log_complete=False, retries=tries,
            timeout=timeout, success_codes=success_codes
        )

    def _get_active_state(self, sub_state: str) -> str:
        if sub_state == "stopping":
            return "deactivating"
        elif sub_state == "running":
            return "active"
        else:
            return "inactive"

    async def _detect_active_services(self) -> None:
        machine: Machine = self.server.lookup_component("machine")
        units: Dict[str, Any] = await self._get_process_info()
        for unit, info in units.items():
            if machine.is_service_allowed(unit):
                self.available_services[unit] = {
                    'active_state': self._get_active_state(info["state"]),
                    'sub_state': info["state"]
                }

    async def _get_process_info(
        self, process_names: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        units: Dict[str, Any] = {}
        cmd = "status"
        if process_names is not None:
            cmd = f"status {' '.join(process_names)}"
        try:
            resp = await self._exec_supervisorctl_command(
                cmd, timeout=6., success_codes=[0, 3]
            )
            lines = [line.strip() for line in resp.split("\n") if line.strip()]
        except Exception:
            return {}
        for line in lines:
            parts = line.split()
            name: str = parts[0]
            state: str = parts[1].lower()
            if state == "running" and len(parts) >= 6:
                units[name] = {
                    "state": state,
                    "pid": int(parts[3].rstrip(",")),
                    "uptime": parts[5]
                }
            else:
                units[name] = {
                    "state": parts[1].lower()
                }
        return units

    async def _update_service_status(self,
                                     sequence: int,
                                     notify: bool = True
                                     ) -> None:
        if sequence % 2:
            # Update every other sequence
            return
        svcs = list(self.available_services.keys())
        try:
            # slow reaction for supervisord, timeout set to 6.0
            resp = await self.svc_cmd.run_with_response(
                log_complete=False, timeout=6., success_codes=[0, 3]
            )
            resp_l = resp.strip().split("\n")  # drop lengend
            for svc, state in zip(svcs, resp_l):
                sub_state = state.split()[1].lower()
                new_state: Dict[str, str] = {
                    'active_state': self._get_active_state(sub_state),
                    'sub_state': sub_state
                }
                if self.available_services[svc] != new_state:
                    self.available_services[svc] = new_state
                    if notify:
                        self.server.send_event(
                            "machine:service_state_changed",
                            {svc: new_state})
        except Exception:
            logging.exception("Error processing service state update")

    async def _find_service_by_pid(
        self, expected_name: str, pid: int
    ) -> Dict[str, Any]:
        service_info: Dict[str, Any] = {}
        for _ in range(5):
            proc_info = await self._get_process_info(
                list(self.available_services.keys())
            )
            service_info["unit_name"] = expected_name
            service_info["is_default"] = True
            service_info["manager"] = "supervisord"
            need_retry = False
            for name, info in proc_info.items():
                if "pid" not in info:
                    need_retry |= info["state"] == "starting"
                elif info["pid"] == pid:
                    if name != expected_name:
                        service_info["unit_name"] = name
                        service_info["is_default"] = False
                        logging.info(
                            "Detected alternate unit name for "
                            f"{expected_name}: {name}"
                        )
                    return service_info
            if need_retry:
                await asyncio.sleep(1.)
            else:
                break
        return {}

    async def extract_service_info(
        self,
        service: str,
        pid: int,
        properties: Optional[List[str]] = None,
        raw: bool = False
    ) -> Dict[str, Any]:
        service_info = await self._find_service_by_pid(service, pid)
        if not service_info:
            logging.info(
                f"Unable to locate service info for {service}, pid: {pid}"
            )
            return {}
        # locate supervisord.conf
        if self.spv_conf:
            spv_path = pathlib.Path(self.spv_conf)
            if not spv_path.is_file():
                logging.info(
                    f"Invalid supervisord configuration file: {self.spv_conf}"
                )
                return service_info
        else:
            default_config_locations = [
                "/etc/supervisord.conf",
                "/etc/supervisor/supervisord.conf"
            ]
            for conf_path in default_config_locations:
                spv_path = pathlib.Path(conf_path)
                if spv_path.is_file():
                    break
            else:
                logging.info("Failed to locate supervisord.conf")
                return service_info
        spv_config = configparser.ConfigParser(interpolation=None)
        spv_config.read_string(spv_path.read_text())
        unit = service_info["unit_name"]
        section_name = f"program:{unit}"
        if not spv_config.has_section(section_name):
            logging.info(
                f"Unable to location supervisor section {section_name}"
            )
            return service_info
        service_info["properties"] = dict(spv_config[section_name])
        return service_info


# Install validation
INSTALL_VERSION = 1
SERVICE_VERSION = 1

SYSTEMD_UNIT = \
"""
# systemd service file for moonraker
[Unit]
Description=API Server for Klipper SV%d
Requires=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=%s
SupplementaryGroups=moonraker-admin
RemainAfterExit=yes
EnvironmentFile=%s
ExecStart=%s $MOONRAKER_ARGS
Restart=always
RestartSec=10
"""  # noqa: E122

TEMPLATE_NAME = "password_request.html"

class ValidationError(Exception):
    pass

class InstallValidator:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.config = config
        self.server.load_component(config, "template")
        self.force_validation = config.getboolean("force_validation", False)
        self.sc_enabled = config.getboolean("validate_service", True)
        self.cc_enabled = config.getboolean("validate_config", True)
        app_args = self.server.get_app_args()
        self.data_path = pathlib.Path(app_args["data_path"])
        self._update_backup_path()
        self.data_path_valid = True
        self._sudo_requested = False
        self.announcement_id = ""
        self.validation_enabled = False

    def _update_backup_path(self) -> None:
        str_time = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        if not hasattr(self, "backup_path"):
            self.backup_path = self.data_path.joinpath(f"backup/{str_time}")
        elif not self.backup_path.exists():
            self.backup_path = self.data_path.joinpath(f"backup/{str_time}")

    async def validation_init(self) -> None:
        db: MoonrakerDatabase = self.server.lookup_component("database")
        install_ver: int = await db.get_item(
            "moonraker", "validate_install.install_version", 0
        )
        if install_ver < INSTALL_VERSION:
            logging.info("Validation version in database out of date")
            self.validation_enabled = True
        else:
            msg = "Installation version in database up to date"
            if self.force_validation:
                msg += ", force is enabled"
            logging.info(msg)
            self.validation_enabled = self.force_validation
        is_bkp_cfg = self.server.get_app_args().get("is_backup_config", False)
        if self.validation_enabled and is_bkp_cfg:
            self.server.add_warning(
                "Backup configuration loaded, aborting install validation. "
                "Please correct the configuration issue and restart moonraker."
            )
            self.validation_enabled = False
            return

    async def perform_validation(self) -> bool:
        db: MoonrakerDatabase = self.server.lookup_component("database")
        if not self.validation_enabled:
            return False
        fm: FileManager = self.server.lookup_component("file_manager")
        need_restart: bool = False
        has_error: bool = False
        try:
            name = "service"
            need_restart = await self._check_service_file()
            name = "config"
            need_restart |= await self._check_configuration()
        except asyncio.CancelledError:
            raise
        except ValidationError as ve:
            has_error = True
            self.server.add_warning(str(ve))
            fm.disable_write_access()
        except Exception as e:
            has_error = True
            msg = f"Failed to validate {name}: {e}"
            logging.exception(msg)
            self.server.add_warning(msg, log=False)
            fm.disable_write_access()
        else:
            self.validation_enabled = False
            await db.insert_item(
                "moonraker", "validate_install.install_version", INSTALL_VERSION
            )
        if not has_error and need_restart:
            machine: Machine = self.server.lookup_component("machine")
            machine.restart_moonraker_service()
            return True
        return False

    async def _check_service_file(self) -> bool:
        if not self.sc_enabled:
            return False
        machine: Machine = self.server.lookup_component("machine")
        if machine.is_inside_container():
            raise ValidationError(
                "Moonraker instance running inside a container, "
                "cannot validate service file."
            )
        if machine.get_provider_type() == "none":
            raise ValidationError(
                "No machine provider configured, cannot validate service file."
            )
        logging.info("Performing Service Validation...")
        app_args = self.server.get_app_args()
        svc_info = machine.get_moonraker_service_info()
        if not svc_info:
            raise ValidationError(
                "Unable to retrieve Moonraker service info.  Service file "
                "must be updated manually."
            )
        props: Dict[str, str] = svc_info.get("properties", {})
        if "FragmentPath" not in props:
            raise ValidationError(
                "Unable to locate path to Moonraker's service unit. Service "
                "file must be must be updated manually."
            )
        desc = props.get("Description", "")
        ver_match = re.match(r"API Server for Klipper SV(\d+)", desc)
        if ver_match is not None and int(ver_match.group(1)) == SERVICE_VERSION:
            logging.info("Service file validated and up to date")
            return False
        unit: str = svc_info.get("unit_name", "").split(".", 1)[0]
        if not unit:
            raise ValidationError(
                "Unable to retrieve service unit name.  Service file "
                "must be updated manually."
            )
        if unit != "moonraker":
            logging.info(f"Custom service file detected: {unit}")
            # Not using he default unit name
            if app_args["is_default_data_path"] and self.data_path_valid:
                # No datapath set, create a new, unique data path
                df = f"~/{unit}_data"
                match = re.match(r"moonraker[-_]?(\d+)", unit)
                if match is not None:
                    df = f"~/printer_{match.group(1)}_data"
                new_dp = pathlib.Path(df).expanduser().resolve()
                if new_dp.exists() and not self._check_path_bare(new_dp):
                    raise ValidationError(
                        f"Cannot resolve data path for custom unit '{unit}', "
                        f"data path '{new_dp}' already exists.  Service file "
                        "must be updated manually."
                    )

                # If the current path is bare we can remove it
                if (
                    self.data_path.exists() and
                    self._check_path_bare(self.data_path)
                ):
                    shutil.rmtree(self.data_path)
                self.data_path = new_dp
                if not self.data_path.exists():
                    logging.info(f"New data path created at {self.data_path}")
                    self.data_path.mkdir()
                # A non-default datapath requires successful update of the
                # service
                self.data_path_valid = False
        user: str = props["User"]
        has_sudo = False
        if await machine.check_sudo_access():
            has_sudo = True
            logging.info("Moonraker has sudo access")
        elif user == "pi" and machine.sudo_password is None:
            machine.sudo_password = "raspberry"
            has_sudo = await machine.check_sudo_access()
        if not has_sudo:
            self._request_sudo_access()
            raise ValidationError(
                "Moonraker requires sudo permission to update the system "
                "service. Please check your notifications for further "
                "intructions."
            )
        self._sudo_requested = False
        svc_dest = pathlib.Path(props["FragmentPath"])
        tmp_svc = pathlib.Path(
            tempfile.gettempdir()
        ).joinpath(f"{unit}-tmp.svc")
        # Create local environment file
        sysd_data = self.data_path.joinpath("systemd")
        if not sysd_data.exists():
            sysd_data.mkdir()
        env_file = sysd_data.joinpath("moonraker.env")
        env_vars: Dict[str, str] = {
            "MOONRAKER_DATA_PATH": str(self.data_path)
        }
        cfg_file = pathlib.Path(app_args["config_file"])
        fm: FileManager = self.server.lookup_component("file_manager")
        cfg_path = fm.get_directory("config")
        log_path = fm.get_directory("logs")
        if not cfg_path or not cfg_file.parent.samefile(cfg_path):
            env_vars["MOONRAKER_CONFIG_PATH"] = str(cfg_file)
        elif cfg_file.name != "moonraker.conf":
            cfg_file = self.data_path.joinpath(f"config/{cfg_file.name}")
            env_vars["MOONRAKER_CONFIG_PATH"] = str(cfg_file)
        if not app_args["log_file"]:
            #  No log file configured
            env_vars["MOONRAKER_DISABLE_FILE_LOG"] = "y"
        else:
            # Log file does not exist in log path
            log_file = pathlib.Path(app_args["log_file"])
            if not log_path or not log_file.parent.samefile(log_path):
                env_vars["MOONRAKER_LOG_PATH"] = str(log_file)
            elif log_file.name != "moonraker.log":
                cfg_file = self.data_path.joinpath(f"logs/{log_file.name}")
                env_vars["MOONRAKER_LOG_PATH"] = str(log_file)
        # backup existing service files
        self._update_backup_path()
        svc_bkp_path = self.backup_path.joinpath("service")
        os.makedirs(str(svc_bkp_path), exist_ok=True)
        if env_file.exists():
            env_bkp = svc_bkp_path.joinpath(env_file.name)
            shutil.copy2(str(env_file), str(env_bkp))
        service_bkp = svc_bkp_path.joinpath(svc_dest.name)
        shutil.copy2(str(svc_dest), str(service_bkp))
        # write temporary service file
        src_path = source_info.source_path()
        exec_path = pathlib.Path(sys.executable)
        py_exec = exec_path.parent.joinpath("python")
        if exec_path.name == "python" or py_exec.is_file():
            # Default to loading via the python executable.  This
            # makes it possible to switch between git repos, pip
            # releases and git releases without reinstalling the
            # service.
            exec_path = py_exec
            env_vars["MOONRAKER_ARGS"] = "-m moonraker"
        if not source_info.is_dist_package():
            # This module isn't in site/dist packages,
            # add PYTHONPATH env variable
            env_vars["PYTHONPATH"] = str(src_path)
        tmp_svc.write_text(
            SYSTEMD_UNIT
            % (SERVICE_VERSION, user, env_file, exec_path)
        )
        try:
            # write new environment
            envout = "\n".join(f"{key}=\"{val}\"" for key, val in env_vars.items())
            env_file.write_text(envout)
            await machine.exec_sudo_command(
                f"cp -f {tmp_svc} {svc_dest}", tries=5, timeout=60.)
            await machine.exec_sudo_command(
                "systemctl daemon-reload", tries=5, timeout=60.
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Failed to update moonraker service unit")
            raise ValidationError(
                f"Failed to update service unit file '{svc_dest}'. Update must "
                f"be performed manually."
            ) from None
        finally:
            tmp_svc.unlink()
        self.data_path_valid = True
        self.sc_enabled = False
        return True

    def _check_path_bare(self, path: pathlib.Path) -> bool:
        empty: bool = True
        if not path.exists():
            return True
        for item in path.iterdir():
            if (
                item.is_file() or
                item.is_symlink() or
                item.name not in ["gcodes", "config", "logs", "certs"]
            ):
                empty = False
                break
            if item.is_dir() and next(item.iterdir(), None) is not None:
                empty = False
                break
        return empty

    def _link_data_subfolder(
        self,
        folder_name: str,
        source_dir: Union[str, pathlib.Path],
        exist_ok: bool = False
    ) -> None:
        if isinstance(source_dir, str):
            source_dir = pathlib.Path(source_dir).expanduser().resolve()
        subfolder = self.data_path.joinpath(folder_name)
        if not source_dir.exists():
            logging.info(
                f"Source path '{source_dir}' does not exist.  Falling "
                f"back to default folder {subfolder}"
            )
            return
        if not source_dir.is_dir():
            raise ValidationError(
                f"Failed to link subfolder '{folder_name}' to source path "
                f"'{source_dir}'.  The requested path is not a valid directory."
            )
        if subfolder.is_symlink():
            if not subfolder.samefile(source_dir):
                if exist_ok:
                    logging.info(
                        f"Folder {subfolder} already linked, aborting link "
                        f"to {source_dir}"
                    )
                    return
                raise ValidationError(
                    f"Failed to link subfolder '{folder_name}' to "
                    f"'{source_dir}'.  '{folder_name}' already exists and is "
                    f"linked to {subfolder}.  This conflict requires "
                    "manual resolution."
                )
            return
        if not subfolder.exists():
            subfolder.symlink_to(source_dir)
            return
        if subfolder.is_dir() and next(subfolder.iterdir(), None) is None:
            subfolder.rmdir()
            subfolder.symlink_to(source_dir)
            return
        if exist_ok:
            logging.info(
                f"Path at {subfolder} exists, aborting link to {source_dir}"
            )
            return
        raise ValidationError(
            f"Failed to link subfolder '{folder_name}' to '{source_dir}'.  "
            f"Folder '{folder_name}' already exists.  This conflict requires "
            "manual resolution."
        )

    def _link_data_file(
        self,
        data_file: Union[str, pathlib.Path],
        target: Union[str, pathlib.Path]
    ) -> None:
        if isinstance(data_file, str):
            data_file = pathlib.Path(data_file)
        if isinstance(target, str):
            target = pathlib.Path(target)
        target = target.expanduser().resolve()
        if not target.exists():
            logging.info(
                f"Target file {target} does not exist.  Aborting symbolic "
                f"link to {data_file.name}."
            )
            return
        if not target.is_file():
            raise ValidationError(
                f"Failed to link data file {data_file.name}.  Target "
                f"{target} is not a valid file."
            )
        if data_file.is_symlink():
            if not data_file.samefile(target):
                raise ValidationError(
                    f"Failed to link data file {data_file.name}.  Link "
                    f"to {data_file.resolve()} already exists. This conflict "
                    "must be resolved manually."
                )
            return
        if not data_file.exists():
            data_file.symlink_to(target)
            return
        raise ValidationError(
            f"Failed to link data file {data_file.name}.  File already exists. "
            f"This conflict must be resolved manually."
        )

    async def _check_configuration(self) -> bool:
        if not self.cc_enabled or not self.data_path_valid:
            return False
        db: MoonrakerDatabase = self.server.lookup_component("database")
        cfg_source = cast(FileSourceWrapper, self.config.get_source())
        cfg_source.backup_source()
        try:
            # write current configuration to backup path
            self._update_backup_path()
            cfg_bkp_path = self.backup_path.joinpath("config")
            os.makedirs(str(cfg_bkp_path), exist_ok=True)
            await cfg_source.write_config(cfg_bkp_path)
            # Create symbolic links for configured folders
            server_cfg = self.config["server"]

            db_cfg = self.config["database"]
            # symlink database path first
            db_path = db_cfg.get("database_path", None)
            default_db = pathlib.Path("~/.moonraker_database").expanduser()
            if db_path is None and default_db.exists():
                self._link_data_subfolder(
                    "database", default_db, exist_ok=True
                )
            elif db_path is not None:
                self._link_data_subfolder("database", db_path)
                cfg_source.remove_option("database", "database_path")

            fm_cfg = self.config["file_manager"]
            cfg_path = fm_cfg.get("config_path", None)
            if cfg_path is None:
                cfg_path = server_cfg.get("config_path", None)
            if cfg_path is not None:
                self._link_data_subfolder("config", cfg_path)
                cfg_source.remove_option("server", "config_path")
                cfg_source.remove_option("file_manager", "config_path")

            log_path = fm_cfg.get("log_path", None)
            if log_path is None:
                log_path = server_cfg.get("log_path", None)
            if log_path is not None:
                self._link_data_subfolder("logs", log_path)
                cfg_source.remove_option("server", "log_path")
                cfg_source.remove_option("file_manager", "log_path")

            gc_path: Optional[str] = await db.get_item(
                "moonraker", "file_manager.gcode_path", None
            )
            if gc_path is not None:
                self._link_data_subfolder("gcodes", gc_path)
                db.delete_item("moonraker", "file_manager.gcode_path")

            # Link individual files
            secrets_path = self.config["secrets"].get("secrets_path", None)
            if secrets_path is not None:
                secrets_dest = self.data_path.joinpath("moonraker.secrets")
                self._link_data_file(secrets_dest, secrets_path)
                cfg_source.remove_option("secrets", "secrets_path")
            certs_path = self.data_path.joinpath("certs")
            if not certs_path.exists():
                certs_path.mkdir()
            ssl_cert = server_cfg.get("ssl_certificate_path", None)
            if ssl_cert is not None:
                cert_dest = certs_path.joinpath("moonraker.cert")
                self._link_data_file(cert_dest, ssl_cert)
                cfg_source.remove_option("server", "ssl_certificate_path")
            ssl_key = server_cfg.get("ssl_key_path", None)
            if ssl_key is not None:
                key_dest = certs_path.joinpath("moonraker.key")
                self._link_data_file(key_dest, ssl_key)
                cfg_source.remove_option("server", "ssl_key_path")

            # Remove deprecated debug options
            if server_cfg.has_option("enable_debug_logging"):
                cfg_source.remove_option("server", "enable_debug_logging")
            um_cfg = server_cfg["update_manager"]
            if um_cfg.has_option("enable_repo_debug"):
                cfg_source.remove_option("update_manager", "enable_repo_debug")
        except Exception:
            cfg_source.cancel()
            raise
        finally:
            self.cc_enabled = False
        return await cfg_source.save()

    def _request_sudo_access(self) -> None:
        if self._sudo_requested:
            return
        self._sudo_requested = True
        auth: Optional[Authorization]
        auth = self.server.lookup_component("authorization", None)
        if auth is not None:
            # Bypass authentication requirements
            auth.register_permited_path("/machine/sudo/password")
        machine: Machine = self.server.lookup_component("machine")
        machine.register_sudo_request(
            self._on_password_received,
            "Sudo password required to update Moonraker's systemd service."
        )
        if not machine.public_ip:
            async def wrapper(pub_ip):
                if not pub_ip:
                    return
                await self.remove_announcement()
                self._announce_sudo_request()
            self.server.register_event_handler(
                "machine:public_ip_changed", wrapper
            )
        self._announce_sudo_request()

    def _announce_sudo_request(self) -> None:
        machine: Machine = self.server.lookup_component("machine")
        host_info = self.server.get_host_info()
        host_addr: str = host_info["address"]
        if host_addr.lower() not in ["all", "0.0.0.0", "::"]:
            address = host_addr
        else:
            address = machine.public_ip
        if not address:
            address = f"{host_info['hostname']}.local"
        elif ":" in address:
            # ipv6 address
            address = f"[{address}]"
        app: MoonrakerApp = self.server.lookup_component("application")
        scheme = "https" if app.https_enabled() else "http"
        host_info = self.server.get_host_info()
        port = host_info["port"]
        url = f"{scheme}://{address}:{port}/"
        ancmp: Announcements = self.server.lookup_component("announcements")
        entry = ancmp.add_internal_announcement(
            "Sudo Password Required",
            "Moonraker requires sudo access to finish updating. "
            "Please click on the attached link and follow the "
            "instructions.",
            url, "high", "machine"
        )
        self.announcement_id = entry.get("entry_id", "")
        gc_announcement = (
            "!! ATTENTION: Moonraker requires sudo access to complete "
            "the update.  Go to the following URL and provide your linux "
            f"password: {url}"
        )
        self.server.send_event("server:gcode_response", gc_announcement)

    async def remove_announcement(self) -> None:
        if not self.announcement_id:
            return
        ancmp: Announcements = self.server.lookup_component("announcements")
        # remove stale announcement
        try:
            await ancmp.remove_announcement(self.announcement_id)
        except self.server.error:
            pass
        self.announcement_id = ""

    async def _on_password_received(self) -> Tuple[str, bool]:
        try:
            name = "Service"
            await self._check_service_file()
            name = "Config"
            await self._check_configuration()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.exception(f"{name} validation failed")
            raise self.server.error(
                f"{name} validation failed", 500
            ) from None
        await self.remove_announcement()
        db: MoonrakerDatabase = self.server.lookup_component("database")
        await db.insert_item(
            "moonraker", "validate_install.install_version", INSTALL_VERSION
        )
        self.validation_enabled = False
        return "System update complete.", True

def load_component(config: ConfigHelper) -> Machine:
    return Machine(config)
