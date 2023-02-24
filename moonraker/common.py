# Common classes used throughout Moonraker
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import ipaddress
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
)

if TYPE_CHECKING:
    from .websockets import BaseSocketClient
    from .components.authorization import Authorization
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
