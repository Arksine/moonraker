# SimplyPrint Connection Support
#
# Copyright (C) 2022  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import json
import logging
import time
import pathlib
import tornado.websocket
from websockets import Subscribable, WebRequest
# XXX: The below imports are for inital dev and
# debugging.  They are used to create a logger for
# messages sent to and received from the simplyprint
# backend
import logging.handlers
import tempfile
from queue import SimpleQueue
from utils import LocalQueueHandler

from typing import (
    TYPE_CHECKING,
    Optional,
    Dict,
    List,
    Union,
    Any,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebsocketManager
    from tornado.websocket import WebSocketClientConnection
    from components.database import MoonrakerDatabase
    from components.klippy_apis import KlippyAPI
    from components.job_state import JobState
    from components.machine import Machine
    from components.file_manager.file_manager import FileManager
    from klippy_connection import KlippyConnection

COMPONENT_VERSION = "0.0.1"
SP_VERSION = "0.1"
TEST_ENDPOINT = f"wss://testws.simplyprint.io/{SP_VERSION}/p"
PROD_ENDPOINT = f"wss://ws.simplyprint.io/{SP_VERSION}/p"
KEEPALIVE_TIME = 96.0
# TODO: Increase this time to something greater, perhaps 30 minutes
CONNECTION_ERROR_LOG_TIME = 60.

class SimplyPrint(Subscribable):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.is_closing = False
        self.test = config.get("sp_test", True)
        self.ws: Optional[WebSocketClientConnection] = None
        self.reported_state = "offline"
        self.reported_temps: Dict[str, Any] = {}
        self.last_received_temps: Dict[str, float] = {}
        self.last_temp_update_time: float = 0.
        self.last_err_log_time: float = 0.
        self.printer_status: Dict[str, Dict[str, Any]] = {}
        self.keepalive_hdl: Optional[asyncio.TimerHandle] = None
        self.reconnect_hdl: Optional[asyncio.TimerHandle] = None
        database: MoonrakerDatabase = self.server.lookup_component("database")
        database.register_local_namespace("simplyprint", forbidden=True)
        self.spdb = database.wrap_namespace("simplyprint")
        self.sp_info = self.spdb.as_dict()
        # TODO: For testing we are initializing connectd to True.  This
        # should be be set to False in the future
        self.connected = True
        self._set_ws_url()

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
            "job_state:started", self._on_print_start)
        self.server.register_event_handler(
            "job_state:paused", self._on_print_paused)
        self.server.register_event_handler(
            "job_state:resumed", self._on_print_start)
        self.server.register_event_handler(
            "job_state:standby", self._on_print_standby)
        self.server.register_event_handler(
            "job_state:complete", self._on_print_complete)
        self.server.register_event_handler(
            "job_state:error", self._on_print_error)
        self.server.register_event_handler(
            "job_state:cancelled", self._on_print_cancelled)

        # XXX: The call below is for dev, remove before release
        self._setup_simplyprint_logging()

        # TODO: We need the ability to show users the activation code.
        # Hook into announcements?  Create endpoint to get
        # the connection code?  We could render something basic here
        # and present it at http://hostname/server/simplyprint

    async def component_init(self) -> None:
        connected = await self._do_connect(try_once=True)
        if not connected:
            self.reconnect_hdl = self.eventloop.delay_callback(
                5., self._do_connect)

    async def _do_connect(self, try_once=False) -> bool:
        self._logger.info(f"Connecting To SimplyPrint: {self.connect_url}")
        while not self.is_closing:
            try:
                self.ws = await tornado.websocket.websocket_connect(
                    self.connect_url, connect_timeout=5.,
                    on_message_callback=self._on_ws_message)
            except Exception:
                curtime = self.eventloop.get_loop_time()
                timediff = curtime - self.last_err_log_time
                if timediff > CONNECTION_ERROR_LOG_TIME:
                    self.last_err_log_time = curtime
                    logging.exception(
                        f"Failed to connect to SimplyPrint: {self.connect_url}")
                if try_once:
                    self.reconnect_hdl = None
                    return False
                await asyncio.sleep(5.)
            else:
                break
        logging.info("Connected to SimplyPrint Cloud")
        self.reconnect_hdl = None
        return True

    def _on_ws_message(self, message: Union[str, bytes, None]) -> None:
        if isinstance(message, str):
            self._process_message(message)
        elif message is None and not self.is_closing:
            logging.info("SimplyPrint Disconnected")
            self.connected = False
            self.ws = None
            if self.reconnect_hdl is None:
                self.reconnect_hdl = self.eventloop.delay_callback(
                    5., self._do_connect)

    def _process_message(self, msg: str) -> None:
        self._logger.info(f"received: {msg}")
        self._reset_keepalive()
        try:
            packet: Dict[str, Any] = json.loads(msg)
        except json.JSONDecodeError:
            logging.debug(f"Invalid message, not JSON: {msg}")
            return
        event: str = packet.get("type", "")
        data: Optional[Dict[str, Any]] = packet.get("data")
        if event == "connected":
            logging.info("SimplyPrint Reports Connection Success")
            self.connected = True
            self._push_initial_state()
        elif event == "error":
            logging.info(f"SimplyPrint Connection Error: {data}")
            # TODO: Disconnected and reconnect?
        elif event == "new_token":
            if data is None:
                self._logger.info("Invalid message, no data")
                return
            token = data.get("token")
            if not isinstance(token, str):
                self._logger.info(f"Invalid token in message")
                return
            logging.info(f"SimplyPrint Token Received")
            self._save_item("printer_token", token)
            self._set_ws_url()
        elif event == "set_up":
            # TODO: This is a stubbed event to receive the printer ID,
            # it could change
            if data is None:
                self._logger.info(f"Invalid message, no data")
                return
            printer_id = data.get("id")
            if not isinstance(token, str):
                self._logger.info(f"Invalid printer id in message")
                return
            logging.info(f"SimplyPrint Printer ID Received: {printer_id}")
            self._save_item("printer_id", printer_id)
            self._set_ws_url()
            name = data.get("name")
            if not isinstance(name, str):
                self._logger.info(f"Invalid name in message: {msg}")
                return
            logging.info(f"SimplyPrint Printer ID Received: {name}")
            self._save_item("printer_name", name)
        else:
            # TODO: It would be good for the backend to send an
            # event indicating that it is ready to recieve printer
            # status.
            self._logger.info(f"Unknown event: {msg}")

    def _save_item(self, name: str, data: Any):
        self.sp_info[name] = data
        self.spdb[name] = data

    def _set_ws_url(self):
        token: Optional[str] = self.sp_info.get("printer_token")
        printer_id: Optional[str] = self.sp_info.get("printer_id")
        ep = TEST_ENDPOINT if self.test else PROD_ENDPOINT
        self.connect_url = f"{ep}/0/0"
        if token is not None:
            if printer_id is None:
                self.connect_url = f"{ep}/0/{token}"
            else:
                self.connect_url = f"{ep}/{printer_id}/{token}"

    async def _on_klippy_ready(self):
        job_state: JobState = self.server.lookup_component("job_state")
        last_stats: Dict[str, Any] = job_state.get_last_stats()
        if last_stats["state"] == "printing":
            self._update_state("printing")
        else:
            self._update_state("operational")
        klippy_apis: KlippyAPI = self.server.lookup_component("klippy_apis")
        query: Dict[str] = await klippy_apis.query_objects(
            {"heaters": None}, None)
        sub_objs = {}
        if query is not None:
            heaters: Dict[str, Any] = query.get("heaters", {})
            avail_htrs: List[str]
            avail_htrs = sorted(heaters.get("available_heaters", []))
            self._logger.info(f"SimplyPrint: Heaters Detected: {avail_htrs}")
            for htr in avail_htrs:
                if htr.startswith("extruder"):
                    sub_objs[htr] = ["temperature", "target"]
                elif htr == "heater_bed":
                    sub_objs[htr] = ["temperature", "target"]
        if not sub_objs:
            return
        status: Dict[str, Any]
        # Create our own subscription rather than use the host sub
        args = {'objects': sub_objs}
        klippy: KlippyConnection
        klippy = self.server.lookup_component("klippy_connection")
        try:
            resp: Dict[str, Dict[str, Any]] = await klippy.request(
                WebRequest("objects/subscribe", args, conn=self))
            status: Dict[str, Any] = resp.get("status", {})
        except self.server.error:
            status = {}
        if status:
            self._logger.info(f"SimplyPrint: Got Initial Status: {status}")
            self.printer_status = status
            self._update_temps(status)

    def _on_klippy_startup(self, state: str) -> None:
        if state != "ready":
            self._update_state("error")
            self._send_sp("printer_error", None)
        self._send_sp("connection", "connected")
        self._send_firmware_data()

    def _on_klippy_shutdown(self) -> None:
        self._send_sp("printer_error", None)

    def _on_klippy_disconnected(self) -> None:
        self._update_state("offline")
        self._send_sp("connection", "disconnected")
        self.reported_temps = {}
        self.printer_status = {}

    def _on_print_start(self, *args) -> None:
        # inlcludes started and resumed events
        self._update_state("printing")
        self._send_sp("print_started", None)

    def _on_print_paused(self, *args) -> None:
        self._update_state("paused")
        self._send_sp("print_paused", None)

    def _on_print_cancelled(self, *args) -> None:
        # TODO: Update State (translate from Klippy), send
        # print_cancelled event
        self._update_state_from_klippy()
        self._send_sp("print_cancelled", None)

    def _on_print_error(self, *args) -> None:
        self._update_state_from_klippy()
        self._send_sp("print_failure", None)

    def _on_print_complete(self, *args) -> None:
        self._update_state_from_klippy()
        self._send_sp("print_done", None)

    def _on_print_standby(self, *args) -> None:
        self._update_state_from_klippy()

    def send_status(self, status: Dict[str, Any], eventtime: float) -> None:
        self._update_temps(status)

    def _update_temps(self, new_status: Dict[str, Dict[str, Any]]) -> None:
        cur_time = self.eventloop.get_loop_time()
        if cur_time - self.last_temp_update_time < 1.:
            return
        temp_data: Dict[str, List[int]] = {}
        for heater, vals in new_status.items():
            if heater == "heater_bed":
                key = "bed"
            elif heater.startswith("extruder"):
                key = "tool"
                postfix = heater[8:]
                if postfix.isdigit():
                    key += postfix
                else:
                    key += "0"
            else:
                continue
            self.printer_status[heater].update(vals)
            reported_temp = self.printer_status[heater]["temperature"]
            ret = [int(reported_temp + .5)]
            target = int(self.printer_status[heater]["target"] + .5)
            if target:
                ret.append(target)
            last_temps = self.reported_temps.get(key, [])
            if (
                len(ret) == len(last_temps) and
                key in self.last_received_temps
            ):
                last_reported = self.last_received_temps[key]
                if abs(reported_temp - last_reported) < .5:
                    self.last_received_temps.pop(key)
                    continue
            self.last_received_temps[key] = reported_temp
            self.reported_temps[key] = ret
            temp_data[key] = ret
        if not temp_data:
            return
        self.last_temp_update_time = cur_time
        self._send_sp("temps", temp_data)

    def _update_state_from_klippy(self) -> None:
        kstate = self.server.get_klippy_state()
        if kstate == "ready":
            sp_state = "operational"
        elif kstate in ["error", "shutdown"]:
            sp_state = "error"
        else:
            sp_state = "offline"
        self._update_state(sp_state)

    def _update_state(self, new_state: str) -> None:
        if self.reported_state == new_state:
            return
        self.reported_state = new_state
        self._send_sp("state_change", {"new": new_state})

    async def _send_printer_data(self):
        data: Dict[str, Any] = {}
        app_args = self.server.get_app_args()
        data["ui"] = None
        data["ui_version"] = None
        websockets: WebsocketManager
        websockets = self.server.lookup_component("websockets")
        conns = websockets.get_websockets_by_type("web")
        if conns:
            longest = conns[0]
            data["ui"] = longest.client_data["client_name"]
            data["ui_version"] = longest.client_data["version"]
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
        data["wifi_ssid"] = pub_intf.get("ssid", "")
        data["local_ip"] = pub_intf.get("address", "Unknown")
        data["hostname"] = pub_intf["hostname"]
        self._logger.info(f"calculated machine data: {data}")
        self._send_sp("machine_data", data)

    def _send_firmware_data(self):
        kinfo = self.server.get_klippy_info()
        if not kinfo:
            return
        fimrware_date: str = ""
        # Approximate the firmware "date" using the last modified
        # time of the Klippy source folder
        kpath = pathlib.Path(kinfo["klipper_path"]).joinpath("klippy")
        if kpath.is_dir():
            mtime = kpath.stat().st_mtime
            fimrware_date = time.asctime(time.gmtime(mtime))
        version: str = kinfo["sofware_version"]
        unsafe = version.endswith("-dirty") or version == "?"
        if unsafe:
            version = version.rsplit("-", 1)[0]
        fw_info = {
            "firmware": "Klipper",
            "firmware_version": version,
            "firmware_date": fimrware_date,
            "firmware_link": "https://github.com/Klipper3d/klipper",
            "firmware_unsafe": unsafe
        }
        self._send_sp("firmware_data", fw_info)

    def _push_initial_state(self):
        # TODO: This method is called after SP is connected
        # and ready to receive state.  We need to determine
        # if we should
        self._send_sp("state_change", {"new": self.reported_state})
        if self.reported_temps:
            self._send_sp("temps", self.reported_temps)
        self.eventloop.create_task(self._send_printer_data())

    def _send_sp(self, evt_name: str, data: Any) -> asyncio.Future:
        if not self.connected or self.ws is None:
            fut = self.eventloop.create_future()
            fut.set_result(False)
            return fut
        packet = {"type": evt_name, "data": data}
        self._logger.info(f"sent: {packet}")
        self._reset_keepalive()
        return self.ws.write_message(json.dumps(packet))

    def _reset_keepalive(self):
        if self.keepalive_hdl is not None:
            self.keepalive_hdl.cancel()
        self.keepalive_hdl = self.eventloop.delay_callback(
            KEEPALIVE_TIME, self._do_keepalive)

    def _do_keepalive(self):
        self.keepalive_hdl = None
        self._send_sp("keepalive", None)

    def _setup_simplyprint_logging(self):
        fm: FileManager = self.server.lookup_component("file_manager")
        log_root = fm.get_directory("logs")
        if log_root:
            log_parent = pathlib.Path(log_root)
        else:
            log_parent = pathlib.Path(tempfile.gettempdir())
        log_path = log_parent.joinpath("simplyprint.log")
        queue: SimpleQueue = SimpleQueue()
        queue_handler = LocalQueueHandler(queue)
        self._logger = logging.getLogger("simplyprint")
        self._logger.addHandler(queue_handler)
        self._logger.propagate = False
        file_hdlr = logging.handlers.TimedRotatingFileHandler(
            log_path, when='midnight', backupCount=2)
        formatter = logging.Formatter(
            '%(asctime)s [%(funcName)s()] - %(message)s')
        file_hdlr.setFormatter(formatter)
        self.qlistner = logging.handlers.QueueListener(queue, file_hdlr)
        self.qlistner.start()

    async def close(self):
        await self._send_sp("shutdown", None)
        self.qlistner.stop()
        self.is_closing = True
        if self.reconnect_hdl is not None:
            # TODO, would be good to cancel the reconnect task as well
            self.reconnect_hdl.cancel()
        if self.keepalive_hdl is not None:
            self.keepalive_hdl.cancel()
            self.keepalive_hdl = None
        if self.ws is not None:
            self.ws.close()

def load_component(config: ConfigHelper) -> SimplyPrint:
    return SimplyPrint(config)
