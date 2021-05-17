# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os
import asyncio
import json
import struct
import socket
import gpiod
from tornado.ioloop import IOLoop
from tornado.iostream import IOStream
from tornado.locks import Lock
from tornado.httpclient import AsyncHTTPClient
from tornado.escape import json_decode

class PrinterPower:
    def __init__(self, config):
        self.server = config.get_server()
        self.chip_factory = GpioChipFactory()
        self.devices = {}
        prefix_sections = config.get_prefix_sections("power")
        logging.info(f"Power component loading devices: {prefix_sections}")
        dev_types = {
            "gpio": GpioDevice,
            "tplink_smartplug": TPLinkSmartPlug,
            "tasmota": Tasmota,
            "shelly": Shelly,
            "homeseer": HomeSeer,
            "homeassistant": HomeAssistant,
            "loxonev1": Loxonev1
        }
        try:
            for section in prefix_sections:
                cfg = config[section]
                dev_type = cfg.get("type")
                dev_class = dev_types.get(dev_type)
                if dev_class is None:
                    raise config.error(f"Unsupported Device Type: {dev_type}")
                elif dev_type == "gpio":
                    dev = dev_class(cfg, self.chip_factory)
                else:
                    dev = dev_class(cfg)
                self.devices[dev.get_name()] = dev
        except Exception:
            self.chip_factory.close()
            raise

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
        self.server.register_notification("power:power_changed")
        IOLoop.current().spawn_callback(
            self._initalize_devices, list(self.devices.values()))

    async def _check_klippy_printing(self):
        klippy_apis = self.server.lookup_component('klippy_apis')
        result = await klippy_apis.query_objects(
            {'print_stats': None}, default={})
        pstate = result.get('print_stats', {}).get('state', "").lower()
        return pstate == "printing"

    async def _initalize_devices(self, inital_devs):
        for dev in inital_devs:
            ret = dev.initialize()
            if asyncio.iscoroutine(ret):
                await ret

    async def _handle_klippy_shutdown(self):
        for name, dev in self.devices.items():
            if hasattr(dev, "off_when_shutdown"):
                if dev.off_when_shutdown:
                    logging.info(
                        f"Powering off device [{name}] due to"
                        " klippy shutdown")
                    await self._process_request(dev, "off")

    async def _handle_list_devices(self, web_request):
        dev_list = [d.get_device_info() for d in self.devices.values()]
        output = {"devices": dev_list}
        return output

    async def _handle_single_power_request(self, web_request):
        dev_name = web_request.get_str('device')
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

    async def _handle_batch_power_request(self, web_request):
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

    async def _process_request(self, device, req):
        ret = device.refresh_status()
        if asyncio.iscoroutine(ret):
            await ret
        dev_info = device.get_device_info()
        if req == "toggle":
            req = "on" if dev_info['status'] == "off" else "off"
        if req in ["on", "off"]:
            cur_state = dev_info['status']
            if req == cur_state:
                # device is already in requested state, do nothing
                return cur_state
            printing = await self._check_klippy_printing()
            if device.get_locked_while_printing() and printing:
                raise self.server.error(
                    f"Unable to change power for {device.get_name()} "
                    "while printing")
            ret = device.set_power(req)
            if asyncio.iscoroutine(ret):
                await ret
            dev_info = device.get_device_info()
            self.server.send_event("power:power_changed", dev_info)
            device.run_power_changed_action()
        elif req != "status":
            raise self.server.error(f"Unsupported power request: {req}")
        return dev_info['status']

    def set_device_power(self, device, state):
        status = None
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
        ioloop = IOLoop.current()
        ioloop.spawn_callback(
            self._process_request, self.devices[device], status)

    async def add_device(self, name, device):
        if name in self.devices:
            raise self.server.error(
                f"Device [{name}] already configured")
        ret = device.initialize()
        if asyncio.iscoroutine(ret):
            await ret
        self.devices[name] = device

    async def close(self):
        for device in self.devices.values():
            if hasattr(device, "close"):
                ret = device.close()
                if asyncio.iscoroutine(ret):
                    await ret
        self.chip_factory.close()


class PowerDevice:
    def __init__(self, config):
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.type = config.get('type')
        self.state = "init"
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

    def get_name(self):
        return self.name

    def get_device_info(self):
        return {
            'device': self.name,
            'status': self.state,
            'locked_while_printing': self.locked_while_printing,
            'type': self.type
        }

    def get_locked_while_printing(self):
        return self.locked_while_printing

    def run_power_changed_action(self):
        if self.state == "on" and self.klipper_restart:
            ioloop = IOLoop.current()
            klippy_apis = self.server.lookup_component("klippy_apis")
            ioloop.call_later(self.restart_delay, klippy_apis.do_restart,
                              "FIRMWARE_RESTART")

class HTTPDevice(PowerDevice):
    def __init__(self, config, default_port=None,
                 default_user=None, default_password=None):
        super().__init__(config)
        self.client = AsyncHTTPClient()
        self.request_mutex = Lock()
        self.addr = config.get("address")
        self.port = config.getint("port", default_port)
        self.user = config.get("user", default_user)
        self.password = config.get("password", default_password)

    async def initialize(self):
        await self.refresh_status()

    async def _send_http_command(self, url, command):
        try:
            response = await self.client.fetch(url)
            data = json_decode(response.body)
        except Exception:
            msg = f"Error sending '{self.type}' command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        return data

    async def _send_power_request(self, state):
        raise NotImplementedError(
            "_send_power_request must be implemented by children")

    async def _send_status_request(self):
        raise NotImplementedError(
            "_send_status_request must be implemented by children")

    async def refresh_status(self):
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


class GpioChipFactory:
    def __init__(self):
        self.chips = {}

    def get_gpio_chip(self, chip_name):
        if chip_name in self.chips:
            return self.chips[chip_name]
        chip = gpiod.Chip(chip_name, gpiod.Chip.OPEN_BY_NAME)
        self.chips[chip_name] = chip
        return chip

    def close(self):
        for chip in self.chips.values():
            chip.close()

class GpioDevice(PowerDevice):
    def __init__(self, config, chip_factory):
        super().__init__(config)
        pin, chip_id, invert = self._parse_pin(config)
        try:
            chip = chip_factory.get_gpio_chip(chip_id)
            self.line = chip.get_line(pin)
            if invert:
                self.line.request(
                    consumer="moonraker", type=gpiod.LINE_REQ_DIR_OUT,
                    flags=gpiod.LINE_REQ_FLAG_ACTIVE_LOW)
            else:
                self.line.request(
                    consumer="moonraker", type=gpiod.LINE_REQ_DIR_OUT)
        except Exception:
            self.state = "error"
            logging.exception(
                f"Unable to init {pin}.  Make sure the gpio is not in "
                "use by another program or exported by sysfs.")
            raise config.error("Power GPIO Config Error")
        self.initial_state = config.getboolean('initial_state', False)

    def _parse_pin(self, config):
        pin = cfg_pin = config.get("pin")
        invert = False
        if pin[0] == "!":
            pin = pin[1:]
            invert = True
        chip_id = "gpiochip0"
        pin_parts = pin.split("/")
        if len(pin_parts) == 2:
            chip_id, pin = pin_parts
        elif len(pin_parts) == 1:
            pin = pin_parts[0]
        # Verify pin
        if not chip_id.startswith("gpiochip") or \
                not chip_id[-1].isdigit() or \
                not pin.startswith("gpio") or \
                not pin[4:].isdigit():
            raise config.error(
                f"Invalid Power Pin configuration: {cfg_pin}")
        pin = int(pin[4:])
        return pin, chip_id, invert

    def initialize(self):
        self.set_power("on" if self.initial_state else "off")

    def refresh_status(self):
        pass

    def set_power(self, state):
        try:
            self.line.set_value(int(state == "on"))
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state

    def close(self):
        self.line.release()


#  This implementation based off the work tplink_smartplug
#  script by Lubomir Stroetmann available at:
#
#  https://github.com/softScheck/tplink-smartplug
#
#  Copyright 2016 softScheck GmbH
class TPLinkSmartPlug(PowerDevice):
    START_KEY = 0xAB
    def __init__(self, config):
        super().__init__(config)
        self.request_mutex = Lock()
        self.addr = config.get("address").split('/')
        self.port = config.getint("port", 9999)

    async def _send_tplink_command(self, command):
        out_cmd = {}
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
        else:
            raise self.server.error(f"Invalid tplink command: {command}")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stream = IOStream(s)
        try:
            await stream.connect((self.addr[0], self.port))
            await stream.write(self._encrypt(out_cmd))
            data = await stream.read_bytes(2048, partial=True)
            length = struct.unpack(">I", data[:4])[0]
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

    def _encrypt(self, data):
        data = json.dumps(data)
        key = self.START_KEY
        res = struct.pack(">I", len(data))
        for c in data:
            val = key ^ ord(c)
            key = val
            res += bytes([val])
        return res

    def _decrypt(self, data):
        key = self.START_KEY
        res = ""
        for c in data:
            val = key ^ c
            key = c
            res += chr(val)
        return res

    async def initialize(self):
        await self.refresh_status()

    async def refresh_status(self):
        async with self.request_mutex:
            try:
                res = await self._send_tplink_command("info")
                if len(self.addr) == 2:
                    # TPLink device controls multiple devices
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

    async def set_power(self, state):
        async with self.request_mutex:
            try:
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
    def __init__(self, config):
        super().__init__(config, default_password="")
        self.output_id = config.getint("output_id", 1)
        self.timer = config.get("timer", "")

    async def _send_tasmota_command(self, command, password=None):
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

    async def _send_status_request(self):
        res = await self._send_tasmota_command("info")
        try:
            state = res[f"POWER{self.output_id}"].lower()
        except KeyError as e:
            if self.output_id == 1:
                state = res[f"POWER"].lower()
            else:
                raise KeyError(e)
        return state

    async def _send_power_request(self, state):
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
    def __init__(self, config):
        super().__init__(config, default_user="admin", default_password="")
        self.output_id = config.getint("output_id", 0)
        self.timer = config.get("timer", "")

    async def _send_shelly_command(self, command):
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

    async def _send_status_request(self):
        res = await self._send_shelly_command("info")
        state = res[f"ison"]
        timer_remaining = res[f"timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"

    async def _send_power_request(self, state):
        res = await self._send_shelly_command(state)
        state = res[f"ison"]
        timer_remaining = res[f"timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"


class HomeSeer(HTTPDevice):
    def __init__(self, config):
        super().__init__(config, default_user="admin", default_password="")
        self.device = config.getint("device")

    async def _send_homeseer(self, request, additional=""):
        url = (f"http://{self.user}:{self.password}@{self.addr}"
               f"/JSON?user={self.user}&pass={self.password}"
               f"&request={request}&ref={self.device}&{additional}")
        return await self._send_http_command(url, request)

    async def _send_status_request(self):
        res = await self._send_homeseer("getstatus")
        return res[f"Devices"][0]["status"].lower()

    async def _send_power_request(self, state):
        if state == "on":
            state_hs = "On"
        elif state == "off":
            state_hs = "Off"
        res = await self._send_homeseer("controldevicebylabel",
                                        f"label={state_hs}")
        return state


class HomeAssistant(HTTPDevice):
    def __init__(self, config):
        super().__init__(config, default_port=8123)
        self.device = config.get("device")
        self.token = config.get("token")

    async def _send_homeassistant_command(self, command):
        if command == "on":
            out_cmd = f"api/services/switch/turn_on"
            body = {"entity_id": self.device}
            method = "POST"
        elif command == "off":
            out_cmd = f"api/services/switch/turn_off"
            body = {"entity_id": self.device}
            method = "POST"
        elif command == "info":
            out_cmd = f"api/states/{self.device}"
            method = "GET"
        else:
            raise self.server.error(
                f"Invalid homeassistant command: {command}")
        url = f"http://{self.addr}:{self.port}/{out_cmd}"
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
            data = json_decode(response.body)
        except Exception:
            msg = f"Error sending homeassistant command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        return data

    async def _send_status_request(self):
        res = await self._send_homeassistant_command("info")
        return res[f"state"]

    async def _send_power_request(self, state):
        res = await self._send_homeassistant_command(state)
        return res[0][f"state"]

class Loxonev1(HTTPDevice):
    def __init__(self, config):
        super().__init__(config, default_user="admin",
                         default_password="admin")
        self.output_id = config.get("output_id", "")

    async def _send_loxonev1_command(self, command):
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

    async def _send_status_request(self):
        res = await self._send_loxonev1_command("info")
        state = res[f"LL"][f"value"]
        return "on" if int(state) == 1 else "off"

    async def _send_power_request(self, state):
        res = await self._send_loxonev1_command(state)
        state = res[f"LL"][f"value"]
        return "on" if int(state) == 1 else "off"


# The power component has multiple configuration sections
def load_component_multi(config):
    return PrinterPower(config)
