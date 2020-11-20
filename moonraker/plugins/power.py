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
from tornado import gen
from tornado.httpclient import AsyncHTTPClient
from tornado.escape import json_decode

class PrinterPower:
    def __init__(self, config):
        self.server = config.get_server()
        self.server.register_endpoint(
            "/machine/device_power/devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/machine/device_power/status", ['GET'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/machine/device_power/on", ['POST'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/machine/device_power/off", ['POST'],
            self._handle_power_request)
        self.server.register_remote_method(
            "set_device_power", self.set_device_power)

        self.chip_factory = GpioChipFactory()
        self.current_dev = None
        self.devices = {}
        prefix_sections = config.get_prefix_sections("power")
        logging.info(f"Power plugin loading devices: {prefix_sections}")
        for section in prefix_sections:
            cfg = config[section]
            dev_type = cfg.get("type")
            if dev_type == "gpio":
                dev = GpioDevice(cfg, self.chip_factory)
            elif dev_type == "tplink_smartplug":
                dev = TPLinkSmartPlug(cfg)
            elif dev_type == "tasmota":
                dev = Tasmota(cfg)
            else:
                raise config.error(f"Unsupported Device Type: {dev_type}")
            self.devices[dev.get_name()] = dev

    async def _handle_list_devices(self, web_request):
        dev_list = [d.get_device_info() for d in self.devices.values()]
        output = {"devices": dev_list}
        return output

    async def _handle_power_request(self, web_request):
        args = web_request.get_args()
        ep = web_request.get_endpoint()
        if not args:
            raise self.server.error("No arguments provided")
        requsted_devs = {k: self.devices.get(k, None) for k in args}
        result = {}
        req = ep.split("/")[-1]
        for name, device in requsted_devs.items():
            if device is not None:
                result[name] = await self._process_request(device, req)
            else:
                result[name] = "device_not_found"
        return result

    async def _process_request(self, device, req):
        if req in ["on", "off"]:
            ret = device.set_power(req)
            if asyncio.iscoroutine(ret):
                await ret
            dev_info = device.get_device_info()
            self.server.send_event("gpio_power:power_changed", dev_info)
        elif req == "status":
            ret = device.refresh_status()
            if asyncio.iscoroutine(ret):
                await ret
            dev_info = device.get_device_info()
        else:
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

class GpioDevice:
    def __init__(self, config, chip_factory):
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]
        self.state = "init"
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
        self.set_power("off")

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
        pass

    def get_name(self):
        return self.name

    def get_device_info(self):
        return {
            'device': self.name,
            'status': self.state,
            'type': "gpio"
        }

    def refresh_status(self):
        try:
            val = self.line.get_value()
        except Exception:
            self.state = "error"
            msg = f"Error Refeshing Device Status: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = "on" if val else "off"

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
class TPLinkSmartPlug:
    START_KEY = 0xAB
    def __init__(self, config):
        self.server = config.get_server()
        self.addr = config.get("address")
        self.port = config.getint("port", 9999)
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]
        self.state = "init"
        IOLoop.current().spawn_callback(self.initialize)

    async def _send_tplink_command(self, command):
        out_cmd = {}
        if command in ["on", "off"]:
            out_cmd = {'system': {'set_relay_state':
                       {'state': int(command == "on")}}}
        elif command == "info":
            out_cmd = {'system': {'get_sysinfo': {}}}
        else:
            raise self.server.error(f"Invalid tplink command: {command}")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stream = IOStream(s)
        try:
            await stream.connect((self.addr, self.port))
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

    def get_name(self):
        return self.name

    def get_device_info(self):
        return {
            'device': self.name,
            'status': self.state,
            'type': "tplink_smartplug"
        }

    async def refresh_status(self):
        try:
            res = await self._send_tplink_command("info")
            state = res['system']['get_sysinfo']['relay_state']
        except Exception:
            self.state = "error"
            msg = f"Error Refeshing Device Status: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = "on" if state else "off"

    async def set_power(self, state):
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


class Tasmota:
    def __init__(self, config):
        self.server = config.get_server()
        self.addr = config.get("address")
        self.output_id = config.getint("output_id", 1)
        self.password = config.get("password", "")
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]
        self.state = "init"
        IOLoop.current().spawn_callback(self.initialize)

    async def _send_tasmota_command(self, command, password=None):
        if command in ["on", "off"]:
            out_cmd = f"Power{self.output_id}%20{command}"
        elif command == "info":
            out_cmd = "Power{self.output_id}"
        else:
            raise self.server.error(f"Invalid tasmota command: {command}")
        
        url = f"http://{self.addr}/cm?user=admin&password={self.password}&cmnd={out_cmd}"
        data = ""
        http_client = AsyncHTTPClient()
        try:
            response = await http_client.fetch(url)
            data = json_decode(response.body)
        except Exception:
            msg = f"Error sending tplink command: {command}"
            logging.exception(msg)
            raise self.server.error(msg)
        return data

    async def initialize(self):
        await self.refresh_status()

    def get_name(self):
        return self.name

    def get_device_info(self):
        return {
            'device': self.name,
            'status': self.state,
            'type': "tasmota"
        }

    async def refresh_status(self):
        try:
            res = await self._send_tasmota_command("info")
            state = res[f"Power{self.output_id}"].lower()
        except Exception:
            self.state = "error"
            msg = f"Error Refeshing Device Status: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state

    async def set_power(self, state):
        try:
            res = await self._send_tasmota_command(state)
            state = res[f"Power{self.output_id}"].lower()
        except Exception:
            self.state = "error"
            msg = f"Error Setting Device Status: {self.name} to {state}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state

# The power plugin has multiple configuration sections
def load_plugin_multi(config):
    return PrinterPower(config)
