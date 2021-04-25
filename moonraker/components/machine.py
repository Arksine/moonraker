# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import re
import pathlib
import logging
import platform
from tornado.ioloop import IOLoop

ALLOWED_SERVICES = ["moonraker", "klipper", "webcamd"]
SD_CID_PATH = "/sys/block/mmcblk0/device/cid"
SD_CSD_PATH = "/sys/block/mmcblk0/device/csd"
SD_MFGRS = {
    '1b': "Samsung",
    '03': "Sandisk"
}

class Machine:
    def __init__(self, config):
        self.server = config.get_server()
        self.system_info = {
            'cpu_info': self._get_cpu_info(),
            'sd_info': self._get_sdcard_info()
        }
        # Add system info to log rollover
        sys_info_msg = "\nSystem Info:"
        for header, info in self.system_info.items():
            sys_info_msg += f"\n\n***{header}***"
            for key, val in info.items():
                sys_info_msg += f"\n  {key}: {val}"
        self.server.add_log_rollover_item('system_info', sys_info_msg)

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

        # Register remote methods
        self.server.register_remote_method(
            "shutdown_machine", self.shutdown_machine)
        self.server.register_remote_method(
            "reboot_machine", self.reboot_machine)

    async def _handle_machine_request(self, web_request):
        ep = web_request.get_endpoint()
        if ep == "/machine/shutdown":
            await self.shutdown_machine()
        elif ep == "/machine/reboot":
            await self.reboot_machine()
        else:
            raise self.server.error("Unsupported machine request")
        return "ok"

    async def shutdown_machine(self):
        await self._execute_cmd("sudo shutdown now")

    async def reboot_machine(self):
        await self._execute_cmd("sudo shutdown -r now")

    async def do_service_action(self, action, service_name):
        await self._execute_cmd(
            f'sudo systemctl {action} {service_name}')

    async def _handle_service_request(self, web_request):
        name = web_request.get('service').lower()
        action = web_request.get_endpoint().split('/')[-1]
        if name == "moonraker":
            if action != "restart":
                raise self.server.error(
                    f"Service action '{action}' not available for moonraker")
            IOLoop.current().spawn_callback(
                self.do_service_action, action, name)
        elif name in ALLOWED_SERVICES:
            await self.do_service_action(action, name)
        else:
            raise self.server.error(
                f"Invalid argument recevied for 'name': {name}")
        return "ok"

    async def _handle_sysinfo_request(self, web_request):
        return {'system_info': self.system_info}

    async def _execute_cmd(self, cmd):
        shell_command = self.server.lookup_component('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")
            raise

    def get_system_info(self):
        return self.system_info

    def _get_sdcard_info(self):
        sd_info = {}
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
                max_block_len = 2**(csd_reg[5] & 0xF)
                c_size = ((csd_reg[6] & 0x3) << 10) | (csd_reg[7] << 2) | \
                    ((csd_reg[8] >> 6) & 0x3)
                c_mult_reg = ((csd_reg[9] & 0x3) << 1) | (csd_reg[10] >> 7)
                c_mult = 2**(c_mult_reg + 2)
                total_bytes = (c_size + 1) * c_mult * max_block_len
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

    def _get_cpu_info(self):
        cpu_file = pathlib.Path("/proc/cpuinfo")
        cpu_info = {
            'cpu_count': os.cpu_count(),
            'bits': platform.architecture()[0],
            'processor': platform.processor() or platform.machine(),
            'cpu_desc': "",
            'hardware_desc': "",
            'model': ""
        }
        if not cpu_file.exists():
            return cpu_info
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
            model_match = re.search(r"Model\s+:\s+(.+)", cpu_items[-1])
            if model_match is not None:
                cpu_info['model'] = model_match.group(1).strip()
        except Exception:
            logging.info("Error Reading /proc/cpuinfo")
        return cpu_info

def load_component(config):
    return Machine(config)
