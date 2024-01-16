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
from ..common import RequestType

if TYPE_CHECKING:
    from typing import Optional
    from ..common import WebRequest
    from .http_client import HttpClient, HttpResponse
    from .database import MoonrakerDatabase
    from .announcements import Announcements
    from .klippy_apis import KlippyAPI as APIComp
    from confighelper import ConfigHelper

DB_NAMESPACE = "moonraker"
ACTIVE_SPOOL_KEY = "spoolman.spool_id"


class SpoolManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.sync_rate_seconds = config.getint("sync_rate", default=5, minval=1)
        self.last_sync_time = datetime.datetime.now()
        self.extruded_lock = asyncio.Lock()
        self.spoolman_url = f"{config.get('server').rstrip('/')}/api"
        self.spool_id: Optional[int] = None
        self.extruded: float = 0
        self._error_logged: bool = False
        self._highest_epos: float = 0
        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.database: MoonrakerDatabase = self.server.lookup_component("database")
        announcements: Announcements = self.server.lookup_component("announcements")
        announcements.register_feed("spoolman")
        self._register_notifications()
        self._register_listeners()
        self._register_endpoints()
        self.server.register_remote_method(
            "spoolman_set_active_spool", self.set_active_spool
        )

    def _register_notifications(self):
        self.server.register_notification("spoolman:active_spool_set")

    def _register_listeners(self):
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready
        )

    def _register_endpoints(self):
        self.server.register_endpoint(
            "/server/spoolman/spool_id",
            RequestType.GET | RequestType.POST,
            self._handle_spool_id_request,
        )
        self.server.register_endpoint(
            "/server/spoolman/proxy",
            RequestType.POST,
            self._proxy_spoolman_request,
        )

    async def component_init(self) -> None:
        self.spool_id = await self.database.get_item(
            DB_NAMESPACE, ACTIVE_SPOOL_KEY, None
        )
        if self.spool_id is not None:
            response = await self.http_client.get(
                f"{self.spoolman_url}/v1/spool/{self.spool_id}",
                connect_timeout=1., request_timeout=2.,
            )
            if response.status_code == 404:
                logging.info(f"Spool ID {self.spool_id} not found, setting to None")
                self._set_spool(None)
            elif response.has_error():
                err_msg = self._get_response_error(response)
                logging.info(
                    "Attempt to initialize Spoolman connection failed with the "
                    f"following: {err_msg}"
                )
            else:
                logging.info(f"Found Spool ID {self.spool_id} on spoolman instance")

    async def _handle_klippy_ready(self):
        result = await self.klippy_apis.subscribe_objects(
            {"toolhead": ["position"]}, self._handle_status_update, {}
        )
        initial_e_pos = self._eposition_from_status(result)
        logging.debug(f"Initial epos: {initial_e_pos}")
        if initial_e_pos is not None:
            self._highest_epos = initial_e_pos
        else:
            logging.error("Spoolman integration unable to subscribe to epos")
            raise self.server.error("Unable to subscribe to e position")

    def _get_response_error(self, response: HttpResponse) -> str:
        err_msg = f"HTTP error: {response.status_code} {response.error}"
        try:
            resp = response.json()
            assert isinstance(resp, dict)
            json_msg: str = resp["message"]
        except Exception:
            pass
        else:
            err_msg += f", Spoolman message: {json_msg}"
        return err_msg

    def _eposition_from_status(self, status: Dict[str, Any]) -> Optional[float]:
        position = status.get("toolhead", {}).get("position", [])
        return position[3] if len(position) > 3 else None

    async def _handle_status_update(self, status: Dict[str, Any], _: float) -> None:
        epos = self._eposition_from_status(status)
        if epos and epos > self._highest_epos:
            async with self.extruded_lock:
                self.extruded += epos - self._highest_epos
                self._highest_epos = epos

            now = datetime.datetime.now()
            difference = now - self.last_sync_time
            if difference.total_seconds() > self.sync_rate_seconds:
                self.last_sync_time = now
                logging.debug("Sync period elapsed, tracking usage")
                await self.track_filament_usage()

    async def set_active_spool(self, spool_id: Optional[int]) -> None:
        if self.spool_id == spool_id:
            logging.info(f"Spool ID already set to: {spool_id}")
            return
        # Store the current spool usage before switching
        if self.spool_id is not None:
            await self.track_filament_usage()
        elif spool_id is not None:
            async with self.extruded_lock:
                self.extruded = 0
        self._set_spool(spool_id)
        logging.info(f"Setting active spool to: {spool_id}")

    def _set_spool(self, spool_id: Optional[int]) -> None:
        if spool_id == self.spool_id:
            return
        self.spool_id = spool_id
        self.database.insert_item(DB_NAMESPACE, ACTIVE_SPOOL_KEY, spool_id)
        self.server.send_event(
            "spoolman:active_spool_set", {"spool_id": spool_id}
        )

    async def track_filament_usage(self):
        spool_id = self.spool_id
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
                    url=f"{self.spoolman_url}/v1/spool/{spool_id}/use",
                    body={
                        "use_length": used_length,
                    },
                )
                if response.has_error():
                    if response.status_code == 404:
                        logging.info(
                            f"Spool ID {self.spool_id} not found, setting to None"
                        )
                        self._set_spool(None)
                    else:
                        if not self._error_logged:
                            error_msg = self._get_response_error(response)
                            self._error_logged = True
                            logging.info(
                                f"Failed to update extrusion for spool id {spool_id}, "
                                f"received {error_msg}"
                            )
                        return
                self._error_logged = False
                self.extruded = 0

    async def _handle_spool_id_request(self, web_request: WebRequest):
        if web_request.get_request_type() == RequestType.POST:
            spool_id = web_request.get_int("spool_id", None)
            await self.set_active_spool(spool_id)
        # For GET requests we will simply return the spool_id
        return {"spool_id": self.spool_id}

    async def _proxy_spoolman_request(self, web_request: WebRequest):
        method = web_request.get_str("request_method")
        path = web_request.get_str("path")
        query = web_request.get_str("query", None)
        body = web_request.get("body", None)

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise self.server.error(f"Invalid HTTP method: {method}")

        if body is not None and method == "GET":
            raise self.server.error("GET requests cannot have a body")

        if len(path) < 4 or path[:4] != "/v1/":
            raise self.server.error(
                "Invalid path, must start with the API version, e.g. /v1"
            )

        if query is not None:
            query = f"?{query}"
        else:
            query = ""

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
