# Sqlite database for Moonraker persistent storage
#
# Copyright (C) 2021-2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import pathlib
import struct
import operator
import inspect
import logging
import contextlib
import time
from asyncio import Future, Task, Lock
from functools import reduce
from queue import Queue
from threading import Thread
import sqlite3
from ..utils import Sentinel, ServerError
from ..utils import json_wrapper as jsonw
from ..common import RequestType, SqlTableDefinition

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    TypeVar,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
    Set,
    Type,
    Sequence,
    Generator
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .klippy_connection import KlippyConnection
    from lmdb import Environment as LmdbEnvironment
    from types import TracebackType
    DBRecord = Optional[Union[int, float, bool, str, List[Any], Dict[str, Any]]]
    DBType = DBRecord
    SqlParams = Union[List[Any], Tuple[Any, ...], Dict[str, Any]]
    _T = TypeVar("_T")

DATABASE_VERSION = 2
SQL_DB_FILENAME = "moonraker-sql.db"
NAMESPACE_TABLE = "namespace_store"
REGISTRATION_TABLE = "table_registry"
SCHEMA_TABLE = (
    "sqlite_schema" if sqlite3.sqlite_version_info >= (3, 33, 0)
    else "sqlite_master"
)

RECORD_ENCODE_FUNCS: Dict[Type, Callable[..., bytes]] = {
    int: lambda x: b"q" + struct.pack("q", x),
    float: lambda x: b"d" + struct.pack("d", x),
    bool: lambda x: b"?" + struct.pack("?", x),
    str: lambda x: b"s" + x.encode(),
    list: lambda x: jsonw.dumps(x),
    dict: lambda x: jsonw.dumps(x),
    type(None): lambda x: b"\x00",
}

RECORD_DECODE_FUNCS: Dict[int, Callable[..., DBRecord]] = {
    ord("q"): lambda x: struct.unpack("q", x[1:])[0],
    ord("d"): lambda x: struct.unpack("d", x[1:])[0],
    ord("?"): lambda x: struct.unpack("?", x[1:])[0],
    ord("s"): lambda x: bytes(x[1:]).decode(),
    ord("["): lambda x: jsonw.loads(bytes(x)),
    ord("{"): lambda x: jsonw.loads(bytes(x)),
    0: lambda _: None
}

def encode_record(value: DBRecord) -> bytes:
    try:
        enc_func = RECORD_ENCODE_FUNCS[type(value)]
        return enc_func(value)
    except Exception:
        raise ServerError(
            f"Error encoding val: {value}, type: {type(value)}"
        )

def decode_record(bvalue: bytes) -> DBRecord:
    fmt = bvalue[0]
    try:
        decode_func = RECORD_DECODE_FUNCS[fmt]
        return decode_func(bvalue)
    except Exception:
        val = bytes(bvalue).decode()
        raise ServerError(
            f"Error decoding value {val}, format: {chr(fmt)}"
        )

def getitem_with_default(item: Dict, field: Any) -> Any:
    if not isinstance(item, Dict):
        raise ServerError(
            f"Cannot reduce a value of type {type(item)}")
    if field not in item:
        item[field] = {}
    return item[field]

def parse_namespace_key(key: Union[List[str], str]) -> List[str]:
    try:
        key_list = key if isinstance(key, list) else key.split('.')
    except Exception:
        key_list = []
    if not key_list or "" in key_list:
        raise ServerError(f"Invalid Key Format: '{key}'")
    return key_list

def generate_lmdb_entries(
    db_folder: pathlib.Path
) -> Generator[Tuple[str, str, bytes], Any, None]:
    if not db_folder.joinpath("data.mdb").is_file():
        return
    MAX_LMDB_NAMESPACES = 100
    MAX_LMDB_SIZE = 200 * 2**20
    inst_attempted: bool = False
    while True:
        try:
            import lmdb
            lmdb_env: LmdbEnvironment = lmdb.open(
                str(db_folder), map_size=MAX_LMDB_SIZE, max_dbs=MAX_LMDB_NAMESPACES
            )
        except ModuleNotFoundError:
            if inst_attempted:
                logging.info(
                    "Attempt to install LMDB failed, aborting conversion."
                )
                return
            import sys
            from ..utils import pip_utils
            inst_attempted = True
            logging.info("LMDB module not found, attempting installation...")
            pip_cmd = f"{sys.executable} -m pip"
            pip_exec = pip_utils.PipExecutor(pip_cmd, logging.info)
            pip_exec.install_packages(["lmdb"])
        except Exception:
            logging.exception(
                "Failed to open lmdb database, aborting conversion"
            )
            return
        else:
            break
    lmdb_namespaces: List[Tuple[str, object]] = []
    with lmdb_env.begin(buffers=True) as txn:
        # lookup existing namespaces
        with txn.cursor() as cursor:
            remaining = cursor.first()
            while remaining:
                key = bytes(cursor.key())
                if not key:
                    continue
                db = lmdb_env.open_db(key, txn)
                lmdb_namespaces.append((key.decode(), db))
                remaining = cursor.next()
        # Copy all records
        for (ns, db) in lmdb_namespaces:
            logging.info(f"Converting LMDB namespace '{ns}'")
            with txn.cursor(db=db) as cursor:
                remaining = cursor.first()
                while remaining:
                    key_buf = cursor.key()
                    value = b""
                    try:
                        decoded_key = bytes(key_buf).decode()
                        value = bytes(cursor.value())
                    except Exception:
                        logging.info("Database Key/Value Decode Error")
                        decoded_key = ''
                    remaining = cursor.next()
                    if not decoded_key or not value:
                        hk = bytes(key_buf).hex()
                        logging.info(
                            f"Invalid key or value '{hk}' found in "
                            f"lmdb namespace '{ns}'"
                        )
                        continue
                    if ns == "moonraker":
                        if decoded_key == "database":
                            # Convert "database" field in the "moonraker" namespace
                            # to its own namespace if possible
                            db_info = decode_record(value)
                            if isinstance(db_info, dict):
                                for db_key, db_val in db_info.items():
                                    yield ("database", db_key, encode_record(db_val))
                                continue
                        elif decoded_key == "database_version":
                            yield ("database", decoded_key, value)
                            continue
                    yield (ns, decoded_key, value)
    lmdb_env.close()

class MoonrakerDatabase:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.registered_namespaces: Set[str] = set(["moonraker", "database"])
        self.registered_tables: Set[str] = set([NAMESPACE_TABLE, REGISTRATION_TABLE])
        self.backup_lock = Lock()
        instance_id: str = self.server.get_app_args()["instance_uuid"]
        db_path = self._get_database_folder(config)
        self._sql_db = db_path.joinpath(SQL_DB_FILENAME)
        self.db_provider = SqliteProvider(config, self._sql_db)
        stored_iid = self.get_item("moonraker", "instance_id", None).result()
        if stored_iid is not None:
            if instance_id != stored_iid:
                self.server.add_log_rollover_item(
                    "uuid_mismatch",
                    "Database: Stored Instance ID does not match current Instance "
                    f"ID.\n\nCurrent UUID: {instance_id}\nStored UUID: {stored_iid}"
                )
        else:
            self.insert_item("moonraker", "instance_id", instance_id)
        dbinfo: Dict[str, Any] = self.get_item("database", default={}).result()
        # Protected Namespaces have read-only API access.  Write access can
        # be granted by enabling the debug option.  Forbidden namespaces
        # have no API access.  This cannot be overridden.
        ptns: Set[str] = set(dbinfo.get("protected_namespaces", []))
        fbns: Set[str] = set(dbinfo.get("forbidden_namespaces", []))
        self.protected_namespaces: Set[str] = ptns.union(["moonraker"])
        self.forbidden_namespaces: Set[str] = fbns.union(["database"])
        # Initialize Debug Counter
        config.getboolean("enable_database_debug", False, deprecate=True)
        self.debug_counter: Dict[str, int] = {"get": 0, "post": 0, "delete": 0}
        db_counter: Optional[Dict[str, int]] = dbinfo.get("debug_counter")
        if isinstance(db_counter, dict):
            self.debug_counter.update(db_counter)
            self.server.add_log_rollover_item(
                "database_debug_counter",
                f"Database Debug Counter: {self.debug_counter}"
            )
        # Track unsafe shutdowns
        self.unsafe_shutdowns: int = dbinfo.get("unsafe_shutdowns", 0)
        msg = f"Unsafe Shutdown Count: {self.unsafe_shutdowns}"
        self.server.add_log_rollover_item("database", msg)
        self.insert_item("database", "database_version", DATABASE_VERSION)
        self.server.register_endpoint(
            "/server/database/list", RequestType.GET, self._handle_list_request
        )
        self.server.register_endpoint(
            "/server/database/item", RequestType.all(), self._handle_item_request
        )
        self.server.register_endpoint(
            "/server/database/backup", RequestType.POST | RequestType.DELETE,
            self._handle_backup_request
        )
        self.server.register_endpoint(
            "/server/database/restore", RequestType.POST, self._handle_restore_request
        )
        self.server.register_endpoint(
            "/server/database/compact", RequestType.POST, self._handle_compact_request
        )
        self.server.register_debug_endpoint(
            "/debug/database/list", RequestType.GET, self._handle_list_request
        )
        self.server.register_debug_endpoint(
            "/debug/database/item", RequestType.all(), self._handle_item_request
        )
        self.server.register_debug_endpoint(
            "/debug/database/table", RequestType.GET, self._handle_table_request
        )
        # self.server.register_debug_endpoint(
        #    "/debug/database/row", RequestType.all(),
        #    self._handle_row_request
        # )

    async def component_init(self) -> None:
        await self.db_provider.async_init()
        # Increment unsafe shutdown counter.  This will be reset if moonraker is
        # safely restarted
        await self.insert_item(
            "database", "unsafe_shutdowns", self.unsafe_shutdowns + 1
        )

    def get_database_path(self) -> str:
        return str(self._sql_db)

    @property
    def database_path(self) -> pathlib.Path:
        return self._sql_db

    def _get_database_folder(self, config: ConfigHelper) -> pathlib.Path:
        app_args = self.server.get_app_args()
        dep_path = config.get("database_path", None, deprecate=True)
        db_path = pathlib.Path(app_args["data_path"]).joinpath("database")
        if (
            app_args["is_default_data_path"] and
            not db_path.joinpath(SQL_DB_FILENAME).exists()
        ):
            # Allow configured DB fallback
            dep_path = dep_path or "~/.moonraker_database"
            legacy_db = pathlib.Path(dep_path).expanduser().resolve()
            try:
                same = legacy_db.samefile(db_path)
            except Exception:
                same = False
            if not same and legacy_db.joinpath("data.mdb").is_file():
                logging.info(
                    f"Reverting to legacy database folder: {legacy_db}"
                )
                db_path = legacy_db
        if not db_path.is_dir():
            db_path.mkdir()
        return db_path

    # *** Nested Database operations***
    # The insert_item(), delete_item(), and get_item() methods may operate on
    # nested objects within a namespace.  Each operation takes a key argument
    # that may either be a string or a list of strings.  If the argument is
    # a string nested keys may be delitmted by a "." by which the string
    # will be split into a list of strings.  The first key in the list must
    # identify the database record.  Subsequent keys are optional and are
    # used to access elements in the deserialized objects.

    def insert_item(
        self, namespace: str, key: Union[List[str], str], value: DBType
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.insert_item, namespace, key, value
        )

    def update_item(
        self, namespace: str, key: Union[List[str], str], value: DBType
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.update_item, namespace, key, value
        )

    def delete_item(
        self, namespace: str, key: Union[List[str], str]
    ) -> Future[Any]:
        return self.db_provider.execute_db_function(
            self.db_provider.delete_item, namespace, key
        )

    def get_item(
        self,
        namespace: str,
        key: Optional[Union[List[str], str]] = None,
        default: Any = Sentinel.MISSING
    ) -> Future[Any]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_item, namespace, key, default
        )

    # *** Batch operations***
    #  The insert_batch(), move_batch(), delete_batch(), and get_batch()
    #  methods can be used to perform record level batch operations on
    #  a namespace in a single transaction.

    def insert_batch(
        self, namespace: str, records: Dict[str, Any]
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.insert_batch, namespace, records
        )

    def move_batch(
        self, namespace: str, source_keys: List[str], dest_keys: List[str]
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.move_batch, namespace, source_keys, dest_keys
        )

    def delete_batch(
        self, namespace: str, keys: List[str]
    ) -> Future[Dict[str, Any]]:
        return self.db_provider.execute_db_function(
            self.db_provider.delete_batch, namespace, keys
        )

    def get_batch(
        self, namespace: str, keys: List[str]
    ) -> Future[Dict[str, Any]]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_batch, namespace, keys
        )

    # *** Namespace level operations***

    def update_namespace(
        self, namespace: str, values: Dict[str, DBRecord]
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.insert_batch, namespace, values
        )

    def clear_namespace(self, namespace: str) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.clear_namespace, namespace
        )

    def sync_namespace(
        self, namespace: str, values: Dict[str, DBRecord]
    ) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.sync_namespace, namespace, values
        )

    def ns_length(self, namespace: str) -> Future[int]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_namespace_length, namespace
        )

    def ns_keys(self, namespace: str) -> Future[List[str]]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_namespace_keys, namespace,
        )

    def ns_values(self, namespace: str) -> Future[List[Any]]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_namespace_values, namespace
        )

    def ns_items(self, namespace: str) -> Future[List[Tuple[str, Any]]]:
        return self.db_provider.execute_db_function(
            self.db_provider.get_namespace_items, namespace
        )

    def ns_contains(
        self, namespace: str, key: Union[List[str], str]
    ) -> Future[bool]:
        return self.db_provider.execute_db_function(
            self.db_provider.namespace_contains, namespace
        )

    # SQL direct query methods
    def sql_execute(
        self, sql: str, params: SqlParams = []
    ) -> Future[SqliteCursorProxy]:
        return self.db_provider.execute_db_function(
            self.db_provider.sql_execute, sql, params
        )

    def sql_executemany(
        self, sql: str, params: Sequence[SqlParams] = []
    ) -> Future[SqliteCursorProxy]:
        return self.db_provider.execute_db_function(
            self.db_provider.sql_executemany, sql, params
        )

    def sql_executescript(self, sql: str) -> Future[SqliteCursorProxy]:
        return self.db_provider.execute_db_function(
            self.db_provider.sql_executescript, sql
        )

    def sql_commit(self) -> Future[None]:
        return self.db_provider.execute_db_function(self.db_provider.sql_commit)

    def sql_rollback(self) -> Future[None]:
        return self.db_provider.execute_db_function(self.db_provider.sql_rollback)

    def queue_sql_callback(
        self, callback: Callable[[sqlite3.Connection], Any]
    ) -> Future[Any]:
        return self.db_provider.execute_db_function(callback)

    def compact_database(self) -> Future[Dict[str, int]]:
        return self.db_provider.execute_db_function(
            self.db_provider.compact_database
        )

    def backup_database(self, bkp_path: pathlib.Path) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.backup_database, bkp_path
        )

    def restore_database(self, restore_path: pathlib.Path) -> Future[Dict[str, Any]]:
        return self.db_provider.execute_db_function(
            self.db_provider.restore_database, restore_path
        )

    def register_local_namespace(
            self, namespace: str, forbidden: bool = False, parse_keys: bool = False
    ) -> NamespaceWrapper:
        if namespace in self.registered_namespaces:
            raise self.server.error(f"Namespace '{namespace}' already registered")
        self.registered_namespaces.add(namespace)
        self.db_provider.register_namespace(namespace)
        if forbidden:
            if namespace not in self.forbidden_namespaces:
                self.forbidden_namespaces.add(namespace)
                self.insert_item(
                    "database", "forbidden_namespaces",
                    sorted(self.forbidden_namespaces)
                )
        elif namespace not in self.protected_namespaces:
            self.protected_namespaces.add(namespace)
            self.insert_item(
                "database", "protected_namespaces", sorted(self.protected_namespaces)
            )
        return NamespaceWrapper(namespace, self, parse_keys)

    def wrap_namespace(
        self, namespace: str, parse_keys: bool = True
    ) -> NamespaceWrapper:
        if namespace not in self.db_provider.namespaces:
            raise self.server.error(f"Namespace '{namespace}' not found", 404)
        return NamespaceWrapper(namespace, self, parse_keys)

    def unregister_local_namespace(self, namespace: str) -> None:
        if namespace in self.registered_namespaces:
            self.registered_namespaces.remove(namespace)
        if namespace in self.forbidden_namespaces:
            self.forbidden_namespaces.remove(namespace)
            self.insert_item(
                "database", "forbidden_namespaces", sorted(self.forbidden_namespaces)
            )
        if namespace in self.protected_namespaces:
            self.protected_namespaces.remove(namespace)
            self.insert_item(
                "database", "protected_namespaces", sorted(self.protected_namespaces)
            )

    def drop_empty_namespace(self, namespace: str) -> Future[None]:
        return self.db_provider.execute_db_function(
            self.db_provider.drop_empty_namespace, namespace
        )

    def get_provider_wrapper(self) -> DBProviderWrapper:
        return self.db_provider.get_provider_wapper()

    def get_backup_dir(self) -> pathlib.Path:
        bkp_dir = pathlib.Path(self.server.get_app_arg("data_path"))
        return bkp_dir.joinpath("backup/database").resolve()

    def register_table(self, table_def: SqlTableDefinition) -> SqlTableWrapper:
        if table_def.name in self.registered_tables:
            raise self.server.error(f"Table '{table_def.name}' already registered")
        self.registered_tables.add(table_def.name)
        self.db_provider.register_table(table_def)
        return SqlTableWrapper(self, table_def)

    async def _handle_compact_request(self, web_request: WebRequest) -> Dict[str, int]:
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        if kconn.is_printing():
            raise self.server.error("Cannot compact when Klipper is printing")
        async with self.backup_lock:
            return await self.compact_database()

    async def _handle_backup_request(self, web_request: WebRequest) -> Dict[str, Any]:
        async with self.backup_lock:
            request_type = web_request.get_request_type()
            if request_type == RequestType.POST:
                kconn: KlippyConnection
                kconn = self.server.lookup_component("klippy_connection")
                if kconn.is_printing():
                    raise self.server.error("Cannot backup when Klipper is printing")
                suffix = time.strftime("%Y%m%d-%H%M%S", time.localtime())
                db_name = web_request.get_str("filename", f"sqldb-backup-{suffix}.db")
                bkp_dir = self.get_backup_dir()
                bkp_path = bkp_dir.joinpath(db_name).resolve()
                if bkp_dir not in bkp_path.parents:
                    raise self.server.error(f"Invalid name {db_name}.")
                await self.backup_database(bkp_path)
            elif request_type == RequestType.DELETE:
                db_name = web_request.get_str("filename")
                bkp_dir = self.get_backup_dir()
                bkp_path = bkp_dir.joinpath(db_name).resolve()
                if bkp_dir not in bkp_path.parents:
                    raise self.server.error(f"Invalid name {db_name}.")
                if not bkp_path.is_file():
                    raise self.server.error(
                        f"Backup file {db_name} does not exist", 404
                    )
                await self.eventloop.run_in_thread(bkp_path.unlink)
            else:
                raise self.server.error("Invalid request type")
            return {
                "backup_path": str(bkp_path)
            }

    async def _handle_restore_request(self, web_request: WebRequest) -> Dict[str, Any]:
        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        if kconn.is_printing():
            raise self.server.error("Cannot restore when Klipper is printing")
        async with self.backup_lock:
            db_name = web_request.get_str("filename")
            bkp_dir = self.get_backup_dir()
            restore_path = bkp_dir.joinpath(db_name).resolve()
            if bkp_dir not in restore_path.parents:
                raise self.server.error(f"Invalid name {db_name}.")
            restore_info = await self.restore_database(restore_path)
            self.server.restart(.1)
            return restore_info

    async def _handle_list_request(
        self, web_request: WebRequest
    ) -> Dict[str, List[str]]:
        path = web_request.get_endpoint()
        ns_list = set(self.db_provider.namespaces)
        bkp_dir = self.get_backup_dir()
        backups: List[str] = []
        if bkp_dir.is_dir():
            backups = [bkp.name for bkp in bkp_dir.iterdir() if bkp.is_file()]
        if not path.startswith("/debug/"):
            ns_list -= self.forbidden_namespaces
            return {
                "namespaces": list(ns_list),
                "backups": backups
            }
        else:
            return {
                "namespaces": list(ns_list),
                "backups": backups,
                "tables": list(self.db_provider.tables)
            }

    async def _handle_item_request(self, web_request: WebRequest) -> Dict[str, Any]:
        req_type = web_request.get_request_type()
        is_debug = web_request.get_endpoint().startswith("/debug/")
        namespace = web_request.get_str("namespace")
        if namespace in self.forbidden_namespaces and not is_debug:
            raise self.server.error(
                f"Read/Write access to namespace '{namespace}' is forbidden", 403
            )
        if req_type == RequestType.GET:
            key = web_request.get("key", None)
            if key is not None and not isinstance(key, (list, str)):
                raise self.server.error(
                    "Value for argument 'key' is an invalid type: "
                    f"{type(key).__name__}"
                )
            val = await self.get_item(namespace, key)
        else:
            if namespace in self.protected_namespaces and not is_debug:
                raise self.server.error(
                    f"Write access to namespace '{namespace}' is forbidden", 403
                )
            key = web_request.get("key")
            if not isinstance(key, (list, str)):
                raise self.server.error(
                    "Value for argument 'key' is an invalid type: "
                    f"{type(key).__name__}"
                )
            if req_type == RequestType.POST:
                val = web_request.get("value")
                await self.insert_item(namespace, key, val)
            elif req_type == RequestType.DELETE:
                val = await self.delete_item(namespace, key)
                await self.drop_empty_namespace(namespace)
            else:
                raise self.server.error(f"Invalid request type {req_type}")

        if is_debug:
            name = req_type.name or str(req_type).split(".", 1)[-1]
            self.debug_counter[name.lower()] += 1
            await self.insert_item(
                "database", "debug_counter", self.debug_counter
            )
            self.server.add_log_rollover_item(
                "database_debug_counter",
                f"Database Debug Counter: {self.debug_counter}",
                log=False
            )
        return {'namespace': namespace, 'key': key, 'value': val}

    async def close(self) -> None:
        if not self.db_provider.is_restored():
            # Don't overwrite unsafe shutdowns on a restored database
            await self.insert_item(
                "database", "unsafe_shutdowns", self.unsafe_shutdowns
            )
        # Stop command thread
        await self.db_provider.stop()

    async def _handle_table_request(self, web_request: WebRequest) -> Dict[str, Any]:
        table = web_request.get_str("table")
        if table not in self.db_provider.tables:
            raise self.server.error(f"Table name '{table}' does not exist", 404)
        cur = await self.sql_execute(f"SELECT rowid, * FROM {table}")
        return {
            "table_name": table,
            "rows": [dict(r) for r in await cur.fetchall()]
        }

    async def _handle_row_request(self, web_request: WebRequest) -> Dict[str, Any]:
        req_type = web_request.get_request_type()
        table = web_request.get_str("table")
        if table not in self.db_provider.tables:
            raise self.server.error(
                f"Table name '{table}' does not exist", 404
            )
        if req_type == RequestType.POST:
            row_id = web_request.get_int("id", None)
            values = web_request.get("values")
            assert isinstance(values, dict)
            keys = set(values.keys())
            cur = await self.sql_execute(f"PRAGMA table_info('{table}')")
            columns = set([r["name"] for r in await cur.fetchall()])
            if row_id is None:
                # insert
                if keys != columns:
                    raise self.server.error(
                        "Keys in value to insert do not match columns of tables"
                    )
                val_str = ",".join([f":{col}" for col in columns])
                cur = await self.sql_execute(
                    f"INSERT INTO {table} VALUES({val_str})", values
                )
            else:
                # update
                if not keys.issubset(columns):
                    raise self.server.error(
                        "Keys in value to update are not a subset of available columns"
                    )
                col_str = ",".join([f"{col}" for col in columns if col in keys])
                vals = [values[col] for col in columns if col in keys]
                vals.append(row_id)
                val_str = ",".join("?" * len(vals))
                cur = await self.sql_execute(
                    f"UPDATE {table} SET ({col_str}) = ({val_str}) WHERE rowid = ?",
                    vals
                )
                if not cur.rowcount:
                    raise self.server.error(f"No row with id {row_id} to update")
        else:
            row_id = web_request.get_int("id")
        cur = await self.sql_execute(
            f"SELECT rowid, * FROM {table} WHERE rowid = ?", (row_id,)
        )
        item = dict(await cur.fetchone() or {})
        if req_type == RequestType.DELETE:
            await self.sql_execute(
                f"DELETE FROM {table} WHERE rowid = ?", (row_id,)
            )
        return {
            "row": item
        }

class SqliteProvider(Thread):
    def __init__(self, config: ConfigHelper, db_path: pathlib.Path) -> None:
        super().__init__()
        self.server = config.get_server()
        self.asyncio_loop = self.server.get_event_loop().asyncio_loop
        self._namespaces: Set[str] = set()
        self._tables: Set[str] = set()
        self._db_path = db_path
        self.restored: bool = False
        self.command_queue: Queue[Tuple[Future, Optional[Callable], Tuple[Any, ...]]]
        self.command_queue = Queue()
        sqlite3.register_converter("record", decode_record)
        sqlite3.register_converter("pyjson", jsonw.loads)
        sqlite3.register_converter("pybool", lambda x: bool(x))
        sqlite3.register_adapter(list, jsonw.dumps)
        sqlite3.register_adapter(dict, jsonw.dumps)
        self.sync_conn = sqlite3.connect(
            str(db_path), timeout=1., detect_types=sqlite3.PARSE_DECLTYPES
        )
        self.sync_conn.row_factory = sqlite3.Row
        self.setup_database()

    @property
    def namespaces(self) -> Set[str]:
        return self._namespaces

    @property
    def tables(self) -> Set[str]:
        return self._tables

    def async_init(self) -> Future[str]:
        self.sync_conn.close()
        self.start()
        fut = self.asyncio_loop.create_future()
        self.command_queue.put_nowait((fut, lambda x: "sqlite", tuple()))
        return fut

    def run(self) -> None:
        loop = self.asyncio_loop
        conn = sqlite3.connect(
            str(self._db_path), timeout=1., detect_types=sqlite3.PARSE_DECLTYPES
        )
        conn.row_factory = sqlite3.Row
        while True:
            future, func, args = self.command_queue.get()
            if func is None:
                break
            try:
                ret = func(conn, *args)
            except Exception as e:
                loop.call_soon_threadsafe(future.set_exception, e)
            else:
                loop.call_soon_threadsafe(future.set_result, ret)
        conn.close()
        loop.call_soon_threadsafe(future.set_result, None)

    def execute_db_function(
        self, command_func: Callable[..., _T], *args
    ) -> Future[_T]:
        fut = self.asyncio_loop.create_future()
        if self.is_alive():
            self.command_queue.put_nowait((fut, command_func, args))
        else:
            ret = command_func(self.sync_conn, *args)
            fut.set_result(ret)
        return fut

    def setup_database(self) -> None:
        self.server.add_log_rollover_item(
            "sqlite_intro",
            "Loading Sqlite database provider. "
            f"Sqlite Version: {sqlite3.sqlite_version}"
        )
        cur = self.sync_conn.execute(
            f"SELECT name FROM {SCHEMA_TABLE} WHERE type='table'"
        )
        cur.arraysize = 100
        self._tables = set([row[0] for row in cur.fetchall()])
        logging.debug(f"Detected SQL Tables: {self._tables}")
        if NAMESPACE_TABLE not in self._tables:
            self._create_default_tables()
            self._migrate_from_lmdb()
        elif REGISTRATION_TABLE not in self._tables:
            self._create_registration_table()
        # Find namespaces
        cur = self.sync_conn.execute(
            f"SELECT DISTINCT namespace FROM {NAMESPACE_TABLE}"
        )
        cur.arraysize = 100
        self._namespaces = set([row[0] for row in cur.fetchall()])
        logging.debug(f"Detected namespaces: {self._namespaces}")

    def _migrate_from_lmdb(self) -> None:
        db_folder = self._db_path.parent
        if not db_folder.joinpath("data.mdb").is_file():
            return
        logging.info("Converting LMDB Database to Sqlite...")
        with self.sync_conn:
            self.sync_conn.executemany(
                f"INSERT INTO {NAMESPACE_TABLE} VALUES (?,?,?)",
                generate_lmdb_entries(db_folder)
            )

    def _create_default_tables(self) -> None:
        self._create_registration_table()
        if NAMESPACE_TABLE in self._tables:
            return
        namespace_proto = inspect.cleandoc(
            f"""
            {NAMESPACE_TABLE} (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value record NOT NULL,
                PRIMARY KEY (namespace, key)
            )
            """
        )
        with self.sync_conn:
            self.sync_conn.execute(f"CREATE TABLE {namespace_proto}")
        self._save_registered_table(NAMESPACE_TABLE, namespace_proto, 1)
        self.server.add_log_rollover_item(
            "db_default_table", f"Created default SQL table {NAMESPACE_TABLE}"
        )

    def _create_registration_table(self) -> None:
        if REGISTRATION_TABLE in self._tables:
            return
        reg_tbl_proto = inspect.cleandoc(
            f"""
            {REGISTRATION_TABLE} (
                name TEXT NOT NULL PRIMARY KEY,
                prototype TEXT NOT NULL,
                version INT
            )
            """
        )
        with self.sync_conn:
            self.sync_conn.execute(f"CREATE TABLE {reg_tbl_proto}")
        self._tables.add(REGISTRATION_TABLE)

    def _save_registered_table(
        self, table_name: str, prototype: str, version: int
    ) -> None:
        with self.sync_conn:
            self.sync_conn.execute(
                f"INSERT INTO {REGISTRATION_TABLE} VALUES(?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "prototype=excluded.prototype, version=excluded.version",
                (table_name, prototype, version)
            )
        self._tables.add(table_name)

    def _lookup_registered_table(self, table_name: str) -> Tuple[str, int]:
        cur = self.sync_conn.execute(
            f"SELECT prototype, version FROM {REGISTRATION_TABLE} "
            f"WHERE name = ?",
            (table_name,)
        )
        ret = cur.fetchall()
        if not ret:
            return "", 0
        return tuple(ret[0])  # type: ignore

    def _insert_record(
        self, conn: sqlite3.Connection, namespace: str, key: str, val: DBType
    ) -> bool:
        if val is None:
            return False
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO {NAMESPACE_TABLE} VALUES(?, ?, ?) "
                    "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                    (namespace, key, encode_record(val))
                )
        except sqlite3.Error:
            if self.server.is_verbose_enabled():
                logging.error("Error inserting record for key")
            return False
        return True

    def _get_record(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        key: str,
        default: Union[Sentinel, DBRecord] = Sentinel.MISSING
    ) -> DBRecord:
        cur = conn.execute(
            f"SELECT value FROM {NAMESPACE_TABLE} WHERE namespace = ? and key = ?",
            (namespace, key)
        )
        val = cur.fetchone()
        if val is None:
            if default is Sentinel.MISSING:
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404
                )
            return default
        return val[0]

    # Namespace Query Ops

    def get_namespace(
        self, conn: sqlite3.Connection, namespace: str, must_exist: bool = True
    ) -> Dict[str, Any]:
        if namespace not in self._namespaces:
            if not must_exist:
                return {}
            raise self.server.error(f"Namespace {namespace} not found", 404)
        cur = conn.execute(
            f"SELECT key, value FROM {NAMESPACE_TABLE} WHERE namespace = ?",
            (namespace,)
        )
        cur.arraysize = 200
        return dict(cur.fetchall())

    def iter_namespace(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        count: int = 1000
    ) -> Generator[Dict[str, Any], Any, None]:
        if self.is_alive():
            raise self.server.error("Cannot iterate a namespace asynchronously")
        if namespace not in self._namespaces:
            return
        offset: int = 0
        total = self.get_namespace_length(conn, namespace)
        while offset < total:
            cur = conn.execute(
                f"SELECT key, value FROM {NAMESPACE_TABLE} WHERE namespace = ? "
                f"LIMIT ? OFFSET ?",
                (namespace, count, offset)
            )
            cur.arraysize = count
            ret = cur.fetchall()
            if not ret:
                return
            yield dict(ret)
            offset += count

    def clear_namespace(self, conn: sqlite3.Connection, namespace: str) -> None:
        with conn:
            conn.execute(
                f"DELETE FROM {NAMESPACE_TABLE} WHERE namespace = ?", (namespace,)
            )

    def drop_empty_namespace(self, conn: sqlite3.Connection, namespace: str) -> None:
        if namespace in self._namespaces:
            if self.get_namespace_length(conn, namespace) == 0:
                self._namespaces.remove(namespace)

    def sync_namespace(
        self, conn: sqlite3.Connection, namespace: str, values: Dict[str, DBRecord]
    ) -> None:
        def generate_params():
            for key, val in values.items():
                yield (namespace, key, val)
        with conn:
            conn.execute(
                f"DELETE FROM {NAMESPACE_TABLE} WHERE namespace = ?", (namespace,)
            )
            conn.executemany(
                f"INSERT INTO {NAMESPACE_TABLE} VALUES(?, ?, ?)", generate_params()
            )

    def get_namespace_length(self, conn: sqlite3.Connection, namespace: str) -> int:
        cur = conn.execute(
            f"SELECT COUNT(namespace) FROM {NAMESPACE_TABLE} WHERE namespace = ?",
            (namespace,)
        )
        return cur.fetchone()[0]

    def get_namespace_keys(self, conn: sqlite3.Connection, namespace: str) -> List[str]:
        cur = conn.execute(
            f"SELECT key FROM {NAMESPACE_TABLE} WHERE namespace = ?",
            (namespace,)
        )
        cur.arraysize = 200
        return [row[0] for row in cur.fetchall()]

    def get_namespace_values(
        self, conn: sqlite3.Connection, namespace: str
    ) -> List[Any]:
        cur = conn.execute(
            f"SELECT value FROM {NAMESPACE_TABLE} WHERE namespace = ?",
            (namespace,)
        )
        cur.arraysize = 200
        return [row[0] for row in cur.fetchall()]

    def get_namespace_items(
        self, conn: sqlite3.Connection, namespace: str
    ) -> List[Tuple[str, Any]]:
        cur = conn.execute(
            f"SELECT key, value FROM {NAMESPACE_TABLE} WHERE namespace = ?",
            (namespace,)
        )
        cur.arraysize = 200
        return cur.fetchall()

    def namespace_contains(
        self, conn: sqlite3.Connection, namespace: str, key: Union[List[str], str]
    ) -> bool:
        try:
            key_list = parse_namespace_key(key)
            if len(key_list) == 1:
                cur = conn.execute(
                    f"SELECT key FROM {NAMESPACE_TABLE} "
                    "WHERE namespace = ? and key = ?",
                    (namespace, key)
                )
                return cur.fetchone() is not None
            record = self._get_record(conn, namespace, key_list[0])
            reduce(operator.getitem, key_list[1:], record)  # type: ignore
        except Exception:
            return False
        return True

    def insert_item(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        key: Union[List[str], str],
        value: DBType
    ) -> None:
        key_list = parse_namespace_key(key)
        record = value
        if len(key_list) > 1:
            record = self._get_record(conn, namespace, key_list[0], default={})
            if not isinstance(record, dict):
                prev_type = type(record)
                record = {}
                logging.info(
                    f"Warning: Key {key_list[0]} contains a value of type "
                    f"{prev_type}. Overwriting with an object."
                )
            item: DBType = reduce(getitem_with_default, key_list[1:-1], record)
            if not isinstance(item, dict):
                rpt_key = ".".join(key_list[:-1])
                raise self.server.error(
                    f"Item at key '{rpt_key}' in namespace '{namespace}'is "
                    "not a dictionary object, cannot insert"
                )
            item[key_list[-1]] = value
        if not self._insert_record(conn, namespace, key_list[0], record):
            logging.info(f"Error inserting key '{key}' in namespace '{namespace}'")
        else:
            self._namespaces.add(namespace)

    def update_item(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        key: Union[List[str], str],
        value: DBType
    ) -> None:
        key_list = parse_namespace_key(key)
        record = self._get_record(conn, namespace, key_list[0])
        if len(key_list) == 1:
            if isinstance(record, dict) and isinstance(value, dict):
                record.update(value)
            else:
                record = value
        else:
            try:
                assert isinstance(record, dict)
                item: Dict[str, Any] = reduce(
                    operator.getitem, key_list[1:-1], record
                )
            except Exception:
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404
                )
            if not isinstance(item, dict) or key_list[-1] not in item:
                rpt_key = ".".join(key_list[:-1])
                raise self.server.error(
                    f"Item at key '{rpt_key}' in namespace '{namespace}'is "
                    "not a dictionary object, cannot update"
                )
            if isinstance(item[key_list[-1]], dict) and isinstance(value, dict):
                item[key_list[-1]].update(value)
            else:
                item[key_list[-1]] = value
        if not self._insert_record(conn, namespace, key_list[0], record):
            logging.info(f"Error updating key '{key}' in namespace '{namespace}'")

    def delete_item(
        self, conn: sqlite3.Connection, namespace: str, key: Union[List[str], str]
    ) -> Any:
        key_list = parse_namespace_key(key)
        val = record = self._get_record(conn, namespace, key_list[0])
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
            with conn:
                conn.execute(
                    f"DELETE FROM {NAMESPACE_TABLE} WHERE namespace = ? and key = ?",
                    (namespace, key_list[0])
                )
        else:
            ret = self._insert_record(conn, namespace, key_list[0], record)
            if not ret:
                logging.info(
                    f"Error deleting key '{key}' from namespace '{namespace}'"
                )
        return val

    def get_item(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        key: Optional[Union[List[str], str]] = None,
        default: Any = Sentinel.MISSING
    ) -> Any:
        try:
            if key is None:
                return self.get_namespace(conn, namespace)
            key_list = parse_namespace_key(key)
            rec = self._get_record(conn, namespace, key_list[0])
            val = reduce(operator.getitem, key_list[1:], rec)  # type: ignore
        except Exception as e:
            if default is not Sentinel.MISSING:
                return default
            if isinstance(e, self.server.error):
                raise
            raise self.server.error(
                f"Key '{key}' in namespace '{namespace}' not found", 404
            )
        return val

    def insert_batch(
        self, conn: sqlite3.Connection, namespace: str, records: Dict[str, Any]
    ) -> None:
        def generate_params():
            for key, val in records.items():
                yield (namespace, key, encode_record(val))
        with conn:
            conn.executemany(
                f"INSERT INTO {NAMESPACE_TABLE} VALUES(?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET value=excluded.value",
                generate_params()
            )
        self._namespaces.add(namespace)

    def move_batch(
        self,
        conn: sqlite3.Connection,
        namespace: str,
        source_keys: List[str],
        dest_keys: List[str]
    ) -> None:
        def generate_params():
            for src, dest in zip(source_keys, dest_keys):
                yield (dest, namespace, src)
        with conn:
            conn.executemany(
                f"UPDATE OR REPLACE {NAMESPACE_TABLE} SET key = ? "
                "WHERE namespace = ? and key = ?",
                generate_params()
            )

    def delete_batch(
        self, conn: sqlite3.Connection, namespace: str, keys: List[str]
    ) -> Dict[str, Any]:
        def generate_params():
            for key in keys:
                yield (namespace, key)
        if sqlite3.sqlite_version_info < (3, 35):
            vals = self.get_batch(conn, namespace, keys)
            with conn:
                conn.executemany(
                    f"DELETE FROM {NAMESPACE_TABLE} WHERE namespace = ? and key = ?",
                    generate_params()
                )
            return vals
        else:
            placeholders = ",".join("?" * len(keys))
            sql = (
                f"DELETE FROM {NAMESPACE_TABLE} "
                f"WHERE namespace = ? and key IN ({placeholders}) "
                "RETURNING key, value"
            )
            params = [namespace] + keys
            with conn:
                cur = conn.execute(sql, params)
                cur.arraysize = 200
                return dict(cur.fetchall())

    def get_batch(
        self, conn: sqlite3.Connection, namespace: str, keys: List[str]
    ) -> Dict[str, Any]:
        placeholders = ",".join("?" * len(keys))
        sql = (
            f"SELECT key, value FROM {NAMESPACE_TABLE} "
            f"WHERE namespace = ? and key IN ({placeholders})"
        )
        ph_vals = [namespace] + keys
        cur = conn.execute(sql, ph_vals)
        cur.arraysize = 200
        return dict(cur.fetchall())

    # SQL Direct Manipulation
    def sql_execute(
        self,
        conn: sqlite3.Connection,
        statement: str,
        params: SqlParams
    ) -> SqliteCursorProxy:
        cur = conn.execute(statement, params)
        cur.arraysize = 100
        return SqliteCursorProxy(self, cur)

    def sql_executemany(
        self,
        conn: sqlite3.Connection,
        statement: str,
        params: Sequence[SqlParams]
    ) -> SqliteCursorProxy:
        cur = conn.executemany(statement, params)
        cur.arraysize = 100
        return SqliteCursorProxy(self, cur)

    def sql_executescript(
        self,
        conn: sqlite3.Connection,
        script: str
    ) -> SqliteCursorProxy:
        cur = conn.executescript(script)
        cur.arraysize = 100
        return SqliteCursorProxy(self, cur)

    def sql_commit(self, conn: sqlite3.Connection) -> None:
        conn.commit()

    def sql_rollback(self, conn: sqlite3.Connection) -> None:
        conn.rollback()

    def register_namespace(self, namespace: str) -> None:
        self._namespaces.add(namespace)

    def register_table(self, table_def: SqlTableDefinition) -> None:
        if self.is_alive():
            raise self.server.error(
                "Table registration must occur during during init."
            )
        if table_def.name in self._tables:
            logging.info(f"Found registered table {table_def.name}")
            if table_def.name in (NAMESPACE_TABLE, REGISTRATION_TABLE):
                raise self.server.error(
                    f"Cannot register table '{table_def.name}', it is reserved"
                )
            detected_proto, version = self._lookup_registered_table(table_def.name)
        else:
            logging.info(f"Creating table {table_def.name}...")
            with self.sync_conn:
                self.sync_conn.execute(f"CREATE TABLE {table_def.prototype}")
            detected_proto = table_def.prototype
            version = 0
        if table_def.version > version:
            table_def.migrate(version, self.get_provider_wapper())
            self._save_registered_table(
                table_def.name, table_def.prototype, table_def.version
            )
        elif detected_proto != table_def.prototype:
            self.server.add_warning(
                f"Table '{table_def.name}' defintion does not match stored "
                "definition.  See the log for details."
            )
            logging.info(
                f"Expected table prototype:\n{table_def.prototype}\n\n"
                f"Stored table prototype:\n{detected_proto}"
            )

    def compact_database(self, conn: sqlite3.Connection) -> Dict[str, int]:
        if self.restored:
            raise self.server.error(
                "Cannot compact restored database, awaiting restart"
            )
        cur_size = self._db_path.stat().st_size
        conn.execute("VACUUM")
        new_size = self._db_path.stat().st_size
        return {
            "previous_size": cur_size,
            "new_size": new_size
        }

    def backup_database(
        self, conn: sqlite3.Connection, bkp_path: pathlib.Path
    ) -> None:
        if self.restored:
            raise self.server.error(
                "Cannot backup restored database, awaiting restart"
            )
        parent = bkp_path.parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        elif bkp_path.exists():
            bkp_path.unlink()
        bkp_conn = sqlite3.connect(str(bkp_path))
        conn.backup(bkp_conn)
        bkp_conn.close()

    def restore_database(
        self, conn: sqlite3.Connection, restore_path: pathlib.Path
    ) -> Dict[str, Any]:
        if self.restored:
            raise self.server.error("Database already restored")
        if not restore_path.is_file():
            raise self.server.error(f"Restoration File {restore_path} does not exist")
        restore_conn = sqlite3.connect(str(restore_path))
        restore_info = self._validate_restore_db(restore_conn)
        restore_conn.backup(conn)
        restore_conn.close()
        self.restored = True
        return restore_info

    def _validate_restore_db(
        self, restore_conn: sqlite3.Connection
    ) -> Dict[str, Any]:
        cursor = restore_conn.execute(
            f"SELECT name FROM {SCHEMA_TABLE} WHERE type = 'table'"
        )
        cursor.arraysize = 100
        tables = [row[0] for row in cursor.fetchall()]
        if NAMESPACE_TABLE not in tables:
            restore_conn.close()
            raise self.server.error(
                f"Invalid database for restoration, missing table '{NAMESPACE_TABLE}'"
            )
        missing_tables = self._tables.difference(tables)
        if missing_tables:
            logging.info(f"Database to restore missing tables: {missing_tables}")
        cursor = restore_conn.execute(
            f"SELECT DISTINCT namespace FROM {NAMESPACE_TABLE}"
        )
        cursor.arraysize = 100
        namespaces = [row[0] for row in cursor.fetchall()]
        missing_ns = self._namespaces.difference(namespaces)
        if missing_ns:
            logging.info(f"Database to restore missing namespaces: {missing_ns}")
        return {
            "restored_tables": tables,
            "restored_namespaces": namespaces
        }

    def get_provider_wapper(self) -> DBProviderWrapper:
        return DBProviderWrapper(self)

    def is_restored(self) -> bool:
        return self.restored

    def stop(self) -> Future[None]:
        fut = self.asyncio_loop.create_future()
        if not self.is_alive():
            fut.set_result(None)
        else:
            self.command_queue.put_nowait((fut, None, tuple()))
        return fut

class DBProviderWrapper:
    def __init__(self, provider: SqliteProvider) -> None:
        self.server = provider.server
        self.provider = provider
        self._sql_conn = provider.sync_conn

    @property
    def connection(self) -> sqlite3.Connection:
        return self._sql_conn

    def iter_namespace(
        self, namespace: str, batch_count: int = 100
    ) -> Generator[Dict[str, Any], Any, None]:
        yield from self.provider.iter_namespace(self._sql_conn, namespace, batch_count)

    def get_namespace_keys(self, namespace: str) -> List[str]:
        return self.provider.get_namespace_keys(self._sql_conn, namespace)

    def get_namespace_values(self, namespace: str) -> List[Any]:
        return self.provider.get_namespace_values(self._sql_conn, namespace)

    def get_namespace_items(self, namespace: str) -> List[Tuple[str, Any]]:
        return self.provider.get_namespace_items(self._sql_conn, namespace)

    def get_namespace_length(self, namespace: str) -> int:
        return self.provider.get_namespace_length(self._sql_conn, namespace)

    def get_namespace(self, namespace: str) -> Dict[str, Any]:
        return self.provider.get_namespace(self._sql_conn, namespace, must_exist=False)

    def clear_namespace(self, namespace: str) -> None:
        self.provider.clear_namespace(self._sql_conn, namespace)

    def get_item(
        self,
        namespace: str,
        key: Union[str, List[str]],
        default: Any = Sentinel.MISSING
    ) -> Any:
        return self.provider.get_item(self._sql_conn, namespace, key, default)

    def delete_item(self, namespace: str, key: Union[str, List[str]]) -> Any:
        return self.provider.delete_item(self._sql_conn, namespace, key)

    def insert_item(
        self, namespace: str, key: Union[str, List[str]], value: DBType
    ) -> None:
        self.provider.insert_item(self._sql_conn, namespace, key, value)

    def update_item(
        self, namespace: str, key: Union[str, List[str]], value: DBType
    ) -> None:
        self.provider.update_item(self._sql_conn, namespace, key, value)

    def get_batch(self, namespace: str, keys: List[str]) -> Dict[str, Any]:
        return self.provider.get_batch(self._sql_conn, namespace, keys)

    def delete_batch(self, namespace: str, keys: List[str]) -> Dict[str, Any]:
        return self.provider.delete_batch(self._sql_conn, namespace, keys)

    def insert_batch(self, namespace: str, records: Dict[str, Any]) -> None:
        self.provider.insert_batch(self._sql_conn, namespace, records)

    def move_batch(
        self, namespace: str, source_keys: List[str], dest_keys: List[str]
    ) -> None:
        self.provider.move_batch(self._sql_conn, namespace, source_keys, dest_keys)

    def wipe_local_namespace(self, namespace: str) -> None:
        """
        Unregister persistent local namespace
        """
        self.provider.clear_namespace(self._sql_conn, namespace)
        self.provider.drop_empty_namespace(self._sql_conn, namespace)
        db: MoonrakerDatabase = self.server.lookup_component("database")
        db.unregister_local_namespace(namespace)


class SqliteCursorProxy:
    def __init__(self, provider: SqliteProvider, cursor: sqlite3.Cursor) -> None:
        self._db_provider = provider
        self._cursor = cursor
        self._description = cursor.description
        self._rowcount = cursor.rowcount
        self._lastrowid = cursor.lastrowid
        self._array_size = cursor.arraysize

    @property
    def rowcount(self) -> int:
        return self._rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return self._lastrowid

    @property
    def description(self):
        return self._description

    @property
    def arraysize(self) -> int:
        return self._array_size

    def set_arraysize(self, size: int) -> Future[None]:
        def wrapper(_) -> None:
            self._cursor.arraysize = size
            self._array_size = size
        return self._db_provider.execute_db_function(wrapper)

    def fetchone(self) -> Future[Optional[sqlite3.Row]]:
        def fetch_wrapper(_) -> Optional[sqlite3.Row]:
            return self._cursor.fetchone()
        return self._db_provider.execute_db_function(fetch_wrapper)

    def fetchmany(self, size: Optional[int] = None) -> Future[List[sqlite3.Row]]:
        def fetch_wrapper(_) -> List[sqlite3.Row]:
            if size is None:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)
        return self._db_provider.execute_db_function(fetch_wrapper)

    def fetchall(self) -> Future[List[sqlite3.Row]]:
        def fetch_wrapper(_) -> List[sqlite3.Row]:
            return self._cursor.fetchall()
        return self._db_provider.execute_db_function(fetch_wrapper)

class SqlTableWrapper(contextlib.AbstractAsyncContextManager):
    def __init__(
        self,
        database: MoonrakerDatabase,
        table_def: SqlTableDefinition
    ) -> None:
        self._database = database
        self._table_def = table_def
        self._db_provider = database.db_provider

    @property
    def version(self) -> int:
        return self._table_def.version

    async def __aenter__(self) -> SqlTableWrapper:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if exc_value is not None:
            await self.rollback()
        else:
            await self.commit()

    def get_provider_wrapper(self) -> DBProviderWrapper:
        return self._database.get_provider_wrapper()

    def queue_callback(
        self, callback: Callable[[sqlite3.Connection], Any]
    ) -> Future[Any]:
        return self._db_provider.execute_db_function(callback)

    def execute(
        self, sql: str, params: SqlParams = []
    ) -> Future[SqliteCursorProxy]:
        return self._db_provider.execute_db_function(
            self._db_provider.sql_execute, sql, params
        )

    def executemany(
        self, sql: str, params: Sequence[SqlParams] = []
    ) -> Future[SqliteCursorProxy]:
        return self._db_provider.execute_db_function(
            self._db_provider.sql_executemany, sql, params
        )

    def executescript(self, sql: str) -> Future[SqliteCursorProxy]:
        return self._db_provider.execute_db_function(
            self._db_provider.sql_executescript, sql
        )

    def commit(self) -> Future[None]:
        return self._db_provider.execute_db_function(
            self._db_provider.sql_commit
        )

    def rollback(self) -> Future[None]:
        return self._db_provider.execute_db_function(
            self._db_provider.sql_rollback
        )


class NamespaceWrapper:
    def __init__(
        self,
        namespace: str,
        database: MoonrakerDatabase,
        parse_keys: bool = False
    ) -> None:
        self.namespace = namespace
        self.db = database
        self.eventloop = database.eventloop
        self.server = database.server
        # If parse keys is true, keys of a string type
        # will be passed straight to the DB methods.
        self._parse_keys = parse_keys

    @property
    def parse_keys(self) -> bool:
        return self._parse_keys

    @parse_keys.setter
    def parse_keys(self, val: bool) -> None:
        self._parse_keys = val

    def get_provider_wrapper(self) -> DBProviderWrapper:
        return self.db.get_provider_wrapper()

    def insert(
        self, key: Union[List[str], str], value: DBType
    ) -> Future[None]:
        if isinstance(key, str) and not self._parse_keys:
            key = [key]
        return self.db.insert_item(self.namespace, key, value)

    def update_child(
        self, key: Union[List[str], str], value: DBType
    ) -> Future[None]:
        if isinstance(key, str) and not self._parse_keys:
            key = [key]
        return self.db.update_item(self.namespace, key, value)

    def update(self, value: Dict[str, DBRecord]) -> Future[None]:
        return self.db.update_namespace(self.namespace, value)

    def sync(self, value: Dict[str, DBRecord]) -> Future[None]:
        return self.db.sync_namespace(self.namespace, value)

    def get(self, key: Union[List[str], str], default: Any = None) -> Future[Any]:
        if isinstance(key, str) and not self._parse_keys:
            key = [key]
        return self.db.get_item(self.namespace, key, default)

    def delete(self, key: Union[List[str], str]) -> Future[Any]:
        if isinstance(key, str) and not self._parse_keys:
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
        return self.db.get_item(self.namespace).result()

    def __getitem__(self, key: Union[List[str], str]) -> Future[Any]:
        return self.get(key, default=Sentinel.MISSING)

    def __setitem__(self, key: Union[List[str], str], value: DBType) -> None:
        self.insert(key, value)

    def __delitem__(self, key: Union[List[str], str]):
        self.delete(key)

    def __contains__(self, key: Union[List[str], str]) -> bool:
        self._check_sync_method("__contains__")
        if isinstance(key, str) and not self._parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key).result()

    def contains(self, key: Union[List[str], str]) -> Future[bool]:
        if isinstance(key, str) and not self._parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key)

    def keys(self) -> Future[List[str]]:
        return self.db.ns_keys(self.namespace)

    def values(self) -> Future[List[Any]]:
        return self.db.ns_values(self.namespace)

    def items(self) -> Future[List[Tuple[str, Any]]]:
        return self.db.ns_items(self.namespace)

    def pop(
        self, key: Union[List[str], str], default: Any = Sentinel.MISSING
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

    def clear(self) -> Future[None]:
        return self.db.clear_namespace(self.namespace)

    def _check_sync_method(self, func_name: str) -> None:
        if self.db.db_provider.is_alive():
            raise self.server.error(
                f"Cannot call method {func_name} while "
                "the eventloop is running"
            )

def load_component(config: ConfigHelper) -> MoonrakerDatabase:
    return MoonrakerDatabase(config)
