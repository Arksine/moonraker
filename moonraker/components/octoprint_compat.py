# OctoPrint API compatibility
#
# Copyright (C) 2021 Nickolas Grigoriadis <nagrigoriadis@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .klippy_apis import KlippyAPI as APIComp
    from .file_manager.file_manager import FileManager
    from .job_queue import JobQueue

OCTO_VERSION = '1.5.0'


class OctoPrintCompat:
    """
    Minimal implementation of the REST API as described here:
    https://docs.octoprint.org/en/master/api/index.html

    So that Cura OctoPrint plugin will function for:
    * Handshake
    * Upload gcode/ufp
    * Webcam config
    * Manual GCode submission
    * Heater temperatures
    """

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.software_version = self.server.get_app_args().get(
            'software_version')
        self.enable_ufp: bool = config.getboolean('enable_ufp', True)

        # Get webcam settings from config
        self.webcam: Dict[str, Any] = {
            'flipH': config.getboolean('flip_h', False),
            'flipV': config.getboolean('flip_v', False),
            'rotate90': config.getboolean('rotate_90', False),
            'streamUrl': config.get('stream_url', '/webcam/?action=stream'),
            'webcamEnabled': config.getboolean('webcam_enabled', True),
        }

        # Local variables
        self.klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        self.heaters: Dict[str, Dict[str, Any]] = {}
        self.last_print_stats: Dict[str, Any] = {}

        # Register status update event
        self.server.register_event_handler(
            'server:klippy_ready', self._init)
        self.server.register_event_handler(
            'server:status_update', self._handle_status_update)

        # Version & Server information
        self.server.register_endpoint(
            '/api/version', ['GET'], self._get_version,
            transports=['http'], wrap_result=False)
        self.server.register_endpoint(
            '/api/server', ['GET'], self._get_server,
            transports=['http'], wrap_result=False)

        # Login, User & Settings
        self.server.register_endpoint(
            '/api/login', ['POST'], self._post_login_user,
            transports=['http'], wrap_result=False)
        self.server.register_endpoint(
            '/api/currentuser', ['GET'], self._post_login_user,
            transports=['http'], wrap_result=False)
        self.server.register_endpoint(
            '/api/settings', ['GET'], self._get_settings,
            transports=['http'], wrap_result=False)

        # File operations
        # Note that file upload is handled in file_manager.py
        # TODO: List/info/select/delete files

        # Job operations
        self.server.register_endpoint(
            '/api/job', ['GET'], self._get_job,
            transports=['http'], wrap_result=False)
        # TODO: start/cancel/restart/pause jobs

        # Printer operations
        self.server.register_endpoint(
            '/api/printer', ['GET'], self._get_printer,
            transports=['http'], wrap_result=False)
        self.server.register_endpoint(
            '/api/printer/command', ['POST'], self._post_command,
            transports=['http'], wrap_result=False)
        # TODO: head/tool/bed/chamber specific read/issue

        # Printer profiles
        self.server.register_endpoint(
            '/api/printerprofiles', ['GET'], self._get_printerprofiles,
            transports=['http'], wrap_result=False)

        # Upload Handlers
        self.server.register_upload_handler(
            "/api/files/local", location_prefix="api/files/moonraker")
        self.server.register_endpoint(
            "/api/files/moonraker/(?P<relative_path>.+)", ['POST'],
            self._select_file, transports=['http'], wrap_result=False)

        # System
        # TODO: shutdown/reboot/restart operations

    async def _init(self) -> None:
        self.heaters = {}
        # Fetch heaters
        try:
            result: Dict[str, Any]
            sensors: List[str]
            result = await self.klippy_apis.query_objects({'heaters': None})
            sensors = result.get('heaters', {}).get('available_sensors', [])
        except self.server.error as e:
            logging.info(f'Error Configuring heaters: {e}')
            sensors = []
        # subscribe objects
        sub: Dict[str, Any] = {s: None for s in sensors}
        sub['print_stats'] = None
        result = await self.klippy_apis.subscribe_objects(sub)
        self.last_print_stats = result.get('print_stats', {})
        if sensors:
            self.heaters = {name: result.get(name, {}) for name in sensors}

    def _handle_status_update(self, status: Dict[str, Any]) -> None:
        if 'print_stats' in status:
            self.last_print_stats.update(status['print_stats'])
        for heater_name, data in self.heaters.items():
            if heater_name in status:
                data.update(status[heater_name])

    def printer_state(self) -> str:
        klippy_state = self.server.get_klippy_state()
        if klippy_state in ["disconnected", "startup"]:
            return 'Offline'
        elif klippy_state != 'ready':
            return 'Error'
        return {
            'standby': 'Operational',
            'printing': 'Printing',
            'paused': 'Paused',
            'complete': 'Operational'
        }.get(self.last_print_stats.get('state', 'standby'), 'Error')

    def printer_temps(self) -> Dict[str, Any]:
        temps: Dict[str, Any] = {}
        for heater, data in self.heaters.items():
            name = 'bed'
            if heater.startswith('extruder'):
                try:
                    tool_no = int(heater[8:])
                except ValueError:
                    tool_no = 0
                name = f'tool{tool_no}'
            elif heater != "heater_bed":
                continue
            temps[name] = {
                'actual': round(data.get('temperature', 0.), 2),
                'offset': 0,
                'target': data.get('target', 0.),
            }
        return temps

    async def _get_version(self,
                           web_request: WebRequest
                           ) -> Dict[str, str]:
        """
        Version information
        """
        return {
            'server': OCTO_VERSION,
            'api': '0.1',
            'text': f'OctoPrint (Moonraker {self.software_version})',
        }

    async def _get_server(self,
                          web_request: WebRequest
                          ) -> Dict[str, Any]:
        """
        Server status
        """
        klippy_state = self.server.get_klippy_state()
        return {
            'server': OCTO_VERSION,
            'safemode': (
                None if klippy_state == 'ready' else 'settings')
        }

    async def _post_login_user(self,
                               web_request: WebRequest
                               ) -> Dict[str, Any]:
        """
        Confirm session login.

        Since we only support apikey auth, do nothing.
        Report hardcoded user called _api
        """
        return {
            '_is_external_client': False,
            '_login_mechanism': 'apikey',
            'name': '_api',
            'active': True,
            'user': True,
            'admin': True,
            'apikey': None,
            'permissions': [],
            'groups': ['admins', 'users'],
        }

    async def _get_settings(self,
                            web_request: WebRequest
                            ) -> Dict[str, Any]:
        """
        Used to parse OctoPrint capabilities
        """
        settings = {
            'plugins': {},
            'feature': {
                'sdSupport': False,
                'temperatureGraph': False
            },
            'webcam': self.webcam,
        }
        if self.enable_ufp:
            settings['plugins'] = {
                'UltimakerFormatPackage': {
                    'align_inline_thumbnail': False,
                    'inline_thumbnail': False,
                    'inline_thumbnail_align_value': 'left',
                    'inline_thumbnail_scale_value': '50',
                    'installed': True,
                    'installed_version': '0.2.2',
                    'scale_inline_thumbnail': False,
                    'state_panel_thumbnail': True,
                },
            }
        return settings

    async def _get_job(self,
                       web_request: WebRequest
                       ) -> Dict[str, Any]:
        """
        Get current job status
        """
        return {
            'job': {
                'file': {'name': None},
                'estimatedPrintTime': None,
                'filament': {'length': None},
                'user': None,
            },
            'progress': {
                'completion': None,
                'filepos': None,
                'printTime': None,
                'printTimeLeft': None,
                'printTimeOrigin': None,
            },
            'state': self.printer_state()
        }

    async def _get_printer(self,
                           web_request: WebRequest
                           ) -> Dict[str, Any]:
        """
        Get Printer status
        """
        state = self.printer_state()
        return {
            'temperature': self.printer_temps(),
            'state': {
                'text': state,
                'flags': {
                    'operational': state not in ['Error', 'Offline'],
                    'paused': state == 'Paused',
                    'printing': state == 'Printing',
                    'cancelling': state == 'Cancelling',
                    'pausing': False,
                    'error': state == 'Error',
                    'ready': state == 'Operational',
                    'closedOrError': state in ['Error', 'Offline'],
                },
            },
        }

    async def _post_command(self,
                            web_request: WebRequest
                            ) -> Dict:
        """
        Request to run some gcode command
        """
        commands: List[str] = web_request.get('commands', [])
        for command in commands:
            logging.info(f'Executing GCode: {command}')
            try:
                await self.klippy_apis.run_gcode(command)
            except self.server.error:
                msg = f"Error executing GCode {command}"
                logging.exception(msg)

        return {}

    async def _get_printerprofiles(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        """
        Get Printer profiles
        """
        return {
            'profiles': {
                '_default': {
                    'id': '_default',
                    'name': 'Default',
                    'color': 'default',
                    'model': 'Default',
                    'default': True,
                    'current': True,
                    'heatedBed': 'heater_bed' in self.heaters,
                    'heatedChamber': 'chamber' in self.heaters,
                    'axes': {
                        'x': {
                            'speed': 6000.,
                            'inverted': False
                        },
                        'y': {
                            'speed': 6000.,
                            'inverted': False
                        },
                        'z': {
                            'speed': 6000.,
                            'inverted': False
                        },
                        'e': {
                            'speed': 300.,
                            'inverted': False
                        }
                    }
                }
            }
        }

    async def _select_file(self,
                           web_request: WebRequest
                           ) -> None:
        command: str = web_request.get_str('command')
        rel_path: str = web_request.get_str('relative_path')
        root, filename = rel_path.strip("/").split("/", 1)
        fmgr: FileManager = self.server.lookup_component('file_manager')
        if command == "select":
            start_print: bool = web_request.get_boolean('print', False)
            if not start_print:
                # No-op, selecting a file has no meaning in Moonraker
                return
            if root != "gcodes":
                raise self.server.error(
                    "File must be located in the 'gcodes' root", 400)
            if not fmgr.check_file_exists(root, filename):
                raise self.server.error("File does not exist")
            try:
                ret = await self.klippy_apis.query_objects(
                    {'print_stats': None})
                pstate: str = ret['print_stats']['state']
            except self.server.error:
                pstate = "not_avail"
            started: bool = False
            if pstate not in ["printing", "paused", "not_avail"]:
                try:
                    await self.klippy_apis.start_print(filename)
                except self.server.error:
                    started = False
                else:
                    logging.debug(f"Job '{filename}' started via OctoPrint API")
                    started = True
            if not started:
                if fmgr.upload_queue_enabled():
                    job_queue: JobQueue = self.server.lookup_component(
                        'job_queue')
                    await job_queue.queue_job(filename, check_exists=False)
                    logging.debug(f"Job '{filename}' queued via OctoPrint API")
                else:
                    raise self.server.error("Conflict", 409)
        else:
            raise self.server.error(f"Unsupported Command: {command}")


def load_component(config: ConfigHelper) -> OctoPrintCompat:
    return OctoPrintCompat(config)
