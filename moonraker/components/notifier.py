# Moonraker Notifier
#
# Copyright (C) 2022 Pataar <me@pataar.nl>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import apprise
from apprise import Apprise
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    Optional,
    Dict,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from . import klippy_apis

    APIComp = klippy_apis.KlippyAPI


class Notifier:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.apprise = apprise.Apprise()
        self.notifiers: Dict[str, NotifierInstance] = {}
        prefix_sections = config.get_prefix_sections("notifier")
        for section in prefix_sections:
            cfg = config[section]
            notifier_class: Optional[Type[NotifierInstance]]
            try:
                notifier = NotifierInstance(cfg)
            except Exception as e:
                msg = f"Failed to load notifier[{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg)
                continue
            logging.info(f"Loaded notifier: '{notifier.get_name()}'")
            self.notifiers[notifier.get_name()] = notifier
            self.apprise = notifier.add_to_notifier(self.apprise)

        self.server.register_event_handler(
            "job_state:started", self._on_job_started)
        self.server.register_event_handler(
            "job_state:complete", self._on_job_complete)

    def notify(self, body="test"):
        logging.info(f"Sending notification to thing")
        self.apprise.async_notify(body)

    async def _on_job_started(self, state: str) -> None:
        try:
            logging.info(f"Job started event triggered'")
            self.notify("Started")
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")

    async def _on_job_complete(self, state: str) -> None:
        try:
            logging.info(f"Job completed event triggered'")
            self.notify("Completed")
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")

class NotifierInstance:
    def __init__(self, config: ConfigHelper) -> None:
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.url: str = config.get('url')
        self.events: str = "init"

    def get_name(self) -> str:
        return self.name

    def add_to_notifier(self, appr: Apprise) -> Apprise:
        appr.add(self.url)

        return appr


def load_component(config: ConfigHelper) -> Notifier:
    return Notifier(config)
