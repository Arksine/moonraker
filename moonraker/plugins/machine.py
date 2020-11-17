# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

class Machine:
    def __init__(self, config):
        self.server = config.get_server()
        self.server.register_endpoint(
            "/machine/reboot", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/shutdown", ['POST'], self._handle_machine_request)

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

    async def _execute_cmd(self, cmd):
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")

def load_plugin(config):
    return Machine(config)
