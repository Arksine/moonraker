# Printer power manipulations
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
#   Sample printer_on script for pi:
#     #!/bin/bash
#     gpio export 23 out
#     gpio -g write 23 0
#     sleep 1
#     sudo service klipper restart
#
#   Sample printer_on script:
#     #!/bin/bash
#     gpio export 23 out
#     gpio -g write 23 1
#
#
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
    def __init__(self, server):
        self.server = server
        self.server.register_endpoint(
            "/printer/power/devices", "power_status", ['GET'],
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
        
        self.printer_status = "unknown"
        self.cmds = {}
    
    async def _handle_list_devices(self, path, method, args):
        output = {"devices": []}
        for dev in self.cmds:
            output['devices'].append(dev)
        return output

    async def _handle_machine_request(self, path, method, args):
        dev = args.get('dev')
        if dev not in self.cmds:
            return "device_not_found"
        
        if path == "/printer/power/status":
            cmd = self.cmds[dev]["status"]
        elif path == "/printer/power/on":
            cmd = self.cmds[dev]["on"]
        elif path == "/printer/power/off":
            cmd = self.cmds[dev]["off"]
        else:
            raise self.server.error("Unsupported power request")
        
        if cmd == None:
            raise self.server.error("Command not configured in printer.cfg under [moonraker_plugins power] for device " + dev)
        
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, self.get_cmd_output)
        try:
            await scmd.run()
        except Exception:
            logging.exception("Error running cmd '%s'" % (cmd))
        
        if path == "/printer/power/status":
            return {"power": self.printer_status}
        else:
            return "ok"
    
    def get_cmd_output(self, partial_output):
        if "on" in str(partial_output):
            self.printer_status = "on"
        elif "off" in str(partial_output):
            self.printer_status = "off"
        else:
            self.printer_status = "unknown"
     
    def load_config(self, config):
        if "devices" not in config.keys():
            return
        
        devices = config["devices"].split(',')
        keys = [ "status", "off", "on"]
        
        logging.info("Power plugin loading devices: " + str(devices))
        
        for dev in devices:
            dev = dev.strip()
            self.cmds[dev] = {}
            
            for key in keys:
                logging.info(dev + "_" + key)
                id = dev + "_" + key
                self.cmds[dev][key] = config[id] if id in config.keys() else None
        

def load_plugin(server):
    return PrinterPower(server)
