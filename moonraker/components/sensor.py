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
from functools import partial
from ..common import RequestType, HistoryFieldData

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
    Callable
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .mqtt import MQTTClient
    from .history import History

SENSOR_UPDATE_TIME = 1.0
SENSOR_EVENT_NAME = "sensors:sensor_update"

def _set_result(
    name: str, value: Union[int, float], store: Dict[str, Union[int, float]]
) -> None:
    if not isinstance(value, (int, float)):
        store[name] = float(value)
    else:
        store[name] = value


class BaseSensor:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.error_state: Optional[str] = None
        self.id = config.get_name().split(maxsplit=1)[-1]
        self.type = config.get("type")
        self.name = config.get("name", self.id)
        self.last_measurements: Dict[str, Union[int, float]] = {}
        self.last_value: Dict[str, Union[int, float]] = {}
        store_size = config.getint("sensor_store_size", 1200)
        self.values: DefaultDict[str, Deque[Union[int, float]]] = defaultdict(
            lambda: deque(maxlen=store_size)
        )
        self.param_info: List[Dict[str, str]] = []
        history: History = self.server.lookup_component("history")
        self.field_info: Dict[str, List[HistoryFieldData]] = {}
        all_opts = list(config.get_options().keys())
        cfg_name = config.get_name()
        param_prefix = "parameter_"
        hist_field_prefix = "history_field_"
        for opt in all_opts:
            if opt.startswith(param_prefix):
                name = opt[len(param_prefix):]
                data = config.getdict(opt)
                data["name"] = opt[len(param_prefix):]
                self.param_info.append(data)
                continue
            if not opt.startswith(hist_field_prefix):
                continue
            name = opt[len(hist_field_prefix):]
            field_cfg: Dict[str, str] = config.getdict(opt)
            ident: Optional[str] = field_cfg.pop("parameter", None)
            if ident is None:
                raise config.error(
                    f"[{cfg_name}]: option '{opt}', key 'parameter' must be"
                    f"specified"
                )
            do_init: str = field_cfg.pop("init_tracker", "false").lower()
            reset_cb = self._gen_reset_callback(ident) if do_init == "true" else None
            excl_paused: str = field_cfg.pop("exclude_paused", "false").lower()
            report_total: str = field_cfg.pop("report_total", "false").lower()
            report_max: str = field_cfg.pop("report_maximum", "false").lower()
            precision: Optional[str] = field_cfg.pop("precision", None)
            try:
                fdata = HistoryFieldData(
                    name,
                    cfg_name,
                    field_cfg.pop("desc", f"{ident} tracker"),
                    field_cfg.pop("strategy", "basic"),
                    units=field_cfg.pop("units", None),
                    reset_callback=reset_cb,
                    exclude_paused=excl_paused == "true",
                    report_total=report_total == "true",
                    report_maximum=report_max == "true",
                    precision=int(precision) if precision is not None else None,
                )
            except Exception as e:
                raise config.error(
                    f"[{cfg_name}]: option '{opt}', error encountered during "
                    f"sensor field configuration: {e}"
                ) from e
            for key in field_cfg.keys():
                self.server.add_warning(
                    f"[{cfg_name}]: Option '{opt}' contains invalid key '{key}'"
                )
            self.field_info.setdefault(ident, []).append(fdata)
            history.register_auxiliary_field(fdata)

    def _gen_reset_callback(self, param_name: str) -> Callable[[], float]:
        def on_reset() -> float:
            return self.last_measurements.get(param_name, 0)
        return on_reset

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
        logging.info("Registered sensor '%s'", self.name)
        return True

    def get_sensor_info(self, extended: bool = False) -> Dict[str, Any]:
        ret: Dict[str, Any] = {
            "id": self.id,
            "friendly_name": self.name,
            "type": self.type,
            "values": self.last_measurements,
        }
        if extended:
            ret["parameter_info"] = self.param_info
            history_fields: List[Dict[str, Any]] = []
            for parameter, field_list in self.field_info.items():
                for field_data in field_list:
                    field_config = field_data.get_configuration()
                    field_config["parameter"] = parameter
                    history_fields.append(field_config)
            ret["history_fields"] = history_fields
        return ret

    def get_sensor_measurements(self) -> Dict[str, List[Union[int, float]]]:
        return {key: list(values) for key, values in self.values.items()}

    def get_name(self) -> str:
        return self.name

    def close(self) -> None:
        pass


class MQTTSensor(BaseSensor):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config=config)
        self.mqtt: MQTTClient = self.server.load_component(config, "mqtt")
        self.state_topic: str = config.get("state_topic")
        self.state_response = config.gettemplate("state_response_template")
        self.qos: Optional[int] = config.getint("qos", None, minval=0, maxval=2)
        self.server.register_event_handler(
            "mqtt:disconnected", self._on_mqtt_disconnected
        )

    def _on_state_update(self, payload: bytes) -> None:
        measurements: Dict[str, Union[int, float]] = {}
        context = {
            "payload": payload.decode(),
            "set_result": partial(_set_result, store=measurements),
            "log_debug": logging.debug
        }

        try:
            self.state_response.render(context)
        except Exception as e:
            logging.error("Error updating sensor results: %s", e)
            self.error_state = str(e)
        else:
            self.error_state = None
            self.last_measurements = measurements
            for name, value in measurements.items():
                fdata_list = self.field_info.get(name)
                if fdata_list is None:
                    continue
                for fdata in fdata_list:
                    fdata.tracker.update(value)

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
        self.sensors: Dict[str, BaseSensor] = {}

        # Register timer to update sensor values in store
        self.sensors_update_timer = self.server.get_event_loop().register_timer(
            self._update_sensor_values
        )

        # Register endpoints
        self.server.register_endpoint(
            "/server/sensors/list",
            RequestType.GET,
            self._handle_sensor_list_request,
        )
        self.server.register_endpoint(
            "/server/sensors/info",
            RequestType.GET,
            self._handle_sensor_info_request,
        )
        self.server.register_endpoint(
            "/server/sensors/measurements",
            RequestType.GET,
            self._handle_sensor_measurements_request,
        )

        # Register notifications
        self.server.register_notification(SENSOR_EVENT_NAME)
        prefix_sections = config.get_prefix_sections("sensor ")
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

                self.sensors[name] = sensor_class(cfg)
            except Exception as e:
                # Ensures that configuration errors are shown to the user
                self.server.add_warning(
                    f"Failed to configure sensor [{cfg.get_name()}]\n{e}", exc_info=e
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
        extended = web_request.get_boolean("extended", False)
        return {
            "sensors": {
                key: sensor.get_sensor_info(extended)
                for key, sensor in self.sensors.items()
            }
        }

    async def _handle_sensor_info_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        sensor_name: str = web_request.get_str("sensor")
        extended = web_request.get_boolean("extended", False)
        if sensor_name not in self.sensors:
            raise self.server.error(f"No valid sensor named {sensor_name}")
        sensor = self.sensors[sensor_name]
        return sensor.get_sensor_info(extended)

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
