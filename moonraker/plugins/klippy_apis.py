# Helper for Moonraker to Klippy API calls.
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import utils
from websockets import WebRequest

INFO_ENDPOINT = "info"
ESTOP_ENDPOINT = "emergency_stop"
LIST_EPS_ENDPOINT = "list_endpoints"
GC_OUTPUT_ENDPOINT = "gcode/subscribe_output"
GCODE_ENDPOINT = "gcode/script"
SUBSCRIPTION_ENDPOINT = "objects/subscribe"
STATUS_ENDPOINT = "objects/query"
OBJ_LIST_ENDPOINT = "objects/list"
REG_METHOD_ENDPOINT = "register_remote_method"

class Sentinel:
    pass

class KlippyAPI:
    def __init__(self, config):
        self.server = config.get_server()

        # Maintain a subscription for all moonraker requests, as
        # we do not want to overwrite them
        self.host_subscription = {}

        # Register GCode Aliases
        self.server.register_endpoint(
            "/printer/print/pause", ['POST'], self._gcode_pause)
        self.server.register_endpoint(
            "/printer/print/resume", ['POST'], self._gcode_resume)
        self.server.register_endpoint(
            "/printer/print/cancel", ['POST'], self._gcode_cancel)
        self.server.register_endpoint(
            "/printer/print/start", ['POST'], self._gcode_start_print)
        self.server.register_endpoint(
            "/printer/restart", ['POST'], self._gcode_restart)
        self.server.register_endpoint(
            "/printer/firmware_restart", ['POST'], self._gcode_firmware_restart)

    async def _gcode_pause(self, web_request):
        return await self.run_gcode("PAUSE")

    async def _gcode_resume(self, web_request):
        return await self.run_gcode("RESUME")

    async def _gcode_cancel(self, web_request):
        return await self.run_gcode("CANCEL_PRINT")

    async def _gcode_start_print(self, web_request):
        filename = web_request.get_str('filename')
        return await self.start_print(filename)

    async def _gcode_restart(self, web_request):
        return await self.do_restart("RESTART")

    async def _gcode_firmware_restart(self, web_request):
        return await self.do_restart("FIRMWARE_RESTART")

    async def _send_klippy_request(self, method, params, default=Sentinel):
        try:
            result = await self.server.make_request(
                WebRequest(method, params, conn=self))
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
        # Escape existing double quotes in the file name
        filename = filename.replace("\"", "\\\"")
        script = f'SDCARD_PRINT_FILE FILENAME="{filename}"'
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

    async def get_klippy_info(self, send_id=False, default=Sentinel):
        params = {}
        if send_id:
            ver = utils.get_software_version()
            params = {'client_info': {'program': "Moonraker", 'version': ver}}
        return await self._send_klippy_request(INFO_ENDPOINT, params, default)

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
        for obj, items in objects.items():
            if obj in self.host_subscription:
                prev = self.host_subscription[obj]
                if items is None or prev is None:
                    self.host_subscription[obj] = None
                else:
                    uitems = list(set(prev) | set(items))
                    self.host_subscription[obj] = uitems
            else:
                self.host_subscription[obj] = items
        params = {'objects': self.host_subscription}
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

    async def register_method(self, method_name):
        return await self._send_klippy_request(
            REG_METHOD_ENDPOINT,
            {'response_template': {"method": method_name},
             'remote_method': method_name})

    def send_status(self, status):
        self.server.send_event("server:status_update", status)

def load_plugin(config):
    return KlippyAPI(config)
