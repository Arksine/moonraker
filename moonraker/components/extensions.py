# Moonraker extension management
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import asyncio
import pathlib
import logging
from ..common import BaseRemoteConnection, RequestType, TransportType
from ..utils import get_unix_peer_credentials

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    Union,
)

if TYPE_CHECKING:
    from ..server import Server
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .klippy_connection import KlippyConnection as Klippy

UNIX_BUFFER_LIMIT = 20 * 1024 * 1024

class ExtensionManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.agents: Dict[str, BaseRemoteConnection] = {}
        self.agent_methods: Dict[int, List[str]] = {}
        self.uds_server: Optional[asyncio.AbstractServer] = None
        self.server.register_endpoint(
            "/connection/register_remote_method", RequestType.POST,
            self._register_agent_method,
            transports=TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/connection/send_event", RequestType.POST, self._handle_agent_event,
            transports=TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/server/extensions/list", RequestType.GET, self._handle_list_extensions
        )
        self.server.register_endpoint(
            "/server/extensions/request", RequestType.POST, self._handle_call_agent
        )

    def register_agent(self, connection: BaseRemoteConnection) -> None:
        data = connection.client_data
        name = data["name"]
        client_type = data["type"]
        if client_type != "agent":
            raise self.server.error(
                f"Cannot register client type '{client_type}' as an agent"
            )
        if name in self.agents:
            raise self.server.error(
                f"Agent '{name}' already registered and connected'"
            )
        self.agents[name] = connection
        data = connection.client_data
        evt: Dict[str, Any] = {
            "agent": name, "event": "connected", "data": data
        }
        connection.send_notification("agent_event", [evt])

    def remove_agent(self, connection: BaseRemoteConnection) -> None:
        name = connection.client_data["name"]
        if name in self.agents:
            klippy: Klippy = self.server.lookup_component("klippy_connection")
            registered_methods = self.agent_methods.pop(connection.uid, [])
            for method in registered_methods:
                klippy.unregister_method(method)
            del self.agents[name]
            evt: Dict[str, Any] = {"agent": name, "event": "disconnected"}
            connection.send_notification("agent_event", [evt])

    async def _handle_agent_event(self, web_request: WebRequest) -> str:
        conn = web_request.get_client_connection()
        if conn is None:
            raise self.server.error("No connection detected")
        if conn.client_data["type"] != "agent":
            raise self.server.error(
                "Only connections of the 'agent' type can send events"
            )
        name = conn.client_data["name"]
        evt_name = web_request.get_str("event")
        if evt_name in ["connected", "disconnected"]:
            raise self.server.error(f"Event '{evt_name}' is reserved")
        data: Optional[Union[List, Dict[str, Any]]]
        data = web_request.get("data", None)
        evt: Dict[str, Any] = {"agent": name, "event": evt_name}
        if data is not None:
            evt["data"] = data
        conn.send_notification("agent_event", [evt])
        return "ok"

    async def _register_agent_method(self, web_request: WebRequest) -> str:
        conn = web_request.get_client_connection()
        if conn is None:
            raise self.server.error("No connection detected")
        method_name = web_request.get_str("method_name")
        klippy: Klippy = self.server.lookup_component("klippy_connection")
        klippy.register_method_from_agent(conn, method_name)
        self.agent_methods.setdefault(conn.uid, []).append(method_name)
        return "ok"

    async def _handle_list_extensions(
        self, web_request: WebRequest
    ) -> Dict[str, List[Dict[str, Any]]]:
        agents: List[Dict[str, Any]]
        agents = [agt.client_data for agt in self.agents.values()]
        return {"agents": agents}

    async def _handle_call_agent(self, web_request: WebRequest) -> Any:
        agent = web_request.get_str("agent")
        method: str = web_request.get_str("method")
        args: Optional[Union[List, Dict[str, Any]]]
        args = web_request.get("arguments", None)
        if args is not None and not isinstance(args, (list, dict)):
            raise self.server.error(
                "The 'arguments' field must contain an object or a list"
            )
        if agent not in self.agents:
            raise self.server.error(f"Agent {agent} not connected")
        conn = self.agents[agent]
        return await conn.call_method_with_response(method, args)

    async def start_unix_server(self) -> None:
        sockfile: str = self.server.get_app_args()["unix_socket_path"]
        sock_path = pathlib.Path(sockfile).expanduser().resolve()
        logging.info(f"Creating Unix Domain Socket at '{sock_path}'")
        try:
            self.uds_server = await asyncio.start_unix_server(
                self.on_unix_socket_connected, sock_path, limit=UNIX_BUFFER_LIMIT
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(f"Failed to create Unix Domain Socket: {sock_path}")
            self.uds_server = None

    def on_unix_socket_connected(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peercred = get_unix_peer_credentials(writer, "Unix Client Connection")
        UnixSocketClient(self.server, reader, writer, peercred)

    async def close(self) -> None:
        if self.uds_server is not None:
            self.uds_server.close()
            await self.uds_server.wait_closed()
            self.uds_server = None

class UnixSocketClient(BaseRemoteConnection):
    def __init__(
        self,
        server: Server,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peercred: Dict[str, int]
    ) -> None:
        self.on_create(server)
        self.writer = writer
        self._peer_cred = peercred
        self._connected_time = self.eventloop.get_loop_time()
        pid = self._peer_cred.get("process_id")
        uid = self._peer_cred.get("user_id")
        gid = self._peer_cred.get("group_id")
        self.wsm.add_client(self)
        logging.info(
            f"Unix Socket Opened - Client ID: {self.uid}, "
            f"Process ID: {pid}, User ID: {uid},  Group ID: {gid}"
        )
        self.eventloop.register_callback(self._read_messages, reader)

    async def _read_messages(self, reader: asyncio.StreamReader) -> None:
        errors_remaining: int = 10
        while not reader.at_eof():
            try:
                data = await reader.readuntil(b'\x03')
                decoded = data[:-1].decode(encoding="utf-8")
            except (ConnectionError, asyncio.IncompleteReadError):
                break
            except asyncio.CancelledError:
                logging.exception("Unix Client Stream Read Cancelled")
                raise
            except Exception:
                logging.exception("Unix Client Stream Read Error")
                errors_remaining -= 1
                if not errors_remaining or self.is_closed:
                    break
                continue
            errors_remaining = 10
            self.eventloop.register_callback(self._process_message, decoded)
        logging.debug("Unix Socket Disconnection From _read_messages()")
        await self._on_close(reason="Read Exit")

    async def write_to_socket(self, message: Union[bytes, str]) -> None:
        if isinstance(message, str):
            data = message.encode() + b"\x03"
        else:
            data = message + b"\x03"
        try:
            self.writer.write(data)
            await self.writer.drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.debug("Unix Socket Disconnection From write_to_socket()")
            await self._on_close(reason="Write Exception")

    async def _on_close(
        self,
        code: Optional[int] = None,
        reason: Optional[str] = None
    ) -> None:
        if self.is_closed:
            return
        self.is_closed = True
        kconn: Klippy = self.server.lookup_component("klippy_connection")
        kconn.remove_subscription(self)
        if not self.writer.is_closing():
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.message_buf = []
        for resp in self.pending_responses.values():
            resp.set_exception(
                self.server.error("Client Socket Disconnected", 500)
            )
        self.pending_responses = {}
        logging.info(
            f"Unix Socket Closed: ID: {self.uid}, "
            f"Close Code: {code}, "
            f"Close Reason: {reason}"
        )
        if self._client_data["type"] == "agent":
            extensions: ExtensionManager
            extensions = self.server.lookup_component("extensions")
            extensions.remove_agent(self)
        self.wsm.remove_client(self)

    def close_socket(self, code: int, reason: str) -> None:
        if not self.is_closed:
            self.eventloop.register_callback(self._on_close, code, reason)


def load_component(config: ConfigHelper) -> ExtensionManager:
    return ExtensionManager(config)
