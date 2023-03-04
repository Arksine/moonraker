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
import shlex
import re
import struct
import socket
import enum
import ipaddress
import platform
from .exceptions import ServerError
from . import source_info
from . import json_wrapper

# Annotation imports
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    Any,
    Tuple,
    Dict,
    Union
)

if TYPE_CHECKING:
    from types import ModuleType
    from asyncio.trsock import TransportSocket

SYS_MOD_PATHS = glob.glob("/usr/lib/python3*/dist-packages")
SYS_MOD_PATHS += glob.glob("/usr/lib/python3*/site-packages")
SYS_MOD_PATHS += glob.glob("/usr/lib/*-linux-gnu/python3*/site-packages")
IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

try:
    KERNEL_VERSION = tuple([int(part) for part in platform.release().split(".")[:2]])
except Exception:
    KERNEL_VERSION = (0, 0)


class Sentinel(enum.Enum):
    MISSING = object()

def _run_git_command(cmd: str) -> str:
    prog = shlex.split(cmd)
    process = subprocess.Popen(prog, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    ret, err = process.communicate()
    retcode = process.wait()
    if retcode == 0:
        return ret.strip().decode()
    raise Exception(
        f"Failed to run git command '{cmd}': {err.decode(errors='ignore')}"
    )

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

def get_repo_info(source_path: str) -> Dict[str, Any]:
    repo_info: Dict[str, Any] = {
        "software_version": "?",
        "git_branch": "?",
        "git_remote": "?",
        "git_repo_url": "?",
        "modified_files": [],
        "unofficial_components": []
    }
    try:
        repo_info["software_version"] = retrieve_git_version(source_path)
        cmd = f"git -C {source_path} branch --no-color"
        branch_list = _run_git_command(cmd)
        for line in branch_list.split("\n"):
            if line[0] == "*":
                repo_info["git_branch"] = line[1:].strip()
                break
        else:
            return repo_info
        if repo_info["git_branch"].startswith("(HEAD detached"):
            parts = repo_info["git_branch"] .strip("()").split()[-1]
            remote, _, _ = parts.partition("/")
            if not remote:
                return repo_info
            repo_info["git_remote"] = remote
        else:
            branch = repo_info["git_branch"]
            cmd = f"git -C {source_path} config --get branch.{branch}.remote"
            repo_info["git_remote"] = _run_git_command(cmd)
        cmd = f"git -C {source_path} remote get-url {repo_info['git_remote']}"
        repo_info["git_repo_url"] = _run_git_command(cmd)
        cmd = f"git -C {source_path} status --porcelain --ignored"
        status = _run_git_command(cmd)
        for line in status.split("\n"):
            parts = line.strip().split(maxsplit=1)
            if len(parts) != 2:
                continue
            if parts[0] == "M":
                repo_info["modified_files"].append(parts[1])
            elif (
                parts[0] in ("??", "!!")
                and parts[1].endswith(".py")
                and parts[1].startswith("components")
            ):
                comp = parts[1].split("/", maxsplit=1)[-1]
                repo_info["unofficial_components"].append(comp)
    except Exception:
        logging.exception("Error Retreiving Git Repo Info")
    return repo_info

def get_software_info() -> Dict[str, Any]:
    src_path = source_info.source_path()
    if source_info.is_git_repo():
        return get_repo_info(str(src_path))
    pkg_ver = source_info.package_version()
    if pkg_ver is not None:
        return {"software_version": pkg_ver}
    version: str = "?"
    vfile = src_path.joinpath("moonraker/.version")
    if vfile.exists():
        try:
            version = vfile.read_text().strip()
        except Exception:
            logging.exception("Unable to extract version from file")
            version = "?"
    return {"software_version": version}

def hash_directory(
    dir_path: Union[str, pathlib.Path],
    ignore_exts: List[str],
    ignore_dirs: List[str]
) -> str:
    if isinstance(dir_path, str):
        dir_path = pathlib.Path(dir_path)
    checksum = hashlib.blake2s()
    if not dir_path.exists():
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

def verify_source(
    path: Optional[Union[str, pathlib.Path]] = None
) -> Optional[Tuple[str, bool]]:
    if path is None:
        path = source_info.source_path()
    elif isinstance(path, str):
        path = pathlib.Path(path)
    rfile = path.joinpath(".release_info")
    if not rfile.exists():
        return None
    try:
        rinfo = json_wrapper.loads(rfile.read_text())
    except Exception:
        return None
    orig_chksum = rinfo['source_checksum']
    ign_dirs = rinfo['ignored_dirs']
    ign_exts = rinfo['ignored_exts']
    checksum = hash_directory(path, ign_exts, ign_dirs)
    return checksum, checksum == orig_chksum

def load_system_module(name: str) -> ModuleType:
    if not SYS_MOD_PATHS:
        # no dist path detected, fall back to direct import attempt
        try:
            return importlib.import_module(name)
        except ImportError as e:
            raise ServerError(f"Unable to import module {name}") from e
    for module_path in SYS_MOD_PATHS:
        sys.path.insert(0, module_path)
        try:
            module = importlib.import_module(name)
        except ImportError as e:
            if not isinstance(e, ModuleNotFoundError):
                logging.exception(f"Failed to load {name} module")
        else:
            break
        finally:
            sys.path.pop(0)
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

def pretty_print_time(seconds: int) -> str:
    if seconds == 0:
        return "0 Seconds"
    fmt_list: List[str] = []
    times: Dict[str, int] = {}
    times["Day"], seconds = divmod(seconds, 86400)
    times["Hour"], seconds = divmod(seconds, 3600)
    times["Minute"], times["Second"] = divmod(seconds, 60)
    for ident, val in times.items():
        if val == 0:
            continue
        fmt_list.append(f"{val} {ident}" if val == 1 else f"{val} {ident}s")
    return ", ".join(fmt_list)

def parse_ip_address(address: str) -> Optional[IPAddress]:
    try:
        return ipaddress.ip_address(address)
    except Exception:
        return None
