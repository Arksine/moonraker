# Websocket Request/Response Handler
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import logging
import ipaddress
import asyncio
from tornado.websocket import WebSocketHandler, WebSocketClosedError
from tornado.web import HTTPError
from .common import (
    WebRequest,
    BaseRemoteConnection,
    APITransport,
    APIDefinition,
    JsonRPC
)
from .utils import ServerError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Optional,
    Callable,
    Coroutine,
    Tuple,
    Union,
    Dict,
    List,
)

if TYPE_CHECKING:
    from .server import Server
    from .klippy_connection import KlippyConnection as Klippy
    from .components.extensions import ExtensionManager
    from .components.authorization import Authorization
    IPUnion = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    ConvType = Union[str, bool, float, int]
    ArgVal = Union[None, int, float, bool, str]
    RPCCallback = Callable[..., Coroutine]
    AuthComp = Optional[Authorization]

CLIENT_TYPES = ["web", "mobile", "desktop", "display", "bot", "agent", "other"]

class WebsocketManager(APITransport):
    def __init__(self, server: Server) -> None:
        self.server = server
        self.clients: Dict[int, BaseRemoteConnection] = {}
        self.bridge_connections: Dict[int, BridgeSocket] = {}
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
            sc: BaseRemoteConnection = args.pop("_socket_")
            sc.check_authenticated(path=endpoint)
            result = await callback(
                WebRequest(endpoint, args, request_method, sc,
                           ip_addr=sc.ip_addr, user=sc.user_info))
            return result
        return func

    async def _handle_id_request(self, args: Dict[str, Any]) -> Dict[str, int]:
        sc: BaseRemoteConnection = args["_socket_"]
        sc.check_authenticated()
        return {'websocket_id': sc.uid}

    async def _handle_identify(self, args: Dict[str, Any]) -> Dict[str, int]:
        sc: BaseRemoteConnection = args["_socket_"]
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

    def get_client(self, ws_id: int) -> Optional[BaseRemoteConnection]:
        sc = self.clients.get(ws_id, None)
        if sc is None or not isinstance(sc, WebSocket):
            return None
        return sc

    def get_clients_by_type(
        self, client_type: str
    ) -> List[BaseRemoteConnection]:
        if not client_type:
            return []
        ret: List[BaseRemoteConnection] = []
        for sc in self.clients.values():
            if sc.client_data.get("type", "") == client_type.lower():
                ret.append(sc)
        return ret

    def get_clients_by_name(self, name: str) -> List[BaseRemoteConnection]:
        if not name:
            return []
        ret: List[BaseRemoteConnection] = []
        for sc in self.clients.values():
            if sc.client_data.get("name", "").lower() == name.lower():
                ret.append(sc)
        return ret

    def get_unidentified_clients(self) -> List[BaseRemoteConnection]:
        ret: List[BaseRemoteConnection] = []
        for sc in self.clients.values():
            if not sc.client_data:
                ret.append(sc)
        return ret

    def add_client(self, sc: BaseRemoteConnection) -> None:
        self.clients[sc.uid] = sc
        self.server.send_event("websockets:client_added", sc)
        logging.debug(f"New Websocket Added: {sc.uid}")

    def remove_client(self, sc: BaseRemoteConnection) -> None:
        old_sc = self.clients.pop(sc.uid, None)
        if old_sc is not None:
            self.server.send_event("websockets:client_removed", sc)
            logging.debug(f"Websocket Removed: {sc.uid}")
        self._check_closed_event()

    def add_bridge_connection(self, bc: BridgeSocket) -> None:
        self.bridge_connections[bc.uid] = bc
        logging.debug(f"New Bridge Connection Added: {bc.uid}")

    def remove_bridge_connection(self, bc: BridgeSocket) -> None:
        old_bc = self.bridge_connections.pop(bc.uid, None)
        if old_bc is not None:
            logging.debug(f"Bridge Connection Removed: {bc.uid}")
        self._check_closed_event()

    def _check_closed_event(self) -> None:
        if (
            self.closed_event is not None and
            not self.clients and
            not self.bridge_connections
        ):
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
        for bc in list(self.bridge_connections.values()):
            bc.close_socket(1001, "Server Shutdown")
        for sc in list(self.clients.values()):
            sc.close_socket(1001, "Server Shutdown")
        try:
            await asyncio.wait_for(self.closed_event.wait(), 2.)
        except asyncio.TimeoutError:
            pass
        self.closed_event = None

class WebSocket(WebSocketHandler, BaseRemoteConnection):
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

class BridgeSocket(WebSocketHandler):
    def initialize(self) -> None:
        self.server: Server = self.settings['server']
        self.wsm: WebsocketManager = self.server.lookup_component("websockets")
        self.eventloop = self.server.get_event_loop()
        self.uid = id(self)
        self.ip_addr: str = self.request.remote_ip or ""
        self.last_pong_time: float = self.eventloop.get_loop_time()
        self.is_closed = False
        self.klippy_writer: Optional[asyncio.StreamWriter] = None
        self.klippy_write_buf: List[bytes] = []
        self.klippy_queue_busy: bool = False

    @property
    def hostname(self) -> str:
        return self.request.host_name

    def open(self, *args, **kwargs) -> None:
        WebSocket.connection_count += 1
        self.set_nodelay(True)
        self._connected_time = self.eventloop.get_loop_time()
        agent = self.request.headers.get("User-Agent", "")
        is_proxy = False
        if (
            "X-Forwarded-For" in self.request.headers or
            "X-Real-Ip" in self.request.headers
        ):
            is_proxy = True
        logging.info(f"Bridge Socket Opened: ID: {self.uid}, "
                     f"Proxied: {is_proxy}, "
                     f"User Agent: {agent}, "
                     f"Host Name: {self.hostname}")
        self.wsm.add_bridge_connection(self)

    def on_message(self, message: Union[bytes, str]) -> None:
        if isinstance(message, str):
            message = message.encode(encoding="utf-8")
        self.klippy_write_buf.append(message)
        if self.klippy_queue_busy:
            return
        self.klippy_queue_busy = True
        self.eventloop.register_callback(self._write_klippy_messages)

    async def _write_klippy_messages(self) -> None:
        while self.klippy_write_buf:
            if self.klippy_writer is None or self.is_closed:
                break
            msg = self.klippy_write_buf.pop(0)
            try:
                self.klippy_writer.write(msg + b"\x03")
                await self.klippy_writer.drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                if not self.is_closed:
                    logging.debug("Klippy Disconnection From _write_request()")
                    self.close(1001, "Klippy Disconnected")
                break
        self.klippy_queue_busy = False

    def on_pong(self, data: bytes) -> None:
        self.last_pong_time = self.eventloop.get_loop_time()

    def on_close(self) -> None:
        WebSocket.connection_count -= 1
        self.is_closed = True
        self.klippy_write_buf.clear()
        if self.klippy_writer is not None:
            self.klippy_writer.close()
            self.klippy_writer = None
        now = self.eventloop.get_loop_time()
        pong_elapsed = now - self.last_pong_time
        logging.info(f"Bridge Socket Closed: ID: {self.uid} "
                     f"Close Code: {self.close_code}, "
                     f"Close Reason: {self.close_reason}, "
                     f"Pong Time Elapsed: {pong_elapsed:.2f}")
        self.wsm.remove_bridge_connection(self)

    async def _read_unix_stream(self, reader: asyncio.StreamReader) -> None:
        errors_remaining: int = 10
        while not reader.at_eof():
            try:
                data = memoryview(await reader.readuntil(b'\x03'))
            except (ConnectionError, asyncio.IncompleteReadError):
                break
            except asyncio.CancelledError:
                logging.exception("Klippy Stream Read Cancelled")
                raise
            except Exception:
                logging.exception("Klippy Stream Read Error")
                errors_remaining -= 1
                if not errors_remaining or self.is_closed:
                    break
                continue
            try:
                await self.write_message(data[:-1].tobytes())
            except WebSocketClosedError:
                logging.info(
                    f"Bridge closed while writing: {self.uid}")
                break
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(
                    f"Error sending data over Bridge: {self.uid}")
                errors_remaining -= 1
                if not errors_remaining or self.is_closed:
                    break
                continue
            errors_remaining = 10
        if not self.is_closed:
            logging.debug("Bridge Disconnection From _read_unix_stream()")
            self.close_socket(1001, "Klippy Disconnected")

    def check_origin(self, origin: str) -> bool:
        if not super().check_origin(origin):
            auth: AuthComp = self.server.lookup_component('authorization', None)
            if auth is not None:
                return auth.check_cors(origin)
            return False
        return True

    # Check Authorized User
    async def prepare(self) -> None:
        max_conns = self.settings["max_websocket_connections"]
        if WebSocket.connection_count >= max_conns:
            raise self.server.error(
                "Maximum Number of Bridge Connections Reached"
            )
        auth: AuthComp = self.server.lookup_component("authorization", None)
        if auth is not None:
            self.current_user = auth.check_authorized(self.request)
        kconn: Klippy = self.server.lookup_component("klippy_connection")
        try:
            reader, writer = await kconn.open_klippy_connection()
        except ServerError as err:
            raise HTTPError(err.status_code, str(err)) from None
        except Exception as e:
            raise HTTPError(503, "Failed to open connection to Klippy") from e
        self.klippy_writer = writer
        self.eventloop.register_callback(self._read_unix_stream, reader)

    def close_socket(self, code: int, reason: str) -> None:
        self.close(code, reason)
