# Log Management
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import logging
import logging.handlers
import time
import os
import sys
import asyncio
from queue import SimpleQueue as Queue

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Optional,
    Awaitable,
    Dict,
    List,
    Any,
)

if TYPE_CHECKING:
    from .server import Server
    from .common import WebRequest
    from .klippy_connection import KlippyConnection

# Coroutine friendly QueueHandler courtesy of Martjin Pieters:
# https://www.zopatista.com/python/2019/05/11/asyncio-logging/
class LocalQueueHandler(logging.handlers.QueueHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Removed the call to self.prepare(), handle task cancellation
        try:
            self.enqueue(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.handleError(record)

# Timed Rotating File Handler, based on Klipper's implementation
class MoonrakerLoggingHandler(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, app_args: Dict[str, Any], **kwargs) -> None:
        super().__init__(app_args['log_file'], **kwargs)
        self.app_args = app_args
        self.rollover_info: Dict[str, str] = {}

    def set_rollover_info(self, name: str, item: str) -> None:
        self.rollover_info[name] = item

    def doRollover(self) -> None:
        super().doRollover()
        self.write_header()

    def write_header(self) -> None:
        if self.stream is None:
            return
        strtime = time.asctime(time.gmtime())
        header = f"{'-'*20} Log Start | {strtime} {'-'*20}\n"
        self.stream.write(header)
        app_section = "\n".join([f"{k}: {v}" for k, v in self.app_args.items()])
        self.stream.write(app_section + "\n")
        if self.rollover_info:
            lines = [line for line in self.rollover_info.values() if line]
            self.stream.write("\n".join(lines) + "\n")

class LogManager:
    def __init__(
        self, app_args: Dict[str, Any], startup_warnings: List[str]
    ) -> None:
        root_logger = logging.getLogger()
        while root_logger.hasHandlers():
            root_logger.removeHandler(root_logger.handlers[0])
        queue: Queue = Queue()
        queue_handler = LocalQueueHandler(queue)
        root_logger.addHandler(queue_handler)
        root_logger.setLevel(logging.INFO)
        stdout_hdlr = logging.StreamHandler(sys.stdout)
        stdout_fmt = logging.Formatter(
            '[%(filename)s:%(funcName)s()] - %(message)s')
        stdout_hdlr.setFormatter(stdout_fmt)
        app_args_str = "\n".join([f"{k}: {v}" for k, v in app_args.items()])
        sys.stdout.write(f"\nApplication Info:\n{app_args_str}")
        self.file_hdlr: Optional[MoonrakerLoggingHandler] = None
        self.listener: Optional[logging.handlers.QueueListener] = None
        log_file: str = app_args.get('log_file', "")
        if log_file:
            try:
                self.file_hdlr = MoonrakerLoggingHandler(
                    app_args, when='midnight', backupCount=2)
                formatter = logging.Formatter(
                    '%(asctime)s [%(filename)s:%(funcName)s()] - %(message)s')
                self.file_hdlr.setFormatter(formatter)
                self.listener = logging.handlers.QueueListener(
                    queue, self.file_hdlr, stdout_hdlr)
                self.file_hdlr.write_header()
            except Exception:
                log_file = os.path.normpath(log_file)
                dir_name = os.path.dirname(log_file)
                startup_warnings.append(
                    f"Unable to create log file at '{log_file}'. "
                    f"Make sure that the folder '{dir_name}' exists "
                    "and Moonraker has Read/Write access to the folder. "
                )
        if self.listener is None:
            self.listener = logging.handlers.QueueListener(
                queue, stdout_hdlr)
        self.listener.start()

    def set_server(self, server: Server) -> None:
        self.server = server
        self.server.register_endpoint(
            "/server/logs/rollover", ['POST'], self._handle_log_rollover
        )

    def set_rollover_info(self, name: str, item: str) -> None:
        if self.file_hdlr is not None:
            self.file_hdlr.set_rollover_info(name, item)

    def rollover_log(self) -> Awaitable[None]:
        if self.file_hdlr is None:
            raise self.server.error("File Logging Disabled")
        eventloop = self.server.get_event_loop()
        return eventloop.run_in_thread(self.file_hdlr.doRollover)

    def stop_logging(self):
        self.listener.stop()

    async def _handle_log_rollover(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        log_apps = ["moonraker", "klipper"]
        app = web_request.get_str("application", None)
        result: Dict[str, Any] = {"rolled_over": [], "failed": {}}
        if app is not None:
            if app not in log_apps:
                raise self.server.error(f"Unknown application {app}")
            log_apps = [app]
        if "moonraker" in log_apps:
            try:
                ret = self.rollover_log()
                if ret is not None:
                    await ret
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result["failed"]["moonraker"] = str(e)
            else:
                result["rolled_over"].append("moonraker")
        if "klipper" in log_apps:
            kconn: KlippyConnection
            kconn = self.server.lookup_component("klippy_connection")
            try:
                await kconn.rollover_log()
            except self.server.error as e:
                result["failed"]["klipper"] = str(e)
            else:
                result["rolled_over"].append("klipper")
        return result
