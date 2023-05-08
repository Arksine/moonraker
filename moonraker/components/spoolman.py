# Integration with Spoolman
#
# Copyright (C) 2023 Daniel Hultgren <daniel.cf.hultgren@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import datetime
import logging
from typing import TYPE_CHECKING, Dict, Any, List
from urllib.parse import urlencode

if TYPE_CHECKING:
    from typing import Optional
    from moonraker.websockets import WebRequest
    from moonraker.components.http_client import HttpClient
    from moonraker.components.database import MoonrakerDatabase, NamespaceWrapper
    from .klippy_apis import KlippyAPI as APIComp
    from confighelper import ConfigHelper

SPOOL_NAMESPACE = "spoolman"
MOONRAKER_NAMESPACE = "moonraker"
ACTIVE_SPOOL_KEY = "spoolman.active_spool_id"

class SpoolManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.highest_e_pos = 0.0
        self.extruded = 0.0
        self.sync_rate_seconds = config.getint("sync_rate", 5)
        self.last_sync_time = datetime.datetime.now()
        self.extruded_lock = asyncio.Lock()
        
        self.spoolman_url = config.get("server", None)
        if self.spoolman_url is None:
            logging.error("Server config not set for spoolman.")
            return
        self.spoolman_url = f"{self.spoolman_url.rstrip('/')}/api/v1"

        self.http_client: HttpClient = self.server.lookup_component("http_client")

        database: MoonrakerDatabase = self.server.lookup_component("database")
        database.register_local_namespace("spoolman")
        self.moonraker_db: NamespaceWrapper = database.wrap_namespace(
            MOONRAKER_NAMESPACE, parse_keys=False)

        self._register_listeners()
        self._register_endpoints()
        self.klippy_apis: APIComp = self.server.lookup_component('klippy_apis')

    def _register_listeners(self):
        self.server.register_event_handler('server:klippy_ready',
                                           self._handle_server_ready)

    def _register_endpoints(self):
        self.server.register_endpoint(
            r"/spoolman/[\W\w]+$", ['GET', 'POST', 'DELETE', 'PUT', 'PATCH'],
            self._proxy_spoolman_request)

    async def _handle_server_ready(self):
        self.server.register_event_handler(
            'server:status_update', self._handle_status_update)
        sub: Dict[str, Optional[List[str]]] = {'toolhead': ['position']}
        result = await self.klippy_apis.subscribe_objects(sub)
        initial_e_pos = self._eposition_from_status(result)

        if initial_e_pos is not None:
            self.highest_e_pos = initial_e_pos
        else:
            logging.error("Spool manager unable to subscribe to epos")
            raise self.server.error('Unable to subscribe to e position')

    def _eposition_from_status(self, status: Dict[str, Any]) -> Optional[float]:
        position = status.get('toolhead', {}).get('position', [])
        return position[3] if len(position) > 0 else None

    async def _handle_status_update(self, status: Dict[str, Any]) -> None:
        epos = self._eposition_from_status(status)
        logging.info(f"Epos: {epos}")
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

    async def set_active_spool(self, spool_id: str) -> bool:
        self.moonraker_db[ACTIVE_SPOOL_KEY] = spool_id
        await self.server.send_event('spool_manager:active_spool_set',
                                        {'spool_id': spool_id})
        logging.info(f'Setting spool active, id: {spool_id}')
        return True
        
    async def get_active_spool_id(self) -> str:
        return await self.moonraker_db.get(ACTIVE_SPOOL_KEY, None)
    
    async def track_filament_usage(self):
        spool_id = await self.get_active_spool_id()
        async with self.extruded_lock:
            if self.extruded > 0:
                used_length = self.extruded

                response = await self.http_client.request(
                    method="PUT",
                    url=f"{self.spoolman_url}/spool/{spool_id}/use",
                    body={
                        "use_length": used_length,
                    },
                )
                response.raise_for_status()

                self.extruded = 0

                logging.info(f'Tracking filament usage, '
                                f'spool_id: {spool_id}, ' +
                                f'length: {used_length}, ')
                    
    async def _proxy_spoolman_request(self, web_request: WebRequest):
        body = None
        query = ""
        if web_request.action != "GET":
            body = web_request.args
        else:
            query = "?" + urlencode(web_request.args)

        endpoint = web_request.endpoint.removeprefix("/spoolman")
        full_url = f"{self.spoolman_url}{endpoint}{query}"

        logging.debug(f"Proxying {web_request.action} request to {full_url}")
        
        response = await self.http_client.request(
            method=web_request.action,
            url=full_url,
            body=body,
        )
        response.raise_for_status()

        return response.json()

def load_component(config: ConfigHelper) -> SpoolManager:
    return SpoolManager(config)
