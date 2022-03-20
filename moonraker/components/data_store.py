# Klipper data logging and storage storage
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import time
from collections import deque

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
    Tuple,
    Deque,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import klippy_apis
    APIComp = klippy_apis.KlippyAPI
    GCQueue = Deque[Dict[str, Any]]
    TempStore = Dict[str, Dict[str, Deque[float]]]

TEMP_UPDATE_TIME = 1.

class DataStore:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.temp_store_size = config.getint('temperature_store_size', 1200)
        self.gcode_store_size = config.getint('gcode_store_size', 1000)

        # Temperature Store Tracking
        self.last_temps: Dict[str, Tuple[float, ...]] = {}
        self.gcode_queue: GCQueue = deque(maxlen=self.gcode_store_size)
        self.temperature_store: TempStore = {}
        eventloop = self.server.get_event_loop()
        self.temp_update_timer = eventloop.register_timer(
            self._update_temperature_store)

        # Register status update event
        self.server.register_event_handler(
            "server:status_update", self._set_current_temps)
        self.server.register_event_handler(
            "server:gcode_response", self._update_gcode_store)
        self.server.register_event_handler(
            "server:klippy_ready", self._init_sensors)
        self.server.register_event_handler(
            "klippy_connection:gcode_received", self._store_gcode_command
        )

        # Register endpoints
        self.server.register_endpoint(
            "/server/temperature_store", ['GET'],
            self._handle_temp_store_request)
        self.server.register_endpoint(
            "/server/gcode_store", ['GET'],
            self._handle_gcode_store_request)

    async def _init_sensors(self) -> None:
        klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        # Fetch sensors
        try:
            result: Dict[str, Any]
            result = await klippy_apis.query_objects({'heaters': None})
        except self.server.error as e:
            logging.info(f"Error Configuring Sensors: {e}")
            return
        sensors: List[str]
        sensors = result.get("heaters", {}).get("available_sensors", [])

        if sensors:
            # Add Subscription
            sub: Dict[str, Optional[List[str]]] = {s: None for s in sensors}
            try:
                status: Dict[str, Any]
                status = await klippy_apis.subscribe_objects(sub)
            except self.server.error as e:
                logging.info(f"Error subscribing to sensors: {e}")
                return
            logging.info(f"Configuring available sensors: {sensors}")
            new_store: TempStore = {}
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
            self.temp_update_timer.start()
        else:
            logging.info("No sensors found")
            self.last_temps = {}
            self.temperature_store = {}
            self.temp_update_timer.stop()

    def _set_current_temps(self, data: Dict[str, Any]) -> None:
        for sensor in self.temperature_store:
            if sensor in data:
                last_val = self.last_temps[sensor]
                self.last_temps[sensor] = (
                    round(data[sensor].get('temperature', last_val[0]), 2),
                    data[sensor].get('target', last_val[1]),
                    data[sensor].get('power', last_val[2]),
                    data[sensor].get('speed', last_val[3]))

    def _update_temperature_store(self, eventtime: float) -> float:
        # XXX - If klippy is not connected, set values to zero
        # as they are unknown?
        for sensor, vals in self.last_temps.items():
            self.temperature_store[sensor]['temperatures'].append(vals[0])
            for val, item in zip(vals[1:], ["targets", "powers", "speeds"]):
                if item in self.temperature_store[sensor]:
                    self.temperature_store[sensor][item].append(val)
        return eventtime + TEMP_UPDATE_TIME

    async def _handle_temp_store_request(self,
                                         web_request: WebRequest
                                         ) -> Dict[str, Dict[str, List[float]]]:
        store = {}
        for name, sensor in self.temperature_store.items():
            store[name] = {k: list(v) for k, v in sensor.items()}
        return store

    async def close(self) -> None:
        self.temp_update_timer.stop()

    def _update_gcode_store(self, response: str) -> None:
        curtime = time.time()
        self.gcode_queue.append(
            {'message': response, 'time': curtime, 'type': "response"})

    def _store_gcode_command(self, script: str) -> None:
        curtime = time.time()
        for cmd in script.split('\n'):
            cmd = cmd.strip()
            if not cmd:
                continue
            self.gcode_queue.append(
                {'message': script, 'time': curtime, 'type': "command"})

    async def _handle_gcode_store_request(self,
                                          web_request: WebRequest
                                          ) -> Dict[str, List[Dict[str, Any]]]:
        count = web_request.get_int("count", None)
        if count is not None:
            gc_responses = list(self.gcode_queue)[-count:]
        else:
            gc_responses = list(self.gcode_queue)
        return {'gcode_store': gc_responses}

def load_component(config: ConfigHelper) -> DataStore:
    return DataStore(config)
