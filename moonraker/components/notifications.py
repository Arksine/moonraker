# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from asyncio_mqtt import Client, MqttError
import logging
import time

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
    from websockets import WebRequest
    from . import database
    from . import klippy_apis
    from . import file_manager
    DBComp = database.MoonrakerDatabase
    APIComp = klippy_apis.KlippyAPI

class Notifications:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.notifications = {}
        self.print_stats: Dict[str, Any] = {}

        prefix_sections = config.get_prefix_sections("notifications ")
        logging.info(
            f"Notification component loading services: {prefix_sections}")
        notify_types = {
            "mqtt": Mqtt
        }
        for section in prefix_sections:
            cfg = config[section]
            type: str = cfg.get("type")
            notify_class = notify_types.get(type)
            if notify_class is None:
                raise config.error(
                    f"Unsupported Notification Type: {type}")
            notify = notify_class(cfg)
            self.notifications[notify.get_name()] = notify

        self.server.register_event_handler(
            "server:klippy_identified", self._identified)
        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)

    def on_exit(self) -> None:
        for notify in self.notifications:
            self.notifications[notify].on_exit()

    async def _identified(self) -> None:
        for notify in self.notifications:
            await self.notifications[notify].start()

    async def _init_ready(self) -> None:
        klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        sub: Dict[str, Optional[List[str]]] = {"print_stats": None}
        try:
            result = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
        self.print_stats = result.get("print_stats", {})

    async def _notify(self, notification: str) -> None:
        for notify in self.notifications:
            await self.notifications[notify].notify(
                notification, self.print_stats.get('filename', None))

    async def _status_update(self, data: Dict[str, Any]) -> None:
        ps = data.get("print_stats", {})
        if "state" in ps:
            old_state: str = self.print_stats['state']
            new_state: str = ps['state']
            self.print_stats.update(ps)

            if new_state is not old_state:
                logging.info("New state: %s" % new_state)
                if new_state == "printing":
                    await self._notify("printing")
                elif old_state in ['printing', 'paused']:
                    if new_state == "complete":
                        await self._notify("completed")
                    elif new_state == "cancelled" or new_state == "standby":
                        await self._notify("cancelled")
                    elif new_state == "error":
                        await self._notify("error")
        else:
            self.print_stats.update(ps)

class NotificationService:
    def __init__(self, config: ConfigHelper) -> None:
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]

    def get_name(self) -> str:
        return self.name

    def on_exit(self) -> None:
        pass

    async def notify(self, notification: str, filename: str) -> None:
        pass

    async def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

class Mqtt(NotificationService):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.client: Client = None
        self.conf = {
            "host": config.get("host", None),
            "port": config.getint('port', 1883),
            "username": config.get("username", None),
            "password": config.get("password", None),
            "topic": config.get("topic", None)
        }

        if self.conf['host'] is None:
            raise config.error("No MQTT host was specified")
        if self.conf['topic'] is None:
            raise config.error("No MQTT topic was specified")

    async def notify(self, notification: str, filename: str) -> None:
        await self.client.publish(self.conf['topic'], "%s %s" % (
            notification, filename))

    async def start(self) -> None:
        self.client = Client(
            self.conf['host'], self.conf['port'],
            username=self.conf['username'], password=self.conf['password'])
        await AsyncExitStack().enter_async_context(self.client)

    def stop(self) -> None:
        self.client = None

def load_component(config: ConfigHelper) -> Notifications:
    return Notifications(config)
