# Moonraker extension management
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
from websockets import BaseSocketClient


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
    from confighelper import ConfigHelper
    from websockets import WebRequest

class ExtensionManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.agents: Dict[str, BaseSocketClient] = {}
        self.server.register_endpoint(
            "/connection/send_event", ["POST"], self._handle_agent_event,
            transports=["websocket"]
        )
        self.server.register_endpoint(
            "/server/extensions/list", ["GET"], self._handle_list_extensions
        )
        self.server.register_endpoint(
            "/server/extensions/request", ["POST"], self._handle_call_agent
        )

    def register_agent(self, connection: BaseSocketClient) -> None:
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

    def remove_agent(self, connection: BaseSocketClient) -> None:
        name = connection.client_data["name"]
        if name in self.agents:
            del self.agents[name]
            evt: Dict[str, Any] = {"agent": name, "event": "disconnected"}
            connection.send_notification("agent_event", [evt])

    async def _handle_agent_event(self, web_request: WebRequest) -> str:
        conn = web_request.get_connection()
        if not isinstance(conn, BaseSocketClient):
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
        return await conn.call_method(method, args)

def load_component(config: ConfigHelper) -> ExtensionManager:
    return ExtensionManager(config)
