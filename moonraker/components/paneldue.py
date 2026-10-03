# PanelDue LCD display support
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
# Copyright (C) 2026  Florian Hofer <SirDeath81_github@outlook.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import re
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

# Matches exactly "extruder", "extruder1", "extruder2", ... - not things like
# "extruder_stepper foo" (a real Klipper section for auxiliary extruder-
# synced steppers, not a heater) which merely starts with the same prefix.
EXTRUDER_NAME_RE = re.compile(r'^extruder\d*$')

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
        self.last_file_name: str = ""
        self.file_metadata: Dict[str, Any] = {}
        # RRF's object model exposes a per-subsystem "seqs" counter block in
        # standalone mode; PanelDue watches specific counters (notably
        # seqs.reply for new messages, seqs.job for job/progress changes) to
        # know when to refresh that part of the UI, rather than passively
        # noticing values changed in the flattened poll. We never sent this at
        # all, which may be why progress/messages never trigger a UI update.
        self.last_fraction_printed: float = -1.0
        self.last_job_status: str = ""
        self.seqs_job: int = 1
        self.seqs_reply: int = 1
        self.seqs_state: int = 1
        self.seqs_fans: int = 1
        # Bumped each time _process_klippy_ready (re)discovers the heater/
        # tool/board configuration. If PanelDue connects and does its initial
        # M409 discovery poll before Klipper itself is ready, self.heaters/
        # extruder_count are still empty and the panel builds an empty
        # control screen (no nozzle/bed/macro widgets) - since these seqs
        # were previously hardcoded to a constant 1, the panel had no signal
        # that the model changed once Klipper actually came online and never
        # rebuilt the screen, requiring a manual PanelDue reset to reconnect
        # after the printer was already up.
        self.seqs_heat: int = 1
        self.seqs_tools: int = 1
        self.seqs_boards: int = 1
        self.last_fan_speed: float = -1.0
        self.enable_checksum = config.getboolean('enable_checksum', True)
        self.debug_queue: Deque[str] = deque(maxlen=100)
        self.enabled: bool = True

        # Protocol auto-detection variants
        self.detected_variant: str = "UNKNOWN"
        self.current_line_no: Optional[int] = None
        self.m409_sequence: int = 0  # Dynamic sequence counter tracking

        # Emulation cache for heater states
        self.bed_active_target: float = 0.0
        self.bed_standby_target: float = 0.0
        self.bed_state_emulation: int = 0

        # Counter for streaming macros safely during initialization phase
        self.init_macro_counter: int = 0

        # Initialize tracked state.
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        self.printer_state: Dict[str, Dict[str, Any]] = kconn.get_subscription_cache()
        self.extruder_count: int = 0
        self.heaters: List[str] = []
        self.chamber_heaters: List[str] = []
        # Filament sensors (filament_switch_sensor/filament_motion_sensor).
        # Their "filament_detected" state is used as an independent signal
        # to force message redelivery - see _check_filament_sensor_edge().
        self.filament_sensors: List[str] = []
        self.filament_sensor_detected: Dict[str, bool] = {}
        self.bed_level_gcode: str = ""
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
        self.confirmed_macro_name: str = ""
        self.mbox_sequence: int = 0
        self.pending_msgbox: Optional[Dict[str, Any]] = None
        self.available_macros: Dict[str, str] = {}
        self.confirmed_macros = {
            "RESTART": "RESTART",
            "FIRMWARE_RESTART": "FIRMWARE_RESTART"}
        macros = config.getlist('macros', None)
        if macros is not None:
            # The macro's configuration name is the key, whereas the full
            # command is the value
            self.available_macros = {m.split()[0]: m for m in macros if m.strip()}
        conf_macros = config.getlist('confirmed_macros', None)
        if conf_macros is not None:
            # The macro's configuration name is the key, whereas the full
            # command is the value
            self.confirmed_macros = {m.split()[0]: m for m in conf_macros if m.strip()}
        self.available_macros.update(self.confirmed_macros)
        self.non_trivial_keys = config.getlist('non_trivial_keys', ["Klipper state"])
        # Diagnostic toggle: when true, the periodic status poll drops the
        # heat/tools/temps/coords sections entirely to test whether response
        # size (not format/content) is why progress/messages never render on
        # the panel. Flip this in moonraker.conf, no need to swap the file.
        self.debug_minimal_response = config.getboolean('debug_minimal_response', False)
        # Master switch for the high-volume per-line/per-request debug logs
        # added while diagnosing the 3.7.0 protocol (raw serial RX/TX dumps,
        # per-M409-request traces). Off by default - these fire constantly
        # during normal operation and drown out the moonraker log. Protocol
        # variant detection (which panel firmware was recognized) always logs
        # regardless of this setting, since that's a one-off, genuinely useful
        # confirmation for any user checking whether their PanelDue connected
        # correctly - not a repeating debug trace.
        self.verbose_logging = config.getboolean('verbose_logging', False)
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

        # Map directly handled G-codes to their respective callbacks. These
        # commands are directly executed on the server and do not to make a
        # request to Klippy
        self.direct_gcodes: Dict[str, FlexCallback] = {
            'M20': self._run_paneldue_M20,
            'M30': self._run_paneldue_M30,
            'M36': self._run_paneldue_M36,
            'M408': self._run_paneldue_M408,
            'M409': self._run_paneldue_M409,
        }

        # Map special G-codes with flexible arguments to Klipper macros. These
        # gcodes require special parsing or handling prior to being sent via
        # Klippy's "gcode/script" api command.
        self.special_gcodes: Dict[str, Callable[[List[str]], str]] = {
            'M0': lambda args: "CANCEL_PRINT",
            'M23': self._prepare_M23,
            'M24': lambda args: "RESUME",
            'M25': lambda args: "PAUSE",
            'M32': self._prepare_M32,
            'M98': self._prepare_M98,
            'M120': lambda args: "SAVE_GCODE_STATE STATE=PANELDUE",
            'M121': lambda args: "RESTORE_GCODE_STATE STATE=PANELDUE",
            'M140': self._prepare_M140,
            'M141': self._prepare_M141,
            'M220': lambda args: f"M220 {' '.join(args)}",
            'M221': lambda args: f"M221 {' '.join(args)}",
            'M290': self._prepare_M290,
            'M292': self._prepare_M292,
            'M999': lambda args: "FIRMWARE_RESTART",
            'G10': self._prepare_G10,
            'G32': self._prepare_G32,
            'T-1': lambda args: ""
        }

    def calc_xor8(self, payload: str) -> int:
        """Calculates the standard 8-bit XOR checksum used by RRF."""
        xor = 0
        for char in payload:
            xor ^= ord(char)
        return xor

    def calc_crc16(self, payload: str) -> int:
        """Calculates CRC16-CCITT (Poly 0x1021, Init 0x0000) for modern
        PanelDue protocols."""
        crc = 0x0000
        for char in payload:
            crc ^= (ord(char) << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc <<= 1
                crc &= 0xFFFF
        return crc

    async def run_serial(self) -> None:
        """Main asynchronous serial loop handling physical UART stream communication."""
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
        """Initializes the background serial connection task via Moonraker
        server loop."""
        self.serial_task = self.event_loop.create_task(self.run_serial())

    async def _process_klippy_ready(self) -> None:
        """Handles Klippy initialization and subscribes to required printer
        object states."""
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
        self.chamber_heaters = []
        extruders = []
        for cfg in config:
            if EXTRUDER_NAME_RE.match(cfg):
                self.extruder_count += 1
                extruders.append(cfg)
                sub_args[cfg] = None
            elif cfg == "heater_bed":
                self.heaters.append(cfg)
                sub_args[cfg] = None
            elif cfg.startswith("heater_generic "):
                # Klipper's generic-heater module is what people use for a
                # chamber heater (config section "[heater_generic chamber]").
                # This was one of the original motivations for this whole
                # feature request, per the user - RRF calls this a "chamber
                # heater" and reports it via heat.chamberHeaters.
                self.heaters.append(cfg)
                self.chamber_heaters.append(cfg)
                sub_args[cfg] = None
            elif cfg.startswith("filament_switch_sensor ") or \
                    cfg.startswith("filament_motion_sensor "):
                self.filament_sensors.append(cfg)
                sub_args[cfg] = None
        extruders.sort()
        self.heaters.extend(extruders)

        # Register one T<index> gcode per configured extruder, mapped straight to
        # Klipper's ACTIVATE_EXTRUDER - modern Klipper no longer implements T0/T1/...
        # itself (removed in favor of user-defined macros), so this makes tool-select
        # buttons work out of the box for any number of extruders without requiring
        # the user to hand-write [gcode_macro Tn] sections.
        for tool_idx, ext_name in enumerate(extruders):
            self.special_gcodes[f"T{tool_idx}"] = self._make_tool_select_gcode(ext_name)

        # Detect which Klipper bed-leveling routine is configured, so the panel's
        # "Bed Leveling" button (which sends the RRF command G32) has something
        # sensible to call - Klipper has no built-in G32.
        if 'quad_gantry_level' in config:
            self.bed_level_gcode = "QUAD_GANTRY_LEVEL"
        elif 'z_tilt' in config:
            self.bed_level_gcode = "Z_TILT_ADJUST"
        elif 'bed_mesh' in config:
            self.bed_level_gcode = "BED_MESH_CALIBRATE"
        else:
            self.bed_level_gcode = ""

        try:
            await self.klippy_apis.subscribe_objects(sub_args)
        except self.server.error:
            logging.exception("Unable to complete subscription request")
        self.is_shutdown = False
        self.is_ready = True
        # Tell an already-connected panel its cached heater/tool/board model
        # is stale and must be re-fetched, in case it discovered an empty
        # model earlier (e.g. it connected while Klipper was still starting).
        self.seqs_heat += 1
        self.seqs_tools += 1
        self.seqs_boards += 1

    def _process_klippy_shutdown(self) -> None:
        """Handles emergency shutdown states from Klippy."""
        self.is_shutdown = True

    def _process_klippy_disconnect(self) -> None:
        """Resets panel state on Klippy disconnect."""
        # Tell the PD that the printer is "off"
        self.write_response({'status': 'O'})
        self.last_printer_state = 'O'
        self.is_ready = False
        self.is_shutdown = self.is_shutdown = False

    def paneldue_beep(self, frequency: int, duration: float) -> None:
        """Sends a hardware beep signal descriptor command packet to the panel."""
        duration = int(duration * 1000.)
        self.write_response(
            {'beep_freq': frequency, 'beep_length': duration})

    def process_line(self, line: str) -> None:
        """Processes raw lines from serial, parsing line numbers and
        auto-detecting variant protocols."""
        if self.verbose_logging:
            logging.info(f"PanelDue RAW INPUT: {line.strip()}")
        self.debug_queue.append(line)

        # If we find M112 in the line then skip verification
        if "M112" in line.upper():
            self.event_loop.register_callback(self.klippy_apis.emergency_stop)
            return

        line_no: Optional[int] = None
        line_index = -1
        script = line

        if self.enable_checksum:
            # Get line number
            if line.startswith('N'):
                line_index = line.find(' ')
                try:
                    line_no = int(line[1:line_index])
                    self.current_line_no = line_no
                except Exception:
                    line_index = -1
                    line_no = None

            # Verify checksum
            cs_index = line.rfind('*')
            if cs_index == -1:
                return

            checksum_str = line[cs_index+1:].strip()
            try:
                received_checksum = int(checksum_str)
            except Exception:
                # Invalid checksum, do not process
                msg = "!! Invalid Checksum"
                if line_no is not None:
                    msg += f" Line Number: {line_no}"
                logging.exception("PanelDue: " + msg)
                raise PanelDueError(msg)

            payload = line[:cs_index].strip()
            script = line[line_index+1:cs_index].strip()

            # Checksum validation cascade: two supported variants only.
            #  - VARIANT_1_LEGACY: PanelDue firmware 1.x, M408 polling,
            #    XOR8 checksum
            #  - VARIANT_4_MODERN: PanelDue firmware 3.7.0+, M409
            #    object-model queries, CRC16 checksum
            variant = "UNKNOWN"
            if "M409" in payload:
                if self.calc_crc16(payload) == received_checksum:
                    variant = "VARIANT_4_MODERN"
            elif "M408" in payload:
                if self.calc_xor8(payload) == received_checksum:
                    variant = "VARIANT_1_LEGACY"
            else:
                # Generic G-code: validate against whichever protocol was
                # already detected
                if self.detected_variant == "VARIANT_4_MODERN":
                    if self.calc_crc16(payload) == received_checksum:
                        variant = "VARIANT_4_MODERN"
                elif self.calc_xor8(payload) == received_checksum:
                    variant = (
                        self.detected_variant
                        if self.detected_variant != "UNKNOWN"
                        else "VARIANT_1_LEGACY"
                    )

            if variant != "UNKNOWN" and variant != self.detected_variant:
                self.detected_variant = variant
                logging.info(f"PanelDue: Protocol variant detected -> {variant}")

            if variant == "UNKNOWN":
                msg = "!! Invalid Checksum"
                if line_no is not None:
                    msg += f" Line Number: {line_no}"
                logging.info(f"PanelDue: {msg} - Raw line: {line}")
                raise PanelDueError(msg)

        # Smart interception layer for M409 tree discovery queries
        if "M409" in script.upper():
            if self.detected_variant == "UNKNOWN":
                self.detected_variant = "VARIANT_4_MODERN"
                logging.info(
                    "PanelDue: Fallback to VARIANT_4_MODERN for early "
                    "M409 processing"
                )

            import re
            # Extract K parameter value inside quotes or as raw word.
            # An empty/absent K addresses the object-model root (matches RRF semantics).
            arg_k = ""
            k_match = re.search(r'[kK]\s*"?([a-zA-Z0-9_\-]+)"?', script)
            if k_match:
                arg_k = k_match.group(1).strip()

            # Extract F parameter value inside quotes or as raw word
            arg_f = None
            f_match = re.search(r'[fF]\s*"?([a-zA-Z0-9_\-]+)"?', script)
            if f_match:
                arg_f = f_match.group(1).strip()

            self._run_paneldue_M409(arg_k=arg_k, arg_f=arg_f)
            return

        # Traditional command routing processing continues for M408 and standard Gcodes.
        # RRF/PanelDue allows several G/M-codes chained on one line, separated by spaces
        # (this is how the jog/move buttons send "G91 G1 Z50 F6000 G90" as a
        # single line).
        # Klipper only ever executes the first command on a line, so split any chained
        # line into individual commands before dispatching each one in order.
        # Execute the gcode.  Check for special RRF gcodes that
        # require special handling
        for sub_script in self._split_chained_commands(script):
            self._dispatch_single_command(sub_script)

    def _split_chained_commands(self, script: str) -> List[str]:
        """Splits an RRF-style multi-command line into individual commands.

        A new command starts at each token matching a G/M word (letter followed
        by a digit) - the standard G-code letters used for axis/parameter values
        (X, Y, Z, E, F, S, P, R, ...) never start with G or M, so this is a safe
        split point. T is deliberately excluded: unlike a standalone tool-change
        command (always sent on its own line, e.g. "T0"), T is also a genuine
        parameter letter on commands like "M104 T0 S180" (select which extruder)
        - treating it as a split point there breaks the S value off into its own
        bogus "T0 S180" command and drops the temperature entirely. Filenames are
        always quoted and never chained by PanelDue, so any line containing a
        quote is left untouched to avoid splitting inside one.
        """
        if '"' in script:
            return [script]
        tokens = script.split()
        if not tokens:
            return []
        commands: List[List[str]] = [[tokens[0]]]
        for tok in tokens[1:]:
            if re.match(r'^[GM]\d', tok):
                commands.append([tok])
            else:
                commands[-1].append(tok)
        return [" ".join(c) for c in commands]

    def _dispatch_single_command(self, script: str) -> None:
        """Routes one already-split G/M/T-code through direct/special gcode handling."""
        parts = script.split()
        if not parts:
            return

        cmd = parts[0].strip()
        if cmd in ["M23", "M30", "M32", "M36", "M37", "M98"]:
            arg = script[len(cmd):].strip()
            if arg:
                parts = [cmd, arg]

        # Check for commands that query state and require immediate response
        if cmd in self.direct_gcodes:
            params: Dict[str, Any] = {}
            for p in parts[1:]:
                p_clean = p.strip(" \"\t\n'")
                if not p_clean:
                    continue
                # A quoted token is always a path/filename argument, never a
                # letter parameter, even if the name starts with P/S/R/K/F.
                if p[0] in "\"'" or p_clean[0].upper() not in "PSRKPF":
                    params["arg_p"] = p_clean
                    continue
                arg = p_clean[0].lower()
                val: Any
                try:
                    if arg in "sr":
                        val = int(p_clean[1:].strip())
                    elif arg == "p" and len(p_clean) == 1:
                        val = True
                    else:
                        val = p_clean[1:].strip(" \"\t\n'")
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
        """Appends a G-code script to the queue and schedules execution."""
        self.gc_queue.append(script)
        if not self.gq_busy:
            self.gq_busy = True
            self.event_loop.register_callback(self._process_gcode_queue)

    async def _process_gcode_queue(self) -> None:
        """Asynchronously executes queued G-code scripts via Klippy API."""
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
        """Appends an internal panel command task to the command execution queue."""
        self.command_queue.append((cmd, args, kwargs))
        if not self.cq_busy:
            self.cq_busy = True
            self.event_loop.register_callback(self._process_command_queue)

    async def _process_command_queue(self) -> None:
        """Processes internal commands safely inside the async server thread."""
        while self.command_queue:
            cmd, args, kwargs = self.command_queue.pop(0)
            try:
                ret = cmd(*args, **kwargs)
                if ret is not None:
                    await ret
            except Exception:
                logging.exception("Error processing command")
        self.command_queue = []
        self.cq_busy = False

    def _clean_filename(self, filename: str) -> str:
        """Normalizes Duet/RRF file directory structures to standard Klipper syntax."""
        # Remove quotes and whitespace
        filename = filename.strip(" \"\t\n")
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
        raw_arg = args[0]
        # RRF evaluates meta-gcode expressions like {job.lastFileName} in firmware
        # before running the command - that's what the panel's "Print Again" button
        # sends. Klipper has no such expression engine, so without this substitution
        # the literal placeholder string was passed straight through as a filename.
        if "{job.lastFileName}" in raw_arg:
            last_name = self.last_file_name
            if not last_name:
                # self.last_file_name only lives in memory and resets on a
                # Moonraker restart. Klipper's own print_stats.filename
                # survives a plain Moonraker restart (it's Klipper's state,
                # not ours) - though not a full FIRMWARE_RESTART, which
                # reinitializes Klipper's objects including this one.
                last_name = self.printer_state.get(
                    'print_stats', {}).get('filename', '')
            if not last_name:
                raise PanelDueError("No previous file to print again")
            raw_arg = raw_arg.replace("{job.lastFileName}", last_name)
        filename = self._clean_filename(raw_arg)
        self.last_file_name = filename
        # Escape existing double quotes in the file name
        filename = filename.replace("\"", "\\\"")
        return f"SDCARD_PRINT_FILE FILENAME=\"{filename}\""

    def _prepare_M98(self, args: List[str]) -> str:
        """Extracts the macro name from a virtual path and triggers Klipper gcode.

        FIRMWARE_RESTART/RESTART go through the same confirmed_macros gate as
        any other macro (matching the original moonraker paneldue.py and the
        legacy M408 path exactly) - now that the modern state:messageBox
        delivery is verified correct against the real firmware source, there's
        no reason for these two to skip the confirmation dialog anymore.
        """
        macro = args[0][1:].strip(" \"\t\n")
        name_start = macro.rfind('/') + 1
        macro = macro[name_start:]
        cmd = self.available_macros.get(macro)
        if cmd is None:
            raise PanelDueError(f"Macro {macro} invalid")
        if macro in self.confirmed_macros:
            self._create_confirmation(macro, cmd)
            return ""
        if macro in RESTART_GCODES:
            # Not gated (removed from confirmed_macros by the user) - still
            # use the instant-restart path directly rather than the normal
            # gc_queue, so it isn't stuck behind a blocking M190/M109.
            if macro == "FIRMWARE_RESTART":
                self.queue_command(self._trigger_klipper_service_restart, macro)
            else:
                self.queue_command(self._trigger_soft_restart, macro)
            return ""
        return cmd

    async def _trigger_klipper_service_restart(self, macro: str) -> None:
        try:
            machine = self.server.lookup_component("machine")
            kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
            unit_name = getattr(kconn, "unit_name", "klipper")
            await machine.do_service_action("restart", unit_name)
        except self.server.error:
            logging.exception(f"PanelDue: {macro} service restart failed")

    async def _trigger_soft_restart(self, macro: str) -> None:
        try:
            await self.klippy_apis.do_restart(macro)
        except self.server.error:
            logging.exception(f"PanelDue: {macro} request failed")

    def _prepare_M220(self, args: List[str]) -> str:
        return f"M220 {args[0]}"

    def _prepare_M221(self, args: List[str]) -> str:
        return f"M221 {args[0]}"

    def _make_tool_select_gcode(self, extruder_name: str) -> Callable[[List[str]], str]:
        """Builds a T<n> handler bound to one specific extruder name."""
        def _select(args: List[str]) -> str:
            return f"ACTIVATE_EXTRUDER EXTRUDER={extruder_name}"
        return _select

    def _prepare_G32(self, args: List[str]) -> str:
        """Translates RRF's bed-leveling command (G32, runs bed.g on real firmware)
        to whichever Klipper leveling routine was detected at startup."""
        if self.bed_level_gcode:
            return self.bed_level_gcode
        logging.info(
            "PanelDue: G32 (bed leveling) requested, but no bed_mesh/quad_gantry_level/"
            "z_tilt section found in the Klipper config - ignoring."
        )
        return ""

    def _prepare_M290(self, args: List[str]) -> str:
        # args should in in the format Z0.02
        offset = args[0][1:].strip()
        return f"SET_GCODE_OFFSET Z_ADJUST={offset} MOVE=1"

    def _prepare_G10(self, args: List[str]) -> str:
        """Translates RRF tool-heater set (G10 P<tool> S<active> R<standby>)
        to Klipper's M104.

        RRF keeps separate active/standby setpoints per tool; Klipper only
        has a single target per heater, so S (active) wins when both are
        given, and R (standby) is used
        as a fallback so the standby button on the panel still does something sensible.
        """
        tool_index: Optional[int] = None
        active: Optional[float] = None
        standby: Optional[float] = None
        for a in args:
            a = a.strip()
            if not a:
                continue
            letter = a[0].upper()
            try:
                val = float(a[1:])
            except ValueError:
                continue
            if letter == 'P':
                tool_index = int(val)
            elif letter == 'S':
                active = val
            elif letter == 'R':
                standby = val

        if tool_index is None:
            # No explicit tool index (P) - fall back to the currently active extruder.
            extruder_name = self.printer_state.get(
                'toolhead', {}).get('extruder', 'extruder')
            tool_index = self._extruder_index(extruder_name) if extruder_name else 0

        temp = active if active is not None else standby
        if temp is None:
            return ""
        return f"M104 T{tool_index} S{temp}"

    def _prepare_M140(self, args: List[str]) -> str:
        """Translates RRF bed-heater set (M140 P<bed> S<active> R<standby>)
        into a Klipper M140.

        Klipper's M140 defaults S to 0 when omitted, so forwarding a bare
        "M140 P0 R25" (the panel's standby button) unmodified turns the bed
        heater off. Fold S/R into a
        single S value instead, same reasoning as _prepare_G10 above.
        """
        active: Optional[float] = None
        standby: Optional[float] = None
        for a in args:
            a = a.strip()
            if not a:
                continue
            letter = a[0].upper()
            try:
                val = float(a[1:])
            except ValueError:
                continue
            if letter == 'S':
                active = val
            elif letter == 'R':
                standby = val
            # 'P' (bed index) is accepted but ignored - Klipper only exposes heater_bed.

        temp = active if active is not None else standby
        if temp is None:
            return ""
        return f"M140 S{temp}"

    def _prepare_M141(self, args: List[str]) -> str:
        """Translates RRF chamber-heater set (M141 P<chamber> S<active> R<standby>)
        into Klipper's SET_HEATER_TEMPERATURE for a [heater_generic ...] section.

        Klipper has no dedicated chamber-heater gcode - heater_generic exposes
        its target exclusively via SET_HEATER_TEMPERATURE HEATER=<name>
        TARGET=<temp>, so P selects which configured chamber heater (index
        into self.chamber_heaters) this applies to.
        """
        chamber_index = 0
        active: Optional[float] = None
        standby: Optional[float] = None
        for a in args:
            a = a.strip()
            if not a:
                continue
            letter = a[0].upper()
            try:
                val = float(a[1:])
            except ValueError:
                continue
            if letter == 'P':
                chamber_index = int(val)
            elif letter == 'S':
                active = val
            elif letter == 'R':
                standby = val

        if not self.chamber_heaters or chamber_index >= len(self.chamber_heaters):
            raise PanelDueError(f"No chamber heater at index {chamber_index}")

        temp = active if active is not None else standby
        if temp is None:
            return ""
        # SET_HEATER_TEMPERATURE wants the bare object name ("chamber"), not
        # the full config section string ("heater_generic chamber") used as
        # the printer_state/subscription key.
        heater_name = self.chamber_heaters[chamber_index]
        if heater_name.startswith("heater_generic "):
            heater_name = heater_name[len("heater_generic "):]
        return f"SET_HEATER_TEMPERATURE HEATER={heater_name} TARGET={temp}"

    def _prepare_M292(self, args: List[str]) -> str:
        p_val = int(args[0][1])
        self.pending_msgbox = None
        if p_val == 0:
            cmd = self.confirmed_gcode
            name = self.confirmed_macro_name
            self.confirmed_gcode = ""
            self.confirmed_macro_name = ""
            if name == "FIRMWARE_RESTART":
                # Same reasoning as the unconfirmed path: route through the
                # instant service-restart, not the blockable gc_queue.
                self.queue_command(self._trigger_klipper_service_restart, name)
                return ""
            if name == "RESTART":
                self.queue_command(self._trigger_soft_restart, name)
                return ""
            return cmd
        return ""

    def _create_confirmation(self, name: str, gcode: str) -> None:
        """Arms a message-box confirmation dialog for the panel to display.

        The two protocol variants need this delivered completely differently:
        - VARIANT_1_LEGACY (M408, 1.x firmware): matches the original
          moonraker paneldue.py exactly - a flat "msgBox.mode"/"msgBox.msg"
          dict sent immediately as a standalone reply. This is untouched,
          proven behavior for real 1.x panels; do not "fix" it to look like
          the modern format below, that would break it for them.
        - VARIANT_4_MODERN (M409, 3.7.0+): PanelDue only ever reads message-
          box data from result.state.messageBox on its own dedicated
          "M409 K\"state\"" poll (verified against the actual firmware source,
          src/PanelDue.cpp fieldTable) - sending it as an ad-hoc reply to
          whatever triggered it doesn't correlate to any request PanelDue
          made, so arm it here and let the state key handler embed it.
        """
        self.mbox_sequence += 1
        self.confirmed_gcode = gcode
        self.confirmed_macro_name = name
        msg = (
            f"Please confirm your intent to run {name}. "
            "Press OK to continue, or CANCEL to abort."
        )
        if self.detected_variant == "VARIANT_4_MODERN":
            self.pending_msgbox = {
                'mode': 3,
                'msg': msg,
                'seq': self.mbox_sequence,
                'title': "Confirmation Dialog",
                'controls': 0,
                'timeout': 0
            }
            # PanelDue only re-queries the dedicated "state" key (where
            # messageBox lives) when seqs.state changes - it won't notice a
            # newly-armed dialog on its own if the printer's status hasn't
            # also changed (e.g. pressing a confirmed macro while idle).
            self.seqs_state += 1
        else:
            mbox: Dict[str, Any] = {
                'msgBox.mode': 3,
                'msgBox.msg': msg,
                'msgBox.seq': self.mbox_sequence,
                'msgBox.title': "Confirmation Dialog",
                'msgBox.controls': 0,
                'msgBox.timeout': 0
            }
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

    def write_response(self, response: Any, line_no: Optional[int] = None) -> None:
        """Calculates checksum, formats the packet with line number, and
        sends it to UART."""
        try:
            # Generate compact JSON payload without spaces
            serialized = jsonw.dumps(response).decode('utf-8')

            # Append line numbers if tracked for flow control sync
            prefix = ""
            if line_no is not None:
                prefix = f"N{line_no} "
            elif hasattr(self, 'current_line_no') and self.current_line_no is not None:
                prefix = f"N{self.current_line_no} "

            raw_out = f"{prefix}{serialized}"

            # PanelDue switched from XOR8 to CRC16 (Poly 0x1021, Init 0x0000)
            # starting with firmware 3.4.1-pre2 - but the official moonraker
            # paneldue.py never checksummed replies at all, for any version,
            # and countless real 1.x panels have run against that for years.
            # Match that exactly for the legacy path instead of guessing at
            # an XOR8 suffix it was never tested with. Only the modern path
            # (empirically confirmed to need CRC16) gets a checksum appended.
            if self.detected_variant == "VARIANT_4_MODERN":
                crc_val = self.calc_crc16(raw_out)
                final_line = f"{raw_out}*{crc_val}\r\n"
            else:
                final_line = f"{raw_out}\r\n"

            if self.verbose_logging:
                logging.info(
                    f"PanelDue DEBUG: Physical TX -> {final_line.strip()} "
                    f"[{len(final_line.encode('utf-8'))} bytes]"
                )
            self.ser_conn.send(final_line.encode('utf-8'))
        except Exception:
            logging.exception("PanelDue: Error writing response to serial transport")

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

    def _get_rrf_printer_status(self) -> str:
        """Translates Klipper print stats into standard RRF Object Model status strings.

        Verified directly against PanelDueFirmware's own source
        (src/ObjectModel/PrinterStatus.hpp, printerStatusMap[]) - the wire
        string is NOT simply the lowercase C++ enum symbol name. Notably:
          "processing" -> PrinterStatus::printing  (NOT "printing" !)
          "halted"     -> PrinterStatus::stopped   (NOT "stopped" !)
          "starting"   -> PrinterStatus::configuring
          "updating"   -> PrinterStatus::flashing
          "changingTool" -> PrinterStatus::toolChange
        idle/busy/paused/pausing/resuming/simulating/off/cancelling do match
        their enum symbol name directly. Sending "printing" (an unrecognized
        string) meant the panel's internal status silently never left
        whatever it last was - this was the root cause of the display being
        stuck all along.
        """
        if self.is_shutdown:
            return "halted"

        p_state = self.printer_state
        sd_state = p_state.get("print_stats", {}).get("state", "standby")

        if sd_state == "printing":
            return "processing"
        elif sd_state == "paused":
            return "paused"

        return "idle"

    def _check_filament_sensor_edge(self) -> bool:
        """Detects whether any configured filament sensor's detected state
        flipped since the last poll.

        Klipper never clears display_status.message between two identical
        M117 calls, so a runout macro that re-sends the exact same text on
        every trigger (remove/reinsert/remove again, or a sensor that isn't
        wired to PAUSE and so never changes the RRF status either) looked
        like "no new event" by value comparison alone and the second/third
        runout silently never reached the panel. The sensor's own
        filament_detected flag, however, genuinely toggles on every real
        trigger regardless of what the message text or print status says -
        use that as an independent, message-text-agnostic signal to force
        redelivery.
        """
        edge = False
        for name in self.filament_sensors:
            detected = bool(
                self.printer_state.get(name, {}).get('filament_detected', True))
            if self.filament_sensor_detected.get(name) != detected:
                edge = True
            self.filament_sensor_detected[name] = detected
        return edge

    @staticmethod
    def _extruder_index(name: str) -> int:
        """Parses the numeric suffix of a Klipper extruder config name:
        'extruder' -> 0, 'extruder1' -> 1, 'extruder10' -> 10, ...
        Using name[-1] instead breaks for 10+ extruders ('extruder10'[-1]
        is '0', not 10).
        """
        suffix = name[len("extruder"):]
        return int(suffix) if suffix else 0

    def _get_rrf_heater_status(self, target: float, current: float) -> str:
        """Maps heater temperature values directly to native RRF status tokens."""
        if target <= 0.0:
            return "off"
        if abs(current - target) <= 2.5:
            return "active"
        return "tuning"

    def _compute_times_left(self) -> Dict[str, float]:
        """Computes remaining print time estimates (file/filament) from slicer
        metadata and live progress. Shared by the "job" key handler (the one
        PanelDue's field table actually recognizes: job:timesLeft:file/
        filament/slicer) and the flattened d99fp poll.
        """
        p_state = self.printer_state
        print_stats = p_state.get('print_stats', {})
        sd_status = p_state.get('virtual_sdcard', {})
        sd_print_state: Optional[str] = print_stats.get('state')

        times_left_file = 0.0
        times_left_filament = 0.0

        if sd_print_state in ['printing', 'paused']:
            progress: float = sd_status.get('progress', 0.0)
            if progress:
                print_duration = print_stats.get('print_duration', 0.0)
                est_time: float = self.file_metadata.get('estimated_time', 0.0)
                if est_time > MIN_EST_TIME:
                    times_left_file = float(
                        max(0, int(est_time - est_time * progress)))
                    est_total_fil = self.file_metadata.get('filament_total')
                    if est_total_fil:
                        cur_filament: float = print_stats.get('filament_used', 0.0)
                        fpct = min(1.0, cur_filament / est_total_fil)
                        times_left_filament = float(
                            max(0, int(est_time - est_time * fpct)))
                else:
                    times_left_file = float(
                        max(0, int(print_duration / progress - print_duration)))

        return {"file": times_left_file, "filament": times_left_filament}

    def _run_paneldue_M409(self,
                           arg_k: str = "state",
                           arg_p: bool = False,
                           arg_f: Optional[str] = None
                           ) -> None:
        """Responds to modern M409 tree discovery queries matching full
        RRF specifications."""
        if self.verbose_logging:
            logging.info(
                f"PanelDue DEBUG: M409 entry point. K='{arg_k}', "
                f"P={arg_p}, F='{arg_f}'"
            )

        curtime = self.event_loop.get_loop_time()
        if curtime - self.last_update_time > INITIALIZE_TIMEOUT:
            self.initialized = False
        self.last_update_time = curtime

        # 0. Handle root model or general capability queries during handshake
        if arg_k == "model" or arg_k == "sensors" or arg_k == "gannt":
            response: Dict[str, Any] = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": {"reply": "ok"}
            }
            self.write_response(response, line_no=None)
            return

        # 1. Handle hardware/network initialization queries (Standard RRF layout)
        if arg_k == "network":
            response = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": {
                    "interfaces": [{"type": "wifi", "state": "active"}]
                }
            }
            self.write_response(response, line_no=None)
            return

        if arg_k == "boards":
            response = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": [{"name": "Duet 3 Mini 5+", "firmwareVersion": "3.7.0"}]
            }
            self.write_response(response, line_no=None)
            return

        if arg_k == "job":
            # PanelDue queries this once during discovery to learn the job-info
            # schema. It must get a properly job-shaped result here - falling
            # through to the generic full-model payload (which has "job" as the
            # requested key but an unrelated shape as its result) means PanelDue
            # can't parse it and the print-progress UI silently never appears,
            # even though the periodic flattened poll has the right numbers.
            p_state = self.printer_state
            print_stats = p_state.get('print_stats', {})
            sd_status = p_state.get('virtual_sdcard', {})
            job_times_left = self._compute_times_left()
            response = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": {
                    "file": {
                        "fileName": self.current_file,
                        "size": self.file_metadata.get('size', 0) or 0,
                        "height": self.file_metadata.get('object_height', 0.0) or 0.0,
                        "layerHeight": self.file_metadata.get(
                            'layer_height', 0.0) or 0.0,
                        "numLayers": self.file_metadata.get('layer_count', 0) or 0,
                        "generatedBy": self.file_metadata.get('slicer', '') or "",
                        "printTime": self.file_metadata.get('estimated_time', 0) or 0,
                        "simulatedTime": 0,
                        "filament": []
                    },
                    "filePosition": int(sd_status.get('file_position', 0)),
                    "lastFileName": (
                        self.last_file_name
                        or p_state.get('print_stats', {}).get('filename', '')
                    ),
                    "layer": int(
                        p_state.get('display_status', {}).get('current_layer', 0)),
                    "duration": round(print_stats.get('print_duration', 0.0), 1),
                    # PanelDue's field table only recognizes job:timesLeft:filament/
                    # file/slicer - "layer" (used elsewhere in this file) is not a
                    # key it looks for here.
                    "timesLeft": {
                        "filament": job_times_left["filament"],
                        "file": job_times_left["file"],
                        "slicer": job_times_left["file"]
                    }
                }
            }
            self.write_response(response, line_no=None)
            return

        if arg_k == "state":
            # PanelDue's standalone-mode client polls this key on its own
            # dedicated schedule (independent of the flattened d99fp poll) and
            # is where it actually looks for the message-box confirmation
            # dialog - under state:messageBox with a "message" field, not
            # output.msgBox/"msg" as the DSF/DWC-facing docs describe. Source:
            # PanelDueFirmware src/PanelDue.cpp fieldTable (rcvStateMessageBox*).
            rrf_status = self._get_rrf_printer_status()
            p_state = self.printer_state
            toolhead = p_state.get('toolhead', {})
            extruder_name = toolhead.get('extruder', '')
            current_tool = self._extruder_index(extruder_name) if extruder_name else 0
            result: Dict[str, Any] = {
                "status": rrf_status,
                "currentTool": current_tool,
                "upTime": int(time.monotonic())
            }
            if self.pending_msgbox is not None:
                mbox = self.pending_msgbox
                result["messageBox"] = {
                    "mode": mbox["mode"],
                    "message": mbox["msg"],
                    "seq": mbox["seq"],
                    "timeout": mbox["timeout"],
                    "title": mbox["title"],
                    "axisControls": 0
                }
            response = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": result
            }
            self.write_response(response, line_no=None)
            return

        if arg_k == "fans":
            # Another dedicated key PanelDue polls on its own schedule (gated
            # by seqs.fans, same pattern as state/job) - result is an array of
            # fan objects with a single recognized field, "requestedValue" (a
            # 0.0-1.0 fraction; the firmware multiplies by 100 itself for the
            # percent display). We had no handler for this at all, so it fell
            # through to the generic full-model payload and was never
            # refreshed after the first (wrong-shaped) discovery reply -
            # matching exactly the "shows the slicer's initial value forever,
            # never updates from Mainsail" symptom.
            fan_speed_now = self.printer_state.get('fan', {}).get('speed', 0.0)
            response = {
                "key": arg_k,
                "flags": arg_f if arg_f is not None else "",
                "result": [{"requestedValue": round(fan_speed_now, 2)}]
            }
            self.write_response(response, line_no=None)
            return
        p_state = self.printer_state
        rrf_status = self._get_rrf_printer_status()

        # Build heater status generically from self.heaters, so any number of
        # extruders - plus the bed - are represented instead of hardcoding one hotend.
        heater_entries: List[Dict[str, Any]] = []
        heater_index_by_name: Dict[str, int] = {}
        for h_idx, h_name in enumerate(self.heaters):
            heater_index_by_name[h_name] = h_idx
            h_state = p_state.get(h_name, {})
            h_temp: float = h_state.get('temperature', 0.0)
            h_target: float = h_state.get('target', 0.0)
            heater_entries.append({
                "current": round(h_temp, 1),
                "active": round(h_target, 1),
                "standby": round(h_target, 1),
                "state": self._get_rrf_heater_status(h_target, h_temp)
            })

        # RRF reports the bed as an index into heat.heaters via bedHeaters,
        # not as a self-contained object - mirror that here.
        bed_index = heater_index_by_name.get("heater_bed")
        bed_heaters_list = [bed_index] if bed_index is not None else []

        # Same pattern for chamber heaters (Klipper's [heater_generic ...]).
        chamber_heaters_list = [
            heater_index_by_name[name] for name in self.chamber_heaters
            if name in heater_index_by_name
        ]

        # One tool per configured extruder ("extruder", "extruder1", ...),
        # each pointing at its own slot in heat.heaters.
        extruder_heater_names = sorted(
            n for n in self.heaters if EXTRUDER_NAME_RE.match(n))
        tools_list: List[Dict[str, Any]] = []
        tools_active: List[float] = []
        tools_standby: List[float] = []
        for tool_idx, ext_name in enumerate(extruder_heater_names):
            h_idx = heater_index_by_name[ext_name]
            entry = heater_entries[h_idx]
            tools_list.append({
                "index": tool_idx,
                "number": tool_idx,
                "heaters": [h_idx],
                "extruders": [tool_idx],
                "active": [entry["active"]],
                "standby": [entry["standby"]],
                "status": entry["state"]
            })
            tools_active.append(entry["active"])
            tools_standby.append(entry["standby"])

        toolhead = p_state.get("toolhead", {})
        gcode_move = p_state.get("gcode_move", {})
        live_pos = p_state.get(
            "motion_report", {}).get('live_position', [0., 0., 0., 0.])
        homed_pos = toolhead.get('homed_axes', "")

        sfactor = round(gcode_move.get('speed_factor', 1.) * 100, 2)
        efactor = round(gcode_move.get('extrude_factor', 1.) * 100., 2)
        fan_speed = p_state.get('fan', {}).get('speed', 0.0)

        # Safely extract and format coordinates and babystepping offsets
        origin_list = gcode_move.get('homing_origin', [0., 0., 0., 0.])
        babystep_val = round(origin_list[2], 3) if len(origin_list) > 2 else 0.0

        current_tool = -1
        if self.extruder_count > 0:
            extruder_name = toolhead.get('extruder', "")
            if extruder_name:
                current_tool = self._extruder_index(extruder_name)

        # Extract print statistics, progress fraction, and remaining times
        sd_status = p_state.get('virtual_sdcard', {})
        print_stats = p_state.get('print_stats', {})
        fname: str = print_stats.get('filename', "")
        sd_print_state: Optional[str] = print_stats.get('state')

        fraction_printed = 0.0
        print_duration = 0.0
        times_left_file = 0.0
        times_left_filament = 0.0
        times_left_layer = 0.0

        if sd_print_state in ['printing', 'paused']:
            if self.current_file != fname:
                self.current_file = fname
                self.file_metadata = self.file_manager.get_file_metadata(fname)

            progress: float = sd_status.get('progress', 0.0)
            if progress:
                fraction_printed = round(progress * 100.0, 1)
                print_duration = round(print_stats.get('print_duration', 0.0), 1)
                est_time: float = self.file_metadata.get('estimated_time', 0.0)

                if est_time > MIN_EST_TIME:
                    times_left_file = float(
                        max(0, int(est_time - est_time * progress)))
                    est_total_fil = self.file_metadata.get('filament_total')
                    if est_total_fil:
                        cur_filament: float = print_stats.get('filament_used', 0.0)
                        fpct = min(1.0, cur_filament / est_total_fil)
                        times_left_filament = float(
                            max(0, int(est_time - est_time * fpct)))
                else:
                    times_left_file = float(
                        max(0, int(print_duration / progress - print_duration)))

                times_left_layer = times_left_file
        else:
            self.current_file = ""
            self.file_metadata = {}

        geom_str = (
            "coreXY" if self.kinematics == "corexy"
            else (self.kinematics if self.kinematics != "none" else "cartesian")
        )

        # Increment the modern M409 sequence counter with every single transaction
        self.m409_sequence = (self.m409_sequence + 1) & 0xFFFF

        # Build deep RRF v3 ObjectModel compatible nested payload structure.
        # Machine-identity fields (name/geometry/firmware/etc.) never change during
        # Send these on every poll, not just the first one. We previously only
        # sent them once (to shrink the message, on a since-disproven buffer-
        # overflow theory - even a ~325 byte minimal test payload didn't fix
        # the status/progress issue), which meant they could be genuinely
        # inconsistent from what real RRF does. No longer worth the risk now
        # that a different root cause (CRC16 on outgoing replies) looks likely.
        result_payload: Dict[str, Any] = {
            "firmwareName": "RepRapFirmware for Klipper",
            "firmwareVersion": "3.7.0",
            "geometry": geom_str,
            "totalAxes": 3,
            "volumes": 1,
            "mountedVolumes": 1,
            "mode": "FFF",
            "name": self.machine_name,
            "coldExtrudeTemp": 160.0,
            "coldRetractTemp": 90.0,
            "compensation": "None",
            "tempLimit": 290.0,
        }
        if self.debug_minimal_response:
            # Diagnostic build: strip everything not needed to test whether
            # progress/messages appear once the message is drastically shorter.
            # Heat/tools/temps/coords/speeds/sensors are dropped entirely here -
            # do not leave this enabled for normal use, bed/hotend display and
            # jog position will not work while it's on.
            result_payload.update({
                "status": rrf_status,
                "axes": [{"letter": "X", "homed": "x" in homed_pos, "visible": True},
                         {"letter": "Y", "homed": "y" in homed_pos, "visible": True},
                         {"letter": "Z", "homed": "z" in homed_pos, "visible": True}],
                "fractionPrinted": fraction_printed,
                "filePosition": int(sd_status.get('file_position', 0)),
                "printDuration": print_duration,
                "currentTool": current_tool,
                "params": {"seq": self.m409_sequence},
                "time": curtime,
                "seq": self.m409_sequence
            })
        else:
            result_payload.update({
                "status": rrf_status,
                "axes": [{"letter": "X", "homed": "x" in homed_pos, "visible": True},
                         {"letter": "Y", "homed": "y" in homed_pos, "visible": True},
                         {"letter": "Z", "homed": "z" in homed_pos, "visible": True}],

                "currentLayer": int(
                    p_state.get('display_status', {}).get('current_layer', 0)),
                "extrRaw": [round(print_stats.get('filament_used', 0.0), 1)],
                "fractionPrinted": fraction_printed,
                "filePosition": int(sd_status.get('file_position', 0)),
                "printDuration": print_duration,
                "timesLeft": {
                    "file": times_left_file,
                    "filament": times_left_filament,
                    "layer": times_left_layer
                },

                "coords": {
                    "axesHomed": [
                        int("x" in homed_pos),
                        int("y" in homed_pos),
                        int("z" in homed_pos)
                    ],
                    "wpl": 1,
                    "xyz": [round(p, 3) for p in live_pos[:3]],
                    "machine": [round(p, 3) for p in live_pos[:3]],
                    "extr": [round(live_pos[3], 3) if len(live_pos) > 3 else 0.0]
                },
                "speeds": {
                    "requested": round(gcode_move.get('speed', 0.0) / 60.0, 1),
                    "top": round(gcode_move.get('speed', 0.0) / 60.0, 1)
                },
                "currentTool": current_tool,
                "params": {
                    "atxPower": 1,
                    "fanPercent": [round(fan_speed * 100, 1)],
                    "speedFactor": round(sfactor, 1),
                    "extrFactors": [round(efactor, 1)],
                    "babystep": babystep_val,
                    # FIXED: Send dynamic shifting sequence inside params
                    "seq": self.m409_sequence
                },
                "sensors": {
                    "probeValue": 0,
                    "fanRPM": [-1]
                },
                "heat": {
                    "bedHeaters": bed_heaters_list,
                    "chamberHeaters": chamber_heaters_list,
                    "heaters": heater_entries
                },
                "tools": tools_list,
                "temps": {
                    **({
                        "bed": {
                            "current": heater_entries[bed_index]["current"],
                            "active": heater_entries[bed_index]["active"],
                            "standby": heater_entries[bed_index]["standby"],
                            "state": heater_entries[bed_index]["state"],
                            "heater": bed_index
                        }
                    } if bed_index is not None else {}),
                    "current": [h["current"] for h in heater_entries],
                    "state": [h["state"] for h in heater_entries],
                    "tools": {
                        "active": tools_active,
                        "standby": tools_standby
                    }
                },
                "time": curtime,
                "seq": self.m409_sequence
            })

        # M117 status messages are recognized by PanelDue's parser as a flat
        # top-level "message" field (source: PanelDueFirmware fieldTable entry
        # rcvPushMessage -> "message") - not nested under "output", which was
        # based on the DSF/DWC-facing docs and doesn't apply to this standalone
        # serial protocol. The message-box confirmation dialog is delivered
        # separately via the dedicated "state" key handler above.
        #
        # Klipper never clears display_status.message between two identical
        # M117 calls (e.g. a filament-runout macro that always sends the same
        # text), so comparing only against the last text misses genuine
        # repeat events - the second runout never got redelivered. Also
        # redeliver whenever the print status just changed (idle/printing <->
        # paused etc.), since that's what actually accompanies a new
        # runout/error condition even when the message text is unchanged.
        status_changed = rrf_status != self.last_job_status
        sensor_edge = self._check_filament_sensor_edge()
        m117_msg: str = p_state.get('display_status', {}).get('message', "")
        msg_changed = m117_msg != self.last_message or status_changed or sensor_edge
        if m117_msg and msg_changed:
            result_payload["message"] = m117_msg
            self.seqs_reply += 1
        self.last_message = m117_msg

        # Bump seqs.job whenever print progress or job state actually moved on,
        # so PanelDue has a reason to refresh the job/progress screen. Bump
        # seqs.state the same way - PanelDue only re-queries the dedicated
        # "state" key (where it reads its actual displayed status from, see
        # the state key handler above) when this counter changes; leaving it
        # fixed meant PanelDue queried "state" exactly once at connect time
        # and then never learned the status changed away from "idle".
        if fraction_printed != self.last_fraction_printed or status_changed:
            self.seqs_job += 1
        if status_changed:
            self.seqs_state += 1
        # Same story for seqs.fans - a fan speed change made anywhere other
        # than the panel itself (Mainsail, a macro, the slicer's startup
        # M106) needs this counter to move or PanelDue never re-queries
        # "fans" and keeps showing whatever value it last saw.
        current_fan_speed = p_state.get('fan', {}).get('speed', 0.0)
        if current_fan_speed != self.last_fan_speed:
            self.seqs_fans += 1
        self.last_fan_speed = current_fan_speed
        self.last_fraction_printed = fraction_printed
        self.last_job_status = rrf_status
        result_payload["seqs"] = {
            "state": self.seqs_state,
            "network": 1,
            "boards": self.seqs_boards,
            "job": self.seqs_job,
            "move": 1,
            "heat": self.seqs_heat,
            "tools": self.seqs_tools,
            "volumes": 1,
            "fans": self.seqs_fans,
            "reply": self.seqs_reply
        }

        self.initialized = True

        # Response key mirrors the queried object-model path (arg_k), flags mirror
        # the requested filter string (arg_f) - PanelDue matches "key" against what
        # it asked for to correlate the response with the pending request.
        response = {
            "key": arg_k,
            "flags": arg_f if arg_f is not None else "",
            "result": result_payload
        }
        self.write_response(response, line_no=None)

    def _run_paneldue_M408(self, arg_r: Optional[int] = None, arg_s: int = 1) -> None:
        """Handles legacy M408 status polling queries with enhanced bitmask updates."""
        sequence = arg_r
        response_type = arg_s

        curtime = self.event_loop.get_loop_time()
        if curtime - self.last_update_time > INITIALIZE_TIMEOUT:
            self.initialized = False
        self.last_update_time = curtime

        response: Dict[str, Any] = {}
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

        origin_list = gcode_move.get('homing_origin', [0., 0., 0., 0.])
        babystep_val = round(origin_list[2], 3) if len(origin_list) > 2 else 0.0
        response['babystep'] = babystep_val

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

        extruder_name = toolhead.get('extruder', "")
        if self.extruder_count > 0 and extruder_name:
            response['tool'] = self._extruder_index(extruder_name)

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
        sensor_edge = self._check_filament_sensor_edge()
        if msg and (msg != self.last_message or sensor_edge):
            response['message'] = msg
            # reset the message so it only shows once.  The paneldue
            # is strange about this, and displays it as a full screen
            # notification
        self.last_message = msg

        if self.detected_variant == "VARIANT_4_MODERN":
            xyz_mask = 0
            if "x" in homed_pos:
                xyz_mask |= 1
            if "y" in homed_pos:
                xyz_mask |= 2
            if "z" in homed_pos:
                xyz_mask |= 4

            response.update({
                "babystep": babystep_val,
                "tool": response.get('tool', 0),
                "sfactor": int(sfactor),
                "xyzFlags": xyz_mask,
                "geometry": self.kinematics,
                "volumes": 1,
                "files": response.get('files', [])
            })

        self.write_response(response)

    def _run_paneldue_M20(self, arg_p: str, arg_s: int = 0, arg_r: int = 0) -> None:
        """Lists available print files or virtual macro directories."""
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
        # PanelDue requires first/next/err in addition to dir/files, or it treats
        # the response as failed and shows an empty directory instead of the list.
        response: Dict[str, Any] = {
            'dir': path, 'first': arg_r, 'files': [], 'next': 0, 'err': 0
        }

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
            try:
                flist = self.file_manager.list_dir(path, simple_format=True)
            except Exception:
                logging.exception(f"PanelDue: Error listing directory '{path}'")
                response['err'] = 1
                flist = None
            if flist:
                response['files'] = flist
        self.write_response(response)

    async def _run_paneldue_M30(self, arg_p: str = "") -> None:
        """Deletes a selected G-code file through Moonraker file manager."""
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
        """Returns standard file metadata for current or queried print file."""
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

        try:
            metadata: Dict[str, Any] = self.file_manager.get_file_metadata(filename)
        except Exception:
            metadata = {}

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
                if self.detected_variant == "VARIANT_4_MODERN":
                    response['objectHeight'] = round(height, 2)
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
        """Closes async serial port transport resources on component destruction."""
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
