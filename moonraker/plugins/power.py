# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os
from tornado.ioloop import IOLoop
from tornado import gen

class PrinterPower:
    def __init__(self, config):
        self.server = config.get_server()
        self.server.register_endpoint(
            "/machine/gpio_power/devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/machine/gpio_power/status", ['GET'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/machine/gpio_power/on", ['POST'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/machine/gpio_power/off", ['POST'],
            self._handle_power_request)
        self.server.register_remote_method(
            "set_device_power", self.set_device_power)

        self.current_dev = None
        self.devices = {}
        dev_names = config.get('devices')
        dev_names = [d.strip() for d in dev_names.split(',') if d.strip()]
        logging.info("Power plugin loading devices: " + str(dev_names))
        devices = {}
        for dev in dev_names:
            pin = config.getint(dev + "_pin")
            name = config.get(dev + "_name", dev)
            active_low = config.getboolean(dev + "_active_low", False)
            devices[dev] = {
                "name": name,
                "pin": pin,
                "active_low": int(active_low),
                "status": None
            }
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.initialize_devices, devices)

    async def _handle_list_devices(self, path, method, args):
        output = {"devices": []}
        for dev in self.devices:
            output['devices'].append({
                "name": self.devices[dev]["name"],
                "id": dev
            })
        return output

    async def _handle_power_request(self, path, method, args):
        if len(args) == 0:
            if path == "/machine/gpio_power/status":
                args = self.devices
            else:
                return "no_devices"

        result = {}
        req = path.split("/")[-1]
        for dev in args:
            if path.startswith("/machine/gpio_power/"):
                res = await self._power_dev(dev, req)
                if res:
                    result[dev] = self.devices[dev]["status"]
                else:
                    result[dev] = "device_not_found"
            else:
                raise self.server.error("Unsupported power request")
        return result

    async def _power_dev(self, dev, req):
        if dev not in self.devices:
            return False

        await GPIO.verify_pin(self.devices[dev]["pin"],
                              self.devices[dev]["active_low"])
        if req in ["on", "off"]:
            val = 1 if req == "on" else 0
            GPIO.set_pin_value(self.devices[dev]["pin"], val)
            self.server.send_event("gpio_power:power_changed", {
                "device": dev,
                "status": req
            })
        elif req != "status":
            raise self.server.error("Unsupported power request")

        self.devices[dev]["status"] = GPIO.is_pin_on(
            self.devices[dev]["pin"])
        return True

    async def initialize_devices(self, devices):
        for name, device in devices.items():
            try:
                logging.debug(
                    f"Attempting to configure pin GPIO{device['pin']}")
                await GPIO.setup_pin(device["pin"], device["active_low"])
                device["status"] = GPIO.is_pin_on(device["pin"])
            except Exception:
                logging.exception(
                    f"Power plugin: ERR Problem configuring the output pin for"
                    f" device {name}. Removing device")
                continue
            self.devices[name] = device

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
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self._power_dev, device, status)

class GPIO:
    gpio_root = "/sys/class/gpio"

    @staticmethod
    def _set_gpio_option(pin, option, value):
        GPIO._write(
            os.path.join(GPIO.gpio_root, f"gpio{pin}", option),
            value
        )

    @staticmethod
    def _get_gpio_option(pin, option):
        return GPIO._read(
            os.path.join(GPIO.gpio_root, f"gpio{pin}", option)
        )

    @staticmethod
    def _write(file, data):
        with open(file, 'w') as f:
            f.write(str(data))
            f.flush()

    @staticmethod
    def _read(file):
        with open(file, 'r') as f:
            f.seek(0)
            return f.read().strip()

    @staticmethod
    async def verify_pin(pin, active_low=1):
        gpiopath = os.path.join(GPIO.gpio_root, f"gpio{pin}")
        if not os.path.exists(gpiopath):
            logging.info(f"Re-intializing GPIO{pin}")
            await GPIO.setup_pin(pin, active_low)
            return

        if GPIO._get_gpio_option(pin, "active_low").strip() != str(active_low):
            GPIO._set_gpio_option(pin, "active_low", active_low)

        if GPIO._get_gpio_option(pin, "direction").strip() != "out":
            GPIO._set_gpio_option(pin, "direction", "out")

    @staticmethod
    async def setup_pin(pin, active_low=1):
        pin = int(pin)
        active_low = 1 if active_low == 1 else 0

        gpiopath = os.path.join(GPIO.gpio_root, f"gpio{pin}")
        if not os.path.exists(gpiopath):
            GPIO._write(
                os.path.join(GPIO.gpio_root, "export"),
                pin)
            logging.info(f"Waiting for GPIO{pin} to initialize")
            while os.stat(os.path.join(
                    GPIO.gpio_root, f"gpio{pin}",
                    "active_low")).st_gid == 0:
                await gen.sleep(.1)

        if GPIO._get_gpio_option(pin, "active_low").strip() != str(active_low):
            GPIO._set_gpio_option(pin, "active_low", active_low)

        if GPIO._get_gpio_option(pin, "direction").strip() != "out":
            GPIO._set_gpio_option(pin, "direction", "out")


    @staticmethod
    def is_pin_on(pin):
        return "on" if int(GPIO._get_gpio_option(pin, "value")) else "off"

    @staticmethod
    def set_pin_value(pin, active):
        value = 1 if (active == 1) else 0
        GPIO._set_gpio_option(pin, "value", value)


def load_plugin(config):
    return PrinterPower(config)
