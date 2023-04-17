# Generic sensor support
#
# Copyright (C) 2022 Morton Jonuschat <mjonuschat+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to read additional generic sensor data and make it
# available to clients
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from functools import partial

# Annotation imports
from typing import (
    Any,
    DefaultDict,
    Deque,
    Dict,
    List,
    Optional,
    Type,
    TYPE_CHECKING,
    Union,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .mqtt import MQTTClient

SENSOR_UPDATE_TIME = 1.0
SENSOR_EVENT_NAME = "sensors:sensor_update"


@dataclass(frozen=True)
class SensorConfiguration:
    id: str
    name: str
    type: str
    source: str = ""


def _set_result(
    name: str, value: Union[int, float], store: Dict[str, Union[int, float]]
) -> None:
    if not isinstance(value, (int, float)):
        store[name] = float(value)
    else:
        store[name] = value


@dataclass(frozen=True)
class Sensor:
    config: SensorConfiguration
    values: Dict[str, Deque[Union[int, float]]]


class BaseSensor:
    def __init__(self, name: str, cfg: ConfigHelper, store_size: int = 1200) -> None:
        self.server = cfg.get_server()
        self.error_state: Optional[str] = None

        self.config = SensorConfiguration(
            id=name,
            type=cfg.get("type"),
            name=cfg.get("name", name),
        )
        self.last_measurements: Dict[str, Union[int, float]] = {}
        self.last_value: Dict[str, Union[int, float]] = {}
        self.values: DefaultDict[str, Deque[Union[int, float]]] = defaultdict(
            lambda: deque(maxlen=store_size)
        )

    def _update_sensor_value(self, eventtime: float) -> None:
        """
        Append the last updated value to the store.
        """
        for key, value in self.last_measurements.items():
            self.values[key].append(value)

        # Copy the last measurements data
        self.last_value = {**self.last_measurements}

    async def initialize(self) -> bool:
        """
        Sensor initialization executed on Moonraker startup.
        """
        logging.info("Registered sensor '%s'", self.config.name)
        return True

    def get_sensor_info(self) -> Dict[str, Any]:
        return {
            "id": self.config.id,
            "friendly_name": self.config.name,
            "type": self.config.type,
            "values": self.last_measurements,
        }

    def get_sensor_measurements(self) -> Dict[str, List[Union[int, float]]]:
        return {key: list(values) for key, values in self.values.items()}

    def get_name(self) -> str:
        return self.config.name

    def close(self) -> None:
        pass


class MQTTSensor(BaseSensor):
    def __init__(self, name: str, cfg: ConfigHelper, store_size: int = 1200):
        super().__init__(name=name, cfg=cfg)
        self.mqtt: MQTTClient = self.server.load_component(cfg, "mqtt")

        self.state_topic: str = cfg.get("state_topic")
        self.state_response = cfg.load_template("state_response_template", "{payload}")
        self.config = replace(self.config, source=self.state_topic)
        self.qos: Optional[int] = cfg.getint("qos", None, minval=0, maxval=2)

        self.server.register_event_handler(
            "mqtt:disconnected", self._on_mqtt_disconnected
        )

    def _on_state_update(self, payload: bytes) -> None:
        measurements: Dict[str, Union[int, float]] = {}
        context = {
            "payload": payload.decode(),
            "set_result": partial(_set_result, store=measurements),
        }

        try:
            self.state_response.render(context)
        except Exception as e:
            logging.error("Error updating sensor results: %s", e)
            self.error_state = str(e)
        else:
            self.error_state = None
            self.last_measurements = measurements
            logging.debug(
                "Received updated sensor value for %s: %s",
                self.config.name,
                self.last_measurements,
            )

    async def _on_mqtt_disconnected(self):
        self.error_state = "MQTT Disconnected"
        self.last_measurements = {}

    async def initialize(self) -> bool:
        await super().initialize()
        try:
            self.mqtt.subscribe_topic(
                self.state_topic,
                self._on_state_update,
                self.qos,
            )
            self.error_state = None
            return True
        except Exception as e:
            self.error_state = str(e)
            return False


class Sensors:
    __sensor_types: Dict[str, Type[BaseSensor]] = {"MQTT": MQTTSensor}

    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.store_size = config.getint("sensor_store_size", 1200)
        prefix_sections = config.get_prefix_sections("sensor")
        self.sensors: Dict[str, BaseSensor] = {}

        # Register timer to update sensor values in store
        self.sensors_update_timer = self.server.get_event_loop().register_timer(
            self._update_sensor_values
        )

        # Register endpoints
        self.server.register_endpoint(
            "/server/sensors/list",
            ["GET"],
            self._handle_sensor_list_request,
        )
        self.server.register_endpoint(
            "/server/sensors/info",
            ["GET"],
            self._handle_sensor_info_request,
        )
        self.server.register_endpoint(
            "/server/sensors/measurements",
            ["GET"],
            self._handle_sensor_measurements_request,
        )

        # Register notifications
        self.server.register_notification(SENSOR_EVENT_NAME)

        for section in prefix_sections:
            cfg = config[section]

            try:
                try:
                    _, name = cfg.get_name().split(maxsplit=1)
                except ValueError:
                    raise cfg.error(f"Invalid section name: {cfg.get_name()}")

                logging.info(f"Configuring sensor: {name}")

                sensor_type: str = cfg.get("type")
                sensor_class: Optional[Type[BaseSensor]] = self.__sensor_types.get(
                    sensor_type.upper(), None
                )
                if sensor_class is None:
                    raise config.error(f"Unsupported sensor type: {sensor_type}")

                self.sensors[name] = sensor_class(
                    name=name,
                    cfg=cfg,
                    store_size=self.store_size,
                )
            except Exception as e:
                # Ensures that configuration errors are shown to the user
                self.server.add_warning(
                    f"Failed to configure sensor [{cfg.get_name()}]\n{e}"
                )
                continue

    def _update_sensor_values(self, eventtime: float) -> float:
        """
        Iterate through the sensors and store the last updated value.
        """
        changed_data: Dict[str, Dict[str, Union[int, float]]] = {}
        for sensor_name, sensor in self.sensors.items():
            base_value = sensor.last_value
            sensor._update_sensor_value(eventtime=eventtime)

            # Notify if a change in sensor values was detected
            if base_value != sensor.last_value:
                changed_data[sensor_name] = sensor.last_value
        if changed_data:
            self.server.send_event(SENSOR_EVENT_NAME, changed_data)
        return eventtime + SENSOR_UPDATE_TIME

    async def component_init(self) -> None:
        try:
            logging.debug("Initializing sensor component")
            for sensor in self.sensors.values():
                if not await sensor.initialize():
                    self.server.add_warning(
                        f"Sensor '{sensor.get_name()}' failed to initialize"
                    )

            self.sensors_update_timer.start()
        except Exception as e:
            logging.exception(e)

    async def _handle_sensor_list_request(
        self, web_request: WebRequest
    ) -> Dict[str, Dict[str, Any]]:
        output = {
            "sensors": {
                key: sensor.get_sensor_info() for key, sensor in self.sensors.items()
            }
        }
        return output

    async def _handle_sensor_info_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        sensor_name: str = web_request.get_str("sensor")
        if sensor_name not in self.sensors:
            raise self.server.error(f"No valid sensor named {sensor_name}")
        sensor = self.sensors[sensor_name]
        return sensor.get_sensor_info()

    async def _handle_sensor_measurements_request(
        self, web_request: WebRequest
    ) -> Dict[str, Dict[str, Any]]:
        sensor_name: str = web_request.get_str("sensor", "")
        if sensor_name:
            sensor = self.sensors.get(sensor_name, None)
            if sensor is None:
                raise self.server.error(f"No valid sensor named {sensor_name}")
            return {sensor_name: sensor.get_sensor_measurements()}
        else:
            return {
                key: sensor.get_sensor_measurements()
                for key, sensor in self.sensors.items()
            }

    def close(self) -> None:
        self.sensors_update_timer.stop()
        for sensor in self.sensors.values():
            sensor.close()


def load_component(config: ConfigHelper) -> Sensors:
    return Sensors(config)
