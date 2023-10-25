# Energy Meter - process energy data from sensors and provide delta measurements
#
# Copyright (C) 2023 Sandro Pischinger <mail@sandropischinger.de>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
from dataclasses import dataclass

# Annotation imports
from typing import (
    Dict,
    Optional,
    TYPE_CHECKING,
    Union,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .sensor import BaseSensor, Sensors


@dataclass(frozen=True)
class EnergyMeterConfiguration:
    sensor: str
    field: str


class DeltaMeasurement:
    start_value: Optional[Union[int, float]] = None
    last_value: Optional[Union[int, float]] = None

    def delta(self) -> Optional[Union[int, float]]:
        if self.start_value is None or self.last_value is None:
            return None
        return self.last_value - self.start_value


class EnergyMeter:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        sensors: Sensors = self.server.load_component(config, "sensor")
        self.sensors = sensors.sensors
        self.config = EnergyMeterConfiguration(
            sensor=config.get("sensor"),
            field=config.get("field", "energy"),
        )
        self.active_msmnt: Optional[DeltaMeasurement] = None

        self.server.register_event_handler(
            "sensors:sensor_update", self._on_sensor_update
        )

    def _update_values(self, sensor_values: Dict[str, Union[int, float]]) -> None:
        """
        Update local values from sensor_values.
        """
        if self.active_msmnt is None:
            return
        if self.config.field not in sensor_values:
            logging.error(
                f"Energy value field '{self.config.field}'"
                "not in data of sensor: '{self.config.sensor}'"
            )
            return None
        value = sensor_values[self.config.field]
        if self.active_msmnt.start_value is None:
            logging.debug("EnergyMeter start value: %d", value)
            self.active_msmnt.start_value = value

        self.active_msmnt.last_value = value

    def _on_sensor_update(
        self,
        changed_data: Dict[str, Dict[str, Union[int, float]]]
    ) -> None:
        """
        Listen to sensor update and find the energy sensor.
        """
        if self.active_msmnt is None:
            return
        sensor_values = changed_data.get(self.config.sensor)
        if sensor_values is None:
            return
        self._update_values(sensor_values)

    def start_measurement(self) -> Optional[DeltaMeasurement]:
        """
        Start a measurement and fill with initial values.
        """
        if self.active_msmnt is not None:
            logging.warning(
                "Can not start another measurement when currently active."
            )
            return None
        sensor: Optional[BaseSensor] = self.sensors.get(self.config.sensor)
        if sensor is None:
            logging.warning(
                "Could not start measurement: sensor '%s' is missing",
                self.config.sensor
            )
            return None
        self.active_msmnt = DeltaMeasurement()
        msmnts: Dict[str, Union[int, float]] = {key: values[0] for key, values
                                                in sensor.values.items()}
        self._update_values(msmnts)

        return self.active_msmnt

    def stop_measurement(self) -> Optional[DeltaMeasurement]:
        """
        Stop the current measurement and return results.
        """
        am = self.active_msmnt
        self.active_msmnt = None
        return am

    async def initialize(self) -> bool:
        """
        EnergyMeter initialization on Moonraker startup.
        """
        logging.info(
            "Registered EnergyMeter for sensor '%s'",
            self.config.sensor
        )
        return True


def load_component(config: ConfigHelper) -> EnergyMeter:
    return EnergyMeter(config)
