#! /usr/bin/python3
# Tool to backup and restore Moonraker's LMDB database
#
# Copyright (C) 2022 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import argparse
import pathlib
import base64
import tempfile
import re
import time
from typing import Any, Dict, Optional, TextIO, Tuple
import lmdb

MAX_NAMESPACES = 100
MAX_DB_SIZE = 200 * 2**20
HEADER_KEY = b"MOONRAKER_DATABASE_START"

LINE_MATCH = re.compile(
    r"^\+(\d+),(\d+):([A-Za-z0-9+/]+={0,2})->([A-Za-z0-9+/]+={0,2})$"
)

class DBToolError(Exception):
    pass

# Use a modified CDBMake Format
# +keylen,datalen:namespace|key->data
# Key length includes the namespace, key and separator (a colon)

def open_db(db_path: str) -> lmdb.Environment:
    return lmdb.open(db_path, map_size=MAX_DB_SIZE,
                     max_dbs=MAX_NAMESPACES)

def _do_dump(namespace: bytes,
             db: object,
             backup: TextIO,
             txn: lmdb.Transaction
             ) -> None:
    expected_key_count: int = txn.stat(db)["entries"]
    # write the namespace header
    ns_key = base64.b64encode(b"namespace_" + namespace).decode()
    ns_str = f"entries={expected_key_count}"
    ns_val = base64.b64encode(ns_str.encode()).decode()
    out = f"+{len(ns_key)},{len(ns_val)}:{ns_key}->{ns_val}\n"
    backup.write(out)
    with txn.cursor(db=db) as cursor:
        count = 0
        remaining = cursor.first()
        while remaining:
            key, value = cursor.item()
            keystr = base64.b64encode(key).decode()
            valstr = base64.b64encode(value).decode()
            out = f"+{len(keystr)},{len(valstr)}:{keystr}->{valstr}\n"
            backup.write(out)
            count += 1
            remaining = cursor.next()
    if expected_key_count != count:
        print("Warning: Key count mismatch for namespace "
              f"'{namespace.decode()}': expected {expected_key_count}"
              f", wrote {count}")

def _write_header(ns_count: int, backup: TextIO):
    val_str = f"namespace_count={ns_count}"
    hkey = base64.b64encode(HEADER_KEY).decode()
    hval = base64.b64encode(val_str.encode()).decode()
    out = f"+{len(hkey)},{len(hval)}:{hkey}->{hval}\n"
    backup.write(out)

def backup(args: Dict[str, Any]):
    source_db = pathlib.Path(args["source"]).expanduser().resolve()
    if not source_db.is_dir():
        print(f"Source path not a folder: '{source_db}'")
        exit(1)
    if not source_db.joinpath("data.mdb").exists():
        print(f"No database file found in source path: '{source_db}'")
        exit(1)
    bkp_dest = pathlib.Path(args["output"]).expanduser().resolve()
    print(f"Backing up database at '{source_db}' to '{bkp_dest}'...")
    if bkp_dest.exists():
        print(f"Warning: file at '{bkp_dest}' exists, will be overwritten")
    env = open_db(str(source_db))
    expected_ns_cnt: int = env.stat()["entries"]
    with bkp_dest.open("wt") as f:
        _write_header(expected_ns_cnt, f)
        with env.begin(buffers=True) as txn:
            count = 0
            with txn.cursor() as cursor:
                remaining = cursor.first()
                while remaining:
                    namespace = bytes(cursor.key())
                    db = env.open_db(namespace, txn=txn, create=False)
                    _do_dump(namespace, db, f, txn)
                    count += 1
                    remaining = cursor.next()
    env.close()
    if expected_ns_cnt != count:
        print("Warning: namespace count mismatch: "
              f"expected: {expected_ns_cnt}, wrote: {count}")
    print("Backup complete!")

def _process_header(key: bytes, value: bytes) -> int:
    if key != HEADER_KEY:
        raise DBToolError(
            "Database Backup does not contain a valid header key, "
            f" got {key.decode()}")
    val_parts = value.split(b"=", 1)
    if val_parts[0] != b"namespace_count":
        raise DBToolError(
            "Database Backup has an invalid header value, got "
            f"{value.decode()}")
    return int(val_parts[1])

def _process_namespace(key: bytes, value: bytes) -> Tuple[bytes, int]:
    key_parts = key.split(b"_", 1)
    if key_parts[0] != b"namespace":
        raise DBToolError(
            f"Invalid Namespace Key '{key.decode()}', ID not prefixed")
    namespace = key_parts[1]
    val_parts = value.split(b"=", 1)
    if val_parts[0] != b"entries":
        raise DBToolError(
            f"Invalid Namespace value '{value.decode()}', entry "
            "count not present")
    entries = int(val_parts[1])
    return namespace, entries

def _process_line(line: str) -> Tuple[bytes, bytes]:
    match = LINE_MATCH.match(line)
    if match is None:
        # TODO: use own exception
        raise DBToolError(
            f"Invalid DB Entry match: {line}")
    parts = match.groups()
    if len(parts) != 4:
        raise DBToolError(
            f"Invalid DB Entry, does not contain all data: {line}")
    key_len, val_len, key, val = parts
    if len(key) != int(key_len):
        raise DBToolError(
            f"Invalid DB Entry, key length mismatch. "
            f"Got {len(key)}, expected {key_len}, line: {line}")
    if len(val) != int(val_len):
        raise DBToolError(
            f"Invalid DB Entry, value length mismatch. "
            f"Got {len(val)}, expected {val_len}, line: {line}")
    decoded_key = base64.b64decode(key.encode())
    decoded_val = base64.b64decode(val.encode())
    return decoded_key, decoded_val

def restore(args: Dict[str, Any]):
    dest_path = pathlib.Path(args["destination"]).expanduser().resolve()
    input_db = pathlib.Path(args["input"]).expanduser().resolve()
    if not input_db.is_file():
        print(f"No backup found at path: {input_db}")
        exit(1)
    if not dest_path.exists():
        print(f"Destination path '{dest_path}' does not exist, directory"
              "will be created")
    print(f"Restoring backup from '{input_db}' to '{dest_path}'...")
    bkp_dir: Optional[pathlib.Path] = None
    if dest_path.joinpath("data.mdb").exists():
        bkp_dir = dest_path.parent.joinpath("backup")
        if not bkp_dir.exists():
            bkp_dir = pathlib.Path(tempfile.gettempdir())
        str_time = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        bkp_dir = bkp_dir.joinpath(f"{str_time}/database")
        if not bkp_dir.is_dir():
            bkp_dir.mkdir(parents=True)
        print(f"Warning: database file at found in '{dest_path}', "
              "all data will be overwritten.  Copying existing DB "
              f"to '{bkp_dir}'")
    env = open_db(str(dest_path))
    if bkp_dir is not None:
        env.copy(str(bkp_dir))
    expected_ns_count = -1
    namespace_count = 0
    keys_left = 0
    namespace = b""
    current_db = object()
    with env.begin(write=True) as txn:
        # clear all existing entries
        dbs = []
        with txn.cursor() as cursor:
            remaining = cursor.first()
            while remaining:
                ns = cursor.key()
                dbs.append(env.open_db(ns, txn=txn, create=False))
                remaining = cursor.next()
        for db in dbs:
            txn.drop(db)
        with input_db.open("rt") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                key, val = _process_line(line)
                if expected_ns_count < 0:
                    expected_ns_count = _process_header(key, val)
                    continue
                if not keys_left:
                    namespace, keys_left = _process_namespace(key, val)
                    current_db = env.open_db(namespace, txn=txn)
                    namespace_count += 1
                    continue
                txn.put(key, val, db=current_db)
                keys_left -= 1
    if expected_ns_count != namespace_count:
        print("Warning: Namespace count mismatch, expected: "
              f"{expected_ns_count}, processed {namespace_count}")
    print("Restore Complete")


if __name__ == "__main__":
    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="dbtool - tool for backup/restore of Moonraker's database")
    subparsers = parser.add_subparsers(
        title="commands", description="valid commands", required=True,
        metavar="<command>")
    bkp_parser = subparsers.add_parser("backup", help="backup a database")
    rst_parser = subparsers.add_parser("restore", help="restore a databse")
    bkp_parser.add_argument(
        "source", metavar="<source path>",
        help="location of the folder containing the database to backup")
    bkp_parser.add_argument(
        "output", metavar="<output file>",
        help="location of the backup file to write to",
        default="~/moonraker_db.bkp")
    bkp_parser.set_defaults(func=backup)
    rst_parser.add_argument(
        "destination", metavar="<destination>",
        help="location of the folder where the database will be restored")
    rst_parser.add_argument(
        "input", metavar="<input file>",
        help="location of the backup file to restore from")
    rst_parser.set_defaults(func=restore)
    args = parser.parse_args()
    args.func(vars(args))
