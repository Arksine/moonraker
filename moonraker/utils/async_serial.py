# Asyncio wrapper for serial communications
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import errno
import logging
import asyncio
import contextlib
from serial import Serial, SerialException
from typing import TYPE_CHECKING, Optional, List, Tuple, Awaitable, Callable

if TYPE_CHECKING:
    from ..server import Server
    from ..confighelper import ConfigHelper

READER_LIMIT = 4*1024*1024


class AsyncSerialConnection:
    error = SerialException
    def __init__(self, server: Server, name: str, port: str, baud: int) -> None:
        self.name = name
        self.eventloop = server.get_event_loop()
        self.port = port
        self.baud = baud
        self.ser: Optional[Serial] = None
        self.send_task: Optional[asyncio.Task] = None
        self.send_buffer: List[Tuple[asyncio.Future, bytes]] = []
        self._reader = asyncio.StreamReader(limit=READER_LIMIT)
        self._read_callback: Callable[[bytes], None] = self._reader.feed_data

    @property
    def connected(self) -> bool:
        return self.ser is not None

    @property
    def reader(self) -> asyncio.StreamReader:
        return self._reader

    @property
    def reader_active(self) -> bool:
        return self._read_callback == self._reader.feed_data

    @staticmethod
    def from_config(
        config: ConfigHelper, default_baud: int = 57600
    ) -> AsyncSerialConnection:
        port: str = config.get("serial")
        name = config.get_name()
        baud = config.getint("baud", default_baud)
        server = config.get_server()
        return AsyncSerialConnection(server, name, port, baud)

    def set_read_callback(
        self, callback: Callable[[bytes], None] | None, force: bool = False
    ) -> None:
        if callback is None:
            if not force and self.reader_active:
                return
            self._reader.feed_eof()
            self._reader = asyncio.StreamReader(limit=READER_LIMIT)
            self._read_callback = self._reader.feed_data
        else:
            self._reader.feed_eof()
            self._read_callback = callback

    def close(self) -> Awaitable:
        if self.ser is not None:
            self.eventloop.remove_reader(self.ser.fileno())
            self.ser.close()
            logging.info(f"{self.name}: Disconnected")
        self.ser = None
        for (fut, _) in self.send_buffer:
            fut.set_exception(SerialException("Serial Device Closed"))
        self.send_buffer.clear()
        self._reader.feed_eof()
        if self.send_task is not None and not self.send_task.done():
            async def _cancel_send(send_task: asyncio.Task):
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(send_task, 2.)
            return self.eventloop.create_task(_cancel_send(self.send_task))
        self.send_task = None
        fut = self.eventloop.create_future()
        fut.set_result(None)
        return fut

    def open(self, exclusive: bool = True) -> None:
        if self.connected:
            return
        logging.info(f"{self.name} :Attempting to open serial device: {self.port}")
        ser = Serial(self.port, self.baud, timeout=0, exclusive=exclusive)
        self.ser = ser
        fd = self.ser.fileno()
        os.set_blocking(fd, False)
        self.eventloop.add_reader(fd, self._handle_incoming)
        if self.reader_active:
            self._reader = asyncio.StreamReader(limit=READER_LIMIT)
            self._read_callback = self._reader.feed_data
        logging.info(f"{self.name} Connected")

    def _handle_incoming(self) -> None:
        # Process incoming data using same method as gcode.py
        if self.ser is None:
            return
        try:
            data = os.read(self.ser.fileno(), 4096)
        except OSError:
            return

        if not data:
            # possibly an error, disconnect
            logging.info(f"{self.name}: No data received, disconnecting")
            self.close()
        else:
            self._read_callback(data)

    def send(self, data: bytes) -> asyncio.Future:
        fut = self.eventloop.create_future()
        if not self.connected:
            fut.set_exception(SerialException("Serial Device Closed"))
            return fut
        self.send_buffer.append((fut, data))
        if self.send_task is None or self.send_task.done():
            self.send_task = self.eventloop.create_task(self._do_send())
        return fut

    async def _do_send(self) -> None:
        while self.send_buffer:
            fut, data = self.send_buffer.pop()
            while data:
                if self.ser is None:
                    sent = 0
                else:
                    try:
                        sent = os.write(self.ser.fileno(), data)
                    except OSError as e:
                        if e.errno == errno.EBADF or e.errno == errno.EPIPE:
                            sent = 0
                        else:
                            await asyncio.sleep(.001)
                            continue
                if sent:
                    data = data[sent:]
                else:
                    logging.exception(
                        f"{self.name}: Error writing data, closing serial connection"
                    )
                    fut.set_exception(SerialException("Serial Device Closed"))
                    self.send_task = None
                    self.close()
                    return
            fut.set_result(None)
        self.send_task = None
