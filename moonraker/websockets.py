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

    def get_connection(self) -> Optional[Subscribable]:
        return self.conn

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
    def __init__(self, transport: str = "Websocket") -> None:
        self.methods: Dict[str, RPCCallback] = {}
        self.transport = transport

    def register_method(self,
                        name: str,
                        method: RPCCallback
                        ) -> None:
        self.methods[name] = method

    def remove_method(self, name: str) -> None:
        self.methods.pop(name, None)

    async def dispatch(self,
                       data: str,
                       conn: Optional[WebSocket] = None
                       ) -> Optional[str]:
        response: Any = None
        try:
            obj: Union[Dict[str, Any], List[dict]] = json.loads(data)
        except Exception:
            msg = f"{self.transport} data not json: {data}"
            logging.exception(msg)
            response = self.build_error(-32700, "Parse error")
            return json.dumps(response)
        logging.debug(f"{self.transport} Received::{data}")
        if isinstance(obj, list):
            response = []
            for item in obj:
                resp = await self.process_object(item, conn)
                if resp is not None:
                    response.append(resp)
            if not response:
                response = None
        else:
            response = await self.process_object(obj, conn)
        if response is not None:
            response = json.dumps(response)
            logging.debug(f"{self.transport} Response::{response}")
        return response

    async def process_object(self,
                             obj: Dict[str, Any],
                             conn: Optional[WebSocket]
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
        if 'params' in obj:
            params = obj['params']
            if isinstance(params, list):
                response = await self.execute_method(
                    method, req_id, conn, *params)
            elif isinstance(params, dict):
                response = await self.execute_method(
                    method, req_id, conn, **params)
            else:
                return self.build_error(-32600, "Invalid Request", req_id)
        else:
            response = await self.execute_method(method, req_id, conn)
        return response

    def process_response(
        self, obj: Dict[str, Any], conn: Optional[WebSocket]
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
            error = obj.get("error")
            msg = f"Invalid RPC Response: {obj}"
            code = 500
            if isinstance(error, dict):
                msg = error.get("message", msg)
                code = error.get("code", code)
            ret = ServerError(msg, code)
        else:
            ret = result
        conn.resolve_pending_response(response_id, ret)

    async def execute_method(self,
                             method: RPCCallback,
                             req_id: Optional[int],
                             conn: Optional[WebSocket],
                             *args,
                             **kwargs
                             ) -> Optional[Dict[str, Any]]:
        try:
            if conn is not None:
                result = await method(conn, *args, **kwargs)
            else:
                result = await method(*args, **kwargs)
        except TypeError as e:
            return self.build_error(
                -32603, f"Invalid params:\n{e}", req_id, True)
        except ServerError as e:
            return self.build_error(e.status_code, str(e), req_id, True)
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
        self.klippy: Klippy = server.lookup_component("klippy_connection")
        self.websockets: Dict[int, WebSocket] = {}
        self.rpc = JsonRPC()
        self.closed_event: Optional[asyncio.Event] = None

        self.rpc.register_method("server.websocket.id", self._handle_id_request)
        self.rpc.register_method(
            "server.connection.identify", self._handle_identify)

    def register_notification(self,
                              event_name: str,
                              notify_name: Optional[str] = None
                              ) -> None:
        if notify_name is None:
            notify_name = event_name.split(':')[-1]

        def notify_handler(*args):
            self.notify_websockets(notify_name, *args)
        self.server.register_event_handler(
            event_name, notify_handler)

    def register_api_handler(self, api_def: APIDefinition) -> None:
        if api_def.callback is None:
            # Remote API, uses RPC to reach out to Klippy
            ws_method = api_def.jrpc_methods[0]
            rpc_cb = self._generate_callback(api_def.endpoint)
            self.rpc.register_method(ws_method, rpc_cb)
        else:
            # Local API, uses local callback
            for ws_method, req_method in \
                    zip(api_def.jrpc_methods, api_def.request_methods):
                rpc_cb = self._generate_local_callback(
                    api_def.endpoint, req_method, api_def.callback)
                self.rpc.register_method(ws_method, rpc_cb)
        logging.info(
            "Registering Websocket JSON-RPC methods: "
            f"{', '.join(api_def.jrpc_methods)}")

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        for jrpc_method in api_def.jrpc_methods:
            self.rpc.remove_method(jrpc_method)

    def _generate_callback(self, endpoint: str) -> RPCCallback:
        async def func(ws: WebSocket, **kwargs) -> Any:
            result = await self.klippy.request(
                WebRequest(endpoint, kwargs, conn=ws, ip_addr=ws.ip_addr,
                           user=ws.current_user))
            return result
        return func

    def _generate_local_callback(self,
                                 endpoint: str,
                                 request_method: str,
                                 callback: Callable[[WebRequest], Coroutine]
                                 ) -> RPCCallback:
        async def func(ws: WebSocket, **kwargs) -> Any:
            result = await callback(
                WebRequest(endpoint, kwargs, request_method, ws,
                           ip_addr=ws.ip_addr, user=ws.current_user))
            return result
        return func

    async def _handle_id_request(self,
                                 ws: WebSocket,
                                 **kwargs
                                 ) -> Dict[str, int]:
        return {'websocket_id': ws.uid}

    async def _handle_identify(self,
                               ws: WebSocket,
                               **kwargs
                               ) -> Dict[str, int]:
        try:
            name = str(kwargs["client_name"])
            version = str(kwargs["version"])
            client_type: str = str(kwargs["type"]).lower()
            url = str(kwargs["url"])
        except KeyError as e:
            missing_key = str(e).split(":")[-1].strip()
            raise self.server.error(
                f"No data for argument: {missing_key}"
            ) from None
        if client_type not in CLIENT_TYPES:
            raise self.server.error(f"Invalid Client Type: {client_type}")
        ws.client_data = {
            "name": name,
            "version": version,
            "type": client_type,
            "url": url
        }
        logging.info(
            f"Websocket {ws.uid} Client Identified - "
            f"Name: {name}, Version: {version}, Type: {client_type}"
        )
        self.server.send_event("websockets:websocket_identified", ws)
        return {'connection_id': ws.uid}

    def has_websocket(self, ws_id: int) -> bool:
        return ws_id in self.websockets

    def get_websocket(self, ws_id: int) -> Optional[WebSocket]:
        return self.websockets.get(ws_id, None)

    def get_websockets_by_type(self, client_type: str) -> List[WebSocket]:
        if not client_type:
            return []
        ret: List[WebSocket] = []
        for ws in self.websockets.values():
            if ws.client_data.get("type", "") == client_type.lower():
                ret.append(ws)
        return ret

    def get_websockets_by_name(self, name: str) -> List[WebSocket]:
        if not name:
            return []
        ret: List[WebSocket] = []
        for ws in self.websockets.values():
            if ws.client_data.get("name", "").lower() == name.lower():
                ret.append(ws)
        return ret

    def get_unidentified_websockets(self) -> List[WebSocket]:
        ret: List[WebSocket] = []
        for ws in self.websockets.values():
            if not ws.client_data:
                ret.append(ws)
        return ret

    def add_websocket(self, ws: WebSocket) -> None:
        self.websockets[ws.uid] = ws
        self.server.send_event("websockets:websocked_added", ws)
        logging.debug(f"New Websocket Added: {ws.uid}")

    def remove_websocket(self, ws: WebSocket) -> None:
        old_ws = self.websockets.pop(ws.uid, None)
        if old_ws is not None:
            self.klippy.remove_subscription(old_ws)
            self.server.send_event("websockets:websocket_removed", ws)
            logging.debug(f"Websocket Removed: {ws.uid}")
        if self.closed_event is not None and not self.websockets:
            self.closed_event.set()

    def notify_websockets(self,
                          name: str,
                          data: Any = SENTINEL
                          ) -> None:
        msg: Dict[str, Any] = {'jsonrpc': "2.0", 'method': "notify_" + name}
        if data != SENTINEL:
            msg['params'] = [data]
        for ws in list(self.websockets.values()):
            ws.queue_message(msg)

    def get_count(self) -> int:
        return len(self.websockets)

    async def close(self) -> None:
        if not self.websockets:
            return
        self.closed_event = asyncio.Event()
        for ws in list(self.websockets.values()):
            ws.close(1001, "Server Shutdown")
        try:
            await asyncio.wait_for(self.closed_event.wait(), 2.)
        except asyncio.TimeoutError:
            pass
        self.closed_event = None

class WebSocket(WebSocketHandler, Subscribable):
    def initialize(self) -> None:
        self.server: Server = self.settings['server']
        self.event_loop = self.server.get_event_loop()
        self.wsm: WebsocketManager = self.server.lookup_component("websockets")
        self.rpc = self.wsm.rpc
        self._uid = id(self)
        self.is_closed: bool = False
        self.ip_addr: str = self.request.remote_ip
        self.queue_busy: bool = False
        self.pending_responses: Dict[int, asyncio.Future] = {}
        self.message_buf: List[Union[str, Dict[str, Any]]] = []
        self.last_pong_time: float = self.event_loop.get_loop_time()
        self._connected_time: float = 0.
        self._client_data: Dict[str, str] = {
            "name": "unknown",
            "version": "",
            "type": "",
            "url": ""
        }

    @property
    def uid(self) -> int:
        return self._uid

    @property
    def hostname(self) -> str:
        return self.request.host_name

    @property
    def start_time(self) -> float:
        return self._connected_time

    @property
    def client_data(self) -> Dict[str, str]:
        return self._client_data

    @client_data.setter
    def client_data(self, data: Dict[str, str]) -> None:
        self._client_data = data

    def open(self, *args, **kwargs) -> None:
        self.set_nodelay(True)
        self._connected_time = self.event_loop.get_loop_time()
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
        self.wsm.add_websocket(self)

    def on_message(self, message: Union[bytes, str]) -> None:
        self.event_loop.register_callback(self._process_message, message)

    def on_pong(self, data: bytes) -> None:
        self.last_pong_time = self.event_loop.get_loop_time()

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
        self.event_loop.register_callback(self._process_messages)

    async def _process_messages(self):
        if self.is_closed:
            self.message_buf = []
            self.queue_busy = False
            return
        while self.message_buf:
            msg = self.message_buf.pop(0)
            try:
                await self.write_message(msg)
            except WebSocketClosedError:
                self.is_closed = True
                logging.info(
                    f"Websocket closed while writing: {self.uid}")
                break
            except Exception:
                logging.exception(
                    f"Error sending data over websocket: {self.uid}")
        self.queue_busy = False

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
        fut = self.event_loop.create_future()
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

    def on_close(self) -> None:
        self.is_closed = True
        self.message_buf = []
        now = self.event_loop.get_loop_time()
        pong_elapsed = now - self.last_pong_time
        for resp in self.pending_responses.values():
            resp.set_exception(ServerError("Client Socket Disconnected", 500))
        self.pending_responses = {}
        logging.info(f"Websocket Closed: ID: {self.uid} "
                     f"Close Code: {self.close_code}, "
                     f"Close Reason: {self.close_reason}, "
                     f"Pong Time Elapsed: {pong_elapsed:.2f}")
        self.wsm.remove_websocket(self)

    def check_origin(self, origin: str) -> bool:
        if not super(WebSocket, self).check_origin(origin):
            auth: AuthComp = self.server.lookup_component('authorization', None)
            if auth is not None:
                return auth.check_cors(origin)
            return False
        return True

    # Check Authorized User
    def prepare(self):
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None:
            self.current_user = auth.check_authorized(self.request)
