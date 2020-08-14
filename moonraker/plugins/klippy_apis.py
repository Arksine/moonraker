# Helper for Moonraker to Klippy API calls.
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

INFO_ENDPOINT = "info"
ESTOP_ENDPOINT = "emergency_stop"
LIST_EPS_ENDPOINT = "list_endpoints"
GC_OUTPUT_ENDPOINT = "gcode/subscribe_output"
GCODE_ENDPOINT = "gcode/script"
SUBSCRIPTION_ENDPOINT = "objects/subscribe"
STATUS_ENDPOINT = "objects/query"
OBJ_LIST_ENDPOINT = "objects/list"

class Sentinel:
    pass

class KlippyAPI:
    def __init__(self, config):
        self.server = config.get_server()

        # Register GCode Aliases
        self.server.register_endpoint(
            "/printer/print/pause", "printer_print_pause", ['POST'],
            self._gcode_pause)
        self.server.register_endpoint(
            "/printer/print/resume", "printer_print_resume", ['POST'],
            self._gcode_resume)
        self.server.register_endpoint(
            "/printer/print/cancel", "printer_print_cancel", ['POST'],
            self._gcode_cancel)
        self.server.register_endpoint(
            "/printer/print/start", "printer_print_start", ['POST'],
            self._gcode_start_print)
        self.server.register_endpoint(
            "/printer/restart", "printer_restart", ['POST'],
            self._gcode_restart)
        self.server.register_endpoint(
            "/printer/firmware_restart", "printer_firmware_restart", ['POST'],
            self._gcode_firmware_restart)

    async def _gcode_pause(self, path, method, args):
        return await self.run_gcode("PAUSE")

    async def _gcode_resume(self, path, method, args):
        return await self.run_gcode("RESUME")

    async def _gcode_cancel(self, path, method, args):
        return await self.run_gcode("CANCEL_PRINT")

    async def _gcode_start_print(self, path, method, args):
        filename = args.get('filename')
        return await self.start_print(filename)

    async def _gcode_restart(self, path, method, args):
        return await self.do_restart("RESTART")

    async def _gcode_firmware_restart(self, path, method, args):
        return await self.do_restart("FIRMWARE_RESTART")

    async def _send_klippy_request(self, method, params, default=Sentinel):
        try:
            result = await self.server.make_request(method, params)
        except self.server.error as e:
            if default == Sentinel:
                raise
            result = default
        return result

    async def run_gcode(self, script, default=Sentinel):
        params = {'script': script}
        result = await self._send_klippy_request(
            GCODE_ENDPOINT, params, default)
        return result

    async def start_print(self, filename):
        # XXX - validate that file is on disk
        if filename[0] == '/':
            filename = filename[1:]
        script = "SDCARD_PRINT_FILE FILENAME=" + filename
        return await self.run_gcode(script)

    async def do_restart(self, gc):
        try:
            result = await self.run_gcode(gc)
        except self.server.error as e:
            if str(e) == "Klippy Disconnected":
                result = "ok"
            else:
                raise
        return result

    async def list_endpoints(self, default=Sentinel):
        return await self._send_klippy_request(
            LIST_EPS_ENDPOINT, {}, default)

    async def emergency_stop(self, default=Sentinel):
        return await self._send_klippy_request(ESTOP_ENDPOINT, {}, default)

    async def get_klippy_info(self, default=Sentinel):
        return await self._send_klippy_request(INFO_ENDPOINT, {}, default)

    async def get_object_list(self, default=Sentinel):
        result = await self._send_klippy_request(
            OBJ_LIST_ENDPOINT, {}, default)
        if isinstance(result, dict) and 'objects' in result:
            return result['objects']
        return result

    async def query_objects(self, objects, default=Sentinel):
        params = {'objects': objects}
        result = await self._send_klippy_request(
            STATUS_ENDPOINT, params, default)
        if isinstance(result, dict) and 'status' in result:
            return result['status']
        return result

    async def subscribe_objects(self, objects, default=Sentinel):
        params = {'objects': objects}
        result = await self._send_klippy_request(
            SUBSCRIPTION_ENDPOINT, params, default)
        if isinstance(result, dict) and 'status' in result:
            return result['status']
        return result

    async def subscribe_gcode_output(self, default=Sentinel):
        template = {'response_template':
                    {'method': "process_gcode_response"}}
        return await self._send_klippy_request(
            GC_OUTPUT_ENDPOINT, template, default)

def load_plugin(config):
    return KlippyAPI(config)
