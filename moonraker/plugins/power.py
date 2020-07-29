# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import os
import time

class PrinterPower:
    def __init__(self, server):
        self.server = server
        self.server.register_endpoint(
            "/printer/power/devices", "power_devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/printer/power/status", "power_status", ['GET'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/printer/power/on", "power_on", ['POST'],
            self._handle_power_request)
        self.server.register_endpoint(
            "/printer/power/off", "power_off", ['POST'],
            self._handle_power_request)

        self.current_dev = None
        self.devices = {}

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
            if path == "/printer/power/status":
                args = self.devices
            else:
                return "no_devices"

        result = {}
        for dev in args:
            if dev not in self.devices:
                result[dev] = "device_not_found"
                continue

            GPIO.verify_pin(self.devices[dev]["pin"],
                            self.devices[dev]["active_low"])
            if path == "/printer/power/on":
                GPIO.set_pin_value(self.devices[dev]["pin"], 1)
            elif path == "/printer/power/off":
                GPIO.set_pin_value(self.devices[dev]["pin"], 0)
            elif path != "/printer/power/status":
                raise self.server.error("Unsupported power request")

            self.devices[dev]["status"] = GPIO.is_pin_on(
                self.devices[dev]["pin"])

            result[dev] = self.devices[dev]["status"]
        return result

    def load_config(self, config):
        if "devices" not in config.keys():
            return

        devices = config["devices"].split(',')
        logging.info("Power plugin loading devices: " + str(devices))

        for dev in devices:
            dev = dev.strip()
            if dev + "_pin" not in config.keys():
                logging.info(
                    "Power plugin: ERR " + dev + " does not have a pin defined")
                continue

            name = config[dev + "_name"] if dev + "_name" in config.keys() \
                else dev
            active_low = 1 if dev+"_active_low" in config.keys() and \
                config[dev+"_active_low"] == "True" else 0
            self.devices[dev] = {
                "name": name,
                "pin": int(config[dev + "_pin"]),
                "active_low": active_low,
                "status": None
            }

            try:
                logging.debug(
                    "Attempting to configure pin GPIO%d"
                    % (self.devices[dev]["pin"]))
                GPIO.setup_pin(
                    self.devices[dev]["pin"], self.devices[dev]["active_low"])
                self.devices[dev]["status"] = GPIO.is_pin_on(
                    self.devices[dev]["pin"])
            except Exception:
                logging.info(
                    "Power plugin: ERR Problem configuring the output pin for"
                    " device %s. Removing device" % (dev))
                self.devices.pop(dev, None)
                continue

class GPIO:
    gpio_root = "/sys/class/gpio"

    @staticmethod
    def _set_gpio_option(gpio, option, value):
        GPIO._write(
            os.path.join(GPIO.gpio_root, "gpio%d" % (gpio), option),
            value
        )

    @staticmethod
    def _get_gpio_option(pin, option):
        return GPIO._read(
            os.path.join(GPIO.gpio_root, "gpio%d" % (pin), option)
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
    def verify_pin(pin, active_low=1):
        gpiopath = os.path.join(GPIO.gpio_root, "gpio%d" % (pin))
        if not os.path.exists(gpiopath):
            logging.info("Re-intializing GPIO%d" % (pin))
            GPIO.setup_pin(pin, active_low)
            return

        if GPIO._get_gpio_option(pin, "active_low").strip() != str(active_low):
            GPIO._set_gpio_option(pin, "active_low", active_low)

        if GPIO._get_gpio_option(pin, "direction").strip() != "out":
            GPIO._set_gpio_option(pin, "direction", "out")

    @staticmethod
    def setup_pin(pin, active_low=1):
        pin = int(pin)
        active_low = 1 if active_low == 1 else 0

        gpiopath = os.path.join(GPIO.gpio_root, "gpio%d" % (pin))
        if not os.path.exists(gpiopath):
            GPIO._write(
                os.path.join(GPIO.gpio_root, "export"),
                pin)
            logging.info("Waiting for GPIO%d to initialize" % (pin))
            while os.stat(os.path.join(
                    GPIO.gpio_root, "gpio%dactive_low") % (pin)).st_gid == 0:
                time.sleep(.1)

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


def load_plugin(server):
    return PrinterPower(server)
