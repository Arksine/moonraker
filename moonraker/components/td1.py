# Support for interfacing with TD-1 devices to assist in filament changers grabbing TD
# and color for all filament loaded.
#
# Copyright (C) 2025 AJAX3D and Jim Madill <jcmadill1@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
from __future__ import annotations

import asyncio
import serial
import serial.tools.list_ports
import logging
import datetime
import contextlib
from ..utils import async_serial
from ..common import RequestType
from typing import Optional, Dict, List, Tuple, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest


class TD1Connection:
    def __init__(
        self,
        config: ConfigHelper,
        serial_number: str,
        port: str,
        logger: logging.Logger
    ) -> None:
        self.config = config
        self.server = config.get_server()
        self.port = port
        self.serial_number = serial_number
        self.baudrate = config.getint("baudrate", 115200)
        self.name = f"{config.get_name()}-{self.serial_number}"
        self.logger = logger
        self.error_state: bool = False
        self.enabled: bool = True
        self.serial = async_serial.AsyncSerialConnection(
            self.server, self.name, self.port, self.baudrate
        )
        self.serial_task: asyncio.Task
        # Values to return back for td1_data endpoint
        self._td: Optional[float] = None
        self._color: Optional[str] = None
        self._scan_time: str = datetime.datetime.now(
            tz=datetime.timezone.utc
        ).isoformat() + "Z"
        self._error: Optional[str] = None
        self.done_initializing: bool = False

    async def initialize(self) -> None:
        self.serial_task = self.server.get_event_loop().create_task(self.run_serial())
        for _ in range(5):
            await asyncio.sleep(.01)
            if self.serial.connected:
                break

    async def readline_with_timeout(self, timeout: float = 5.) -> Optional[bytes]:
        with contextlib.suppress(asyncio.TimeoutError):
            return await asyncio.wait_for(self.serial.reader.readline(), timeout)
        return None

    async def run_serial(self) -> None:
        try:
            self.serial.open()
        except (self.serial.error, OSError):
            self.error_state = True
            self.logger.exception(
                f"Error trying to open TD1 serial: {self.serial_number}"
            )
            return
        await self.check_error_file()
        self.done_initializing = True
        async for read_line in self.serial.reader:
            line = read_line.decode("utf-8").rstrip()
            self._parse_line(line)
            self.logger.debug(f"{self.serial_number}: {line}")

    def _parse_line(self, line: str) -> None:
        try:
            parts = line.split(",")
            if len(parts) >= 6:
                self._td = float(parts[4])
                self._color = parts[5].strip()
                self._scan_time = datetime.datetime.now(
                    tz=datetime.timezone.utc
                ).isoformat() + "Z"
        except Exception as e:
            self.logger.error(f"Parse error from {self.serial_number}: {e}")

    async def check_error_file(self) -> None:
        """
        Requests error file from TD1

        :return: Returns empty string if no error.txt file exists, returns error.txt
                 content if it exists so error can be displayed to user.
        """
        file_name = "errors.txt"
        # Wait for acknowledgment from the Raspberry Pi Pico
        ack = None
        while ack != 'ready' and ack != 'No file named errors.txt' and self.enabled:
            # Send the file request command
            if ack is None:
                self.logger.debug(f"{self.serial_number}: Sending retrieve file")
                await self.serial.send(b'retrieve file\n')
                await self.serial.send(f"{file_name}\n".encode('utf-8'))
            line = await self.readline_with_timeout()
            if line is not None:
                ack = line.decode('utf-8').strip()
                self.logger.debug(f"{self.serial_number}: {ack}")
            else:
                self.logger.debug(f"{self.serial_number}: Timed out")
                ack = None
        self.logger.debug(f"{self.serial_number}: Done checking for error file")
        if ack == 'No file named errors.txt':
            return
        # Receive file size and block count
        data = await self.serial.reader.readline()
        file_size = int(data.decode('utf-8').strip())
        data = await self.serial.reader.readline()
        blk_count = int(data.decode('utf-8').strip())
        self.logger.debug(
            f"Receiving file: {file_name}, Size: {file_size} bytes, Blocks: {blk_count}"
        )
        block_content = ''
        for _ in range(blk_count):
            # Receive a block size
            data = await self.serial.reader.readuntil(b'\n')
            # Receive the block content
            data = await self.serial.reader.readuntil(b'\n')
            block_content += data.decode()
            # Send acknowledgment to the Raspberry Pi Pico
            await self.serial.send(b'ready\n')
        self.logger.info(
            f"{self.serial_number}: Error file content: {block_content}"
        )
        self.logger.info(
            f"{self.serial_number}: File '{file_name}' received successfully."
        )
        self._error = block_content

    async def close(self) -> None:
        self.enabled = False
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.serial.close(), 1)
        if hasattr(self, "serial_task") and not self.serial_task.done():
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(self.serial_task, 1)

    async def reboot(self) -> None:
        self.logger.info(f"Rebooting {self.serial_number}")
        self.enabled = False
        self.serial.set_read_callback(None, force=True)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.serial_task, 1.)
        await self.serial.send('change settings\n'.encode('utf-8'))
        ack = await self.serial.reader.readuntil(b'\n')
        ack_str = ack.decode('utf-8').strip()
        if ack_str == 'ready':
            await self.serial.send(b'RGB_Enabled = True\ndone\n')
        ack = await self.serial.reader.readuntil(b'\n')
        ack_str = ack.decode('utf-8').strip()

    def get_data(self) -> Dict[str, Dict[str, Any]]:
        if not self.done_initializing:
            # Return empty dictionary until check_error_file has compeleted
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
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self._config = config
        self._loop = self.server.get_event_loop()
        self.logger = logging.getLogger("td1")
        self._server = config.get_server()
        self._vid: int = 0xE4B2
        self._pid: int = 0x0045
        self._enabled: bool = True
        self._watch_task: Optional[asyncio.Task] = None
        self._start_task: Optional[asyncio.Task] = None
        self.td1_conns: Dict[str, TD1Connection] = {}
        self._register_endpoints()

    def _register_endpoints(self) -> None:
        self._server.register_endpoint(
            "/machine/td1/data", RequestType.GET, self._handle_get_data
        )
        self._server.register_endpoint(
            "/machine/td1/reboot", RequestType.POST, self._handle_reboot
        )

    async def close(self) -> None:
        # Stop watching for new devices
        self._enabled = False
        if self._start_task is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._start_task, 1.)
        if self._watch_task is not None:
            self._watch_task.cancel()
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._watch_task, 1.)
        for td1 in self.td1_conns.values():
            await td1.close()

    async def component_init(self) -> None:
        self.logger.debug("Initializing TD1")
        self._start_task = self._loop.create_task(self._start_all_serial_tasks())
        self._watch_task = self._loop.create_task(self._watch_for_new_devices())

    async def _start_all_serial_tasks(self) -> None:
        devices = self._find_td1_devices()
        if not devices:
            self.logger.warning("No TD1 devices found.")
            return
        for port, serial_number in devices:
            await self._start_serial_task(port, serial_number)
            await asyncio.sleep(0.5)

    async def _watch_for_new_devices(self) -> None:
        while self._enabled:
            await asyncio.sleep(5.)  # Scan every 5 seconds
            # Find currently connected devices
            current_serials: set[str] = set()
            for port, serial_number in self._find_td1_devices():
                current_serials.add(serial_number)
                if serial_number not in self.td1_conns:
                    self.logger.info(f"Hot-plugged TD1 detected: {serial_number}")
                    try:
                        await self._start_serial_task(port, serial_number)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        continue
            # Detect disconnected devices
            known_serials = set(self.td1_conns.keys())
            disconnected_serials = known_serials - current_serials
            self.logger.debug(
                f"Disconnected: {disconnected_serials}, All: {known_serials}"
            )
            for serial_number in disconnected_serials:
                conn = self.td1_conns.pop(serial_number, None)
                if conn is not None:
                    await conn.close()

    def _find_td1_devices(self) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        for port in serial.tools.list_ports.comports():
            if port.vid == self._vid and port.pid == self._pid:
                if port.serial_number:
                    found.append((port.device, port.serial_number))
        return found

    async def _start_serial_task(self, port: str, serial_number: str) -> None:
        try:
            conn = TD1Connection(self._config, serial_number, port, self.logger)
            await conn.initialize()
            if not conn.error_state:
                self.td1_conns[serial_number] = conn
                self.logger.info(f"TD1 listening on {port} (Serial: {serial_number})")
            else:
                self.logger.error(
                    f"Failed to open port:{port} for TD1 serial {serial_number}")
        except serial.SerialException as e:
            self.logger.error(f"Failed to open serial port {port}: {e}")
            raise
        except asyncio.CancelledError as e:
            self.logger.error(f"Task canceled error {e}")
            raise

    async def _handle_get_data(self, web_request: WebRequest) -> Dict[str, Any]:
        latest_data: Dict[str, Any] = {}
        for task in self.td1_conns.values():
            latest_data.update(task.get_data())
        return {
            "status": "ok",
            "devices": latest_data
        }

    async def _handle_reboot(self, web_request: WebRequest) -> Dict[str, str]:
        """
        Reboots TD1 device by serial when requested
        """
        serial_number = web_request.get_str("serial")
        req_conn = self.td1_conns.get(serial_number, None)
        if req_conn is None:
            return {
                "status": "serial_error"
            }
        await req_conn.reboot()
        await req_conn.close()
        # Remove serial number from entries if they exist
        self.td1_conns.pop(serial_number, None)
        return {
            "status": "ok"
        }

def load_component(config: ConfigHelper) -> TD1:
    return TD1(config)
