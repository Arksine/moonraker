# General Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import logging
import os
import glob
import importlib
import pathlib
import sys
import subprocess
import asyncio
import hashlib
import json
import shlex
import re
import struct
import socket

# Annotation imports
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    ClassVar,
    Tuple,
    Dict,
)

if TYPE_CHECKING:
    from types import ModuleType
    from asyncio.trsock import TransportSocket

MOONRAKER_PATH = str(pathlib.Path(__file__).parent.parent.parent.resolve())
SYS_MOD_PATHS = glob.glob("/usr/lib/python3*/dist-packages")
SYS_MOD_PATHS += glob.glob("/usr/lib/python3*/site-packages")

class ServerError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        Exception.__init__(self, message)
        self.status_code = status_code


class SentinelClass:
    _instance: ClassVar[Optional[SentinelClass]] = None

    @staticmethod
    def get_instance() -> SentinelClass:
        if SentinelClass._instance is None:
            SentinelClass._instance = SentinelClass()
        return SentinelClass._instance

def _run_git_command(cmd: str) -> str:
    prog = shlex.split(cmd)
    process = subprocess.Popen(prog, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    ret, err = process.communicate()
    retcode = process.wait()
    if retcode == 0:
        return ret.strip().decode()
    raise Exception(f"Failed to run git command: {cmd}")

def _retrieve_git_tag(source_path: str) -> str:
    cmd = f"git -C {source_path} rev-list --tags --max-count=1"
    hash = _run_git_command(cmd)
    cmd = f"git -C {source_path} describe --tags {hash}"
    tag = _run_git_command(cmd)
    cmd = f"git -C {source_path} rev-list {tag}..HEAD --count"
    count = _run_git_command(cmd)
    return f"{tag}-{count}"

# Parse the git version from the command line.  This code
# is borrowed from Klipper.
def retrieve_git_version(source_path: str) -> str:
    # Obtain version info from "git" program
    cmd = f"git -C {source_path} describe --always --tags --long --dirty"
    ver = _run_git_command(cmd)
    tag_match = re.match(r"v\d+\.\d+\.\d+", ver)
    if tag_match is not None:
        return ver
    # This is likely a shallow clone.  Resolve the tag and manually create
    # the version string
    tag = _retrieve_git_tag(source_path)
    return f"t{tag}-g{ver}-shallow"

def get_software_version() -> str:
    version: str = "?"
    try:
        import moonraker.__version__ as ver  # type: ignore
        version = ver.__version__
    except Exception:
        pass
    else:
        if version:
            return version
    try:
        version = retrieve_git_version(MOONRAKER_PATH)
    except Exception:
        vfile = pathlib.Path(os.path.join(
            MOONRAKER_PATH, "moonraker/.version"))
        if vfile.exists():
            try:
                version = vfile.read_text().strip()
            except Exception:
                logging.exception("Unable to extract version from file")
                version = "?"
    return version


def hash_directory(dir_path: str,
                   ignore_exts: List[str],
                   ignore_dirs: List[str]
                   ) -> str:
    checksum = hashlib.blake2s()
    if not os.path.exists(dir_path):
        return ""
    for dpath, dnames, fnames in os.walk(dir_path):
        valid_dirs: List[str] = []
        for dname in sorted(dnames):
            if dname[0] == '.' or dname in ignore_dirs:
                continue
            valid_dirs.append(dname)
        dnames[:] = valid_dirs
        for fname in sorted(fnames):
            ext = os.path.splitext(fname)[-1].lower()
            if fname[0] == '.' or ext in ignore_exts:
                continue
            fpath = pathlib.Path(os.path.join(dpath, fname))
            try:
                checksum.update(fpath.read_bytes())
            except Exception:
                pass
    return checksum.hexdigest()

def verify_source(path: str = MOONRAKER_PATH) -> Optional[Tuple[str, bool]]:
    rfile = pathlib.Path(os.path.join(path, ".release_info"))
    if not rfile.exists():
        return None
    try:
        rinfo = json.loads(rfile.read_text())
    except Exception:
        return None
    orig_chksum = rinfo['source_checksum']
    ign_dirs = rinfo['ignored_dirs']
    ign_exts = rinfo['ignored_exts']
    checksum = hash_directory(path, ign_exts, ign_dirs)
    return checksum, checksum == orig_chksum

def load_system_module(name: str) -> ModuleType:
    for module_path in SYS_MOD_PATHS:
        sys.path.insert(0, module_path)
        try:
            module = importlib.import_module(name)
        except ImportError as e:
            if not isinstance(e, ModuleNotFoundError):
                logging.exception(f"Failed to load {name} module")
            sys.path.pop(0)
        else:
            sys.path.pop(0)
            break
    else:
        raise ServerError(f"Unable to import module {name}")
    return module

def get_unix_peer_credentials(
    writer: asyncio.StreamWriter, name: str
) -> Dict[str, int]:
    sock: TransportSocket
    sock = writer.get_extra_info("socket", None)
    if sock is None:
        logging.debug(
            f"Unable to get underlying Unix Socket for {name}, "
            "cant fetch peer credentials"
        )
        return {}
    data: bytes = b""
    try:
        size = struct.calcsize("3I")
        data = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, size)
        pid, uid, gid = struct.unpack("3I", data)
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception(
            f"Failed to get Unix Socket Peer Credentials for {name}"
            f", raw: 0x{data.hex()}"
        )
        return {}
    return {
        "process_id": pid,
        "user_id": uid,
        "group_id": gid
    }
