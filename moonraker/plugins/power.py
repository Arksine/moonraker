# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os
import asyncio
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
        prefix_sections = config.get_prefix_sections("power")
        logging.info(f"Power plugin loading devices: f{prefix_sections}")
        devices = {}
        for section in prefix_sections:
            dev = GpioDevice(config[section])
            devices[dev.name] = dev
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.initialize_devices, devices)

    async def _handle_list_devices(self, web_request):
        output = {"devices": list(self.devices.keys())}
        return output

    async def _handle_power_request(self, web_request):
        args = web_request.get_args()
        ep = web_request.get_endpoint()
        if len(args) == 0:
            if ep == "/machine/gpio_power/status":
                args = self.devices
            else:
                return "no_devices"

        result = {}
        req = ep.split("/")[-1]
        for dev in args:
            if req not in ("on", "off", "status"):
                raise self.server.error("Unsupported power request")
            if (await self._power_dev(dev, req)):
                result[dev] = self.devices[dev].status
            else:
                result[dev] = "device_not_found"
        return result

    async def _power_dev(self, dev, req):
        if dev not in self.devices:
            return False
        if req in ["on", "off"]:
            await self.devices[dev].power(req)

            self.server.send_event("gpio_power:power_changed", {
                "device": dev,
                "status": req
            })
        elif req != "status":
            raise self.server.error("Unsupported power request")
        ret = self.devices[dev].refresh_status()
        if asyncio.iscoroutine(ret):
            await ret
        return True

    async def initialize_devices(self, devices):
        for name, device in devices.items():
            try:
                await device.initialize()
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

    async def add_device(self, name, device):
        if name in self.devices:
            raise self.server.error(
                f"Device [{name}] already configured")
        await device.initialize()
        self.devices[name] = device


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


class GpioDevice:
    def __init__(self, config):
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]
        self.status = None
        cfg_pin = pin = config.get("pin")
        self.invert = False
        if pin[0] == "!":
            pin = pin[1:]
            self.invert = True
        self.consumer = "gpiochip0"
        pin_parts = pin.split("/")
        self.pin = ""
        if len(pin_parts) == 2:
            self.consumer, self.pin = pin_parts
        elif len(pin_parts) == 1:
            self.pin = pin_parts[0]
        # Verify pin
        if not self.consumer.startswith("gpiochip") or \
                not self.consumer[-1].isdigit() or \
                not self.pin.startswith("gpio") or \
                not self.pin[4:].isdigit():
            raise config.error(
                f"Invalid Power Pin configuration: {cfg_pin}")
        self.pin = int(self.pin[4:])

    async def initialize(self):
        await GPIO.setup_pin(self.pin, int(self.invert))
        self.refresh_status()

    def refresh_status(self):
        self.status = GPIO.is_pin_on(self.pin)

    async def power(self, status):
        await GPIO.verify_pin(self.pin, int(self.invert))
        GPIO.set_pin_value(self.pin, int(status == "on"))

# The power plugin has multiple configuration sections
def load_plugin_multi(config):
    return PrinterPower(config)
