# Printer power manipulations
#
#   GET  /printer/power/devices             # List devices
#   GET  /printer/power/status?dev=devname  # Get device status
#   POST /printer/power/on?dev=devname      # Turn device on
#   POST /printer/power/off?dev=devname     # Turn device off
#
# Add to printer.cfg:
#   [moonraker_plugin power]
#   devices: printer, led           (comma separated list of devices)
#   {dev}_status: /script/to/power/status   Swap {dev} for name of device under devices
#   printer_status: /script/to/printer/status
#   led_status: /script/to/led/status
#   {dev}_off: /script/to/power/off
#   {dev}_on: /script/to/power/on
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
#   Sample on script for pi:
#     #!/bin/bash
#     gpio export 23 out
#     gpio -g write 23 1
#
#   Sample off script:
#     #!/bin/bash
#     gpio export 23 out
#     gpio -g write 23 0
#
#
#   Sample status script:
#     #!/bin/bash
#     status=$(cat /sys/class/gpio/gpiochip0/subsystem/gpio23/value)
#     if [ $status -eq 0 ]
#     then
#             echo "Printer is on"
#             exit 0
#     fi
#     echo "Printer is off"
#     exit 1
#
#
#


import logging

class PrinterPower:
    paths = {
        "status":   "/printer/power/status",
        "on":       "/printer/power/on",
        "off":      "/printer/power/off"
    }


    def __init__(self, server):
        self.server = server
        self.server.register_endpoint(
            "/printer/power/devices", "power_devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/printer/power/status", "power_status", ['GET'],
            self._handle_machine_request)
        self.server.register_endpoint(
            "/printer/power/on", "power_on", ['POST'],
            self._handle_machine_request)
        self.server.register_endpoint(
            "/printer/power/off", "power_off", ['POST'],
            self._handle_machine_request)
        
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

    async def _handle_machine_request(self, path, method, args):
        if len(args) == 0:
            if path == self.paths['status']:
                args = self.devices
            else:
                return "no_devices"
         
        result = {}
        for dev in args:
            if dev not in self.devices:
                result[dev] = "device_not_found"
                continue
            
            if path == self.paths['status']:
                cmd = self.devices[dev]["cmds"]["status"]
            elif path == self.paths['on']:
                cmd = self.devices[dev]["cmds"]["on"]
                action = "on"
            elif path == self.paths['off']:
                cmd = self.devices[dev]["cmds"]["off"]
                action = "off"
            else:
                raise self.server.error("Unsupported power request")
            
            if cmd == None:
                raise self.server.error("Command not configured in printer.cfg under [moonraker_plugins power] for device " + dev)
            
            self.current_dev = dev
            shell_command = self.server.lookup_plugin('shell_command')
            scmd = shell_command.build_shell_command(cmd, self.get_cmd_output)
            try:
                await scmd.run()
            except Exception:
                logging.exception("Error running cmd '%s'" % (cmd))
        
            if path == self.paths['status']:
                result[dev] = self.devices[dev]["status"]
            else:
                result[dev] = action
        return result
    
    def get_cmd_output(self, partial_output):
        if "on" in str(partial_output):
            self.devices[self.current_dev]["status"] = "on"
        elif "off" in str(partial_output):
            self.devices[self.current_dev]["status"] = "off"
        else:
            self.devices[self.current_dev]["status"] = "unknown"
     
    def load_config(self, config):
        if "devices" not in config.keys():
            return
        
        devices = config["devices"].split(',')
        keys = [ "status", "off", "on"]
        
        logging.info("Power plugin loading devices: " + str(devices))
        
        for dev in devices:
            dev = dev.strip()
            name = config[dev + "_name"] if dev + "_name" in config.keys() else dev
            self.devices[dev] = {
                "name": name,
                "cmds": {}
            }

            for key in keys:
                logging.info(dev + "_" + key)
                id = dev + "_" + key
                self.devices[dev]["cmds"][key] = config[id] if id in config.keys() else None
        

def load_plugin(server):
    return PrinterPower(server)
