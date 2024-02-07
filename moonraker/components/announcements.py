# Support for Moonraker/Klipper/Client announcements
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import datetime
import pathlib
import asyncio
import logging
import email.utils
import xml.etree.ElementTree as etree
from ..common import RequestType
from typing import (
    TYPE_CHECKING,
    Awaitable,
    List,
    Dict,
    Any,
    Optional
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .http_client import HttpClient
    from .database import MoonrakerDatabase


MOONLIGHT_URL = "https://arksine.github.io/moonlight"
UPDATE_CHECK_TIME = 1800.
etree.register_namespace("moonlight", MOONLIGHT_URL)

class Announcements:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.entry_mgr = EntryManager(config)
        self.eventloop = self.server.get_event_loop()
        self.update_timer = self.eventloop.register_timer(
            self._handle_update_timer
        )
        self.request_lock = asyncio.Lock()
        self.dev_mode = config.getboolean("dev_mode", False)
        self.subscriptions: Dict[str, RssFeed] = {
            "moonraker": RssFeed("moonraker", self.entry_mgr, self.dev_mode),
            "klipper": RssFeed("klipper", self.entry_mgr, self.dev_mode)
        }
        self.stored_feeds: List[str] = []
        sub_list: List[str] = config.getlist("subscriptions", [])
        self.configured_feeds: List[str] = ["moonraker", "klipper"]
        for sub in sub_list:
            sub = sub.lower()
            if sub in self.subscriptions:
                continue
            self.configured_feeds.append(sub)
            self.subscriptions[sub] = RssFeed(
                sub, self.entry_mgr, self.dev_mode
            )

        self.server.register_endpoint(
            "/server/announcements/list", RequestType.GET,
            self._list_announcements
        )
        self.server.register_endpoint(
            "/server/announcements/dismiss", RequestType.POST,
            self._handle_dismiss_request
        )
        self.server.register_endpoint(
            "/server/announcements/update", RequestType.POST,
            self._handle_update_request
        )
        self.server.register_endpoint(
            "/server/announcements/feed", RequestType.POST | RequestType.DELETE,
            self._handle_feed_request
        )
        self.server.register_endpoint(
            "/server/announcements/feeds", RequestType.GET,
            self._handle_list_feeds
        )
        self.server.register_notification(
            "announcements:dismissed", "announcement_dismissed"
        )
        self.server.register_notification(
            "announcements:entries_updated", "announcement_update"
        )
        self.server.register_notification(
            "announcements:dismiss_wake", "announcement_wake"
        )

    async def component_init(self) -> None:
        db: MoonrakerDatabase = self.server.lookup_component("database")
        stored_feeds: List[str] = await db.get_item(
            "moonraker", "announcements.stored_feeds", []
        )
        self.stored_feeds = stored_feeds
        for name in stored_feeds:
            if name in self.subscriptions:
                continue
            feed = RssFeed(name, self.entry_mgr, self.dev_mode)
            self.subscriptions[name] = feed
        async with self.request_lock:
            await self.entry_mgr.initialize()
            for sub in self.subscriptions.values():
                await sub.initialize()
        self.update_timer.start()

    async def _handle_update_timer(self, eventtime: float) -> float:
        changed = False
        entries: List[Dict[str, Any]] = []
        async with self.request_lock:
            for sub in self.subscriptions.values():
                ret = await sub.update_entries()
                changed |= ret
            if changed:
                entries = await self.entry_mgr.list_entries()
                self.server.send_event(
                    "announcements:entries_updated", {"entries": entries}
                )
        return eventtime + UPDATE_CHECK_TIME

    async def _handle_dismiss_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        async with self.request_lock:
            entry_id: str = web_request.get_str("entry_id")
            wake_time: Optional[int] = web_request.get_int("wake_time", None)
            await self.entry_mgr.dismiss_entry(entry_id, wake_time)
            return {
                "entry_id": entry_id
            }

    async def _list_announcements(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        async with self.request_lock:
            incl_dsm = web_request.get_boolean("include_dismissed", True)
            entries = await self.entry_mgr.list_entries(incl_dsm)
            return {
                "entries": entries,
                "feeds": list(self.subscriptions.keys())
            }

    async def _handle_update_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        subs = web_request.get_list("subscriptions", list(self.subscriptions.keys()))
        for sub in subs:
            if sub not in self.subscriptions:
                raise self.server.error(f"No subscription for {sub}")
        async with self.request_lock:
            changed = False
            for sub in subs:
                ret = await self.subscriptions[sub].update_entries()
                changed |= ret
            entries = await self.entry_mgr.list_entries()
            if changed:
                self.eventloop.delay_callback(
                    .05, self.server.send_event,
                    "announcements:entries_updated",
                    {"entries": entries})
            return {
                "entries": entries,
                "modified": changed
            }

    async def _handle_list_feeds(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        return {"feeds": list(self.subscriptions.keys())}

    async def _handle_feed_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        req_type = web_request.get_request_type()
        name: str = web_request.get("name")
        name = name.lower()
        changed: bool = False
        db: MoonrakerDatabase = self.server.lookup_component("database")
        result = "skipped"
        if req_type == RequestType.POST:
            if name not in self.subscriptions:
                feed = RssFeed(name, self.entry_mgr, self.dev_mode)
                self.subscriptions[name] = feed
                await feed.initialize()
                changed = await feed.update_entries()
                self.stored_feeds.append(name)
                db.insert_item(
                    "moonraker", "announcements.stored_feeds", self.stored_feeds
                )
                result = "added"
        elif req_type == RequestType.DELETE:
            if name not in self.stored_feeds:
                raise self.server.error(f"Feed '{name}' not stored")
            if name in self.configured_feeds:
                raise self.server.error(
                    f"Feed '{name}' exists in the configuration, cannot remove"
                )
            self.stored_feeds.remove(name)
            db.insert_item(
                "moonraker", "announcements.stored_feeds", self.stored_feeds
            )
            if name in self.subscriptions:
                del self.subscriptions[name]
                changed = await self.entry_mgr.prune_by_feed(name)
                logging.info(f"Removed Announcement Feed: {name}")
                result = "removed"
            else:
                raise self.server.error(f"Feed does not exist: {name}")
        if changed:
            entries = await self.entry_mgr.list_entries()
            self.eventloop.delay_callback(
                .05, self.server.send_event, "announcements:entries_updated",
                {"entries": entries}
            )
        return {
            "feed": name,
            "action": result
        }

    def add_internal_announcement(
        self, title: str, desc: str, url: str, priority: str, feed: str
    ) -> Dict[str, Any]:
        date = datetime.datetime.utcnow()
        entry_id: str = f"{feed}/{date.isoformat(timespec='seconds')}"
        entry = {
            "entry_id": entry_id,
            "url": url,
            "title": title,
            "description": desc,
            "priority": priority,
            "date": date.timestamp(),
            "dismissed": False,
            "date_dismissed": None,
            "dismiss_wake": None,
            "source": "internal",
            "feed": feed
        }
        self.entry_mgr.add_entry(entry)
        self.eventloop.create_task(self._notify_internal())
        return entry

    async def _notify_internal(self) -> None:
        entries = await self.entry_mgr.list_entries()
        self.server.send_event(
            "announcements:entries_updated", {"entries": entries}
        )

    async def remove_announcement(self, entry_id: str) -> None:
        ret = await self.entry_mgr.remove_entry(entry_id)
        if ret is not None:
            entries = await self.entry_mgr.list_entries()
            self.server.send_event(
                "announcements:entries_updated", {"entries": entries}
            )
    async def dismiss_announcement(
        self, entry_id, wake_time: Optional[int] = None
    ) -> None:
        await self.entry_mgr.dismiss_entry(entry_id, wake_time)

    async def get_announcements(
        self, include_dismissed: bool = False
    ) -> List[Dict[str, Any]]:
        return await self.entry_mgr.list_entries(include_dismissed)

    def register_feed(self, name: str) -> None:
        name = name.lower()
        if name in self.subscriptions:
            logging.info(f"Feed {name} already configured")
            return
        logging.info(f"Registering feed {name}")
        self.configured_feeds.append(name)
        self.subscriptions[name] = RssFeed(name, self.entry_mgr, self.dev_mode)

    def close(self):
        self.entry_mgr.close()

class EntryManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        database: MoonrakerDatabase
        database = self.server.lookup_component("database")
        database.register_local_namespace("announcements")
        self.announce_db = database.wrap_namespace("announcements")
        self.entry_id_map: Dict[str, str] = {}
        self.next_key = 0
        self.dismiss_handles: Dict[str, asyncio.TimerHandle] = {}

    async def initialize(self) -> None:
        last_key = ""
        eventloop = self.server.get_event_loop()
        curtime = datetime.datetime.utcnow().timestamp()
        for key, entry in await self.announce_db.items():
            last_key = key
            aid = entry["entry_id"]
            self.entry_id_map[aid] = key
            if entry["dismissed"]:
                wake_time: Optional[float] = entry.get("dismiss_wake")
                if wake_time is not None:
                    time_diff = wake_time - curtime
                    if time_diff - 10. < 0.:
                        # announcement is near or past wake time
                        entry["dismissed"] = False
                        entry["date_dismissed"] = None
                        entry["dismiss_wake"] = None
                        self.announce_db[key] = entry
                    else:
                        self.dismiss_handles[key] = eventloop.delay_callback(
                            time_diff, self._wake_dismissed, key
                        )
        if last_key:
            self.next_key = int(last_key, 16) + 1

    async def list_entries(
        self, include_dismissed: bool = True
    ) -> List[Dict[str, Any]]:
        vals: List[Dict[str, Any]] = await self.announce_db.values()
        vals = sorted(vals, key=lambda x: x["date"], reverse=True)
        if include_dismissed:
            return vals
        return [val for val in vals if not val["dismissed"]]

    def has_entry(self, entry_id: str) -> bool:
        return entry_id in self.entry_id_map

    def add_entry(self, entry: Dict[str, Any]) -> Awaitable[None]:
        aid = entry["entry_id"]
        key = f"{self.next_key:06X}"
        self.next_key += 1
        self.entry_id_map[aid] = key
        return self.announce_db.insert(key, entry)

    def remove_entry(self, entry_id: str) -> Awaitable[Any]:
        key = self.entry_id_map.pop(entry_id, None)
        if key is None:
            raise self.server.error(f"No key matching entry id: {entry_id}")
        return self.announce_db.pop(key, None)

    async def dismiss_entry(
        self, entry_id: str, wake_time: Optional[int] = None
    ) -> None:
        key = self.entry_id_map.get(entry_id)
        if key is None:
            raise self.server.error(f"No key matching entry id: {entry_id}")
        entry = await self.announce_db[key]
        is_dismissed = entry["dismissed"]
        if is_dismissed:
            return
        entry["dismissed"] = True
        eventloop = self.server.get_event_loop()
        curtime = datetime.datetime.utcnow().timestamp()
        entry["date_dismissed"] = curtime
        if wake_time is not None:
            entry["dismiss_wake"] = curtime + wake_time
            self.dismiss_handles[key] = eventloop.delay_callback(
                wake_time, self._wake_dismissed, key
            )
        self.announce_db[key] = entry
        eventloop.delay_callback(
            .05, self.server.send_event, "announcements:dismissed",
            {"entry_id": entry_id}
        )

    async def _wake_dismissed(self, key: str) -> None:
        self.dismiss_handles.pop(key, None)
        entry = await self.announce_db.get(key, None)
        if entry is None:
            return
        if not entry["dismissed"]:
            return
        entry["dismissed"] = False
        entry["date_dismissed"] = None
        entry["dismiss_wake"] = None
        self.announce_db[key] = entry
        self.server.send_event(
            "announcements:dismiss_wake", {"entry_id": entry["entry_id"]}
        )

    def prune_by_prefix(self, prefix: str, valid_ids: List[str]) -> bool:
        del_keys: List[str] = []
        for entry_id in list(self.entry_id_map.keys()):
            if not entry_id.startswith(prefix) or entry_id in valid_ids:
                continue
            # Entry is no longer valid and should be removed
            key = self.entry_id_map.pop(entry_id, None)
            if key is not None:
                del_keys.append(key)
        if del_keys:
            self.announce_db.delete_batch(del_keys)
            return True
        return False

    async def prune_by_feed(self, feed: str) -> bool:
        entries = await self.list_entries()
        del_keys: List[str] = []
        for entry in entries:
            if entry["feed"].lower() == feed:
                key = self.entry_id_map.pop(entry["entry_id"], None)
                if key is not None:
                    del_keys.append(key)
        if del_keys:
            self.announce_db.delete_batch(del_keys)
            return True
        return False

    def close(self):
        for handle in self.dismiss_handles.values():
            handle.cancel()

class RssFeed:
    def __init__(
        self, name: str, entry_mgr: EntryManager, dev_mode: bool
    ) -> None:
        self.server = entry_mgr.server
        self.name = name
        self.entry_mgr = entry_mgr
        self.client: HttpClient = self.server.lookup_component("http_client")
        self.database: MoonrakerDatabase
        self.database = self.server.lookup_component("database")
        self.xml_file = f"{self.name}.xml"
        self.asset_url = f"{MOONLIGHT_URL}/assets/{self.xml_file}"
        self.last_modified: int = 0
        self.etag: Optional[str] = None
        self.dev_xml_path: Optional[pathlib.Path] = None
        if dev_mode:
            res_dir = pathlib.Path(__file__).parent.parent.parent.resolve()
            res_path = res_dir.joinpath(".devel/announcement_xml")
            self.dev_xml_path = res_path.joinpath(self.xml_file)

    async def initialize(self) -> None:
        self.etag = await self.database.get_item(
            "moonraker", f"announcements.{self.name}.etag", None
        )

    async def update_entries(self) -> bool:
        if self.dev_xml_path is None:
            xml_data = await self._fetch_moonlight()
        else:
            xml_data = await self._fetch_local_folder()
        if not xml_data:
            return False
        return self._parse_xml(xml_data)

    async def _fetch_moonlight(self) -> str:
        headers = {"Accept": "application/xml"}
        if self.etag is not None:
            headers["If-None-Match"] = self.etag
        resp = await self.client.get(
            self.asset_url, headers, attempts=5,
            retry_pause_time=.5, enable_cache=False,
        )
        if resp.has_error():
            logging.info(
                f"Failed to update subscription '{self.name}': {resp.error}"
            )
            return ""
        if resp.status_code == 304:
            logging.debug(f"Content at {self.xml_file} not modified")
            return ""
        # update etag
        self.etag = resp.etag
        try:
            if self.etag is not None:
                self.database.insert_item(
                    "moonraker", f"announcements.{self.name}.etag", resp.etag
                )
            else:
                self.database.delete_item(
                    "moonraker", f"announcements.{self.name}.etag",
                )
        except self.server.error:
            pass
        return resp.text

    async def _fetch_local_folder(self) -> str:
        if self.dev_xml_path is None:
            return ""
        if not self.dev_xml_path.is_file():
            logging.info(f"No file at path {self.dev_xml_path}")
            return ""
        mtime = self.dev_xml_path.stat().st_mtime_ns
        if mtime <= self.last_modified:
            logging.debug(f"Content at {self.xml_file} not modified")
            return ""
        try:
            eventloop = self.server.get_event_loop()
            xml_data = await eventloop.run_in_thread(
                self.dev_xml_path.read_text)
        except Exception:
            logging.exception(f"Unable read xml file {self.dev_xml_path}")
            return ""
        self.last_modified = mtime
        return xml_data

    def _parse_xml(self, xml_data: str) -> bool:
        root = etree.fromstring(xml_data)
        channel = root.find("channel")
        if channel is None:
            root_str = etree.tostring(root, encoding="unicode")
            logging.debug(f"Feed {self.name}: no channel found\n{root_str}")
            return False
        # extract prefix
        prefix = channel.findtext("title", "").lower()
        if not prefix:
            logging.info(f"Feed {self.name}: No prefix found")
        items = channel.findall("item")
        valid_ids: List[str] = []
        changed: bool = False
        for item in items:
            guid = item.findtext("guid")
            if guid is None:
                item_str = etree.tostring(item, encoding="unicode")
                logging.debug(f"Feed {self.name}: Invalid Item\n{item_str}")
                continue
            if not prefix:
                # fall back to first guid prefix
                prefix = "/".join(guid.split("/")[:2])
            elif not guid.startswith(prefix):
                logging.debug(
                    f"Feed {self.name}: Guid {guid} is not "
                    f"prefixed with {prefix}")
            valid_ids.append(guid)
            if self.entry_mgr.has_entry(guid):
                continue
            try:
                rfc_date = item.findtext("pubDate", "")
                dt = email.utils.parsedate_to_datetime(rfc_date)
            except Exception:
                dt = datetime.datetime.utcnow()
            entry: Dict[str, Any] = {
                "entry_id": guid,
                "url": item.findtext("link"),
                "title": item.findtext("title"),
                "description": item.findtext("description"),
                "priority": item.findtext("category"),
                "date": dt.timestamp(),
                "dismissed": False,
                "date_dismissed": None,
                "dismiss_wake": None,
                "source": "moonlight",
                "feed": self.name
            }
            changed = True
            self.entry_mgr.add_entry(entry)
        logging.debug(f"Feed {self.name}: found entries {valid_ids}")
        if prefix:
            pruned = self.entry_mgr.prune_by_prefix(prefix, valid_ids)
            changed = changed or pruned
        return changed


def load_component(config: ConfigHelper) -> Announcements:
    return Announcements(config)
