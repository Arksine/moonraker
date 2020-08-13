# Map HTTP/Websocket APIs for specific gcode tasks
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

GCODE_ENDPOINT = "gcode/script"

class GCodeAPIs:
    def __init__(self, config):
        self.server = config.get_server()

        # Register GCode Endpoints
        self.server.register_endpoint(
            "/printer/print/pause", "printer_print_pause", ['POST'],
            self.gcode_pause)
        self.server.register_endpoint(
            "/printer/print/resume", "printer_print_resume", ['POST'],
            self.gcode_resume)
        self.server.register_endpoint(
            "/printer/print/cancel", "printer_print_cancel", ['POST'],
            self.gcode_cancel)
        self.server.register_endpoint(
            "/printer/print/start", "printer_print_start", ['POST'],
            self.gcode_start_print)
        self.server.register_endpoint(
            "/printer/restart", "printer_restart", ['POST'],
            self.gcode_restart)
        self.server.register_endpoint(
            "/printer/firmware_restart", "printer_firmware_restart", ['POST'],
            self.gcode_firmware_restart)

    async def _send_gcode(self, script):
        args = {'script': script}
        result = await self.server.make_request(GCODE_ENDPOINT, args)
        return result

    async def gcode_pause(self, path, method, args):
        return await self._send_gcode("PAUSE")

    async def gcode_resume(self, path, method, args):
        return await self._send_gcode("RESUME")

    async def gcode_cancel(self, path, method, args):
        return await self._send_gcode("CANCEL_PRINT")

    async def gcode_start_print(self, path, method, args):
        filename = args.get('filename')
        # XXX - validate that file is on disk

        if filename[0] == '/':
            filename = filename[1:]
        script = "SDCARD_PRINT_FILE FILENAME=" + filename
        return await self._send_gcode(script)

    async def gcode_restart(self, path, method, args):
        return await self._do_restart("RESTART")

    async def gcode_firmware_restart(self, path, method, args):
        return await self._do_restart("FIRMWARE_RESTART")

    async def _do_restart(self, gc):
        try:
            result = await self._send_gcode(gc)
        except self.server.error as e:
            if str(e) == "Klippy Disconnected":
                result = "ok"
            else:
                raise
        return result

def load_plugin(config):
    return GCodeAPIs(config)
