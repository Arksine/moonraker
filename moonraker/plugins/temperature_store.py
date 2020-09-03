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
    def __init__(self, config):
        self.server = config.get_server()

        # Temperature Store Tracking
        self.last_temps = {}
        self.temperature_store = {}
        self.temp_update_cb = PeriodicCallback(
            self._update_temperature_store, TEMPERATURE_UPDATE_MS)

        # Register status update event
        self.server.register_event_handler(
            "server:status_update", self._set_current_temps)
        self.server.register_event_handler(
            "server:klippy_ready", self._init_sensors)

        # Register endpoint
        self.server.register_endpoint(
            "/server/temperature_store", ['GET'],
            self._handle_temp_store_request)

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
                if sensor in self.temperature_store:
                    new_store[sensor] = self.temperature_store[sensor]
                else:
                    new_store[sensor] = {
                        'temperatures': deque(maxlen=TEMPERATURE_STORE_SIZE),
                        'targets': deque(maxlen=TEMPERATURE_STORE_SIZE)}
                if sensor not in self.last_temps:
                    self.last_temps[sensor] = (0., 0.)
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
                last_temp, last_target = self.last_temps[sensor]
                self.last_temps[sensor] = (
                    round(data[sensor].get('temperature', last_temp), 2),
                    data[sensor].get('target', last_target))

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

def load_plugin(config):
    return TemperatureStore(config)
