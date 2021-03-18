# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from tornado.ioloop import IOLoop

ALLOWED_SERVICES = ["moonraker", "klipper", "webcamd"]

class Machine:
    def __init__(self, config):
        self.server = config.get_server()
        self.server.register_endpoint(
            "/machine/reboot", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/shutdown", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/services/restart", ['POST'],
            self._handle_service_request)
        self.server.register_endpoint(
            "/machine/services/stop", ['POST'],
            self._handle_service_request)
        self.server.register_endpoint(
            "/machine/services/start", ['POST'],
            self._handle_service_request)

        # Register remote methods
        self.server.register_remote_method(
            "shutdown_machine", self.shutdown_machine)
        self.server.register_remote_method(
            "reboot_machine", self.reboot_machine)

    async def _handle_machine_request(self, web_request):
        ep = web_request.get_endpoint()
        if ep == "/machine/shutdown":
            await self.shutdown_machine()
        elif ep == "/machine/reboot":
            await self.reboot_machine()
        else:
            raise self.server.error("Unsupported machine request")
        return "ok"

    async def shutdown_machine(self):
        await self._execute_cmd("sudo shutdown now")

    async def reboot_machine(self):
        await self._execute_cmd("sudo shutdown -r now")

    async def do_service_action(self, action, service_name):
        await self._execute_cmd(
            f'sudo systemctl {action} {service_name}')

    async def _handle_service_request(self, web_request):
        name = web_request.get('service').lower()
        action = web_request.get_endpoint().split('/')[-1]
        if name == "moonraker":
            if action != "restart":
                raise self.server.error(
                    f"Service action '{action}' not available for moonraker")
            IOLoop.current().spawn_callback(
                self.do_service_action, action, name)
        elif name in ALLOWED_SERVICES:
            await self.do_service_action(action, name)
        else:
            raise self.server.error(
                f"Invalid argument recevied for 'name': {name}")
        return "ok"

    async def _execute_cmd(self, cmd):
        shell_command = self.server.lookup_component('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")
            raise

def load_component(config):
    return Machine(config)
