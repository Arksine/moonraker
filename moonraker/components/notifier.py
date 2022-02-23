# Moonraker Notifier
#
# Copyright (C) 2022 Pataar <me@pataar.nl>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import apprise
from apprise import Apprise

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
            self.notifiers[notifier.get_name()] = notifier
            self.apprise = notifier.add_to_notifier(self.apprise)


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
