# Integration with Spoolman
#
# Copyright (C) 2023 Daniel Hultgren <daniel.cf.hultgren@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import logging
import re
import contextlib
import tornado.websocket as tornado_ws
from tornado import version_info as tornado_version
from ..common import RequestType, HistoryFieldData
from ..utils import json_wrapper as jsonw
from typing import (
    TYPE_CHECKING,
    List,
    Dict,
    Any,
    Optional,
    Union,
    cast
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .http_client import HttpClient, HttpResponse
    from .database import MoonrakerDatabase
    from .announcements import Announcements
    from .klippy_apis import KlippyAPI as APIComp
    from .history import History
    from tornado.websocket import WebSocketClientConnection

DB_NAMESPACE = "moonraker"
ACTIVE_SPOOL_KEY = "spoolman.spool_id"

class SpoolManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self._get_spoolman_urls(config)
        self.sync_rate_seconds = config.getint("sync_rate", default=5, minval=1)
        self.report_timer = self.eventloop.register_timer(self.report_extrusion)
        self.pending_reports: Dict[int, float] = {}
        self.spoolman_ws: Optional[WebSocketClientConnection] = None
        self.connection_task: Optional[asyncio.Task] = None
        self.spool_check_task: Optional[asyncio.Task] = None
        self.ws_connected: bool = False
        self.reconnect_delay: float = 2.
        self.is_closing: bool = False
        self.spool_id: Optional[int] = None
        self._error_logged: bool = False
        self._highest_epos: float = 0
        self._last_epos: float = 0
        self._current_extruder: str = "extruder"
        self.spool_history = HistoryFieldData(
            "spool_ids", "spoolman", "Spool IDs used", "collect",
            reset_callback=self._on_history_reset
        )
        history: History = self.server.lookup_component("history")
        history.register_auxiliary_field(self.spool_history)
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

    def _get_spoolman_urls(self, config: ConfigHelper) -> None:
        orig_url = config.get('server')
        url_match = re.match(r"(?i:(?P<scheme>https?)://)?(?P<host>.+)", orig_url)
        if url_match is None:
            raise config.error(
                f"Section [spoolman], Option server: {orig_url}: Invalid URL format"
            )
        scheme = url_match["scheme"] or "http"
        host = url_match["host"].rstrip("/")
        ws_scheme = "wss" if scheme == "https" else "ws"
        self.spoolman_url = f"{scheme}://{host}/api"
        self.ws_url = f"{ws_scheme}://{host}/api/v1/spool"

    def _register_notifications(self):
        self.server.register_notification("spoolman:active_spool_set")
        self.server.register_notification("spoolman:spoolman_status_changed")

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
        self.server.register_endpoint(
            "/server/spoolman/status",
            RequestType.GET,
            self._handle_status_request,
        )

    def _on_history_reset(self) -> List[int]:
        if self.spool_id is None:
            return []
        return [self.spool_id]

    async def component_init(self) -> None:
        self.spool_id = await self.database.get_item(
            DB_NAMESPACE, ACTIVE_SPOOL_KEY, None
        )
        self.connection_task = self.eventloop.create_task(self._connect_websocket())

    async def _connect_websocket(self) -> None:
        log_connect: bool = True
        err_list: List[Exception] = []
        while not self.is_closing:
            if log_connect:
                logging.info(f"Connecting To Spoolman: {self.ws_url}")
                log_connect = False
            try:
                self.spoolman_ws = await tornado_ws.websocket_connect(
                    self.ws_url,
                    connect_timeout=5.,
                    ping_interval=None if tornado_version < (6, 5) else 20.
                )
                setattr(self.spoolman_ws, "on_ping", self._on_ws_ping)
                cur_time = self.eventloop.get_loop_time()
                self._last_ping_received = cur_time
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if len(err_list) < 10:
                    # Allow up to 10 unique errors.
                    for err in err_list:
                        if type(err) is type(e) and err.args == e.args:
                            break
                    else:
                        err_list.append(e)
                        verbose = self.server.is_verbose_enabled()
                        if verbose:
                            logging.exception("Failed to connect to Spoolman")
                        self.server.add_log_rollover_item(
                            "spoolman_connect", f"Failed to Connect to spoolman: {e}",
                            not verbose
                        )
            else:
                err_list = []
                self.ws_connected = True
                self._error_logged = False
                self.report_timer.start()
                self.server.add_log_rollover_item(
                    "spoolman_connect", "Connected to Spoolman Spool Manager"
                )
                if self.spool_id is not None:
                    self._cancel_spool_check_task()
                    coro = self._check_spool_deleted()
                    self.spool_check_task = self.eventloop.create_task(coro)
                self._send_status_notification()
                await self._read_messages()
                log_connect = True
            if not self.is_closing:
                await asyncio.sleep(self.reconnect_delay)

    async def _read_messages(self) -> None:
        message: Union[str, bytes, None]
        while self.spoolman_ws is not None:
            message = await self.spoolman_ws.read_message()
            if isinstance(message, str):
                self._decode_message(message)
            elif message is None:
                self.report_timer.stop()
                self.ws_connected = False
                cur_time = self.eventloop.get_loop_time()
                ping_time: float = cur_time - self._last_ping_received
                reason = code = None
                if self.spoolman_ws is not None:
                    reason = self.spoolman_ws.close_reason
                    code = self.spoolman_ws.close_code
                logging.info(
                    f"Spoolman Disconnected - Code: {code}, Reason: {reason}, "
                    f"Server Ping Time Elapsed: {ping_time}"
                )
                self.spoolman_ws = None
                if not self.is_closing:
                    self._send_status_notification()
                break

    def _decode_message(self, message: str) -> None:
        event: Dict[str, Any] = jsonw.loads(message)
        if event.get("resource") != "spool":
            return
        if self.spool_id is not None and event.get("type") == "deleted":
            payload: Dict[str, Any] = event.get("payload", {})
            if payload.get("id") == self.spool_id:
                self.pending_reports.pop(self.spool_id, None)
                self.set_active_spool(None)

    def _cancel_spool_check_task(self) -> None:
        if self.spool_check_task is None or self.spool_check_task.done():
            return
        self.spool_check_task.cancel()

    async def _check_spool_deleted(self) -> None:
        if self.spool_id is not None:
            response = await self.http_client.get(
                f"{self.spoolman_url}/v1/spool/{self.spool_id}",
                connect_timeout=1., request_timeout=2.
            )
            if response.status_code == 404:
                logging.info(f"Spool ID {self.spool_id} not found, setting to None")
                self.pending_reports.pop(self.spool_id, None)
                self.set_active_spool(None)
            elif response.has_error():
                err_msg = self._get_response_error(response)
                logging.info(f"Attempt to check spool status failed: {err_msg}")
            else:
                logging.info(f"Found Spool ID {self.spool_id} on spoolman instance")
        self.spool_check_task = None

    def connected(self) -> bool:
        return self.ws_connected

    def _on_ws_ping(self, data: bytes = b"") -> None:
        self._last_ping_received = self.eventloop.get_loop_time()

    async def _handle_klippy_ready(self) -> None:
        result: Dict[str, Dict[str, Any]]
        result = await self.klippy_apis.subscribe_objects(
            {"toolhead": ["position", "extruder"]}, self._handle_status_update, {}
        )
        toolhead = result.get("toolhead", {})
        self._current_extruder = toolhead.get("extruder", "extruder")
        initial_e_pos = toolhead.get("position", [None]*4)[3]
        logging.debug(f"Initial epos: {initial_e_pos}")
        if initial_e_pos is not None:
            self._highest_epos = initial_e_pos
        else:
            logging.error("Spoolman integration unable to subscribe to epos")
            raise self.server.error("Unable to subscribe to e position")

    def _get_response_error(self, response: HttpResponse) -> str:
        err_msg = f"HTTP error: {response.status_code} {response.error}"
        with contextlib.suppress(Exception):
            msg: Optional[str] = cast(dict, response.json())["message"]
            err_msg += f", Spoolman message: {msg}"
        return err_msg

    def _handle_status_update(self, status: Dict[str, Any], _: float) -> None:
        toolhead: Optional[Dict[str, Any]] = status.get("toolhead")
        if toolhead is None:
            return
        epos: float = toolhead.get("position", [0, 0, 0, self._highest_epos])[3]
        self._last_epos = epos
        extr = toolhead.get("extruder", self._current_extruder)
        if extr != self._current_extruder:
            self._highest_epos = epos
            self._current_extruder = extr
        elif epos > self._highest_epos:
            if self.spool_id is not None:
                self._add_extrusion(self.spool_id, epos - self._highest_epos)
            self._highest_epos = epos

    def _add_extrusion(self, spool_id: int, used_length: float) -> None:
        if spool_id in self.pending_reports:
            self.pending_reports[spool_id] += used_length
        else:
            self.pending_reports[spool_id] = used_length

    def set_active_spool(self, spool_id: Union[int, None]) -> None:
        assert spool_id is None or isinstance(spool_id, int)
        if self.spool_id == spool_id:
            logging.info(f"Spool ID already set to: {spool_id}")
            return
        self.spool_history.tracker.update(spool_id)
        self.spool_id = spool_id
        self.database.insert_item(DB_NAMESPACE, ACTIVE_SPOOL_KEY, spool_id)
        self._highest_epos = self._last_epos
        self.server.send_event(
            "spoolman:active_spool_set", {"spool_id": spool_id}
        )
        logging.info(f"Setting active spool to: {spool_id}")

    async def report_extrusion(self, eventtime: float) -> float:
        if not self.ws_connected:
            return eventtime + self.sync_rate_seconds
        pending_reports = self.pending_reports
        self.pending_reports = {}
        for spool_id, used_length in pending_reports.items():
            if not self.ws_connected:
                self._add_extrusion(spool_id, used_length)
                continue
            logging.debug(
                f"Sending spool usage: ID: {spool_id}, Length: {used_length:.3f}mm"
            )
            response = await self.http_client.request(
                method="PUT",
                url=f"{self.spoolman_url}/v1/spool/{spool_id}/use",
                body={"use_length": used_length}
            )
            if response.has_error():
                if response.status_code == 404:
                    # Since the spool is deleted we can remove any pending reports
                    # added while waiting for the request
                    self.pending_reports.pop(spool_id, None)
                    if spool_id == self.spool_id:
                        logging.info(f"Spool ID {spool_id} not found, setting to None")
                        self.set_active_spool(None)
                else:
                    if not self._error_logged:
                        error_msg = self._get_response_error(response)
                        self._error_logged = True
                        logging.info(
                            f"Failed to update extrusion for spool id {spool_id}, "
                            f"received {error_msg}"
                        )
                    # Add missed reports back to pending reports for the next cycle
                    self._add_extrusion(spool_id, used_length)
                    continue
            self._error_logged = False
        return self.eventloop.get_loop_time() + self.sync_rate_seconds

    async def _handle_spool_id_request(self, web_request: WebRequest):
        if web_request.get_request_type() == RequestType.POST:
            spool_id = web_request.get_int("spool_id", None)
            self.set_active_spool(spool_id)
        # For GET requests we will simply return the spool_id
        return {"spool_id": self.spool_id}

    async def _proxy_spoolman_request(self, web_request: WebRequest):
        method = web_request.get_str("request_method")
        path = web_request.get_str("path")
        query = web_request.get_str("query", None)
        body = web_request.get("body", None)
        use_v2_response = web_request.get_boolean("use_v2_response", False)
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise self.server.error(f"Invalid HTTP method: {method}")
        if body is not None and method == "GET":
            raise self.server.error("GET requests cannot have a body")
        if len(path) < 4 or path[:4] != "/v1/":
            raise self.server.error(
                "Invalid path, must start with the API version, e.g. /v1"
            )
        query = f"?{query}" if query is not None else ""
        full_url = f"{self.spoolman_url}{path}{query}"
        if not self.ws_connected:
            if not use_v2_response:
                raise self.server.error("Spoolman server not available", 503)
            return {
                "response": None,
                "error": {
                    "status_code": 503,
                    "message": "Spoolman server not available"
                }
            }
        logging.debug(f"Proxying {method} request to {full_url}")
        response = await self.http_client.request(
            method=method,
            url=full_url,
            body=body,
        )
        if not use_v2_response:
            response.raise_for_status()
            return response.json()
        if response.has_error():
            msg: str = str(response.error or "")
            with contextlib.suppress(Exception):
                spoolman_msg = cast(dict, response.json()).get("message", msg)
                msg = spoolman_msg
            return {
                "response": None,
                "error": {
                    "status_code": response.status_code,
                    "message": msg
                }
            }
        else:
            return {
                "response": response.json(),
                "response_headers": dict(response.headers.items()),
                "error": None
            }

    async def _handle_status_request(self, web_request: WebRequest) -> Dict[str, Any]:
        pending: List[Dict[str, Any]] = [
            {"spool_id": sid, "filament_used": used} for sid, used in
            self.pending_reports.items()
        ]
        return {
            "spoolman_connected": self.ws_connected,
            "pending_reports": pending,
            "spool_id": self.spool_id
        }

    def _send_status_notification(self) -> None:
        self.server.send_event(
            "spoolman:spoolman_status_changed",
            {"spoolman_connected": self.ws_connected}
        )

    async def close(self):
        self.is_closing = True
        self.report_timer.stop()
        if self.spoolman_ws is not None:
            self.spoolman_ws.close(1001, "Moonraker Shutdown")
        self._cancel_spool_check_task()
        if self.connection_task is None or self.connection_task.done():
            return
        try:
            await asyncio.wait_for(self.connection_task, 2.)
        except asyncio.TimeoutError:
            pass

def load_component(config: ConfigHelper) -> SpoolManager:
    return SpoolManager(config)
