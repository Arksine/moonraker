# Prometheus client implementation for Moonraker
#
# Copyright (C) 2024 Kamil Domański <kamil@domanski.co>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
from asyncio import gather
import logging
from prometheus_client import (
    exposition, registry,
    Info, Gauge
)
from prometheus_client.metrics import MetricWrapperBase

from ..common import (
    KlippyState,
    RequestType,
    TransportType,
    WebRequest
)

from .klippy_apis import KlippyAPI

from typing import (
    TYPE_CHECKING,
    Dict,
    Any
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class PrometheusExporter:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        app_args = self.server.get_app_args()

        # Not saved in the object, because it should not be changed or cleared.
        i = Info('moonraker_instance', '')
        i.info({
            'version': app_args['software_version'],
            'instance_uuid': app_args['instance_uuid'],
            'python_version': app_args['python_version'],
        })

        # metrics
        self.m_temp = Gauge(
            'temp', 'Current temperature of a heater or sensor', ['sensor'])
        self.m_target_temp = Gauge(
            'target_temp', 'Target temperature of a heater or fan', ['sensor'])
        self.m_heater_power = Gauge(
            'heater_power', 'Current power setting of a heater', ['heater'])

        self.server.register_endpoint(
            "/server/prometheus/metrics", RequestType.GET,
            self._handle_metrics_endpoint, transports=TransportType.HTTP,
            wrap_result=False, content_type=exposition.CONTENT_TYPE_LATEST
        )
        self.server.register_event_handler(
            "server:klippy_started", self._handle_klippy_started
        )

        # clear metrics to stop providing metrics when their value is simply unknown
        self.server.register_event_handler(
            "server:klippy_shutdown", self._clear_metrics
        )
        self.server.register_event_handler(
            "server:klippy_disconnect", self._clear_metrics
        )
        self.server.register_event_handler(
            "server:klippy_disconnected", self._clear_metrics
        )

    async def _get_objects_to_subscribe(self) -> Dict[str, Any]:
        kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
        result = await kapi.query_objects({'heaters': None})
        heaters_dict = result.get("heaters", {})

        heaters = set(heaters_dict.get("available_heaters", []))
        sensors = set(heaters_dict.get("available_sensors", []))

        return {s: None for s in heaters.union(sensors)}

    async def _init_metrics(self, objs: Dict[str, Any]) -> None:
        """Gets the current status of all the objects. Without it, we'd only export
        the metrics which have changed since Moonraker statup."""
        kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
        result = await kapi.query_objects(objs)
        await self._handle_status_update(result, None)

    async def _handle_klippy_started(self, state: KlippyState) -> None:
        """Upon klippy startup, it queries current statuses
        and subscribes for updates."""
        self._clear_metrics()

        kapi: KlippyAPI = self.server.lookup_component("klippy_apis")
        subs = await self._get_objects_to_subscribe()

        await gather(
            self._init_metrics(subs),
            kapi.subscribe_objects(subs, self._handle_status_update)
        )

        logging.info("Prometheus handler registered and subscribed to status updates")

    async def _handle_status_update(self, status: Dict[str, Dict[str, Any]],
                                    eventtime: float | None) -> None:
        for key, value in status.items():
            module = key.split()[0]
            if module in ['heater_bed', 'extruder', 'heater_generic']:
                self._status_update_heater(key, value)
            elif module in ['temperature_combined', 'temperature_sensor', 'tmc2240',
                            'temperature_fan']:
                self._status_update_temp_sensor(key, value)
            else:
                logging.debug("[prometheus]: unhandled status for object %s" % key)

    def _status_update_temp_sensor(self, sensor_name: str,
                                   status: Dict[str, Any]) -> None:
        temp = status.get('temperature', None)
        if temp is not None:
            self.m_temp.labels(sensor_name).set(temp)

    def _status_update_heater(self, heater_name: str,
                              status: Dict[str, Any]) -> None:
        temp = status.get('temperature', None)
        if temp is not None:
            self.m_temp.labels(heater_name).set(temp)

        target = status.get('target', None)
        if target is not None:
            self.m_target_temp.labels(heater_name).set(target)

        power = status.get('power', None)
        if power is not None:
            self.m_heater_power.labels(heater_name).set(power)

    def _clear_metrics(self) -> None:
        for attr_name, attr_value in self.__dict__.items():
            if isinstance(attr_value, MetricWrapperBase):
                attr_value.clear()

    async def _handle_metrics_endpoint(self, web_request: WebRequest) -> bytes:
        """Writes metrics in response to the scrape.

        Usually some properties of the response depend on request headers.
        To make request headers available here, a serious refactoring would be needed.
        Instead, we make some assumptions:
        - Response will be in the "normal" format and not openmetrics
        - No filtering by name will be done
        - gzip won't be used (also sparing the CPU cycles in return for bandwidth)
        """
        return exposition.generate_latest(registry.REGISTRY)

def load_component(config: ConfigHelper) -> PrometheusExporter:
    return PrometheusExporter(config)
