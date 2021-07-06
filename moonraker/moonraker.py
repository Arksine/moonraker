#!/usr/bin/env python3
# Moonraker - HTTP/Websocket API Server for Klipper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import argparse
import sys
import importlib
import os
import io
import time
import socket
import logging
import json
import signal
import confighelper
import utils
import asyncio
from tornado import iostream, gen
from tornado.ioloop import IOLoop
from tornado.util import TimeoutError
from tornado.locks import Event
from app import MoonrakerApp
from utils import ServerError, SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Callable,
    Coroutine,
    Tuple,
    Dict,
    List,
    Union,
    TypeVar,
)
if TYPE_CHECKING:
    from websockets import WebRequest, Subscribable
    from components.data_store import DataStore
    from components.klippy_apis import KlippyAPI
    from components.file_manager import FileManager
    FlexCallback = Callable[..., Optional[Coroutine]]
    _T = TypeVar("_T")

INIT_TIME = .25
LOG_ATTEMPT_INTERVAL = int(2. / INIT_TIME + .5)
MAX_LOG_ATTEMPTS = 10 * LOG_ATTEMPT_INTERVAL

CORE_COMPONENTS = [
    'database', 'file_manager', 'klippy_apis', 'machine',
    'data_store', 'shell_command', 'proc_stats']

SENTINEL = SentinelClass.get_instance()

class Server:
    error = ServerError
    def __init__(self,
                 args: argparse.Namespace,
                 file_logger: Optional[utils.MoonrakerLoggingHandler]
                 ) -> None:
        self.file_logger = file_logger
        self.config = config = confighelper.get_configuration(self, args)
        # log config file
        strio = io.StringIO()
        config.write_config(strio)
        cfg_item = f"\n{'#'*20} Moonraker Configuration {'#'*20}\n\n"
        cfg_item += strio.getvalue()
        cfg_item += "#"*65
        strio.close()
        self.add_log_rollover_item('config', cfg_item)
        self.host: str = config.get('host', "0.0.0.0")
        self.port: int = config.getint('port', 7125)
        self.ssl_port: int = config.getint('ssl_port', 7130)
        self.exit_reason: str = ""

        # Event initialization
        self.events: Dict[str, List[FlexCallback]] = {}

        # Klippy Connection Handling
        self.klippy_address: str = config.get(
            'klippy_uds_address', "/tmp/klippy_uds")
        self.klippy_connection = KlippyConnection(
            self.process_command, self.on_connection_closed)
        self.klippy_info: Dict[str, Any] = {}
        self.init_list: List[str] = []
        self.init_handle: Optional[object] = None
        self.init_attempts: int = 0
        self.klippy_state: str = "disconnected"
        self.klippy_disconnect_evt: Optional[Event] = None
        self.subscriptions: Dict[Subscribable, Dict[str, Any]] = {}
        self.failed_components: List[str] = []
        self.warnings: List[str] = []

        # Server/IOLoop
        self.server_running: bool = False
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.get_websocket_manager = app.get_websocket_manager
        self.register_api_transport = app.register_api_transport
        self.ioloop = IOLoop.current()

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

        # Setup remote methods accessable to Klippy.  Note that all
        # registered remote methods should be of the notification type,
        # they do not return a response to Klippy after execution
        self.pending_requests: Dict[int, BaseRequest] = {}
        self.remote_methods: Dict[str, FlexCallback] = {}
        self.klippy_reg_methods: List[str] = []
        self.register_remote_method(
            'process_gcode_response', self._process_gcode_response,
            need_klippy_reg=False)
        self.register_remote_method(
            'process_status_update', self._process_status_update,
            need_klippy_reg=False)

        # Component initialization
        self.components: Dict[str, Any] = {}
        self._load_components(config)
        self.klippy_apis: KlippyAPI = self.lookup_component('klippy_apis')
        config.validate_config()

    def start(self) -> None:
        hostname, hostport = self.get_host_info()
        logging.info(
            f"Starting Moonraker on ({self.host}, {hostport}), "
            f"Hostname: {hostname}")
        self.moonraker_app.listen(self.host, self.port, self.ssl_port)
        self.server_running = True
        self.ioloop.spawn_callback(self._init_signals)
        self.ioloop.spawn_callback(self._connect_klippy)

    def _init_signals(self) -> None:
        aioloop = asyncio.get_event_loop()
        aioloop.add_signal_handler(
            signal.SIGTERM, self._handle_term_signal)

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
    def _load_components(self, config: confighelper.ConfigHelper) -> None:
        # load core components
        for component in CORE_COMPONENTS:
            self.load_component(config, component)

        # check for optional components
        opt_sections = set([s.split()[0] for s in config.sections()]) - \
            set(['server', 'system_args'])
        for section in opt_sections:
            self.load_component(config, section, None)

    def load_component(self,
                       config: confighelper.ConfigHelper,
                       component_name: str,
                       default: Union[SentinelClass, _T] = SENTINEL
                       ) -> Union[_T, Any]:
        if component_name in self.components:
            return self.components[component_name]
        # Make sure component exists
        mod_path = os.path.join(
            os.path.dirname(__file__), 'components', component_name + '.py')
        if not os.path.exists(mod_path):
            msg = f"Component ({component_name}) does not exist"
            logging.info(msg)
            self.failed_components.append(component_name)
            if isinstance(default, SentinelClass):
                raise ServerError(msg)
            return default
        try:
            module = importlib.import_module("components." + component_name)
            func_name = "load_component"
            if hasattr(module, "load_component_multi"):
                func_name = "load_component_multi"
            if component_name not in CORE_COMPONENTS and \
                    func_name == "load_component":
                config = config[component_name]
            load_func = getattr(module, func_name)
            component = load_func(config)
        except Exception:
            msg = f"Unable to load component: ({component_name})"
            logging.exception(msg)
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

    def register_notification(self,
                              event_name: str,
                              notify_name: Optional[str] = None
                              ) -> None:
        wsm = self.get_websocket_manager()
        wsm.register_notification(event_name, notify_name)

    def register_event_handler(self,
                               event: str,
                               callback: FlexCallback
                               ) -> None:
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event: str, *args) -> None:
        events = self.events.get(event, [])
        for evt in events:
            self.ioloop.spawn_callback(evt, *args)

    def register_remote_method(self,
                               method_name: str,
                               cb: FlexCallback,
                               need_klippy_reg: bool = True
                               ) -> None:
        if method_name in self.remote_methods:
            # XXX - may want to raise an exception here
            logging.info(f"Remote method ({method_name}) already registered")
            return
        self.remote_methods[method_name] = cb
        if need_klippy_reg:
            # These methods need to be registered with Klippy
            self.klippy_reg_methods.append(method_name)

    def get_host_info(self) -> Tuple[str, int]:
        hostname = socket.gethostname()
        return hostname, self.port

    def get_klippy_info(self) -> Dict[str, Any]:
        return dict(self.klippy_info)

    def get_klippy_state(self) -> str:
        return self.klippy_state

    # ***** Klippy Connection *****
    async def _connect_klippy(self) -> None:
        if not self.server_running:
            return
        ret = await self.klippy_connection.connect(self.klippy_address)
        if not ret:
            self.ioloop.call_later(.25, self._connect_klippy)  # type: ignore
            return
        # begin server iniialization
        self.ioloop.spawn_callback(self._initialize)

    def process_command(self, cmd: Dict[str, Any]) -> None:
        method = cmd.get('method', None)
        if method is not None:
            # This is a remote method called from klippy
            if method in self.remote_methods:
                params = cmd.get('params', {})
                self.ioloop.spawn_callback(
                    self._execute_method, method, **params)
            else:
                logging.info(f"Unknown method received: {method}")
            return
        # This is a response to a request, process
        req_id = cmd.get('id', None)
        request = self.pending_requests.pop(req_id, None)
        if request is None:
            logging.info(
                f"No request matching request ID: {req_id}, "
                f"response: {cmd}")
            return
        if 'result' in cmd:
            result = cmd['result']
            if not result:
                result = "ok"
        else:
            err = cmd.get('error', "Malformed Klippy Response")
            result = ServerError(err, 400)
        request.notify(result)

    async def _execute_method(self, method_name: str, **kwargs) -> None:
        try:
            ret = self.remote_methods[method_name](**kwargs)
            if ret is not None:
                await ret
        except Exception:
            logging.exception(f"Error running remote method: {method_name}")

    def on_connection_closed(self) -> None:
        self.init_list = []
        self.klippy_state = "disconnected"
        for request in self.pending_requests.values():
            request.notify(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        self.subscriptions = {}
        logging.info("Klippy Connection Removed")
        self.send_event("server:klippy_disconnect")
        if self.init_handle is not None:
            self.ioloop.remove_timeout(self.init_handle)
        if self.server_running:
            self.ioloop.call_later(.25, self._connect_klippy)  # type: ignore
        if self.klippy_disconnect_evt is not None:
            self.klippy_disconnect_evt.set()

    async def _initialize(self) -> None:
        if not self.server_running:
            return
        await self._check_ready()
        await self._request_endpoints()
        # Subscribe to "webhooks"
        # Register "webhooks" subscription
        if "webhooks_sub" not in self.init_list:
            try:
                await self.klippy_apis.subscribe_objects({'webhooks': None})
            except ServerError as e:
                logging.info(f"{e}\nUnable to subscribe to webhooks object")
            else:
                logging.info("Webhooks Subscribed")
                self.init_list.append("webhooks_sub")
        # Subscribe to Gcode Output
        if "gcode_output_sub" not in self.init_list:
            try:
                await self.klippy_apis.subscribe_gcode_output()
            except ServerError as e:
                logging.info(
                    f"{e}\nUnable to register gcode output subscription")
            else:
                logging.info("GCode Output Subscribed")
                self.init_list.append("gcode_output_sub")
        if "klippy_ready" in self.init_list or \
                not self.klippy_connection.is_connected():
            # Either Klippy is ready or the connection dropped
            # during initialization.  Exit initialization
            self.init_attempts = 0
            self.init_handle = None
        else:
            self.init_attempts += 1
            self.init_handle = self.ioloop.call_later(
                INIT_TIME, self._initialize)  # type: ignore

    async def _request_endpoints(self) -> None:
        result = await self.klippy_apis.list_endpoints(default=None)
        if result is None:
            return
        endpoints = result.get('endpoints', [])
        for ep in endpoints:
            self.moonraker_app.register_remote_handler(ep)

    async def _check_ready(self) -> None:
        send_id = "identified" not in self.init_list
        result: Dict[str, Any]
        try:
            result = await self.klippy_apis.get_klippy_info(send_id)
        except ServerError as e:
            if self.init_attempts % LOG_ATTEMPT_INTERVAL == 0 and \
                    self.init_attempts <= MAX_LOG_ATTEMPTS:
                logging.info(
                    f"{e}\nKlippy info request error.  This indicates that\n"
                    f"Klippy may have experienced an error during startup.\n"
                    f"Please check klippy.log for more information")
            return
        self.klippy_info = dict(result)
        self.klippy_state = result.get('state', "unknown")
        if send_id:
            self.init_list.append("identified")
            self.send_event("server:klippy_identified")
        if self.klippy_state == "ready":
            await self._verify_klippy_requirements()
            logging.info("Klippy ready")
            self.init_list.append('klippy_ready')
            # register methods with klippy
            for method in self.klippy_reg_methods:
                try:
                    await self.klippy_apis.register_method(method)
                except ServerError:
                    logging.exception(f"Unable to register method '{method}'")
            self.send_event("server:klippy_ready")
        elif self.init_attempts % LOG_ATTEMPT_INTERVAL == 0 and \
                self.init_attempts <= MAX_LOG_ATTEMPTS:
            msg = result.get('state_message', "Klippy Not Ready")
            logging.info("\n" + msg)

    async def _verify_klippy_requirements(self) -> None:
        result = await self.klippy_apis.get_object_list(default=None)
        if result is None:
            logging.info(
                f"Unable to retreive Klipper Object List")
            return
        req_objs = set(["virtual_sdcard", "display_status", "pause_resume"])
        missing_objs = req_objs - set(result)
        if missing_objs:
            err_str = ", ".join([f"[{o}]" for o in missing_objs])
            logging.info(
                f"\nWarning, unable to detect the following printer "
                f"objects:\n{err_str}\nPlease add the the above sections "
                f"to printer.cfg for full Moonraker functionality.")
        if "virtual_sdcard" not in missing_objs:
            # Update the gcode path
            query_res = await self.klippy_apis.query_objects(
                {'configfile': None}, default=None)
            if query_res is None:
                logging.info(f"Unable to set SD Card path")
            else:
                config = query_res.get('configfile', {}).get('config', {})
                vsd_config = config.get('virtual_sdcard', {})
                vsd_path = vsd_config.get('path', None)
                if vsd_path is not None:
                    file_manager: FileManager = self.lookup_component(
                        'file_manager')
                    file_manager.register_directory('gcodes', vsd_path)
                else:
                    logging.info(
                        "Configuration for [virtual_sdcard] not found,"
                        " unable to set SD Card path")

    def _process_gcode_response(self, response: str) -> None:
        self.send_event("server:gcode_response", response)

    def _process_status_update(self,
                               eventtime: float,
                               status: Dict[str, Any]
                               ) -> None:
        if 'webhooks' in status:
            # XXX - process other states (startup, ready, error, etc)?
            state: Optional[str] = status['webhooks'].get('state', None)
            if state is not None:
                if state == "shutdown":
                    logging.info("Klippy has shutdown")
                    self.send_event("server:klippy_shutdown")
                self.klippy_state = state
        for conn, sub in self.subscriptions.items():
            conn_status: Dict[str, Any] = {}
            for name, fields in sub.items():
                if name in status:
                    val: Dict[str, Any] = status[name]
                    if fields is None:
                        conn_status[name] = dict(val)
                    else:
                        conn_status[name] = {
                            k: v for k, v in val.items() if k in fields}
            conn.send_status(conn_status, eventtime)

    async def make_request(self, web_request: WebRequest) -> Any:
        rpc_method = web_request.get_endpoint()
        if rpc_method == "objects/subscribe":
            return await self._request_subscripton(web_request)
        else:
            if rpc_method == "gcode/script":
                script = web_request.get_str('script', "")
                data_store: DataStore = self.lookup_component('data_store')
                data_store.store_gcode_command(script)
            return await self._request_standard(web_request)

    async def _request_subscripton(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        args = web_request.get_args()
        conn = web_request.get_connection()

        # Build the subscription request from a superset of all client
        # subscriptions
        sub = args.get('objects', {})
        if conn is None:
            raise self.error(
                "No connection associated with subscription request")
        self.subscriptions[conn] = sub
        all_subs: Dict[str, Any] = {}
        # request superset of all client subscriptions
        for sub in self.subscriptions.values():
            for obj, items in sub.items():
                if obj in all_subs:
                    pi = all_subs[obj]
                    if items is None or pi is None:
                        all_subs[obj] = None
                    else:
                        uitems = list(set(pi) | set(items))
                        all_subs[obj] = uitems
                else:
                    all_subs[obj] = items
        args['objects'] = all_subs
        args['response_template'] = {'method': "process_status_update"}

        result = await self._request_standard(web_request)

        # prune the status response
        pruned_status = {}
        all_status = result['status']
        sub = self.subscriptions.get(conn, {})
        for obj, fields in all_status.items():
            if obj in sub:
                valid_fields = sub[obj]
                if valid_fields is None:
                    pruned_status[obj] = fields
                else:
                    pruned_status[obj] = {k: v for k, v in fields.items()
                                          if k in valid_fields}
        result['status'] = pruned_status
        return result

    async def _request_standard(self, web_request: WebRequest) -> Any:
        rpc_method = web_request.get_endpoint()
        args = web_request.get_args()
        # Create a base klippy request
        base_request = BaseRequest(rpc_method, args)
        self.pending_requests[base_request.id] = base_request
        self.ioloop.spawn_callback(
            self.klippy_connection.send_request, base_request)
        return await base_request.wait()

    def remove_subscription(self, conn: Subscribable) -> None:
        self.subscriptions.pop(conn, None)

    def _handle_term_signal(self) -> None:
        logging.info(f"Exiting with signal SIGTERM")
        self.ioloop.spawn_callback(self._stop_server, "terminate")

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
        await gen.sleep(.1)
        try:
            await self.moonraker_app.close()
        except Exception:
            logging.exception("Error Closing App")

        # Disconnect from Klippy
        try:
            if self.klippy_connection.is_connected():
                self.klippy_disconnect_evt = Event()
                self.klippy_connection.close()
                timeout = time.time() + 2.
                await self.klippy_disconnect_evt.wait(timeout)
                self.klippy_disconnect_evt = None
        except Exception:
            logging.exception("Klippy Disconnect Error")

        # Close all components
        for name, component in self.components.items():
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
        aioloop = asyncio.get_event_loop()
        aioloop.remove_signal_handler(signal.SIGTERM)
        self.ioloop.stop()

    async def _handle_server_restart(self, web_request: WebRequest) -> str:
        self.ioloop.spawn_callback(self._stop_server)
        return "ok"

    async def _handle_info_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        file_manager: Optional[FileManager] = self.lookup_component(
            'file_manager', None)
        reg_dirs = []
        if file_manager is not None:
            reg_dirs = file_manager.get_registered_dirs()
        return {
            'klippy_connected': self.klippy_connection.is_connected(),
            'klippy_state': self.klippy_state,
            'components': list(self.components.keys()),
            'failed_components': self.failed_components,
            'plugins': list(self.components.keys()),
            'failed_plugins': self.failed_components,
            'registered_directories': reg_dirs,
            'warnings': self.warnings
        }

    async def _handle_config_request(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, Any]:
        return {
            'config': self.config.get_parsed_config()
        }

class KlippyConnection:
    def __init__(self,
                 on_recd: Callable[[dict], None],
                 on_close: Callable[[], None]
                 ) -> None:
        self.ioloop = IOLoop.current()
        self.iostream: Optional[iostream.IOStream] = None
        self.on_recd = on_recd
        self.on_close = on_close

    async def connect(self, address: str) -> bool:
        ksock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        kstream = iostream.IOStream(ksock)
        try:
            await kstream.connect(address)
        except iostream.StreamClosedError:
            return False
        logging.info("Klippy Connection Established")
        self.iostream = kstream
        self.iostream.set_close_callback(self.on_close)
        self.ioloop.spawn_callback(self._read_stream, self.iostream)
        return True

    async def _read_stream(self, stream: iostream.IOStream) -> None:
        while not stream.closed():
            try:
                data = await stream.read_until(b'\x03')
            except iostream.StreamClosedError as e:
                return
            except Exception:
                logging.exception("Klippy Stream Read Error")
                continue
            try:
                decoded_cmd = json.loads(data[:-1])
                self.on_recd(decoded_cmd)
            except Exception:
                logging.exception(
                    f"Error processing Klippy Host Response: {data.decode()}")

    async def send_request(self, request: BaseRequest) -> None:
        if self.iostream is None:
            request.notify(ServerError("Klippy Host not connected", 503))
            return
        data = json.dumps(request.to_dict()).encode() + b"\x03"
        try:
            await self.iostream.write(data)
        except iostream.StreamClosedError:
            request.notify(ServerError("Klippy Host not connected", 503))

    def is_connected(self) -> bool:
        return self.iostream is not None and not self.iostream.closed()

    def close(self) -> None:
        if self.iostream is not None and \
                not self.iostream.closed():
            self.iostream.close()

# Basic WebRequest class, easily converted to dict for json encoding
class BaseRequest:
    def __init__(self, rpc_method: str, params: Dict[str, Any]) -> None:
        self.id = id(self)
        self.rpc_method = rpc_method
        self.params = params
        self._event = Event()
        self.response: Any = None

    async def wait(self) -> Any:
        # Log pending requests every 60 seconds
        start_time = time.time()
        while True:
            timeout = time.time() + 60.
            try:
                await self._event.wait(timeout=timeout)
            except TimeoutError:
                pending_time = time.time() - start_time
                logging.info(
                    f"Request '{self.rpc_method}' pending: "
                    f"{pending_time:.2f} seconds")
                self._event.clear()
                continue
            break
        if isinstance(self.response, ServerError):
            raise self.response
        return self.response

    def notify(self, response: Any) -> None:
        self.response = response
        self._event.set()

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'method': self.rpc_method,
                'params': self.params}

def main() -> None:
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
    system_args = parser.parse_args()

    # Setup Logging
    version = utils.get_software_version()
    if system_args.nologfile:
        log_file = ""
    else:
        log_file = os.path.normpath(os.path.expanduser(
            system_args.logfile))
    system_args.logfile = log_file
    system_args.software_version = version
    ql, file_logger = utils.setup_logging(log_file, version)

    if sys.version_info < (3, 7):
        msg = f"Moonraker requires Python 3.7 or above.  " \
            f"Detected Version: {sys.version}"
        logging.info(msg)
        print(msg)
        ql.stop()
        exit(1)

    # Start IOLoop and Server
    io_loop = IOLoop.current()
    estatus = 0
    while True:
        try:
            server = Server(system_args, file_logger)
        except Exception:
            logging.exception("Moonraker Error")
            estatus = 1
            break
        try:
            server.start()
            io_loop.start()
        except Exception:
            logging.exception("Server Running Error")
            estatus = 1
            break
        if server.exit_reason == "terminate":
            break
        # Since we are running outside of the the server
        # it is ok to use a blocking sleep here
        time.sleep(.5)
        logging.info("Attempting Server Restart...")
    io_loop.close(True)
    logging.info("Server Shutdown")
    ql.stop()
    exit(estatus)


if __name__ == '__main__':
    main()
