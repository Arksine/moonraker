# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

class Machine:
    def __init__(self, server):
        self.server = server
        self.server.register_endpoint(
            "/machine/reboot", "machine_reboot", ['POST'],
            self._handle_machine_request)
        self.server.register_endpoint(
            "/machine/shutdown", "machine_shutdown", ['POST'],
            self._handle_machine_request)

    async def _handle_machine_request(self, path, method, args):
        if path == "/machine/shutdown":
            cmd = "sudo shutdown now"
        elif path == "/machine/reboot":
            cmd = "sudo reboot now"
        else:
            raise self.server.error("Unsupported machine request")
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception("Error running cmd '%s'" % (cmd))
        return "ok"

def load_plugin(server):
    return Machine(server)
