# SimplyPrint Connection Support
#
# Copyright (C) 2022  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import asyncio
import logging
import time
import pathlib
import base64
import tornado.websocket
from tornado.escape import url_escape
import logging.handlers
import tempfile
from queue import SimpleQueue
from ..loghelper import LocalQueueHandler
from ..common import APITransport, JobEvent, KlippyState, UserInfo
from ..utils import json_wrapper as jsonw

from typing import (
    TYPE_CHECKING,
    Awaitable,
    Optional,
    Dict,
    List,
    Union,
    Any,
    Callable,
)
if TYPE_CHECKING:
    from .application import InternalTransport
    from ..confighelper import ConfigHelper
    from .websockets import WebsocketManager
    from ..common import BaseRemoteConnection
    from tornado.websocket import WebSocketClientConnection
    from .database import MoonrakerDatabase
    from .klippy_apis import KlippyAPI
    from .job_state import JobState
    from .machine import Machine
    from .file_manager.file_manager import FileManager
    from .http_client import HttpClient
    from .power import PrinterPower
    from .announcements import Announcements
    from .webcam import WebcamManager, WebCam
    from .klippy_connection import KlippyConnection

COMPONENT_VERSION = "0.0.1"
SP_VERSION = "0.1"
TEST_ENDPOINT = f"wss://testws3.simplyprint.io/{SP_VERSION}/p"
PROD_ENDPOINT = f"wss://ws.simplyprint.io/{SP_VERSION}/p"
# TODO: Increase this time to something greater, perhaps 30 minutes
CONNECTION_ERROR_LOG_TIME = 60.
PRE_SETUP_EVENTS = [
    "connection", "state_change", "shutdown", "machine_data", "firmware",
    "ping"
]

class SimplyPrint(APITransport):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self._logger = ProtoLogger(config)
        self.eventloop = self.server.get_event_loop()
        self.job_state: JobState
        self.job_state = self.server.lookup_component("job_state")
        self.klippy_apis: KlippyAPI
        self.klippy_apis = self.server.lookup_component("klippy_apis")
        database: MoonrakerDatabase = self.server.lookup_component("database")
        database.register_local_namespace("simplyprint", forbidden=True)
        self.spdb = database.wrap_namespace("simplyprint")
        self.sp_info = self.spdb.as_dict()
        self.is_closing = False
        self.ws: Optional[WebSocketClientConnection] = None
        self.cache = ReportCache()
        ambient = self.sp_info.get("ambient_temp", INITIAL_AMBIENT)
        self.amb_detect = AmbientDetect(config, self, ambient)
        self.layer_detect = LayerDetect()
        self.webcam_stream = WebcamStream(config, self)
        self.print_handler = PrintHandler(self)
        self.last_received_temps: Dict[str, float] = {}
        self.last_err_log_time: float = 0.
        self.last_cpu_update_time: float = 0.
        self.intervals: Dict[str, float] = {
            "job": 1.,
            "temps": 1.,
            "temps_target": .25,
            "cpu": 10.,
            "ai": 0.,
            "ping": 20.,
        }
        self.printer_status: Dict[str, Dict[str, Any]] = {}
        self.heaters: Dict[str, str] = {}
        self.missed_job_events: List[Dict[str, Any]] = []
        self.announce_mutex = asyncio.Lock()
        self.connection_task: Optional[asyncio.Task] = None
        self.reconnect_delay: float = 1.
        self.reconnect_token: Optional[str] = None
        self._last_sp_ping: float = 0.
        self.ping_sp_timer = self.eventloop.register_timer(self._handle_sp_ping)
        self.printer_info_timer = self.eventloop.register_timer(
            self._handle_printer_info_update)
        self._print_request_event: asyncio.Event = asyncio.Event()
        self.next_temp_update_time: float = 0.
        self._last_ping_received: float = 0.
        self.gcode_terminal_enabled: bool = False
        self._current_job_id: Optional[int] = None
        self.connected = False
        self.is_set_up = False
        self.test = config.get("use_test_endpoint", False)
        connect_url = config.get("url", None)
        if connect_url is not None:
            self.connect_url = connect_url
            self.is_set_up = True
        else:
            self._set_ws_url()

        self.power_id: str = ""
        power_id: Optional[str] = config.get("power_device", None)
        if power_id is not None:
            self.power_id = power_id
            if self.power_id.startswith("power "):
                self.power_id = self.power_id[6:]
            if not config.has_section(f"power {self.power_id}"):
                self.power_id = ""
                self.server.add_warning(
                    "Section [simplyprint], option 'power_device': Unable "
                    f"to locate configuration for power device {power_id}"
                )
        else:
            power_pfx = config.get_prefix_sections("power ")
            if len(power_pfx) == 1:
                name = power_pfx[0][6:]
                if "printer" in name.lower():
                    self.power_id = name
        self.filament_sensor: str = ""
        fsensor = config.get("filament_sensor", None)
        fs_prefixes = ["filament_switch_sensor ", "filament_motion_sensor "]
        if fsensor is not None:
            for prefix in fs_prefixes:
                if fsensor.startswith(prefix):
                    self.filament_sensor = fsensor
                    break
            else:
                self.server.add_warning(
                    "Section [simplyprint], option 'filament_sensor': Invalid "
                    f"sensor '{fsensor}', must start with one the following "
                    f"prefixes: {fs_prefixes}"
                )

        # Register State Events
        self.server.register_event_handler(
            "server:klippy_started", self._on_klippy_startup)
        self.server.register_event_handler(
            "server:klippy_ready", self._on_klippy_ready)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._on_klippy_shutdown)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._on_klippy_disconnected)
        self.server.register_event_handler(
            "job_state:state_changed", self._on_job_state_changed)
        self.server.register_event_handler(
            "klippy_apis:pause_requested", self._on_pause_requested)
        self.server.register_event_handler(
            "klippy_apis:resume_requested", self._on_resume_requested)
        self.server.register_event_handler(
            "klippy_apis:cancel_requested", self._on_cancel_requested)
        self.server.register_event_handler(
            "proc_stats:proc_stat_update", self._on_proc_update)
        self.server.register_event_handler(
            "proc_stats:cpu_throttled", self._on_cpu_throttled
        )
        self.server.register_event_handler(
            "websockets:client_identified", self._on_websocket_identified)
        self.server.register_event_handler(
            "websockets:client_removed", self._on_websocket_removed)
        self.server.register_event_handler(
            "server:gcode_response", self._on_gcode_response)
        self.server.register_event_handler(
            "klippy_connection:gcode_received", self._on_gcode_received
        )
        self.server.register_event_handler(
            "power:power_changed", self._on_power_changed
        )

    async def component_init(self) -> None:
        await self.webcam_stream.intialize_url()
        await self.webcam_stream.test_connection()
        self.connection_task = self.eventloop.create_task(self._connect())

    async def _connect(self) -> None:
        log_connect = True
        while not self.is_closing:
            url = self.connect_url
            if self.reconnect_token is not None:
                url = f"{self.connect_url}/{self.reconnect_token}"
            if log_connect:
                logging.info(f"Connecting To SimplyPrint: {url}")
                log_connect = False
            try:
                self.ws = await tornado.websocket.websocket_connect(
                    url, connect_timeout=15.,
                )
                setattr(self.ws, "on_ping", self._on_ws_ping)
                cur_time = self.eventloop.get_loop_time()
                self._last_ping_received = cur_time
            except asyncio.CancelledError:
                raise
            except Exception:
                curtime = self.eventloop.get_loop_time()
                timediff = curtime - self.last_err_log_time
                if timediff > CONNECTION_ERROR_LOG_TIME:
                    self.last_err_log_time = curtime
                    logging.exception("Failed to connect to SimplyPrint")
            else:
                logging.info("Connected to SimplyPrint Cloud")
                await self._read_messages()
                log_connect = True
            if not self.is_closing:
                await asyncio.sleep(self.reconnect_delay)

    async def _read_messages(self) -> None:
        message: Union[str, bytes, None]
        while self.ws is not None:
            message = await self.ws.read_message()
            if isinstance(message, str):
                self._process_message(message)
            elif message is None:
                self.ping_sp_timer.stop()
                cur_time = self.eventloop.get_loop_time()
                ping_time: float = cur_time - self._last_ping_received
                reason = code = None
                if self.ws is not None:
                    reason = self.ws.close_reason
                    code = self.ws.close_code
                msg = (
                    f"SimplyPrint Disconnected - Code: {code}, "
                    f"Reason: {reason}, "
                    f"Server Ping Time Elapsed: {ping_time}"
                )
                logging.info(msg)
                self.connected = False
                self.ws = None
                break

    def _on_ws_ping(self, data: bytes = b"") -> None:
        self._last_ping_received = self.eventloop.get_loop_time()

    def _process_message(self, msg: str) -> None:
        self._logger.info(f"received: {msg}")
        try:
            packet: Dict[str, Any] = jsonw.loads(msg)
        except jsonw.JSONDecodeError:
            logging.debug(f"Invalid message, not JSON: {msg}")
            return
        event: str = packet.get("type", "")
        data: Optional[Dict[str, Any]] = packet.get("data")
        if event == "connected":
            logging.info("SimplyPrint Reports Connection Success")
            self.connected = True
            self.reconnect_token = None
            if data is not None:
                if data.get("in_setup", 0) == 1:
                    self.is_set_up = False
                    self.save_item("printer_id", None)
                    self.save_item("printer_name", None)
                    if "short_id" in data:
                        self.eventloop.create_task(
                            self._announce_setup(data["short_id"])
                        )
                interval = data.get("interval")
                if isinstance(interval, dict):
                    self._update_intervals(interval)
                self.reconnect_token = data.get("reconnect_token")
                name = data.get("name")
                if name is not None:
                    self.save_item("printer_name", name)
            self.reconnect_delay = 1.
            self._push_initial_state()
            self.ping_sp_timer.start()
        elif event == "error":
            logging.info(f"SimplyPrint Connection Error: {data}")
            self.reconnect_delay = 30.
            self.reconnect_token = None
        elif event == "new_token":
            if data is None:
                logging.debug("Invalid message, no data")
                return
            if data.get("no_exist", False) is True and self.is_set_up:
                self.is_set_up = False
                self.save_item("printer_id", None)
            token: Optional[str] = data.get("token")
            if not isinstance(token, str):
                logging.debug(f"Invalid token received: {token}")
                token = None
            else:
                logging.info("SimplyPrint Token Received")
            self.save_item("printer_token", token)
            self._set_ws_url()
            if "short_id" in data:
                short_id = data["short_id"]
                if not isinstance(short_id, str):
                    self._logger.debug(f"Invalid short_id received: {short_id}")
                else:
                    self.eventloop.create_task(
                        self._announce_setup(data["short_id"])
                    )
        elif event == "complete_setup":
            if data is None:
                logging.debug("Invalid message, no data")
                return
            printer_id = data.get("printer_id")
            if printer_id is None:
                logging.debug("Invalid printer id, received null (None) value")
            self.save_item("printer_id", str(printer_id))
            self._set_ws_url()
            self.save_item("temp_short_setup_id", None)
            self.eventloop.create_task(self._remove_setup_announcement())
        elif event == "demand":
            if data is None:
                logging.debug("Invalid message, no data")
                return
            demand = data.pop("demand", "unknown")
            self._process_demand(demand, data)
        elif event == "interval_change":
            if isinstance(data, dict):
                self._update_intervals(data)
        elif event == "pong":
            diff = self.eventloop.get_loop_time() - self._last_sp_ping
            self.send_sp("latency", {"ms": int(diff * 1000 + .5)})
        else:
            # TODO: It would be good for the backend to send an
            # event indicating that it is ready to recieve printer
            # status.
            logging.debug(f"Unknown event: {msg}")

    def _process_demand(self, demand: str, args: Dict[str, Any]) -> None:
        kconn: KlippyConnection
        kconn = self.server.lookup_component("klippy_connection")
        if demand in ["pause", "resume", "cancel"]:
            if not kconn.is_connected():
                return
            self.eventloop.create_task(self._request_print_action(demand))
        elif demand == "terminal":
            if "enabled" in args:
                self.gcode_terminal_enabled = args["enabled"]
        elif demand == "gcode":
            if not kconn.is_connected():
                return
            script_list: List[str] = args.get("list", [])
            ident: Optional[str] = args.get("identifier", None)
            if script_list:
                script = "\n".join(script_list)
                self.eventloop.create_task(self._handle_gcode_demand(script, ident))
        elif demand == "webcam_snapshot":
            self.eventloop.create_task(self.webcam_stream.post_image(args))
        elif demand == "file":
            url: Optional[str] = args.get("url")
            if not isinstance(url, str):
                logging.debug("Invalid url in message")
                return
            start = bool(args.get("auto_start", 0))
            # Keep track of current job
            # Consider whether to persists this across restarts
            self._current_job_id = int(args.get("job_id", 0)) or None
            self.print_handler.download_file(url, start)
        elif demand == "start_print":
            if (
                kconn.is_connected() and
                self.cache.state == "operational"
            ):
                self.eventloop.create_task(self.print_handler.start_print())
            else:
                logging.debug("Failed to start print")
        elif demand == "system_restart":
            coro = self._call_internal_api("machine.reboot")
            self.eventloop.create_task(coro)
        elif demand == "system_shutdown":
            coro = self._call_internal_api("machine.shutdown")
            self.eventloop.create_task(coro)
        elif demand == "api_restart":
            self.eventloop.create_task(self._do_service_action("restart"))
        elif demand == "api_shutdown":
            self.eventloop.create_task(self._do_service_action("shutdown"))
        elif demand == "psu_on":
            self._do_power_action("on")
        elif demand == "psu_off":
            self._do_power_action("off")
        elif demand == "test_webcam":
            self.eventloop.create_task(self._test_webcam())
        else:
            logging.debug(f"Unknown demand: {demand}")

    def save_item(self, name: str, data: Any):
        if data is None:
            self.sp_info.pop(name, None)
            self.spdb.pop(name, None)
        else:
            self.sp_info[name] = data
            self.spdb[name] = data

    async def _handle_gcode_demand(
        self, script: str, ident: Optional[str]
    ) -> None:
        success: bool = True
        msg: Optional[str] = None
        try:
            await self.klippy_apis.run_gcode(script)
        except self.server.error as e:
            msg = str(e)
            success = False
        if ident is not None:
            self.send_sp(
                "gcode_executed",
                {
                    "identifier": ident,
                    "success": success,
                    "message": msg
                }
            )

    async def _call_internal_api(self, method: str, **kwargs) -> Any:
        itransport: InternalTransport
        itransport = self.server.lookup_component("internal_transport")
        try:
            ret = await itransport.call_method(method, **kwargs)
        except self.server.error:
            return None
        return ret

    def _set_ws_url(self) -> None:
        token: Optional[str] = self.sp_info.get("printer_token")
        printer_id: Optional[str] = self.sp_info.get("printer_id")
        ep = TEST_ENDPOINT if self.test else PROD_ENDPOINT
        self.connect_url = f"{ep}/0/0"
        if token is not None:
            if printer_id is None:
                self.connect_url = f"{ep}/0/{token}"
            else:
                self.is_set_up = True
                self.connect_url = f"{ep}/{printer_id}/{token}"

    def _update_intervals(self, intervals: Dict[str, Any]) -> None:
        for key, val in intervals.items():
            self.intervals[key] = val / 1000.
        logging.debug(f"Intervals Updated: {self.intervals}")

    async def _announce_setup(self, short_id: str) -> None:
        async with self.announce_mutex:
            eid: Optional[str] = self.sp_info.get("announcement_id")
            if (
                eid is not None and
                self.sp_info.get("temp_short_setup_id") == short_id
            ):
                return
            ann: Announcements = self.server.lookup_component("announcements")
            if eid is not None:
                # remove stale announcement
                try:
                    await ann.remove_announcement(eid)
                except self.server.error:
                    pass

            self.save_item("temp_short_setup_id", short_id)
            entry = ann.add_internal_announcement(
                "SimplyPrint Setup Request",
                "SimplyPrint is ready to complete setup for your printer. "
                "Please log in to your account and enter the following "
                f"setup code:\n\n{short_id}\n\n",
                "https://simplyprint.io", "high", "simplyprint"
            )
            eid = entry.get("entry_id")
            self.save_item("announcement_id", eid)

    async def _remove_setup_announcement(self) -> None:
        async with self.announce_mutex:
            eid = self.sp_info.get("announcement_id")
            if eid is None:
                return
            self.save_item("announcement_id", None)
            ann: Announcements = self.server.lookup_component("announcements")
            try:
                await ann.remove_announcement(eid)
            except self.server.error:
                pass

    def _do_power_action(self, state: str) -> None:
        if self.power_id:
            power: PrinterPower = self.server.lookup_component("power")
            power.set_device_power(self.power_id, state)

    async def _do_service_action(self, action: str) -> None:
        try:
            machine: Machine = self.server.lookup_component("machine")
            await machine.do_service_action(action, "moonraker")
        except self.server.error:
            pass

    async def _request_print_action(self, action: str) -> None:
        cur_state = self.cache.state
        ret: Optional[str] = ""
        self._print_request_event.clear()
        if action == "pause":
            if cur_state == "printing":
                self._update_state("pausing")
                ret = await self.klippy_apis.pause_print(None)
        elif action == "resume":
            if cur_state == "paused":
                self._print_request_fut = self.eventloop.create_future()
                self._update_state("resuming")
                ret = await self.klippy_apis.resume_print(None)
        elif action == "cancel":
            if cur_state in ["printing", "paused"]:
                self._update_state("cancelling")
                ret = await self.klippy_apis.cancel_print(None)
        if ret is None:
            # Wait for the "action" requested event to fire, then reset the
            # state
            try:
                await asyncio.wait_for(self._print_request_event.wait(), 1.)
            except Exception:
                pass
            self._update_state_from_klippy()

    async def _test_webcam(self) -> None:
        await self.webcam_stream.test_connection()
        self.send_sp(
            "webcam_status", {"connected": self.webcam_stream.connected}
        )

    async def _on_klippy_ready(self) -> None:
        last_stats: Dict[str, Any] = self.job_state.get_last_stats()
        if last_stats["state"] == "printing":
            self._on_print_started(last_stats, last_stats, False)
        else:
            self._update_state("operational")
        query: Optional[Dict[str, Any]]
        query = await self.klippy_apis.query_objects({"heaters": None}, None)
        sub_objs = {
            "display_status": ["progress"],
            "virtual_sdcard": ["file_position", "progress"],
            "bed_mesh": ["mesh_matrix", "mesh_min", "mesh_max"],
            "toolhead": ["extruder"],
            "gcode_move": ["gcode_position"],
            "exclude_object": ["objects", "excluded_objects", "current_object"],
        }
        # Add Heater Subscriptions
        has_amb_sensor: bool = False
        cfg_amb_sensor = self.amb_detect.sensor_name
        if query is not None:
            heaters: Dict[str, Any] = query.get("heaters", {})
            avail_htrs: List[str]
            avail_htrs = sorted(heaters.get("available_heaters", []))
            logging.debug(f"SimplyPrint: Heaters Detected: {avail_htrs}")
            for htr in avail_htrs:
                if htr.startswith("extruder"):
                    sub_objs[htr] = ["temperature", "target"]
                    if htr == "extruder":
                        tool_id = "tool0"
                    else:
                        tool_id = "tool" + htr[8:]
                    self.heaters[htr] = tool_id
                elif htr == "heater_bed":
                    sub_objs[htr] = ["temperature", "target"]
                    self.heaters[htr] = "bed"
            sensors: List[str] = heaters.get("available_sensors", [])
            if cfg_amb_sensor:
                if cfg_amb_sensor in sensors:
                    has_amb_sensor = True
                    sub_objs[cfg_amb_sensor] = ["temperature"]
                else:
                    logging.info(
                        f"SimplyPrint: Ambient sensor {cfg_amb_sensor} not "
                        "configured in Klipper"
                    )
        if self.filament_sensor:
            objects: List[str]
            objects = await self.klippy_apis.get_object_list(default=[])
            if self.filament_sensor in objects:
                sub_objs[self.filament_sensor] = ["filament_detected"]
            # Add filament sensor subscription
        if not sub_objs:
            return
        # Create our own subscription rather than use the host sub
        status: Dict[str, Any] = await self.klippy_apis.subscribe_from_transport(
            sub_objs, self, default={}
        )
        if status:
            logging.debug(f"SimplyPrint: Got Initial Status: {status}")
            self.printer_status = status
            self._update_temps(1.)
            self.next_temp_update_time = 0.
            if "bed_mesh" in status:
                self._send_mesh_data()
            if "toolhead" in status:
                self._send_active_extruder(status["toolhead"]["extruder"])
            if "gcode_move" in status:
                self.layer_detect.update(
                    status["gcode_move"]["gcode_position"]
                )
            if "exclude_object" in status:
                self._handle_exclude_object(status["exclude_object"])
            if self.filament_sensor and self.filament_sensor in status:
                detected = status[self.filament_sensor]["filament_detected"]
                fstate = "loaded" if detected else "runout"
                self.cache.filament_state = fstate
                self.send_sp("filament_sensor", {"state": fstate})
            if has_amb_sensor and cfg_amb_sensor in status:
                self.amb_detect.update_ambient(status[cfg_amb_sensor])
        if not has_amb_sensor:
            self.amb_detect.start()
        self.printer_info_timer.start(delay=1.)

    def _on_power_changed(self, device_info: Dict[str, Any]) -> None:
        if self.power_id and device_info["device"] == self.power_id:
            is_on = device_info["status"] == "on"
            self.send_sp("power_controller", {"on": is_on})

    def _on_websocket_identified(self, ws: BaseRemoteConnection) -> None:
        if (
            self.cache.current_wsid is None and
            ws.client_data.get("type", "") == "web"
        ):
            ui_data: Dict[str, Any] = {
                "ui": ws.client_data["name"],
                "ui_version": ws.client_data["version"]
            }
            self.cache.firmware_info.update(ui_data)
            self.cache.current_wsid = ws.uid
            self.send_sp("machine_data", ui_data)

    def _on_websocket_removed(self, ws: BaseRemoteConnection) -> None:
        if self.cache.current_wsid is None or self.cache.current_wsid != ws.uid:
            return
        ui_data = self._get_ui_info()
        diff = self._get_object_diff(ui_data, self.cache.firmware_info)
        if diff:
            self.cache.firmware_info.update(ui_data)
            self.send_sp("machine_data", ui_data)

    def _on_klippy_startup(self, state: KlippyState) -> None:
        if state != KlippyState.READY:
            self._update_state("error")
            kconn: KlippyConnection
            kconn = self.server.lookup_component("klippy_connection")
            self.send_sp("printer_error", {"error": kconn.state.message})
        self.send_sp("connection", {"new": "connected"})
        self._send_firmware_data()

    def _on_klippy_shutdown(self) -> None:
        self._update_state("error")
        kconn: KlippyConnection
        kconn = self.server.lookup_component("klippy_connection")
        self.send_sp("printer_error", {"error": kconn.state.message})

    def _on_klippy_disconnected(self) -> None:
        self._update_state("offline")
        self.send_sp("connection", {"new": "disconnected"})
        self.amb_detect.stop()
        self.printer_info_timer.stop()
        self.cache.reset_print_state()
        self.printer_status = {}

    def _on_job_state_changed(self, job_event: JobEvent, *args) -> None:
        callback: Optional[Callable] = getattr(self, f"_on_print_{job_event}", None)
        if callback is not None:
            callback(*args)
        else:
            logging.info(f"No defined callback for Job Event: {job_event}")

    def _on_print_started(
        self,
        prev_stats: Dict[str, Any],
        new_stats: Dict[str, Any],
        need_start_event: bool = True
    ) -> None:
        # includes started and resumed events
        self._update_state("printing")
        filename = new_stats["filename"]
        job_info: Dict[str, Any] = {"filename": filename}
        fm: FileManager = self.server.lookup_component("file_manager")
        metadata = fm.get_file_metadata(filename)
        filament: Optional[float] = metadata.get("filament_total")
        if filament is not None:
            job_info["filament"] = round(filament)
        est_time = metadata.get("estimated_time")
        if est_time is not None:
            job_info["time"] = est_time
        self.cache.metadata = metadata
        self.cache.job_info.update(job_info)
        if need_start_event:
            job_info["started"] = True
        self.layer_detect.start(metadata)
        self._send_job_event(job_info)

    def _check_job_started(
        self,
        prev_stats: Dict[str, Any],
        new_stats: Dict[str, Any]
    ) -> None:
        if not self.cache.job_info:
            job_info: Dict[str, Any] = {
                "filename": new_stats.get("filename", ""),
                "started": True
            }
            self._send_job_event(job_info)

    def _reset_file(self) -> None:
        cur_job = self.cache.job_info.get("filename", "")
        last_started = self.print_handler.last_started
        if last_started and last_started == cur_job:
            kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
            self.eventloop.create_task(
                kapi.run_gcode("SDCARD_RESET_FILE", default=None)
            )
        self.print_handler.last_started = ""

    def _on_print_paused(self, *args) -> None:
        self.send_sp("job_info", {"paused": True})
        self._update_state("paused")
        self.layer_detect.stop()

    def _on_print_resumed(self, *args) -> None:
        self._update_state("printing")
        self.layer_detect.resume()

    def _on_print_cancelled(self, *args) -> None:
        self._check_job_started(*args)
        self._reset_file()
        self._send_job_event({"cancelled": True})
        self._update_state_from_klippy()
        self.cache.job_info = {}
        self.layer_detect.stop()

    def _on_print_error(self, *args) -> None:
        self._check_job_started(*args)
        self._reset_file()
        payload: Dict[str, Any] = {"failed": True}
        new_stats: Dict[str, Any] = args[1]
        msg = new_stats.get("message", "Unknown Error")
        payload["error"] = msg
        self._send_job_event(payload)
        self._update_state_from_klippy()
        self.cache.job_info = {}
        self.layer_detect.stop()

    def _on_print_complete(self, *args) -> None:
        self._check_job_started(*args)
        self._reset_file()
        self._send_job_event({"finished": True})
        self._update_state_from_klippy()
        self.cache.job_info = {}
        self.layer_detect.stop()

    def _on_print_standby(self, *args) -> None:
        self._update_state_from_klippy()
        self.cache.job_info = {}
        self.layer_detect.stop()

    def _on_pause_requested(self) -> None:
        self._print_request_event.set()
        if self.cache.state == "printing":
            self._update_state("pausing")

    def _on_resume_requested(self) -> None:
        self._print_request_event.set()
        if self.cache.state == "paused":
            self._update_state("resuming")

    def _on_cancel_requested(self) -> None:
        self._print_request_event.set()
        if self.cache.state in ["printing", "paused", "pausing"]:
            self._update_state("cancelling")

    def _on_gcode_response(self, response: str):
        if self.gcode_terminal_enabled:
            resp = [
                r.strip() for r in response.strip().split("\n") if r.strip()
            ]
            self.send_sp("term_update", {"response": resp})

    def _on_gcode_received(self, script: str):
        if self.gcode_terminal_enabled:
            cmds = [s.strip() for s in script.strip().split() if s.strip()]
            self.send_sp("term_update", {"command": cmds})

    def _on_proc_update(self, proc_stats: Dict[str, Any]) -> None:
        cpu = proc_stats["system_cpu_usage"]
        if not cpu:
            return
        curtime = self.eventloop.get_loop_time()
        if curtime - self.last_cpu_update_time < self.intervals["cpu"]:
            return
        self.last_cpu_update_time = curtime
        sys_mem = proc_stats["system_memory"]
        mem_pct: float = 0.
        if sys_mem:
            mem_pct = sys_mem["used"] / sys_mem["total"] * 100
        cpu_data = {
            "usage": int(cpu["cpu"] + .5),
            "memory": int(mem_pct + .5),
            "flags": self.cache.throttled_state.get("bits", 0)
        }
        temp: Optional[float] = proc_stats["cpu_temp"]
        if temp is not None:
            cpu_data["temp"] = int(temp + .5)
        diff = self._get_object_diff(cpu_data, self.cache.cpu_info)
        if diff:
            self.cache.cpu_info.update(cpu_data)
            self.send_sp("cpu", diff)

    def _on_cpu_throttled(self, throttled_state: Dict[str, Any]):
        self.cache.throttled_state = throttled_state

    def send_status(self, status: Dict[str, Any], eventtime: float) -> None:
        for printer_obj, vals in status.items():
            self.printer_status[printer_obj].update(vals)
        if self.amb_detect.sensor_name in status:
            self.amb_detect.update_ambient(
                status[self.amb_detect.sensor_name], eventtime
            )
        self._update_temps(eventtime)
        if "bed_mesh" in status:
            self._send_mesh_data()
        if "toolhead" in status and "extruder" in status["toolhead"]:
            self._send_active_extruder(status["toolhead"]["extruder"])
        if "gcode_move" in status:
            self.layer_detect.update(status["gcode_move"]["gcode_position"])
        if "exclude_object" in status:
            self._handle_exclude_object(status["exclude_object"])
        if self.filament_sensor and self.filament_sensor in status:
            detected = status[self.filament_sensor]["filament_detected"]
            fstate = "loaded" if detected else "runout"
            self.cache.filament_state = fstate
            self.send_sp("filament_sensor", {"state": fstate})

    def _handle_printer_info_update(self, eventtime: float) -> float:
        # Job Info Timer handler
        if self.cache.state == "printing":
            self._update_job_progress()
        return eventtime + self.intervals["job"]

    def _handle_sp_ping(self, eventtime: float) -> float:
        self._last_sp_ping = eventtime
        self.send_sp("ping", None)
        return eventtime + self.intervals["ping"]

    def _handle_exclude_object(self, exclude_obj=None) -> None:
        exclude_obj = exclude_obj or self.printer_status.get("exclude_object", {})
        diff = self._get_object_diff(exclude_obj, self.cache.exclude_object_status)
        if not diff:
            return
        self.cache.exclude_object_status = dict(exclude_obj)
        objects = diff.get("objects", [])
        if objects:
            self.send_sp("objects", objects)
        job_data = {}
        current_object = diff.get("current_object")
        if "current_object" in diff:
            job_data.update({"object": current_object})
        skipped_objects = diff.get("excluded_objects", [])
        if skipped_objects:
            job_data.update({"skipped_objects": skipped_objects})
        if job_data:
            self.send_sp("job_info", job_data)


    def _update_job_progress(self) -> None:
        job_info: Dict[str, Any] = {}
        est_time = self.cache.metadata.get("estimated_time")
        last_stats: Dict[str, Any] = self.job_state.get_last_stats()
        if est_time is not None:
            duration: float = last_stats["print_duration"]
            time_left = max(0, int(est_time - duration + .5))
            last_time_left = self.cache.job_info.get("time", time_left + 60.)
            time_diff = last_time_left - time_left
            if (
                (time_left < 60 or time_diff >= 30) and
                time_left != last_time_left
            ):
                job_info["time"] = time_left

        progress = None

        if "display_status" in self.printer_status:
            if "progress" in self.printer_status["display_status"]:
                if self.printer_status["display_status"]["progress"]:
                    progress = self.printer_status["display_status"]["progress"]

        if not progress and "virtual_sdcard" in self.printer_status:
            if "file_position" in self.printer_status["virtual_sdcard"]:
                file_position = self.printer_status["virtual_sdcard"]["file_position"]
                gcode_start_byte = self.cache.metadata.get('gcode_start_byte', 0)
                gcode_end_byte = self.cache.metadata.get('gcode_end_byte', 0)
                gcode_length = gcode_end_byte - gcode_start_byte
                if gcode_start_byte and gcode_end_byte and gcode_length > 0:
                    progress = (file_position - gcode_start_byte) / gcode_length
                    progress = max(0, min(progress, 1))
            if not progress and "progress" in self.printer_status["virtual_sdcard"]:
                progress = self.printer_status["virtual_sdcard"]["progress"]

        if progress:
            pct_prog = int(progress * 100 + .5)
            if pct_prog != self.cache.job_info.get("progress", 0):
                job_info["progress"] = pct_prog

        layer: Optional[int] = last_stats.get("info", {}).get("current_layer")
        if layer is None:
            layer = self.layer_detect.layer
        if layer != self.cache.job_info.get("layer", -1):
            job_info["layer"] = layer
        if job_info:
            self.cache.job_info.update(job_info)
            self.send_sp("job_info", job_info)

    def _update_temps(self, eventtime: float) -> None:
        if eventtime < self.next_temp_update_time:
            return
        need_rapid_update: bool = False
        temp_data: Dict[str, List[int]] = {}
        for printer_obj, key in self.heaters.items():
            reported_temp = self.printer_status[printer_obj]["temperature"]
            ret = [
                int(reported_temp + .5),
                int(self.printer_status[printer_obj]["target"] + .5)
            ]
            last_temps = self.cache.temps.get(key, [-100., -100.])
            if ret[1] == last_temps[1]:
                if ret[1]:
                    seeking_target = abs(ret[1] - ret[0]) > 5
                else:
                    seeking_target = ret[0] >= self.amb_detect.ambient + 25
                need_rapid_update |= seeking_target
                # The target hasn't changed and not heating, debounce temp
                if key in self.last_received_temps and not seeking_target:
                    last_reported = self.last_received_temps[key]
                    if abs(reported_temp - last_reported) < .75:
                        self.last_received_temps.pop(key)
                        continue
                if ret[0] == last_temps[0]:
                    self.last_received_temps[key] = reported_temp
                    continue
                temp_data[key] = ret[:1]
            else:
                # target has changed, send full data
                temp_data[key] = ret
            self.last_received_temps[key] = reported_temp
            self.cache.temps[key] = ret
        if need_rapid_update:
            self.next_temp_update_time = (
                0. if self.intervals["temps_target"] < .2501 else
                eventtime + self.intervals["temps_target"]
            )
        else:
            self.next_temp_update_time = eventtime + self.intervals["temps"]
        if not temp_data:
            return
        self.send_sp("temps", temp_data)

    def _update_state_from_klippy(self) -> None:
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        klippy_state = kconn.state
        if klippy_state == KlippyState.READY:
            sp_state = "operational"
        elif klippy_state in [KlippyState.ERROR, KlippyState.SHUTDOWN]:
            sp_state = "error"
        else:
            sp_state = "offline"
        self._update_state(sp_state)

    def _update_state(self, new_state: str) -> None:
        if self.cache.state == new_state:
            return
        self.cache.state = new_state
        self.send_sp("state_change", {"new": new_state})
        if new_state == "operational":
            self.print_handler.notify_ready()

    def _send_mesh_data(self) -> None:
        mesh = self.printer_status["bed_mesh"]
        # TODO: We are probably going to have to reformat the mesh
        self.cache.mesh = mesh
        self.send_sp("mesh_data", mesh)

    def _send_job_event(self, job_info: Dict[str, Any]) -> None:
        if self.connected:
            self.send_sp("job_info", job_info)
        else:
            job_info.update(self.cache.job_info)
            job_info["delay"] = self.eventloop.get_loop_time()
            self.missed_job_events.append(job_info)
            if len(self.missed_job_events) > 10:
                self.missed_job_events.pop(0)

    def _get_ui_info(self) -> Dict[str, Any]:
        ui_data: Dict[str, Any] = {"ui": None, "ui_version": None}
        self.cache.current_wsid = None
        websockets: WebsocketManager
        websockets = self.server.lookup_component("websockets")
        conns = websockets.get_clients_by_type("web")
        if conns:
            longest = conns[0]
            ui_data["ui"] = longest.client_data["name"]
            ui_data["ui_version"] = longest.client_data["version"]
            self.cache.current_wsid = longest.uid
        return ui_data

    async def _send_machine_data(self) -> None:
        app_args = self.server.get_app_args()
        data = self._get_ui_info()
        data["api"] = "Moonraker"
        data["api_version"] = app_args["software_version"]
        data["sp_version"] = COMPONENT_VERSION
        machine: Machine = self.server.lookup_component("machine")
        sys_info = machine.get_system_info()
        pyver = sys_info["python"]["version"][:3]
        data["python_version"] = ".".join([str(part) for part in pyver])
        model: str = sys_info["cpu_info"].get("model", "")
        if not model or model.isdigit():
            model = sys_info["cpu_info"].get("cpu_desc", "Unknown")
        data["machine"] = model
        data["os"] = sys_info["distribution"].get("name", "Unknown")
        pub_intf = await machine.get_public_network()
        data["is_ethernet"] = int(not pub_intf["is_wifi"])
        data["ssid"] = pub_intf.get("ssid", "")
        data["local_ip"] = pub_intf.get("address", "Unknown")
        data["hostname"] = pub_intf["hostname"]
        data["core_count"] = os.cpu_count()
        mem = sys_info["cpu_info"]["total_memory"]
        if mem is not None:
            data["total_memory"] = mem * 1024
        self.cache.machine_info = data
        self.send_sp("machine_data", data)

    def _send_firmware_data(self) -> None:
        kinfo = self.server.get_klippy_info()
        if "software_version" not in kinfo:
            return
        firmware_date: str = ""
        # Approximate the firmware "date" using the last modified
        # time of the Klippy source folder
        kpath = pathlib.Path(kinfo["klipper_path"]).joinpath("klippy")
        if kpath.is_dir():
            mtime = kpath.stat().st_mtime
            firmware_date = time.asctime(time.gmtime(mtime))
        version: str = kinfo["software_version"]
        unsafe = version.endswith("-dirty") or version == "?"
        if unsafe:
            version = version.rsplit("-", 1)[0]
        fw_info = {
            "firmware": "Klipper",
            "firmware_version": version,
            "firmware_date": firmware_date,
            "firmware_link": "https://github.com/Klipper3d/klipper",
        }
        diff = self._get_object_diff(fw_info, self.cache.firmware_info)
        if diff:
            self.cache.firmware_info = fw_info
            self.send_sp(
                "firmware", {"fw": diff, "raw": False, "unsafe": unsafe}
            )

    def _send_active_extruder(self, new_extruder: str):
        tool = "T0" if new_extruder == "extruder" else f"T{new_extruder[8:]}"
        if tool == self.cache.active_extruder:
            return
        self.cache.active_extruder = tool
        self.send_sp("tool", {"new": tool})

    async def _send_webcam_config(self) -> None:
        wc_cfg = await self.webcam_stream.get_webcam_config()
        wc_data = {
            "flipH": wc_cfg.get("flip_horizontal", False),
            "flipV": wc_cfg.get("flip_vertical", False),
            "rotate90": wc_cfg.get("rotation", 0) == 90
        }
        self.send_sp("webcam", wc_data)

    async def _send_power_state(self) -> None:
        dev_info: Optional[Dict[str, Any]]
        dev_info = await self._call_internal_api(
            "machine.device_power.get_device", device=self.power_id
        )
        if dev_info is not None:
            is_on = dev_info[self.power_id] == "on"
            self.send_sp("power_controller", {"on": is_on})

    def _push_initial_state(self):
        self.send_sp("state_change", {"new": self.cache.state})
        if self.cache.temps:
            self.send_sp("temps", self.cache.temps)
        if self.cache.firmware_info:
            self.send_sp(
                "firmware",
                {"fw": self.cache.firmware_info, "raw": False})
        curtime = self.eventloop.get_loop_time()
        for evt in self.missed_job_events:
            evt["delay"] = int((curtime - evt["delay"]) + .5)
            self.send_sp("job_info", evt)
        self.missed_job_events = []
        if self.cache.active_extruder:
            self.send_sp("tool", {"new": self.cache.active_extruder})
        if self.cache.cpu_info:
            self.send_sp("cpu_info", self.cache.cpu_info)
        self.send_sp("ambient", {"new": self.amb_detect.ambient})
        if self.power_id:
            self.eventloop.create_task(self._send_power_state())
        if self.cache.filament_state:
            self.send_sp(
                "filament_sensor", {"state": self.cache.filament_state}
            )
        self.send_sp(
            "webcam_status", {"connected": self.webcam_stream.connected}
        )
        self.eventloop.create_task(self._send_machine_data())
        self.eventloop.create_task(self._send_webcam_config())

    def _check_setup_event(self, evt_name: str) -> bool:
        return self.is_set_up or evt_name in PRE_SETUP_EVENTS

    def send_sp(self, evt_name: str, data: Any) -> Awaitable[bool]:
        if (
            not self.connected or
            self.ws is None or
            self.ws.protocol is None or
            not self._check_setup_event(evt_name)
        ):
            fut = self.eventloop.create_future()
            fut.set_result(False)
            return fut
        # Include tracked job_id with some message types.
        if (
            evt_name in ("file_progress", "job_info")
            and self._current_job_id is not None
            and isinstance(data, dict)
        ):
            data.update({"job_id": self._current_job_id})
        packet = {"type": evt_name, "data": data}
        return self.eventloop.create_task(self._send_wrapper(packet))

    async def _send_wrapper(self, packet: Dict[str, Any]) -> bool:
        try:
            assert self.ws is not None
            await self.ws.write_message(jsonw.dumps(packet))
        except Exception:
            return False
        else:
            if packet["type"] != "stream":
                self._logger.info(f"sent: {packet}")
            else:
                self._logger.info("sent: webcam stream")
        return True

    def _get_object_diff(
        self, new_obj: Dict[str, Any], cached_obj: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not cached_obj:
            return new_obj
        diff: Dict[str, Any] = {}
        for key, val in new_obj.items():
            if key in cached_obj and val == cached_obj[key]:
                continue
            diff[key] = val
        return diff

    async def close(self):
        self.print_handler.cancel()
        self.amb_detect.stop()
        self.printer_info_timer.stop()
        self.ping_sp_timer.stop()
        await self.send_sp("shutdown", None)
        self._logger.close()
        self.is_closing = True
        if self.ws is not None:
            self.ws.close(1001, "Client Shutdown")
        if (
            self.connection_task is not None and
            not self.connection_task.done()
        ):
            try:
                await asyncio.wait_for(self.connection_task, 2.)
            except asyncio.TimeoutError:
                pass

class ReportCache:
    def __init__(self) -> None:
        self.state = "offline"
        self.temps: Dict[str, Any] = {}
        self.metadata: Dict[str, Any] = {}
        self.mesh: Dict[str, Any] = {}
        self.job_info: Dict[str, Any] = {}
        self.exclude_object_status: Dict[str, Any] = {}
        self.active_extruder: str = ""
        # Persistent state across connections
        self.firmware_info: Dict[str, Any] = {}
        self.machine_info: Dict[str, Any] = {}
        self.cpu_info: Dict[str, Any] = {}
        self.throttled_state: Dict[str, Any] = {}
        self.current_wsid: Optional[int] = None
        self.filament_state: str = ""

    def reset_print_state(self) -> None:
        self.temps = {}
        self.mesh = {}
        self.job_info = {}
        self.exclude_object_status = {}


INITIAL_AMBIENT = 85
AMBIENT_CHECK_TIME = 5. * 60.
TARGET_CHECK_TIME = 60. * 60.
SAMPLE_CHECK_TIME = 20.

class AmbientDetect:
    CHECK_INTERVAL = 5
    def __init__(
        self,
        config: ConfigHelper,
        simplyprint: SimplyPrint,
        initial_ambient: int
    ) -> None:
        self.server = config.get_server()
        self.simplyprint = simplyprint
        self.cache = simplyprint.cache
        self._initial_sample: int = -1000
        self._ambient = initial_ambient
        self._last_sample_time: float = 0.
        self._update_interval = AMBIENT_CHECK_TIME
        self.eventloop = self.server.get_event_loop()
        self._detect_timer = self.eventloop.register_timer(
            self._handle_detect_timer
        )
        self._sensor_name: str = config.get("ambient_sensor", "")

    @property
    def ambient(self) -> int:
        return self._ambient

    @property
    def sensor_name(self) -> str:
        return self._sensor_name

    def update_ambient(
        self, sensor_info: Dict[str, Any], eventtime: float = SAMPLE_CHECK_TIME
    ) -> None:
        if "temperature" not in sensor_info:
            return
        if eventtime < self._last_sample_time + SAMPLE_CHECK_TIME:
            return
        self._last_sample_time = eventtime
        new_amb = int(sensor_info["temperature"] + .5)
        if abs(new_amb - self._ambient) < 2:
            return
        self._ambient = new_amb
        self._on_ambient_changed(self._ambient)

    def _handle_detect_timer(self, eventtime: float) -> float:
        if "tool0" not in self.cache.temps:
            self._initial_sample = -1000
            return eventtime + self.CHECK_INTERVAL
        temp, target = self.cache.temps["tool0"]
        if target:
            self._initial_sample = -1000
            self._last_sample_time = eventtime
            self._update_interval = TARGET_CHECK_TIME
            return eventtime + self.CHECK_INTERVAL
        if eventtime - self._last_sample_time < self._update_interval:
            return eventtime + self.CHECK_INTERVAL
        if self._initial_sample == -1000:
            self._initial_sample = temp
            self._update_interval = SAMPLE_CHECK_TIME
        else:
            diff = abs(temp - self._initial_sample)
            if diff <= 2:
                last_ambient = self._ambient
                self._ambient = int((temp + self._initial_sample) / 2 + .5)
                self._initial_sample = -1000
                self._last_sample_time = eventtime
                self._update_interval = AMBIENT_CHECK_TIME
                if last_ambient != self._ambient:
                    logging.debug(f"SimplyPrint: New Ambient: {self._ambient}")
                    self._on_ambient_changed(self._ambient)
            else:
                self._initial_sample = temp
                self._update_interval = SAMPLE_CHECK_TIME
        return eventtime + self.CHECK_INTERVAL

    def _on_ambient_changed(self, new_ambient: int) -> None:
        self.simplyprint.save_item("ambient_temp", new_ambient)
        self.simplyprint.send_sp("ambient", {"new": new_ambient})

    def start(self) -> None:
        if self._detect_timer.is_running():
            return
        if "tool0" in self.cache.temps:
            cur_temp = self.cache.temps["tool0"][0]
            if cur_temp < self._ambient:
                self._ambient = cur_temp
                self._on_ambient_changed(self._ambient)
        self._detect_timer.start()

    def stop(self) -> None:
        self._detect_timer.stop()
        self._last_sample_time = 0.

class LayerDetect:
    def __init__(self) -> None:
        self._layer: int = 0
        self._layer_z: float = 0.
        self._active: bool = False
        self._layer_height: float = 0.
        self._fl_height: float = 0.
        self._layer_count: int = 99999999999
        self._check_next: bool = False

    @property
    def layer(self) -> int:
        return self._layer

    def update(self, new_pos: List[float]) -> None:
        if (
            not self._active or
            self._layer_z == new_pos[2] or
            self._layer_height == 0
        ):
            self._check_next = False
            return
        if not self._check_next:
            # Try to avoid z-hops by skipping the first detected change
            self._check_next = True
            return
        self._check_next = False
        layer = 1 + int(
            (new_pos[2] - self._fl_height) / self._layer_height + .5
        )
        self._layer = min(layer, self._layer_count)
        self._layer_z = new_pos[2]

    def start(self, metadata: Dict[str, Any]) -> None:
        self.reset()
        lh: float = metadata.get("layer_height", 0)
        flh: float = metadata.get("first_layer_height", lh)
        if lh > 0.000001 and flh > 0.000001:
            self._active = True
            self._layer_height = lh
            self._fl_height = flh
            layer_count: Optional[int] = metadata.get("layer_count")
            obj_height: Optional[float] = metadata.get("object_height")
            if layer_count is not None:
                self._layer_count = layer_count
            elif obj_height is not None:
                self._layer_count = int((obj_height - flh) / lh + .5)

    def resume(self) -> None:
        if self._layer_height > 0.:
            self._active = True

    def stop(self) -> None:
        self._active = False

    def reset(self) -> None:
        self._active = False
        self._layer = 0
        self._layer_z = 0.
        self._layer_height = 0.
        self._fl_height = 0.
        self._layer_count = 99999999999
        self._check_next = False


# TODO: We need to get the URL/Port from settings in the future.
# Ideally we will always fetch from the localhost rather than
# go through the reverse proxy
FALLBACK_URL = "http://127.0.0.1:8080/?action=snapshot"
SP_SNAPSHOT_URL = "https://api.simplyprint.io/jobs/ReceiveSnapshot"

class WebcamStream:
    def __init__(
        self, config: ConfigHelper, simplyprint: SimplyPrint
    ) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.simplyprint = simplyprint
        self.webcam_name = config.get("webcam_name", "")
        self.url = FALLBACK_URL
        self.client: HttpClient = self.server.lookup_component("http_client")
        self.cam: Optional[WebCam] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def intialize_url(self) -> None:
        wcmgr: WebcamManager = self.server.lookup_component("webcam")
        cams = wcmgr.get_webcams()
        if not cams:
            # no camera configured, try the fallback url
            return
        if self.webcam_name and self.webcam_name in cams:
            cam = cams[self.webcam_name]
        else:
            cam = list(cams.values())[0]
        try:
            url = await cam.get_snapshot_url(True)
        except Exception:
            logging.exception("Failed to retrieve webcam url")
            return
        self.cam = cam
        logging.info(f"SimplyPrint Webcam URL assigned: {url}")
        self.url = url

    async def test_connection(self):
        if not self.url.startswith("http"):
            self._connected = False
            return
        headers = {"Accept": "image/jpeg"}
        resp = await self.client.get(self.url, headers, enable_cache=False)
        self._connected = not resp.has_error()

    async def get_webcam_config(self) -> Dict[str, Any]:
        if self.cam is None:
            return {}
        return self.cam.as_dict()

    async def extract_image(self) -> str:
        headers = {"Accept": "image/jpeg"}
        resp = await self.client.get(self.url, headers, enable_cache=False)
        resp.raise_for_status()
        return await self.eventloop.run_in_thread(
            self._encode_image, resp.content
        )

    def _encode_image(self, image: bytes) -> str:
        return base64.b64encode(image).decode()

    async def post_image(self, payload: Dict[str, Any]) -> None:
        uid: Optional[str] = payload.get("id")
        timer: Optional[int] = payload.get("timer")
        try:
            if uid is not None:
                url = payload.get("endpoint", SP_SNAPSHOT_URL)
                img = await self.extract_image()
                headers = {
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                body = f"id={url_escape(uid)}&image={url_escape(img)}"
                resp = await self.client.post(
                    url, body=body, headers=headers, enable_cache=False
                )
                resp.raise_for_status()
            elif timer is not None:
                await asyncio.sleep(timer / 1000)
                img = await self.extract_image()
                self.simplyprint.send_sp("stream", {"base": img})
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self.server.is_verbose_enabled():
                return
            logging.exception("SimplyPrint WebCam Stream Error")

class PrintHandler:
    def __init__(self, simplyprint: SimplyPrint) -> None:
        self.simplyprint = simplyprint
        self.server = simplyprint.server
        self.eventloop = self.server.get_event_loop()
        self.cache = simplyprint.cache
        self.download_task: Optional[asyncio.Task] = None
        self.print_ready_event: asyncio.Event = asyncio.Event()
        self.download_progress: int = -1
        self.pending_file: str = ""
        self.last_started: str = ""
        self.sp_user = UserInfo("SimplyPrint", "")

    def download_file(self, url: str, start: bool):
        coro = self._download_sp_file(url, start)
        self.download_task = self.eventloop.create_task(coro)

    def cancel(self):
        if (
            self.download_task is not None and
            not self.download_task.done()
        ):
            self.download_task.cancel()
            self.download_task = None

    def notify_ready(self):
        self.print_ready_event.set()

    async def _download_sp_file(self, url: str, start: bool):
        client: HttpClient = self.server.lookup_component("http_client")
        fm: FileManager = self.server.lookup_component("file_manager")
        gc_path = pathlib.Path(fm.get_directory())
        if not gc_path.is_dir():
            logging.debug(f"GCode Path Not Registered: {gc_path}")
            self.simplyprint.send_sp(
                "file_progress",
                {"state": "error", "message": "GCode Path not Registered"}
            )
            return
        accept = "text/plain,applicaton/octet-stream"
        self._on_download_progress(0, 0, 0)
        try:
            logging.debug(f"Downloading URL: {url}")
            tmp_path = await client.download_file(
                url, accept, progress_callback=self._on_download_progress,
                connect_timeout=15.,
                request_timeout=3600.,
            )
        except asyncio.TimeoutError:
            raise
        except Exception:
            logging.exception(f"Failed to download file: {url}")
            self.simplyprint.send_sp(
                "file_progress",
                {"state": "error", "message": "Network Error"}
            )
            return
        finally:
            self.download_progress = -1
        logging.debug("Simplyprint: Download Complete")
        filename = pathlib.PurePath(tmp_path.name)
        fpath = gc_path.joinpath(filename.name)
        if self.cache.job_info.get("filename", "") == str(fpath):
            # This is an attempt to overwrite a print in progress, make a copy
            count = 0
            while fpath.exists():
                name = f"{filename.stem}_copy_{count}.{filename.suffix}"
                fpath = gc_path.joinpath(name)
                count += 1
        args: Dict[str, Any] = {
            "filename": fpath.name,
            "tmp_file_path": str(tmp_path),
        }
        state = "pending"
        if self.cache.state == "operational":
            args["print"] = "true" if start else "false"
        try:
            ret = await fm.finalize_upload(args)
        except self.server.error as e:
            logging.exception("GCode Finalization Failed")
            self.simplyprint.send_sp(
                "file_progress",
                {"state": "error", "message": f"GCode Finalization Failed: {e}"}
            )
            return
        self.pending_file = fpath.name
        if ret.get("print_started", False):
            state = "started"
            self.last_started = self.pending_file
            self.pending_file = ""
        elif not start and await self._check_can_print():
            state = "ready"
        if state == "pending":
            self.print_ready_event.clear()
            try:
                await asyncio.wait_for(self.print_ready_event.wait(), 10.)
            except asyncio.TimeoutError:
                self.pending_file = ""
                self.simplyprint.send_sp(
                    "file_progress",
                    {"state": "error", "message": "Pending print timed out"}
                )
                return
            else:
                if start:
                    await self.start_print()
                    return
                state = "ready"
        self.simplyprint.send_sp("file_progress", {"state": state})

    async def start_print(self) -> None:
        if not self.pending_file:
            return
        pending = self.pending_file
        self.pending_file = ""
        kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
        data = {"state": "started"}
        try:
            await kapi.start_print(pending, user=self.sp_user)
        except Exception:
            logging.exception("Print Failed to start")
            data["state"] = "error"
            data["message"] = "Failed to start print"
        else:
            self.last_started = pending
        self.simplyprint.send_sp("file_progress", data)

    async def _check_can_print(self) -> bool:
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        if kconn.state != KlippyState.READY:
            return False
        kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
        try:
            result = await kapi.query_objects({"print_stats": None})
        except Exception:
            # Klippy not connected
            return False
        if 'print_stats' not in result:
            return False
        state: str = result['print_stats']['state']
        if state in ["printing", "paused"]:
            return False
        return True

    def _on_download_progress(self, percent: int, size: int, recd: int) -> None:
        if percent == self.download_progress:
            return
        self.download_progress = percent
        self.simplyprint.send_sp(
            "file_progress", {"state": "downloading", "percent": percent}
        )

class ProtoLogger:
    def __init__(self, config: ConfigHelper) -> None:
        server = config.get_server()
        self._logger: Optional[logging.Logger] = None
        if not server.is_verbose_enabled():
            return
        fm: FileManager = server.lookup_component("file_manager")
        log_root = fm.get_directory("logs")
        if log_root:
            log_parent = pathlib.Path(log_root)
        else:
            log_parent = pathlib.Path(tempfile.gettempdir())
        log_path = log_parent.joinpath("simplyprint.log")
        queue: SimpleQueue = SimpleQueue()
        self.queue_handler = LocalQueueHandler(queue)
        self._logger = logging.getLogger("simplyprint")
        self._logger.addHandler(self.queue_handler)
        self._logger.propagate = False
        file_hdlr = logging.handlers.TimedRotatingFileHandler(
            log_path, when='midnight', backupCount=2)
        formatter = logging.Formatter(
            '%(asctime)s [%(funcName)s()] - %(message)s')
        file_hdlr.setFormatter(formatter)
        self.qlistner = logging.handlers.QueueListener(queue, file_hdlr)
        self.qlistner.start()

    def info(self, msg: str) -> None:
        if self._logger is None:
            return
        self._logger.info(msg)

    def debug(self, msg: str) -> None:
        if self._logger is None:
            return
        self._logger.debug(msg)

    def warning(self, msg: str) -> None:
        if self._logger is None:
            return
        self._logger.warning(msg)

    def exception(self, msg: str) -> None:
        if self._logger is None:
            return
        self._logger.exception(msg)

    def close(self):
        if self._logger is None:
            return
        self._logger.removeHandler(self.queue_handler)
        self.qlistner.stop()
        self._logger = None

def load_component(config: ConfigHelper) -> SimplyPrint:
    return SimplyPrint(config)
