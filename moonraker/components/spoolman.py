# Integration with Spoolman
#
# Copyright (C) 2023 Daniel Hultgren <daniel.cf.hultgren@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Dict, Any

if TYPE_CHECKING:
    from typing import Optional
    from moonraker.websockets import WebRequest
    from moonraker.components.http_client import HttpClient
    from moonraker.components.database import MoonrakerDatabase
    from moonraker.utils import ServerError
    from .klippy_apis import KlippyAPI as APIComp
    from confighelper import ConfigHelper

DB_NAMESPACE = "moonraker"
ACTIVE_SPOOL_KEY = "spoolman.spool_id"


class SpoolManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()

        self.highest_e_pos = 0.0
        self.extruded = 0.0
        self.sync_rate_seconds = config.getint("sync_rate", default=5, above=1)
        self.last_sync_time = datetime.datetime.now()
        self.extruded_lock = asyncio.Lock()
        self.spoolman_url = f"{config.get('server').rstrip('/')}/api"
        self.spool_id: Optional[int] = None

        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")
        self.http_client: HttpClient = self.server.lookup_component(
            "http_client"
        )
        self.database: MoonrakerDatabase = self.server.lookup_component(
            "database"
        )

        self._register_notifications()
        self._register_listeners()
        self._register_endpoints()

    def _register_notifications(self):
        self.server.register_notification("spoolman:active_spool_set")

    def _register_listeners(self):
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_server_ready
        )

    def _register_endpoints(self):
        self.server.register_endpoint(
            "/spoolman/spool_id",
            ["GET", "POST"],
            self._handle_spool_id_request,
        )
        self.server.register_endpoint(
            "/spoolman/proxy",
            ["POST"],
            self._proxy_spoolman_request,
        )

    async def component_init(self) -> None:
        self.spool_id = await self.database.get_item(
            DB_NAMESPACE, ACTIVE_SPOOL_KEY, None
        )

    async def _handle_server_ready(self):
        self.server.register_event_handler(
            "server:status_update", self._handle_status_update
        )
        result = await self.klippy_apis.subscribe_objects(
            {"toolhead": ["position"]}
        )
        initial_e_pos = self._eposition_from_status(result)

        logging.debug(f"Initial epos: {initial_e_pos}")

        if initial_e_pos is not None:
            self.highest_e_pos = initial_e_pos
        else:
            logging.error("Spoolman integration unable to subscribe to epos")
            raise self.server.error("Unable to subscribe to e position")

    def _eposition_from_status(self, status: Dict[str, Any]) -> Optional[float]:
        position = status.get("toolhead", {}).get("position", [])
        return position[3] if len(position) > 3 else None

    async def _handle_status_update(self, status: Dict[str, Any]) -> None:
        epos = self._eposition_from_status(status)
        if epos and epos > self.highest_e_pos:
            async with self.extruded_lock:
                self.extruded += epos - self.highest_e_pos
                self.highest_e_pos = epos

            now = datetime.datetime.now()
            difference = now - self.last_sync_time
            if difference.total_seconds() > self.sync_rate_seconds:
                self.last_sync_time = now
                logging.debug("Sync period elapsed, tracking usage")
                await self.track_filament_usage()

    async def set_active_spool(self, spool_id: Optional[int]) -> None:
        self.database.insert_item(DB_NAMESPACE, ACTIVE_SPOOL_KEY, spool_id)
        self.spool_id = spool_id
        await self.server.send_event(
            "spool_manager:active_spool_set", {"spool_id": spool_id}
        )
        logging.info(f"Setting active spool to: {spool_id}")

    async def get_active_spool(self) -> Optional[int]:
        return self.spool_id

    async def track_filament_usage(self):
        spool_id = self.get_active_spool()
        if spool_id is None:
            logging.debug("No active spool, skipping tracking")
            return
        async with self.extruded_lock:
            if self.extruded > 0:
                used_length = self.extruded

                logging.debug(
                    f"Sending spool usage: "
                    f"ID: {spool_id}, "
                    f"Length: {used_length:.3f}mm, "
                )

                response = await self.http_client.request(
                    method="PUT",
                    url=f"{self.spoolman_url}/spool/{spool_id}/use",
                    body={
                        "use_length": used_length,
                    },
                )
                response.raise_for_status()

                self.extruded = 0

    async def _handle_spool_id_request(self, web_request: WebRequest):
        if web_request.method == "GET":
            return {"spool_id": self.get_active_spool()}
        elif web_request.method == "POST":
            spool_id = web_request.get_int("spool_id", None)
            await self.set_active_spool(spool_id)
            return True

    async def _proxy_spoolman_request(self, web_request: WebRequest):
        method = web_request.get_str("method")
        path = web_request.get_str("path")
        query = web_request.get_str("query", None)
        body = web_request.get("body", None)

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ServerError(f"Invalid HTTP method: {method}")

        if body is not None and method == "GET":
            raise ServerError("GET requests cannot have a body")

        if len(path) < 4 or path[:4] != "/v1/":
            raise ServerError(
                "Invalid path, must start with the API version, e.g. /v1"
            )

        if query is not None:
            query = f"?{query}"

        full_url = f"{self.spoolman_url}{path}{query}"

        logging.debug(f"Proxying {method} request to {full_url}")

        response = await self.http_client.request(
            method=method,
            url=full_url,
            body=body,
        )
        response.raise_for_status()

        return response.json()


def load_component(config: ConfigHelper) -> SpoolManager:
    return SpoolManager(config)
