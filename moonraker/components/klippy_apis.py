# Helper for Moonraker to Klippy API calls.
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
from utils import SentinelClass
from websockets import WebRequest, Subscribable

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Optional,
    Dict,
    List,
    TypeVar,
    Mapping,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from klippy_connection import KlippyConnection as Klippy
    Subscription = Dict[str, Optional[List[Any]]]
    _T = TypeVar("_T")

INFO_ENDPOINT = "info"
ESTOP_ENDPOINT = "emergency_stop"
LIST_EPS_ENDPOINT = "list_endpoints"
GC_OUTPUT_ENDPOINT = "gcode/subscribe_output"
GCODE_ENDPOINT = "gcode/script"
SUBSCRIPTION_ENDPOINT = "objects/subscribe"
STATUS_ENDPOINT = "objects/query"
OBJ_LIST_ENDPOINT = "objects/list"
REG_METHOD_ENDPOINT = "register_remote_method"
SENTINEL = SentinelClass.get_instance()

class KlippyAPI(Subscribable):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.klippy: Klippy = self.server.lookup_component("klippy_connection")
        app_args = self.server.get_app_args()
        self.version = app_args.get('software_version')
        # Maintain a subscription for all moonraker requests, as
        # we do not want to overwrite them
        self.host_subscription: Subscription = {}

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

    async def _gcode_pause(self, web_request: WebRequest) -> str:
        return await self._send_klippy_request("pause_resume/pause", {})

    async def _gcode_resume(self, web_request: WebRequest) -> str:
        return await self._send_klippy_request("pause_resume/resume", {})

    async def _gcode_cancel(self, web_request: WebRequest) -> str:
        return await self._send_klippy_request("pause_resume/cancel", {})

    async def _gcode_start_print(self, web_request: WebRequest) -> str:
        filename: str = web_request.get_str('filename')
        return await self.start_print(filename)

    async def _gcode_restart(self, web_request: WebRequest) -> str:
        return await self.do_restart("RESTART")

    async def _gcode_firmware_restart(self, web_request: WebRequest) -> str:
        return await self.do_restart("FIRMWARE_RESTART")

    async def _send_klippy_request(self,
                                   method: str,
                                   params: Dict[str, Any],
                                   default: Any = SENTINEL
                                   ) -> Any:
        try:
            result = await self.klippy.request(
                WebRequest(method, params, conn=self))
        except self.server.error:
            if isinstance(default, SentinelClass):
                raise
            result = default
        return result

    async def run_gcode(self,
                        script: str,
                        default: Any = SENTINEL
                        ) -> str:
        params = {'script': script}
        result = await self._send_klippy_request(
            GCODE_ENDPOINT, params, default)
        return result

    async def start_print(self, filename: str) -> str:
        # WARNING: Do not call this method from within the following
        # event handlers:
        # klippy_identified, klippy_started, klippy_ready, klippy_disconnect
        # Doing so will result in a deadlock
        # XXX - validate that file is on disk
        if filename[0] == '/':
            filename = filename[1:]
        # Escape existing double quotes in the file name
        filename = filename.replace("\"", "\\\"")
        script = f'SDCARD_PRINT_FILE FILENAME="{filename}"'
        await self.klippy.wait_connected()
        return await self.run_gcode(script)

    async def do_restart(self, gc: str) -> str:
        # WARNING: Do not call this method from within the following
        # event handlers:
        # klippy_identified, klippy_started, klippy_ready, klippy_disconnect
        # Doing so will result in a deadlock
        # XXX - validate that file is on disk
        await self.klippy.wait_connected()
        try:
            result = await self.run_gcode(gc)
        except self.server.error as e:
            if str(e) == "Klippy Disconnected":
                result = "ok"
            else:
                raise
        return result

    async def list_endpoints(self,
                             default: Union[SentinelClass, _T] = SENTINEL
                             ) -> Union[_T, Dict[str, List[str]]]:
        return await self._send_klippy_request(
            LIST_EPS_ENDPOINT, {}, default)

    async def emergency_stop(self) -> str:
        return await self._send_klippy_request(ESTOP_ENDPOINT, {})

    async def get_klippy_info(self,
                              send_id: bool = False,
                              default: Union[SentinelClass, _T] = SENTINEL
                              ) -> Union[_T, Dict[str, Any]]:
        params = {}
        if send_id:
            ver = self.version
            params = {'client_info': {'program': "Moonraker", 'version': ver}}
        return await self._send_klippy_request(INFO_ENDPOINT, params, default)

    async def get_object_list(self,
                              default: Union[SentinelClass, _T] = SENTINEL
                              ) -> Union[_T, List[str]]:
        result = await self._send_klippy_request(
            OBJ_LIST_ENDPOINT, {}, default)
        if isinstance(result, dict) and 'objects' in result:
            return result['objects']
        return result

    async def query_objects(self,
                            objects: Mapping[str, Optional[List[str]]],
                            default: Union[SentinelClass, _T] = SENTINEL
                            ) -> Union[_T, Dict[str, Any]]:
        params = {'objects': objects}
        result = await self._send_klippy_request(
            STATUS_ENDPOINT, params, default)
        if isinstance(result, dict) and 'status' in result:
            return result['status']
        return result

    async def subscribe_objects(self,
                                objects: Mapping[str, Optional[List[str]]],
                                default: Union[SentinelClass, _T] = SENTINEL
                                ) -> Union[_T, Dict[str, Any]]:
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

    async def subscribe_gcode_output(self) -> str:
        template = {'response_template':
                    {'method': "process_gcode_response"}}
        return await self._send_klippy_request(GC_OUTPUT_ENDPOINT, template)

    async def register_method(self, method_name: str) -> str:
        return await self._send_klippy_request(
            REG_METHOD_ENDPOINT,
            {'response_template': {"method": method_name},
             'remote_method': method_name})

    def send_status(self,
                    status: Dict[str, Any],
                    eventtime: float
                    ) -> None:
        self.server.send_event("server:status_update", status)

def load_component(config: ConfigHelper) -> KlippyAPI:
    return KlippyAPI(config)
