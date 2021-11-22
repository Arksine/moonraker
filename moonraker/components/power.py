# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import json
import struct
import socket
import asyncio
import time
from tornado.iostream import IOStream
from tornado.httpclient import AsyncHTTPClient
from tornado.escape import json_decode

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    List,
    Any,
    Optional,
    Dict,
    Coroutine,
    Union,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from .machine import Machine
    from . import klippy_apis
    APIComp = klippy_apis.KlippyAPI

class PrinterPower:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        has_gpio = self.server.load_component(config, 'gpio', None) is not None
        self.devices: Dict[str, PowerDevice] = {}
        prefix_sections = config.get_prefix_sections("power")
        logging.info(f"Power component loading devices: {prefix_sections}")
        dev_types = {
            "gpio": GpioDevice,
            "tplink_smartplug": TPLinkSmartPlug,
            "tasmota": Tasmota,
            "shelly": Shelly,
            "homeseer": HomeSeer,
            "homeassistant": HomeAssistant,
            "loxonev1": Loxonev1,
            "rf": RFDevice
        }

        for section in prefix_sections:
            cfg = config[section]
            dev_type: str = cfg.get("type")
            dev_class: Optional[Type[PowerDevice]]
            dev_class = dev_types.get(dev_type)
            if dev_class is None:
                raise config.error(f"Unsupported Device Type: {dev_type}")
            if issubclass(dev_class, GpioDevice) and not has_gpio:
                self.server.add_warning(
                    f"Unable to load power device [{cfg.get_name()}], "
                    "gpio component not available")
                continue
            dev = dev_class(cfg)
            self.devices[dev.get_name()] = dev

        self.server.register_endpoint(
            "/machine/device_power/devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/machine/device_power/status", ['GET'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/on", ['POST'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/off", ['POST'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/device", ['GET', 'POST'],
            self._handle_single_power_request)
        self.server.register_remote_method(
            "set_device_power", self.set_device_power)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_klippy_shutdown)
        self.server.register_event_handler(
            "file_manager:upload_queued", self._handle_upload_queued)
        self.server.register_notification("power:power_changed")

    async def _check_klippy_printing(self) -> bool:
        kapis: APIComp = self.server.lookup_component('klippy_apis')
        result: Dict[str, Any] = await kapis.query_objects(
            {'print_stats': None}, default={})
        pstate = result.get('print_stats', {}).get('state', "").lower()
        return pstate == "printing"

    async def component_init(self) -> None:
        event_loop = self.server.get_event_loop()
        # Wait up to 5 seconds for the machine component to init
        machine_cmp: Machine = self.server.lookup_component("machine")
        await machine_cmp.wait_for_init(5.)
        cur_time = event_loop.get_loop_time()
        endtime = cur_time + 120.
        query_devs = list(self.devices.values())
        failed_devs: List[PowerDevice] = []
        while cur_time < endtime:
            for dev in query_devs:
                ret = dev.initialize()
                if ret is not None:
                    await ret
                if dev.get_state() == "error":
                    failed_devs.append(dev)
            if not failed_devs:
                logging.debug("All power devices initialized")
                return
            query_devs = failed_devs
            failed_devs = []
            await asyncio.sleep(2.)
            cur_time = event_loop.get_loop_time()
        if failed_devs:
            failed_names = [d.get_name() for d in failed_devs]
            self.server.add_warning(
                "The following power devices failed init:"
                f" {failed_names}")

    async def _handle_klippy_shutdown(self) -> None:
        for name, dev in self.devices.items():
            if dev.has_off_when_shutdown():
                logging.info(
                    f"Powering off device [{name}] due to"
                    " klippy shutdown")
                await self._process_request(dev, "off")

    async def _handle_upload_queued(self, filename: str) -> None:
        for name, dev in self.devices.items():
            if dev.has_on_when_queued():
                if dev.get_state() == "on":
                    # device already on
                    continue
                logging.debug(
                    f"File '{filename}' queued, powering on device [{name}]")
                await self._process_request(dev, "on")

    async def _handle_list_devices(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        dev_list = [d.get_device_info() for d in self.devices.values()]
        output = {"devices": dev_list}
        return output

    async def _handle_single_power_request(self,
                                           web_request: WebRequest
                                           ) -> Dict[str, Any]:
        dev_name: str = web_request.get_str('device')
        req_action = web_request.get_action()
        if dev_name not in self.devices:
            raise self.server.error(f"No valid device named {dev_name}")
        dev = self.devices[dev_name]
        if req_action == 'GET':
            action = "status"
        elif req_action == "POST":
            action = web_request.get_str('action').lower()
            if action not in ["on", "off", "toggle"]:
                raise self.server.error(
                    f"Invalid requested action '{action}'")
        result = await self._process_request(dev, action)
        return {dev_name: result}

    async def _handle_batch_power_request(self,
                                          web_request: WebRequest
                                          ) -> Dict[str, Any]:
        args = web_request.get_args()
        ep = web_request.get_endpoint()
        if not args:
            raise self.server.error("No arguments provided")
        requested_devs = {k: self.devices.get(k, None) for k in args}
        result = {}
        req = ep.split("/")[-1]
        for name, device in requested_devs.items():
            if device is not None:
                result[name] = await self._process_request(device, req)
            else:
                result[name] = "device_not_found"
        return result

    async def _process_request(self,
                               device: PowerDevice,
                               req: str
                               ) -> str:
        ret = device.refresh_status()
        if ret is not None:
            await ret
        dev_info = device.get_device_info()
        if req == "toggle":
            req = "on" if dev_info['status'] == "off" else "off"
        if req in ["on", "off"]:
            cur_state: str = dev_info['status']
            if req == cur_state:
                # device is already in requested state, do nothing
                return cur_state
            printing = await self._check_klippy_printing()
            if device.get_locked_while_printing() and printing:
                raise self.server.error(
                    f"Unable to change power for {device.get_name()} "
                    "while printing")
            ret = device.set_power(req)
            if ret is not None:
                await ret
            dev_info = device.get_device_info()
            self.server.send_event("power:power_changed", dev_info)
            await device.run_power_changed_action()
        elif req != "status":
            raise self.server.error(f"Unsupported power request: {req}")
        return dev_info['status']

    def set_device_power(self, device: str, state: str) -> None:
        status: Optional[str] = None
        if isinstance(state, bool):
            status = "on" if state else "off"
        elif isinstance(state, str):
            status = state.lower()
            if status in ["true", "false"]:
                status = "on" if status == "true" else "off"
        if status not in ["on", "off"]:
            logging.info(f"Invalid state received: {state}")
            return
        if device not in self.devices:
            logging.info(f"No device found: {device}")
            return
        event_loop = self.server.get_event_loop()
        event_loop.register_callback(
            self._process_request, self.devices[device], status)

    async def add_device(self, name: str, device: PowerDevice) -> None:
        if name in self.devices:
            raise self.server.error(
                f"Device [{name}] already configured")
        ret = device.initialize()
        if ret is not None:
            await ret
        self.devices[name] = device

    async def close(self) -> None:
        for device in self.devices.values():
            ret = device.close()
            if ret is not None:
                await ret


class PowerDevice:
    def __init__(self, config: ConfigHelper) -> None:
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.type: str = config.get('type')
        self.state: str = "init"
        self.locked_while_printing = config.getboolean(
            'locked_while_printing', False)
        self.off_when_shutdown = config.getboolean('off_when_shutdown', False)
        self.restart_delay = 1.
        self.klipper_restart = config.getboolean(
            'restart_klipper_when_powered', False)
        if self.klipper_restart:
            self.restart_delay = config.getfloat('restart_delay', 1.)
            if self.restart_delay < .000001:
                raise config.error("Option 'restart_delay' must be above 0.0")
        self.bound_service: Optional[str] = config.get('bound_service', None)
        self.need_scheduled_restart = False
        self.on_when_queued = config.getboolean('on_when_upload_queued', False)

    def _is_bound_to_klipper(self):
        return (
            self.bound_service is not None and
            self.bound_service.startswith("klipper") and
            not self.bound_service.startswith("klipper_mcu")
        )

    def _schedule_firmware_restart(self, state: str = "") -> None:
        if not self.need_scheduled_restart:
            return
        self.need_scheduled_restart = False
        if state == "ready":
            logging.info("Klipper reports 'ready', aborting FIRMWARE_RESTART")
            return
        event_loop = self.server.get_event_loop()
        kapis: APIComp = self.server.lookup_component("klippy_apis")
        event_loop.delay_callback(
            self.restart_delay, kapis.do_restart,
            "FIRMWARE_RESTART")

    def get_name(self) -> str:
        return self.name

    def get_state(self) -> str:
        return self.state

    def get_device_info(self) -> Dict[str, Any]:
        return {
            'device': self.name,
            'status': self.state,
            'locked_while_printing': self.locked_while_printing,
            'type': self.type
        }

    def get_locked_while_printing(self) -> bool:
        return self.locked_while_printing

    async def run_power_changed_action(self) -> None:
        if self.bound_service is not None:
            machine_cmp: Machine = self.server.lookup_component("machine")
            action = "start" if self.state == "on" else "stop"
            await machine_cmp.do_service_action(action, self.bound_service)
        if self.state == "on" and self.klipper_restart:
            self.need_scheduled_restart = True
            klippy_state = self.server.get_klippy_state()
            if klippy_state in ["disconnected", "startup"]:
                # If klippy is currently disconnected or hasn't proceeded past
                # the startup state, schedule the restart in the
                # "klippy_started" event callback.
                return
            self._schedule_firmware_restart()

    def has_off_when_shutdown(self) -> bool:
        return self.off_when_shutdown

    def has_on_when_queued(self) -> bool:
        return self.on_when_queued

    def initialize(self) -> Optional[Coroutine]:
        if self.bound_service is None:
            return None
        if self.bound_service.startswith("moonraker"):
            raise self.server.error(
                f"Cannot bind to '{self.bound_service}' "
                "service")
        machine_cmp: Machine = self.server.lookup_component("machine")
        sys_info = machine_cmp.get_system_info()
        avail_svcs: List[str] = sys_info.get('available_services', [])
        if self.bound_service not in avail_svcs:
            raise self.server.error(
                f"Bound Service {self.bound_service} is not available")
        if self._is_bound_to_klipper() and self.klipper_restart:
            # Schedule the Firmware Restart after Klipper reconnects
            logging.info(f"Power Device '{self.name}' bound to "
                         f"klipper service '{self.bound_service}'")
            self.server.register_event_handler(
                "server:klippy_started",
                self._schedule_firmware_restart
            )
        return None

    def refresh_status(self) -> Optional[Coroutine]:
        raise NotImplementedError

    def set_power(self, state: str) -> Optional[Coroutine]:
        raise NotImplementedError

    def close(self) -> Optional[Coroutine]:
        pass

class HTTPDevice(PowerDevice):
    def __init__(self,
                 config: ConfigHelper,
                 default_port: int = -1,
                 default_user: str = "",
                 default_password: str = "",
                 default_protocol: str = "http"
                 ) -> None:
        super().__init__(config)
        self.client = AsyncHTTPClient()
        self.request_mutex = asyncio.Lock()
        self.addr: str = config.get("address")
        self.port = config.getint("port", default_port)
        self.user = config.get("user", default_user)
        self.password = config.get("password", default_password)
        self.protocol = config.get("protocol", default_protocol)

    async def initialize(self) -> None:
        super().initialize()
        await self.refresh_status()

    async def _send_http_command(self,
                                 url: str,
                                 command: str
                                 ) -> Dict[str, Any]:
        try:
            response = await self.client.fetch(url)
            data = json_decode(response.body)
        except Exception:
            msg = f"Error sending '{self.type}' command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        return data

    async def _send_power_request(self, state: str) -> str:
        raise NotImplementedError(
            "_send_power_request must be implemented by children")

    async def _send_status_request(self) -> str:
        raise NotImplementedError(
            "_send_status_request must be implemented by children")

    async def refresh_status(self) -> None:
        async with self.request_mutex:
            try:
                state = await self._send_status_request()
            except Exception:
                self.state = "error"
                msg = f"Error Refeshing Device Status: {self.name}"
                logging.exception(msg)
                raise self.server.error(msg) from None
            self.state = state

    async def set_power(self, state):
        async with self.request_mutex:
            try:
                state = await self._send_power_request(state)
            except Exception:
                self.state = "error"
                msg = f"Error Setting Device Status: {self.name} to {state}"
                logging.exception(msg)
                raise self.server.error(msg) from None
            self.state = state


class GpioDevice(PowerDevice):
    def __init__(self,
                 config: ConfigHelper,
                 initial_val: Optional[int] = None
                 ) -> None:
        super().__init__(config)
        self.initial_state = config.getboolean('initial_state', False)
        self.timer: Optional[float] = config.getfloat('timer', None)
        if self.timer is not None and self.timer < 0.000001:
            raise config.error(
                f"Option 'timer' in section [{config.get_name()}] must "
                "be above 0.0")
        self.timer_handle: Optional[asyncio.TimerHandle] = None
        if initial_val is None:
            initial_val = int(self.initial_state)
        self.gpio_out = config.getgpioout('pin', initial_value=initial_val)

    def initialize(self) -> None:
        super().initialize()
        self.set_power("on" if self.initial_state else "off")

    def refresh_status(self) -> None:
        pass

    def set_power(self, state) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None
        try:
            self.gpio_out.write(int(state == "on"))
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state
        self._check_timer()

    def _check_timer(self):
        if self.state == "on" and self.timer is not None:
            event_loop = self.server.get_event_loop()
            power: PrinterPower = self.server.lookup_component("power")
            self.timer_handle = event_loop.delay_callback(
                self.timer, power.set_device_power, self.name, "off")

    def close(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

class RFDevice(GpioDevice):

    # Protocol definition
    # [1, 3] means HIGH is set for 1x pulse_len and LOW for 3x pulse_len
    ZERO_BIT = [1, 3]  # zero bit
    ONE_BIT = [3, 1]  # one bit
    SYNC_BIT = [1, 31]  # sync between
    PULSE_LEN = 0.00035  # length of a single pulse
    RETRIES = 10  # send the code this many times

    def __init__(self, config: ConfigHelper):
        super().__init__(config, initial_val=0)
        self.on = config.get("on_code").zfill(24)
        self.off = config.get("off_code").zfill(24)

    def _transmit_digit(self, waveform) -> None:
        self.gpio_out.write(1)
        time.sleep(waveform[0]*RFDevice.PULSE_LEN)
        self.gpio_out.write(0)
        time.sleep(waveform[1]*RFDevice.PULSE_LEN)

    def _transmit_code(self, code) -> None:
        for _ in range(RFDevice.RETRIES):
            for i in code:
                if i == "1":
                    self._transmit_digit(RFDevice.ONE_BIT)
                elif i == "0":
                    self._transmit_digit(RFDevice.ZERO_BIT)
            self._transmit_digit(RFDevice.SYNC_BIT)

    def set_power(self, state) -> None:
        try:
            if state == "on":
                code = self.on
            else:
                code = self.off
            self._transmit_code(code)
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state
        self._check_timer()


#  This implementation based off the work tplink_smartplug
#  script by Lubomir Stroetmann available at:
#
#  https://github.com/softScheck/tplink-smartplug
#
#  Copyright 2016 softScheck GmbH
class TPLinkSmartPlug(PowerDevice):
    START_KEY = 0xAB
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.timer = config.get("timer", "")
        self.request_mutex = asyncio.Lock()
        self.addr: List[str] = config.get("address").split('/')
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
            if len(self.addr) == 2:
                sysinfo = await self._send_tplink_command("info")
                dev_id = sysinfo["system"]["get_sysinfo"]["deviceId"]
                out_cmd["context"] = {
                    'child_ids': [f"{dev_id}{int(self.addr[1]):02}"]
                }
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
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stream = IOStream(s)
        try:
            await stream.connect((self.addr[0], self.port))
            await stream.write(self._encrypt(out_cmd))
            data = await stream.read_bytes(2048, partial=True)
            length: int = struct.unpack(">I", data[:4])[0]
            data = data[4:]
            retries = 5
            remaining = length - len(data)
            while remaining and retries:
                data += await stream.read_bytes(remaining)
                remaining = length - len(data)
                retries -= 1
            if not retries:
                raise self.server.error("Unable to read tplink packet")
        except Exception:
            msg = f"Error sending tplink command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        finally:
            stream.close()
        return json.loads(self._decrypt(data))

    def _encrypt(self, outdata: Dict[str, Any]) -> bytes:
        data = json.dumps(outdata)
        key = self.START_KEY
        res = struct.pack(">I", len(data))
        for c in data:
            val = key ^ ord(c)
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

    async def initialize(self) -> None:
        super().initialize()
        await self.refresh_status()

    async def refresh_status(self) -> None:
        async with self.request_mutex:
            try:
                state: str
                res = await self._send_tplink_command("info")
                if len(self.addr) == 2:
                    # TPLink device controls multiple devices
                    children: Dict[int, Any]
                    children = res['system']['get_sysinfo']['children']
                    state = children[int(self.addr[1])]['state']
                else:
                    state = res['system']['get_sysinfo']['relay_state']
            except Exception:
                self.state = "error"
                msg = f"Error Refeshing Device Status: {self.name}"
                logging.exception(msg)
                raise self.server.error(msg) from None
            self.state = "on" if state else "off"

    async def set_power(self, state) -> None:
        async with self.request_mutex:
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


class Tasmota(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_password="")
        self.output_id = config.getint("output_id", 1)
        self.timer = config.get("timer", "")

    async def _send_tasmota_command(self,
                                    command: str,
                                    password: Optional[str] = None
                                    ) -> Dict[str, Any]:
        if command in ["on", "off"]:
            out_cmd = f"Power{self.output_id}%20{command}"
            if self.timer != "" and command == "off":
                out_cmd = f"Backlog%20Delay%20{self.timer}0%3B%20{out_cmd}"
        elif command == "info":
            out_cmd = f"Power{self.output_id}"
        else:
            raise self.server.error(f"Invalid tasmota command: {command}")

        url = f"http://{self.addr}/cm?user=admin&password=" \
            f"{self.password}&cmnd={out_cmd}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_tasmota_command("info")
        try:
            state: str = res[f"POWER{self.output_id}"].lower()
        except KeyError as e:
            if self.output_id == 1:
                state = res[f"POWER"].lower()
            else:
                raise KeyError(e)
        return state

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_tasmota_command(state)
        if self.timer == "" or state != "off":
            try:
                state = res[f"POWER{self.output_id}"].lower()
            except KeyError as e:
                if self.output_id == 1:
                    state = res[f"POWER"].lower()
                else:
                    raise KeyError(e)
        return state


class Shelly(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin", default_password="")
        self.output_id = config.getint("output_id", 0)
        self.timer = config.get("timer", "")

    async def _send_shelly_command(self, command: str) -> Dict[str, Any]:
        if command == "on":
            out_cmd = f"relay/{self.output_id}?turn={command}"
        elif command == "off":
            if self.timer != "":
                out_cmd = f"relay/{self.output_id}?turn=on&timer={self.timer}"
            else:
                out_cmd = f"relay/{self.output_id}?turn={command}"
        elif command == "info":
            out_cmd = f"relay/{self.output_id}"
        else:
            raise self.server.error(f"Invalid shelly command: {command}")
        if self.password != "":
            out_pwd = f"{self.user}:{self.password}@"
        else:
            out_pwd = f""
        url = f"http://{out_pwd}{self.addr}/{out_cmd}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_shelly_command("info")
        state: str = res[f"ison"]
        timer_remaining = res[f"timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_shelly_command(state)
        state = res[f"ison"]
        timer_remaining = res[f"timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"


class HomeSeer(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin", default_password="")
        self.device = config.getint("device")

    async def _send_homeseer(self,
                             request: str,
                             additional: str = ""
                             ) -> Dict[str, Any]:
        url = (f"http://{self.user}:{self.password}@{self.addr}"
               f"/JSON?user={self.user}&pass={self.password}"
               f"&request={request}&ref={self.device}&{additional}")
        return await self._send_http_command(url, request)

    async def _send_status_request(self) -> str:
        res = await self._send_homeseer("getstatus")
        return res[f"Devices"][0]["status"].lower()

    async def _send_power_request(self, state: str) -> str:
        if state == "on":
            state_hs = "On"
        elif state == "off":
            state_hs = "Off"
        res = await self._send_homeseer("controldevicebylabel",
                                        f"label={state_hs}")
        return state


class HomeAssistant(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_port=8123)
        self.device: str = config.get("device")
        self.token: str = config.get("token")
        self.domain: str = config.get("domain", "switch")
        self.status_delay: float = config.getfloat("status_delay", 1.)

    async def _send_homeassistant_command(self,
                                          command: str
                                          ) -> Dict[Union[str, int], Any]:
        if command == "on":
            out_cmd = f"api/services/{self.domain}/turn_on"
            body = {"entity_id": self.device}
            method = "POST"
        elif command == "off":
            out_cmd = f"api/services/{self.domain}/turn_off"
            body = {"entity_id": self.device}
            method = "POST"
        elif command == "info":
            out_cmd = f"api/states/{self.device}"
            method = "GET"
        else:
            raise self.server.error(
                f"Invalid homeassistant command: {command}")
        url = f"{self.protocol}://{self.addr}:{self.port}/{out_cmd}"
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }
        try:
            if (method == "POST"):
                response = await self.client.fetch(
                    url, method="POST", body=json.dumps(body), headers=headers)
            else:
                response = await self.client.fetch(
                    url, method="GET", headers=headers)
            data: Dict[Union[str, int], Any] = json_decode(response.body)
        except Exception:
            msg = f"Error sending homeassistant command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        return data

    async def _send_status_request(self) -> str:
        res = await self._send_homeassistant_command("info")
        return res[f"state"]

    async def _send_power_request(self, state: str) -> str:
        await self._send_homeassistant_command(state)
        await asyncio.sleep(self.status_delay)
        res = await self._send_status_request()
        return res

class Loxonev1(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin",
                         default_password="admin")
        self.output_id = config.get("output_id", "")

    async def _send_loxonev1_command(self, command: str) -> Dict[str, Any]:
        if command in ["on", "off"]:
            out_cmd = f"jdev/sps/io/{self.output_id}/{command}"
        elif command == "info":
            out_cmd = f"jdev/sps/io/{self.output_id}"
        else:
            raise self.server.error(f"Invalid loxonev1 command: {command}")
        if self.password != "":
            out_pwd = f"{self.user}:{self.password}@"
        else:
            out_pwd = f""
        url = f"http://{out_pwd}{self.addr}/{out_cmd}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_loxonev1_command("info")
        state = res[f"LL"][f"value"]
        return "on" if int(state) == 1 else "off"

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_loxonev1_command(state)
        state = res[f"LL"][f"value"]
        return "on" if int(state) == 1 else "off"


# The power component has multiple configuration sections
def load_component(config: ConfigHelper) -> PrinterPower:
    return PrinterPower(config)
