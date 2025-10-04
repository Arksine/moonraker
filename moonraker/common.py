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
import inspect
import dataclasses
import time
from enum import Enum, Flag, auto
from abc import ABCMeta, abstractmethod
from .utils import Sentinel
from .utils import json_wrapper as jsonw
from .utils.exceptions import ServerError, AgentError

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
    Tuple,
    Generic
)

if TYPE_CHECKING:
    from .server import Server
    from .components.websockets import WebsocketManager
    from .components.authorization import Authorization
    from .components.history import History
    from .components.database import DBProviderWrapper
    from .utils import IPAddress
    from asyncio import Future
    _C = TypeVar("_C", str, bool, float, int)
    _F = TypeVar("_F", bound="ExtendedFlag")
    ConvType = Union[str, bool, float, int]
    ArgVal = Union[None, int, float, bool, str]
    RPCCallback = Callable[..., Coroutine]
    AuthComp = Optional[Authorization]

_T = TypeVar("_T")
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

@dataclasses.dataclass
class UserInfo:
    username: str
    password: str
    created_on: float = dataclasses.field(default_factory=time.time)
    salt: str = ""
    source: str = "moonraker"
    jwt_secret: Optional[str] = None
    jwk_id: Optional[str] = None
    groups: List[str] = dataclasses.field(default_factory=lambda: ["admin"])

    def as_tuple(self) -> Tuple[Any, ...]:
        return dataclasses.astuple(self)

    def as_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

@dataclasses.dataclass(frozen=True)
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
        user: Optional[UserInfo] = None
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
                    if rtype.name is None:
                        continue
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
    def user_info(self) -> Optional[UserInfo]:
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
        self._user_info: Optional[UserInfo] = None

    @property
    def user_info(self) -> Optional[UserInfo]:
        return self._user_info

    @user_info.setter
    def user_info(self, uinfo: UserInfo) -> None:
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
        self.message_buf.append(
            jsonw.dumps(message) if isinstance(message, dict) else message
        )
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
        if user == self._user_info.username:
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
        user: Optional[UserInfo] = None
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

    def get_current_user(self) -> Optional[UserInfo]:
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
                f"value received: {val}")

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
            msg = f"Agent {name} RPC error"
            ret = AgentError(msg, obj.get("error"))
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
                -32602, f"Invalid params:\n{e}", req_id, e, method_name
            )
        except ServerError as e:
            code = e.status_code
            if code == 404:
                code = -32601
            elif code == 401:
                code = -32602
            return self.build_error(code, str(e), req_id, e, method_name)
        except Exception as e:
            return self.build_error(500, str(e), req_id, e, method_name)

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
        exc: Exception | None = None,
        method_name: str = ""
    ) -> Dict[str, Any]:
        if method_name:
            method_name = f"Requested Method: {method_name}, "
        log_msg = f"JSON-RPC Request Error - {method_name}Code: {code}, Message: {msg}"
        err = {'code': code, 'message': msg}
        if isinstance(exc, AgentError):
            err["data"] = exc.error_data
            if self.verbose:
                log_msg += f"\nExtra data: {exc.error_data}"
        logging.info(log_msg, exc_info=(exc is not None and self.verbose))
        return {
            'jsonrpc': "2.0",
            'error': err,
            'id': req_id
        }


# *** Job History Common Classes ***

class FieldTracker(Generic[_T]):
    history: History = None  # type: ignore
    def __init__(
        self,
        value: _T = None,  # type: ignore
        reset_callback: Optional[Callable[[], _T]] = None,
        exclude_paused: bool = False,
    ) -> None:
        self.tracked_value = value
        self.exclude_paused = exclude_paused
        self.reset_callback: Optional[Callable[[], _T]] = reset_callback

    def set_reset_callback(self, cb: Optional[Callable[[], _T]]) -> None:
        self.reset_callback = cb

    def set_exclude_paused(self, exclude: bool) -> None:
        self.exclude_paused = exclude

    def reset(self) -> None:
        raise NotImplementedError()

    def update(self, value: _T) -> None:
        raise NotImplementedError()

    def get_tracked_value(self) -> _T:
        return self.tracked_value

    def has_totals(self) -> bool:
        return False

    @classmethod
    def class_init(cls, history: History) -> None:
        cls.history = history


class BasicTracker(FieldTracker[Any]):
    def __init__(
        self,
        value: Any = None,
        reset_callback: Optional[Callable[[], Any]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)

    def reset(self) -> None:
        if self.reset_callback is not None:
            self.tracked_value = self.reset_callback()

    def update(self, value: Any) -> None:
        if self.history.tracking_enabled(self.exclude_paused):
            self.tracked_value = value

    def has_totals(self) -> bool:
        return isinstance(self.tracked_value, (int, float))


class DeltaTracker(FieldTracker[Union[int, float]]):
    def __init__(
        self,
        value: Union[int, float] = 0,
        reset_callback: Optional[Callable[[], Union[float, int]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)
        self.last_value: Union[float, int, None] = None

    def reset(self) -> None:
        self.tracked_value = 0
        self.last_value = None
        if self.reset_callback is not None:
            self.last_value = self.reset_callback()
            if not isinstance(self.last_value, (float, int)):
                logging.info("DeltaTracker reset to invalid type")
                self.last_value = None

    def update(self, value: Union[int, float]) -> None:
        if not isinstance(value, (int, float)):
            return
        if self.history.tracking_enabled(self.exclude_paused):
            if self.last_value is not None:
                self.tracked_value += value - self.last_value
        self.last_value = value

    def has_totals(self) -> bool:
        return True


class CumulativeTracker(FieldTracker[Union[int, float]]):
    def __init__(
        self,
        value: Union[int, float] = 0,
        reset_callback: Optional[Callable[[], Union[float, int]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)

    def reset(self) -> None:
        if self.reset_callback is not None:
            self.tracked_value = self.reset_callback()
            if not isinstance(self.tracked_value, (float, int)):
                logging.info(f"{self.__class__.__name__} reset to invalid type")
                self.tracked_value = 0
        else:
            self.tracked_value = 0

    def update(self, value: Union[int, float]) -> None:
        if not isinstance(value, (int, float)):
            return
        if self.history.tracking_enabled(self.exclude_paused):
            self.tracked_value += value

    def has_totals(self) -> bool:
        return True

class AveragingTracker(CumulativeTracker):
    def __init__(
        self,
        value: Union[int, float] = 0,
        reset_callback: Optional[Callable[[], Union[float, int]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)
        self.count = 0

    def reset(self) -> None:
        super().reset()
        self.count = 0

    def update(self, value: Union[int, float]) -> None:
        if not isinstance(value, (int, float)):
            return
        if self.history.tracking_enabled(self.exclude_paused):
            lv = self.tracked_value
            self.count += 1
            self.tracked_value = (lv * (self.count - 1) + value) / self.count


class MaximumTracker(CumulativeTracker):
    def __init__(
        self,
        value: Union[int, float] = 0,
        reset_callback: Optional[Callable[[], Union[float, int]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)
        self.initialized = False

    def reset(self) -> None:
        self.initialized = False
        if self.reset_callback is not None:
            self.tracked_value = self.reset_callback()
            if not isinstance(self.tracked_value, (int, float)):
                self.tracked_value = 0
                logging.info("MaximumTracker reset to invalid type")
            else:
                self.initialized = True
        else:
            self.tracked_value = 0

    def update(self, value: Union[float, int]) -> None:
        if not isinstance(value, (int, float)):
            return
        if self.history.tracking_enabled(self.exclude_paused):
            if not self.initialized:
                self.tracked_value = value
                self.initialized = True
            else:
                self.tracked_value = max(self.tracked_value, value)

class MinimumTracker(CumulativeTracker):
    def __init__(
        self,
        value: Union[int, float] = 0,
        reset_callback: Optional[Callable[[], Union[float, int]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(value, reset_callback, exclude_paused)
        self.initialized = False

    def reset(self) -> None:
        self.initialized = False
        if self.reset_callback is not None:
            self.tracked_value = self.reset_callback()
            if not isinstance(self.tracked_value, (int, float)):
                self.tracked_value = 0
                logging.info("MinimumTracker reset to invalid type")
            else:
                self.initialized = True
        else:
            self.tracked_value = 0

    def update(self, value: Union[float, int]) -> None:
        if not isinstance(value, (int, float)):
            return
        if self.history.tracking_enabled(self.exclude_paused):
            if not self.initialized:
                self.tracked_value = value
                self.initialized = True
            else:
                self.tracked_value = min(self.tracked_value, value)

class CollectionTracker(FieldTracker[List[Any]]):
    MAX_SIZE = 100
    def __init__(
        self,
        value: List[Any] = [],
        reset_callback: Optional[Callable[[], List[Any]]] = None,
        exclude_paused: bool = False
    ) -> None:
        super().__init__(list(value), reset_callback, exclude_paused)

    def reset(self) -> None:
        if self.reset_callback is not None:
            self.tracked_value = self.reset_callback()
            if not isinstance(self.tracked_value, list):
                logging.info("CollectionTracker reset to invalid type")
                self.tracked_value = []
        else:
            self.tracked_value.clear()

    def update(self, value: Any) -> None:
        if value in self.tracked_value:
            return
        if self.history.tracking_enabled(self.exclude_paused):
            self.tracked_value.append(value)
            if len(self.tracked_value) > self.MAX_SIZE:
                self.tracked_value.pop(0)

    def has_totals(self) -> bool:
        return False


class TrackingStrategy(ExtendedEnum):
    BASIC = 1
    DELTA = 2
    ACCUMULATE = 3
    AVERAGE = 4
    MAXIMUM = 5
    MINIMUM = 6
    COLLECT = 7

    def get_tracker(self, **kwargs) -> FieldTracker:
        trackers: Dict[TrackingStrategy, Type[FieldTracker]] = {
            TrackingStrategy.BASIC: BasicTracker,
            TrackingStrategy.DELTA: DeltaTracker,
            TrackingStrategy.ACCUMULATE: CumulativeTracker,
            TrackingStrategy.AVERAGE: AveragingTracker,
            TrackingStrategy.MAXIMUM: MaximumTracker,
            TrackingStrategy.MINIMUM: MinimumTracker,
            TrackingStrategy.COLLECT: CollectionTracker
        }
        return trackers[self](**kwargs)


class HistoryFieldData:
    def __init__(
        self,
        field_name: str,
        provider: str,
        desc: str,
        strategy: str,
        units: Optional[str] = None,
        reset_callback: Optional[Callable[[], _T]] = None,
        exclude_paused: bool = False,
        report_total: bool = False,
        report_maximum: bool = False,
        precision: Optional[int] = None
    ) -> None:
        self._name = field_name
        self._provider = provider
        self._desc = desc
        self._strategy = TrackingStrategy.from_string(strategy)
        self._units = units
        self._tracker = self._strategy.get_tracker(
            reset_callback=reset_callback,
            exclude_paused=exclude_paused
        )
        self._report_total = report_total
        self._report_maximum = report_maximum
        self._precision = precision

    @property
    def name(self) -> str:
        return self._name

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def tracker(self) -> FieldTracker:
        return self._tracker

    def __eq__(self, value: object) -> bool:
        if isinstance(value, HistoryFieldData):
            return value._provider == self._provider and value._name == self._name
        raise ValueError("Invalid type for comparison")

    def get_configuration(self) -> Dict[str, Any]:
        return {
            "field": self._name,
            "provider": self._provider,
            "description": self._desc,
            "strategy": self._strategy.name.lower(),
            "units": self._units,
            "init_tracker": self._tracker.reset_callback is not None,
            "exclude_paused": self._tracker.exclude_paused,
            "report_total": self._report_total,
            "report_maximum": self._report_maximum,
            "precision": self._precision
        }

    def as_dict(self) -> Dict[str, Any]:
        val = self._tracker.get_tracked_value()
        if self._precision is not None and isinstance(val, float):
            val = round(val, self._precision)
        return {
            "provider": self._provider,
            "name": self.name,
            "value": val,
            "description": self._desc,
            "units": self._units
        }

    def has_totals(self) -> bool:
        return (
            self._tracker.has_totals() and
            (self._report_total or self._report_maximum)
        )

    def get_totals(
        self, last_totals: List[Dict[str, Any]], reset: bool = False
    ) -> Dict[str, Any]:
        if not self.has_totals():
            return {}
        if reset:
            maximum: Optional[float] = 0 if self._report_maximum else None
            total: Optional[float] = 0 if self._report_total else None
        else:
            cur_val: Union[float, int] = self._tracker.get_tracked_value()
            maximum = cur_val if self._report_maximum else None
            total = cur_val if self._report_total else None
            for obj in last_totals:
                if obj["provider"] == self._provider and obj["field"] == self._name:
                    if maximum is not None:
                        maximum = max(cur_val, obj["maximum"] or 0)
                    if total is not None:
                        total = cur_val + (obj["total"] or 0)
                    break
            if self._precision is not None:
                if maximum is not None:
                    maximum = round(maximum, self._precision)
                if total is not None:
                    total = round(total, self._precision)
        return {
            "provider": self._provider,
            "field": self._name,
            "maximum": maximum,
            "total": total
        }

class SqlTableDefType(type):
    def __new__(
        metacls,
        clsname: str,
        bases: Tuple[type, ...],
        cls_attrs: Dict[str, Any]
    ):
        if clsname != "SqlTableDefinition":
            for item in ("name", "prototype"):
                if not cls_attrs[item]:
                    raise ValueError(
                        f"Class attribute `{item}` must be set for class {clsname}"
                    )
            if cls_attrs["version"] < 1:
                raise ValueError(
                    f"The 'version' attribute of {clsname} must be greater than 0"
                )
            cls_attrs["prototype"] = inspect.cleandoc(cls_attrs["prototype"].strip())
            prototype = cls_attrs["prototype"]
            proto_match = re.match(
                r"([a-zA-Z][0-9a-zA-Z_-]+)\s*\((.+)\)\s*;?$", prototype, re.DOTALL
            )
            if proto_match is None:
                raise ValueError(f"Invalid SQL Table prototype:\n{prototype}")
            table_name = cls_attrs["name"]
            parsed_name = proto_match.group(1)
            if table_name != parsed_name:
                raise ValueError(
                    f"Table name '{table_name}' does not match parsed name from "
                    f"table prototype '{parsed_name}'"
                )
        return super().__new__(metacls, clsname, bases, cls_attrs)

class SqlTableDefinition(metaclass=SqlTableDefType):
    name: str = ""
    version: int = 0
    prototype: str = ""
    def __init__(self) -> None:
        if self.__class__ == SqlTableDefinition:
            raise ServerError("Cannot directly instantiate SqlTableDefinition")

    def migrate(
        self, last_version: int, db_provider: DBProviderWrapper
    ) -> None:
        raise NotImplementedError("Children must implement migrate")
