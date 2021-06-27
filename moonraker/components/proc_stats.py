# Moonraker Process Stat Tracking
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import time
import re
import os
import pathlib
import logging
from collections import deque
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.locks import Lock

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Deque,
    Any,
    Tuple,
    Optional,
    Dict,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import shell_command

VC_GEN_CMD_FILE = "/usr/bin/vcgencmd"
STATM_FILE_PATH = "/proc/self/smaps_rollup"
TEMPERATURE_PATH = "/sys/class/thermal/thermal_zone0/temp"
STAT_UPDATE_TIME_MS = 1000
REPORT_QUEUE_SIZE = 30
THROTTLE_CHECK_INTERVAL = 10
REPORT_BLOCKED_TIME = 5.

THROTTLED_FLAGS = {
    1: "Under-Voltage Detected",
    1 << 1: "Frequency Capped",
    1 << 2: "Currently Throttled",
    1 << 3: "Temperature Limit Active",
    1 << 16: "Previously Under-Volted",
    1 << 17: "Previously Frequency Capped",
    1 << 18: "Previously Throttled",
    1 << 19: "Previously Temperature Limited"
}

class ProcStats:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.ioloop = IOLoop.current()
        self.stat_update_cb = PeriodicCallback(
            self._handle_stat_update, STAT_UPDATE_TIME_MS)  # type: ignore
        self.vcgencmd: Optional[shell_command.ShellCommand] = None
        if os.path.exists(VC_GEN_CMD_FILE):
            logging.info("Detected 'vcgencmd', throttle checking enabled")
            shell_cmd: shell_command.ShellCommandFactory
            shell_cmd = self.server.load_component(config, "shell_command")
            self.vcgencmd = shell_cmd.build_shell_command(
                "vcgencmd get_throttled")
            self.server.register_notification("proc_stats:cpu_throttled")
        else:
            logging.info("Unable to find 'vcgencmd', throttle checking "
                         "disabled")
        self.temp_file = pathlib.Path(TEMPERATURE_PATH)
        self.smaps = pathlib.Path(STATM_FILE_PATH)
        self.server.register_endpoint(
            "/machine/proc_stats", ["GET"], self._handle_stat_request)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_notification("proc_stats:proc_stat_update")
        self.proc_stat_queue: Deque[Dict[str, Any]] = deque(maxlen=30)
        self.last_update_time = time.time()
        self.last_proc_time = time.process_time()
        self.throttle_check_lock = Lock()
        self.total_throttled: int = 0
        self.last_throttled: int = 0
        self.update_sequence: int = 0
        self.stat_update_cb.start()

    async def _handle_stat_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        ts: Optional[Dict[str, Any]] = None
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
        return {
            'moonraker_stats': list(self.proc_stat_queue),
            'throttled_state': ts,
            'cpu_temp': self._get_cpu_temperature()
        }

    async def _handle_shutdown(self) -> None:
        msg = "\nMoonraker System Usage Statistics:"
        for stats in self.proc_stat_queue:
            msg += f"\n{self._format_stats(stats)}"
        msg += f"\nCPU Temperature: {self._get_cpu_temperature()}"
        logging.info(msg)
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
            logging.info(f"Throttled Flags: {' '.join(ts['flags'])}")

    async def _handle_stat_update(self) -> None:
        update_time = time.time()
        proc_time = time.process_time()
        time_diff = update_time - self.last_update_time
        usage = round((proc_time - self.last_proc_time) / time_diff * 100, 2)
        if time_diff > REPORT_BLOCKED_TIME:
            logging.info(
                f"EVENT LOOP BLOCKED: {round(time_diff, 2)} seconds, "
                f"Moonraker Process Usage: {usage}%")
        mem, mem_units = self._get_memory_usage()
        cpu_temp = self._get_cpu_temperature()
        result = {
            "time": update_time,
            "cpu_usage": usage,
            "memory": mem,
            "mem_units": mem_units,
        }
        self.proc_stat_queue.append(result)
        self.server.send_event("proc_stats:proc_stat_update", {
            'moonraker_stats': result,
            'cpu_temp': cpu_temp
        })
        self.last_update_time = update_time
        self.last_proc_time = proc_time
        self.update_sequence += 1
        if self.update_sequence == THROTTLE_CHECK_INTERVAL:
            self.update_sequence = 0
            if self.vcgencmd is not None:
                ts = await self._check_throttled_state()
                cur_throttled = ts['bits']
                if cur_throttled & ~self.total_throttled:
                    self.server.add_log_rollover_item(
                        'throttled', f"CPU Throttled Flags: {ts['flags']}")
                if cur_throttled != self.last_throttled:
                    self.server.send_event("proc_stats:cpu_throttled", ts)
                self.last_throttled = cur_throttled
                self.total_throttled |= cur_throttled

    async def _check_throttled_state(self) -> Dict[str, Any]:
        async with self.throttle_check_lock:
            assert self.vcgencmd is not None
            try:
                resp = await self.vcgencmd.run_with_response(
                    timeout=.5, log_complete=False)
                ts = int(resp.strip().split("=")[-1], 16)
            except Exception:
                return {'bits': 0, 'flags': ["?"]}
            flags = []
            for flag, desc in THROTTLED_FLAGS.items():
                if flag & ts:
                    flags.append(desc)
            return {'bits': ts, 'flags': flags}

    def _get_memory_usage(self) -> Tuple[Optional[int], Optional[str]]:
        try:
            mem_data = self.smaps.read_text()
            rss_match = re.search(r"Rss:\s+(\d+)\s+(\w+)", mem_data)
            if rss_match is None:
                return None, None
            mem = int(rss_match.group(1))
            units = rss_match.group(2)
        except Exception:
            return None, None
        return mem, units

    def _get_cpu_temperature(self) -> Optional[float]:
        temp = None
        if self.temp_file.exists():
            try:
                res = int(self.temp_file.read_text().strip())
                temp = res / 1000.
            except Exception:
                return None
        return temp

    def _format_stats(self, stats: Dict[str, Any]) -> str:
        return f"System Time: {stats['time']:2f}, " \
               f"Usage: {stats['cpu_usage']}%, " \
               f"Memory: {stats['memory']} {stats['mem_units']}"

    def close(self) -> None:
        self.stat_update_cb.stop()

def load_component(config: ConfigHelper) -> ProcStats:
    return ProcStats(config)
