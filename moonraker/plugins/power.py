# Printer power manipulations
#
# Add to printer.cfg:
#   [moonraker_plugin power]
#   cmd_status: /script/to/power/status
#   cmd_off: /script/to/power/off
#   cmd_on: /script/to/power/on
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

    async def _handle_machine_request(self, path, method, args):
        if path == "/printer/power/status":
            cmd = self.cmds["status"]
        elif path == "/printer/power/on":
            cmd = self.cmds["on"]
        elif path == "/printer/power/off":
            cmd = self.cmds["off"]
        else:
            raise self.server.error("Unsupported machine request")
        
        if cmd == None:
            raise self.server.error("Command not configured in printer.cfg under [moonraker_plugins power]")
        
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, self.get_cmd_output)
        try:
            await scmd.run()
        except Exception:
            logging.exception("Error running cmd '%s'" % (cmd))
        
        if path == "/printer/power/status":
            return {"printer_power": self.printer_status}
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
        logging.info("Config: " + str(config))
        
        keys = [ "status", "off", "on"]
        
        for key in keys:
          self.cmds[key] = config["cmd_" + key] if key in config else None
        
        logging.info("Config: " + str(self.cmds))
        

def load_plugin(server):
    return PrinterPower(server)
