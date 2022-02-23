# Moonraker Notifier
#
# Copyright (C) 2022 Pataar <me@pataar.nl>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import apprise
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    Optional,
    Dict,
    Any,
    List,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from . import klippy_apis

    APIComp = klippy_apis.KlippyAPI


class Notifier:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.notifiers: Dict[str, NotifierInstance] = {}
        self.events: Dict[str, NotifierEvent] = {}
        prefix_sections = config.get_prefix_sections("notifier")

        self.register_events(config)

        for section in prefix_sections:
            cfg = config[section]
            notifier_class: Optional[Type[NotifierInstance]]
            try:
                notifier = NotifierInstance(cfg)

                for event in self.events:
                    if event in notifier.events or "*" in notifier.events:
                        self.events[event].register_notifier(notifier)

                logging.info(f"Registered notifier: '{notifier.get_name()}'")

            except Exception as e:
                msg = f"Failed to load notifier[{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg)
                continue
            self.notifiers[notifier.get_name()] = notifier

    def register_events(self, config: ConfigHelper):

        self.events["completed"] = NotifierEvent(
            "completed",
            "job_state:completed",
            config)

        self.events["started"] = NotifierEvent(
            "started",
            "job_state:started",
            config)

        self.events["error"] = NotifierEvent(
            "error",
            "job_state:error",
            config)

        self.events["cancelled"] = NotifierEvent(
            "cancelled",
            "job_state:cancelled",
            config)


class NotifierEvent:
    def __init__(self, identifier: str, event_name: str, config: ConfigHelper):
        self.identifier = identifier
        self.event_name = event_name
        self.server = config.get_server()
        self.apprise = apprise.Apprise()
        self.notifiers: Dict[str, NotifierInstance] = {}

        self.server.register_event_handler(self.event_name, self._handle)

    def register_notifier(self, notifier: NotifierInstance):
        self.notifiers[notifier.get_name()] = notifier
        self.apprise.add(notifier.url)

    async def _handle(self,
                      prev_stats: Dict[str, Any],
                      new_stats: Dict[str, Any]
                      ) -> None:
        try:
            logging.info(f"'{self.event_name}' event triggered'")
            await self.notify(self.event_name)
        except self.server.error as e:
            logging.info(f"Error while notifiying '{self.event_name}'")

    async def notify(self, body="test"):
        logging.info(
            f"Notifying '{self.event_name}' to {len(self.notifiers.keys())} n"
        )
        await self.apprise.async_notify(
            title='Moonraker',
            body=body
        )


class NotifierInstance:
    def __init__(self, config: ConfigHelper) -> None:
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]

        if len(config.get('url', '')) < 2:
            raise config.error(f"Invalid url for: {config.get_name()}")

        self.url: str = config.get('url')
        self.events: List[str] = config.get('events', '').split(",")

    def get_name(self) -> str:
        return self.name


def load_component(config: ConfigHelper) -> Notifier:
    return Notifier(config)
