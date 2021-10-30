# Axes distance tracking
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import time

class AxesDistance:
    def __init__(self, config):
        self.server = config.get_server()
        self.database = self.server.lookup_component("database")

        self.total_dist = self.database.get_item(
            "moonraker", "axes_distance",
            {
                'x': 0,
                'y': 0.,
                'z': 0.,
                'e': 0.,
                'tracked_since': 0.,
            })
        self.cur_pos = {
            'x': 0.,
            'y': 0.,
            'z': 0.,
            'e': 0.
        }

        if self.total_dist['tracked_since'] == 0:
            self.total_dist['tracked_since'] = time.time()

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)

        self.server.register_endpoint(
            "/server/axes_distance", ['GET', 'DELETE'],
                self._handle_totals)

    async def _init_ready(self):
        klippy_apis = self.server.lookup_component('klippy_apis')
        sub = {"gcode_move": ["position"], "toolhead": ["homed_axes"]}
        try:
            result = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            raise self.server.error(f"Error subscribing to print_stats")

        pos = result.get("gcode_move", {"position": [0,0,0,0]})['position']
        self.cur_pos = {
            'x': pos[0],
            'y': pos[1],
            'z': pos[2],
            'e': pos[3],
        }
        toolhead = result.get("toolhead", {"homed_axes": ""})
        self.homed_axes = toolhead.get("homed_axes", "")

    async def _status_update(self, data):
        if "toolhead" in data and "homed_axes" in data['toolhead']:
            self.homed_axes = data['toolhead']['homed_axes']

        gcode = data.get("gcode_move", {})
        if "position" not in gcode:
            return
        pos = gcode['position']

        i = 0
        for axis in ['x','y','z','e']:
            if axis in self.homed_axes or axis == 'e':
                self.total_dist[axis] += abs(self.cur_pos[axis] - float(pos[i]))
            self.cur_pos[axis] = float(pos[i])
            i += 1

    async def _handle_totals(self, web_request):
        action = web_request.get_action()
        if action == "GET":
            return self.total_dist
        if action == "DELETE":
            self.total_dist = {
                'x': 0,
                'y': 0.,
                'z': 0.,
                'e': 0.,
                'tracked_since': time.time(),
            }
            self.database.insert_item(
                "moonraker", "axes_distance", self.total_dist)
            return "ok"

    def on_exit(self):
        self.database.insert_item("moonraker", "axes_distance", self.total_dist)

def load_component(config):
    return AxesDistance(config)
