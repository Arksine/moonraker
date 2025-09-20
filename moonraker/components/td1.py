# Support for interfacing with TD-1 devices to assist in filament changers grabbing TD
# and color for all filament loaded.
#
# Copyright (C) 2025 AJAX3D and Jim Madill <jcmadill1@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import serial
import serial.tools.list_ports
import logging
import datetime

from ..common import RequestType
from ..common import WebRequest
from ..utils import async_serial

class TD1Protocol(asyncio.Protocol):
    def __init__(self, config, serial_number, port, logger):
        self.config = config
        self.server = config.get_server()
        self.serial_number = serial_number
        # Setting port in config file
        self.config.source.config.set('td1', 'serial', port)
        self.logger = logger
        self.buffer = ""
        self.error_state = False
        self.serial = async_serial.AsyncSerialConnection(config,
                                                         config.get("baudrate", 115200))
        self.serial_task = None
        self.enabled = True

        # Values to return back for td1_data endpoint
        self._td = None
        self._color = None
        self._scan_time = datetime.datetime.utcnow().isoformat() + "Z"
        self._error = None
        self.done_initializing = False

    async def initialize(self) -> None:
        self.serial_task = self.server.get_event_loop().create_task(self.run_serial())

        for _ in range(5):
            await asyncio.sleep(.01)
            if self.serial.connected:
                break

    async def run_serial(self) -> None:
        try:
            self.serial.open(timeout=10)
        except (self.serial.error, OSError):
            self.error_state = True
            self.logger.exception(
                f"Error trying to open TD1 serial: {self.serial_number}"
            )
            return

        await self.check_error_file()

        self.done_initializing = True

        while self.enabled:
            async for line in self.serial.reader:
                self.buffer += line.decode("utf-8")
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self._parse_line(line)
                        self.logger.debug(f"Serial: {self.serial_number} Line: {line}")

            if self.enabled:
                await asyncio.sleep(0.5)

    def _parse_line(self, line):
        try:
            parts = line.split(",")
            if len(parts) >= 6:
                self._td = float(parts[4])
                self._color = parts[5].strip()
                self._scan_time = datetime.datetime.utcnow().isoformat() + "Z"
        except Exception as e:
            self.logger.error(f"Parse error from {self.serial_number}: {e}")

    async def check_error_file(self):
        """
        Requests error file from TD1

        :return: Returns empty string if no error.txt file exists, returns error.txt
                 content if it exists so error can be displayed to user.
        """
        file_name = "errors.txt"

        # Wait for acknowledgment from the Raspberry Pi Pico
        ack = ''
        while ack != 'ready' and ack != 'No file named errors.txt':
            # Send the file request command
            await self.serial.send(b'retrieve file\n')
            await self.serial.send(f"{file_name}\n".encode('utf-8'))
            line = await self.serial.reader.readuntil(b'\n')
            ack = line.decode('utf-8').strip()

        if ack == 'No file named errors.txt':
            return

        # Receive file size and block count
        data = await self.serial.reader.readline()
        file_size = int(data.decode('utf-8').strip())
        data = await self.serial.reader.readline()
        blk_count = int(data.decode('utf-8').strip())

        self.logger.info(
            f"Receiving file: {file_name}, Size: {file_size} bytes, Blocks: {blk_count}"
        )

        remaining_bytes = file_size
        block_content = ''
        for _ in range(blk_count):
            # Receive a block size
            data = await self.serial.reader.readuntil(b'\n')
            block_size = int(data.decode('utf-8').strip())

            # Receive the block content
            data = await self.serial.reader.readuntil(b'\n')
            block_content += data.decode()

            remaining_bytes -= block_size

            # Send acknowledgment to the Raspberry Pi Pico
            await self.serial.send(b'ready\n')

        self.logger.info(f"Error file content: {block_content}")
        self.logger.info(f"File '{file_name}' received successfully.")
        self._error = block_content

    async def close(self):
        self.enabled = False
        await self.serial.close()
        try:
            if self.serial_task is not None or not self.serial_task.done():
                await self.serial_task
        except asyncio.CancelledError:
            self.logger.debug(f"TD1 {self.serial_number} task was already canceled")

    async def reboot(self):
        self.logger.info(f"Rebooting {self.serial_number}")
        self.enabled = False
        self.serial_task.cancel()
        await self.serial.send('change settings\n'.encode('utf-8'))

        ack = await self.serial.reader.readuntil(b'\n')
        ack = ack.decode('utf-8').strip()
        if ack == 'ready':
            await self.serial.send(b'RGB_Enabled = True\ndone\n')

        ack = await self.serial.reader.readuntil(b'\n')
        ack = ack.decode('utf-8').strip()

    def get_data(self):
        if not self.done_initializing:
            return {}
        return {
            self.serial_number: {
                "td": self._td,
                "color": self._color,
                "scan_time": self._scan_time,
                "error": self._error
            }
        }

class TD1:
    def __init__(self, config):
        self.server = config.get_server()
        self._config = config
        self._loop = self.server.get_event_loop()
        self.logger = logging.getLogger("td1")
        self._server = config.get_server()
        self._baudrate = int(config.get('baudrate', 115200))
        self._known_serials = []
        self._vid = 0xE4B2
        self._pid = 0x0045
        self._enabled = True
        self._watch_task = None
        self._start_task = None
        self.serial_tasks = {}

        self._register_endpoints()

    def _register_endpoints(self):
        self._server.register_endpoint(
            "/machine/td1_data", RequestType.GET, self._handle_get_data
        )
        self._server.register_endpoint(
            "/machine/td1_reboot", RequestType.POST, self._handle_reboot
        )

    async def close_device(self, device):
        ret = device.close()
        if ret is not None:
            await ret

    async def close(self):
        # Stop watching for new devices
        self._enabled = False
        if self._start_task is not None:
            await self._start_task

        if self._watch_task is not None:
            await self._watch_task

        for td1 in self.serial_tasks.values():
            await self.close_device(td1)

    async def component_init(self):
        self.logger.debug("Initializing TD1")
        self._start_task = self._loop.create_task(self._start_all_serial_tasks())
        self._watch_task = self._loop.create_task(self._watch_for_new_devices())

    async def _start_all_serial_tasks(self):
        devices = self._find_td1_devices()
        if not devices:
            self.logger.warning("No TD1 devices found.")
            return
        for port, serial_number in devices:
            await self._start_serial_task(port, serial_number)
            await asyncio.sleep(0.5)

    async def _watch_for_new_devices(self):
        self._known_serials = set(self.serial_tasks.keys())
        while self._enabled:
            await asyncio.sleep(5)  # Scan every 5 seconds

            # Find currently connected devices
            current_serials = set()
            for port, serial_number in self._find_td1_devices():
                current_serials.add(serial_number)
                if serial_number not in self._known_serials:
                    self.logger.info(f"Hot-plugged TD1 detected: {serial_number}")
                    try:
                        await self._start_serial_task(port, serial_number)
                    except Exception:
                        pass

            # Detect disconnected devices
            disconnected_serials = self._known_serials - current_serials
            self.logger.info(disconnected_serials, self._known_serials)
            for serial_number in disconnected_serials:
                if serial_number in self.serial_tasks:
                    await self.close_device(self.serial_tasks[serial_number])
                    del self.serial_tasks[serial_number]
                self._known_serials.remove(serial_number)

    def _find_td1_devices(self):
        found = []
        for port in serial.tools.list_ports.comports():
            if port.vid == self._vid and port.pid == self._pid:
                if port.serial_number:
                    found.append((port.device, port.serial_number))
        return found

    async def _start_serial_task(self, port, serial_number):
        try:
            self.serial_tasks[serial_number] = TD1Protocol(self._config,
                                                           serial_number,
                                                           port,
                                                           self.logger)
            ret = self.serial_tasks[serial_number].initialize()
            if ret is not None:
                await ret

            if not self.serial_tasks[serial_number].error_state:
                self.logger.info(f"TD1 listening on {port} (Serial: {serial_number})")
                self._known_serials.add(serial_number)
            else:
                self.logger.error(
                    f"Failed to open port:{port} for TD1 serial {serial_number}")

        except Exception as e:
            self.logger.error(f"Failed to open serial port {port}: {e}")

    async def _handle_get_data(self, request):
        latest_data = {}
        for sn, task in self.serial_tasks.items():
            latest_data.update(task.get_data())

        return {
            "status": "ok",
            "devices": latest_data
        }

    async def _handle_reboot(self, web_request: WebRequest):
        """
        Reboots TD1 device by serial when requested
        """
        serial_number = web_request.get_str("serial")

        if not serial_number or serial_number not in self.serial_tasks:
            return {
                "status": "serial_error"
            }

        await self.serial_tasks[serial_number].reboot()
        await self.close_device(self.serial_tasks[serial_number])

        # # Remove serial number from entries if they exist
        if serial_number in self.serial_tasks:
            del self.serial_tasks[serial_number]
        if serial_number in self._known_serials:
            self._known_serials.remove(serial_number)

        return {
            "status": "ok"
        }

def load_component(config):
    return TD1(config)
