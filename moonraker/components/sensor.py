# Generic sensor support
#
# Copyright (C) 2022 Morton Jonuschat <mjonuschat+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to read additional generic sensor data and make it
# available to clients
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from collections import deque

# Annotation imports
from typing import Any, Dict, List, Optional, Type, Deque, TYPE_CHECKING

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest

SENSOR_UPDATE_TIME = 1.0


@dataclass(frozen=True)
class SensorConfiguration:
    id: str
    name: str
    source: str = ""
    unit: str = ""
    accuracy_decimals: int = 2


if TYPE_CHECKING:
    from confighelper import ConfigHelper

    from .mqtt import MQTTClient


@dataclass(frozen=True)
class Sensor:
    config: SensorConfiguration
    values: Deque


class BaseSensor:
    def __init__(
        self, name: str, cfg: ConfigHelper, store_size: int = 1200
    ) -> None:
        self.server = cfg.get_server()
        self.error_state: Optional[str] = None

        self.config = SensorConfiguration(
            id=name,
            name=cfg.get("name", name),
            unit=cfg.get("unit", ""),
            accuracy_decimals=cfg.getint("accuracy_decimals", 2),
        )
        self.last_value: float = 0.0
        self.values: Deque[float] = deque(maxlen=store_size)
        self.sensor_update_timer = self.server.get_event_loop().register_timer(
            self._update_sensor_value
        )

    def _update_sensor_value(self, eventtime: float) -> float:
        """
        Append the last updated value to the store.
        """
        self.values.append(self.last_value)

        return eventtime + SENSOR_UPDATE_TIME

    async def initialize(self) -> None:
        """
        Sensor initialization executed on Moonraker startup.
        """
        self.sensor_update_timer.start()
        logging.info("Registered sensor '%s'", self.config.name)

    def close(self) -> None:
        self.sensor_update_timer.stop()


class MQTTSensor(BaseSensor):
    def __init__(self, name: str, cfg: ConfigHelper, store_size: int = 1200):
        super().__init__(name=name, cfg=cfg)
        self.mqtt: MQTTClient = self.server.load_component(cfg, "mqtt")

        self.state_topic: str = cfg.get("state_topic")
        self.state_response = cfg.load_template(
            "state_response_template", "{payload}"
        )
        self.config = replace(self.config, source=self.state_topic)
        self.qos: Optional[int] = cfg.getint("qos", None, minval=0, maxval=2)

        self.server.register_event_handler(
            "mqtt:disconnected", self._on_mqtt_disconnected
        )

    def _on_state_update(self, payload: bytes) -> None:
        err: Optional[Exception] = None
        context = {"payload": payload.decode()}
        logging.debug("Context: %s", context)
        try:
            response = float(self.state_response.render(context))
        except Exception as e:
            self.error_state = str(e)
        else:
            self.error_state = None

            self.last_value = round(response, self.config.accuracy_decimals)
            logging.debug(
                "Received updated sensor value for %s: %s%s",
                self.config.name,
                self.last_value,
                self.config.unit,
            )

    async def _on_mqtt_disconnected(self):
        self.error_state = "MQTT Disconnected"
        self.last_value = 0.0

    async def initialize(self) -> None:
        try:
            self.mqtt.subscribe_topic(
                self.state_topic, self._on_state_update, self.qos
            )
            self.error_state = None
        except Exception as e:
            self.error_state = str(e)

        return await super().initialize()


class Sensors:
    __sensor_types: Dict[str, Type[BaseSensor]] = {"MQTT": MQTTSensor}

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        prefix_sections = config.get_prefix_sections("sensor")

        self.sensors = {}
        # Register endpoints
        self.server.register_endpoint(
            "/server/sensors", ["GET"], self._handle_sensor_data_request
        )

        for section in prefix_sections:
            cfg = config[section]

            try:
                try:
                    _, name = cfg.get_name().split(maxsplit=1)
                except ValueError:
                    raise cfg.error(f"Invalid section name: {cfg.get_name()}")

                logging.info(f"Configuring sensor: {name}")

                sensor_type: str = cfg.get("type", "mqtt")
                sensor_class: Optional[
                    Type[BaseSensor]
                ] = self.__sensor_types.get(sensor_type.upper(), None)
                if sensor_class is None:
                    raise config.error(
                        f"Unsupported sensor type: {sensor_type}"
                    )

                self.sensors[name] = sensor_class(name=name, cfg=cfg)
            except Exception as e:
                # Ensures that configuration errors are shown to the user
                self.server.add_warning(
                    f"Failed to configure sensor [{cfg.get_name()}]\n{e}"
                )
                continue

    async def component_init(self) -> None:
        try:
            logging.debug("Initializing sensor component")
            event_loop = self.server.get_event_loop()
            cur_time = event_loop.get_loop_time()
            endtime = cur_time + 120.0

            uninitialized_sensors = list(self.sensors.values())
            while cur_time < endtime:
                failed_sensors: List[BaseSensor] = []
                for sensor in uninitialized_sensors:
                    ret = sensor.initialize()
                    if ret is not None:
                        await ret
                    if sensor.error_state is not None:
                        failed_sensors.append(sensor)
                if not failed_sensors:
                    logging.debug("All sensors have been initialized")
                    return

                uninitialized_sensors = failed_sensors
                await asyncio.sleep(2.0)
                cur_time = event_loop.get_loop_time()

            for sensor in failed_sensors:
                msg = (
                    f"Sensor {sensor.config.name} is not available: "
                    f"{sensor.error_state}"
                )
                logging.warning(msg)
                self.server.add_warning(msg)

        except Exception as e:
            logging.exception(e)

    async def _handle_sensor_data_request(
        self, web_request: WebRequest
    ) -> Dict[str, Dict[str, Any]]:
        data = {}
        for sensor in self.sensors.values():
            data[sensor.config.id] = {
                "source": sensor.config.source,
                "name": sensor.config.name,
                "unit_of_measurement": sensor.config.unit,
                "accuracy_decimals": sensor.config.accuracy_decimals,
                "measurements": list(sensor.values),
            }
        return data

    def close(self) -> None:
        for sensor in self.sensors.values():
            sensor.close()


def load_component(config: ConfigHelper) -> Sensors:
    return Sensors(config)
