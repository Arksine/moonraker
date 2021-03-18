# Mimimal database for moonraker storage
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import json
import struct
import operator
import logging
from io import BytesIO
from functools import reduce
import lmdb

DATABASE_VERSION = 1
MAX_NAMESPACES = 50
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

def getitem_with_default(item, field):
    if field not in item:
        item[field] = {}
    return item[field]

class Sentinel:
    pass

class MoonrakerDatabase:
    def __init__(self, config):
        self.server = config.get_server()
        self.namespaces = {}
        self.protected_namespaces = {"moonraker"}
        self.database_path = os.path.expanduser(config.get(
            'database_path', "~/.moonraker_database"))
        if not os.path.isdir(self.database_path):
            os.mkdir(self.database_path)
        self.lmdb_env = lmdb.open(self.database_path, map_size=MAX_DB_SIZE,
                                  max_dbs=MAX_NAMESPACES)
        with self.lmdb_env.begin(write=True, buffers=True) as txn:
            # lookup existing namespaces
            cursor = txn.cursor()
            remaining = cursor.first()
            while remaining:
                key = bytes(cursor.key())
                self.namespaces[key.decode()] = self.lmdb_env.open_db(key, txn)
                remaining = cursor.next()
            cursor.close()
            if "moonraker" not in self.namespaces:
                mrdb = self.lmdb_env.open_db(b"moonraker", txn)
                self.namespaces["moonraker"] = mrdb
                txn.put(b'database_version',
                        self._encode_value(DATABASE_VERSION),
                        db=mrdb)

        self.server.register_endpoint(
            "/server/database/list", ['GET'], self._handle_list_request)
        self.server.register_endpoint(
            "/server/database/item", ["GET", "POST", "DELETE"],
            self._handle_item_request)

    def insert_item(self, namespace, key, value):
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
                    f"Warning: Key {key_list[0]} contains a value of type "
                    f"{type(record)}. Overwriting with an object.")
            item = reduce(getitem_with_default, key_list[1:-1], record)
            item[key_list[-1]] = value
        if not self._insert_record(namespace, key_list[0], record):
            logging.info(
                f"Error inserting key '{key}' in namespace '{namespace}'")

    def update_item(self, namespace, key, value):
        key_list = self._process_key(key)
        record = self._get_record(namespace, key_list[0])
        if len(key_list) == 1:
            if isinstance(record, dict) and isinstance(value, dict):
                record.update(value)
            else:
                record = value
        else:
            try:
                item = reduce(operator.getitem, key_list[1:-1], record)
            except Exception:
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404)
            if isinstance(item[key_list[-1]], dict) \
                    and isinstance(value, dict):
                item[key_list[-1]].update(value)
            else:
                item[key_list[-1]] = value
        if not self._insert_record(namespace, key_list[0], record):
            logging.info(
                f"Error updating key '{key}' in namespace '{namespace}'")

    def delete_item(self, namespace, key, drop_empty_db=False):
        key_list = self._process_key(key)
        val = record = self._get_record(namespace, key_list[0])
        remove_record = True
        if len(key_list) > 1:
            try:
                item = reduce(operator.getitem, key_list[1:-1], record)
                val = item.pop(key_list[-1])
            except Exception:
                raise self.server.error(
                    f"Key '{key}' in namespace '{namespace}' not found", 404)
            remove_record = False if record else True
        if remove_record:
            db = self.namespaces[namespace]
            with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
                ret = txn.delete(key_list[0].encode())
                cursor = txn.cursor()
                if not cursor.first() and drop_empty_db:
                    txn.drop(db)
                    del self.namespaces[namespace]
        else:
            ret = self._insert_record(namespace, key_list[0], record)
        if not ret:
            logging.info(
                f"Error deleting key '{key}' from namespace '{namespace}'")
        return val

    def get_item(self, namespace, key=None, default=Sentinel):
        try:
            if key is None:
                return self._get_namespace(namespace)
            key_list = self._process_key(key)
            ns = self._get_record(namespace, key_list[0])
            val = reduce(operator.getitem, key_list[1:], ns)
        except Exception:
            if default != Sentinel:
                return default
            raise self.server.error(
                f"Key '{key}' in namespace '{namespace}' not found", 404)
        return val

    def ns_length(self, namespace):
        return len(self.ns_keys(namespace))

    def ns_keys(self, namespace):
        keys = []
        db = self.namespaces[namespace]
        with self.lmdb_env.begin(db=db) as txn:
            cursor = txn.cursor()
            remaining = cursor.first()
            while remaining:
                keys.append(cursor.key().decode())
                remaining = cursor.next()
        return keys

    def ns_values(self, namespace):
        ns = self._get_namespace(namespace)
        return ns.values()

    def ns_items(self, namespace):
        ns = self._get_namespace(namespace)
        return ns.items()

    def ns_contains(self, namespace, key):
        try:
            key_list = self._process_key(key)
            if len(key_list) == 1:
                return key_list[0] in self.ns_keys(namespace)
            ns = self._get_namespace(namespace)
            reduce(operator.getitem, key_list[1:], ns)
        except Exception:
            return False
        return True

    def register_local_namespace(self, namespace):
        if namespace not in self.namespaces:
            self.namespaces[namespace] = self.lmdb_env.open_db(
                namespace.encode())
        self.protected_namespaces.add(namespace)

    def wrap_namespace(self, namespace, parse_keys=True):
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Namespace '{namespace}' not found", 404)
        self.protected_namespaces.add(namespace)
        return NamespaceWrapper(namespace, self, parse_keys)

    def _process_key(self, key):
        try:
            key_list = key if isinstance(key, list) else key.split('.')
        except Exception:
            key_list = []
        if not key_list or "" in key_list:
            raise self.server.error(f"Invalid Key Format: '{key}'")
        return key_list

    def _insert_record(self, namespace, key, val):
        db = self.namespaces[namespace]
        with self.lmdb_env.begin(write=True, buffers=True, db=db) as txn:
            ret = txn.put(key.encode(), self._encode_value(val))
        return ret

    def _get_record(self, namespace, key, force=False):
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

    def _get_namespace(self, namespace):
        if namespace not in self.namespaces:
            raise self.server.error(
                f"Invalid database namespace '{namespace}'")
        db = self.namespaces[namespace]
        result = {}
        with self.lmdb_env.begin(buffers=True, db=db) as txn:
            cursor = txn.cursor()
            cursor.first()
            for db_key, value in cursor:
                k = bytes(db_key).decode()
                result[k] = self._decode_value(value)
        return result

    def _encode_value(self, value):
        try:
            enc_func = RECORD_ENCODE_FUNCS[type(value)]
            return enc_func(value)
        except Exception:
            raise self.server.error(
                f"Error encoding val: {value}, type: {type(value)}")

    def _decode_value(self, bvalue):
        fmt = bvalue[0]
        try:
            decode_func = RECORD_DECODE_FUNCS[fmt]
            return decode_func(bvalue)
        except Exception:
            raise self.server.error(
                f"Error decoding value {bvalue}, format: {chr(fmt)}")

    async def _handle_list_request(self, web_request):
        return {'namespaces': list(self.namespaces.keys())}

    async def _handle_item_request(self, web_request):
        action = web_request.get_action()
        namespace = web_request.get_str("namespace")
        if action != "GET":
            if namespace in self.protected_namespaces:
                raise self.server.error(
                    f"Namespace '{namespace}' is write protected")
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
            val = self.get_item(namespace, key)
        elif action == "POST":
            val = web_request.get("value")
            self.insert_item(namespace, key, val)
        elif action == "DELETE":
            val = self.delete_item(namespace, key, drop_empty_db=True)
        return {'namespace': namespace, 'key': key, 'value': val}

    def close(self):
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

class NamespaceWrapper:
    def __init__(self, namespace, database, parse_keys):
        self.namespace = namespace
        self.db = database
        # If parse keys is true, keys of a string type
        # will be passed straight to the DB methods.
        self.parse_keys = parse_keys

    def insert(self, key, value):
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        self.db.insert_item(self.namespace, key, value)

    def update_child(self, key, value):
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        self.db.update_item(self.namespace, key, value)

    def update(self, value):
        val_keys = set(value.keys())
        new_keys = val_keys - set(self.keys())
        update_keys = val_keys - new_keys
        for key in update_keys:
            self.update_child([key], value[key])
        for key in new_keys:
            self.insert([key], value[key])

    def get(self, key, default=None):
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.get_item(self.namespace, key, default)

    def delete(self, key):
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.delete_item(self.namespace, key)

    def __len__(self):
        return self.db.ns_length(self.namespace)

    def __getitem__(self, key):
        return self.get(key, default=Sentinel)

    def __setitem__(self, key, value):
        self.insert(key, value)

    def __delitem__(self, key):
        self.delete(key)

    def __contains__(self, key):
        if isinstance(key, str) and not self.parse_keys:
            key = [key]
        return self.db.ns_contains(self.namespace, key)

    def keys(self):
        return self.db.ns_keys(self.namespace)

    def values(self):
        return self.db.ns_values(self.namespace)

    def items(self):
        return self.db.ns_items(self.namespace)

    def pop(self, key, default=Sentinel):
        try:
            val = self.delete(key)
        except Exception:
            if default == Sentinel:
                raise
            val = default
        return val

    def clear(self):
        keys = self.keys()
        for k in keys:
            try:
                self.delete([k])
            except Exception:
                pass

def load_component(config):
    return MoonrakerDatabase(config)
