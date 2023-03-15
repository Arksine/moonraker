# Mimimal database for moonraker storage
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import pathlib
import json
import struct
import operator
import logging
from asyncio import Future, Task
from functools import reduce
from threading import Lock as ThreadLock
import lmdb
from ..utils import Sentinel, ServerError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Mapping,
    TypeVar,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
    cast
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    DBRecord = Union[int, float, bool, str, List[Any], Dict[str, Any]]
    DBType = Optional[DBRecord]
    _T = TypeVar("_T")

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
    ord("["): lambda x: json.loads(bytes(x)),
    ord("{"): lambda x: json.loads(bytes(x)),
}

def getitem_with_default(item: Dict, field: Any) -> Any:
    if not isinstance(item, Dict):
        raise ServerError(
            f"Cannot reduce a value of type {type(item)}")
    if field not in item:
        item[field] = {}
    return item[field]


class MoonrakerDatabase:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.namespaces: Dict[str, object] = {}
        self.thread_lock = ThreadLock()
        app_args = self.server.get_app_args()
        dep_path = config.get("database_path", None, deprecate=True)
        db_path = pathlib.Path(app_args["data_path"]).joinpath("database")
        if (
            app_args["is_default_data_path"] and
            not db_path.joinpath("data.mdb").exists()
        ):
            # Allow configured DB fallback
            dep_path = dep_path or "~/.moonraker_database"
            legacy_db = pathlib.Path(dep_path).expanduser().resolve()
            try:
                same = legacy_db.samefile(db_path)
            except Exception:
                same = False
            if not same and legacy_db.exists():
                logging.info(
                    f"Reverting to legacy database path: {legacy_db}"
                )
                db_path = legacy_db
        if not db_path.is_dir():
            db_path.mkdir()
        self.database_path = str(db_path)
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
            "moonraker", "database.protected_namespaces",
            ["moonraker"]).result())
        self.forbidden_namespaces = set(self.get_item(
            "moonraker", "database.forbidden_namespaces",
            []).result())
        # Initialize Debug Counter
        config.getboolean("enable_database_debug", False, deprecate=True)
        self.debug_counter: Dict[str, int] = {"get": 0, "post": 0, "delete": 0}
        db_counter: Optional[Dict[str, int]] = self.get_item(
            "moonraker", "database.debug_counter", None
        ).result()
        if isinstance(db_counter, dict):
            self.debug_counter.update(db_counter)
            self.server.add_log_rollover_item(
                "database_debug_counter",
                f"Database Debug Counter: {self.debug_counter}"
            )
        # Track unsafe shutdowns
        unsafe_shutdowns: int = self.get_item(
            "moonraker", "database.unsafe_shutdowns", 0).result()
        msg = f"Unsafe Shutdown Count: {unsafe_shutdowns}"
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
        self.server.register_debug_endpoint(
            "/debug/database/list", ['GET'], self._handle_list_request)
        self.server.register_debug_endpoint(
            "/debug/database/item", ["GET", "POST", "DELETE"],
            self._handle_item_request)

    def get_database_path(self) -> str:
        return self.database_path

    def _run_command(self,
                     command_func: Callable[..., _T],
                     *args
                     ) -> Future[_T]:
        def func_wrapper():
            with self.thread_lock:
                return command_func(*args)

        if self.server.is_running():
            return cast(Future, self.eventloop.run_in_thread(func_wrapper))
        else:
            ret = func_wrapper()
            fut = self.eventloop.create_future()
            fut.set_result(ret)
            return fut

    # *** Nested Database operations***
    # The insert_item(), delete_item(), and get_item() methods may operate on
    # nested objects within a namespace.  Each operation takes a key argument
    # that may either be a string or a list of strings.  If the argument is
    # a string nested keys may be delitmted by a "." by which the string
    # will be split into a list of strings.  The first key in the list must
    # identify the database record.  Subsequent keys are optional and are
    # used to access elements in the deserialized objects.

    def insert_item(self,
                    namespace: str,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> Future[None]:
        return self._run_command(self._insert_impl, namespace, key, value)

    def _insert_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     value: DBType
                     ) -> None:
        key_list = self._process_key(key)
        if namespace not in self.namespaces:
            self.namespaces[namespace] = self.lmdb_env.open_db(
                namespace.encode())
        record = value
        if len(key_list) > 1:
            record = self._get_record(namespace, key_list[0], force=True)
            if not isinstance(record, dict):
                prev_type = type(record)
                record = {}
                logging.info(
                    f"Warning: Key {key_list[0]} contains a value of type "
                    f"{prev_type}. Overwriting with an object.")
            item: Dict[str, Any] = reduce(
                getitem_with_default, key_list[1:-1], record)
            if not isinstance(item, dict):
                rpt_key = ".".join(key_list[:-1])
                raise self.server.error(
                    f"Item at key '{rpt_key}' in namespace '{namespace}'is "
                    "not a dictionary object, cannot insert"
                )
            item[key_list[-1]] = value
        if not self._insert_record(namespace, key_list[0], record):
            logging.info(
                f"Error inserting key '{key}' in namespace '{namespace}'")

    def update_item(self,
                    namespace: str,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> Future[None]:
        return self._run_command(self._update_impl, namespace, key, value)

    def _update_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     value: DBType
                     ) -> None:
        key_list = self._process_key(key)
        record = self._get_record(namespace, key_list[0])
        if len(key_list) == 1:
            if isinstance(record, dict) and isinstance(value, dict):
                record.update(value)
            else:
                if value is None:
                    raise self.server.error(
                        f"Item at key '{key}', namespace '{namespace}': "
                        "Cannot assign a record level null value")
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
            if not isinstance(item, dict) or key_list[-1] not in item:
                rpt_key = ".".join(key_list[:-1])
                raise self.server.error(
                    f"Item at key '{rpt_key}' in namespace '{namespace}'is "
                    "not a dictionary object, cannot update"
                )
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
                    ) -> Future[Any]:
        return self._run_command(self._delete_impl, namespace, key,
                                 drop_empty_db)

    def _delete_impl(self,
                     namespace: str,
                     key: Union[List[str], str],
                     drop_empty_db: bool = False
                     ) -> Any:
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
            with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
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
                 default: Any = Sentinel.MISSING
                 ) -> Future[Any]:
        return self._run_command(self._get_impl, namespace, key, default)

    def _get_impl(self,
                  namespace: str,
                  key: Optional[Union[List[str], str]] = None,
                  default: Any = Sentinel.MISSING
                  ) -> Any:
        try:
            if key is None:
                return self._get_namespace(namespace)
            key_list = self._process_key(key)
            ns = self._get_record(namespace, key_list[0])
            val = reduce(operator.getitem,  # type: ignore
                         key_list[1:], ns)
        except Exception as e:
            if default is not Sentinel.MISSING:
                return default
            if isinstance(e, self.server.error):
                raise
            raise self.server.error(
                f"Key '{key}' in namespace '{namespace}' not found", 404)
        return val

    # *** Batch operations***
    #  The insert_batch(), move_batch(), delete_batch(), and get_batch()
    #  methods can be used to perform record level batch operations on
    #  a namespace in a single transaction.

    def insert_batch(self,
                     namespace: str,
                     records: Dict[str, Any]
                     ) -> Future[None]:
        return self._run_command(self._insert_batch_impl, namespace, records)

    def _insert_batch_impl(self,
                           namespace: str,
                           records: Dict[str, Any]
                           ) -> None:
        if namespace not in self.namespaces:
            self.namespaces[namespace] = self.lmdb_env.open_db(
                namespace.encode())
        db = self.namespaces[namespace]
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            for key, val in records.items():
                ret = txn.put(key.encode(), self._encode_value(val))
                if not ret:
                    logging.info(f"Error inserting record {key} into "
                                 f"namespace {namespace}")

    def move_batch(self,
                   namespace: str,
                   source_keys: List[str],
                   dest_keys: List[str]
                   ) -> Future[None]:
        return self._run_command(self._move_batch_impl, namespace,
                                 source_keys, dest_keys)

    def _move_batch_impl(self,
                         namespace: str,
                         source_keys: List[str],
                         dest_keys: List[str]
                         ) -> None:
        db = self._get_db(namespace)
        if len(source_keys) != len(dest_keys):
            raise self.server.error(
                "Source key list and destination key list must "
                "be of the same length")
        with self.lmdb_env.begin(write=True, db=db) as txn:
            for source, dest in zip(source_keys, dest_keys):
                val = txn.pop(source.encode())
                if val is not None:
                    txn.put(dest.encode(), val)

    def delete_batch(self,
                     namespace: str,
                     keys: List[str]
                     ) -> Future[Dict[str, Any]]:
        return self._run_command(self._del_batch_impl, namespace, keys)

    def _del_batch_impl(self,
                        namespace: str,
                        keys: List[str]
                        ) -> Dict[str, Any]:
        db = self._get_db(namespace)
        result: Dict[str, Any] = {}
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            for key in keys:
                val = txn.pop(key.encode())
                if val is not None:
                    result[key] = self._decode_value(val)
        return result

    def get_batch(self,
                  namespace: str,
                  keys: List[str]
                  ) -> Future[Dict[str, Any]]:
        return self._run_command(self._get_batch_impl, namespace, keys)

    def _get_batch_impl(self,
                        namespace: str,
                        keys: List[str]
                        ) -> Dict[str, Any]:
        db = self._get_db(namespace)
        result: Dict[str, Any] = {}
        encoded_keys: List[bytes] = [k.encode() for k in keys]
        with self.lmdb_env.begin(buffers=True, db=db) as txn:
            with txn.cursor() as cursor:
                vals = cursor.getmulti(encoded_keys)
                result = {bytes(k).decode(): self._decode_value(v)
                          for k, v in vals}
        return result

    # *** Namespace level operations***

    def update_namespace(self,
                         namespace: str,
                         value: Mapping[str, DBRecord]
                         ) -> Future[None]:
        return self._run_command(self._update_ns_impl, namespace, value)

    def _update_ns_impl(self,
                        namespace: str,
                        value: Mapping[str, DBRecord]
                        ) -> None:
        if not value:
            return
        db = self._get_db(namespace)
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
                        ) -> Future[None]:
        return self._run_command(self._clear_ns_impl, namespace, drop_empty_db)

    def _clear_ns_impl(self,
                       namespace: str,
                       drop_empty_db: bool = False
                       ) -> None:
        db = self._get_db(namespace)
        with self.lmdb_env.begin(write=True, db=db) as txn:
            txn.drop(db, delete=drop_empty_db)
        if drop_empty_db:
            del self.namespaces[namespace]

    def sync_namespace(self,
                       namespace: str,
                       value: Mapping[str, DBRecord]
                       ) -> Future[None]:
        return self._run_command(self._sync_ns_impl, namespace, value)

    def _sync_ns_impl(self,
                      namespace: str,
                      value: Mapping[str, DBRecord]
                      ) -> None:
        if not value:
            raise self.server.error("Cannot sync to an empty value")
        db = self._get_db(namespace)
        new_keys = set(value.keys())
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            with txn.cursor() as cursor:
                remaining = cursor.first()
                while remaining:
                    bkey, bval = cursor.item()
                    key = bytes(bkey).decode()
                    if key not in value:
                        remaining = cursor.delete()
                    else:
                        decoded = self._decode_value(bval)
                        if decoded != value[key]:
                            new_val = self._encode_value(value[key])
                            txn.put(key.encode(), new_val)
                        new_keys.remove(key)
                        remaining = cursor.next()
            for key in new_keys:
                val = value[key]
                ret = txn.put(key.encode(), self._encode_value(val))
                if not ret:
                    logging.info(f"Error inserting key '{key}' "
                                 f"in namespace '{namespace}'")

    def ns_length(self, namespace: str) -> Future[int]:
        return self._run_command(self._ns_length_impl, namespace)

    def _ns_length_impl(self, namespace: str) -> int:
        db = self._get_db(namespace)
        with self.lmdb_env.begin(db=db) as txn:
            stats = txn.stat(db)
        return stats['entries']

    def ns_keys(self, namespace: str) -> Future[List[str]]:
        return self._run_command(self._ns_keys_impl, namespace)

    def _ns_keys_impl(self, namespace: str) -> List[str]:
        keys: List[str] = []
        db = self._get_db(namespace)
        with self.lmdb_env.begin(db=db) as txn:
            with txn.cursor() as cursor:
                remaining = cursor.first()
                while remaining:
                    keys.append(cursor.key().decode())
                    remaining = cursor.next()
        return keys

    def ns_values(self, namespace: str) -> Future[List[Any]]:
        return self._run_command(self._ns_values_impl, namespace)

    def _ns_values_impl(self, namespace: str) -> List[Any]:
        values: List[Any] = []
        db = self._get_db(namespace)
        with self.lmdb_env.begin(db=db, buffers=True) as txn:
            with txn.cursor() as cursor:
                remaining = cursor.first()
                while remaining:
                    values.append(self._decode_value(cursor.value()))
                    remaining = cursor.next()
        return values

    def ns_items(self, namespace: str) -> Future[List[Tuple[str, Any]]]:
        return self._run_command(self._ns_items_impl, namespace)

    def _ns_items_impl(self, namespace: str) -> List[Tuple[str, Any]]:
        ns = self._get_namespace(namespace)
        return list(ns.items())

    def ns_contains(self,
                    namespace: str,
                    key: Union[List[str], str]
                    ) -> Future[bool]:
        return self._run_command(self._ns_contains_impl, namespace, key)

    def _ns_contains_impl(self,
                          namespace: str,
                          key: Union[List[str], str]
                          ) -> bool:
        self._get_db(namespace)
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
        if self.server.is_running():
            raise self.server.error(
                "Cannot register a namespace while the "
                "server is running")
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
                             sorted(self.protected_namespaces))

    def wrap_namespace(self,
                       namespace: str,
                       parse_keys: bool = True
                       ) -> NamespaceWrapper:
        if self.server.is_running():
            raise self.server.error(
                "Cannot wrap a namespace while the "
                "server is running")
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Namespace '{namespace}' not found", 404)
        return NamespaceWrapper(namespace, self, parse_keys)

    def _get_db(self, namespace: str) -> object:
        if namespace not in self.namespaces:
            raise self.server.error(f"Namespace '{namespace}' not found", 404)
        return self.namespaces[namespace]

    def _process_key(self, key: Union[List[str], str]) -> List[str]:
        try:
            key_list = key if isinstance(key, list) else key.split('.')
        except Exception:
            key_list = []
        if not key_list or "" in key_list:
            raise self.server.error(f"Invalid Key Format: '{key}'")
        return key_list

    def _insert_record(self, namespace: str, key: str, val: DBType) -> bool:
        db = self._get_db(namespace)
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
        db = self._get_db(namespace)
        with self.lmdb_env.begin(buffers=True, db=db) as txn:
            value = txn.get(key.encode())
            if value is None:
                if force:
                    return {}
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404)
            return self._decode_value(value)

    def _get_namespace(self, namespace: str) -> Dict[str, Any]:
        db = self._get_db(namespace)
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

    def _decode_value(self, bvalue: Union[bytes, memoryview]) -> DBRecord:
        fmt = bvalue[0]
        try:
            decode_func = RECORD_DECODE_FUNCS[fmt]
            return decode_func(bvalue)
        except Exception:
            val = bytes(bvalue).decode()
            raise self.server.error(
                f"Error decoding value {val}, format: {chr(fmt)}")

    async def _handle_list_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, List[str]]:
        path = web_request.get_endpoint()
        await self.eventloop.run_in_thread(self.thread_lock.acquire)
        try:
            ns_list = set(self.namespaces.keys())
            if not path.startswith("/debug/"):
                ns_list -= self.forbidden_namespaces
        finally:
            self.thread_lock.release()
        return {'namespaces': list(ns_list)}

    async def _handle_item_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        action = web_request.get_action()
        is_debug = web_request.get_endpoint().startswith("/debug/")
        namespace = web_request.get_str("namespace")
        if namespace in self.forbidden_namespaces and not is_debug:
            raise self.server.error(
                f"Read/Write access to namespace '{namespace}'"
                " is forbidden", 403)
        key: Any
        valid_types: Tuple[type, ...]
        if action != "GET":
            if namespace in self.protected_namespaces and not is_debug:
                raise self.server.error(
                    f"Write access to namespace '{namespace}'"
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

        if is_debug:
            self.debug_counter[action.lower()] += 1
            await self.insert_item(
                "moonraker", "database.debug_counter", self.debug_counter
            )
            self.server.add_log_rollover_item(
                "database_debug_counter",
                f"Database Debug Counter: {self.debug_counter}",
                log=False
            )
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
        self.eventloop = database.eventloop
        self.server = database.server
        # If parse keys is true, keys of a string type
        # will be passed straight to the DB methods.
        self.parse_keys = parse_keys

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

    def update(self, value: Mapping[str, DBRecord]) -> Awaitable[None]:
        return self.db.update_namespace(self.namespace, value)

    def sync(self, value: Mapping[str, DBRecord]) -> Awaitable[None]:
        return self.db.sync_namespace(self.namespace, value)

    def get(self,
            key: Union[List[str], str],
            default: Any = None
            ) -> Future[Any]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.get_item(self.namespace, key, default)

    def delete(self, key: Union[List[str], str]) -> Future[Any]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.delete_item(self.namespace, key)

    def insert_batch(self, records: Dict[str, Any]) -> Future[None]:
        return self.db.insert_batch(self.namespace, records)

    def move_batch(self,
                   source_keys: List[str],
                   dest_keys: List[str]
                   ) -> Future[None]:
        return self.db.move_batch(self.namespace, source_keys, dest_keys)

    def delete_batch(self, keys: List[str]) -> Future[Dict[str, Any]]:
        return self.db.delete_batch(self.namespace, keys)

    def get_batch(self, keys: List[str]) -> Future[Dict[str, Any]]:
        return self.db.get_batch(self.namespace, keys)

    def length(self) -> Future[int]:
        return self.db.ns_length(self.namespace)

    def as_dict(self) -> Dict[str, Any]:
        self._check_sync_method("as_dict")
        return self.db._get_namespace(self.namespace)

    def __getitem__(self, key: Union[List[str], str]) -> Future[Any]:
        return self.get(key, default=Sentinel.MISSING)

    def __setitem__(self,
                    key: Union[List[str], str],
                    value: DBType
                    ) -> None:
        self.insert(key, value)

    def __delitem__(self, key: Union[List[str], str]):
        self.delete(key)

    def __contains__(self, key: Union[List[str], str]) -> bool:
        self._check_sync_method("__contains__")
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key).result()

    def contains(self, key: Union[List[str], str]) -> Future[bool]:
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key)

    def keys(self) -> Future[List[str]]:
        return self.db.ns_keys(self.namespace)

    def values(self) -> Future[List[Any]]:
        return self.db.ns_values(self.namespace)

    def items(self) -> Future[List[Tuple[str, Any]]]:
        return self.db.ns_items(self.namespace)

    def pop(self,
            key: Union[List[str], str],
            default: Any = Sentinel.MISSING
            ) -> Union[Future[Any], Task[Any]]:
        if not self.server.is_running():
            try:
                val = self.delete(key).result()
            except Exception:
                if default is Sentinel.MISSING:
                    raise
                val = default
            fut = self.eventloop.create_future()
            fut.set_result(val)
            return fut

        async def _do_pop() -> Any:
            try:
                val = await self.delete(key)
            except Exception:
                if default is Sentinel.MISSING:
                    raise
                val = default
            return val
        return self.eventloop.create_task(_do_pop())

    def clear(self) -> Awaitable[None]:
        return self.db.clear_namespace(self.namespace)

    def _check_sync_method(self, func_name: str) -> None:
        if self.server.is_running():
            raise self.server.error(
                f"Cannot call method {func_name} while "
                "the eventloop is running")

def load_component(config: ConfigHelper) -> MoonrakerDatabase:
    return MoonrakerDatabase(config)
