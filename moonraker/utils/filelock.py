# Async file locking using flock
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import fcntl
import errno
import logging
import pathlib
import contextlib
import asyncio
from . import ServerError
from typing import Optional, Type, Union
from types import TracebackType

class LockTimeout(ServerError):
    pass

class AsyncExclusiveFileLock(contextlib.AbstractAsyncContextManager):
    def __init__(
        self, file_path: pathlib.Path, timeout: Union[int, float] = 0
    ) -> None:
        self.lock_path = file_path.parent.joinpath(f".{file_path.name}.lock")
        self.timeout = timeout
        self.fd: int = -1
        self.locked: bool = False
        self.required_wait: bool = False

    async def __aenter__(self) -> AsyncExclusiveFileLock:
        await self.acquire()
        return self

    async def __aexit__(
        self,
        __exc_type: Optional[Type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType]
    ) -> None:
        await self.release()

    def _get_lock(self) -> bool:
        flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(self.lock_path), flags, 0o644)
        with contextlib.suppress(PermissionError):
            os.chmod(fd, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as err:
            os.close(fd)
            if err.errno == errno.ENOSYS:
                raise
            return False
        stat = os.fstat(fd)
        if stat.st_nlink == 0:
            # File was deleted before opening and after acquiring
            # lock, create a new one
            os.close(fd)
            return False
        self.fd = fd
        return True

    async def acquire(self) -> None:
        self.required_wait = False
        if self.timeout < 0:
            return
        loop = asyncio.get_running_loop()
        endtime = loop.time() + self.timeout
        logged: bool = False
        while True:
            try:
                self.locked = await loop.run_in_executor(None, self._get_lock)
            except OSError as err:
                logging.info(
                    "Failed to acquire advisory lock, allowing unlocked entry."
                    f"Error: {err}"
                )
                self.locked = False
                return
            if self.locked:
                return
            self.required_wait = True
            await asyncio.sleep(.25)
            if not logged:
                logged = True
                logging.info(
                    f"File lock {self.lock_path} is currently acquired by another "
                    "process, waiting for release."
                )
            if self.timeout > 0 and endtime >= loop.time():
                raise LockTimeout(
                    f"Attempt to acquire lock '{self.lock_path}' timed out"
                )

    def _release_file(self) -> None:
        with contextlib.suppress(OSError, PermissionError):
            if self.lock_path.is_file():
                self.lock_path.unlink()
        with contextlib.suppress(OSError, PermissionError):
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError, PermissionError):
            os.close(self.fd)

    async def release(self) -> None:
        if not self.locked:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._release_file)
        self.locked = False
