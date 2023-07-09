# Common classes used throughout Moonraker
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import ipaddress
import logging
import copy
import json
from .utils import ServerError, Sentinel

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Callable,
    Coroutine,
    Type,
    TypeVar,
    Union,
    Dict,
    List,
    Awaitable
)

if TYPE_CHECKING:
    from .server import Server
    from .websockets import WebsocketManager
    from .components.authorization import Authorization
    from asyncio import Future
    _T = TypeVar("_T")
    _C = TypeVar("_C", str, bool, float, int)
    IPUnion = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    ConvType = Union[str, bool, float, int]
    ArgVal = Union[None, int, float, bool, str]
    RPCCallback = Callable[..., Coroutine]
    AuthComp = Optional[Authorization]

class Subscribable:
    def send_status(self,
                    status: Dict[str, Any],
                    eventtime: float
                    ) -> None:
        raise NotImplementedError

class APIDefinition:
    def __init__(self,
                 endpoint: str,
                 http_uri: str,
                 jrpc_methods: List[str],
                 request_methods: Union[str, List[str]],
                 transports: List[str],
                 callback: Optional[Callable[[WebRequest], Coroutine]],
                 need_object_parser: bool):
        self.endpoint = endpoint
        self.uri = http_uri
        self.jrpc_methods = jrpc_methods
        if not isinstance(request_methods, list):
            request_methods = [request_methods]
        self.request_methods = request_methods
        self.supported_transports = transports
        self.callback = callback
        self.need_object_parser = need_object_parser

class APITransport:
    def register_api_handler(self, api_def: APIDefinition) -> None:
        raise NotImplementedError

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        raise NotImplementedError

class BaseRemoteConnection(Subscribable):
    def on_create(self, server: Server) -> None:
        self.server = server
        self.eventloop = server.get_event_loop()
        self.wsm: WebsocketManager = self.server.lookup_component("websockets")
        self.rpc = self.wsm.rpc
        self._uid = id(self)
        self.ip_addr = ""
        self.is_closed: bool = False
        self.queue_busy: bool = False
        self.pending_responses: Dict[int, Future] = {}
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

    def get_client_connection(self) -> Optional[BaseRemoteConnection]:
        if isinstance(self.conn, BaseRemoteConnection):
            return self.conn
        return None

    def get_ip_address(self) -> Optional[IPUnion]:
        return self.ip_addr

    def get_current_user(self) -> Optional[Dict[str, Any]]:
        return self.current_user

    def _get_converted_arg(self,
                           key: str,
                           default: Union[Sentinel, _T],
                           dtype: Type[_C]
                           ) -> Union[_C, _T]:
        if key not in self.args:
            if default is Sentinel.MISSING:
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
            default: Union[Sentinel, _T] = Sentinel.MISSING
            ) -> Union[_T, Any]:
        val = self.args.get(key, default)
        if val is Sentinel.MISSING:
            raise ServerError(f"No data for argument: {key}")
        return val

    def get_str(self,
                key: str,
                default: Union[Sentinel, _T] = Sentinel.MISSING
                ) -> Union[str, _T]:
        return self._get_converted_arg(key, default, str)

    def get_int(self,
                key: str,
                default: Union[Sentinel, _T] = Sentinel.MISSING
                ) -> Union[int, _T]:
        return self._get_converted_arg(key, default, int)

    def get_float(self,
                  key: str,
                  default: Union[Sentinel, _T] = Sentinel.MISSING
                  ) -> Union[float, _T]:
        return self._get_converted_arg(key, default, float)

    def get_boolean(self,
                    key: str,
                    default: Union[Sentinel, _T] = Sentinel.MISSING
                    ) -> Union[bool, _T]:
        return self._get_converted_arg(key, default, bool)

    def _parse_list(
        self,
        key: str,
        sep: str,
        ltype: Type[_C],
        count: Optional[int],
        default: Union[Sentinel, _T]
    ) -> Union[List[_C], _T]:
        if key not in self.args:
            if default is Sentinel.MISSING:
                raise ServerError(f"No data for argument: {key}")
            return default
        value = self.args[key]
        if isinstance(value, str):
            try:
                ret = [ltype(val.strip()) for val in value.split(sep) if val.strip()]
            except Exception as e:
                raise ServerError(
                    f"Invalid list format received for argument '{key}', "
                    "parsing failed."
                ) from e
        elif isinstance(value, list):
            for val in value:
                if not isinstance(val, ltype):
                    raise ServerError(
                        f"Invalid list format for argument '{key}', expected all "
                        f"values to be of type {ltype.__name__}."
                    )
            # List already parsed
            ret = value
        else:
            raise ServerError(
                f"Invalid value received for argument '{key}'.  Expected List type, "
                f"received {type(value).__name__}"
            )
        if count is not None and len(ret) != count:
            raise ServerError(
                f"Invalid list received for argument '{key}', count mismatch. "
                f"Expected {count} items, got {len(ret)}."
            )
        return ret

    def get_list(
        self,
        key: str,
        default: Union[Sentinel, _T] = Sentinel.MISSING,
        sep: str = ",",
        count: Optional[int] = None
    ) -> Union[_T, List[str]]:
        return self._parse_list(key, sep, str, count, default)


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
                       conn: Optional[BaseRemoteConnection] = None
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
                             conn: Optional[BaseRemoteConnection]
                             ) -> Optional[Dict[str, Any]]:
        req_id: Optional[int] = obj.get('id', None)
        rpc_version: str = obj.get('jsonrpc', "")
        if rpc_version != "2.0":
            return self.build_error(-32600, "Invalid Request", req_id)
        method_name = obj.get('method', Sentinel.MISSING)
        if method_name is Sentinel.MISSING:
            self.process_response(obj, conn)
            return None
        if not isinstance(method_name, str):
            return self.build_error(
                -32600, "Invalid Request", req_id, method_name=str(method_name)
            )
        method = self.methods.get(method_name, None)
        if method is None:
            return self.build_error(
                -32601, "Method not found", req_id, method_name=method_name
            )
        params: Dict[str, Any] = {}
        if 'params' in obj:
            params = obj['params']
            if not isinstance(params, dict):
                return self.build_error(
                    -32602, f"Invalid params:", req_id, method_name=method_name
                )
        return await self.execute_method(method_name, method, req_id, conn, params)

    def process_response(
        self, obj: Dict[str, Any], conn: Optional[BaseRemoteConnection]
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

    async def execute_method(
        self,
        method_name: str,
        callback: RPCCallback,
        req_id: Optional[int],
        conn: Optional[BaseRemoteConnection],
        params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if conn is not None:
            params["_socket_"] = conn
        try:
            result = await callback(params)
        except TypeError as e:
            return self.build_error(
                -32602, f"Invalid params:\n{e}", req_id, True, method_name
            )
        except ServerError as e:
            code = e.status_code
            if code == 404:
                code = -32601
            elif code == 401:
                code = -32602
            return self.build_error(code, str(e), req_id, True, method_name)
        except Exception as e:
            return self.build_error(-31000, str(e), req_id, True, method_name)

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

    def build_error(
        self,
        code: int,
        msg: str,
        req_id: Optional[int] = None,
        is_exc: bool = False,
        method_name: str = ""
    ) -> Dict[str, Any]:
        if method_name:
            method_name = f"Requested Method: {method_name}, "
        log_msg = f"JSON-RPC Request Error - {method_name}Code: {code}, Message: {msg}"
        if is_exc and self.verbose:
            logging.exception(log_msg)
        else:
            logging.info(log_msg)
        return {
            'jsonrpc': "2.0",
            'error': {'code': code, 'message': msg},
            'id': req_id
        }
