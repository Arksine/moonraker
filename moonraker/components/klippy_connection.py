
# KlippyConnection - manage unix socket connection to Klipper
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import time
import logging
import getpass
import asyncio
import pathlib
from ..utils import ServerError, get_unix_peer_credentials
from ..utils import json_wrapper as jsonw
from ..common import KlippyState, RequestType

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
    Tuple,
    Union
)
if TYPE_CHECKING:
    from ..common import WebRequest, APITransport, BaseRemoteConnection
    from ..confighelper import ConfigHelper
    from .klippy_apis import KlippyAPI
    from .file_manager.file_manager import FileManager
    from .machine import Machine
    from .job_state import JobState
    from .database import MoonrakerDatabase as Database
    FlexCallback = Callable[..., Optional[Coroutine]]
    Subscription = Dict[str, Optional[List[str]]]

# These endpoints are reserved for klippy/moonraker communication only and are
# not exposed via http or the websocket
RESERVED_ENDPOINTS = [
    "list_endpoints",
    "gcode/subscribe_output",
    "register_remote_method",
]

# Items to exclude from the subscription cache.  They never change and can be
# quite large.
CACHE_EXCLUSIONS = {
    "configfile": ["config", "settings"]
}

INIT_TIME = .25
LOG_ATTEMPT_INTERVAL = int(2. / INIT_TIME + .5)
MAX_LOG_ATTEMPTS = 10 * LOG_ATTEMPT_INTERVAL
UNIX_BUFFER_LIMIT = 20 * 1024 * 1024
SVC_INFO_KEY = "klippy_connection.service_info"
SRC_PATH_KEY = "klippy_connection.path"
PY_EXEC_KEY = "klippy_connection.executable"

class KlippyConnection:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.uds_address = config.getpath(
            "klippy_uds_address", pathlib.Path("/tmp/klippy_uds")
        )
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
        self._methods_registered: bool = False
        self._klipper_version: str = ""
        self._missing_reqs: Set[str] = set()
        self._peer_cred: Dict[str, int] = {}
        self._service_info: Dict[str, Any] = {}
        self._path = pathlib.Path("~/klipper").expanduser()
        self._executable = pathlib.Path("~/klippy-env/bin/python").expanduser()
        self.init_attempts: int = 0
        self._state: KlippyState = KlippyState.DISCONNECTED
        self._state.set_message("Klippy Disconnected")
        self.subscriptions: Dict[APITransport, Subscription] = {}
        self.subscription_cache: Dict[str, Dict[str, Any]] = {}
        # Setup remote methods accessible to Klippy.  Note that all
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

    @property
    def klippy_apis(self) -> KlippyAPI:
        return self.server.lookup_component("klippy_apis")

    @property
    def state(self) -> KlippyState:
        if self.is_connected() and not self._klippy_started:
            return KlippyState.STARTUP
        return self._state

    @property
    def state_message(self) -> str:
        return self._state.message

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

    @property
    def path(self) -> pathlib.Path:
        return self._path

    @property
    def executable(self) -> pathlib.Path:
        return self._executable

    def load_saved_state(self) -> None:
        db: Database = self.server.lookup_component("database")
        sync_provider = db.get_provider_wrapper()
        kc_info: Dict[str, Any]
        kc_info = sync_provider.get_item("moonraker", "klippy_connection", {})
        self._path = pathlib.Path(kc_info.get("path", str(self._path)))
        self._executable = pathlib.Path(kc_info.get("executable", str(self.executable)))
        self._service_info = kc_info.get("service_info", {})

    async def component_init(self) -> None:
        machine: Machine = self.server.lookup_component("machine")
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
                decoded_cmd = jsonw.loads(data[:-1])
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
        data = jsonw.dumps(request.to_dict()) + b"\x03"
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

    def register_method_from_agent(
        self, connection: BaseRemoteConnection, method_name: str
    ) -> Optional[Awaitable]:
        if connection.client_data["type"] != "agent":
            raise self.server.error(
                "Only connections of the 'agent' type can register methods"
            )
        if method_name in self.remote_methods:
            raise self.server.error(
                f"Remote method ({method_name}) already registered"
            )

        def _on_agent_method_received(**kwargs) -> None:
            connection.call_method(method_name, kwargs)
        self.remote_methods[method_name] = _on_agent_method_received
        self.klippy_reg_methods.append(method_name)
        if self._methods_registered and self._state != KlippyState.DISCONNECTED:
            coro = self.klippy_apis.register_method(method_name)
            return self.event_loop.create_task(coro)
        return None

    def unregister_method(self, method_name: str):
        self.remote_methods.pop(method_name, None)
        try:
            self.klippy_reg_methods.remove(method_name)
        except ValueError:
            pass

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
        return self.connection_task  # type: ignore

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
                    await self._get_service_info(self._peer_cred["process_id"])
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
        peer_cred = get_unix_peer_credentials(writer, "Klippy")
        if not peer_cred:
            return False
        if peer_cred.get("process_id") == 1:
            logging.debug("Klipper Unix Socket created via Systemd Socket Activation")
            return False
        self._peer_cred = peer_cred
        logging.debug(
            f"Klippy Connection: Received Peer Credentials: {self._peer_cred}"
        )
        return True

    async def _get_service_info(self, process_id: int) -> None:
        machine: Machine = self.server.lookup_component("machine")
        provider = machine.get_system_provider()
        svc_info = await provider.extract_service_info("klipper", process_id)
        if svc_info != self._service_info:
            db: Database = self.server.lookup_component('database')
            db.insert_item("moonraker", SVC_INFO_KEY, svc_info)
            self._service_info = svc_info
            machine.log_service_info(svc_info)

    def _save_path_info(self) -> None:
        kpath = pathlib.Path(self._klippy_info["klipper_path"])
        kexec = pathlib.Path(self._klippy_info["python_path"])
        db: Database = self.server.lookup_component("database")
        if kpath != self.path:
            self._path = kpath
            db.insert_item("moonraker", SRC_PATH_KEY, str(kpath))
            logging.info(f"Updated Stored Klipper Source Path: {kpath}")
        if kexec != self.executable:
            self._executable = kexec
            db.insert_item("moonraker", PY_EXEC_KEY, str(kexec))
            logging.info(f"Updated Stored Klipper Python Path: {kexec}")

    async def _init_klippy_connection(self) -> bool:
        self._klippy_identified = False
        self._klippy_started = False
        self._klippy_initializing = True
        self._methods_registered = False
        self._missing_reqs.clear()
        self.init_attempts = 0
        self._state = KlippyState.STARTUP
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
        for ep in endpoints:
            if ep not in RESERVED_ENDPOINTS:
                self.server.register_endpoint(
                    ep, RequestType.GET | RequestType.POST, self.request,
                    is_remote=True
                )

    async def _request_initial_subscriptions(self) -> None:
        try:
            await self.klippy_apis.subscribe_objects({'webhooks': None})
        except ServerError:
            logging.exception("Unable to subscribe to webhooks object")
        else:
            logging.info("Webhooks Subscribed")
        try:
            await self.klippy_apis.subscribe_gcode_output()
        except ServerError:
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
        klipper_pid: Optional[int] = result.get("process_id")
        if klipper_pid is not None:
            cur_pid: Optional[int] = self._peer_cred.get("process_id")
            if cur_pid is None or klipper_pid != cur_pid:
                self._peer_cred = dict(
                    process_id=klipper_pid,
                    group_id=result.get("group_id", -1),
                    user_id=result.get("user_id", -1)
                )
                await self._get_service_info(klipper_pid)
        self._klippy_info = dict(result)
        state_message: str = self._state.message
        if "state_message" in self._klippy_info:
            state_message = self._klippy_info["state_message"]
            self._state.set_message(state_message)
        if "state" not in result:
            return
        if send_id:
            self._klippy_identified = True
            self._save_path_info()
            await self.server.send_event("server:klippy_identified")
            # Request initial endpoints to register info, emergency stop APIs
            await self._request_endpoints()
        self._state = KlippyState.from_string(result["state"], state_message)
        if self._state != KlippyState.STARTUP:
            await self._request_initial_subscriptions()
            # Register remaining endpoints available
            await self._request_endpoints()
            startup_state = self._state
            await self.server.send_event("server:klippy_started", startup_state)
            self._klippy_started = True
            if self._state != KlippyState.READY:
                logging.info("\n" + self._state.message)
                if (
                    self._state == KlippyState.SHUTDOWN and
                    startup_state != KlippyState.SHUTDOWN
                ):
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
                self._methods_registered = True
                if self._state == KlippyState.READY:
                    logging.info("Klippy ready")
                    await self.server.send_event("server:klippy_ready")
                    if self._state == KlippyState.SHUTDOWN:
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
            logging.info("Unable to retrieve Klipper Object List")
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
                logging.info("Unable to set SD Card path")
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
            err: Union[str, Dict[str, str]]
            err = cmd.get('error', "Malformed Klippy Response")
            if isinstance(err, dict):
                err = err.get("message", "Malformed Klippy Response")
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
            state_message: str = self._state.message
            if "state_message" in wh:
                state_message = wh["state_message"]
                self._state.set_message(state_message)
            # XXX - process other states (startup, ready, error, etc)?
            if "state" in wh:
                new_state = KlippyState.from_string(wh["state"], state_message)
                if (
                    new_state == KlippyState.SHUTDOWN and
                    not self._klippy_initializing and
                    self._state != KlippyState.SHUTDOWN
                ):
                    # If the shutdown state is received during initialization
                    # defer the event, the init routine will handle it.
                    logging.info("Klippy has shutdown")
                    self.server.send_event("server:klippy_shutdown")
                self._state = new_state
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
            # if the connection has an existing subscription pop it off
            self.subscriptions.pop(conn, None)
            requested_sub: Subscription = args.get('objects', {})
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
                if obj in CACHE_EXCLUSIONS:
                    # Make a shallow copy so we can pop off fields we want to
                    # exclude from the cache without modifying the return value
                    fields_to_cache = dict(fields)
                    removed: List[str] = []
                    for excluded_field in CACHE_EXCLUSIONS[obj]:
                        if excluded_field in fields_to_cache:
                            removed.append(excluded_field)
                            del fields_to_cache[excluded_field]
                    if removed:
                        logging.debug(
                            "Removed excluded fields from subscription cache: "
                            f"{obj}: {removed}"
                        )
                    self.subscription_cache[obj] = fields_to_cache
                else:
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
            if requested_sub:
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

    def remove_subscription(self, conn: APITransport) -> None:
        self.subscriptions.pop(conn, None)

    def is_connected(self) -> bool:
        return self.writer is not None and not self.closing

    def is_ready(self) -> bool:
        return self._state == KlippyState.READY

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
        self._methods_registered = False
        self._state = KlippyState.DISCONNECTED
        self._state.set_message("Klippy Disconnected")
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

def load_component(config: ConfigHelper) -> KlippyConnection:
    return KlippyConnection(config)
