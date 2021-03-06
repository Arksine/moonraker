# Moonraker Process Stat Tracking
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import time
import re
import os
import pathlib
import logging
from collections import deque
from tornado.ioloop import IOLoop, PeriodicCallback

VC_GEN_CMD_FILE = "/usr/bin/vcgencmd"
STATM_FILE_PATH = "/proc/self/smaps_rollup"
STAT_UPDATE_TIME_MS = 1000
REPORT_QUEUE_SIZE = 30
THROTTLE_CHECK_INTERVAL = 10

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
    def __init__(self, config):
        self.server = config.get_server()
        self.ioloop = IOLoop.current()
        self.stat_update_cb = PeriodicCallback(
            self._handle_stat_update, STAT_UPDATE_TIME_MS)
        self.vcgencmd = None
        if os.path.exists(VC_GEN_CMD_FILE):
            logging.info("Detected 'vcgencmd', throttle checking enabled")
            shell_command = self.server.load_plugin(config, "shell_command")
            self.vcgencmd = shell_command.build_shell_command(
                "vcgencmd get_throttled")
            self.server.register_notification("proc_stats:cpu_throttled")
        else:
            logging.info("Unable to find 'vcgencmd', throttle checking "
                         "disabled")
        self.smaps = pathlib.Path(STATM_FILE_PATH)
        self.server.register_endpoint(
            "/machine/proc_stats", ["GET"], self._handle_stat_request)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.proc_stat_queue = deque(maxlen=30)
        self.last_update_time = time.time()
        self.last_proc_time = time.process_time()
        self.throttled = False
        self.update_sequence = 0
        self.stat_update_cb.start()

    async def _handle_stat_request(self, web_request):
        ts = None
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
        return {
            'moonraker_stats': list(self.proc_stat_queue),
            'throttled_state': ts
        }

    async def _handle_shutdown(self):
        msg = "\nMoonraker System Usage Statistics:"
        for stats in self.proc_stat_queue:
            msg += f"\n{self._format_stats(stats)}"
        logging.info(msg)
        if self.vcgencmd is not None:
            ts = await self._check_throttled_state()
            logging.info(f"Throttled Flags: {' '.join(ts['flags'])}")

    async def _handle_stat_update(self):
        update_time = time.time()
        proc_time = time.process_time()
        time_diff = update_time - self.last_update_time
        usage = round((proc_time - self.last_proc_time) / time_diff * 100, 2)
        mem, mem_units = self._get_memory_usage()
        self.proc_stat_queue.append({
            "time": update_time,
            "cpu_usage": usage,
            "memory": mem,
            "mem_units": mem_units
        })
        self.last_update_time = update_time
        self.last_proc_time = proc_time
        self.update_sequence += 1
        if self.update_sequence == THROTTLE_CHECK_INTERVAL:
            self.update_sequence = 0
            if self.vcgencmd is not None:
                ts = await self._check_throttled_state()
                cur_throttled = ts['bits'] & 0xF
                if cur_throttled and not self.throttled:
                    logging.info(
                        f"CPU Throttled State Detected: {ts['flags']}")
                    self.server.send_event("proc_stats:cpu_throttled", ts)
                self.throttled = cur_throttled

    async def _check_throttled_state(self):
        try:
            resp = await self.vcgencmd.run_with_response(
                timeout=.5, quiet=True)
            ts = int(resp.strip().split("=")[-1], 16)
        except Exception:
            return {'bits': 0, 'flags': ["?"]}
        flags = []
        for flag, desc in THROTTLED_FLAGS.items():
            if flag & ts:
                flags.append(desc)
        return {'bits': ts, 'flags': flags}

    def _get_memory_usage(self):
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

    def _format_stats(self, stats):
        return f"System Time: {stats['time']:2f}, " \
               f"Usage: {stats['cpu_usage']}%, " \
               f"Memory: {stats['memory']} {stats['mem_units']}"

    def close(self):
        self.stat_update_cb.stop()

def load_plugin(config):
    return ProcStats(config)
