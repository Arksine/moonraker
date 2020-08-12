# PanelDue LCD display support
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import serial
import os
import time
import json
import errno
import logging
from utils import ServerError
from tornado import gen
from tornado.ioloop import IOLoop
from tornado.locks import Lock

MIN_EST_TIME = 10.

class PanelDueError(ServerError):
    pass

class SerialConnection:
    def __init__(self, config, paneldue):
        self.ioloop = IOLoop.current()
        self.paneldue = paneldue
        self.port = config.get('serial')
        self.baud = config.getint('baud', 57600)
        self.sendlock = Lock()
        self.partial_input = b""
        self.ser = self.fd = None
        self.connected = False
        self.ioloop.spawn_callback(self._connect)

    def disconnect(self):
        if self.connected:
            if self.fd is not None:
                self.ioloop.remove_handler(self.fd)
                self.fd = None
            self.connected = False
            self.ser.close()
            self.ser = None
            logging.info("PanelDue Disconnected")

    async def _connect(self):
        start_time = connect_time = time.time()
        while not self.connected:
            if connect_time > start_time + 30.:
                logging.info("Unable to connect, aborting")
                break
            logging.info(f"Attempting to connect to: {self.port}")
            try:
                # XXX - sometimes the port cannot be exclusively locked, this
                # would likely be due to a restart where the serial port was
                # not correctly closed.  Maybe don't use exclusive mode?
                self.ser = serial.Serial(
                    self.port, self.baud, timeout=0, exclusive=True)
            except (OSError, IOError, serial.SerialException):
                logging.exception(f"Unable to open port: {self.port}")
                await gen.sleep(2.)
                connect_time += time.time()
                continue
            self.fd = self.ser.fileno()
            os.set_blocking(self.fd, False)
            self.ioloop.add_handler(
                self.fd, self._handle_incoming, IOLoop.READ | IOLoop.ERROR)
            self.connected = True
            logging.info("PanelDue Connected")

    def _handle_incoming(self, fd, events):
        if events & IOLoop.ERROR:
            logging.info("PanelDue Connection Error")
            self.disconnect()
            return
        # Process incoming data using same method as gcode.py
        try:
            data = os.read(fd, 4096)
        except os.error:
            return

        if not data:
            # possibly an error, disconnect
            self.disconnect()
            logging.info("serial_display: No data received, disconnecting")
            return
        self.ioloop.spawn_callback(self._process_data, data)

    async def _process_data(self, data):
        # Remove null bytes, separate into lines
        data = data.strip(b'\x00')
        lines = data.split(b'\n')
        lines[0] = self.partial_input + lines[0]
        self.partial_input = lines.pop()
        for line in lines:
            line = line.strip().decode()
            try:
                await self.paneldue.process_line(line)
            except ServerError:
                logging.exception(
                    "GCode Processing Error: " + line)
                self.paneldue.handle_gcode_response(
                    "!! GCode Processing Error: " + line)
            except Exception:
                logging.exception("Error during gcode processing")

    async def send(self, data):
        if self.connected:
            async with self.sendlock:
                while data:
                    try:
                        sent = os.write(self.fd, data)
                    except os.error as e:
                        if e.errno == errno.EBADF or e.errno == errno.EPIPE:
                            sent = 0
                        else:
                            await gen.sleep(.001)
                            continue
                    if sent:
                        data = data[sent:]
                    else:
                        logging.exception(
                            "Error writing data, closing serial connection")
                        self.disconnect()
                        return


class PanelDue:
    def __init__(self, config):
        self.server = config.get_server()
        self.ioloop = IOLoop.current()
        self.ser_conn = SerialConnection(config, self)
        self.file_manager = self.server.lookup_plugin('file_manager')
        self.kinematics = "none"
        self.machine_name = config.get('machine_name', "Klipper")
        self.firmware_name = "Repetier | Klipper"
        self.last_message = None
        self.last_gcode_response = None
        self.current_file = ""
        self.file_metadata = {}

        # Initialize tracked state.
        self.printer_state = {
            'gcode': {}, 'toolhead': {}, 'virtual_sdcard': {},
            'fan': {}, 'display_status': {}, 'print_stats': {}}
        self.extruder_count = 0
        self.heaters = []
        self.is_ready = False
        self.is_shutdown = False
        self.last_printer_state = 'C'

        # Set up macros
        self.available_macros = {}
        macros = config.get('macros', None)
        if macros is not None:
            # The macro's configuration name is the key, whereas the full
            # command is the value
            macros = [m for m in macros.split('\n') if m.strip()]
            self.available_macros = {m.split()[0]: m for m in macros}

        ntkeys = config.get('non_trivial_keys', "Klipper state")
        self.non_trivial_keys = [k for k in ntkeys.split('\n') if k.strip()]
        logging.info("PanelDue Configured")

        # Register server events
        self.server.register_event_handler(
            "server:klippy_state_changed", self.handle_klippy_state)
        self.server.register_event_handler(
            "server:status_update", self.handle_status_update)
        self.server.register_event_handler(
            "server:gcode_response", self.handle_gcode_response)

        self.server.register_remote_method(
            "paneldue_beep", self.handle_paneldue_beep)

        # These commands are directly executued on the server and do not to
        # make a request to Klippy
        self.direct_gcodes = {
            'M20': self._run_paneldue_M20,
            'M30': self._run_paneldue_M30,
            'M36': self._run_paneldue_M36,
            'M408': self._run_paneldue_M408
        }

        # These gcodes require special parsing or handling prior to being
        # sent via Klippy's "gcode/script" api command.
        self.special_gcodes = {
            'M0': lambda args: "CANCEL_PRINT",
            'M23': self._prepare_M23,
            'M24': lambda args: "RESUME",
            'M25': lambda args: "PAUSE",
            'M32': self._prepare_M32,
            'M98': self._prepare_M98,
            'M120': lambda args: "SAVE_GCODE_STATE STATE=PANELDUE",
            'M121': lambda args: "RESTORE_GCODE_STATE STATE=PANELDUE",
            'M290': self._prepare_M290,
            'M999': lambda args: "FIRMWARE_RESTART"
        }

    async def _klippy_request(self, command, method='GET', args={}):
        try:
            result = await self.server.make_request(command, method, args)
        except self.server.error as e:
            raise PanelDueError(str(e)) from e
        return result

    async def handle_klippy_state(self, state):
        # XXX - Add a "connected" state and send a "C" to paneldue?
        if state == "ready":
            await self._process_klippy_ready()
        elif state == "shutdown":
            await self._process_klippy_shutdown()
        elif state == "disconnect":
            await self._process_klippy_disconnect()

    async def _process_klippy_ready(self):
        # Request "info" and "configfile" status
        retries = 10
        printer_info = cfg_status = {}
        while retries:
            try:
                printer_info = await self._klippy_request("info")
                cfg_status = await self._klippy_request(
                    "objects/status", args={'configfile': []})
            except PanelDueError:
                logging.exception("PanelDue initialization request failed")
                retries -= 1
                if not retries:
                    raise
                await gen.sleep(1.)
                continue
            break

        self.firmware_name = "Repetier | Klipper " + printer_info['version']
        config = cfg_status.get('configfile', {}).get('config', {})
        printer_cfg = config.get('printer', {})
        self.kinematics = printer_cfg.get('kinematics', "none")

        logging.info(
            f"PanelDue Config Received:\n"
            f"Firmware Name: {self.firmware_name}\n"
            f"Kinematics: {self.kinematics}\n"
            f"Printer Config: {config}\n")

        # Initalize printer state and make subscription request
        self.printer_state = {
            'gcode': {}, 'toolhead': {}, 'virtual_sdcard': {},
            'fan': {}, 'display_status': {}, 'print_stats': {}}
        sub_args = {k: [] for k in self.printer_state.keys()}
        self.extruder_count = 0
        self.heaters = []
        for cfg in config:
            if cfg.startswith("extruder"):
                self.extruder_count += 1
                self.printer_state[cfg] = {}
                self.heaters.append(cfg)
                sub_args[cfg] = []
            elif cfg == "heater_bed":
                self.printer_state[cfg] = {}
                self.heaters.append(cfg)
                sub_args[cfg] = []
        try:
            await self._klippy_request(
                "objects/subscription", method='POST', args=sub_args)
        except PanelDueError:
            logging.exception("Unable to complete subscription request")
        self.is_shutdown = False
        self.is_ready = True

    async def _process_klippy_shutdown(self):
        self.is_shutdown = True

    async def _process_klippy_disconnect(self):
        # Tell the PD that we are shutting down
        await self.write_response({'status': 'S'})
        self.is_ready = False

    async def handle_status_update(self, status):
        self.printer_state.update(status)

    def handle_paneldue_beep(self, frequency, duration):
        duration = int(duration * 1000.)
        self.ioloop.spawn_callback(
            self.write_response,
            {'beep_freq': frequency, 'beep_length': duration})

    async def process_line(self, line):
        # If we find M112 in the line then skip verification
        if "M112" in line.upper():
            await self._klippy_request("emergency_stop", method='POST')
            return

        # Get line number
        line_index = line.find(' ')
        try:
            line_no = int(line[1:line_index])
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

        await self._run_gcode(line[line_index+1:cs_index])

    async def _run_gcode(self, script):
        # Execute the gcode.  Check for special RRF gcodes that
        # require special handling
        parts = script.split()
        cmd = parts[0].strip()

        # Check for commands that query state and require immediate response
        if cmd in self.direct_gcodes:
            params = {}
            for p in parts[1:]:
                arg = p[0].lower() if p[0].lower() in "psr" else "p"
                try:
                    val = int(p[1:].strip()) if arg in "sr" else p[1:].strip()
                except Exception:
                    msg = f"paneldue: Error parsing direct gcode {script}"
                    self.handle_gcode_response("!! " + msg)
                    logging.exception(msg)
                    return
                params["arg_" + arg] = val
            func = self.direct_gcodes[cmd]
            await func(**params)
            return

        # Prepare GCodes that require special handling
        if cmd in self.special_gcodes:
            func = self.special_gcodes[cmd]
            script = func(parts[1:])

        try:
            args = {'script': script}
            await self._klippy_request(
                "gcode/script", method='POST', args=args)
        except PanelDueError:
            msg = f"Error executing script {script}"
            self.handle_gcode_response("!! " + msg)
            logging.exception(msg)

    def _clean_filename(self, filename):
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

    def _prepare_M23(self, args):
        filename = self._clean_filename(args[0].strip())
        return "M23 " + filename

    def _prepare_M32(self, args):
        filename = self._clean_filename(args[0].strip())
        return "SDCARD_PRINT_FILE FILENAME=" + filename

    def _prepare_M98(self, args):
        macro = args[0][1:].strip()
        name_start = macro.rfind('/') + 1
        macro = macro[name_start:]
        cmd = self.available_macros.get(macro)
        if cmd is None:
            raise PanelDueError(f"Macro {macro} invalid")
        return cmd

    def _prepare_M290(self, args):
        # args should in in the format Z0.02
        offset = args[0][1:].strip()
        return f"SET_GCODE_OFFSET Z_ADJUST={offset} MOVE=1"

    def handle_gcode_response(self, response):
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

    async def write_response(self, response):
        byte_resp = json.dumps(response) + "\r\n"
        await self.ser_conn.send(byte_resp.encode())

    def _get_printer_status(self):
        # PanelDue States applicable to Klipper:
        # I = idle, P = printing from SD, S = stopped (shutdown),
        # C = starting up (not ready), A = paused, D = pausing,
        # B = busy
        if self.is_shutdown:
            return 'S'

        printer_state = self.printer_state
        th_busy = printer_state['toolhead'].get(
            'status', 'Ready') == "Printing"
        sd_state = printer_state['print_stats'].get('state', "standby")
        if sd_state == "printing":
            if self.last_printer_state == 'A':
                # Resuming
                return 'R'
            # Printing
            return 'P'
        elif sd_state == "paused":
            if th_busy and self.last_printer_state != 'A':
                # Pausing
                return 'D'
            else:
                # Paused
                return 'A'

        if th_busy:
            # Printer is "busy"
            return 'B'

        return 'I'

    async def _run_paneldue_M408(self, arg_r=None, arg_s=1):
        response = {}
        sequence = arg_r
        response_type = arg_s

        if not self.is_ready:
            # Klipper is still starting up, do not query status
            self.last_printer_state = 'S' if self.is_shutdown else 'C'
            response['status'] = self.last_printer_state
            await self.write_response(response)
            return

        # Send gcode responses
        if sequence is not None and self.last_gcode_response:
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
        self.last_printer_state = self._get_printer_status()
        response['status'] = self.last_printer_state
        response['babystep'] = round(p_state['gcode'].get(
            'homing_zpos', 0.), 3)

        # Current position
        pos = p_state['toolhead'].get('position', [0., 0., 0., 0.])
        response['pos'] = [round(p, 2) for p in pos[:3]]
        homed_pos = p_state['toolhead'].get('homed_axes', "")
        response['homed'] = [int(a in homed_pos) for a in "xyz"]
        sfactor = round(p_state['gcode'].get('speed_factor', 1.) * 100, 2)
        response['sfactor'] = sfactor

        # Print Progress Tracking
        sd_status = p_state['virtual_sdcard']
        print_stats = p_state['print_stats']
        fname = print_stats.get('filename', "")
        sd_print_state = print_stats.get('state')
        if sd_print_state in ['printing', 'paused']:
            # We know a file has been loaded, initialize metadata
            if self.current_file != fname:
                self.current_file = fname
                self.file_metadata = self.file_manager.get_file_metadata(fname)
            progress = sd_status.get('progress', 0)
            # progress and print tracking
            if progress:
                response['fraction_printed'] = round(progress, 3)
                est_time = self.file_metadata.get('estimated_time', 0)
                if est_time > MIN_EST_TIME:
                    # file read estimate
                    times_left = [int(est_time - est_time * progress)]
                    # filament estimate
                    est_total_fil = self.file_metadata.get('filament_total')
                    if est_total_fil:
                        cur_filament = print_stats.get('filament_used', 0.)
                        fpct = min(1., cur_filament / est_total_fil)
                        times_left.append(int(est_time - est_time * fpct))
                    # object height estimate
                    obj_height = self.file_metadata.get('object_height')
                    if obj_height:
                        cur_height = p_state['gcode'].get('move_zpos', 0.)
                        hpct = min(1., cur_height / obj_height)
                        times_left.append(int(est_time - est_time * hpct))
                else:
                    # The estimated time is not in the metadata, however we
                    # can still provide an estimate based on file progress
                    duration = print_stats.get('print_duration', 0.)
                    times_left = [int(duration / progress - duration)]
                response['timesLeft'] = times_left
        else:
            # clear filename and metadata
            self.current_file = ""
            self.file_metadata = {}

        fan_speed = p_state['fan'].get('speed')
        if fan_speed is not None:
            response['fanPercent'] = [round(fan_speed * 100, 1)]

        if self.extruder_count > 0:
            extruder_name = p_state['toolhead'].get('extruder')
            if extruder_name is not None:
                tool = 0
                if extruder_name != "extruder":
                    tool = int(extruder_name[-1])
                response['tool'] = tool

        # Report Heater Status
        efactor = round(p_state['gcode'].get('extrude_factor', 1.) * 100., 2)

        for name in self.heaters:
            temp = round(p_state[name].get('temperature', 0.0), 1)
            target = round(p_state[name].get('target', 0.0), 1)
            response.setdefault('heaters', []).append(temp)
            response.setdefault('active', []).append(target)
            response.setdefault('standby', []).append(target)
            response.setdefault('hstat', []).append(2 if target else 0)
            if name.startswith('extruder'):
                response.setdefault('efactor', []).append(efactor)
                response.setdefault('extr', []).append(round(pos[3], 2))

        # Display message (via M117)
        msg = p_state['display_status'].get('message')
        if msg and msg != self.last_message:
            response['message'] = msg
            # reset the message so it only shows once.  The paneldue
            # is strange about this, and displays it as a full screen
            # notification
        self.last_message = msg
        await self.write_response(response)

    async def _run_paneldue_M20(self, arg_p, arg_s=0):
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
        response = {'dir': path}
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
        await self.write_response(response)

    async def _run_paneldue_M30(self, arg_p=None):
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
        self.file_manager.delete_file(path)

    async def _run_paneldue_M36(self, arg_p=None):
        response = {}
        filename = arg_p
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
                await self.write_response(response)
                return
            else:
                response['fileName'] = filename.split("/")[-1]


        # For consistency make sure that the filename begins with the
        # "gcodes/" root.  The M20 HACK should add this in some cases.
        # Ideally we would add support to the PanelDue firmware that
        # indicates Moonraker supports a "gcodes" directory.
        if not filename.startswith("gcodes/"):
            filename = "gcodes/" + filename

        metadata = self.file_manager.get_file_metadata(filename)
        if metadata:
            response['err'] = 0
            response['size'] = metadata['size']
            # workaround for PanelDue replacing the first "T" found
            response['lastModified'] = "T" + metadata['modified']
            slicer = metadata.get('slicer')
            if slicer is not None:
                response['generatedBy'] = slicer
            height = metadata.get('object_height')
            if height is not None:
                response['height'] = round(height, 2)
            layer_height = metadata.get('layer_height')
            if layer_height is not None:
                response['layerHeight'] = round(layer_height, 2)
            filament = metadata.get('filament_total')
            if filament is not None:
                response['filament'] = [round(filament, 1)]
            est_time = metadata.get('estimated_time')
            if est_time is not None:
                response['printTime'] = int(est_time + .5)
        else:
            response['err'] = 1
        await self.write_response(response)

    async def close(self):
        self.ser_conn.disconnect()

def load_plugin(config):
    return PanelDue(config)
