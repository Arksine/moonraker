#!/usr/bin/env python3
# Moonraker - HTTP/Websocket API Server for Klipper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import pathlib
import sys
import argparse
import importlib
import os
import io
import time
import socket
import logging
import signal
import asyncio
import uuid
import traceback
from . import confighelper
from .eventloop import EventLoop
from .app import MoonrakerApp
from .klippy_connection import KlippyConnection
from .utils import ServerError, Sentinel, get_software_info
from .loghelper import LogManager

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
    from .common import WebRequest
    from .websockets import WebsocketManager
    from .components.file_manager.file_manager import FileManager
    from .components.machine import Machine
    from .components.extensions import ExtensionManager
    FlexCallback = Callable[..., Optional[Coroutine]]
    _T = TypeVar("_T", Sentinel, Any)

API_VERSION = (1, 3, 0)
CORE_COMPONENTS = [
    'dbus_manager', 'database', 'file_manager', 'klippy_apis',
    'machine', 'data_store', 'shell_command', 'proc_stats',
    'job_state', 'job_queue', 'http_client', 'announcements',
    'webcam', 'extensions',
]


class Server:
    error = ServerError
    config_error = confighelper.ConfigError
    def __init__(self,
                 args: Dict[str, Any],
                 log_manager: LogManager,
                 event_loop: EventLoop
                 ) -> None:
        self.event_loop = event_loop
        self.log_manager = log_manager
        self.app_args = args
        self.events: Dict[str, List[FlexCallback]] = {}
        self.components: Dict[str, Any] = {}
        self.failed_components: List[str] = []
        self.warnings: Dict[str, str] = {}
        self._is_configured: bool = False

        self.config = config = self._parse_config()
        self.host: str = config.get('host', "0.0.0.0")
        self.port: int = config.getint('port', 7125)
        self.ssl_port: int = config.getint('ssl_port', 7130)
        self.exit_reason: str = ""
        self.server_running: bool = False

        # Configure Debug Logging
        config.getboolean('enable_debug_logging', False, deprecate=True)
        self.debug = args["debug"]
        log_level = logging.DEBUG if args["verbose"] else logging.INFO
        logging.getLogger().setLevel(log_level)
        self.event_loop.set_debug(args["asyncio_debug"])
        self.klippy_connection = KlippyConnection(self)

        # Tornado Application/Server
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_debug_endpoint = app.register_debug_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.register_api_transport = app.register_api_transport
        self.log_manager.set_server(self)

        for warning in args.get("startup_warnings", []):
            self.add_warning(warning)

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
        return list(self.warnings.values())

    def is_running(self) -> bool:
        return self.server_running

    def is_configured(self) -> bool:
        return self._is_configured

    def is_debug_enabled(self) -> bool:
        return self.debug

    def is_verbose_enabled(self) -> bool:
        return self.app_args["verbose"]

    def _parse_config(self) -> confighelper.ConfigHelper:
        config = confighelper.get_configuration(self, self.app_args)
        # log config file
        cfg_files = "\n".join(config.get_config_files())
        strio = io.StringIO()
        config.write_config(strio)
        cfg_item = f"\n{'#'*20} Moonraker Configuration {'#'*20}\n\n"
        cfg_item += strio.getvalue()
        cfg_item += "#"*65
        cfg_item += f"\nAll Configuration Files:\n{cfg_files}\n"
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
            await self.event_loop.run_in_thread(self.config.create_backup)

        machine: Machine = self.lookup_component("machine")
        if await machine.validate_installation():
            return

        if start_server:
            await self.start_server()

    async def start_server(self, connect_to_klippy: bool = True) -> None:
        # Open Unix Socket Server
        extm: ExtensionManager = self.lookup_component("extensions")
        await extm.start_unix_server()

        # Start HTTP Server
        logging.info(
            f"Starting Moonraker on ({self.host}, {self.port}), "
            f"Hostname: {socket.gethostname()}")
        self.moonraker_app.listen(self.host, self.port, self.ssl_port)
        self.server_running = True
        if connect_to_klippy:
            self.klippy_connection.connect()

    def add_log_rollover_item(
        self, name: str, item: str, log: bool = True
    ) -> None:
        self.log_manager.set_rollover_info(name, item)
        if log and item is not None:
            logging.info(item)

    def add_warning(
        self, warning: str, warn_id: Optional[str] = None, log: bool = True
    ) -> str:
        if warn_id is None:
            warn_id = str(id(warning))
        self.warnings[warn_id] = warning
        if log:
            logging.warning(warning)
        return warn_id

    def remove_warning(self, warn_id: str) -> None:
        self.warnings.pop(warn_id, None)

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
        cfg_sections = set([s.split()[0] for s in config.sections()])
        cfg_sections.remove('server')

        # load core components
        for component in CORE_COMPONENTS:
            self.load_component(config, component)
            if component in cfg_sections:
                cfg_sections.remove(component)

        # load remaining optional components
        for section in cfg_sections:
            self.load_component(config, section, None)

        self.klippy_connection.configure(config)
        config.validate_config()
        self._is_configured = True

    def load_component(
        self,
        config: confighelper.ConfigHelper,
        component_name: str,
        default: _T = Sentinel.MISSING
    ) -> Union[_T, Any]:
        if component_name in self.components:
            return self.components[component_name]
        if self.is_configured():
            raise self.error(
                "Cannot load components after configuration", 500
            )
        if component_name in self.failed_components:
            raise self.error(
                f"Component {component_name} previously failed to load", 500
            )
        try:
            full_name = f"moonraker.components.{component_name}"
            module = importlib.import_module(full_name)
            is_core = component_name in CORE_COMPONENTS
            fallback: Optional[str] = "server" if is_core else None
            config = config.getsection(component_name, fallback)
            load_func = getattr(module, "load_component")
            component = load_func(config)
        except Exception:
            msg = f"Unable to load component: ({component_name})"
            logging.exception(msg)
            if component_name not in self.failed_components:
                self.failed_components.append(component_name)
            if default is Sentinel.MISSING:
                raise
            return default
        self.components[component_name] = component
        logging.info(f"Component ({component_name}) loaded")
        return component

    def lookup_component(
        self, component_name: str, default: _T = Sentinel.MISSING
    ) -> Union[_T, Any]:
        component = self.components.get(component_name, default)
        if component is Sentinel.MISSING:
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

    def register_notification(
        self, event_name: str, notify_name: Optional[str] = None
    ) -> None:
        wsm: WebsocketManager = self.lookup_component("websockets")
        wsm.register_notification(event_name, notify_name)

    def register_event_handler(
        self, event: str, callback: FlexCallback
    ) -> None:
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event: str, *args) -> asyncio.Future:
        fut = self.event_loop.create_future()
        self.event_loop.register_callback(
            self._process_event, fut, event, *args)
        return fut

    async def _process_event(
        self, fut: asyncio.Future, event: str, *args
    ) -> None:
        events = self.events.get(event, [])
        coroutines: List[Coroutine] = []
        for func in events:
            try:
                ret = func(*args)
            except Exception:
                logging.exception(f"Error processing callback in event {event}")
            else:
                if ret is not None:
                    coroutines.append(ret)
        if coroutines:
            results = await asyncio.gather(*coroutines, return_exceptions=True)
            for val in results:
                if isinstance(val, Exception):
                    if sys.version_info < (3, 10):
                        exc_info = "".join(traceback.format_exception(
                            type(val), val, val.__traceback__
                        ))
                    else:
                        exc_info = "".join(traceback.format_exception(val))
                    logging.info(
                        f"\nError processing callback in event {event}\n{exc_info}"
                    )
        if not fut.done():
            fut.set_result(None)

    def register_remote_method(
        self, method_name: str, cb: FlexCallback
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
        # Allow cancelled tasks a chance to run in the eventloop
        await asyncio.sleep(.001)

        self.exit_reason = exit_reason
        self.event_loop.remove_signal_handler(signal.SIGTERM)
        self.event_loop.stop()

    async def _handle_server_restart(self, web_request: WebRequest) -> str:
        self.event_loop.register_callback(self._stop_server)
        return "ok"

    async def _handle_info_request(self, web_request: WebRequest) -> Dict[str, Any]:
        raw = web_request.get_boolean("raw", False)
        file_manager: Optional[FileManager] = self.lookup_component(
            'file_manager', None)
        reg_dirs = []
        if file_manager is not None:
            reg_dirs = file_manager.get_registered_dirs()
        wsm: WebsocketManager = self.lookup_component('websockets')
        mreqs = self.klippy_connection.missing_requirements
        if raw:
            warnings = list(self.warnings.values())
        else:
            warnings = [
                w.replace("\n", "<br/>") for w in self.warnings.values()
            ]
        return {
            'klippy_connected': self.klippy_connection.is_connected(),
            'klippy_state': self.klippy_connection.state,
            'components': list(self.components.keys()),
            'failed_components': self.failed_components,
            'registered_directories': reg_dirs,
            'warnings': warnings,
            'websocket_count': wsm.get_count(),
            'moonraker_version': self.app_args['software_version'],
            'missing_klippy_requirements': mreqs,
            'api_version': API_VERSION,
            'api_version_string': ".".join([str(v) for v in API_VERSION])
        }

    async def _handle_config_request(self, web_request: WebRequest) -> Dict[str, Any]:
        cfg_file_list: List[Dict[str, Any]] = []
        cfg_parent = pathlib.Path(
            self.app_args["config_file"]
        ).expanduser().resolve().parent
        for fname, sections in self.config.get_file_sections().items():
            path = pathlib.Path(fname)
            try:
                rel_path = str(path.relative_to(str(cfg_parent)))
            except ValueError:
                rel_path = fname
            cfg_file_list.append({"filename": rel_path, "sections": sections})
        return {
            'config': self.config.get_parsed_config(),
            'orig': self.config.get_orig_config(),
            'files': cfg_file_list
        }

def main(from_package: bool = True) -> None:
    def get_env_bool(key: str) -> bool:
        return os.getenv(key, "").lower() in ["y", "yes", "true"]

    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="Moonraker - Klipper API Server")
    parser.add_argument(
        "-d", "--datapath",
        default=os.getenv("MOONRAKER_DATA_PATH"),
        metavar='<data path>',
        help="Location of Moonraker Data File Path"
    )
    parser.add_argument(
        "-c", "--configfile",
        default=os.getenv("MOONRAKER_CONFIG_PATH"),
        metavar='<configfile>',
        help="Path to Moonraker's configuration file"
    )
    parser.add_argument(
        "-l", "--logfile",
        default=os.getenv("MOONRAKER_LOG_PATH"),
        metavar='<logfile>',
        help="Path to Moonraker's log file"
    )
    parser.add_argument(
        "-u", "--unixsocket",
        default=os.getenv("MOONRAKER_UDS_PATH"),
        metavar="<unixsocket>",
        help="Path to Moonraker's unix domain socket"
    )
    parser.add_argument(
        "-n", "--nologfile",
        action='store_const',
        const=True,
        default=get_env_bool("MOONRAKER_DISABLE_FILE_LOG"),
        help="disable logging to a file"
    )
    parser.add_argument(
        "-v", "--verbose",
        action='store_const',
        const=True,
        default=get_env_bool("MOONRAKER_VERBOSE_LOGGING"),
        help="Enable verbose logging"
    )
    parser.add_argument(
        "-g", "--debug",
        action='store_const',
        const=True,
        default=get_env_bool("MOONRAKER_ENABLE_DEBUG"),
        help="Enable Moonraker debug features"
    )
    parser.add_argument(
        "-o", "--asyncio-debug",
        action='store_const',
        const=True,
        default=get_env_bool("MOONRAKER_ASYNCIO_DEBUG"),
        help="Enable asyncio debug flag"
    )
    cmd_line_args = parser.parse_args()

    startup_warnings: List[str] = []
    dp: str = cmd_line_args.datapath or "~/printer_data"
    data_path = pathlib.Path(dp).expanduser().resolve()
    if not data_path.exists():
        try:
            data_path.mkdir()
        except Exception:
            startup_warnings.append(
                f"Unable to create data path folder at {data_path}"
            )
    uuid_path = data_path.joinpath(".moonraker.uuid")
    if not uuid_path.is_file():
        instance_uuid = uuid.uuid4().hex
        uuid_path.write_text(instance_uuid)
    else:
        instance_uuid = uuid_path.read_text().strip()
    if cmd_line_args.configfile is not None:
        cfg_file: str = cmd_line_args.configfile
    else:
        cfg_file = str(data_path.joinpath("config/moonraker.conf"))
    if cmd_line_args.unixsocket is not None:
        unix_sock: str = cmd_line_args.unixsocket
    else:
        comms_dir = data_path.joinpath("comms")
        if not comms_dir.exists():
            comms_dir.mkdir()
        unix_sock = str(comms_dir.joinpath("moonraker.sock"))
    app_args = {
        "data_path": str(data_path),
        "is_default_data_path": cmd_line_args.datapath is None,
        "config_file": cfg_file,
        "startup_warnings": startup_warnings,
        "verbose": cmd_line_args.verbose,
        "debug": cmd_line_args.debug,
        "asyncio_debug": cmd_line_args.asyncio_debug,
        "is_backup_config": False,
        "is_python_package": from_package,
        "instance_uuid": instance_uuid,
        "unix_socket_path": unix_sock
    }

    # Setup Logging
    app_args.update(get_software_info())
    if cmd_line_args.nologfile:
        app_args["log_file"] = ""
    elif cmd_line_args.logfile:
        app_args["log_file"] = os.path.normpath(
            os.path.expanduser(cmd_line_args.logfile))
    else:
        app_args["log_file"] = str(data_path.joinpath("logs/moonraker.log"))
    app_args["python_version"] = sys.version.replace("\n", " ")
    log_manager = LogManager(app_args, startup_warnings)

    # Start asyncio event loop and server
    event_loop = EventLoop()
    alt_config_loaded = False
    estatus = 0
    while True:
        try:
            server = Server(app_args, log_manager, event_loop)
            server.load_components()
        except confighelper.ConfigError as e:
            backup_cfg = confighelper.find_config_backup(cfg_file)
            logging.exception("Server Config Error")
            if alt_config_loaded or backup_cfg is None:
                estatus = 1
                break
            app_args["config_file"] = backup_cfg
            app_args["is_backup_config"] = True
            warn_list = list(startup_warnings)
            app_args["startup_warnings"] = warn_list
            warn_list.append(
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
            app_args["config_file"] = cfg_file
            app_args["startup_warnings"] = startup_warnings
            app_args["is_backup_config"] = False
            alt_config_loaded = False
        event_loop.close()
        # Since we are running outside of the the server
        # it is ok to use a blocking sleep here
        time.sleep(.5)
        logging.info("Attempting Server Restart...")
        event_loop.reset()
    event_loop.close()
    logging.info("Server Shutdown")
    log_manager.stop_logging()
    exit(estatus)
