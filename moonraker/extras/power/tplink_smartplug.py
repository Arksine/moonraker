#  This implementation based off the work tplink_smartplug
#  script by Lubomir Stroetmann available at:
#
#  https://github.com/softScheck/tplink-smartplug
#
#  Copyright 2016 softScheck GmbH

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Optional, Dict, Any

from moonraker.components.power import PowerDevice
from moonraker.confighelper import ConfigHelper
from moonraker.utils import json_wrapper as jsonw


class TPLinkSmartPlug(PowerDevice):
    START_KEY = 0xAB
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.timer = config.get("timer", "")
        addr_and_output_id = config.get("address").split('/')
        self.addr = addr_and_output_id[0]
        if (len(addr_and_output_id) > 1):
            self.server.add_warning(
                f"Power Device {self.name}: Including the output id in the"
                " address is deprecated, use the 'output_id' option")
            self.output_id: Optional[int] = int(addr_and_output_id[1])
        else:
            self.output_id = config.getint("output_id", None)
        self.port = config.getint("port", 9999)

    async def _send_tplink_command(self,
                                   command: str
                                   ) -> Dict[str, Any]:
        out_cmd: Dict[str, Any] = {}
        if command in ["on", "off"]:
            out_cmd = {
                'system': {'set_relay_state': {'state': int(command == "on")}}
            }
            # TPLink device controls multiple devices
            if self.output_id is not None:
                sysinfo = await self._send_tplink_command("info")
                children = sysinfo["system"]["get_sysinfo"]["children"]
                child_id = children[self.output_id]["id"]
                out_cmd["context"] = {"child_ids": [f"{child_id}"]}
        elif command == "info":
            out_cmd = {'system': {'get_sysinfo': {}}}
        elif command == "clear_rules":
            out_cmd = {'count_down': {'delete_all_rules': None}}
        elif command == "count_off":
            out_cmd = {
                'count_down': {'add_rule':
                               {'enable': 1, 'delay': int(self.timer),
                                'act': 0, 'name': 'turn off'}}
            }
        else:
            raise self.server.error(f"Invalid tplink command: {command}")
        reader, writer = await asyncio.open_connection(
            self.addr, self.port, family=socket.AF_INET)
        try:
            writer.write(self._encrypt(out_cmd))
            await writer.drain()
            data = await reader.read(2048)
            length: int = struct.unpack(">I", data[:4])[0]
            data = data[4:]
            retries = 5
            remaining = length - len(data)
            while remaining and retries:
                data += await reader.read(remaining)
                remaining = length - len(data)
                retries -= 1
            if not retries:
                raise self.server.error("Unable to read tplink packet")
        except Exception:
            msg = f"Error sending tplink command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        finally:
            writer.close()
            await writer.wait_closed()
        return jsonw.loads(self._decrypt(data))

    def _encrypt(self, outdata: Dict[str, Any]) -> bytes:
        data = jsonw.dumps(outdata)
        key = self.START_KEY
        res = struct.pack(">I", len(data))
        for c in data:
            val = key ^ c
            key = val
            res += bytes([val])
        return res

    def _decrypt(self, data: bytes) -> str:
        key: int = self.START_KEY
        res: str = ""
        for c in data:
            val = key ^ c
            key = c
            res += chr(val)
        return res

    async def _send_info_request(self) -> int:
        res = await self._send_tplink_command("info")
        if self.output_id is not None:
            # TPLink device controls multiple devices
            children: Dict[int, Any]
            children = res['system']['get_sysinfo']['children']
            return children[self.output_id]['state']
        else:
            return res['system']['get_sysinfo']['relay_state']

    async def init_state(self) -> None:
        async with self.request_lock:
            last_err: Exception = Exception()
            while True:
                try:
                    state: int = await self._send_info_request()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if type(last_err) is not type(e) or last_err.args != e.args:
                        logging.exception(f"Device Init Error: {self.name}")
                        last_err = e
                    await asyncio.sleep(5.)
                    continue
                else:
                    self.init_task = None
                    self.state = "on" if state else "off"
                    if (
                        self.initial_state is not None and
                        self.state in ["on", "off"]
                    ):
                        new_state = "on" if self.initial_state else "off"
                        if new_state != self.state:
                            logging.info(
                                f"Power Device {self.name}: setting initial "
                                f"state to {new_state}"
                            )
                            await self.set_power(new_state)
                        await self.process_bound_services()
                    self.notify_power_changed()
                    return

    async def refresh_status(self) -> None:
        try:
            state: int = await self._send_info_request()
        except Exception:
            self.state = "error"
            msg = f"Error Refeshing Device Status: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = "on" if state else "off"

    async def set_power(self, state) -> None:
        err: int
        try:
            if self.timer != "" and state == "off":
                await self._send_tplink_command("clear_rules")
                res = await self._send_tplink_command("count_off")
                err = res['count_down']['add_rule']['err_code']
            else:
                res = await self._send_tplink_command(state)
                err = res['system']['set_relay_state']['err_code']
        except Exception:
            err = 1
            logging.exception(f"Power Toggle Error: {self.name}")
        if err:
            self.state = "error"
            raise self.server.error(
                f"Error Toggling Device Power: {self.name}")
        self.state = state
