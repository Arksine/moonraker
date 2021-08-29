# Notification Service
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import time

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
    from . import klippy_apis
    from . import mqtt
    APIComp = klippy_apis.KlippyAPI
    MQTTClient = mqtt.MQTTClient

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
            notify = notify_class(cfg, self)
            self.notifications[notify.get_name()] = notify

        self.server.register_event_handler(
            "server:klippy_identified", self._identified)
        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)

        self.server.register_notification("printer:state_cancelled")
        self.server.register_notification("printer:state_complete")
        self.server.register_notification("printer:state_error")
        self.server.register_notification("printer:state_paused")
        self.server.register_notification("printer:state_printing")

    async def get_current_filename(self) -> str:
        return self.print_stats.get('filename', None)

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
        logging.info("initial print stats: %s" % self.print_stats)

    async def _notify(self, notification: List[str]) -> None:
        for notify in self.notifications:
            await self.notifications[notify].notify(notification)

    async def _status_update(self, data: Dict[str, Any]) -> None:
        ps = data.get("print_stats", {})
        if "filename" in data.get("print_stats", {}):
            if self.print_stats['filename'] != data['print_stats']['filename']:
                logging.info("File changed: '%s' > '%s'" % (
                    self.print_stats['filename'],
                    data['print_stats']['filename']))
                if data['print_stats']['filename'] == '':
                    self.server.send_event("printer:file_unloaded")
                else:
                    self.server.send_event(
                        "printer:file_loaded",
                        data['print_stats']['filename'])
        if "state" in ps:
            old_state: str = self.print_stats['state']
            new_state: str = ps['state']
            self.print_stats.update(ps)

            if new_state is not old_state:
                if new_state == "printing":
                    self.server.send_event("printer:state_printing")
                elif new_state == "paused":
                    self.server.send_event("printer:state_paused")
                elif old_state in ['printing', 'paused']:
                    if new_state == "complete":
                        self.server.send_event("printer:state_complete")
                    elif new_state == "cancelled" or new_state == "standby":
                        self.server.send_event("printer:state_cancelled")
                    elif new_state == "error":
                        self.server.send_event("printer:state_error")
                elif new_state == "standby":
                    self.server.send_event("printer:state_standby")
        else:
            self.print_stats.update(ps)

class NotificationService:
    def __init__(self, config: ConfigHelper,
                 notifications: Notifications) -> None:
        self.server = config.get_server()
        self.notifications = notifications

        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.name = name_parts[1]

        events = [
            "printer:state_cancelled",
            "printer:state_complete",
            "printer:state_error",
            "printer:state_paused",
            "printer:state_printing",
            "printer:state_standby",
            "printer:file_loaded",
            "printer:file_unloaded"
        ]
        for evt in events:
            args = evt.split(':')
            for i in args.pop(1).split('_'):
                args.append(i)
            self.server.register_event_handler(evt, self.notify, args)

    def get_name(self) -> str:
        return self.name

    def on_exit(self) -> None:
        pass

    async def notify(self,
                     notification: List[str],
                     evtargs: List[Any] = None
                     ) -> None:
        pass

    async def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

class Mqtt(NotificationService):
    def __init__(self, config: ConfigHelper,
                 notifications: Notifications) -> None:
        super().__init__(config, notifications)
        self.conf = {
            "topic": "test"
        }

    async def notify(self,
                     notification: List[str],
                     evtargs: List[Any] = None
                     ) -> None:
        try:
            self.client: MQTTClient = self.server.lookup_component('mqtt')
            iname = self.client.get_instance_name()
        except Exception:
            logging.info("Unable to find mqtt client")
            return

        notification = notification.copy()
        payload = notification.pop(-1)
        tstr = "/".join(notification)
        topic = f"{iname}/moonraker/{tstr}"

        if tstr == "printer/file" and payload == "loaded":
            payload = "loaded %s" % (
                await self.notifications.get_current_filename())

        self.client.publish_topic(topic, payload)

def load_component(config: ConfigHelper) -> Notifications:
    return Notifications(config)
