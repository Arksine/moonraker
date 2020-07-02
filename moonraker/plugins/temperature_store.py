# Heater sensor temperature storage
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from collections import deque
from tornado.ioloop import IOLoop, PeriodicCallback

TEMPERATURE_UPDATE_MS = 1000
TEMPERATURE_STORE_SIZE = 20 * 60

class TemperatureStore:
    def __init__(self, server):
        self.server = server

        # Temperature Store Tracking
        self.last_temps = {}
        self.temperature_store = {}
        self.temp_update_cb = PeriodicCallback(
            self._update_temperature_store, TEMPERATURE_UPDATE_MS)

        # Register status update event
        self.server.register_event_handler(
            "server:status_update", self._set_current_temps)
        self.server.register_event_handler(
            "server:refresh_temp_sensors", self._init_sensors)

        # Register endpoint
        self.server.register_endpoint(
            "/server/temperature_store", "server_temperature_store", ['GET'],
            self._handle_temp_store_request)

    def _init_sensors(self, sensors):
        logging.info("Configuring available sensors: %s" % (str(sensors)))
        new_store = {}
        for sensor in sensors:
            if sensor in self.temperature_store:
                new_store[sensor] = self.temperature_store[sensor]
            else:
                new_store[sensor] = {
                    'temperatures': deque(maxlen=TEMPERATURE_STORE_SIZE),
                    'targets': deque(maxlen=TEMPERATURE_STORE_SIZE)}
        self.temperature_store = new_store
        self.temp_update_cb.start()
        # XXX - spawn a callback that requests temperature updates?

    def _set_current_temps(self, data):
        for sensor in self.temperature_store:
            if sensor in data:
                self.last_temps[sensor] = (
                    round(data[sensor].get('temperature', 0.), 2),
                    data[sensor].get('target', 0.))


    def _update_temperature_store(self):
        # XXX - If klippy is not connected, set values to zero
        # as they are unknown?
        for sensor, (temp, target) in self.last_temps.items():
            self.temperature_store[sensor]['temperatures'].append(temp)
            self.temperature_store[sensor]['targets'].append(target)

    async def _handle_temp_store_request(self, path, method, args):
        store = {}
        for name, sensor in self.temperature_store.items():
            store[name] = {k: list(v) for k, v in sensor.items()}
        return store

    async def close(self):
        self.temp_update_cb.stop()

def load_plugin(server):
    return TemperatureStore(server)
