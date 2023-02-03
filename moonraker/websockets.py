# Websocket Request/Response Handler
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import logging
import ipaddress
import json
import asyncio
import copy
from tornado.websocket import WebSocketHandler, WebSocketClosedError
from utils import ServerError, SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Optional,
    Callable,
    Coroutine,
    Tuple,
    Type,
    TypeVar,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from moonraker import Server
    from app import APIDefinition
    from klippy_connection import KlippyConnection as Klippy
    from .components.extensions import ExtensionManager
    import components.authorization
    _T = TypeVar("_T")
    _C = TypeVar("_C", str, bool, float, int)
    IPUnion = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    ConvType = Union[str, bool, float, int]
    ArgVal = Union[None, int, float, bool, str]
    RPCCallback = Callable[..., Coroutine]
    AuthComp = Optional[components.authorization.Authorization]

CLIENT_TYPES = ["web", "mobile", "desktop", "display", "bot", "agent", "other"]
SENTINEL = SentinelClass.get_instance()

class Subscribable:
    def send_status(self,
                    status: Dict[str, Any],
                    eventtime: float
                    ) -> None:
        raise NotImplementedError

class WebRequest:
    def __init__(self,
                 endpoint: str,
                 args: Dict[str, Any],
                 action: Optional[str] = "",
                 conn: Optional[Subscribable] = None,
                 ip_addr: str = "",
                 user: Optional[Dict[str, Any]] = None
                 ) -> None:
        self.endpoint = endpoint
        self.action = action or ""
        self.args = args
        self.conn = conn
        self.ip_addr: Optional[IPUnion] = None
        try:
            self.ip_addr = ipaddress.ip_address(ip_addr)
        except Exception:
            self.ip_addr = None
        self.current_user = user

    def get_endpoint(self) -> str:
        return self.endpoint

    def get_action(self) -> str:
        return self.action

    def get_args(self) -> Dict[str, Any]:
        return self.args

    def get_subscribable(self) -> Optional[Subscribable]:
        return self.conn

    def get_client_connection(self) -> Optional[BaseSocketClient]:
        if isinstance(self.conn, BaseSocketClient):
            return self.conn
        return None

    def get_ip_address(self) -> Optional[IPUnion]:
        return self.ip_addr

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        return self.current_user

    def _get_converted_arg(self,
                           key: str,
                           default: Union[SentinelClass, _T],
                           dtype: Type[_C]
                           ) -> Union[_C, _T]:
        if key not in self.args:
            if isinstance(default, SentinelClass):
                raise ServerError(f"No data for argument: {key}")
            return default
        val = self.args[key]
        try:
            if dtype is not bool:
                return dtype(val)
            else:
                if isinstance(val, str):
                    val = val.lower()
                    if val in ["true", "false"]:
                        return True if val == "true" else False  # type: ignore
                elif isinstance(val, bool):
                    return val  # type: ignore
                raise TypeError
        except Exception:
            raise ServerError(
                f"Unable to convert argument [{key}] to {dtype}: "
                f"value recieved: {val}")

    def get(self,
            key: str,
            default: Union[SentinelClass, _T] = SENTINEL
            ) -> Union[_T, Any]:
        val = self.args.get(key, default)
        if isinstance(val, SentinelClass):
            raise ServerError(f"No data for argument: {key}")
        return val

    def get_str(self,
                key: str,
                default: Union[SentinelClass, _T] = SENTINEL
                ) -> Union[str, _T]:
        return self._get_converted_arg(key, default, str)

    def get_int(self,
                key: str,
                default: Union[SentinelClass, _T] = SENTINEL
                ) -> Union[int, _T]:
        return self._get_converted_arg(key, default, int)

    def get_float(self,
                  key: str,
                  default: Union[SentinelClass, _T] = SENTINEL
                  ) -> Union[float, _T]:
        return self._get_converted_arg(key, default, float)

    def get_boolean(self,
                    key: str,
                    default: Union[SentinelClass, _T] = SENTINEL
                    ) -> Union[bool, _T]:
        return self._get_converted_arg(key, default, bool)

class JsonRPC:
    def __init__(
        self, server: Server, transport: str = "Websocket"
    ) -> None:
        self.methods: Dict[str, RPCCallback] = {}
        self.transport = transport
        self.sanitize_response = False
        self.verbose = server.is_verbose_enabled()

    def _log_request(self, rpc_obj: Dict[str, Any], ) -> None:
        if not self.verbose:
            return
        self.sanitize_response = False
        output = rpc_obj
        method: Optional[str] = rpc_obj.get("method")
        params: Dict[str, Any] = rpc_obj.get("params", {})
        if isinstance(method, str):
            if (
                method.startswith("access.") or
                method == "machine.sudo.password"
            ):
                self.sanitize_response = True
                if params and isinstance(params, dict):
                    output = copy.deepcopy(rpc_obj)
                    output["params"] = {key: "<sanitized>" for key in params}
            elif method == "server.connection.identify":
                output = copy.deepcopy(rpc_obj)
                for field in ["access_token", "api_key"]:
                    if field in params:
                        output["params"][field] = "<sanitized>"
        logging.debug(f"{self.transport} Received::{json.dumps(output)}")

    def _log_response(self, resp_obj: Optional[Dict[str, Any]]) -> None:
        if not self.verbose:
            return
        if resp_obj is None:
            return
        output = resp_obj
        if self.sanitize_response and "result" in resp_obj:
            output = copy.deepcopy(resp_obj)
            output["result"] = "<sanitized>"
        self.sanitize_response = False
        logging.debug(f"{self.transport} Response::{json.dumps(output)}")

    def register_method(self,
                        name: str,
                        method: RPCCallback
                        ) -> None:
        self.methods[name] = method

    def remove_method(self, name: str) -> None:
        self.methods.pop(name, None)

    async def dispatch(self,
                       data: str,
                       conn: Optional[BaseSocketClient] = None
                       ) -> Optional[str]:
        try:
            obj: Union[Dict[str, Any], List[dict]] = json.loads(data)
        except Exception:
            msg = f"{self.transport} data not json: {data}"
            logging.exception(msg)
            err = self.build_error(-32700, "Parse error")
            return json.dumps(err)
        if isinstance(obj, list):
            responses: List[Dict[str, Any]] = []
            for item in obj:
                self._log_request(item)
                resp = await self.process_object(item, conn)
                if resp is not None:
                    self._log_response(resp)
                    responses.append(resp)
            if responses:
                return json.dumps(responses)
        else:
            self._log_request(obj)
            response = await self.process_object(obj, conn)
            if response is not None:
                self._log_response(response)
                return json.dumps(response)
        return None

    async def process_object(self,
                             obj: Dict[str, Any],
                             conn: Optional[BaseSocketClient]
                             ) -> Optional[Dict[str, Any]]:
        req_id: Optional[int] = obj.get('id', None)
        rpc_version: str = obj.get('jsonrpc', "")
        if rpc_version != "2.0":
            return self.build_error(-32600, "Invalid Request", req_id)
        method_name = obj.get('method', SENTINEL)
        if method_name is SENTINEL:
            self.process_response(obj, conn)
            return None
        if not isinstance(method_name, str):
            return self.build_error(-32600, "Invalid Request", req_id)
        method = self.methods.get(method_name, None)
        if method is None:
            return self.build_error(-32601, "Method not found", req_id)
        params: Dict[str, Any] = {}
        if 'params' in obj:
            params = obj['params']
            if not isinstance(params, dict):
                return self.build_error(
                    -32602, f"Invalid params:", req_id, True)
        response = await self.execute_method(method, req_id, conn, params)
        return response

    def process_response(
        self, obj: Dict[str, Any], conn: Optional[BaseSocketClient]
    ) -> None:
        if conn is None:
            logging.debug(f"RPC Response to non-socket request: {obj}")
            return
        response_id = obj.get("id")
        if response_id is None:
            logging.debug(f"RPC Response with null ID: {obj}")
            return
        result = obj.get("result")
        if result is None:
            name = conn.client_data["name"]
            error = obj.get("error")
            msg = f"Invalid Response: {obj}"
            code = -32600
            if isinstance(error, dict):
                msg = error.get("message", msg)
                code = error.get("code", code)
            msg = f"{name} rpc error: {code} {msg}"
            ret = ServerError(msg, 418)
        else:
            ret = result
        conn.resolve_pending_response(response_id, ret)

    async def execute_method(self,
                             callback: RPCCallback,
                             req_id: Optional[int],
                             conn: Optional[BaseSocketClient],
                             params: Dict[str, Any]
                             ) -> Optional[Dict[str, Any]]:
        if conn is not None:
            params["_socket_"] = conn
        try:
            result = await callback(params)
        except TypeError as e:
            return self.build_error(
                -32602, f"Invalid params:\n{e}", req_id, True)
        except ServerError as e:
            code = e.status_code
            if code == 404:
                code = -32601
            elif code == 401:
                code = -32602
            return self.build_error(code, str(e), req_id, True)
        except Exception as e:
            return self.build_error(-31000, str(e), req_id, True)

        if req_id is None:
            return None
        else:
            return self.build_result(result, req_id)

    def build_result(self, result: Any, req_id: int) -> Dict[str, Any]:
        return {
            'jsonrpc': "2.0",
            'result': result,
            'id': req_id
        }

    def build_error(self,
                    code: int,
                    msg: str,
                    req_id: Optional[int] = None,
                    is_exc: bool = False
                    ) -> Dict[str, Any]:
        log_msg = f"JSON-RPC Request Error: {code}\n{msg}"
        if is_exc:
            logging.exception(log_msg)
        else:
            logging.info(log_msg)
        return {
            'jsonrpc': "2.0",
            'error': {'code': code, 'message': msg},
            'id': req_id
        }

class APITransport:
    def register_api_handler(self, api_def: APIDefinition) -> None:
        raise NotImplementedError

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        raise NotImplementedError

class WebsocketManager(APITransport):
    def __init__(self, server: Server) -> None:
        self.server = server
        self.clients: Dict[int, BaseSocketClient] = {}
        self.rpc = JsonRPC(server)
        self.closed_event: Optional[asyncio.Event] = None

        self.rpc.register_method("server.websocket.id", self._handle_id_request)
        self.rpc.register_method(
            "server.connection.identify", self._handle_identify)

    def register_notification(
        self,
        event_name: str,
        notify_name: Optional[str] = None,
        event_type: Optional[str] = None
    ) -> None:
        if notify_name is None:
            notify_name = event_name.split(':')[-1]
        if event_type == "logout":
            def notify_handler(*args):
                self.notify_clients(notify_name, args)
                self._process_logout(*args)
        else:
            def notify_handler(*args):
                self.notify_clients(notify_name, args)
        self.server.register_event_handler(event_name, notify_handler)

    def register_api_handler(self, api_def: APIDefinition) -> None:
        klippy: Klippy = self.server.lookup_component("klippy_connection")
        if api_def.callback is None:
            # Remote API, uses RPC to reach out to Klippy
            ws_method = api_def.jrpc_methods[0]
            rpc_cb = self._generate_callback(
                api_def.endpoint, "", klippy.request
            )
            self.rpc.register_method(ws_method, rpc_cb)
        else:
            # Local API, uses local callback
            for ws_method, req_method in \
                    zip(api_def.jrpc_methods, api_def.request_methods):
                rpc_cb = self._generate_callback(
                    api_def.endpoint, req_method, api_def.callback
                )
                self.rpc.register_method(ws_method, rpc_cb)
        logging.info(
            "Registering Websocket JSON-RPC methods: "
            f"{', '.join(api_def.jrpc_methods)}"
        )

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        for jrpc_method in api_def.jrpc_methods:
            self.rpc.remove_method(jrpc_method)

    def _generate_callback(
        self,
        endpoint: str,
        request_method: str,
        callback: Callable[[WebRequest], Coroutine]
    ) -> RPCCallback:
        async def func(args: Dict[str, Any]) -> Any:
            sc: BaseSocketClient = args.pop("_socket_")
            sc.check_authenticated(path=endpoint)
            result = await callback(
                WebRequest(endpoint, args, request_method, sc,
                           ip_addr=sc.ip_addr, user=sc.user_info))
            return result
        return func

    async def _handle_id_request(self, args: Dict[str, Any]) -> Dict[str, int]:
        sc: BaseSocketClient = args["_socket_"]
        sc.check_authenticated()
        return {'websocket_id': sc.uid}

    async def _handle_identify(self, args: Dict[str, Any]) -> Dict[str, int]:
        sc: BaseSocketClient = args["_socket_"]
        sc.authenticate(
            token=args.get("access_token", None),
            api_key=args.get("api_key", None)
        )
        if sc.identified:
            raise self.server.error(
                f"Connection already identified: {sc.client_data}"
            )
        try:
            name = str(args["client_name"])
            version = str(args["version"])
            client_type: str = str(args["type"]).lower()
            url = str(args["url"])
        except KeyError as e:
            missing_key = str(e).split(":")[-1].strip()
            raise self.server.error(
                f"No data for argument: {missing_key}"
            ) from None
        if client_type not in CLIENT_TYPES:
            raise self.server.error(f"Invalid Client Type: {client_type}")
        sc.client_data = {
            "name": name,
            "version": version,
            "type": client_type,
            "url": url
        }
        if client_type == "agent":
            extensions: ExtensionManager
            extensions = self.server.lookup_component("extensions")
            try:
                extensions.register_agent(sc)
            except ServerError:
                sc.client_data["type"] = ""
                raise
        logging.info(
            f"Websocket {sc.uid} Client Identified - "
            f"Name: {name}, Version: {version}, Type: {client_type}"
        )
        self.server.send_event("websockets:client_identified", sc)
        return {'connection_id': sc.uid}

    def _process_logout(self, user: Dict[str, Any]) -> None:
        if "username" not in user:
            return
        name = user["username"]
        for sc in self.clients.values():
            sc.on_user_logout(name)

    def has_socket(self, ws_id: int) -> bool:
        return ws_id in self.clients

    def get_client(self, ws_id: int) -> Optional[BaseSocketClient]:
        sc = self.clients.get(ws_id, None)
        if sc is None or not isinstance(sc, WebSocket):
            return None
        return sc

    def get_clients_by_type(
        self, client_type: str
    ) -> List[BaseSocketClient]:
        if not client_type:
            return []
        ret: List[BaseSocketClient] = []
        for sc in self.clients.values():
            if sc.client_data.get("type", "") == client_type.lower():
                ret.append(sc)
        return ret

    def get_clients_by_name(self, name: str) -> List[BaseSocketClient]:
        if not name:
            return []
        ret: List[BaseSocketClient] = []
        for sc in self.clients.values():
            if sc.client_data.get("name", "").lower() == name.lower():
                ret.append(sc)
        return ret

    def get_unidentified_clients(self) -> List[BaseSocketClient]:
        ret: List[BaseSocketClient] = []
        for sc in self.clients.values():
            if not sc.client_data:
                ret.append(sc)
        return ret

    def add_client(self, sc: BaseSocketClient) -> None:
        self.clients[sc.uid] = sc
        self.server.send_event("websockets:client_added", sc)
        logging.debug(f"New Websocket Added: {sc.uid}")

    def remove_client(self, sc: BaseSocketClient) -> None:
        old_sc = self.clients.pop(sc.uid, None)
        if old_sc is not None:
            self.server.send_event("websockets:client_removed", sc)
            logging.debug(f"Websocket Removed: {sc.uid}")
        if self.closed_event is not None and not self.clients:
            self.closed_event.set()

    def notify_clients(
        self,
        name: str,
        data: Union[List, Tuple] = [],
        mask: List[int] = []
    ) -> None:
        msg: Dict[str, Any] = {'jsonrpc': "2.0", 'method': "notify_" + name}
        if data:
            msg['params'] = data
        for sc in list(self.clients.values()):
            if sc.uid in mask or sc.need_auth:
                continue
            sc.queue_message(msg)

    def get_count(self) -> int:
        return len(self.clients)

    async def close(self) -> None:
        if not self.clients:
            return
        self.closed_event = asyncio.Event()
        for sc in list(self.clients.values()):
            sc.close_socket(1001, "Server Shutdown")
        try:
            await asyncio.wait_for(self.closed_event.wait(), 2.)
        except asyncio.TimeoutError:
            pass
        self.closed_event = None

class BaseSocketClient(Subscribable):
    def on_create(self, server: Server) -> None:
        self.server = server
        self.eventloop = server.get_event_loop()
        self.wsm: WebsocketManager = self.server.lookup_component("websockets")
        self.rpc = self.wsm.rpc
        self._uid = id(self)
        self.ip_addr = ""
        self.is_closed: bool = False
        self.queue_busy: bool = False
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self.message_buf: List[Union[str, Dict[str, Any]]] = []
        self._connected_time: float = 0.
        self._identified: bool = False
        self._client_data: Dict[str, str] = {
            "name": "unknown",
            "version": "",
            "type": "",
            "url": ""
        }
        self._need_auth: bool = False
        self._user_info: Optional[Dict[str, Any]] = None

    @property
    def user_info(self) -> Optional[Dict[str, Any]]:
        return self._user_info

    @user_info.setter
    def user_info(self, uinfo: Dict[str, Any]) -> None:
        self._user_info = uinfo
        self._need_auth = False

    @property
    def need_auth(self) -> bool:
        return self._need_auth

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def hostname(self) -> str:
        return ""

    @property
    def start_time(self) -> float:
        return self._connected_time

    @property
    def identified(self) -> bool:
        return self._identified

    @property
    def client_data(self) -> Dict[str, str]:
        return self._client_data

    @client_data.setter
    def client_data(self, data: Dict[str, str]) -> None:
        self._client_data = data
        self._identified = True

    async def _process_message(self, message: str) -> None:
        try:
            response = await self.rpc.dispatch(message, self)
            if response is not None:
                self.queue_message(response)
        except Exception:
            logging.exception("Websocket Command Error")

    def queue_message(self, message: Union[str, Dict[str, Any]]):
        self.message_buf.append(message)
        if self.queue_busy:
            return
        self.queue_busy = True
        self.eventloop.register_callback(self._write_messages)

    def authenticate(
        self,
        token: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> None:
        auth: AuthComp = self.server.lookup_component("authorization", None)
        if auth is None:
            return
        if token is not None:
            self.user_info = auth.validate_jwt(token)
        elif api_key is not None and self.user_info is None:
            self.user_info = auth.validate_api_key(api_key)
        else:
            self.check_authenticated()

    def check_authenticated(self, path: str = "") -> None:
        if not self._need_auth:
            return
        auth: AuthComp = self.server.lookup_component("authorization", None)
        if auth is None:
            return
        if not auth.is_path_permitted(path):
            raise self.server.error("Unauthorized", 401)

    def on_user_logout(self, user: str) -> bool:
        if self._user_info is None:
            return False
        if user == self._user_info.get("username", ""):
            self._user_info = None
            return True
        return False

    async def _write_messages(self):
        if self.is_closed:
            self.message_buf = []
            self.queue_busy = False
            return
        while self.message_buf:
            msg = self.message_buf.pop(0)
            await self.write_to_socket(msg)
        self.queue_busy = False

    async def write_to_socket(
        self, message: Union[str, Dict[str, Any]]
    ) -> None:
        raise NotImplementedError("Children must implement write_to_socket")

    def send_status(self,
                    status: Dict[str, Any],
                    eventtime: float
                    ) -> None:
        if not status:
            return
        self.queue_message({
            'jsonrpc': "2.0",
            'method': "notify_status_update",
            'params': [status, eventtime]})

    def call_method(
        self,
        method: str,
        params: Optional[Union[List, Dict[str, Any]]] = None
    ) -> Awaitable:
        fut = self.eventloop.create_future()
        msg = {
            'jsonrpc': "2.0",
            'method': method,
            'id': id(fut)
        }
        if params is not None:
            msg["params"] = params
        self.pending_responses[id(fut)] = fut
        self.queue_message(msg)
        return fut

    def send_notification(self, name: str, data: List) -> None:
        self.wsm.notify_clients(name, data, [self._uid])

    def resolve_pending_response(
        self, response_id: int, result: Any
    ) -> bool:
        fut = self.pending_responses.pop(response_id, None)
        if fut is None:
            return False
        if isinstance(result, ServerError):
            fut.set_exception(result)
        else:
            fut.set_result(result)
        return True

    def close_socket(self, code: int, reason: str) -> None:
        raise NotImplementedError("Children must implement close_socket()")

class WebSocket(WebSocketHandler, BaseSocketClient):
    connection_count: int = 0

    def initialize(self) -> None:
        self.on_create(self.settings['server'])
        self.ip_addr: str = self.request.remote_ip or ""
        self.last_pong_time: float = self.eventloop.get_loop_time()

    @property
    def hostname(self) -> str:
        return self.request.host_name

    def get_current_user(self) -> Any:
        return self._user_info

    def open(self, *args, **kwargs) -> None:
        self.__class__.connection_count += 1
        self.set_nodelay(True)
        self._connected_time = self.eventloop.get_loop_time()
        agent = self.request.headers.get("User-Agent", "")
        is_proxy = False
        if (
            "X-Forwarded-For" in self.request.headers or
            "X-Real-Ip" in self.request.headers
        ):
            is_proxy = True
        logging.info(f"Websocket Opened: ID: {self.uid}, "
                     f"Proxied: {is_proxy}, "
                     f"User Agent: {agent}, "
                     f"Host Name: {self.hostname}")
        self.wsm.add_client(self)

    def on_message(self, message: Union[bytes, str]) -> None:
        self.eventloop.register_callback(self._process_message, message)

    def on_pong(self, data: bytes) -> None:
        self.last_pong_time = self.eventloop.get_loop_time()

    def on_close(self) -> None:
        self.is_closed = True
        self.__class__.connection_count -= 1
        kconn: Klippy = self.server.lookup_component("klippy_connection")
        kconn.remove_subscription(self)
        self.message_buf = []
        now = self.eventloop.get_loop_time()
        pong_elapsed = now - self.last_pong_time
        for resp in self.pending_responses.values():
            resp.set_exception(ServerError("Client Socket Disconnected", 500))
        self.pending_responses = {}
        logging.info(f"Websocket Closed: ID: {self.uid} "
                     f"Close Code: {self.close_code}, "
                     f"Close Reason: {self.close_reason}, "
                     f"Pong Time Elapsed: {pong_elapsed:.2f}")
        if self._client_data["type"] == "agent":
            extensions: ExtensionManager
            extensions = self.server.lookup_component("extensions")
            extensions.remove_agent(self)
        self.wsm.remove_client(self)

    async def write_to_socket(
        self, message: Union[str, Dict[str, Any]]
    ) -> None:
        try:
            await self.write_message(message)
        except WebSocketClosedError:
            self.is_closed = True
            self.message_buf.clear()
            logging.info(
                f"Websocket closed while writing: {self.uid}")
        except Exception:
            logging.exception(
                f"Error sending data over websocket: {self.uid}")

    def check_origin(self, origin: str) -> bool:
        if not super(WebSocket, self).check_origin(origin):
            auth: AuthComp = self.server.lookup_component('authorization', None)
            if auth is not None:
                return auth.check_cors(origin)
            return False
        return True

    def on_user_logout(self, user: str) -> bool:
        if super().on_user_logout(user):
            self._need_auth = True
            return True
        return False

    # Check Authorized User
    def prepare(self) -> None:
        max_conns = self.settings["max_websocket_connections"]
        if self.__class__.connection_count >= max_conns:
            raise self.server.error(
                "Maximum Number of Websocket Connections Reached"
            )
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None:
            try:
                self._user_info = auth.check_authorized(self.request)
            except Exception as e:
                logging.info(f"Websocket Failed Authentication: {e}")
                self._user_info = None
                self._need_auth = True

    def close_socket(self, code: int, reason: str) -> None:
        self.close(code, reason)
