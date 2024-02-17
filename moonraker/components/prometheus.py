# Prometheus client implementation for Moonraker
#
# Copyright (C) 2024 Kamil Domański <kamil@domanski.co>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
from prometheus_client import (
    exposition, registry,
    Info
)

from ..common import (
    RequestType,
    TransportType,
    WebRequest
)

from typing import (
    TYPE_CHECKING
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class PrometheusClient:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        app_args = self.server.get_app_args()

        i = Info('moonraker_instance', '')
        i.info({
            'version': app_args['software_version'],
            'instance_uuid': app_args['instance_uuid'],
            'python_version': app_args['python_version'],
        })

        self.server.register_endpoint(
            "/server/prometheus/metrics", RequestType.GET,
            self._handle_metrics_endpoint, transports=TransportType.HTTP,
            wrap_result=False, content_type=exposition.CONTENT_TYPE_LATEST
        )

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

def load_component(config: ConfigHelper) -> PrometheusClient:
    return PrometheusClient(config)
