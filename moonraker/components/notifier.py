# Notifier
#
# Copyright (C) 2022 Pataar <me@pataar.nl>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import apprise
import logging
import pathlib
import re
from ..common import JobEvent, RequestType

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Dict,
    Any,
    List,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .file_manager.file_manager import FileManager
    from .klippy_apis import KlippyAPI as APIComp

class Notifier:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.notifiers: Dict[str, NotifierInstance] = {}
        self.events: Dict[str, List[NotifierInstance]] = {}
        prefix_sections = config.get_prefix_sections("notifier")
        self.register_remote_actions()
        for section in prefix_sections:
            cfg = config[section]
            try:
                notifier = NotifierInstance(cfg)
                for job_event in list(JobEvent):
                    if job_event == JobEvent.STANDBY:
                        continue
                    evt_name = str(job_event)
                    if "*" in notifier.events or evt_name in notifier.events:
                        self.events.setdefault(evt_name, []).append(notifier)
                logging.info(f"Registered notifier: '{notifier.get_name()}'")
            except Exception as e:
                msg = f"Failed to load notifier[{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg, exc_info=e)
                continue
            self.notifiers[notifier.get_name()] = notifier

        self.register_endpoints(config)
        self.server.register_event_handler(
            "job_state:state_changed", self._on_job_state_changed
        )

    def register_remote_actions(self):
        self.server.register_remote_method("notify", self.notify_action)

    async def notify_action(self, name: str, message: str = ""):
        if name not in self.notifiers:
            raise self.server.error(f"Notifier '{name}' not found", 404)
        notifier = self.notifiers[name]
        await notifier.notify("remote_action", [], message)

    async def _on_job_state_changed(
            self,
            job_event: JobEvent,
            prev_stats: Dict[str, Any],
            new_stats: Dict[str, Any]
    ) -> None:
        evt_name = str(job_event)
        for notifier in self.events.get(evt_name, []):
            await notifier.notify(evt_name, [prev_stats, new_stats])

    def register_endpoints(self, config: ConfigHelper):
        self.server.register_endpoint(
            "/server/notifiers/list", RequestType.GET, self._handle_notifier_list
        )
        self.server.register_debug_endpoint(
            "/debug/notifiers/test", RequestType.POST, self._handle_notifier_test
        )

    async def _handle_notifier_list(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        return {"notifiers": self._list_notifiers()}

    def _list_notifiers(self) -> List[Dict[str, Any]]:
        return [notifier.as_dict() for notifier in self.notifiers.values()]

    async def _handle_notifier_test(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:

        name = web_request.get_str("name")
        if name not in self.notifiers:
            raise self.server.error(f"Notifier '{name}' not found", 404)
        notifier = self.notifiers[name]

        kapis: APIComp = self.server.lookup_component('klippy_apis')
        result: Dict[str, Any] = await kapis.query_objects(
            {'print_stats': None}, default={})
        print_stats = result.get('print_stats', {})
        print_stats["filename"] = "notifier_test.gcode"  # Mock the filename

        await notifier.notify(notifier.events[0], [print_stats, print_stats])

        return {
            "status": "success",
            "stats": print_stats
        }

class NotifierInstance:
    def __init__(self, config: ConfigHelper) -> None:
        self.config = config
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.apprise = apprise.Apprise()
        self.attach = config.gettemplate("attach", None)
        url_template = config.gettemplate("url")
        self.url = url_template.render()

        if re.match(r"\w+?://", self.url) is None:
            raise config.error(f"Invalid url for: {config.get_name()}")

        self.title = config.gettemplate("title", None)
        self.body = config.gettemplate("body", None)
        upper_body_format = config.get("body_format", 'text').upper()
        if not hasattr(apprise.NotifyFormat, upper_body_format):
            raise config.error(f"Invalid body_format for {config.get_name()}")
        self.body_format = getattr(apprise.NotifyFormat, upper_body_format)
        self.events: List[str] = config.getlist("events", separator=",")
        self.apprise.add(self.url)

    def as_dict(self):
        return {
            "name": self.name,
            "url": self.config.get("url"),
            "title": self.config.get("title", None),
            "body": self.config.get("body", None),
            "body_format": self.config.get("body_format", None),
            "events": self.events,
            "attach": self.attach
        }

    async def notify(
        self, event_name: str, event_args: List, message: str = ""
    ) -> None:
        context = {
            "event_name": event_name,
            "event_args": event_args,
            "event_message": message
        }

        rendered_title = (
            '' if self.title is None else self.title.render(context)
        )
        rendered_body = (
            event_name if self.body is None else self.body.render(context)
        )

        # Verify the attachment
        attachments: List[str] = []
        if self.attach is not None:
            fm: FileManager = self.server.lookup_component("file_manager")
            try:
                rendered = self.attach.render(context)
            except self.server.error as e:
                self.server.add_warning(
                    f"[notifier {self.name}]: The attachment is not valid. The "
                    "template failed to render.",
                    f"notifier {self.name}",
                    exc_info=e
                )
                self.attach = None
            else:
                for item in rendered.splitlines():
                    item = item.strip()
                    if not item:
                        continue
                    if re.match(r"https?://", item) is not None:
                        # Attachment is a url, system check not necessary
                        attachments.append(item)
                        continue
                    attach_path = pathlib.Path(item).expanduser().resolve()
                    if not attach_path.is_file():
                        self.server.add_warning(
                            f"[notifier {self.name}]: Invalid attachment detected, "
                            f"file does not exist: {attach_path}.",
                            f"notifier {self.name}"
                        )
                    elif not fm.can_access_path(attach_path):
                        self.server.add_warning(
                            f"[notifier {self.name}]: Invalid attachment detected, "
                            f"no read permission for the file {attach_path}.",
                            f"notifier {self.name}"
                        )
                    else:
                        attachments.append(str(attach_path))
        await self.apprise.async_notify(
            rendered_body.strip(), rendered_title.strip(),
            body_format=self.body_format,
            attach=None if not attachments else attachments
        )

    def get_name(self) -> str:
        return self.name


def load_component(config: ConfigHelper) -> Notifier:
    return Notifier(config)
