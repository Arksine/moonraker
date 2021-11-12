# Klippy job state event handlers
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .klippy_apis import KlippyAPI

class JobState:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.last_print_stats: Dict[str, Any] = {}
        self.server.register_event_handler(
            "server:klippy_ready", self._handle_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)

    async def _handle_ready(self) -> None:
        kapis: KlippyAPI = self.server.lookup_component('klippy_apis')
        sub: Dict[str, Optional[List[str]]] = {"print_stats": None}
        try:
            result = await kapis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
        self.last_print_stats = result.get("print_stats", {})

    async def _status_update(self, data: Dict[str, Any]) -> None:
        if 'print_stats' not in data:
            return
        ps = data['print_stats']
        if "state" in ps:
            prev_ps = dict(self.last_print_stats)
            old_state: str = prev_ps['state']
            new_state: str = ps['state']
            new_ps = dict(self.last_print_stats)
            new_ps.update(ps)
            if new_state is not old_state:
                if new_state == "printing":
                    # The "printing" state needs some special handling
                    # to detect "resets" and a transition from pause to resume
                    if self._check_resumed(prev_ps, new_ps):
                        new_state = "resumed"
                    else:
                        new_state = "started"
                self.server.send_event(
                    f"job_state:{new_state}", prev_ps, new_ps)
        self.last_print_stats.update(ps)

    def _check_resumed(self,
                       prev_ps: Dict[str, Any],
                       new_ps: Dict[str, Any]
                       ) -> bool:
        return (
            prev_ps['state'] == "paused" and
            prev_ps['filename'] == new_ps['filename'] and
            prev_ps['total_duration'] < new_ps['total_duration']
        )

    def get_last_stats(self) -> Dict[str, Any]:
        return dict(self.last_print_stats)

def load_component(config: ConfigHelper) -> JobState:
    return JobState(config)
