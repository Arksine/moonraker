# General Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import logging
import logging.handlers
import os
import glob
import importlib
import pathlib
import sys
import subprocess
import asyncio
import hashlib
import json
from queue import SimpleQueue as Queue

# Annotation imports
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
    ClassVar,
    Tuple,
    Dict,
    Any,
)

if TYPE_CHECKING:
    from types import ModuleType

MOONRAKER_PATH = os.path.join(os.path.dirname(__file__), '..')
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

# Coroutine friendly QueueHandler courtesy of Martjin Pieters:
# https://www.zopatista.com/python/2019/05/11/asyncio-logging/
class LocalQueueHandler(logging.handlers.QueueHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Removed the call to self.prepare(), handle task cancellation
        try:
            self.enqueue(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.handleError(record)

# Timed Rotating File Handler, based on Klipper's implementation
class MoonrakerLoggingHandler(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, app_args: Dict[str, Any], **kwargs) -> None:
        super().__init__(app_args['log_file'], **kwargs)
        self.rollover_info: Dict[str, str] = {
            'header': f"{'-'*20}Moonraker Log Start{'-'*20}"
        }
        self.rollover_info['application_args'] = "\n".join(
            [f"{k}: {v}" for k, v in app_args.items()])
        lines = [line for line in self.rollover_info.values() if line]
        if self.stream is not None:
            self.stream.write("\n".join(lines) + "\n")

    def set_rollover_info(self, name: str, item: str) -> None:
        self.rollover_info[name] = item

    def doRollover(self) -> None:
        super().doRollover()
        lines = [line for line in self.rollover_info.values() if line]
        if self.stream is not None:
            self.stream.write("\n".join(lines) + "\n")

# Parse the git version from the command line.  This code
# is borrowed from Klipper.
def retreive_git_version(source_path: str) -> str:
    # Obtain version info from "git" program
    prog = ('git', '-C', source_path, 'describe', '--always',
            '--tags', '--long', '--dirty')
    process = subprocess.Popen(prog, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    ver, err = process.communicate()
    retcode = process.wait()
    if retcode == 0:
        return ver.strip().decode()
    raise Exception(f"Failed to retreive git version: {err.decode()}")

def get_software_version() -> str:
    version = "?"

    try:
        version = retreive_git_version(MOONRAKER_PATH)
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

def setup_logging(app_args: Dict[str, Any]
                  ) -> Tuple[logging.handlers.QueueListener,
                             Optional[MoonrakerLoggingHandler]]:
    root_logger = logging.getLogger()
    queue: Queue = Queue()
    queue_handler = LocalQueueHandler(queue)
    root_logger.addHandler(queue_handler)
    root_logger.setLevel(logging.INFO)
    stdout_hdlr = logging.StreamHandler(sys.stdout)
    stdout_fmt = logging.Formatter(
        '[%(filename)s:%(funcName)s()] - %(message)s')
    stdout_hdlr.setFormatter(stdout_fmt)
    for name, val in app_args.items():
        logging.info(f"{name}: {val}")
    file_hdlr = None
    if app_args.get('log_file', ""):
        file_hdlr = MoonrakerLoggingHandler(
            app_args, when='midnight', backupCount=2)
        formatter = logging.Formatter(
            '%(asctime)s [%(filename)s:%(funcName)s()] - %(message)s')
        file_hdlr.setFormatter(formatter)
        listener = logging.handlers.QueueListener(
            queue, file_hdlr, stdout_hdlr)
    else:
        listener = logging.handlers.QueueListener(
            queue, stdout_hdlr)
    listener.start()
    return listener, file_hdlr

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
