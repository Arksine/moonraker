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
from typing import (
    TYPE_CHECKING,
    Awaitable,
    List,
    Dict,
    Any,
    Optional,
    Union
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from http_client import HttpClient
    from components.database import MoonrakerDatabase


MOONLIGHT_URL = "https://arksine.github.io/moonlight"
UPDATE_CHECK_TIME = 1800.
etree.register_namespace("moonlight", MOONLIGHT_URL)

class Announcements:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.config = config
        self.entry_mgr = EntryManager(config)
        self.eventloop = self.server.get_event_loop()
        self.update_timer = self.eventloop.register_timer(
            self._handle_update_timer
        )
        self.request_lock = asyncio.Lock()
        self.subscriptions: Dict[str, RssFeed] = {
            "moonraker": RssFeed(config, "moonraker", self.entry_mgr),
            "klipper": RssFeed(config, "klipper", self.entry_mgr)
        }
        self.stored_feeds: List[str] = []
        sub_list: List[str] = config.getlist("subscriptions", [])
        self.configured_feeds: List[str] = ["moonraker", "klipper"]
        for sub in sub_list:
            sub = sub.lower()
            if sub in self.subscriptions:
                continue
            self.configured_feeds.append(sub)
            self.subscriptions[sub] = RssFeed(config, sub, self.entry_mgr)

        self.server.register_endpoint(
            "/server/announcements/list", ["GET"],
            self._list_announcements
        )
        self.server.register_endpoint(
            "/server/announcements/dismiss", ["POST"],
            self._handle_dismiss_request
        )
        self.server.register_endpoint(
            "/server/announcements/update", ["POST"],
            self._handle_update_request
        )
        self.server.register_endpoint(
            "/server/announcements/feed", ["POST", "DELETE"],
            self._handle_feed_request
        )
        self.server.register_notification(
            "announcements:dismissed", "announcement_dismissed"
        )
        self.server.register_notification(
            "announcements:entries_updated", "announcement_update"
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
            feed = RssFeed(self.config, name, self.entry_mgr)
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
            await self.entry_mgr.dismiss_entry(entry_id)
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
        subs: Optional[Union[str, List[str]]]
        subs = web_request.get("subscriptions", None)
        if isinstance(subs, str):
            subs = [sub.strip() for sub in subs.split(",") if sub.strip()]
        elif subs is None:
            subs = list(self.subscriptions.keys())
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

    async def _handle_feed_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        action = web_request.get_action()
        name: str = web_request.get("name")
        name = name.lower()
        changed: bool = False
        db: MoonrakerDatabase = self.server.lookup_component("database")
        result = "skipped"
        if action == "POST":
            if name not in self.subscriptions:
                feed = RssFeed(self.config, name, self.entry_mgr)
                self.subscriptions[name] = feed
                await feed.initialize()
                changed = await feed.update_entries()
                self.stored_feeds.append(name)
                db.insert_item(
                    "moonraker", "announcements.stored_feeds", self.stored_feeds
                )
                result = "added"
        elif action == "DELETE":
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
            "source": "internal",
            "feed": feed
        }
        self.entry_mgr.add_entry(entry)
        return entry

    async def remove_internal_announcment(self, entry_id: str) -> None:
        ret = await self.entry_mgr.remove_entry(entry_id)
        if ret is not None:
            entries = await self.entry_mgr.list_entries()
            self.server.send_event(
                "announcements:entries_updated", {"entries": entries}
            )

class EntryManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        database: MoonrakerDatabase
        database = self.server.lookup_component("database")
        database.register_local_namespace("announcements")
        self.announce_db = database.wrap_namespace("announcements")
        self.entry_id_map: Dict[str, str] = {}
        self.next_key = 0

    async def initialize(self) -> None:
        last_key = ""
        for key, entry in await self.announce_db.items():
            last_key = key
            aid = entry["entry_id"]
            self.entry_id_map[aid] = key
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

    async def dismiss_entry(self, entry_id: str) -> None:
        key = self.entry_id_map.get(entry_id)
        if key is None:
            raise self.server.error(f"No key matching entry id: {entry_id}")
        is_dismissed = await self.announce_db[f"{key}.dismissed"]
        if is_dismissed:
            return
        await self.announce_db.insert(f"{key}.dismissed", True)
        eventloop = self.server.get_event_loop()
        eventloop.delay_callback(
            .05, self.server.send_event, "announcements:dismissed",
            {"entry_id": entry_id}
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
            if entry["feed"] == feed:
                key = self.entry_id_map.pop(entry["entry_id"], None)
                if key is not None:
                    del_keys.append(key)
        if del_keys:
            self.announce_db.delete_batch(del_keys)
            return True
        return False

class RssFeed:
    def __init__(
        self, config: ConfigHelper, name: str, entry_mgr: EntryManager
    ) -> None:
        self.server = config.get_server()
        self.name = name
        self.entry_mgr = entry_mgr
        self.client: HttpClient = self.server.lookup_component("http_client")
        database: MoonrakerDatabase
        database = self.server.lookup_component("database")
        self.moon_db = database.wrap_namespace("moonraker")
        self.xml_file = f"{self.name}.xml"
        self.asset_url = f"{MOONLIGHT_URL}/assets/{self.xml_file}"
        self.warned: bool = False
        self.last_modified: int = 0
        self.etag: Optional[str] = None
        self.dev_xml_path: Optional[pathlib.Path] = None
        dev_mode = config.getboolean("dev_mode", False)
        if dev_mode:
            res_dir = pathlib.Path(__file__).parent.parent.parent.resolve()
            res_path = res_dir.joinpath(".devel/announcement_xml")
            self.dev_xml_path = res_path.joinpath(self.xml_file)

    async def initialize(self) -> None:
        self.etag = await self.moon_db.get(
            f"announcements.{self.name}.etag", None
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
            self.asset_url, headers, enable_cache=False
        )
        if resp.has_error():
            msg = f"Failed to update subscription '{self.name}': {resp.error}"
            logging.info(msg)
            if not self.warned:
                self.warned = True
                self.server.add_warning(msg)
            return ""
        if resp.status_code == 304:
            logging.debug(f"Content at {self.xml_file} not modified")
            return ""
        # update etag
        self.etag = resp.etag
        if self.etag is not None:
            self.moon_db[f"announcements.{self.name}.etag"] = resp.etag
        else:
            self.moon_db.pop(f"announcements.{self.name}.etag", None)
        return resp.text

    async def _fetch_local_folder(self) -> str:
        if self.dev_xml_path is None:
            return ""
        if not self.dev_xml_path.is_file():
            msg = f"No file at path {self.dev_xml_path}"
            if not self.warned:
                self.warned = True
                self.server.add_warning(msg)
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
            msg = f"Unable read xml file {self.dev_xml_path}"
            if not self.warned:
                self.warned = True
                self.server.add_warning(msg)
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
                "source": "moonlight",
                "feed": self.name.capitalize()
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
