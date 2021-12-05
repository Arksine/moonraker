# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import re
import json
import pathlib
import logging
import asyncio
import platform
import distro

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import shell_command
    SCMDComp = shell_command.ShellCommandFactory

ALLOWED_SERVICES = [
    "moonraker", "klipper", "webcamd", "MoonCord",
    "KlipperScreen", "moonraker-telegram-bot"
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
class Machine:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        dist_info: Dict[str, Any]
        dist_info = {'name': distro.name(pretty=True)}
        dist_info.update(distro.info())
        self.inside_container = False
        self.virt_id = "none"
        self.system_info: Dict[str, Any] = {
            'cpu_info': self._get_cpu_info(),
            'sd_info': self._get_sdcard_info(),
            'distribution': dist_info,
            'virtualization': self._check_inside_container()
        }
        self._update_log_rollover(log=True)
        self.available_services: Dict[str, Dict[str, str]] = {}

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
            "shutdown_machine", self.shutdown_machine)
        self.server.register_remote_method(
            "reboot_machine", self.reboot_machine)

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
        if not self.inside_container:
            await self._check_virt_status()
        await self._find_active_services()
        await self.parse_network_interfaces(notify=False)
        self._update_log_rollover()

    async def _handle_machine_request(self, web_request: WebRequest) -> str:
        ep = web_request.get_endpoint()
        if self.inside_container:
            raise self.server.error(
                f"Cannot {ep.split('/')[-1]} from within a "
                f"{self.virt_id} container")
        if ep == "/machine/shutdown":
            await self.shutdown_machine()
        elif ep == "/machine/reboot":
            await self.reboot_machine()
        else:
            raise self.server.error("Unsupported machine request")
        return "ok"

    async def shutdown_machine(self) -> None:
        await self._execute_cmd("sudo shutdown now")

    async def reboot_machine(self) -> None:
        await self._execute_cmd("sudo shutdown -r now")

    async def do_service_action(self,
                                action: str,
                                service_name: str
                                ) -> None:
        await self._execute_cmd(
            f'sudo systemctl {action} {service_name}')

    async def _handle_service_request(self, web_request: WebRequest) -> str:
        name: str = web_request.get('service')
        action = web_request.get_endpoint().split('/')[-1]
        if name == "moonraker":
            if action != "restart":
                raise self.server.error(
                    f"Service action '{action}' not available for moonraker")
            event_loop = self.server.get_event_loop()
            event_loop.register_callback(self.do_service_action, action, name)
        elif name in self.available_services:
            await self.do_service_action(action, name)
        else:
            if name in ALLOWED_SERVICES and \
                    name not in self.available_services:
                raise self.server.error(f"Service '{name}' not installed")
            raise self.server.error(
                f"Service '{name}' not allowed")
        return "ok"

    async def _handle_sysinfo_request(self,
                                      web_request: WebRequest
                                      ) -> Dict[str, Any]:
        return {'system_info': self.system_info}

    async def _execute_cmd(self, cmd: str) -> None:
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")
            raise

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
                        continue
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

    async def _find_active_services(self):
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command(
            "systemctl list-units --all --type=service --plain --no-legend")
        try:
            resp = await scmd.run_with_response()
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
        avail_list = list(self.available_services.keys())
        self.system_info['available_services'] = avail_list
        self.system_info['service_state'] = self.available_services
        await self.update_service_status(notify=False)
        self.init_evt.set()

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

        self.virt_id = virt_id
        return {
            'virt_type': virt_type,
            'virt_identifier': virt_id
        }

    async def _check_virt_status(self) -> None:
        # Fallback virtualization check
        virt_id = virt_type = "none"

        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')

        # Check for any form of virtualization.  This will report the innermost
        # virtualization type in the event that nested virtualization is used
        scmd = shell_cmd.build_shell_command("systemd-detect-virt")
        try:
            resp = await scmd.run_with_response()
        except shell_cmd.error:
            pass
        else:
            virt_id = resp.strip()

        if virt_id != "none":
            # Check explicitly for container virtualization
            scmd = shell_cmd.build_shell_command(
                "systemd-detect-virt --container")
            try:
                resp = await scmd.run_with_response()
            except shell_cmd.error:
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

        self.virt_id = virt_id
        self.system_info['virtualization'] = {
            'virt_type': virt_type,
            'virt_identifier': virt_id
        }

    async def update_service_status(self, notify: bool = True) -> None:
        if not self.available_services:
            return
        svcs = self.system_info['available_services']
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command(
            "systemctl show -p ActiveState,SubState --value "
            f"{' '.join(svcs)}")
        try:
            resp = await scmd.run_with_response(log_complete=False)
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

    async def parse_network_interfaces(self, notify: bool = True) -> None:
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command("ip -json address")
        network: Dict[str, Any] = {}
        try:
            resp = await scmd.run_with_response(log_complete=False)
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

def load_component(config: ConfigHelper) -> Machine:
    return Machine(config)
