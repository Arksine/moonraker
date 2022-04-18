
# KlippyConnection - manage unix socket connection to Klipper
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import time
import logging
import json
import getpass
import confighelper
import asyncio
import socket
import struct
from utils import ServerError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Optional,
    Callable,
    Coroutine,
    Dict,
    List,
    Set,
)
if TYPE_CHECKING:
    from app import MoonrakerApp
    from websockets import WebRequest, Subscribable
    from components.klippy_apis import KlippyAPI
    from components.file_manager.file_manager import FileManager
    from asyncio.trsock import TransportSocket
    FlexCallback = Callable[..., Optional[Coroutine]]

INIT_TIME = .25
LOG_ATTEMPT_INTERVAL = int(2. / INIT_TIME + .5)
MAX_LOG_ATTEMPTS = 10 * LOG_ATTEMPT_INTERVAL
UNIX_BUFFER_LIMIT = 2 * 1024 * 1024

class KlippyConnection:
    def __init__(self, config: confighelper.ConfigHelper) -> None:
        self.server = config.get_server()
        self.uds_address: str = config.get(
            'klippy_uds_address', "/tmp/klippy_uds")
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connection_mutex: asyncio.Lock = asyncio.Lock()
        self.event_loop = self.server.get_event_loop()
        self.log_no_access = True
        # Connection State
        self.connection_task: Optional[asyncio.Task] = None
        self.closing: bool = False
        self._klippy_info: Dict[str, Any] = {}
        self.init_list: List[str] = []
        self._klipper_version: str = ""
        self._missing_reqs: Set[str] = set()
        self._peer_cred: Dict[str, int] = {}
        self.init_attempts: int = 0
        self._state: str = "disconnected"
        self.subscriptions: Dict[Subscribable, Dict[str, Any]] = {}
        # Setup remote methods accessable to Klippy.  Note that all
        # registered remote methods should be of the notification type,
        # they do not return a response to Klippy after execution
        self.pending_requests: Dict[int, KlippyRequest] = {}
        self.remote_methods: Dict[str, FlexCallback] = {}
        self.klippy_reg_methods: List[str] = []
        self.register_remote_method(
            'process_gcode_response', self._process_gcode_response,
            need_klippy_reg=False)
        self.register_remote_method(
            'process_status_update', self._process_status_update,
            need_klippy_reg=False)
        self.server.register_component("klippy_connection", self)

    @property
    def klippy_apis(self) -> KlippyAPI:
        return self.server.lookup_component("klippy_apis")

    @property
    def state(self) -> str:
        return self._state

    @property
    def klippy_info(self) -> Dict[str, Any]:
        return self._klippy_info

    @property
    def missing_requirements(self) -> List[str]:
        return list(self._missing_reqs)

    @property
    def peer_credentials(self) -> Dict[str, int]:
        return dict(self._peer_cred)

    async def wait_connected(self) -> bool:
        if (
            self.connection_task is None or
            self.connection_task.done()
        ):
            return self.is_connected()
        try:
            await self.connection_task
        except Exception:
            pass
        return self.is_connected()

    async def wait_started(self, timeout: float = 20.) -> bool:
        if self.connection_task is None or not self.is_connected():
            return False
        if not self.connection_task.done():
            await asyncio.wait_for(
                asyncio.shield(self.connection_task), timeout=timeout)
        return self.is_connected()

    async def _read_stream(self, reader: asyncio.StreamReader) -> None:
        errors_remaining: int = 10
        while not reader.at_eof():
            try:
                data = await reader.readuntil(b'\x03')
            except (ConnectionError, asyncio.IncompleteReadError):
                break
            except asyncio.CancelledError:
                logging.exception("Klippy Stream Read Cancelled")
                raise
            except Exception:
                logging.exception("Klippy Stream Read Error")
                errors_remaining -= 1
                if not errors_remaining or not self.is_connected():
                    break
                continue
            errors_remaining = 10
            try:
                decoded_cmd = json.loads(data[:-1])
                self._process_command(decoded_cmd)
            except Exception:
                logging.exception(
                    f"Error processing Klippy Host Response: {data.decode()}")
        if not self.closing:
            logging.debug("Klippy Disconnection From _read_stream()")
            await self.close()

    async def _write_request(self, request: KlippyRequest) -> None:
        if self.writer is None or self.closing:
            self.pending_requests.pop(request.id, None)
            request.notify(ServerError("Klippy Host not connected", 503))
            return
        data = json.dumps(request.to_dict()).encode() + b"\x03"
        try:
            self.writer.write(data)
            await self.writer.drain()
        except asyncio.CancelledError:
            self.pending_requests.pop(request.id, None)
            request.notify(ServerError("Klippy Write Request Cancelled", 503))
            raise
        except Exception:
            self.pending_requests.pop(request.id, None)
            request.notify(ServerError("Klippy Write Request Error", 503))
            if not self.closing:
                logging.debug("Klippy Disconnection From _write_request()")
                await self.close()

    def register_remote_method(self,
                               method_name: str,
                               cb: FlexCallback,
                               need_klippy_reg: bool = True
                               ) -> None:
        if method_name in self.remote_methods:
            raise self.server.error(
                f"Remote method ({method_name}) already registered")
        if self.server.is_running():
            raise self.server.error(
                f"Failed to register remote method {method_name}, "
                "methods must be registered during initialization")
        self.remote_methods[method_name] = cb
        if need_klippy_reg:
            # These methods need to be registered with Klippy
            self.klippy_reg_methods.append(method_name)

    def connect(self) -> Awaitable[bool]:
        if (
            self.is_connected() or
            not self.server.is_running() or
            (self.connection_task is not None and
             not self.connection_task.done())
        ):
            # already connecting
            fut = self.event_loop.create_future()
            fut.set_result(self.is_connected())
            return fut
        self.connection_task = self.event_loop.create_task(self._do_connect())
        return self.connection_task

    async def _do_connect(self) -> bool:
        async with self.connection_mutex:
            while self.writer is None:
                await asyncio.sleep(INIT_TIME)
                if self.closing or not self.server.is_running():
                    return False
                if not os.path.exists(self.uds_address):
                    continue
                if not os.access(self.uds_address, os.R_OK | os.W_OK):
                    if self.log_no_access:
                        user = getpass.getuser()
                        logging.info(
                            f"Cannot connect to Klippy, Linux user '{user}' "
                            "lacks permission to open Unix Domain Socket: "
                            f"{self.uds_address}")
                        self.log_no_access = False
                    continue
                self.log_no_access = True
                try:
                    reader, writer = await asyncio.open_unix_connection(
                        self.uds_address, limit=UNIX_BUFFER_LIMIT)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                logging.info("Klippy Connection Established")
                self.writer = writer
                self._get_peer_credentials(writer)
            self.event_loop.create_task(self._read_stream(reader))
            return await self._init_klippy_connection()

    def _get_peer_credentials(self, writer: asyncio.StreamWriter) -> None:
        sock: TransportSocket
        sock = writer.get_extra_info("socket", None)
        if sock is None:
            logging.debug(
                "Unable to get Unix Socket, cant fetch peer credentials"
            )
            return
        try:
            data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            pid, uid, gid = struct.unpack("@LLL", data)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Failed to get Klippy Credentials")
            return
        self._peer_cred = {
            "process_id": pid,
            "user_id": uid,
            "group_id": gid
        }
        logging.debug(
            f"Klippy Connection: Received Peer Credentials: {self._peer_cred}"
        )

    async def _init_klippy_connection(self) -> bool:
        self.init_list = []
        self._missing_reqs.clear()
        self.init_attempts = 0
        self._state = "initializing"
        webhooks_err_logged = False
        gcout_err_logged = False
        while self.server.is_running():
            await asyncio.sleep(INIT_TIME)
            # Subscribe to "webhooks"
            # Register "webhooks" subscription
            if "webhooks_sub" not in self.init_list:
                try:
                    await self.klippy_apis.subscribe_objects(
                        {'webhooks': None})
                except ServerError as e:
                    if not webhooks_err_logged:
                        webhooks_err_logged = True
                        logging.info(
                            f"{e}\nUnable to subscribe to webhooks object")
                else:
                    logging.info("Webhooks Subscribed")
                    self.init_list.append("webhooks_sub")
            # Subscribe to Gcode Output
            if "gcode_output_sub" not in self.init_list:
                try:
                    await self.klippy_apis.subscribe_gcode_output()
                except ServerError as e:
                    if not gcout_err_logged:
                        gcout_err_logged = True
                        logging.info(
                            f"{e}\nUnable to register gcode output "
                            "subscription")
                else:
                    logging.info("GCode Output Subscribed")
                    self.init_list.append("gcode_output_sub")
            if "startup_complete" not in self.init_list:
                await self._check_ready()
            if len(self.init_list) == 4:
                logging.debug("Klippy Connection Initialized")
                return True
            elif not self.is_connected():
                break
            else:
                self.init_attempts += 1
        logging.debug("Klippy Connection Failed to Init")
        return False

    async def _request_endpoints(self) -> None:
        result = await self.klippy_apis.list_endpoints(default=None)
        if result is None:
            return
        endpoints = result.get('endpoints', [])
        app: MoonrakerApp = self.server.lookup_component("application")
        for ep in endpoints:
            app.register_remote_handler(ep)

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
        version = result.get("software_version", "")
        if version != self._klipper_version:
            self._klipper_version = version
            msg = f"Klipper Version: {version}"
            self.server.add_log_rollover_item("klipper_version", msg)
        self._klippy_info = dict(result)
        self._state = result.get('state', "unknown")
        if send_id:
            self.init_list.append("identified")
            await self.server.send_event("server:klippy_identified")
        if self._state != "startup":
            self.init_list.append('startup_complete')
            await self._request_endpoints()
            await self.server.send_event("server:klippy_started",
                                         self._state)
            if self._state != "ready":
                msg = result.get('state_message', "Klippy Not Ready")
                logging.info("\n" + msg)
            else:
                await self._verify_klippy_requirements()
                # register methods with klippy
                for method in self.klippy_reg_methods:
                    try:
                        await self.klippy_apis.register_method(method)
                    except ServerError:
                        logging.exception(
                            f"Unable to register method '{method}'")
                logging.info("Klippy ready")
                await self.server.send_event("server:klippy_ready")

    async def _verify_klippy_requirements(self) -> None:
        result = await self.klippy_apis.get_object_list(default=None)
        if result is None:
            logging.info(
                f"Unable to retrieve Klipper Object List")
            return
        req_objs = set(["virtual_sdcard", "display_status", "pause_resume"])
        self._missing_reqs = req_objs - set(result)
        if self._missing_reqs:
            err_str = ", ".join([f"[{o}]" for o in self._missing_reqs])
            logging.info(
                f"\nWarning, unable to detect the following printer "
                f"objects:\n{err_str}\nPlease add the the above sections "
                f"to printer.cfg for full Moonraker functionality.")
        if "virtual_sdcard" not in self._missing_reqs:
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
                    file_manager: FileManager = self.server.lookup_component(
                        'file_manager')
                    file_manager.register_directory('gcodes', vsd_path,
                                                    full_access=True)
                else:
                    logging.info(
                        "Configuration for [virtual_sdcard] not found,"
                        " unable to set SD Card path")

    def _process_command(self, cmd: Dict[str, Any]) -> None:
        method = cmd.get('method', None)
        if method is not None:
            # This is a remote method called from klippy
            if method in self.remote_methods:
                params = cmd.get('params', {})
                self.event_loop.register_callback(
                    self._execute_method, method, **params)
            else:
                logging.info(f"Unknown method received: {method}")
            return
        # This is a response to a request, process
        req_id = cmd.get('id', None)
        request: Optional[KlippyRequest]
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

    def _process_gcode_response(self, response: str) -> None:
        self.server.send_event("server:gcode_response", response)

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
                    self.server.send_event("server:klippy_shutdown")
                self._state = state
        for conn, sub in self.subscriptions.items():
            conn_status: Dict[str, Any] = {}
            for name, fields in sub.items():
                if name in status:
                    val: Dict[str, Any] = dict(status[name])
                    if fields is not None:
                        val = {k: v for k, v in val.items() if k in fields}
                    if val:
                        conn_status[name] = val
            conn.send_status(conn_status, eventtime)

    async def request(self, web_request: WebRequest) -> Any:
        if not self.is_connected():
            raise ServerError("Klippy Host not connected", 503)
        rpc_method = web_request.get_endpoint()
        if rpc_method == "objects/subscribe":
            return await self._request_subscripton(web_request)
        else:
            if rpc_method == "gcode/script":
                script = web_request.get_str('script', "")
                if script:
                    self.server.send_event(
                        "klippy_connection:gcode_received", script)
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
            raise self.server.error(
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
        base_request = KlippyRequest(rpc_method, args)
        self.pending_requests[base_request.id] = base_request
        self.event_loop.register_callback(self._write_request, base_request)
        return await base_request.wait()

    def remove_subscription(self, conn: Subscribable) -> None:
        self.subscriptions.pop(conn, None)

    def is_connected(self) -> bool:
        return self.writer is not None and not self.closing

    async def _on_connection_closed(self) -> None:
        self.init_list = []
        self._state = "disconnected"
        for request in self.pending_requests.values():
            request.notify(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        self.subscriptions = {}
        self._peer_cred = {}
        self._missing_reqs.clear()
        logging.info("Klippy Connection Removed")
        await self.server.send_event("server:klippy_disconnect")
        if self.server.is_running():
            # Reconnect if server is running
            loop = self.event_loop
            self.connection_task = loop.create_task(self._do_connect())

    async def close(self, wait_closed: bool = False) -> None:
        if self.closing:
            if wait_closed:
                await self.connection_mutex.acquire()
                self.connection_mutex.release()
            return
        self.closing = True
        if (
            self.connection_task is not None and
            not self.connection_task.done()
        ):
            self.connection_task.cancel()
        async with self.connection_mutex:
            if self.writer is not None:
                try:
                    self.writer.close()
                    await self.writer.wait_closed()
                except Exception:
                    logging.exception("Error closing Klippy Unix Socket")
                self.writer = None
                await self._on_connection_closed()
        self.closing = False

# Basic KlippyRequest class, easily converted to dict for json encoding
class KlippyRequest:
    def __init__(self, rpc_method: str, params: Dict[str, Any]) -> None:
        self.id = id(self)
        self.rpc_method = rpc_method
        self.params = params
        self._event = asyncio.Event()
        self.response: Any = None

    async def wait(self) -> Any:
        # Log pending requests every 60 seconds
        start_time = time.time()
        while True:
            try:
                await asyncio.wait_for(self._event.wait(), 60.)
            except asyncio.TimeoutError:
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
        if self._event.is_set():
            return
        self.response = response
        self._event.set()

    def to_dict(self) -> Dict[str, Any]:
        return {'id': self.id, 'method': self.rpc_method,
                'params': self.params}
