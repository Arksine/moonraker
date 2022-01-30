# Mimimal database for moonraker storage
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import json
import struct
import operator
import logging
from io import BytesIO
from functools import reduce
from threading import Lock as ThreadLock
import lmdb
from utils import SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Coroutine,
    ItemsView,
    ValuesView,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    DBRecord = Union[int, float, bool, str, List[Any], Dict[str, Any]]
    DBType = Optional[DBRecord]

DATABASE_VERSION = 1
MAX_NAMESPACES = 100
MAX_DB_SIZE = 200 * 2**20

RECORD_ENCODE_FUNCS = {
    int: lambda x: b"q" + struct.pack("q", x),
    float: lambda x: b"d" + struct.pack("d", x),
    bool: lambda x: b"?" + struct.pack("?", x),
    str: lambda x: b"s" + x.encode(),
    list: lambda x: json.dumps(x).encode(),
    dict: lambda x: json.dumps(x).encode(),
}

RECORD_DECODE_FUNCS = {
    ord("q"): lambda x: struct.unpack("q", x[1:])[0],
    ord("d"): lambda x: struct.unpack("d", x[1:])[0],
    ord("?"): lambda x: struct.unpack("?", x[1:])[0],
    ord("s"): lambda x: bytes(x[1:]).decode(),
    ord("["): lambda x: json.load(BytesIO(x)),
    ord("{"): lambda x: json.load(BytesIO(x)),
}

SENTINEL = SentinelClass.get_instance()

def getitem_with_default(item: Dict, field: Any) -> Any:
    if field not in item:
        item[field] = {}
    return item[field]


class MoonrakerDatabase:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.namespaces: Dict[str, object] = {}
        self.thread_lock = ThreadLock()
        self.enable_debug = config.getboolean("enable_database_debug", False)
        self.database_path = os.path.expanduser(config.get(
            'database_path', "~/.moonraker_database"))
        if not os.path.isdir(self.database_path):
            os.mkdir(self.database_path)
        self.lmdb_env = lmdb.open(self.database_path, map_size=MAX_DB_SIZE,
                                  max_dbs=MAX_NAMESPACES)
        with self.lmdb_env.begin(write=True, buffers=True) as txn:
            # lookup existing namespaces
            with txn.cursor() as cursor:
                remaining = cursor.first()
                while remaining:
                    key = bytes(cursor.key())
                    self.namespaces[key.decode()] = self.lmdb_env.open_db(
                        key, txn)
                    remaining = cursor.next()
            if "moonraker" not in self.namespaces:
                mrdb = self.lmdb_env.open_db(b"moonraker", txn)
                self.namespaces["moonraker"] = mrdb
                txn.put(b'database_version',
                        self._encode_value(DATABASE_VERSION),
                        db=mrdb)
            # Iterate through all records, checking for invalid keys
            for ns, db in self.namespaces.items():
                with txn.cursor(db=db) as cursor:
                    remaining = cursor.first()
                    while remaining:
                        key_buf = cursor.key()
                        try:
                            decoded_key = bytes(key_buf).decode()
                        except Exception:
                            logging.info("Database Key Decode Error")
                            decoded_key = ''
                        if not decoded_key:
                            hex_key = bytes(key_buf).hex()
                            try:
                                invalid_val = self._decode_value(cursor.value())
                            except Exception:
                                invalid_val = ""
                            logging.info(
                                f"Invalid Key '{hex_key}' found in namespace "
                                f"'{ns}', dropping value: {repr(invalid_val)}")
                            try:
                                remaining = cursor.delete()
                            except Exception:
                                logging.exception("Error Deleting LMDB Key")
                            else:
                                continue
                        remaining = cursor.next()

        # Protected Namespaces have read-only API access.  Write access can
        # be granted by enabling the debug option.  Forbidden namespaces
        # have no API access.  This cannot be overridden.
        self.protected_namespaces = set(self.get_item(
            "moonraker", "database.protected_namespaces", ["moonraker"]))
        self.forbidden_namespaces = set(self.get_item(
            "moonraker", "database.forbidden_namespaces", []))
        # Track debug access and unsafe shutdowns
        debug_counter: int = self.get_item(
            "moonraker", "database.debug_counter", 0)
        if self.enable_debug:
            debug_counter += 1
            self.insert_item("moonraker", "database.debug_counter",
                             debug_counter)
        unsafe_shutdowns: int = self.get_item(
            "moonraker", "database.unsafe_shutdowns", 0)
        msg = f"Unsafe Shutdown Count: {unsafe_shutdowns}"
        if debug_counter:
            msg += f"; Database Debug Count: {debug_counter}"
        self.server.add_log_rollover_item("database", msg)

        # Increment unsafe shutdown counter.  This will be reset if
        # moonraker is safely restarted
        self.insert_item("moonraker", "database.unsafe_shutdowns",
                         unsafe_shutdowns + 1)
        self.server.register_endpoint(
            "/server/database/list", ['GET'], self._handle_list_request)
        self.server.register_endpoint(
            "/server/database/item", ["GET", "POST", "DELETE"],
            self._handle_item_request)

    def insert_item(self,
                    namespace: str,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> Awaitable[None]:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._insert_impl, namespace, key, value)
        else:
            self._insert_impl(namespace, key, value)
            fut = self.eventloop.create_future()
            fut.set_result(None)
            return fut

    def _insert_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     value: DBType
                     ) -> None:
        with self.thread_lock:
            key_list = self._process_key(key)
            if namespace not in self.namespaces:
                self.namespaces[namespace] = self.lmdb_env.open_db(
                    namespace.encode())
            record = value
            if len(key_list) > 1:
                record = self._get_record(namespace, key_list[0], force=True)
                if not isinstance(record, dict):
                    record = {}
                    logging.info(
                        f"Warning: Key {key_list[0]} contains a value of type"
                        f" {type(record)}. Overwriting with an object.")
                item: Dict[str, Any] = reduce(
                    getitem_with_default, key_list[1:-1], record)
                item[key_list[-1]] = value
            if not self._insert_record(namespace, key_list[0], record):
                logging.info(
                    f"Error inserting key '{key}' in namespace '{namespace}'")

    def update_item(self,
                    namespace: str,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> Awaitable[None]:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._update_impl, namespace, key, value)
        else:
            self._update_impl(namespace, key, value)
            fut = self.eventloop.create_future()
            fut.set_result(None)
            return fut

    def _update_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     value: DBType
                     ) -> None:
        with self.thread_lock:
            key_list = self._process_key(key)
            record = self._get_record(namespace, key_list[0])
            if len(key_list) == 1:
                if isinstance(record, dict) and isinstance(value, dict):
                    record.update(value)
                else:
                    assert value is not None
                    record = value
            else:
                try:
                    assert isinstance(record, dict)
                    item: Dict[str, Any] = reduce(
                        operator.getitem, key_list[1:-1], record)
                except Exception:
                    raise self.server.error(
                        f"Key '{key}' in namespace '{namespace}' not found",
                        404)
                if isinstance(item[key_list[-1]], dict) \
                        and isinstance(value, dict):
                    item[key_list[-1]].update(value)
                else:
                    item[key_list[-1]] = value
            if not self._insert_record(namespace, key_list[0], record):
                logging.info(
                    f"Error updating key '{key}' in namespace '{namespace}'")

    def delete_item(self,
                    namespace: str,
                    key: Union[List[str], str],
                    drop_empty_db: bool = False
                    ) -> Any:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._delete_impl, namespace, key, drop_empty_db)
        else:
            return self._delete_impl(namespace, key, drop_empty_db)

    def _delete_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     drop_empty_db: bool = False
                     ) -> Any:
        with self.thread_lock:
            key_list = self._process_key(key)
            val = record = self._get_record(namespace, key_list[0])
            remove_record = True
            if len(key_list) > 1:
                try:
                    assert isinstance(record, dict)
                    item: Dict[str, Any] = reduce(
                        operator.getitem, key_list[1:-1], record)
                    val = item.pop(key_list[-1])
                except Exception:
                    raise self.server.error(
                        f"Key '{key}' in namespace '{namespace}' not found",
                        404)
                remove_record = False if record else True
            if remove_record:
                db = self.namespaces[namespace]
                with (
                    self.lmdb_env.begin(write=True, buffers=True, db=db) as txn
                ):
                    ret = txn.delete(key_list[0].encode())
                    with txn.cursor() as cursor:
                        if not cursor.first() and drop_empty_db:
                            txn.drop(db)
                            del self.namespaces[namespace]
            else:
                ret = self._insert_record(namespace, key_list[0], record)
            if not ret:
                logging.info(
                    f"Error deleting key '{key}' from namespace "
                    f"'{namespace}'")
            return val

    def get_item(self,
                 namespace: str,
                 key: Optional[Union[List[str], str]] = None,
                 default: Any = SENTINEL
                 ) -> Any:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._get_impl, namespace, key, default)
        else:
            return self._get_impl(namespace, key, default)

    def _get_impl(self,
                  namespace: str,
                  key: Optional[Union[List[str], str]] = None,
                  default: Any = SENTINEL
                  ) -> Any:
        with self.thread_lock:
            try:
                if key is None:
                    return self._get_namespace(namespace)
                key_list = self._process_key(key)
                ns = self._get_record(namespace, key_list[0])
                val = reduce(operator.getitem,  # type: ignore
                             key_list[1:], ns)
            except Exception:
                if not isinstance(default, SentinelClass):
                    return default
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404)
            return val

    def update_namespace(self,
                         namespace: str,
                         value: Dict[str, DBRecord]
                         ) -> Awaitable[None]:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._update_ns_impl, namespace, value)
        else:
            self._update_ns_impl(namespace, value)
            fut = self.eventloop.create_future()
            fut.set_result(None)
            return fut

    def _update_ns_impl(self,
                        namespace: str,
                        value: Dict[str, DBRecord]
                        ) -> None:
        with self.thread_lock:
            if not value:
                return
            if namespace not in self.namespaces:
                raise self.server.error(
                    f"Invalid database namespace '{namespace}'")
            db = self.namespaces[namespace]
            with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
                # We only need to update the keys that changed
                for key, val in value.items():
                    stored = txn.get(key.encode())
                    if stored is not None:
                        decoded = self._decode_value(stored)
                        if val == decoded:
                            continue
                    ret = txn.put(key.encode(), self._encode_value(val))
                    if not ret:
                        logging.info(f"Error inserting key '{key}' "
                                     f"in namespace '{namespace}'")

    def clear_namespace(self,
                        namespace: str,
                        drop_empty_db: bool = False
                        ) -> Awaitable[None]:
        if self.eventloop.is_running():
            return self.eventloop.run_in_thread(
                self._clear_ns_impl, namespace, drop_empty_db)
        else:
            self._clear_ns_impl(namespace, drop_empty_db)
            fut = self.eventloop.create_future()
            fut.set_result(None)
            return fut

    def _clear_ns_impl(self,
                       namespace: str,
                       drop_empty_db: bool = False
                       ) -> None:
        with self.thread_lock:
            if namespace not in self.namespaces:
                raise self.server.error(
                    f"Invalid database namespace '{namespace}'")
            db = self.namespaces[namespace]
            with self.lmdb_env.begin(write=True, db=db) as txn:
                txn.drop(db, delete=drop_empty_db)
            if drop_empty_db:
                del self.namespaces[namespace]

    async def ns_length_async(self, namespace: str) -> int:
        return len(await self.ns_keys_async(namespace))

    def ns_length(self, namespace: str) -> int:
        return len(self.ns_keys(namespace))

    def ns_keys_async(self, namespace: str) -> Awaitable[List[str]]:
        return self.eventloop.run_in_thread(self.ns_keys, namespace)

    def ns_keys(self, namespace: str) -> List[str]:
        with self.thread_lock:
            keys: List[str] = []
            db = self.namespaces[namespace]
            with self.lmdb_env.begin(db=db) as txn:
                with txn.cursor() as cursor:
                    remaining = cursor.first()
                    while remaining:
                        keys.append(cursor.key().decode())
                        remaining = cursor.next()
            return keys

    def ns_values_async(self, namespace: str) -> Awaitable[ValuesView]:
        return self.eventloop.run_in_thread(self.ns_values, namespace)

    def ns_values(self, namespace: str) -> ValuesView:
        with self.thread_lock:
            ns = self._get_namespace(namespace)
            return ns.values()

    def ns_items_async(self, namespace: str) -> Awaitable[ItemsView]:
        return self.eventloop.run_in_thread(self.ns_items, namespace)

    def ns_items(self, namespace: str) -> ItemsView:
        with self.thread_lock:
            ns = self._get_namespace(namespace)
            return ns.items()

    def ns_contains_async(self,
                          namespace: str,
                          key: Union[List[str], str]
                          ) -> Awaitable[bool]:
        return self.eventloop.run_in_thread(
            self.ns_contains, namespace, key)

    def ns_contains(self, namespace: str, key: Union[List[str], str]) -> bool:
        with self.thread_lock:
            try:
                key_list = self._process_key(key)
                record = self._get_record(namespace, key_list[0])
                if len(key_list) == 1:
                    return True
                reduce(operator.getitem,      # type: ignore
                       key_list[1:], record)
            except Exception:
                return False
            return True

    def register_local_namespace(self,
                                 namespace: str,
                                 forbidden: bool = False
                                 ) -> None:
        if self.eventloop.is_running():
            raise self.server.error(
                "Cannot register a namespace while the "
                "eventloop is running")
        if namespace not in self.namespaces:
            self.namespaces[namespace] = self.lmdb_env.open_db(
                namespace.encode())
        if forbidden:
            if namespace not in self.forbidden_namespaces:
                self.forbidden_namespaces.add(namespace)
                self.insert_item(
                    "moonraker", "database.forbidden_namespaces",
                    list(self.forbidden_namespaces))
        elif namespace not in self.protected_namespaces:
            self.protected_namespaces.add(namespace)
            self.insert_item("moonraker", "database.protected_namespaces",
                             list(self.protected_namespaces))

    def wrap_namespace(self,
                       namespace: str,
                       parse_keys: bool = True
                       ) -> NamespaceWrapper:
        if self.eventloop.is_running():
            raise self.server.error(
                "Cannot wrap a namespace while the "
                "eventloop is running")
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Namespace '{namespace}' not found", 404)
        return NamespaceWrapper(namespace, self, parse_keys)

    def wrap_async_namespace(self,
                             namespace: str,
                             parse_keys: bool = True
                             ) -> AsyncNamespaceWrapper:
        if self.eventloop.is_running():
            raise self.server.error(
                "Cannot wrap a namespace while the "
                "eventloop is running")
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Namespace '{namespace}' not found", 404)
        return AsyncNamespaceWrapper(namespace, self, parse_keys)

    def _process_key(self, key: Union[List[str], str]) -> List[str]:
        try:
            key_list = key if isinstance(key, list) else key.split('.')
        except Exception:
            key_list = []
        if not key_list or "" in key_list:
            raise self.server.error(f"Invalid Key Format: '{key}'")
        return key_list

    def _insert_record(self, namespace: str, key: str, val: DBType) -> bool:
        db = self.namespaces[namespace]
        if val is None:
            return False
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            ret = txn.put(key.encode(), self._encode_value(val))
        return ret

    def _get_record(self,
                    namespace: str,
                    key: str,
                    force: bool = False
                    ) -> DBRecord:
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Namespace '{namespace}' not found", 404)
        db = self.namespaces[namespace]
        with self.lmdb_env.begin(buffers=True, db=db) as txn:
            value = txn.get(key.encode())
            if value is None:
                if force:
                    return {}
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404)
            return self._decode_value(value)

    def _get_namespace(self, namespace: str) -> Dict[str, Any]:
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Invalid database namespace '{namespace}'")
        db = self.namespaces[namespace]
        result = {}
        invalid_key_result = None
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            with txn.cursor() as cursor:
                has_remaining = cursor.first()
                while has_remaining:
                    db_key, value = cursor.item()
                    k = bytes(db_key).decode()
                    if not k:
                        invalid_key_result = self._decode_value(value)
                        logging.info(
                            f"Invalid Key '{db_key}' found in namespace "
                            f"'{namespace}', dropping value: "
                            f"{repr(invalid_key_result)}")
                        try:
                            has_remaining = cursor.delete()
                        except Exception:
                            logging.exception("Error Deleting LMDB Key")
                            has_remaining = cursor.next()
                    else:
                        result[k] = self._decode_value(value)
                        has_remaining = cursor.next()
        return result

    def _encode_value(self, value: DBRecord) -> bytes:
        try:
            enc_func = RECORD_ENCODE_FUNCS[type(value)]
            return enc_func(value)
        except Exception:
            raise self.server.error(
                f"Error encoding val: {value}, type: {type(value)}")

    def _decode_value(self, bvalue: bytes) -> DBRecord:
        fmt = bvalue[0]
        try:
            decode_func = RECORD_DECODE_FUNCS[fmt]
            return decode_func(bvalue)
        except Exception:
            raise self.server.error(
                f"Error decoding value {bvalue.decode()}, format: {chr(fmt)}")

    def can_call_sync(self, name: str = "") -> None:
        if self.eventloop.is_running():
            raise self.server.error(
                f"Cannot call method {name} while the eventloop is running")

    async def _handle_list_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, List[str]]:
        await self.eventloop.run_in_thread(self.thread_lock.acquire)
        try:
            ns_list = set(self.namespaces.keys()) - self.forbidden_namespaces
        finally:
            self.thread_lock.release()
        return {'namespaces': list(ns_list)}

    async def _handle_item_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        action = web_request.get_action()
        namespace = web_request.get_str("namespace")
        if namespace in self.forbidden_namespaces:
            raise self.server.error(
                f"Read/Write access to namespace '{namespace}'"
                " is forbidden", 403)
        key: Any
        valid_types: Tuple[type, ...]
        if action != "GET":
            if namespace in self.protected_namespaces and \
                    not self.enable_debug:
                raise self.server.error(
                    f"Write access to namespaces '{namespace}'"
                    " is forbidden", 403)
            key = web_request.get("key")
            valid_types = (list, str)
        else:
            key = web_request.get("key", None)
            valid_types = (list, str, type(None))
        if not isinstance(key, valid_types):
            raise self.server.error(
                "Value for argument 'key' is an invalid type: "
                f"{type(key).__name__}")
        if action == "GET":
            val = await self.get_item(namespace, key)
        elif action == "POST":
            val = web_request.get("value")
            await self.insert_item(namespace, key, val)
        elif action == "DELETE":
            val = await self.delete_item(namespace, key, drop_empty_db=True)
        return {'namespace': namespace, 'key': key, 'value': val}

    async def close(self) -> None:
        # Decrement unsafe shutdown counter
        unsafe_shutdowns: int = await self.get_item(
            "moonraker", "database.unsafe_shutdowns", 0)
        await self.insert_item(
            "moonraker", "database.unsafe_shutdowns",
            unsafe_shutdowns - 1)
        await self.eventloop.run_in_thread(self.thread_lock.acquire)
        try:
            # log db stats
            msg = ""
            with self.lmdb_env.begin() as txn:
                for db_name, db in self.namespaces.items():
                    stats = txn.stat(db)
                    msg += f"\n{db_name}:\n"
                    msg += "\n".join([f"{k}: {v}" for k, v in stats.items()])
            logging.info(f"Database statistics:\n{msg}")
            self.lmdb_env.sync()
            self.lmdb_env.close()
        finally:
            self.thread_lock.release()

class NamespaceWrapper:
    def __init__(self,
                 namespace: str,
                 database: MoonrakerDatabase,
                 parse_keys: bool
                 ) -> None:
        self.namespace = namespace
        self.db = database
        # If parse keys is true, keys of a string type
        # will be passed straight to the DB methods.
        self.parse_keys = parse_keys

    def to_async_wrapper(self) -> AsyncNamespaceWrapper:
        return AsyncNamespaceWrapper(self.namespace, self.db, self.parse_keys)

    def insert(self, key: Union[List[str], str], value: DBType) -> None:
        self.db.can_call_sync("insert")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        self.db.insert_item(self.namespace, key, value)

    def update_child(self, key: Union[List[str], str], value: DBType) -> None:
        self.db.can_call_sync("update_child")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        self.db.update_item(self.namespace, key, value)

    def update(self, value: Dict[str, DBRecord]) -> None:
        self.db.can_call_sync("update")
        self.db.update_namespace(self.namespace, value)

    def get(self,
            key: Union[List[str], str],
            default: Any = None
            ) -> Any:
        self.db.can_call_sync("get")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.get_item(self.namespace, key, default)

    def delete(self, key: Union[List[str], str]) -> Any:
        self.db.can_call_sync("delete")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.delete_item(self.namespace, key)

    def __len__(self) -> int:
        self.db.can_call_sync("length")
        return self.db.ns_length(self.namespace)

    def __getitem__(self, key: Union[List[str], str]) -> Any:
        return self.get(key, default=SENTINEL)

    def __setitem__(self,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> None:
        self.insert(key, value)

    def __delitem__(self, key: Union[List[str], str]):
        self.delete(key)

    def __contains__(self, key: Union[List[str], str]) -> bool:
        self.db.can_call_sync("contains")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key)

    def keys(self) -> List[str]:
        self.db.can_call_sync("keys")
        return self.db.ns_keys(self.namespace)

    def values(self) -> ValuesView:
        self.db.can_call_sync("values")
        return self.db.ns_values(self.namespace)

    def items(self) -> ItemsView:
        self.db.can_call_sync("items")
        return self.db.ns_items(self.namespace)

    def pop(self,
            key: Union[List[str], str],
            default: Any = SENTINEL
            ) -> Any:
        self.db.can_call_sync("pop")
        try:
            val = self.delete(key)
        except Exception:
            if isinstance(default, SentinelClass):
                raise
            val = default
        return val

    def clear(self) -> None:
        self.db.can_call_sync("clear")
        self.db.clear_namespace(self.namespace)

class AsyncNamespaceWrapper:
    def __init__(self,
                 namespace: str,
                 database: MoonrakerDatabase,
                 parse_keys: bool
                 ) -> None:
        self.namespace = namespace
        self.db = database
        self.eventloop = database.eventloop
        # If parse keys is true, keys of a string type
        # will be passed straight to the DB methods.
        self.parse_keys = parse_keys

    def to_sync_wrapper(self) -> NamespaceWrapper:
        return NamespaceWrapper(self.namespace, self.db, self.parse_keys)

    def insert(self,
               key: Union[List[str], str],
               value: DBType
               ) -> Awaitable[None]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.insert_item(self.namespace, key, value)

    def update_child(self,
                     key: Union[List[str], str],
                     value: DBType
                     ) -> Awaitable[None]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.update_item(self.namespace, key, value)

    def update(self, value: Dict[str, DBRecord]) -> Awaitable[None]:
        return self.db.update_namespace(self.namespace, value)

    async def get(self,
                  key: Union[List[str], str],
                  default: Any = None
                  ) -> Any:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return await self.db.get_item(self.namespace, key, default)

    def delete(self, key: Union[List[str], str]) -> Awaitable[Any]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.delete_item(self.namespace, key)

    async def length(self) -> int:
        return await self.db.ns_length_async(self.namespace)

    def __getitem__(self, key: Union[List[str], str]) -> Coroutine:
        return self.get(key, default=SENTINEL)

    def __setitem__(self,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> None:
        self.insert(key, value)

    def __delitem__(self, key: Union[List[str], str]):
        self.delete(key)

    async def contains(self, key: Union[List[str], str]) -> bool:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return await self.db.ns_contains_async(self.namespace, key)

    async def keys(self) -> List[str]:
        return await self.db.ns_keys_async(self.namespace)

    async def values(self) -> ValuesView:
        return await self.db.ns_values_async(self.namespace)

    async def items(self) -> ItemsView:
        return await self.db.ns_items_async(self.namespace)

    def pop(self,
            key: Union[List[str], str],
            default: Any = SENTINEL
            ) -> Awaitable[Any]:
        async def _do_pop() -> Any:
            try:
                val = await self.delete(key)
            except Exception:
                if isinstance(default, SentinelClass):
                    raise
                val = default
            return val
        return self.eventloop.create_task(_do_pop())

    def clear(self) -> Awaitable[None]:
        return self.db.clear_namespace(self.namespace)


def load_component(config: ConfigHelper) -> MoonrakerDatabase:
    return MoonrakerDatabase(config)
