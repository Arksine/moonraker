# Async CAN Socket utility
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import socket
import asyncio
import errno
import struct
import logging
from . import ServerError
from typing import List, Dict, Optional, Union

CAN_FMT = "<IB3x8s"
CAN_READER_LIMIT = 1024 * 1024
KLIPPER_ADMIN_ID = 0x3f0
KLIPPER_SET_NODE_CMD = 0x01
KATAPULT_SET_NODE_CMD = 0x11
CMD_QUERY_UNASSIGNED = 0x00
CANBUS_RESP_NEED_NODEID = 0x20

class CanNode:
    def __init__(self, node_id: int, cansocket: CanSocket) -> None:
        self.node_id = node_id
        self._reader = asyncio.StreamReader(CAN_READER_LIMIT)
        self._cansocket = cansocket

    async def read(
        self, n: int = -1, timeout: Optional[float] = 2
    ) -> bytes:
        return await asyncio.wait_for(self._reader.read(n), timeout)

    async def readexactly(
        self, n: int, timeout: Optional[float] = 2
    ) -> bytes:
        return await asyncio.wait_for(self._reader.readexactly(n), timeout)

    async def readuntil(
        self, sep: bytes = b"\x03", timeout: Optional[float] = 2
    ) -> bytes:
        return await asyncio.wait_for(self._reader.readuntil(sep), timeout)

    def write(self, payload: Union[bytes, bytearray]) -> None:
        if isinstance(payload, bytearray):
            payload = bytes(payload)
        self._cansocket.send(self.node_id, payload)

    async def write_with_response(
        self,
        payload: Union[bytearray, bytes],
        resp_length: int,
        timeout: Optional[float] = 2.
    ) -> bytes:
        self.write(payload)
        return await self.readexactly(resp_length, timeout)

    def feed_data(self, data: bytes) -> None:
        self._reader.feed_data(data)

    def close(self) -> None:
        self._reader.feed_eof()

class CanSocket:
    def __init__(self, interface: str):
        self._loop = asyncio.get_running_loop()
        self.nodes: Dict[int, CanNode] = {}
        self.cansock = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.input_buffer = b""
        self.output_packets: List[bytes] = []
        self.input_busy = False
        self.send_task: asyncio.Task | None = None
        self.closed = True
        try:
            self.cansock.bind((interface,))
        except Exception:
            raise ServerError(f"Unable to bind socket to interface '{interface}'", 500)
        self.closed = False
        self.cansock.setblocking(False)
        self._loop.add_reader(self.cansock.fileno(), self._handle_can_response)

    def register_node(self, node_id: int) -> CanNode:
        if node_id in self.nodes:
            return self.nodes[node_id]
        node = CanNode(node_id, self)
        self.nodes[node_id + 1] = node
        return node

    def remove_node(self, node_id: int) -> None:
        node = self.nodes.pop(node_id + 1, None)
        if node is not None:
            node.close()

    def _handle_can_response(self) -> None:
        try:
            data = self.cansock.recv(4096)
        except socket.error as e:
            # If bad file descriptor allow connection to be
            # closed by the data check
            if e.errno == errno.EBADF:
                logging.exception("Can Socket Read Error, closing")
                data = b''
            else:
                return
        if not data:
            # socket closed
            self.close()
            return
        self.input_buffer += data
        if self.input_busy:
            return
        self.input_busy = True
        while len(self.input_buffer) >= 16:
            packet = self.input_buffer[:16]
            self._process_packet(packet)
            self.input_buffer = self.input_buffer[16:]
        self.input_busy = False

    def _process_packet(self, packet: bytes) -> None:
        can_id, length, data = struct.unpack(CAN_FMT, packet)
        can_id &= socket.CAN_EFF_MASK
        payload = data[:length]
        node = self.nodes.get(can_id)
        if node is not None:
            node.feed_data(payload)

    def send(self, can_id: int, payload: bytes = b"") -> None:
        if can_id > 0x7FF:
            can_id |= socket.CAN_EFF_FLAG
        if not payload:
            packet = struct.pack(CAN_FMT, can_id, 0, b"")
            self.output_packets.append(packet)
        else:
            while payload:
                length = min(len(payload), 8)
                pkt_data = payload[:length]
                payload = payload[length:]
                packet = struct.pack(
                    CAN_FMT, can_id, length, pkt_data)
                self.output_packets.append(packet)
        if self.send_task is not None:
            return
        self.send_task = asyncio.create_task(self._do_can_send())

    async def _do_can_send(self):
        while self.output_packets:
            packet = self.output_packets.pop(0)
            try:
                await self._loop.sock_sendall(self.cansock, packet)
            except socket.error:
                logging.info("Socket Write Error, closing")
                self.close()
                break
        self.send_task = None

    def close(self):
        if self.closed:
            return
        self.closed = True
        for node in self.nodes.values():
            node.close()
        self._loop.remove_reader(self.cansock.fileno())
        self.cansock.close()

async def query_klipper_uuids(can_socket: CanSocket) -> List[Dict[str, str]]:
    loop = asyncio.get_running_loop()
    admin_node = can_socket.register_node(KLIPPER_ADMIN_ID)
    payload = bytes([CMD_QUERY_UNASSIGNED])
    admin_node.write(payload)
    curtime = loop.time()
    endtime = curtime + 2.
    uuids: List[Dict[str, str]] = []
    while curtime < endtime:
        timeout = max(.1, endtime - curtime)
        try:
            resp = await admin_node.read(8, timeout)
        except asyncio.TimeoutError:
            continue
        finally:
            curtime = loop.time()
        if len(resp) < 7 or resp[0] != CANBUS_RESP_NEED_NODEID:
            continue
        app_names = {
            KLIPPER_SET_NODE_CMD: "Klipper",
            KATAPULT_SET_NODE_CMD: "Katapult"
        }
        app = "Unknown"
        if len(resp) > 7:
            app = app_names.get(resp[7], "Unknown")
        data = resp[1:7]
        uuids.append(
            {
                "uuid": data.hex(),
                "application": app
            }
        )
    return uuids
