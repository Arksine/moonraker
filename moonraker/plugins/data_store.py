# Klipper data logging and storage storage
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import time
from collections import deque
from tornado.ioloop import IOLoop, PeriodicCallback

TEMPERATURE_UPDATE_MS = 1000

class DataStore:
    def __init__(self, config):
        self.server = config.get_server()
        self.temp_store_size = config.getint('temperature_store_size', 1200)
        self.gcode_store_size = config.getint('gcode_store_size', 1000)

        # Temperature Store Tracking
        self.last_temps = {}
        self.gcode_queue = deque(maxlen=self.gcode_store_size)
        self.temperature_store = {}
        self.temp_update_cb = PeriodicCallback(
            self._update_temperature_store, TEMPERATURE_UPDATE_MS)

        # Register status update event
        self.server.register_event_handler(
            "server:status_update", self._set_current_temps)
        self.server.register_event_handler(
            "server:gcode_response", self._update_gcode_store)
        self.server.register_event_handler(
            "server:klippy_ready", self._init_sensors)

        # Register endpoints
        self.server.register_endpoint(
            "/server/temperature_store", ['GET'],
            self._handle_temp_store_request)
        self.server.register_endpoint(
            "/server/gcode_store", ['GET'],
            self._handle_gcode_store_request)

    async def _init_sensors(self):
        klippy_apis = self.server.lookup_plugin('klippy_apis')
        # Fetch sensors
        try:
            result = await klippy_apis.query_objects({'heaters': None})
        except self.server.error as e:
            logging.info(f"Error Configuring Sensors: {e}")
            return
        sensors = result.get("heaters", {}).get("available_sensors", [])

        if sensors:
            # Add Subscription
            sub = {s: None for s in sensors}
            try:
                status = await klippy_apis.subscribe_objects(sub)
            except self.server.error as e:
                logging.info(f"Error subscribing to sensors: {e}")
                return
            logging.info(f"Configuring available sensors: {sensors}")
            new_store = {}
            for sensor in sensors:
                fields = list(status.get(sensor, {}).keys())
                if sensor in self.temperature_store:
                    new_store[sensor] = self.temperature_store[sensor]
                else:
                    new_store[sensor] = {
                        'temperatures': deque(maxlen=self.temp_store_size)}
                    for item in ["target", "power", "speed"]:
                        if item in fields:
                            new_store[sensor][f"{item}s"] = deque(
                                maxlen=self.temp_store_size)
                if sensor not in self.last_temps:
                    self.last_temps[sensor] = (0., 0., 0., 0.)
            self.temperature_store = new_store
            # Prune unconfigured sensors in self.last_temps
            for sensor in list(self.last_temps.keys()):
                if sensor not in self.temperature_store:
                    del self.last_temps[sensor]
            # Update initial temperatures
            self._set_current_temps(status)
            self.temp_update_cb.start()
        else:
            logging.info("No sensors found")
            self.last_temps = {}
            self.temperature_store = {}
            self.temp_update_cb.stop()

    def _set_current_temps(self, data):
        for sensor in self.temperature_store:
            if sensor in data:
                last_val = self.last_temps[sensor]
                self.last_temps[sensor] = (
                    round(data[sensor].get('temperature', last_val[0]), 2),
                    data[sensor].get('target', last_val[1]),
                    data[sensor].get('power', last_val[2]),
                    data[sensor].get('speed', last_val[3]))

    def _update_temperature_store(self):
        # XXX - If klippy is not connected, set values to zero
        # as they are unknown?
        for sensor, vals in self.last_temps.items():
            self.temperature_store[sensor]['temperatures'].append(vals[0])
            for val, item in zip(vals[1:], ["targets", "powers", "speeds"]):
                if item in self.temperature_store[sensor]:
                    self.temperature_store[sensor][item].append(val)

    async def _handle_temp_store_request(self, web_request):
        store = {}
        for name, sensor in self.temperature_store.items():
            store[name] = {k: list(v) for k, v in sensor.items()}
        return store

    async def close(self):
        self.temp_update_cb.stop()

    def _update_gcode_store(self, response):
        curtime = time.time()
        self.gcode_queue.append(
            {'message': response, 'time': curtime, 'type': "response"})

    def store_gcode_command(self, script):
        curtime = time.time()
        for cmd in script.split('\n'):
            cmd = cmd.strip()
            if not cmd:
                continue
            self.gcode_queue.append(
                {'message': script, 'time': curtime, 'type': "command"})

    async def _handle_gcode_store_request(self, web_request):
        count = web_request.get_int("count", None)
        if count is not None:
            gc_responses = list(self.gcode_queue)[-count:]
        else:
            gc_responses = list(self.gcode_queue)
        return {'gcode_store': gc_responses}

def load_plugin(config):
    return DataStore(config)
