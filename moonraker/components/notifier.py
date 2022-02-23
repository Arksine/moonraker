# Notifier
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

        self.events["started"] = NotifierEvent(
            "started",
            "job_state:started",
            config)

        self.events["completed"] = NotifierEvent(
            "completed",
            "job_state:completed",
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
        self.notifiers: Dict[str, NotifierInstance] = {}
        self.config = config

        self.server.register_event_handler(self.event_name, self._handle)

    def register_notifier(self, notifier: NotifierInstance):
        self.notifiers[notifier.get_name()] = notifier

    async def _handle(self, *args) -> None:
        logging.info(f"'{self.identifier}' notifier event triggered'")
        await self.invoke_notifiers(args)

    async def invoke_notifiers(self, args):
        for notifier_name in self.notifiers:
            try:
                notifier = self.notifiers[notifier_name]
                await notifier.notify(self.identifier, args)
            except Exception as e:
                logging.info(f"Failed to notify [{notifier_name}]\n{e}")
                continue


class NotifierInstance:
    def __init__(self, config: ConfigHelper) -> None:

        self.config = config
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.apprise = apprise.Apprise()

        url_template = config.gettemplate('url')
        self.url = url_template.render()

        if len(self.url) < 2:
            raise config.error(f"Invalid url for: {config.get_name()}")

        self.title = config.gettemplate('title', None)
        self.body = config.gettemplate("body", None)

        self.events: List[str] = config.getlist("events", separator=",")

        self.apprise.add(self.url)

    async def notify(self, event_name: str, event_args: List) -> None:
        context = {
            "event_name": event_name,
            "event_args": event_args
        }

        rendered_title = (
            '' if self.title is None else self.title.render(context)
        )
        rendered_body = (
            event_name if self.body is None else self.body.render(context)
        )

        await self.apprise.async_notify(
            rendered_body.strip(),
            rendered_title.strip()
        )

    def get_name(self) -> str:
        return self.name


def load_component(config: ConfigHelper) -> Notifier:
    return Notifier(config)
