#!/usr/bin/env python3
# Moonraker - HTTP/Websocket API Server for Klipper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import sys
import argparse
import importlib
import os
import io
import time
import socket
import logging
import signal
import confighelper
import utils
import asyncio
from eventloop import EventLoop
from app import MoonrakerApp
from klippy_connection import KlippyConnection
from utils import ServerError, SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Callable,
    Coroutine,
    Dict,
    List,
    Tuple,
    Union,
    TypeVar,
)
if TYPE_CHECKING:
    from websockets import WebRequest, WebsocketManager
    from components.file_manager.file_manager import FileManager
    FlexCallback = Callable[..., Optional[Coroutine]]
    _T = TypeVar("_T")

API_VERSION = (1, 0, 3)

CORE_COMPONENTS = [
    'dbus_manager', 'database', 'file_manager', 'klippy_apis',
    'machine', 'data_store', 'shell_command', 'proc_stats',
    'job_state', 'job_queue', 'http_client', 'announcements'
]

SENTINEL = SentinelClass.get_instance()

class Server:
    error = ServerError
    def __init__(self,
                 args: Dict[str, Any],
                 file_logger: Optional[utils.MoonrakerLoggingHandler],
                 event_loop: EventLoop
                 ) -> None:
        self.event_loop = event_loop
        self.file_logger = file_logger
        self.app_args = args
        self.config = config = self._parse_config()
        self.host: str = config.get('host', "0.0.0.0")
        self.port: int = config.getint('port', 7125)
        self.ssl_port: int = config.getint('ssl_port', 7130)
        self.exit_reason: str = ""
        self.server_running: bool = False

        # Configure Debug Logging
        self.debug = config.getboolean('enable_debug_logging', False)
        asyncio_debug = config.getboolean('enable_asyncio_debug', False)
        log_level = logging.DEBUG if self.debug else logging.INFO
        logging.getLogger().setLevel(log_level)
        self.event_loop.set_debug(asyncio_debug)

        # Event initialization
        self.events: Dict[str, List[FlexCallback]] = {}
        self.components: Dict[str, Any] = {}
        self.failed_components: List[str] = []
        self.warnings: List[str] = []
        self.klippy_connection = KlippyConnection(config)

        # Tornado Application/Server
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.register_api_transport = app.register_api_transport

        log_warn = args.get('log_warning', "")
        if log_warn:
            self.add_warning(log_warn)
        cfg_warn = args.get("config_warning", "")
        if cfg_warn:
            self.add_warning(cfg_warn)

        self.register_endpoint(
            "/server/info", ['GET'], self._handle_info_request)
        self.register_endpoint(
            "/server/config", ['GET'], self._handle_config_request)
        self.register_endpoint(
            "/server/restart", ['POST'], self._handle_server_restart)
        self.register_notification("server:klippy_ready")
        self.register_notification("server:klippy_shutdown")
        self.register_notification("server:klippy_disconnect",
                                   "klippy_disconnected")
        self.register_notification("server:gcode_response")

    def get_app_args(self) -> Dict[str, Any]:
        return dict(self.app_args)

    def get_event_loop(self) -> EventLoop:
        return self.event_loop

    def get_api_version(self) -> Tuple[int, int, int]:
        return API_VERSION

    def get_warnings(self) -> List[str]:
        return self.warnings

    def is_running(self) -> bool:
        return self.server_running

    def is_debug_enabled(self) -> bool:
        return self.debug

    def _parse_config(self) -> confighelper.ConfigHelper:
        config = confighelper.get_configuration(self, self.app_args)
        # log config file
        strio = io.StringIO()
        config.write_config(strio)
        cfg_item = f"\n{'#'*20} Moonraker Configuration {'#'*20}\n\n"
        cfg_item += strio.getvalue()
        cfg_item += "#"*65
        strio.close()
        self.add_log_rollover_item('config', cfg_item)
        return config

    async def server_init(self, start_server: bool = True) -> None:
        self.event_loop.add_signal_handler(
            signal.SIGTERM, self._handle_term_signal)

        # Perform asynchronous init after the event loop starts
        optional_comps: List[Coroutine] = []
        for name, component in self.components.items():
            if not hasattr(component, "component_init"):
                continue
            if name in CORE_COMPONENTS:
                # Process core components in order synchronously
                await self._initialize_component(name, component)
            else:
                optional_comps.append(
                    self._initialize_component(name, component))

        # Asynchronous Optional Component Initialization
        if optional_comps:
            await asyncio.gather(*optional_comps)

        if not self.warnings:
            cfg_file = self.app_args['config_file']
            await self.event_loop.run_in_thread(
                confighelper.backup_config, cfg_file)

        if start_server:
            await self.start_server()

    async def start_server(self, connect_to_klippy: bool = True) -> None:
        # Start HTTP Server
        logging.info(
            f"Starting Moonraker on ({self.host}, {self.port}), "
            f"Hostname: {socket.gethostname()}")
        self.moonraker_app.listen(self.host, self.port, self.ssl_port)
        self.server_running = True
        if connect_to_klippy:
            self.klippy_connection.connect()

    def add_log_rollover_item(self, name: str, item: str,
                              log: bool = True) -> None:
        if self.file_logger is not None:
            self.file_logger.set_rollover_info(name, item)
        if log and item is not None:
            logging.info(item)

    def add_warning(self, warning: str, log: bool = True) -> None:
        self.warnings.append(warning)
        if log:
            logging.warning(warning)

    # ***** Component Management *****
    async def _initialize_component(self, name: str, component: Any) -> None:
        logging.info(f"Performing Component Post Init: [{name}]")
        try:
            ret = component.component_init()
            if ret is not None:
                await ret
        except Exception as e:
            logging.exception(f"Component [{name}] failed post init")
            self.add_warning(f"Component '{name}' failed to load with "
                             f"error: {e}")
            self.set_failed_component(name)

    def load_components(self) -> None:
        config = self.config
        cfg_sections = [s.split()[0] for s in config.sections()]
        cfg_sections.remove('server')

        # load core components
        for component in CORE_COMPONENTS:
            self.load_component(config, component)
            if component in cfg_sections:
                cfg_sections.remove(component)

        # load remaining optional components
        for section in cfg_sections:
            self.load_component(config, section, None)

        config.validate_config()

    def load_component(self,
                       config: confighelper.ConfigHelper,
                       component_name: str,
                       default: Union[SentinelClass, _T] = SENTINEL
                       ) -> Union[_T, Any]:
        if component_name in self.components:
            return self.components[component_name]
        try:
            module = importlib.import_module("components." + component_name)
            config = config[component_name]
            load_func = getattr(module, "load_component")
            component = load_func(config)
        except Exception:
            msg = f"Unable to load component: ({component_name})"
            logging.exception(msg)
            if component_name not in self.failed_components:
                self.failed_components.append(component_name)
            if isinstance(default, SentinelClass):
                raise ServerError(msg)
            return default
        self.components[component_name] = component
        logging.info(f"Component ({component_name}) loaded")
        return component

    def lookup_component(self,
                         component_name: str,
                         default: Union[SentinelClass, _T] = SENTINEL
                         ) -> Union[_T, Any]:
        component = self.components.get(component_name, default)
        if isinstance(component, SentinelClass):
            raise ServerError(f"Component ({component_name}) not found")
        return component

    def set_failed_component(self, component_name: str) -> None:
        if component_name not in self.failed_components:
            self.failed_components.append(component_name)

    def register_component(self, component_name: str, component: Any) -> None:
        if component_name in self.components:
            raise self.error(
                f"Component '{component_name}' already registered")
        self.components[component_name] = component

    def register_notification(self,
                              event_name: str,
                              notify_name: Optional[str] = None
                              ) -> None:
        wsm: WebsocketManager = self.lookup_component("websockets")
        wsm.register_notification(event_name, notify_name)

    def register_event_handler(self,
                               event: str,
                               callback: FlexCallback
                               ) -> None:
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event: str, *args) -> asyncio.Future:
        fut = self.event_loop.create_future()
        self.event_loop.register_callback(
            self._process_event, fut, event, *args)
        return fut

    async def _process_event(self,
                             fut: asyncio.Future,
                             event: str,
                             *args
                             ) -> None:
        events = self.events.get(event, [])
        coroutines: List[Coroutine] = []
        for func in events:
            ret = func(*args)
            if ret is not None:
                coroutines.append(ret)
        if coroutines:
            await asyncio.gather(*coroutines)
        fut.set_result(None)

    def register_remote_method(self,
                               method_name: str,
                               cb: FlexCallback
                               ) -> None:
        self.klippy_connection.register_remote_method(method_name, cb)

    def get_host_info(self) -> Dict[str, Any]:
        return {
            'hostname': socket.gethostname(),
            'address': self.host,
            'port': self.port,
            'ssl_port': self.ssl_port
        }

    def get_klippy_info(self) -> Dict[str, Any]:
        return self.klippy_connection.klippy_info

    def get_klippy_state(self) -> str:
        return self.klippy_connection.state

    def _handle_term_signal(self) -> None:
        logging.info(f"Exiting with signal SIGTERM")
        self.event_loop.register_callback(self._stop_server, "terminate")

    async def _stop_server(self, exit_reason: str = "restart") -> None:
        self.server_running = False
        # Call each component's "on_exit" method
        for name, component in self.components.items():
            if hasattr(component, "on_exit"):
                func: FlexCallback = getattr(component, "on_exit")
                try:
                    ret = func()
                    if ret is not None:
                        await ret
                except Exception:
                    logging.exception(
                        f"Error executing 'on_exit()' for component: {name}")

        # Sleep for 100ms to allow connected websockets to write out
        # remaining data
        await asyncio.sleep(.1)
        try:
            await self.moonraker_app.close()
        except Exception:
            logging.exception("Error Closing App")

        # Disconnect from Klippy
        try:
            await asyncio.wait_for(
                asyncio.shield(self.klippy_connection.close(
                    wait_closed=True)), 2.)
        except Exception:
            logging.exception("Klippy Disconnect Error")

        # Close all components
        for name, component in self.components.items():
            if name in ["application", "websockets", "klippy_connection"]:
                # These components have already been closed
                continue
            if hasattr(component, "close"):
                func = getattr(component, "close")
                try:
                    ret = func()
                    if ret is not None:
                        await ret
                except Exception:
                    logging.exception(
                        f"Error executing 'close()' for component: {name}")

        self.exit_reason = exit_reason
        self.event_loop.remove_signal_handler(signal.SIGTERM)
        self.event_loop.stop()

    async def _handle_server_restart(self, web_request: WebRequest) -> str:
        self.event_loop.register_callback(self._stop_server)
        return "ok"

    async def _handle_info_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        file_manager: Optional[FileManager] = self.lookup_component(
            'file_manager', None)
        reg_dirs = []
        if file_manager is not None:
            reg_dirs = file_manager.get_registered_dirs()
        wsm: WebsocketManager = self.lookup_component('websockets')
        mreqs = self.klippy_connection.missing_requirements
        return {
            'klippy_connected': self.klippy_connection.is_connected(),
            'klippy_state': self.klippy_connection.state,
            'components': list(self.components.keys()),
            'failed_components': self.failed_components,
            'registered_directories': reg_dirs,
            'warnings': self.warnings,
            'websocket_count': wsm.get_count(),
            'moonraker_version': self.app_args['software_version'],
            'missing_klippy_requirements': mreqs,
            'api_version': API_VERSION,
            'api_version_string': ".".join([str(v) for v in API_VERSION])
        }

    async def _handle_config_request(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, Any]:
        return {
            'config': self.config.get_parsed_config()
        }

def main(cmd_line_args: argparse.Namespace) -> None:
    cfg_file = cmd_line_args.configfile
    app_args = {'config_file': cfg_file}

    # Setup Logging
    version = utils.get_software_version()
    if cmd_line_args.nologfile:
        app_args['log_file'] = ""
    else:
        app_args['log_file'] = os.path.normpath(
            os.path.expanduser(cmd_line_args.logfile))
    app_args['software_version'] = version
    app_args['python_version'] = sys.version.replace("\n", " ")
    ql, file_logger, warning = utils.setup_logging(app_args)
    if warning is not None:
        app_args['log_warning'] = warning

    # Start asyncio event loop and server
    event_loop = EventLoop()
    alt_config_loaded = False
    estatus = 0
    while True:
        try:
            server = Server(app_args, file_logger, event_loop)
            server.load_components()
        except confighelper.ConfigError as e:
            backup_cfg = confighelper.find_config_backup(cfg_file)
            if alt_config_loaded or backup_cfg is None:
                logging.exception("Server Config Error")
                estatus = 1
                break
            app_args['config_file'] = backup_cfg
            app_args['config_warning'] = (
                f"Server configuration error: {e}\n"
                f"Loaded server from most recent working configuration:"
                f" '{app_args['config_file']}'\n"
                f"Please fix the issue in moonraker.conf and restart "
                f"the server."
            )
            alt_config_loaded = True
            continue
        except Exception:
            logging.exception("Moonraker Error")
            estatus = 1
            break
        try:
            event_loop.register_callback(server.server_init)
            event_loop.start()
        except Exception:
            logging.exception("Server Running Error")
            estatus = 1
            break
        if server.exit_reason == "terminate":
            break
        # Restore the original config and clear the warning
        # before the server restarts
        if alt_config_loaded:
            app_args['config_file'] = cfg_file
            app_args.pop('config_warning', None)
            alt_config_loaded = False
        event_loop.close()
        # Since we are running outside of the the server
        # it is ok to use a blocking sleep here
        time.sleep(.5)
        logging.info("Attempting Server Restart...")
        for _ in range(5):
            # Sometimes the new loop does not properly instantiate.
            # Give 5 attempts before raising an exception
            new_loop = asyncio.new_event_loop()
            if not new_loop.is_closed():
                break
            logging.info("Failed to create open eventloop, "
                         "retyring in .5 seconds...")
            time.sleep(.5)
        else:
            raise RuntimeError("Unable to create new open eventloop")
        asyncio.set_event_loop(new_loop)
        event_loop.reset()
    event_loop.close()
    logging.info("Server Shutdown")
    ql.stop()
    exit(estatus)


if __name__ == '__main__':
    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="Moonraker - Klipper API Server")
    parser.add_argument(
        "-c", "--configfile", default="~/moonraker.conf",
        metavar='<configfile>',
        help="Location of moonraker configuration file")
    parser.add_argument(
        "-l", "--logfile", default="/tmp/moonraker.log", metavar='<logfile>',
        help="log file name and location")
    parser.add_argument(
        "-n", "--nologfile", action='store_true',
        help="disable logging to a file")
    main(parser.parse_args())
