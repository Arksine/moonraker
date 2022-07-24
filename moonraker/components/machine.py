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
import distro

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from .shell_command import ShellCommandFactory as SCMDComp
    from .proc_stats import ProcStats
    from .dbus_manager import DbusManager
    from dbus_next.aio import ProxyInterface
    from dbus_next import Variant

ALLOWED_SERVICES = [
    "moonraker", "klipper", "webcamd", "MoonCord",
    "KlipperScreen", "moonraker-telegram-bot",
    "sonar", "crowsnest"
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

class Machine:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        dist_info: Dict[str, Any]
        dist_info = {'name': distro.name(pretty=True)}
        dist_info.update(distro.info())
        dist_info['release_info'] = distro.distro_release_info()
        self.inside_container = False
        self.system_info: Dict[str, Any] = {
            'python': {
                "version": sys.version_info,
                "version_string": sys.version.replace("\n", " ")
            },
            'cpu_info': self._get_cpu_info(),
            'sd_info': self._get_sdcard_info(),
            'distribution': dist_info,
            'virtualization': self._check_inside_container()
        }
        self._update_log_rollover(log=True)
        providers: Dict[str, type] = {
            "none": BaseProvider,
            "systemd_cli": SystemdCliProvider,
            "systemd_dbus": SystemdDbusProvider
        }
        ptype = config.get('provider', 'systemd_dbus')
        pclass = providers.get(ptype)
        if pclass is None:
            raise config.error(f"Invalid Provider: {ptype}")
        self.sys_provider: BaseProvider = pclass(config)
        logging.info(f"Using System Provider: {ptype}")

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

        self.server.register_notification("machine:service_state_changed")

        # Register remote methods
        self.server.register_remote_method(
            "shutdown_machine", self.sys_provider.shutdown)
        self.server.register_remote_method(
            "reboot_machine", self.sys_provider.reboot)

        # IP network shell commands
        shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')
        self.addr_cmd = shell_cmd.build_shell_command("ip -json address")
        iwgetbin = "/sbin/iwgetid"
        if not pathlib.Path(iwgetbin).exists():
            iwgetbin = "iwgetid"
        self.iwgetid_cmd = shell_cmd.build_shell_command(iwgetbin)
        self.init_evt = asyncio.Event()

    def _update_log_rollover(self, log: bool = False) -> None:
        sys_info_msg = "\nSystem Info:"
        for header, info in self.system_info.items():
            sys_info_msg += f"\n\n***{header}***"
            if not isinstance(info, dict):
                sys_info_msg += f"\n {repr(info)}"
            else:
                for key, val in info.items():
                    sys_info_msg += f"\n  {key}: {val}"
        self.server.add_log_rollover_item('system_info', sys_info_msg, log=log)

    async def wait_for_init(self, timeout: float = None) -> None:
        try:
            await asyncio.wait_for(self.init_evt.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def component_init(self):
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
        self.init_evt.set()

    async def _handle_machine_request(self, web_request: WebRequest) -> str:
        ep = web_request.get_endpoint()
        if self.inside_container:
            virt_id = self.system_info['virtualization'].get('virt_id', "none")
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

    async def _handle_service_request(self, web_request: WebRequest) -> str:
        name: str = web_request.get('service')
        action = web_request.get_endpoint().split('/')[-1]
        if name == "moonraker":
            if action != "restart":
                raise self.server.error(
                    f"Service action '{action}' not available for moonraker")
            event_loop = self.server.get_event_loop()
            event_loop.register_callback(self.do_service_action, action, name)
        elif self.sys_provider.is_service_available(name):
            await self.do_service_action(action, name)
        else:
            if name in ALLOWED_SERVICES:
                raise self.server.error(f"Service '{name}' not installed")
            raise self.server.error(
                f"Service '{name}' not allowed")
        return "ok"

    async def _handle_sysinfo_request(self,
                                      web_request: WebRequest
                                      ) -> Dict[str, Any]:
        return {'system_info': self.system_info}

    def get_system_info(self) -> Dict[str, Any]:
        return self.system_info

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
        try:
            # get network interfaces
            resp = await self.addr_cmd.run_with_response(log_complete=False)
            decoded = json.loads(resp)
            for interface in decoded:
                if (
                    interface['operstate'] != "UP" or
                    interface['link_type'] != "ether" or
                    'address' not in interface
                ):
                    continue
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
        if notify and network != prev_network:
            self.server.send_event("machine:net_state_changed", network)
        self.system_info['network'] = network

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
                if fam == "ipv6" and src_ip is None:
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

    def _find_public_ip(self) -> Optional[str]:
        #  Check for an IPv4 Source IP
        # NOTE: It should also be possible to extract this from
        # the routing table, ie: ip -json route
        # It would be an entry with a "gateway" with the lowest
        # metric.  Might also be able to get IPv6 info from this.
        # However, it would be better to use NETLINK for this rather
        # than run another shell command
        src_ip: Optional[str] = None
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(0)
            s.connect(('10.255.255.255', 1))
            src_ip = s.getsockname()[0]
        except Exception:
            pass
        finally:
            s.close()
        return src_ip

    async def _get_wifi_interfaces(self) -> Dict[str, Any]:
        # get wifi interfaces
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        wifi_intfs: Dict[str, Any] = {}
        try:
            resp = await self.iwgetid_cmd.run_with_response(log_complete=False)
        except shell_cmd.error:
            logging.exception("Failed to run 'iwgetid' command")
            return {}
        if resp:
            for line in resp.split("\n"):
                parts = line.strip().split(maxsplit=1)
                wifi_intfs[parts[0]] = parts[1].split(":")[-1].strip('"')
        return wifi_intfs

class BaseProvider:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.available_services: Dict[str, Dict[str, str]] = {}
        self.shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        await self.shell_cmd.exec_cmd(f"sudo shutdown now")

    async def reboot(self) -> None:
        await self.shell_cmd.exec_cmd(f"sudo shutdown -r now")

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        raise self.server.error("Serice Actions Not Available", 503)

    async def check_virt_status(self) -> Dict[str, Any]:
        return {
            'virt_type': "unknown",
            'virt_identifier': "unknown"
        }

    def is_service_available(self, service: str) -> bool:
        return service in self.available_services

    def get_available_services(self) -> Dict[str, Dict[str, str]]:
        return self.available_services

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
        await self.shell_cmd.exec_cmd(
            f'sudo systemctl {action} {service_name}')

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

    async def _detect_active_services(self):
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
            for allowed in ALLOWED_SERVICES:
                if sname.startswith(allowed):
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
        await self.dbus_mgr.check_permission(
            "org.freedesktop.login1.power-off",
            "The shutdown API will be disabled"
        )
        await self.dbus_mgr.check_permission(
            "org.freedesktop.login1.power-off-multiple-sessions",
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
        await self.login_mgr.call_power_off(False)  # type: ignore

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
        patterns = [f"{svc}*.service" for svc in ALLOWED_SERVICES]
        units = await mgr.call_list_units_by_patterns(  # type: ignore
            ["loaded"], patterns)
        for unit in units:
            name: str = unit[0].split('.')[0]
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


def load_component(config: ConfigHelper) -> Machine:
    return Machine(config)
