# PanelDue LCD display support
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import time
import logging
import asyncio
from collections import deque
from ..utils import ServerError, async_serial
from ..utils import json_wrapper as jsonw

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Deque,
    Any,
    Tuple,
    Optional,
    Dict,
    List,
    Callable,
    Coroutine,
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .klippy_connection import KlippyConnection
    from .klippy_apis import KlippyAPI as APIComp
    from .file_manager.file_manager import FileManager as FMComp
    FlexCallback = Callable[..., Optional[Coroutine]]

MIN_EST_TIME = 10.
INITIALIZE_TIMEOUT = 10.

class PanelDueError(ServerError):
    pass


RESTART_GCODES = ["RESTART", "FIRMWARE_RESTART"]


class PanelDue:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.file_manager: FMComp = self.server.lookup_component('file_manager')
        self.klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        self.kinematics: str = "none"
        self.machine_name = config.get('machine_name', "Klipper")
        self.firmware_name: str = "Repetier | Klipper"
        self.last_message: Optional[str] = None
        self.last_gcode_response: Optional[str] = None
        self.current_file: str = ""
        self.file_metadata: Dict[str, Any] = {}
        self.enable_checksum = config.getboolean('enable_checksum', True)
        self.debug_queue: Deque[str] = deque(maxlen=100)
        self.enabled: bool = True

        # Initialize tracked state.
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        self.printer_state: Dict[str, Dict[str, Any]] = kconn.get_subscription_cache()
        self.extruder_count: int = 0
        self.heaters: List[str] = []
        self.is_ready: bool = False
        self.is_shutdown: bool = False
        self.initialized: bool = False
        self.cq_busy: bool = False
        self.gq_busy: bool = False
        self.command_queue: List[Tuple[FlexCallback, Any, Any]] = []
        self.gc_queue: List[str] = []
        self.last_printer_state: str = 'O'
        self.last_update_time: float = 0.

        # Set up macros
        self.confirmed_gcode: str = ""
        self.mbox_sequence: int = 0
        self.available_macros: Dict[str, str] = {}
        self.confirmed_macros = {
            "RESTART": "RESTART",
            "FIRMWARE_RESTART": "FIRMWARE_RESTART"}
        macros = config.getlist('macros', None)
        if macros is not None:
            # The macro's configuration name is the key, whereas the full
            # command is the value
            self.available_macros = {m.split()[0]: m for m in macros}
        conf_macros = config.getlist('confirmed_macros', None)
        if conf_macros is not None:
            # The macro's configuration name is the key, whereas the full
            # command is the value
            self.confirmed_macros = {m.split()[0]: m for m in conf_macros}
        self.available_macros.update(self.confirmed_macros)
        self.non_trivial_keys = config.getlist('non_trivial_keys', ["Klipper state"])
        self.ser_conn = async_serial.AsyncSerialConnection.from_config(config)
        logging.info("PanelDue Configured")

        # Register server events
        self.server.register_event_handler(
            "server:klippy_ready", self._process_klippy_ready
        )
        self.server.register_event_handler(
            "server:klippy_shutdown", self._process_klippy_shutdown
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._process_klippy_disconnect
        )
        self.server.register_event_handler(
            "server:gcode_response", self.handle_gcode_response
        )
        self.server.register_remote_method("paneldue_beep", self.paneldue_beep)

        # These commands are directly executed on the server and do not to
        # make a request to Klippy
        self.direct_gcodes: Dict[str, FlexCallback] = {
            'M20': self._run_paneldue_M20,
            'M30': self._run_paneldue_M30,
            'M36': self._run_paneldue_M36,
            'M408': self._run_paneldue_M408
        }

        # These gcodes require special parsing or handling prior to being
        # sent via Klippy's "gcode/script" api command.
        self.special_gcodes: Dict[str, Callable[[List[str]], str]] = {
            'M0': lambda args: "CANCEL_PRINT",
            'M23': self._prepare_M23,
            'M24': lambda args: "RESUME",
            'M25': lambda args: "PAUSE",
            'M32': self._prepare_M32,
            'M98': self._prepare_M98,
            'M120': lambda args: "SAVE_GCODE_STATE STATE=PANELDUE",
            'M121': lambda args: "RESTORE_GCODE_STATE STATE=PANELDUE",
            'M290': self._prepare_M290,
            'M292': self._prepare_M292,
            'M999': lambda args: "FIRMWARE_RESTART"
        }

    async def run_serial(self) -> None:
        last_exc = Exception()
        while self.enabled:
            try:
                self.ser_conn.open()
            except (self.ser_conn.error, OSError) as e:
                if type(last_exc) is not type(e) and last_exc.args != e.args:
                    logging.exception("PanelDue Serial Open Error")
                    last_exc = e
                await asyncio.sleep(2.)
                continue
            reader = self.ser_conn.reader
            decoded_line: str = ""
            async for line in reader:
                try:
                    decoded_line = line.strip().decode('utf-8', 'ignore')
                    self.process_line(decoded_line)
                except asyncio.CancelledError:
                    raise
                except ServerError:
                    msg = f"GCode Processing Error: {decoded_line}"
                    logging.exception(msg)
                    self.handle_gcode_response(f"!! {msg}")
                except Exception:
                    logging.exception("Error during gcode processing")
            if self.enabled:
                await asyncio.sleep(2.)
            last_exc = Exception()
            self.initialized = False

    async def component_init(self) -> None:
        self.serial_task = self.event_loop.create_task(self.run_serial())

    async def _process_klippy_ready(self) -> None:
        # Request "info" and "configfile" status
        retries = 10
        printer_info: Dict[str, Any] = {}
        cfg_status: Dict[str, Any] = {}
        while retries:
            try:
                printer_info = await self.klippy_apis.get_klippy_info()
                cfg_status = await self.klippy_apis.query_objects({'configfile': None})
            except self.server.error:
                logging.exception("PanelDue initialization request failed")
                retries -= 1
                if not retries:
                    raise
                await asyncio.sleep(1.)
                continue
            break

        self.firmware_name = "Repetier | Klipper " + printer_info['software_version']
        config: Dict[str, Any] = cfg_status.get('configfile', {}).get('config', {})
        printer_cfg: Dict[str, Any] = config.get('printer', {})
        self.kinematics = printer_cfg.get('kinematics', "none")

        logging.info(
            f"PanelDue Config Received:\n"
            f"Firmware Name: {self.firmware_name}\n"
            f"Kinematics: {self.kinematics}\n"
            f"Printer Config: {config}\n")

        # Make subscription request
        sub_args: Dict[str, Optional[List[str]]] = {
            "motion_report": None,
            "gcode_move": None,
            "toolhead": None,
            "virtual_sdcard": None,
            "fan": None,
            "display_status": None,
            "print_stats": None,
            "idle_timeout": None,
            "gcode_macro PANELDUE_BEEP": None
        }
        self.extruder_count = 0
        self.heaters = []
        extruders = []
        for cfg in config:
            if cfg.startswith("extruder"):
                self.extruder_count += 1
                extruders.append(cfg)
                sub_args[cfg] = None
            elif cfg == "heater_bed":
                self.heaters.append(cfg)
                sub_args[cfg] = None
        extruders.sort()
        self.heaters.extend(extruders)
        try:
            await self.klippy_apis.subscribe_objects(sub_args)
        except self.server.error:
            logging.exception("Unable to complete subscription request")
        self.is_shutdown = False
        self.is_ready = True

    def _process_klippy_shutdown(self) -> None:
        self.is_shutdown = True

    def _process_klippy_disconnect(self) -> None:
        # Tell the PD that the printer is "off"
        self.write_response({'status': 'O'})
        self.last_printer_state = 'O'
        self.is_ready = False
        self.is_shutdown = self.is_shutdown = False

    def paneldue_beep(self, frequency: int, duration: float) -> None:
        duration = int(duration * 1000.)
        self.write_response(
            {'beep_freq': frequency, 'beep_length': duration})

    def process_line(self, line: str) -> None:
        self.debug_queue.append(line)
        # If we find M112 in the line then skip verification
        if "M112" in line.upper():
            self.event_loop.register_callback(self.klippy_apis.emergency_stop)
            return

        if self.enable_checksum:
            # Get line number
            line_index = line.find(' ')
            try:
                line_no: Optional[int] = int(line[1:line_index])
            except Exception:
                line_index = -1
                line_no = None

            # Verify checksum
            cs_index = line.rfind('*')
            try:
                checksum = int(line[cs_index+1:])
            except Exception:
                # Invalid checksum, do not process
                msg = "!! Invalid Checksum"
                if line_no is not None:
                    msg += f" Line Number: {line_no}"
                logging.exception("PanelDue: " + msg)
                raise PanelDueError(msg)

            # Checksum is calculated by XORing every byte in the line other
            # than the checksum itself
            calculated_cs = 0
            for c in line[:cs_index]:
                calculated_cs ^= ord(c)
            if calculated_cs & 0xFF != checksum:
                msg = "!! Invalid Checksum"
                if line_no is not None:
                    msg += f" Line Number: {line_no}"
                logging.info("PanelDue: " + msg)
                raise PanelDueError(msg)

            script = line[line_index+1:cs_index]
        else:
            script = line
        # Execute the gcode.  Check for special RRF gcodes that
        # require special handling
        parts = script.split()
        cmd = parts[0].strip()
        if cmd in ["M23", "M30", "M32", "M36", "M37", "M98"]:
            arg = script[len(cmd):].strip()
            parts = [cmd, arg]

        # Check for commands that query state and require immediate response
        if cmd in self.direct_gcodes:
            params: Dict[str, Any] = {}
            for p in parts[1:]:
                if p[0] not in "PSR":
                    params["arg_p"] = p.strip(" \"\t\n")
                    continue
                arg = p[0].lower()
                try:
                    val = int(p[1:].strip()) if arg in "sr" \
                        else p[1:].strip(" \"\t\n")
                except Exception:
                    msg = f"paneldue: Error parsing direct gcode {script}"
                    self.handle_gcode_response("!! " + msg)
                    logging.exception(msg)
                    return
                params[f"arg_{arg}"] = val
            func = self.direct_gcodes[cmd]
            self.queue_command(func, **params)
            return

        # Prepare GCodes that require special handling
        if cmd in self.special_gcodes:
            sgc_func = self.special_gcodes[cmd]
            script = sgc_func(parts[1:])

        if not script:
            return
        self.queue_gcode(script)

    def queue_gcode(self, script: str) -> None:
        self.gc_queue.append(script)
        if not self.gq_busy:
            self.gq_busy = True
            self.event_loop.register_callback(self._process_gcode_queue)

    async def _process_gcode_queue(self) -> None:
        while self.gc_queue:
            script = self.gc_queue.pop(0)
            try:
                if script in RESTART_GCODES:
                    await self.klippy_apis.do_restart(script)
                else:
                    await self.klippy_apis.run_gcode(script)
            except self.server.error:
                msg = f"Error executing script {script}"
                self.handle_gcode_response("!! " + msg)
                logging.exception(msg)
        self.gq_busy = False

    def queue_command(self, cmd: FlexCallback, *args, **kwargs) -> None:
        self.command_queue.append((cmd, args, kwargs))
        if not self.cq_busy:
            self.cq_busy = True
            self.event_loop.register_callback(self._process_command_queue)

    async def _process_command_queue(self) -> None:
        while self.command_queue:
            cmd, args, kwargs = self.command_queue.pop(0)
            try:
                ret = cmd(*args, **kwargs)
                if ret is not None:
                    await ret
            except Exception:
                logging.exception("Error processing command")
        self.cq_busy = False

    def _clean_filename(self, filename: str) -> str:
        # Remove quotes and whitespace
        filename.strip(" \"\t\n")
        # Remove drive number
        if filename.startswith("0:/"):
            filename = filename[3:]
        # Remove initial "gcodes" folder.  This is necessary
        # due to the HACK in the paneldue_M20 gcode.
        if filename.startswith("gcodes/"):
            filename = filename[6:]
        elif filename.startswith("/gcodes/"):
            filename = filename[7:]
        # Start with a "/" so the gcode parser can correctly
        # handle files that begin with digits or special chars
        if filename[0] != "/":
            filename = "/" + filename
        return filename

    def _prepare_M23(self, args: List[str]) -> str:
        filename = self._clean_filename(args[0])
        return f"M23 {filename}"

    def _prepare_M32(self, args: List[str]) -> str:
        filename = self._clean_filename(args[0])
        # Escape existing double quotes in the file name
        filename = filename.replace("\"", "\\\"")
        return f"SDCARD_PRINT_FILE FILENAME=\"{filename}\""

    def _prepare_M98(self, args: List[str]) -> str:
        macro = args[0][1:].strip(" \"\t\n")
        name_start = macro.rfind('/') + 1
        macro = macro[name_start:]
        cmd = self.available_macros.get(macro)
        if cmd is None:
            raise PanelDueError(f"Macro {macro} invalid")
        if macro in self.confirmed_macros:
            self._create_confirmation(macro, cmd)
            cmd = ""
        return cmd

    def _prepare_M290(self, args: List[str]) -> str:
        # args should in in the format Z0.02
        offset = args[0][1:].strip()
        return f"SET_GCODE_OFFSET Z_ADJUST={offset} MOVE=1"

    def _prepare_M292(self, args: List[str]) -> str:
        p_val = int(args[0][1])
        if p_val == 0:
            cmd = self.confirmed_gcode
            self.confirmed_gcode = ""
            return cmd
        return ""

    def _create_confirmation(self, name: str, gcode: str) -> None:
        self.mbox_sequence += 1
        self.confirmed_gcode = gcode
        title = "Confirmation Dialog"
        msg = f"Please confirm your intent to run {name}."  \
            " Press OK to continue, or CANCEL to abort."
        mbox: Dict[str, Any] = {}
        mbox['msgBox.mode'] = 3
        mbox['msgBox.msg'] = msg
        mbox['msgBox.seq'] = self.mbox_sequence
        mbox['msgBox.title'] = title
        mbox['msgBox.controls'] = 0
        mbox['msgBox.timeout'] = 0
        logging.debug(f"Creating PanelDue Confirmation: {mbox}")
        self.write_response(mbox)

    def handle_gcode_response(self, response: str) -> None:
        # Only queue up "non-trivial" gcode responses.  At the
        # moment we'll handle state changes and errors
        if "Klipper state" in response \
                or response.startswith('!!'):
            self.last_gcode_response = response
        else:
            for key in self.non_trivial_keys:
                if key in response:
                    self.last_gcode_response = response
                    return

    def write_response(self, response: Dict[str, Any]) -> None:
        byte_resp = jsonw.dumps(response) + b"\r\n"
        self.ser_conn.send(byte_resp)

    def _get_printer_status(self) -> str:
        # PanelDue States applicable to Klipper:
        # I = idle, P = printing from SD, S = stopped (shutdown),
        # C = starting up (not ready), A = paused, D = pausing,
        # B = busy
        if self.is_shutdown:
            return 'S'

        p_state = self.printer_state
        sd_state: str
        sd_state = p_state.get("print_stats", {}).get("state", "standby")
        if sd_state == "printing":
            if self.last_printer_state == 'A':
                # Resuming
                return 'R'
            # Printing
            return 'P'
        elif sd_state == "paused":
            p_active = (
                p_state.get("idle_timeout", {}).get("state", 'Idle') == "Printing"
            )
            if p_active and self.last_printer_state != 'A':
                # Pausing
                return 'D'
            else:
                # Paused
                return 'A'

        return 'I'

    def _run_paneldue_M408(self,
                           arg_r: Optional[int] = None,
                           arg_s: int = 1
                           ) -> None:
        response: Dict[str, Any] = {}
        sequence = arg_r
        response_type = arg_s

        curtime = self.event_loop.get_loop_time()
        if curtime - self.last_update_time > INITIALIZE_TIMEOUT:
            self.initialized = False
        self.last_update_time = curtime

        if not self.initialized:
            response['dir'] = "/macros"
            response['files'] = list(self.available_macros.keys())
            self.initialized = True
        if not self.is_ready:
            self.last_printer_state = 'O'
            response['status'] = self.last_printer_state
            self.write_response(response)
            return
        if sequence is not None and self.last_gcode_response:
            # Send gcode responses
            response['seq'] = sequence + 1
            response['resp'] = self.last_gcode_response
            self.last_gcode_response = None
        if response_type == 1:
            # Extended response Request
            response['myName'] = self.machine_name
            response['firmwareName'] = self.firmware_name
            response['numTools'] = self.extruder_count
            response['geometry'] = self.kinematics
            response['axes'] = 3

        p_state = self.printer_state
        toolhead = p_state.get("toolhead", {})
        gcode_move = p_state.get("gcode_move", {})
        self.last_printer_state = self._get_printer_status()
        response['status'] = self.last_printer_state
        response['babystep'] = round(
            gcode_move.get('homing_origin', [0., 0., 0., 0.])[2], 3
        )

        # Current position
        pos: List[float]
        homed_pos: str
        sfactor: float
        pos = p_state.get("motion_report", {}).get('live_position', [0., 0., 0., 0.])
        response['pos'] = [round(p, 2) for p in pos[:3]]
        homed_pos = toolhead.get('homed_axes', "")
        response['homed'] = [int(a in homed_pos) for a in "xyz"]
        sfactor = round(gcode_move.get('speed_factor', 1.) * 100, 2)
        response['sfactor'] = sfactor

        # Print Progress Tracking
        sd_status = p_state.get('virtual_sdcard', {})
        print_stats = p_state.get('print_stats', {})
        fname: str = print_stats.get('filename', "")
        sd_print_state: Optional[str] = print_stats.get('state')
        if sd_print_state in ['printing', 'paused']:
            # We know a file has been loaded, initialize metadata
            if self.current_file != fname:
                self.current_file = fname
                self.file_metadata = self.file_manager.get_file_metadata(fname)
            progress: float = sd_status.get('progress', 0)
            # progress and print tracking
            if progress:
                response['fraction_printed'] = round(progress, 3)
                est_time: float = self.file_metadata.get('estimated_time', 0)
                if est_time > MIN_EST_TIME:
                    # file read estimate
                    times_left = [int(est_time - est_time * progress)]
                    # filament estimate
                    est_total_fil: Optional[float]
                    est_total_fil = self.file_metadata.get('filament_total')
                    if est_total_fil:
                        cur_filament: float = print_stats.get(
                            'filament_used', 0.)
                        fpct = min(1., cur_filament / est_total_fil)
                        times_left.append(int(est_time - est_time * fpct))
                    # object height estimate
                    obj_height: Optional[float]
                    obj_height = self.file_metadata.get('object_height')
                    if obj_height:
                        cur_height: float = gcode_move.get(
                            'gcode_position', [0., 0., 0., 0.]
                        )[2]
                        hpct = min(1., cur_height / obj_height)
                        times_left.append(int(est_time - est_time * hpct))
                else:
                    # The estimated time is not in the metadata, however we
                    # can still provide an estimate based on file progress
                    duration: float = print_stats.get('print_duration', 0.)
                    times_left = [int(duration / progress - duration)]
                response['timesLeft'] = times_left
        else:
            # clear filename and metadata
            self.current_file = ""
            self.file_metadata = {}

        fan_speed: Optional[float] = p_state.get('fan', {}).get('speed')
        if fan_speed is not None:
            response['fanPercent'] = [round(fan_speed * 100, 1)]

        extruder_name: str = ""
        if self.extruder_count > 0:
            extruder_name = toolhead.get('extruder', "")
            if extruder_name:
                tool = 0
                if extruder_name != "extruder":
                    tool = int(extruder_name[-1])
                response['tool'] = tool

        # Report Heater Status
        efactor: float = round(gcode_move.get('extrude_factor', 1.) * 100., 2)

        for name in self.heaters:
            htr_state = p_state.get(name, {})
            temp: float = round(htr_state.get('temperature', 0.0), 1)
            target: float = round(htr_state.get('target', 0.0), 1)
            response.setdefault('heaters', []).append(temp)
            response.setdefault('active', []).append(target)
            response.setdefault('standby', []).append(target)
            if name.startswith('extruder'):
                a_stat = 2 if name == extruder_name else 1
                response.setdefault('hstat', []).append(a_stat if target else 0)
                response.setdefault('efactor', []).append(efactor)
                response.setdefault('extr', []).append(round(pos[3], 2))
            else:
                response.setdefault('hstat', []).append(2 if target else 0)

        # Display message (via M117)
        msg: str = p_state.get('display_status', {}).get('message', "")
        if msg and msg != self.last_message:
            response['message'] = msg
            # reset the message so it only shows once.  The paneldue
            # is strange about this, and displays it as a full screen
            # notification
        self.last_message = msg
        self.write_response(response)

    def _run_paneldue_M20(self, arg_p: str, arg_s: int = 0) -> None:
        response_type = arg_s
        if response_type != 2:
            logging.info(
                f"Cannot process response type {response_type} in M20")
            return
        path = arg_p

        # Strip quotes if they exist
        path = path.strip('\"')

        # Path should come in as "0:/macros, or 0:/<gcode_folder>".  With
        # repetier compatibility enabled, the default folder is root,
        # ie. "0:/"
        if path.startswith("0:/"):
            path = path[2:]
        response: Dict[str, Any] = {'dir': path}
        response['files'] = []

        if path == "/macros":
            response['files'] = list(self.available_macros.keys())
        else:
            # HACK: The PanelDue has a bug where it does not correctly detect
            # subdirectories if we return the root as "/".  Moonraker can
            # support a "gcodes" directory, however we must choose between this
            # support or disabling RRF specific gcodes (this is done by
            # identifying as Repetier).
            # The workaround below converts both "/" and "/gcodes" paths to
            # "gcodes".
            if path == "/":
                response['dir'] = "/gcodes"
                path = "gcodes"
            elif path.startswith("/gcodes"):
                path = path[1:]

            flist = self.file_manager.list_dir(path, simple_format=True)
            if flist:
                response['files'] = flist
        self.write_response(response)

    async def _run_paneldue_M30(self, arg_p: str = "") -> None:
        # Delete a file.  Clean up the file name and make sure
        # it is relative to the "gcodes" root.
        path = arg_p
        path = path.strip('\"')
        if path.startswith("0:/"):
            path = path[3:]
        elif path[0] == "/":
            path = path[1:]

        if not path.startswith("gcodes/"):
            path = "gcodes/" + path
        await self.file_manager.delete_file(path)

    def _run_paneldue_M36(self, arg_p: Optional[str] = None) -> None:
        response: Dict[str, Any] = {}
        filename: Optional[str] = arg_p
        sd_status = self.printer_state.get('virtual_sdcard', {})
        print_stats = self.printer_state.get('print_stats', {})
        if filename is None:
            # PanelDue is requesting file information on a
            # currently printed file
            active = False
            if sd_status and print_stats:
                filename = print_stats['filename']
                active = sd_status['is_active']
            if not filename or not active:
                # Either no file printing or no virtual_sdcard
                response['err'] = 1
                self.write_response(response)
                return
            else:
                response['fileName'] = filename.split("/")[-1]

        # For consistency make sure that the filename begins with the
        # "gcodes/" root.  The M20 HACK should add this in some cases.
        # Ideally we would add support to the PanelDue firmware that
        # indicates Moonraker supports a "gcodes" directory.
        if filename[0] == "/":
            filename = filename[1:]
        if not filename.startswith("gcodes/"):
            filename = "gcodes/" + filename

        metadata: Dict[str, Any] = \
            self.file_manager.get_file_metadata(filename)
        if metadata:
            response['err'] = 0
            response['size'] = metadata['size']
            # workaround for PanelDue replacing the first "T" found
            response['lastModified'] = "T" + time.ctime(metadata['modified'])
            slicer: Optional[str] = metadata.get('slicer')
            if slicer is not None:
                response['generatedBy'] = slicer
            height: Optional[float] = metadata.get('object_height')
            if height is not None:
                response['height'] = round(height, 2)
            layer_height: Optional[float] = metadata.get('layer_height')
            if layer_height is not None:
                response['layerHeight'] = round(layer_height, 2)
            filament: Optional[float] = metadata.get('filament_total')
            if filament is not None:
                response['filament'] = [round(filament, 1)]
            est_time: Optional[float] = metadata.get('estimated_time')
            if est_time is not None:
                response['printTime'] = int(est_time + .5)
        else:
            response['err'] = 1
        self.write_response(response)

    async def close(self) -> None:
        self.enabled = False
        await self.ser_conn.close()
        if hasattr(self, "serial_task"):
            await self.serial_task
        msg = "\nPanelDue GCode Dump:"
        for i, gc in enumerate(self.debug_queue):
            msg += f"\nSequence {i}: {gc}"
        logging.debug(msg)

def load_component(config: ConfigHelper) -> PanelDue:
    return PanelDue(config)
