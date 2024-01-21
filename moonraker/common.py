# Common classes used throughout Moonraker
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import sys
import logging
import copy
import re
from enum import Enum, Flag, auto
from dataclasses import dataclass
from abc import ABCMeta, abstractmethod
from .utils import ServerError, Sentinel
from .utils import json_wrapper as jsonw

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
    Awaitable,
    ClassVar,
    Tuple
)

if TYPE_CHECKING:
    from .server import Server
    from .components.websockets import WebsocketManager
    from .components.authorization import Authorization
    from .utils import IPAddress
    from asyncio import Future
    _T = TypeVar("_T")
    _C = TypeVar("_C", str, bool, float, int)
    _F = TypeVar("_F", bound="ExtendedFlag")
    ConvType = Union[str, bool, float, int]
    ArgVal = Union[None, int, float, bool, str]
    RPCCallback = Callable[..., Coroutine]
    AuthComp = Optional[Authorization]

ENDPOINT_PREFIXES = ["printer", "server", "machine", "access", "api", "debug"]

class ExtendedFlag(Flag):
    @classmethod
    def from_string(cls: Type[_F], flag_name: str) -> _F:
        str_name = flag_name.upper()
        for name, member in cls.__members__.items():
            if name == str_name:
                return cls(member.value)
        raise ValueError(f"No flag member named {flag_name}")

    @classmethod
    def from_string_list(cls: Type[_F], flag_list: List[str]) -> _F:
        ret = cls(0)
        for flag in flag_list:
            flag = flag.upper()
            ret |= cls.from_string(flag)
        return ret

    @classmethod
    def all(cls: Type[_F]) -> _F:
        return ~cls(0)

    if sys.version_info < (3, 11):
        def __len__(self) -> int:
            return bin(self._value_).count("1")

        def __iter__(self):
            for i in range(self._value_.bit_length()):
                val = 1 << i
                if val & self._value_ == val:
                    yield self.__class__(val)

class RequestType(ExtendedFlag):
    """
    The Request Type is also known as the "Request Method" for
    HTTP/REST APIs.  The use of "Request Method" nomenclature
    is discouraged in Moonraker as it could be confused with
    the JSON-RPC "method" field.
    """
    GET = auto()
    POST = auto()
    DELETE = auto()

class TransportType(ExtendedFlag):
    HTTP = auto()
    WEBSOCKET = auto()
    MQTT = auto()
    INTERNAL = auto()

class ExtendedEnum(Enum):
    @classmethod
    def from_string(cls, enum_name: str):
        str_name = enum_name.upper()
        for name, member in cls.__members__.items():
            if name == str_name:
                return cls(member.value)
        raise ValueError(f"No enum member named {enum_name}")

    def __str__(self) -> str:
        return self._name_.lower()  # type: ignore

class JobEvent(ExtendedEnum):
    STANDBY = 1
    STARTED = 2
    PAUSED = 3
    RESUMED = 4
    COMPLETE = 5
    ERROR = 6
    CANCELLED = 7

    @property
    def finished(self) -> bool:
        return self.value >= 5

    @property
    def aborted(self) -> bool:
        return self.value >= 6

    @property
    def is_printing(self) -> bool:
        return self.value in [2, 4]

class KlippyState(ExtendedEnum):
    DISCONNECTED = 1
    STARTUP = 2
    READY = 3
    ERROR = 4
    SHUTDOWN = 5

    @classmethod
    def from_string(cls, enum_name: str, msg: str = ""):
        str_name = enum_name.upper()
        for name, member in cls.__members__.items():
            if name == str_name:
                instance = cls(member.value)
                if msg:
                    instance.set_message(msg)
                return instance
        raise ValueError(f"No enum member named {enum_name}")


    def set_message(self, msg: str) -> None:
        self._state_message: str = msg

    @property
    def message(self) -> str:
        if hasattr(self, "_state_message"):
            return self._state_message
        return ""

    def startup_complete(self) -> bool:
        return self.value > 2

class RenderableTemplate(metaclass=ABCMeta):
    @abstractmethod
    def __str__(self) -> str:
        ...

    @abstractmethod
    def render(self, context: Dict[str, Any] = {}) -> str:
        ...

    @abstractmethod
    async def render_async(self, context: Dict[str, Any] = {}) -> str:
        ...

@dataclass(frozen=True)
class APIDefinition:
    endpoint: str
    http_path: str
    rpc_methods: List[str]
    request_types: RequestType
    transports: TransportType
    callback: Callable[[WebRequest], Coroutine]
    auth_required: bool
    _cache: ClassVar[Dict[str, APIDefinition]] = {}

    def __str__(self) -> str:
        tprt_str = "|".join([tprt.name for tprt in self.transports if tprt.name])
        val: str = f"(Transports: {tprt_str})"
        if TransportType.HTTP in self.transports:
            req_types = "|".join([rt.name for rt in self.request_types if rt.name])
            val += f" (HTTP Request: {req_types} {self.http_path})"
        if self.rpc_methods:
            methods = " ".join(self.rpc_methods)
            val += f" (RPC Methods: {methods})"
        val += f" (Auth Required: {self.auth_required})"
        return val

    def request(
        self,
        args: Dict[str, Any],
        request_type: RequestType,
        transport: Optional[APITransport] = None,
        ip_addr: Optional[IPAddress] = None,
        user: Optional[Dict[str, Any]] = None
    ) -> Coroutine:
        return self.callback(
            WebRequest(self.endpoint, args, request_type, transport, ip_addr, user)
        )

    @property
    def need_object_parser(self) -> bool:
        return self.endpoint.startswith("objects/")

    def rpc_items(self) -> zip[Tuple[RequestType, str]]:
        return zip(self.request_types, self.rpc_methods)

    @classmethod
    def create(
        cls,
        endpoint: str,
        request_types: Union[List[str], RequestType],
        callback: Callable[[WebRequest], Coroutine],
        transports: Union[List[str], TransportType] = TransportType.all(),
        auth_required: bool = True,
        is_remote: bool = False
    ) -> APIDefinition:
        if isinstance(request_types, list):
            request_types = RequestType.from_string_list(request_types)
        if isinstance(transports, list):
            transports = TransportType.from_string_list(transports)
        if endpoint in cls._cache:
            return cls._cache[endpoint]
        http_path = f"/printer/{endpoint.strip('/')}" if is_remote else endpoint
        prf_match = re.match(r"/([^/]+)", http_path)
        if TransportType.HTTP in transports:
            # Validate the first path segment for definitions that support the
            # HTTP transport.  We want to restrict components from registering
            # using unknown paths.
            if prf_match is None or prf_match.group(1) not in ENDPOINT_PREFIXES:
                prefixes = [f"/{prefix} " for prefix in ENDPOINT_PREFIXES]
                raise ServerError(
                    f"Invalid endpoint name '{endpoint}', must start with one of "
                    f"the following: {prefixes}"
                )
        rpc_methods: List[str] = []
        if is_remote:
            # Request Types have no meaning for remote requests.  Therefore
            # both GET and POST http requests are accepted.  JRPC requests do
            # not need an associated RequestType, so the unknown value is used.
            request_types = RequestType.GET | RequestType.POST
            rpc_methods.append(http_path[1:].replace('/', '.'))
        elif transports != TransportType.HTTP:
            name_parts = http_path[1:].split('/')
            if len(request_types) > 1:
                for rtype in request_types:
                    func_name = rtype.name.lower() + "_" + name_parts[-1]
                    rpc_methods.append(".".join(name_parts[:-1] + [func_name]))
            else:
                rpc_methods.append(".".join(name_parts))
            if len(request_types) != len(rpc_methods):
                raise ServerError(
                    "Invalid API definition.  Number of websocket methods must "
                    "match the number of request methods"
                )

        api_def = cls(
            endpoint, http_path, rpc_methods, request_types,
            transports, callback, auth_required
        )
        cls._cache[endpoint] = api_def
        return api_def

    @classmethod
    def pop_cached_def(cls, endpoint: str) -> Optional[APIDefinition]:
        return cls._cache.pop(endpoint, None)

    @classmethod
    def get_cache(cls) -> Dict[str, APIDefinition]:
        return cls._cache

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()

class APITransport:
    @property
    def transport_type(self) -> TransportType:
        return TransportType.INTERNAL

    @property
    def user_info(self) -> Optional[Dict[str, Any]]:
        return None

    @property
    def ip_addr(self) -> Optional[IPAddress]:
        return None

    def screen_rpc_request(
        self, api_def: APIDefinition, req_type: RequestType, args: Dict[str, Any]
    ) -> None:
        return None

    def send_status(
        self, status: Dict[str, Any], eventtime: float
    ) -> None:
        raise NotImplementedError

class BaseRemoteConnection(APITransport):
    def on_create(self, server: Server) -> None:
        self.server = server
        self.eventloop = server.get_event_loop()
        self.wsm: WebsocketManager = self.server.lookup_component("websockets")
        self.rpc: JsonRPC = self.server.lookup_component("jsonrpc")
        self._uid = id(self)
        self.is_closed: bool = False
        self.queue_busy: bool = False
        self.pending_responses: Dict[int, Future] = {}
        self.message_buf: List[Union[bytes, str]] = []
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

    @property
    def transport_type(self) -> TransportType:
        return TransportType.WEBSOCKET

    def screen_rpc_request(
        self, api_def: APIDefinition, req_type: RequestType, args: Dict[str, Any]
    ) -> None:
        self.check_authenticated(api_def)

    async def _process_message(self, message: str) -> None:
        try:
            response = await self.rpc.dispatch(message, self)
            if response is not None:
                self.queue_message(response)
        except Exception:
            logging.exception("Websocket Command Error")

    def queue_message(self, message: Union[bytes, str, Dict[str, Any]]):
        if isinstance(message, dict):
            message = jsonw.dumps(message)
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
        elif self._need_auth:
            raise self.server.error("Unauthorized", 401)

    def check_authenticated(self, api_def: APIDefinition) -> None:
        if not self._need_auth:
            return
        auth: AuthComp = self.server.lookup_component("authorization", None)
        if auth is None:
            return
        if api_def.auth_required:
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

    async def write_to_socket(self, message: Union[bytes, str]) -> None:
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

    def call_method_with_response(
        self,
        method: str,
        params: Optional[Union[List, Dict[str, Any]]] = None,
    ) -> Awaitable:
        fut = self.eventloop.create_future()
        msg: Dict[str, Any] = {
            'jsonrpc': "2.0",
            'method': method,
            'id': id(fut)
        }
        if params:
            msg["params"] = params
        self.pending_responses[id(fut)] = fut
        self.queue_message(msg)
        return fut

    def call_method(
        self,
        method: str,
        params: Optional[Union[List, Dict[str, Any]]] = None
    ) -> None:
        msg: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method
        }
        if params:
            msg["params"] = params
        self.queue_message(msg)

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
    def __init__(
        self,
        endpoint: str,
        args: Dict[str, Any],
        request_type: RequestType = RequestType(0),
        transport: Optional[APITransport] = None,
        ip_addr: Optional[IPAddress] = None,
        user: Optional[Dict[str, Any]] = None
    ) -> None:
        self.endpoint = endpoint
        self.args = args
        self.transport = transport
        self.request_type = request_type
        self.ip_addr: Optional[IPAddress] = ip_addr
        self.current_user = user

    def get_endpoint(self) -> str:
        return self.endpoint

    def get_request_type(self) -> RequestType:
        return self.request_type

    def get_action(self) -> str:
        return self.request_type.name or ""

    def get_args(self) -> Dict[str, Any]:
        return self.args

    def get_subscribable(self) -> Optional[APITransport]:
        return self.transport

    def get_client_connection(self) -> Optional[BaseRemoteConnection]:
        if isinstance(self.transport, BaseRemoteConnection):
            return self.transport
        return None

    def get_ip_address(self) -> Optional[IPAddress]:
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
    def __init__(self, server: Server) -> None:
        self.methods: Dict[str, Tuple[RequestType, APIDefinition]] = {}
        self.sanitize_response = False
        self.verbose = server.is_verbose_enabled()

    def _log_request(self, rpc_obj: Dict[str, Any], trtype: TransportType) -> None:
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
        logging.debug(f"{trtype} Received::{jsonw.dumps(output).decode()}")

    def _log_response(
        self, resp_obj: Optional[Dict[str, Any]], trtype: TransportType
    ) -> None:
        if not self.verbose:
            return
        if resp_obj is None:
            return
        output = resp_obj
        if self.sanitize_response and "result" in resp_obj:
            output = copy.deepcopy(resp_obj)
            output["result"] = "<sanitized>"
        self.sanitize_response = False
        logging.debug(f"{trtype} Response::{jsonw.dumps(output).decode()}")

    def register_method(
        self,
        name: str,
        request_type: RequestType,
        api_definition: APIDefinition
    ) -> None:
        self.methods[name] = (request_type, api_definition)

    def get_method(self, name: str) -> Optional[Tuple[RequestType, APIDefinition]]:
        return self.methods.get(name, None)

    def remove_method(self, name: str) -> None:
        self.methods.pop(name, None)

    async def dispatch(
        self,
        data: Union[str, bytes],
        transport: APITransport
    ) -> Optional[bytes]:
        transport_type = transport.transport_type
        try:
            obj: Union[Dict[str, Any], List[dict]] = jsonw.loads(data)
        except Exception:
            if isinstance(data, bytes):
                data = data.decode()
            msg = f"{transport_type} data not valid json: {data}"
            logging.exception(msg)
            err = self.build_error(-32700, "Parse error")
            return jsonw.dumps(err)
        if isinstance(obj, list):
            responses: List[Dict[str, Any]] = []
            for item in obj:
                self._log_request(item, transport_type)
                resp = await self.process_object(item, transport)
                if resp is not None:
                    self._log_response(resp, transport_type)
                    responses.append(resp)
            if responses:
                return jsonw.dumps(responses)
        else:
            self._log_request(obj, transport_type)
            response = await self.process_object(obj, transport)
            if response is not None:
                self._log_response(response, transport_type)
                return jsonw.dumps(response)
        return None

    async def process_object(
        self,
        obj: Dict[str, Any],
        transport: APITransport
    ) -> Optional[Dict[str, Any]]:
        req_id: Optional[int] = obj.get('id', None)
        rpc_version: str = obj.get('jsonrpc', "")
        if rpc_version != "2.0":
            return self.build_error(-32600, "Invalid Request", req_id)
        method_name = obj.get('method', Sentinel.MISSING)
        if method_name is Sentinel.MISSING:
            self.process_response(obj, transport)
            return None
        if not isinstance(method_name, str):
            return self.build_error(
                -32600, "Invalid Request", req_id, method_name=str(method_name)
            )
        method_info = self.methods.get(method_name, None)
        if method_info is None:
            return self.build_error(
                -32601, "Method not found", req_id, method_name=method_name
            )
        request_type, api_definition = method_info
        transport_type = transport.transport_type
        if transport_type not in api_definition.transports:
            return self.build_error(
                -32601, f"Method not found for transport {transport_type.name}",
                req_id, method_name=method_name
            )
        params: Dict[str, Any] = {}
        if 'params' in obj:
            params = obj['params']
            if not isinstance(params, dict):
                return self.build_error(
                    -32602, "Invalid params:", req_id, method_name=method_name
                )
        return await self.execute_method(
            method_name, request_type, api_definition, req_id, transport, params
        )

    def process_response(
        self, obj: Dict[str, Any], conn: APITransport
    ) -> None:
        if not isinstance(conn, BaseRemoteConnection):
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
        request_type: RequestType,
        api_definition: APIDefinition,
        req_id: Optional[int],
        transport: APITransport,
        params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        try:
            transport.screen_rpc_request(api_definition, request_type, params)
            result = await api_definition.request(
                params, request_type, transport, transport.ip_addr, transport.user_info
            )
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
