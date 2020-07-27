# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


import logging
import RPi.GPIO as GPIO

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

            if path == "/printer/power/status":
                # Update device status
                self.devices[dev]["status"] = self.is_pin_on(dev)
            elif path == "/printer/power/on":
                self.set_pin_level(dev, True)
            elif path == "/printer/power/off":
                self.set_pin_level(dev, False)
            else:
                raise self.server.error("Unsupported power request")

            result[dev] = self.devices[dev]["status"]
        return result

    def is_pin_on(self, dev):
        value = GPIO.input(self.devices[dev]["pin"])
        if (value == 1 and self.devices[dev]["active_high"] == True) or (value == 0 and self.devices[dev]["active_high"] == False):
            return "on"
        return "off"

    def set_pin_level(self, dev, active):
        value = 1 if (active == True and self.devices[dev]["active_high"] == True) or (active == False and self.devices[dev]["active_high"] == False) else 0
        GPIO.output(self.devices[dev]["pin"], value)
        self.devices[dev]["status"] = self.is_pin_on(dev)

    def load_config(self, config):
        if "devices" not in config.keys():
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False) #Ignore if other things are controlling GPIO pins

        devices = config["devices"].split(',')
        logging.info("Power plugin loading devices: " + str(devices))

        for dev in devices:
            dev = dev.strip()
            if dev + "_pin" not in config.keys():
                logging.info("Power plugin: ERR " + dev + " does not have a pin defined")
                continue

            self.devices[dev] = {
                "name": config[dev + "_name"] if dev + "_name" in config.keys() else dev,
                "pin": int(config[dev + "_pin"]),
                "active_high": False if dev+"_activehigh" in config.keys() and config[dev+"_activehigh"] == "False" else True,
                "status": None
            }

            try:
                GPIO.setup(self.devices[dev]["pin"], GPIO.OUT)
                self.devices[dev]["status"] = self.is_pin_on(dev)
            except:
                logging.info("Power plugin: ERR Problem configuring the output pin for device " + dev + ". Removing device")
                self.devices.pop(dev, None)
                continue


def load_plugin(server):
    return PrinterPower(server)
