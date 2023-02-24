# Moonraker Process Stat Tracking
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import time
import re
import os
import pathlib
import logging
from collections import deque

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    Deque,
    Any,
    List,
    Tuple,
    Optional,
    Dict,
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from ..websockets import WebsocketManager
    from . import shell_command
    STAT_CALLBACK = Callable[[int], Optional[Awaitable]]

VC_GEN_CMD_FILE = "/usr/bin/vcgencmd"
STATM_FILE_PATH = "/proc/self/smaps_rollup"
NET_DEV_PATH = "/proc/net/dev"
TEMPERATURE_PATH = "/sys/class/thermal/thermal_zone0/temp"
CPU_STAT_PATH = "/proc/stat"
MEM_AVAIL_PATH = "/proc/meminfo"
STAT_UPDATE_TIME = 1.
REPORT_QUEUE_SIZE = 30
THROTTLE_CHECK_INTERVAL = 10
WATCHDOG_REFRESH_TIME = 2.
REPORT_BLOCKED_TIME = 4.

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
        self.event_loop = self.server.get_event_loop()
        self.watchdog = Watchdog(self)
        self.stat_update_timer = self.event_loop.register_timer(
            self._handle_stat_update)
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
        self.netdev_file = pathlib.Path(NET_DEV_PATH)
        self.cpu_stats_file = pathlib.Path(CPU_STAT_PATH)
        self.meminfo_file = pathlib.Path(MEM_AVAIL_PATH)
        self.server.register_endpoint(
            "/machine/proc_stats", ["GET"], self._handle_stat_request)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_notification("proc_stats:proc_stat_update")
        self.proc_stat_queue: Deque[Dict[str, Any]] = deque(maxlen=30)
        self.last_update_time = time.time()
        self.last_proc_time = time.process_time()
        self.throttle_check_lock = asyncio.Lock()
        self.total_throttled: int = 0
        self.last_throttled: int = 0
        self.update_sequence: int = 0
        self.last_net_stats: Dict[str, Dict[str, Any]] = {}
        self.last_cpu_stats: Dict[str, Tuple[int, int]] = {}
        self.cpu_usage: Dict[str, float] = {}
        self.memory_usage: Dict[str, int] = {}
        self.stat_callbacks: List[STAT_CALLBACK] = []

    async def component_init(self) -> None:
        self.stat_update_timer.start()
        self.watchdog.start()

    def register_stat_callback(self, callback: STAT_CALLBACK) -> None:
        self.stat_callbacks.append(callback)

    async def _handle_stat_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        ts: Optional[Dict[str, Any]] = None
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
        cpu_temp = await self.event_loop.run_in_thread(
            self._get_cpu_temperature)
        wsm: WebsocketManager = self.server.lookup_component("websockets")
        websocket_count = wsm.get_count()
        return {
            'moonraker_stats': list(self.proc_stat_queue),
            'throttled_state': ts,
            'cpu_temp': cpu_temp,
            'network': self.last_net_stats,
            'system_cpu_usage': self.cpu_usage,
            'system_uptime': time.clock_gettime(time.CLOCK_BOOTTIME),
            'system_memory': self.memory_usage,
            'websocket_connections': websocket_count
        }

    async def _handle_shutdown(self) -> None:
        msg = "\nMoonraker System Usage Statistics:"
        for stats in self.proc_stat_queue:
            msg += f"\n{self._format_stats(stats)}"
        cpu_temp = await self.event_loop.run_in_thread(
            self._get_cpu_temperature)
        msg += f"\nCPU Temperature: {cpu_temp}"
        logging.info(msg)
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
            logging.info(f"Throttled Flags: {' '.join(ts['flags'])}")

    async def _handle_stat_update(self, eventtime: float) -> float:
        update_time = eventtime
        proc_time = time.process_time()
        time_diff = update_time - self.last_update_time
        usage = round((proc_time - self.last_proc_time) / time_diff * 100, 2)
        cpu_temp, mem, mem_units, net = (
            await self.event_loop.run_in_thread(self._read_system_files)
        )
        for dev in net:
            bytes_sec = 0.
            if dev in self.last_net_stats:
                last_dev_stats = self.last_net_stats[dev]
                cur_total: int = net[dev]['rx_bytes'] + net[dev]['tx_bytes']
                last_total: int = last_dev_stats['rx_bytes'] + \
                    last_dev_stats['tx_bytes']
                bytes_sec = round((cur_total - last_total) / time_diff, 2)
            net[dev]['bandwidth'] = bytes_sec
        self.last_net_stats = net
        result = {
            'time': time.time(),
            'cpu_usage': usage,
            'memory': mem,
            'mem_units': mem_units
        }
        self.proc_stat_queue.append(result)
        wsm: WebsocketManager = self.server.lookup_component("websockets")
        websocket_count = wsm.get_count()
        self.server.send_event("proc_stats:proc_stat_update", {
            'moonraker_stats': result,
            'cpu_temp': cpu_temp,
            'network': net,
            'system_cpu_usage': self.cpu_usage,
            'system_memory': self.memory_usage,
            'websocket_connections': websocket_count
        })
        if not self.update_sequence % THROTTLE_CHECK_INTERVAL:
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
        for cb in self.stat_callbacks:
            ret = cb(self.update_sequence)
            if ret is not None:
                await ret
        self.last_update_time = update_time
        self.last_proc_time = proc_time
        self.update_sequence += 1
        return eventtime + STAT_UPDATE_TIME

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

    def _read_system_files(self) -> Tuple:
        mem, units = self._get_memory_usage()
        temp = self._get_cpu_temperature()
        net_stats = self._get_net_stats()
        self._update_cpu_stats()
        self._update_system_memory()
        return temp, mem, units, net_stats

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
        try:
            res = int(self.temp_file.read_text().strip())
            temp = res / 1000.
        except Exception:
            return None
        return temp

    def _get_net_stats(self) -> Dict[str, Any]:
        net_stats: Dict[str, Any] = {}
        try:
            ret = self.netdev_file.read_text()
            dev_info = re.findall(r"([\w]+):(.+)", ret)
            for (dev_name, stats) in dev_info:
                parsed_stats = stats.strip().split()
                net_stats[dev_name] = {
                    'rx_bytes': int(parsed_stats[0]),
                    'tx_bytes': int(parsed_stats[8]),
                    'rx_packets': int(parsed_stats[1]),
                    'tx_packets': int(parsed_stats[9]),
                    'rx_errs': int(parsed_stats[2]),
                    'tx_errs': int(parsed_stats[10]),
                    'rx_drop': int(parsed_stats[3]),
                    'tx_drop': int(parsed_stats[11])
                }
            return net_stats
        except Exception:
            return {}

    def _update_system_memory(self) -> None:
        mem_stats: Dict[str, Any] = {}
        try:
            ret = self.meminfo_file.read_text()
            total_match = re.search(r"MemTotal:\s+(\d+)", ret)
            avail_match = re.search(r"MemAvailable:\s+(\d+)", ret)
            if total_match is not None and avail_match is not None:
                mem_stats["total"] = int(total_match.group(1))
                mem_stats["available"] = int(avail_match.group(1))
                mem_stats["used"] = mem_stats["total"] - mem_stats["available"]
            self.memory_usage.update(mem_stats)
        except Exception:
            pass

    def _update_cpu_stats(self) -> None:
        try:
            cpu_usage: Dict[str, Any] = {}
            ret = self.cpu_stats_file.read_text()
            usage_info: List[str] = re.findall(r"cpu[^\n]+", ret)
            for cpu in usage_info:
                parts = cpu.split()
                name = parts[0]
                cpu_sum = sum([int(t) for t in parts[1:]])
                cpu_idle = int(parts[4])
                if name in self.last_cpu_stats:
                    last_sum, last_idle = self.last_cpu_stats[name]
                    cpu_delta = cpu_sum - last_sum
                    idle_delta = cpu_idle - last_idle
                    cpu_used = cpu_delta - idle_delta
                    cpu_usage[name] = round(
                        100 * (cpu_used / cpu_delta), 2)
                self.cpu_usage = cpu_usage
                self.last_cpu_stats[name] = (cpu_sum, cpu_idle)
        except Exception:
            pass

    def _format_stats(self, stats: Dict[str, Any]) -> str:
        return f"System Time: {stats['time']:2f}, " \
               f"Usage: {stats['cpu_usage']}%, " \
               f"Memory: {stats['memory']} {stats['mem_units']}"

    def log_last_stats(self, count: int = 1):
        count = min(len(self.proc_stat_queue), count)
        msg = ""
        for stats in list(self.proc_stat_queue)[-count:]:
            msg += f"\n{self._format_stats(stats)}"
        logging.info(msg)

    def close(self) -> None:
        self.stat_update_timer.stop()
        self.watchdog.stop()

class Watchdog:
    def __init__(self, proc_stats: ProcStats) -> None:
        self.proc_stats = proc_stats
        self.event_loop = proc_stats.event_loop
        self.blocked_count: int = 0
        self.last_watch_time: float = 0.
        self.watchdog_timer = self.event_loop.register_timer(
            self._watchdog_callback
        )

    def _watchdog_callback(self, eventtime: float) -> float:
        time_diff = eventtime - self.last_watch_time
        if time_diff > REPORT_BLOCKED_TIME:
            self.blocked_count += 1
            logging.info(
                f"EVENT LOOP BLOCKED: {round(time_diff, 2)} seconds"
                f", total blocked count: {self.blocked_count}")
            # delay the stat logging so we capture the CPU percentage after
            # the next cycle
            self.event_loop.delay_callback(
                .2, self.proc_stats.log_last_stats, 5)
        self.last_watch_time = eventtime
        return eventtime + WATCHDOG_REFRESH_TIME

    def start(self):
        if not self.watchdog_timer.is_running():
            self.last_watch_time = self.event_loop.get_loop_time()
            self.watchdog_timer.start()

    def stop(self):
        self.watchdog_timer.stop()

def load_component(config: ConfigHelper) -> ProcStats:
    return ProcStats(config)
