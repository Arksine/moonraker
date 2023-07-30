
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
import asyncio
import pathlib
from .utils import ServerError, get_unix_peer_credentials

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
    Tuple
)
if TYPE_CHECKING:
    from .server import Server
    from .app import MoonrakerApp
    from .common import WebRequest, Subscribable
    from .confighelper import ConfigHelper
    from .components.klippy_apis import KlippyAPI
    from .components.file_manager.file_manager import FileManager
    from .components.machine import Machine
    from .components.job_state import JobState
    from .components.database import MoonrakerDatabase as Database
    FlexCallback = Callable[..., Optional[Coroutine]]
    Subscription = Dict[str, Optional[List[str]]]

# These endpoints are reserved for klippy/moonraker communication only and are
# not exposed via http or the websocket
RESERVED_ENDPOINTS = [
    "list_endpoints",
    "gcode/subscribe_output",
    "register_remote_method",
]

INIT_TIME = .25
LOG_ATTEMPT_INTERVAL = int(2. / INIT_TIME + .5)
MAX_LOG_ATTEMPTS = 10 * LOG_ATTEMPT_INTERVAL
UNIX_BUFFER_LIMIT = 20 * 1024 * 1024
SVC_INFO_KEY = "klippy_connection.service_info"

class KlippyConnection:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.uds_address = pathlib.Path("/tmp/klippy_uds")
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connection_mutex: asyncio.Lock = asyncio.Lock()
        self.event_loop = self.server.get_event_loop()
        self.log_no_access = True
        # Connection State
        self.connection_task: Optional[asyncio.Task] = None
        self.closing: bool = False
        self.subscription_lock = asyncio.Lock()
        self._klippy_info: Dict[str, Any] = {}
        self._klippy_identified: bool = False
        self._klippy_initializing: bool = False
        self._klippy_started: bool = False
        self._klipper_version: str = ""
        self._missing_reqs: Set[str] = set()
        self._peer_cred: Dict[str, int] = {}
        self._service_info: Dict[str, Any] = {}
        self.init_attempts: int = 0
        self._state: str = "disconnected"
        self._state_message: str = "Klippy Disconnected"
        self.subscriptions: Dict[Subscribable, Subscription] = {}
        self.subscription_cache: Dict[str, Dict[str, Any]] = {}
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

    def configure(self, config: ConfigHelper):
        self.uds_address = config.getpath(
            "klippy_uds_address", self.uds_address
        )

    @property
    def klippy_apis(self) -> KlippyAPI:
        return self.server.lookup_component("klippy_apis")

    @property
    def state(self) -> str:
        if self.is_connected() and not self._klippy_started:
            return "startup"
        return self._state

    @property
    def state_message(self) -> str:
        return self._state_message

    @property
    def klippy_info(self) -> Dict[str, Any]:
        return self._klippy_info

    @property
    def missing_requirements(self) -> List[str]:
        return list(self._missing_reqs)

    @property
    def peer_credentials(self) -> Dict[str, int]:
        return dict(self._peer_cred)

    @property
    def service_info(self) -> Dict[str, Any]:
        return self._service_info

    @property
    def unit_name(self) -> str:
        svc_info = self._service_info
        unit_name = svc_info.get("unit_name", "klipper.service")
        return unit_name.split(".", 1)[0]

    async def component_init(self) -> None:
        db: Database = self.server.lookup_component('database')
        machine: Machine = self.server.lookup_component("machine")
        self._service_info = await db.get_item("moonraker", SVC_INFO_KEY, {})
        if self._service_info:
            machine.log_service_info(self._service_info)

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
            request.set_exception(ServerError("Klippy Host not connected", 503))
            return
        data = json.dumps(request.to_dict()).encode() + b"\x03"
        try:
            self.writer.write(data)
            await self.writer.drain()
        except asyncio.CancelledError:
            request.set_exception(ServerError("Klippy Write Request Cancelled", 503))
            raise
        except Exception:
            request.set_exception(ServerError("Klippy Write Request Error", 503))
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
                if not self.uds_address.exists():
                    continue
                if not os.access(str(self.uds_address), os.R_OK | os.W_OK):
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
                    reader, writer = await self.open_klippy_connection(True)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    continue
                logging.info("Klippy Connection Established")
                self.writer = writer
                if self._get_peer_credentials(writer):
                    machine: Machine = self.server.lookup_component("machine")
                    provider = machine.get_system_provider()
                    svc_info = await provider.extract_service_info(
                        "klipper", self._peer_cred["process_id"]
                    )
                    if svc_info != self._service_info:
                        db: Database = self.server.lookup_component('database')
                        db.insert_item("moonraker", SVC_INFO_KEY, svc_info)
                        self._service_info = svc_info
                        machine.log_service_info(svc_info)
            self.event_loop.create_task(self._read_stream(reader))
            return await self._init_klippy_connection()

    async def open_klippy_connection(
        self, primary: bool = False
    ) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if not primary and not self.is_connected():
            raise ServerError("Klippy Unix Connection Not Available", 503)
        return await asyncio.open_unix_connection(
            str(self.uds_address), limit=UNIX_BUFFER_LIMIT)

    def _get_peer_credentials(self, writer: asyncio.StreamWriter) -> bool:
        self._peer_cred = get_unix_peer_credentials(writer, "Klippy")
        if not self._peer_cred:
            return False
        logging.debug(
            f"Klippy Connection: Received Peer Credentials: {self._peer_cred}"
        )
        return True

    async def _init_klippy_connection(self) -> bool:
        self._klippy_identified = False
        self._klippy_started = False
        self._klippy_initializing = True
        self._missing_reqs.clear()
        self.init_attempts = 0
        self._state = "startup"
        while self.server.is_running():
            await asyncio.sleep(INIT_TIME)
            await self._check_ready()
            if not self._klippy_initializing:
                logging.debug("Klippy Connection Initialized")
                return True
            if not self.is_connected():
                self._klippy_initializing = False
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
            if ep not in RESERVED_ENDPOINTS:
                app.register_remote_handler(ep)

    async def _request_initial_subscriptions(self) -> None:
        try:
            await self.klippy_apis.subscribe_objects({'webhooks': None})
        except ServerError as e:
            logging.exception("Unable to subscribe to webhooks object")
        else:
            logging.info("Webhooks Subscribed")
        try:
            await self.klippy_apis.subscribe_gcode_output()
        except ServerError as e:
            logging.exception(
                "Unable to register gcode output subscription"
            )
        else:
            logging.info("GCode Output Subscribed")

    async def _check_ready(self) -> None:
        send_id = not self._klippy_identified
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
        if "state_message" in self._klippy_info:
            self._state_message = self._klippy_info["state_message"]
        if "state" not in result:
            return
        if send_id:
            self._klippy_identified = True
            await self.server.send_event("server:klippy_identified")
            # Request initial endpoints to register info, emergency stop APIs
            await self._request_endpoints()
        self._state = result["state"]
        if self._state != "startup":
            await self._request_initial_subscriptions()
            # Register remaining endpoints available
            await self._request_endpoints()
            startup_state = self._state
            await self.server.send_event(
                "server:klippy_started", startup_state
            )
            self._klippy_started = True
            if self._state != "ready":
                logging.info("\n" + self._state_message)
                if self._state == "shutdown" and startup_state != "shutdown":
                    # Klippy shutdown during startup event
                    self.server.send_event("server:klippy_shutdown")
            else:
                await self._verify_klippy_requirements()
                # register methods with klippy
                for method in self.klippy_reg_methods:
                    try:
                        await self.klippy_apis.register_method(method)
                    except ServerError:
                        logging.exception(
                            f"Unable to register method '{method}'")
                if self._state == "ready":
                    logging.info("Klippy ready")
                    await self.server.send_event("server:klippy_ready")
                    if self._state == "shutdown":
                        # Klippy shutdown during ready event
                        self.server.send_event("server:klippy_shutdown")
                else:
                    logging.info(
                        "Klippy state transition from ready during init, "
                        f"new state: {self._state}"
                    )
            self._klippy_initializing = False

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
                    file_manager.validate_gcode_path(vsd_path)
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
            request.set_result(result)
        else:
            err = cmd.get('error', "Malformed Klippy Response")
            request.set_exception(ServerError(err, 400))

    async def _execute_method(self, method_name: str, **kwargs) -> None:
        try:
            ret = self.remote_methods[method_name](**kwargs)
            if ret is not None:
                await ret
        except Exception:
            logging.exception(f"Error running remote method: {method_name}")

    def _process_gcode_response(self, response: str) -> None:
        self.server.send_event("server:gcode_response", response)

    def _process_status_update(
        self, eventtime: float, status: Dict[str, Dict[str, Any]]
    ) -> None:
        for field, item in status.items():
            self.subscription_cache.setdefault(field, {}).update(item)
        if 'webhooks' in status:
            wh: Dict[str, str] = status['webhooks']
            if "state_message" in wh:
                self._state_message = wh["state_message"]
            # XXX - process other states (startup, ready, error, etc)?
            if "state" in wh:
                state = wh["state"]
                if (
                    state == "shutdown" and
                    not self._klippy_initializing and
                    self._state != "shutdown"
                ):
                    # If the shutdown state is received during initialization
                    # defer the event, the init routine will handle it.
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

    async def _request_subscripton(self, web_request: WebRequest) -> Dict[str, Any]:
        async with self.subscription_lock:
            args = web_request.get_args()
            conn = web_request.get_subscribable()
            if conn is None:
                raise self.server.error(
                    "No connection associated with subscription request"
                )
            requested_sub: Subscription = args.get('objects', {})
            if self.server.is_verbose_enabled() and "configfile" in requested_sub:
                cfg_sub = requested_sub["configfile"]
                if (
                    cfg_sub is None or "config" in cfg_sub or "settings" in cfg_sub
                ):
                    logging.debug(
                        f"Detected 'configfile: {cfg_sub}' subscription.  The "
                        "'config' and 'status' fields in this object do not change "
                        "and substantially increase cache size."
                    )
            all_subs: Subscription = dict(requested_sub)
            # Build the subscription request from a superset of all client subscriptions
            for sub in self.subscriptions.values():
                for obj, items in sub.items():
                    if obj in all_subs:
                        prev_items = all_subs[obj]
                        if items is None or prev_items is None:
                            all_subs[obj] = None
                        else:
                            uitems = list(set(prev_items) | set(items))
                            all_subs[obj] = uitems
                    else:
                        all_subs[obj] = items
            args['objects'] = all_subs
            args['response_template'] = {'method': "process_status_update"}

            result = await self._request_standard(web_request, 20.0)

            # prune the status response
            pruned_status: Dict[str, Dict[str, Any]] = {}
            status_diff: Dict[str, Dict[str, Any]] = {}
            all_status: Dict[str, Dict[str, Any]] = result['status']
            for obj, fields in all_status.items():
                # Diff the current cache, then update the cache
                if obj in self.subscription_cache:
                    cached_status = self.subscription_cache[obj]
                    for field_name, value in fields.items():
                        if field_name not in cached_status:
                            continue
                        if value != cached_status[field_name]:
                            status_diff.setdefault(obj, {})[field_name] = value
                self.subscription_cache[obj] = fields
                # Prune Response
                if obj in requested_sub:
                    valid_fields = requested_sub[obj]
                    if valid_fields is None:
                        pruned_status[obj] = fields
                    else:
                        pruned_status[obj] = {
                            k: v for k, v in fields.items() if k in valid_fields
                        }
            if status_diff:
                # The response to the status request contains changed data, so it
                # is necessary to manually push the status update to existing
                # subscribers
                logging.debug(
                    f"Detected status difference during subscription: {status_diff}"
                )
                self._process_status_update(result["eventtime"], status_diff)
            for obj_name in list(self.subscription_cache.keys()):
                # Prune the cache to match the current status response
                if obj_name not in all_status:
                    del self.subscription_cache[obj_name]
            result['status'] = pruned_status
            self.subscriptions[conn] = requested_sub
            return result

    async def _request_standard(
        self, web_request: WebRequest, timeout: Optional[float] = None
    ) -> Any:
        rpc_method = web_request.get_endpoint()
        args = web_request.get_args()
        # Create a base klippy request
        base_request = KlippyRequest(rpc_method, args)
        self.pending_requests[base_request.id] = base_request
        self.event_loop.register_callback(self._write_request, base_request)
        try:
            return await base_request.wait(timeout)
        finally:
            self.pending_requests.pop(base_request.id, None)

    def remove_subscription(self, conn: Subscribable) -> None:
        self.subscriptions.pop(conn, None)

    def is_connected(self) -> bool:
        return self.writer is not None and not self.closing

    def is_ready(self) -> bool:
        return self._state == "ready"

    def is_printing(self) -> bool:
        if not self.is_ready():
            return False
        job_state: JobState = self.server.lookup_component("job_state")
        stats = job_state.get_last_stats()
        return stats.get("state", "") == "printing"

    def get_subscription_cache(self) -> Dict[str, Dict[str, Any]]:
        return self.subscription_cache

    async def rollover_log(self) -> None:
        if "unit_name" not in self._service_info:
            raise self.server.error(
                "Unable to detect Klipper Service, cannot perform "
                "manual rollover"
            )
        logfile: Optional[str] = self._klippy_info.get("log_file", None)
        if logfile is None:
            raise self.server.error(
                "Unable to detect path to Klipper log file"
            )
        if self.is_printing():
            raise self.server.error("Cannot rollover log while printing")
        logpath = pathlib.Path(logfile).expanduser().resolve()
        if not logpath.is_file():
            raise self.server.error(
                f"No file at {logpath} exists, cannot perform rollover"
            )
        machine: Machine = self.server.lookup_component("machine")
        await machine.do_service_action("stop", self.unit_name)
        suffix = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
        new_path = pathlib.Path(f"{logpath}.{suffix}")

        def _do_file_op() -> None:
            if new_path.exists():
                new_path.unlink()
            logpath.rename(new_path)

        await self.event_loop.run_in_thread(_do_file_op)
        await machine.do_service_action("start", self.unit_name)

    async def _on_connection_closed(self) -> None:
        self._klippy_identified = False
        self._klippy_initializing = False
        self._klippy_started = False
        self._state = "disconnected"
        self._state_message = "Klippy Disconnected"
        for request in self.pending_requests.values():
            request.set_exception(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        self.subscriptions = {}
        self.subscription_cache.clear()
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
        self._fut = asyncio.get_running_loop().create_future()

    async def wait(self, timeout: Optional[float] = None) -> Any:
        start_time = time.time()
        to = timeout or 60.
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(self._fut), to)
            except asyncio.TimeoutError:
                if timeout is not None:
                    self._fut.cancel()
                    raise ServerError("Klippy request timed out", 500) from None
                pending_time = time.time() - start_time
                logging.info(
                    f"Request '{self.rpc_method}' pending: "
                    f"{pending_time:.2f} seconds"
                )

    def set_exception(self, exc: Exception) -> None:
        if not self._fut.done():
            self._fut.set_exception(exc)

    def set_result(self, result: Any) -> None:
        if not self._fut.done():
            self._fut.set_result(result)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'method': self.rpc_method,
            'params': self.params
        }
