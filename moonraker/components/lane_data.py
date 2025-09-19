# Support for posting filament changer data to so third-parties(slicers) can
# grab this information to use.
#
# Copyright (C) 2025 AJAX3D and Jim Madill <jcmadill1@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import logging

class LaneDataStore:
    def __init__(self, config):
        self._config = config
        self._server = config.get_server()
        self._logger = logging.getLogger("lane_data")
        self._loop = asyncio.get_event_loop()
        self._lane_data = {}  # key: lane number or id, value: data dict

    async def initialize(self):
        self._server.register_endpoint(
            "/machine/lane_data", ["GET"], self._handle_get_data
        )
        self._server.register_endpoint(
            "/machine/set_lane_data", ["POST"], self._handle_set_data
        )

    async def _handle_get_data(self, request):
        return {
            "status": "ok",
            "lanes": self._lane_data
        }

    async def _handle_set_data(self, request):
        data = request.get("data", {})
        if not isinstance(data, dict):
            return {"status": "error", "error": "Invalid JSON data."}

        for lane_id, lane_info in data.items():
            if not isinstance(lane_info, dict):
                continue
            self._lane_data[lane_id] = {
                "color": lane_info.get("color"),
                "td": lane_info.get("td", None),
                "material": lane_info.get("material"),
                "bed_temp": lane_info.get("bed_temp"),
                "nozzle_temp": lane_info.get("nozzle_temp"),
                "scan_time": lane_info.get("scan_time", None),
                "lane": lane_info.get("lane"),
            }
        return {"status": "ok"}

def load_component(config):
    component = LaneDataStore(config)
    asyncio.get_event_loop().create_task(component.initialize())
    return component
