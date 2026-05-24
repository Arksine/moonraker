# Native FilaMan integration for Moonraker
#
# Inspired by Moonraker's Spoolman component.
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..common import HistoryFieldData, RequestType
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union, cast

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .announcements import Announcements
    from .database import MoonrakerDatabase
    from .history import History
    from .http_client import HttpClient, HttpResponse
    from .klippy_apis import KlippyAPI as APIComp

DB_NAMESPACE = "moonraker"
ACTIVE_SPOOL_KEY = "filaman.spool_id"
LEGACY_ACTIVE_SPOOL_KEY = "spoolman.spool_id"

DEFAULT_PLA_DENSITY_G_CM3 = 1.24
DEFAULT_FILAMENT_DIAMETER_MM = 1.75
CONSUMPTION_PATH_RE = re.compile(r"^/spools/\d+/consumptions$")


class FilaManManager:
    def __init__(self, config: ConfigHelper):
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()

        self._get_filaman_urls(config)
        self.api_key: Optional[str] = config.get("api_key", default=None)
        self.sync_rate_seconds = config.getint("sync_rate", default=5, minval=1)
        self.reconnect_delay: float = 2.0
        self.connected_check_delay: float = 30.0

        self.default_density_g_cm3 = self._get_float_option(
            config,
            "default_density_g_cm3",
            default=DEFAULT_PLA_DENSITY_G_CM3,
            minimum=0.0,
        )
        self.default_diameter_mm = self._get_float_option(
            config,
            "default_diameter_mm",
            default=DEFAULT_FILAMENT_DIAMETER_MM,
            minimum=0.0,
        )

        self.report_timer = self.eventloop.register_timer(self.report_extrusion)
        self.pending_reports: Dict[int, float] = {}

        self.connection_task: Optional[asyncio.Task] = None
        self.spool_check_task: Optional[asyncio.Task] = None
        self.is_closing: bool = False

        self.api_connected: bool = False
        self.spool_id: Optional[int] = None
        self._last_epos: float = 0.0
        self._current_extruder: str = "extruder"

        self._error_logged: bool = False
        self._last_error: Optional[str] = None
        self._last_success_at: Optional[str] = None

        self.spool_history = HistoryFieldData(
            "spool_ids",
            "filaman",
            "Spool IDs used",
            "collect",
            reset_callback=self._on_history_reset,
        )
        history: History = self.server.lookup_component("history")
        history.register_auxiliary_field(self.spool_history)

        self.klippy_apis: APIComp = self.server.lookup_component("klippy_apis")
        self.http_client: HttpClient = self.server.lookup_component("http_client")
        self.database: MoonrakerDatabase = self.server.lookup_component("database")

        announcements: Announcements = self.server.lookup_component("announcements")
        with contextlib.suppress(Exception):
            announcements.register_feed("filaman")
        with contextlib.suppress(Exception):
            announcements.register_feed("spoolman")

        if not self.api_key:
            logging.warning(
                "FilaMan component configured without api_key. "
                "If your API requires authentication, requests will fail."
            )

        self._register_notifications()
        self._register_listeners()
        self._register_endpoints()
        self._register_remote_methods()

    def _get_float_option(
        self,
        config: ConfigHelper,
        option: str,
        default: float,
        minimum: float,
    ) -> float:
        raw_val = config.get(option, default=None)
        if raw_val is None:
            return default
        try:
            value = float(raw_val)
        except Exception:
            raise config.error(
                f"Section [filaman], Option {option}: '{raw_val}' is not a valid number"
            )
        if value <= minimum:
            raise config.error(
                f"Section [filaman], Option {option}: value must be > {minimum}"
            )
        return value

    def _get_filaman_urls(self, config: ConfigHelper) -> None:
        orig_url = config.get("server")
        if not re.match(r"(?i)^https?://", orig_url):
            orig_url = f"http://{orig_url}"
        parsed = urlparse(orig_url)
        if not parsed.scheme or not parsed.netloc:
            raise config.error(
                f"Section [filaman], Option server: {orig_url}: Invalid URL format"
            )

        base = f"{parsed.scheme}://{parsed.netloc}"
        server_path = parsed.path.rstrip("/")

        if server_path.endswith("/api/v1"):
            api_path = server_path
        elif server_path.endswith("/api"):
            api_path = f"{server_path}/v1"
        elif server_path:
            api_path = f"{server_path}/api/v1"
        else:
            api_path = "/api/v1"

        self.server_url = f"{base}{server_path}"
        self.api_url = f"{base}{api_path}"

    def _register_notifications(self) -> None:
        self._register_notification_safe("filaman:active_spool_set")
        self._register_notification_safe("filaman:filaman_status_changed")
        self._register_notification_safe("spoolman:active_spool_set")
        self._register_notification_safe("spoolman:spoolman_status_changed")

    def _register_notification_safe(self, event_name: str) -> None:
        with contextlib.suppress(Exception):
            self.server.register_notification(event_name)

    def _register_listeners(self) -> None:
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_klippy_ready
        )

    def _register_endpoints(self) -> None:
        endpoint_prefixes = ["/server/filaman", "/server/spoolman"]

        for prefix in endpoint_prefixes:
            self.server.register_endpoint(
                f"{prefix}/spool_id",
                RequestType.GET | RequestType.POST,
                self._handle_spool_id_request,
            )
            self.server.register_endpoint(
                f"{prefix}/proxy",
                RequestType.POST,
                self._proxy_filaman_request,
            )
            self.server.register_endpoint(
                f"{prefix}/status",
                RequestType.GET,
                self._handle_status_request,
            )

    def _register_remote_methods(self) -> None:
        self.server.register_remote_method(
            "filaman_set_active_spool", self.set_active_spool
        )
        with contextlib.suppress(Exception):
            self.server.register_remote_method(
                "spoolman_set_active_spool", self.set_active_spool
            )

    def _on_history_reset(self) -> List[int]:
        if self.spool_id is None:
            return []
        return [self.spool_id]

    async def component_init(self) -> None:
        self.spool_id = await self.database.get_item(
            DB_NAMESPACE, ACTIVE_SPOOL_KEY, None
        )
        if self.spool_id is None:
            self.spool_id = await self.database.get_item(
                DB_NAMESPACE,
                LEGACY_ACTIVE_SPOOL_KEY,
                None,
            )
            if self.spool_id is not None:
                self.database.insert_item(
                    DB_NAMESPACE, ACTIVE_SPOOL_KEY, self.spool_id
                )

        self.report_timer.start()
        self.connection_task = self.eventloop.create_task(self._availability_loop())

        if self.spool_id is not None:
            self._cancel_spool_check_task()
            self.spool_check_task = self.eventloop.create_task(
                self._check_spool_deleted()
            )

    async def _availability_loop(self) -> None:
        while not self.is_closing:
            await self._check_api_available()
            if not self.is_closing:
                delay = (
                    self.connected_check_delay
                    if self.api_connected
                    else self.reconnect_delay
                )
                await asyncio.sleep(delay)

    async def _check_api_available(self) -> None:
        response = await self._request(
            method="GET",
            url=f"{self.api_url}/spools?page=1&page_size=1",
            connect_timeout=2.0,
            request_timeout=4.0,
        )

        if response.has_error():
            msg = self._get_response_error(response)
            self._set_last_error(f"FilaMan availability check failed: {msg}")
            self._set_connected(False)
            return

        self._mark_success()

    def _set_connected(self, value: bool) -> None:
        if self.api_connected == value:
            return
        self.api_connected = value
        self._send_status_notification()

    def connected(self) -> bool:
        return self.api_connected

    async def _handle_klippy_ready(self) -> None:
        result: Dict[str, Dict[str, Any]]
        result = await self.klippy_apis.subscribe_objects(
            {"toolhead": ["position", "extruder"]}, self._handle_status_update, {}
        )
        toolhead = result.get("toolhead", {})
        self._current_extruder = toolhead.get("extruder", "extruder")
        initial_e_pos = toolhead.get("position", [None] * 4)[3]
        logging.debug(f"Initial epos: {initial_e_pos}")
        if initial_e_pos is not None:
            self._last_epos = initial_e_pos
        else:
            logging.error("FilaMan integration unable to subscribe to epos")
            raise self.server.error("Unable to subscribe to e position")

    def _handle_status_update(self, status: Dict[str, Any], _: float) -> None:
        toolhead: Optional[Dict[str, Any]] = status.get("toolhead")
        if toolhead is None:
            return

        epos: float = toolhead.get("position", [0, 0, 0, self._last_epos])[3]
        extr = toolhead.get("extruder", self._current_extruder)
        if extr != self._current_extruder:
            self._current_extruder = extr
            self._last_epos = epos
            return

        epos_delta = epos - self._last_epos
        if epos_delta > 0 and self.spool_id is not None:
            self._add_extrusion(self.spool_id, epos_delta)
        self._last_epos = epos

    def _add_extrusion(self, spool_id: int, used_length_mm: float) -> None:
        if spool_id in self.pending_reports:
            self.pending_reports[spool_id] += used_length_mm
        else:
            self.pending_reports[spool_id] = used_length_mm

    def _set_last_error(self, message: str) -> None:
        self._last_error = message
        if not self._error_logged:
            self._error_logged = True
            logging.info(message)

    def _mark_success(self) -> None:
        self._error_logged = False
        self._last_error = None
        self._last_success_at = datetime.now(timezone.utc).isoformat()
        self._set_connected(True)

    def _get_response_error(self, response: HttpResponse) -> str:
        err_msg = f"HTTP error: {response.status_code} {response.error}"
        with contextlib.suppress(Exception):
            payload = cast(Dict[str, Any], response.json())
            detail = payload.get("detail")
            if isinstance(detail, dict):
                detail_msg = detail.get("message") or detail.get("code")
                if detail_msg:
                    err_msg += f", FilaMan message: {detail_msg}"
                    return err_msg
            if "message" in payload and isinstance(payload["message"], str):
                err_msg += f", FilaMan message: {payload['message']}"
        return err_msg

    async def _request(
        self,
        method: str,
        url: str,
        body: Optional[Union[bytes, str, List[Any], Dict[str, Any]]] = None,
        connect_timeout: float = 5.0,
        request_timeout: float = 10.0,
    ) -> HttpResponse:
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"ApiKey {self.api_key}"
        return await self.http_client.request(
            method=method,
            url=url,
            body=body,
            headers=headers,
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
        )

    def set_active_spool(self, spool_id: Union[int, None]) -> None:
        if spool_id is not None and not isinstance(spool_id, int):
            raise self.server.error("spool_id must be an integer or None")
        if self.spool_id == spool_id:
            logging.info(f"Spool ID already set to: {spool_id}")
            return

        self.spool_history.tracker.update(spool_id)
        self.spool_id = spool_id

        self.database.insert_item(DB_NAMESPACE, ACTIVE_SPOOL_KEY, spool_id)
        self.database.insert_item(DB_NAMESPACE, LEGACY_ACTIVE_SPOOL_KEY, spool_id)

        payload = {"spool_id": spool_id}
        self.server.send_event("filaman:active_spool_set", payload)
        self.server.send_event("spoolman:active_spool_set", payload)

        if spool_id is not None:
            self._cancel_spool_check_task()
            self.spool_check_task = self.eventloop.create_task(
                self._check_spool_deleted()
            )

        logging.info(f"Setting active spool to: {spool_id}")

    async def _check_spool_deleted(self) -> None:
        try:
            if self.spool_id is not None:
                response = await self._request(
                    method="GET",
                    url=f"{self.api_url}/spools/{self.spool_id}",
                    connect_timeout=2.0,
                    request_timeout=4.0,
                )
                if response.status_code == 404:
                    logging.info(f"Spool ID {self.spool_id} not found, setting to None")
                    self.pending_reports.pop(self.spool_id, None)
                    self.set_active_spool(None)
                elif response.has_error():
                    err_msg = self._get_response_error(response)
                    self._set_last_error(
                        f"Attempt to check spool status failed: {err_msg}"
                    )
                else:
                    self._mark_success()
        finally:
            current_task = asyncio.current_task()
            if self.spool_check_task is current_task:
                self.spool_check_task = None

    def _cancel_spool_check_task(self) -> None:
        if self.spool_check_task is None or self.spool_check_task.done():
            return
        self.spool_check_task.cancel()

    async def _fetch_spool(
        self, spool_id: int
    ) -> Tuple[Optional[Dict[str, Any]], HttpResponse]:
        response = await self._request(
            method="GET",
            url=f"{self.api_url}/spools/{spool_id}",
            connect_timeout=2.0,
            request_timeout=5.0,
        )
        if response.has_error():
            return None, response
        with contextlib.suppress(Exception):
            payload = cast(Dict[str, Any], response.json())
            return payload, response
        return None, response

    def _resolve_material_values(
        self, spool_data: Dict[str, Any]
    ) -> Tuple[float, float]:
        filament = spool_data.get("filament")
        if not isinstance(filament, dict):
            filament = {}

        density_raw = filament.get("density_g_cm3")
        diameter_raw = filament.get("diameter_mm")

        density = self.default_density_g_cm3
        diameter = self.default_diameter_mm

        with contextlib.suppress(Exception):
            parsed_density = float(cast(Union[str, int, float], density_raw))
            if parsed_density > 0:
                density = parsed_density

        with contextlib.suppress(Exception):
            parsed_diameter = float(cast(Union[str, int, float], diameter_raw))
            if parsed_diameter > 0:
                diameter = parsed_diameter

        return density, diameter

    def _length_to_weight_g(
        self,
        used_length_mm: float,
        density_g_cm3: float,
        diameter_mm: float,
    ) -> float:
        radius_mm = diameter_mm / 2.0
        cross_section_mm2 = math.pi * radius_mm * radius_mm
        volume_mm3 = cross_section_mm2 * used_length_mm
        volume_cm3 = volume_mm3 / 1000.0
        return volume_cm3 * density_g_cm3

    async def _build_delta_from_length(
        self,
        spool_id: int,
        used_length_mm: float,
    ) -> Tuple[Optional[float], bool, bool]:
        spool_data, response = await self._fetch_spool(spool_id)
        if spool_data is None:
            if response.status_code == 404:
                if spool_id == self.spool_id:
                    logging.info(f"Spool ID {spool_id} not found, setting to None")
                    self.set_active_spool(None)
                return None, False, True

            err_msg = self._get_response_error(response)
            self._set_last_error(
                f"Failed to load spool metadata for spool id {spool_id}: {err_msg}"
            )
            return None, True, False

        density, diameter = self._resolve_material_values(spool_data)
        used_weight_g = self._length_to_weight_g(used_length_mm, density, diameter)
        return -used_weight_g, False, False

    async def _report_spool_usage(
        self, spool_id: int, used_length_mm: float
    ) -> Tuple[bool, bool]:
        delta_weight_g, should_retry, not_found = await self._build_delta_from_length(
            spool_id,
            used_length_mm,
        )
        if delta_weight_g is None:
            return False, should_retry and not not_found

        response = await self._request(
            method="POST",
            url=f"{self.api_url}/spools/{spool_id}/consumptions",
            body={"delta_weight_g": delta_weight_g},
            connect_timeout=2.0,
            request_timeout=5.0,
        )
        if response.has_error():
            if response.status_code == 404:
                if spool_id == self.spool_id:
                    logging.info(f"Spool ID {spool_id} not found, setting to None")
                    self.set_active_spool(None)
                return False, False

            err_msg = self._get_response_error(response)
            self._set_last_error(
                "Failed to update extrusion for spool id "
                f"{spool_id}, received {err_msg}"
            )
            return False, True

        self._mark_success()
        return True, False

    async def report_extrusion(self, eventtime: float) -> float:
        pending_reports = self.pending_reports
        self.pending_reports = {}

        for spool_id, used_length_mm in pending_reports.items():
            if used_length_mm <= 0:
                continue

            logging.debug(
                f"Sending spool usage: ID: {spool_id}, Length: {used_length_mm:.3f}mm"
            )
            success, should_retry = await self._report_spool_usage(
                spool_id, used_length_mm
            )
            if not success and should_retry:
                self._add_extrusion(spool_id, used_length_mm)

        return self.eventloop.get_loop_time() + self.sync_rate_seconds

    async def _handle_spool_id_request(self, web_request: WebRequest) -> Dict[str, Any]:
        if web_request.get_request_type() == RequestType.POST:
            spool_id = web_request.get_int("spool_id", None)
            self.set_active_spool(spool_id)
        return {"spool_id": self.spool_id}

    def _normalize_proxy_path(self, path: str) -> str:
        if path.startswith("/api/v1"):
            suffix = path[len("/api/v1"):]
        elif path.startswith("/v1"):
            suffix = path[len("/v1"):]
        elif path.startswith("/"):
            suffix = path
        else:
            raise self.server.error("Invalid path format. Path must start with '/'")

        if suffix == "":
            return ""

        if suffix == "/spool":
            return "/spools"
        if suffix.startswith("/spool/"):
            return "/spools/" + suffix[len("/spool/"):]
        if suffix == "/filament":
            return "/filaments"
        if suffix.startswith("/filament/"):
            return "/filaments/" + suffix[len("/filament/"):]

        return suffix

    def _is_allowed_proxy_request(self, method: str, path_suffix: str) -> bool:
        if method == "GET":
            return path_suffix.startswith("/spools") or path_suffix.startswith(
                "/filaments"
            )
        if method == "POST":
            return CONSUMPTION_PATH_RE.match(path_suffix) is not None
        return False

    async def _map_legacy_use_request(
        self,
        method: str,
        path_suffix: str,
        body: Any,
    ) -> Tuple[str, str, Any]:
        match = re.match(r"^/spools/(?P<spool_id>\d+)/use$", path_suffix)
        if match is None:
            return method, path_suffix, body

        if method not in {"PUT", "POST"}:
            raise self.server.error(
                "Invalid HTTP method for '/use', expected PUT or POST"
            )
        if not isinstance(body, dict):
            raise self.server.error("Legacy '/use' requests require a JSON body")
        if "use_length" not in body:
            raise self.server.error("Legacy '/use' body must include 'use_length'")

        try:
            use_length_mm = float(body["use_length"])
        except Exception:
            raise self.server.error("Legacy '/use' field 'use_length' must be numeric")

        if use_length_mm < 0:
            use_length_mm = abs(use_length_mm)

        spool_id = int(match.group("spool_id"))
        delta_weight_g, should_retry, _ = await self._build_delta_from_length(
            spool_id,
            use_length_mm,
        )
        if delta_weight_g is None:
            if should_retry:
                raise self.server.error(
                    "Unable to fetch spool metadata for use_length mapping"
                )
            raise self.server.error(f"Spool id {spool_id} was not found", 404)

        return (
            "POST",
            f"/spools/{spool_id}/consumptions",
            {"delta_weight_g": delta_weight_g},
        )

    async def _proxy_filaman_request(self, web_request: WebRequest) -> Dict[str, Any]:
        method = web_request.get_str("request_method").upper()
        path = web_request.get_str("path")
        query = web_request.get_str("query", None)
        body = web_request.get("body", None)
        use_v2_response = web_request.get_boolean("use_v2_response", False)

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise self.server.error(f"Invalid HTTP method: {method}")
        if body is not None and method == "GET":
            raise self.server.error("GET requests cannot have a body")

        path_suffix = self._normalize_proxy_path(path)
        method, path_suffix, body = await self._map_legacy_use_request(
            method,
            path_suffix,
            body,
        )
        if not self._is_allowed_proxy_request(method, path_suffix):
            raise self.server.error(
                f"Proxy request not permitted: {method} {path_suffix}", 403
            )

        normalized_query: Optional[str] = None
        if query is not None:
            normalized_query = query.lstrip("?").strip()
            if normalized_query == "":
                normalized_query = None

        query_suffix = f"?{normalized_query}" if normalized_query is not None else ""
        full_url = f"{self.api_url}{path_suffix}{query_suffix}"

        logging.debug(f"Proxying {method} request to {full_url}")
        response = await self._request(method=method, url=full_url, body=body)

        if not use_v2_response:
            response.raise_for_status()
            if not response.content:
                return {}
            return cast(Dict[str, Any], response.json())

        if response.has_error():
            msg: str = str(response.error or "")
            with contextlib.suppress(Exception):
                payload = cast(Dict[str, Any], response.json())
                detail = payload.get("detail")
                if isinstance(detail, dict) and isinstance(detail.get("message"), str):
                    msg = detail["message"]
                elif isinstance(payload.get("message"), str):
                    msg = payload["message"]
            return {
                "response": None,
                "error": {
                    "status_code": response.status_code,
                    "message": msg,
                },
            }

        data: Any = None
        with contextlib.suppress(Exception):
            data = response.json()
        return {
            "response": data,
            "response_headers": dict(response.headers.items()),
            "error": None,
        }

    async def _handle_status_request(self, web_request: WebRequest) -> Dict[str, Any]:
        pending: List[Dict[str, Any]] = [
            {
                "spool_id": sid,
                "filament_used": used_mm,
                "filament_used_mm": used_mm,
            }
            for sid, used_mm in self.pending_reports.items()
        ]
        return {
            "filaman_connected": self.api_connected,
            "spoolman_connected": self.api_connected,
            "pending_reports": pending,
            "pending_reports_count": len(pending),
            "spool_id": self.spool_id,
            "last_error": self._last_error,
            "last_success_at": self._last_success_at,
        }

    def _send_status_notification(self) -> None:
        payload = {
            "filaman_connected": self.api_connected,
            "spoolman_connected": self.api_connected,
        }
        self.server.send_event("filaman:filaman_status_changed", payload)
        self.server.send_event("spoolman:spoolman_status_changed", payload)

    async def close(self) -> None:
        self.is_closing = True
        self.report_timer.stop()
        self._cancel_spool_check_task()

        if self.connection_task is None or self.connection_task.done():
            return

        try:
            await asyncio.wait_for(self.connection_task, 2.0)
        except asyncio.TimeoutError:
            self.connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.connection_task


def load_component(config: ConfigHelper) -> FilaManManager:
    return FilaManManager(config)
