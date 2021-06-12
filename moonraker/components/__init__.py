
# Package definition for the components directory
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Optional,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from components.klippy_apis import KlippyAPI

PRINTING_STATE = ["printing", "paused"]
FINISHED_STATE = ["standby", "complete", "error", "cancelled"]

class Component:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()

class StateComponent(Component):
    def __init__(self, config: ConfigHelper) -> None:
        super(StateComponent, self).__init__(config)

        self.print_stats: Dict[str, Any] = {}

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)

    async def _init_ready(self) -> None:
        klippy_apis: KlippyAPI = self.server.lookup_component('klippy_apis')

        try:
            result = await klippy_apis.subscribe_objects({"print_stats": None})
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
            return

        try:
            result = await klippy_apis.query_objects({"print_stats": None})
            await self._status_update(result)
        except self.server.error as e:
            logging.info(f"Error querying print_stats")
            return

    async def _status_update(self, data: Dict[str, Any]) -> None:
        # merge old print stats with current new_print_stats
        print_stats = data.get("print_stats", {})
        if "state" not in print_stats:
            return

        new_print_stats = dict(self.print_stats)
        new_print_stats.update(print_stats)

        old_state: str = self.print_stats.get('state', '')
        new_state: str = new_print_stats['state']

        if new_state == "printing" and self._check_need_cancel(new_print_stats):
            if old_state in PRINTING_STATE:
                self.on_print_finish("cancelled", self.print_stats)
            self.on_print_start(new_print_stats)
        elif new_state == old_state:
            # no state change
            return
        elif new_state == "printing" and old_state in FINISHED_STATE:
            self.on_print_start(new_print_stats)
        elif new_state == "paused" and old_state == "printing":
            self.on_print_pause(new_print_stats)
        elif new_state == "printing" and old_state == "paused":
            self.on_print_resume(new_print_stats)
        elif new_state == "standby" and old_state in PRINTING_STATE:
            # Backward compatibility with
            # `CLEAR_PAUSE/SDCARD_RESET_FILE` workflow
            self.on_print_finish("cancelled", self.print_stats)
        elif new_state in FINISHED_STATE and old_state in PRINTING_STATE:
            self.on_print_finish(new_state, new_print_stats)

        self.print_stats.update(new_print_stats)

    def _check_need_cancel(self, new_stats: Dict[str, Any]) -> bool:
        # Cancel if the file name has changed, total duration has
        # decreased, or if job is not resuming from a pause
        ps = self.print_stats
        return ps.get('filename', '') != new_stats['filename'] or \
            ps.get('total_duration', 0) > new_stats['total_duration'] or \
            ps.get('state', '') != "paused"

    def _handle_shutdown(self) -> None:
        if self.print_stats["state"] in PRINTING_STATE:
            self.on_print_finish("klippy_shutdown", self.print_stats)
        self.print_stats.clear()

    def _handle_disconnect(self) -> None:
        if self.print_stats["state"] in PRINTING_STATE:
            self.on_print_finish("klippy_disconnect", self.print_stats)
        self.print_stats.clear()

    def on_exit(self) -> None:
        if self.print_stats["state"] in PRINTING_STATE:
            self.on_print_finish("server_exit", self.print_stats)
        self.print_stats.clear()

    # Define callbacks
    def on_print_start(self, print_stats):
        pass

    def on_print_pause(self, print_stats):
        pass

    def on_print_resume(self, print_stats):
        pass

    def on_print_finish(self, status, print_stats):
        pass
