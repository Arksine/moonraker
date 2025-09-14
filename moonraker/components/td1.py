# Support for interfacing with TD-1 devices to assist in filament changers grabbing TD
# and color for all filament loaded.
#
# Copyright (C) 2025 AJAX3D and Jim Madill <jcmadill1@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import serial_asyncio
import serial
import serial.tools.list_ports
import logging
import datetime

from ..common import RequestType
from ..common import WebRequest

class TD1Protocol(asyncio.Protocol):
    def __init__(self, serial_number, callback, logger):
        self.serial_number = serial_number
        self.callback = callback
        self.logger = logger
        self.buffer = ""

    def connection_made(self, transport):
        self.transport = transport
        self.loop = asyncio.get_event_loop()

    def data_received(self, data):
        self.buffer += data.decode("utf-8")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            line = line.strip()
            if line:
                self.callback(self.serial_number, line)

class TD1:
    def __init__(self, config):
        self._config = config
        self._loop = asyncio.get_event_loop()
        self._logger = logging.getLogger("td1")
        self._server = config.get_server()
        self._baudrate = int(config.get('baudrate', 115200))
        self._ports_by_serial = {}
        self._latest_data = {}
        self._known_serials = []
        self._vid = 0xE4B2
        self._pid = 0x0045

    async def initialize(self):
        self._server.register_endpoint(
            "/machine/td1_data", RequestType.GET, self._handle_get_data
        )
        self._server.register_endpoint(
            "/machine/td1_reboot", RequestType.POST, self._handle_reboot
        )
        await self._start_all_serial_tasks()
        self._loop.create_task(self._watch_for_new_devices())

    async def _start_all_serial_tasks(self):
        devices = self._find_td1_devices()
        if not devices:
            self._logger.warning("No TD1 devices found.")
            return
        for port, serial_number in devices:
            await self._start_serial_task(port, serial_number)
            await asyncio.sleep(0.5)

    async def _watch_for_new_devices(self):
        self._known_serials = set(self._latest_data.keys())
        while True:
            await asyncio.sleep(5)  # Scan every 5 seconds

            # Find currently connected devices
            current_serials = set()
            for port, serial_number in self._find_td1_devices():
                current_serials.add(serial_number)
                if serial_number not in self._known_serials:
                    self._logger.info(f"Hot-plugged TD1 detected: {serial_number}")
                    try:
                        await self._start_serial_task(port, serial_number)
                        self._known_serials.add(serial_number)
                    except Exception:
                        pass

            # Detect disconnected devices
            disconnected_serials = self._known_serials - current_serials
            self._logger.info(disconnected_serials, self._known_serials)
            for serial_number in disconnected_serials:
                self._logger.warning(f"TD1 device disconnected: {serial_number}")
                if serial_number in self._latest_data:
                    del self._latest_data[serial_number]
                    del self._ports_by_serial[serial_number]
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
            # Gather error log
            error_content = self.check_error_file(port)
            # Pre-register empty entry so it's visible even before data arrives
            if serial_number not in self._latest_data:
                self._latest_data[serial_number] = {
                    "td": None,
                    "color": None,
                    "scan_time": datetime.datetime.utcnow().isoformat() + "Z"
                }
                if error_content:
                    self._latest_data[serial_number].update({"error": error_content})
            transport, _ = await serial_asyncio.create_serial_connection(
                asyncio.get_event_loop(),
                lambda: TD1Protocol(serial_number, self._parse_and_store, self._logger),
                port,
                baudrate=self._baudrate
            )
            self._logger.info(f"TD1 listening on {port} (Serial: {serial_number})")
            # Store port and transport so this information can be looked up later
            # by serial
            self._ports_by_serial[serial_number] = {
                "port": port, "transport": transport
            }

        except Exception as e:
            self._logger.error(f"Failed to open serial port {port}: {e}")

    def _parse_and_store(self, serial_number, line):
        try:
            parts = line.split(",")
            if len(parts) >= 6:
                td = float(parts[4])
                color = parts[5].strip()
                self._latest_data[serial_number] = {
                    "td": td,
                    "color": color,
                    "scan_time": datetime.datetime.utcnow().isoformat() + "Z"
                }
        except Exception as e:
            self._logger.error(f"Parse error from {serial_number}: {e}")

    async def _handle_get_data(self, request):
        return {
            "status": "ok",
            "devices": self._latest_data
        }

    async def _handle_reboot(self, web_request: WebRequest):
        """
        Reboots TD1 device by serial when requested
        """
        serial_number = web_request.get_str("serial")

        if not serial_number:
            return {
                "status": "serial_error"
            }

        try:
            port = self._ports_by_serial[serial_number]["port"]
            # Close port so that change settings command can be sent to for a reboot
            transport = self._ports_by_serial[serial_number]["transport"]
            transport.close()
        except KeyError as e:
            self._logger.error(f"Error: {e}")
            return {
                "status": "key_error"
            }

        if port:
            # Setting RGB_Enabled setting to True will cause TD-1 to reboot
            ser = serial.Serial(port, self._baudrate, timeout=5)
            ser.write('change settings\n'.encode('utf-8'))

            ack = ser.readline().decode('utf-8').strip()
            if ack == 'ready':
                ser.write(b'RGB_Enabled = True\ndone\n')

            ack = ser.readline().decode('utf-8').strip()
            ser.close()

        # Remove serial number from entries if they exist
        if serial_number in self._latest_data:
            del self._latest_data[serial_number]
        if serial_number in self._ports_by_serial:
            del self._ports_by_serial[serial_number]
        if serial_number in self._known_serials:
            self._known_serials.remove(serial_number)
        return {
            "status": "ok"
        }

    def check_error_file(self, port):
        """
        Requests error file from TD1

        :param port: Port to connect and read from TD1

        :return: Returns empty string if no error.txt file exists, returns error.txt
                 content if it exists so error can be displayed to user.
        """
        ser = serial.Serial(port, self._baudrate, timeout=5)
        file_name = "errors.txt"
        # Send the file request command
        ser.write(b'retrieve file\n')
        ser.write(f"{file_name}\n".encode('utf-8'))
        # Wait for acknowledgment from the Raspberry Pi Pico
        ack = ''
        while ack != 'ready' and ack != 'No file named errors.txt':
            ack = ser.readline().decode('utf-8').strip()
            self._logger.info(f"ACK: {ack}")

            # Timeout might have occurred, re-request errors file
            if not ack:
                ser.write(b'retrieve file\n')
                ser.write(f"{file_name}\n".encode('utf-8'))

        if ack == 'No file named errors.txt':
            return ''

        # Receive file size and block count
        file_size = int(ser.readline().decode('utf-8').strip())
        blk_count = int(ser.readline().decode('utf-8').strip())

        self._logger.info(
            f"Receiving file: {file_name}, Size: {file_size} bytes, Blocks: {blk_count}"
        )

        remaining_bytes = file_size
        block_content = ''
        for _ in range(blk_count):
            # Receive a block size
            block_size = int(ser.readline().decode('utf-8').strip())

            # Receive the block content
            block_content += ser.read(block_size).decode()

            remaining_bytes -= block_size

            # Send acknowledgment to the Raspberry Pi Pico
            ser.write(b'ready\n')
        ser.close()
        self._logger.info(f"Error file content: {block_content}")
        self._logger.info(f"File '{file_name}' received successfully.")
        return block_content

def load_component(config):
    component = TD1(config)
    asyncio.get_event_loop().create_task(component.initialize())
    return component
