# Wrapper around the asyncio eventloop
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import contextlib
import asyncio
import inspect
import functools
import socket
import time
import logging
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Callable,
    Optional,
    Tuple,
    TypeVar,
    Union,
    Set
)

_uvl_var = os.getenv("MOONRAKER_ENABLE_UVLOOP", "y").lower()
_uvl_enabled = False
if _uvl_var in ["y", "yes", "true"]:
    with contextlib.suppress(ImportError):
        import uvloop  # type: ignore
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        _uvl_enabled = True

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop
    _T = TypeVar("_T")
    FlexCallback = Callable[..., Optional[Awaitable]]
    TimerCallback = Callable[[float], Union[float, Awaitable[float]]]

class EventLoop:
    UVLOOP_ENABLED = _uvl_enabled
    TimeoutError = asyncio.TimeoutError
    def __init__(self) -> None:
        self.reset()

    @property
    def asyncio_loop(self) -> AbstractEventLoop:
        return self.aioloop

    def reset(self) -> None:
        self.aioloop = asyncio.get_running_loop()
        self.bg_tasks: Set[asyncio.Task] = set()
        self.add_signal_handler = self.aioloop.add_signal_handler
        self.remove_signal_handler = self.aioloop.remove_signal_handler
        self.add_reader = self.aioloop.add_reader
        self.add_writer = self.aioloop.add_writer
        self.remove_reader = self.aioloop.remove_reader
        self.remove_writer = self.aioloop.remove_writer
        self.get_loop_time = self.aioloop.time
        self.create_future = self.aioloop.create_future
        self.call_at = self.aioloop.call_at
        self.set_debug = self.aioloop.set_debug
        self.is_running = self.aioloop.is_running

    def create_task(self, coro: asyncio._CoroutineLike[_T]) -> asyncio.Task[_T]:
        tsk = self.aioloop.create_task(coro)
        self.bg_tasks.add(tsk)
        tsk.add_done_callback(self.bg_tasks.discard)
        return tsk

    def _create_new_loop(self) -> asyncio.AbstractEventLoop:
        for _ in range(5):
            # Sometimes the new loop does not properly instantiate.
            # Give 5 attempts before raising an exception
            new_loop = asyncio.new_event_loop()
            if not new_loop.is_closed():
                break
            logging.info("Failed to create open eventloop, "
                         "retrying in .5 seconds...")
            time.sleep(.5)
        else:
            raise RuntimeError("Unable to create new open eventloop")
        asyncio.set_event_loop(new_loop)
        return new_loop

    def register_callback(self,
                          callback: FlexCallback,
                          *args,
                          **kwargs
                          ) -> None:
        async def _wrapper():
            try:
                ret = callback(*args, **kwargs)
                if inspect.isawaitable(ret):
                    await ret
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Error Running Callback")
        self.create_task(_wrapper())

    def delay_callback(self,
                       delay: float,
                       callback: FlexCallback,
                       *args,
                       **kwargs
                       ) -> asyncio.TimerHandle:
        return self.aioloop.call_later(
            delay, self.register_callback,
            functools.partial(callback, *args, **kwargs)
        )

    def register_timer(self, callback: TimerCallback):
        return FlexTimer(self, callback)

    def run_in_thread(self,
                      callback: Callable[..., _T],
                      *args
                      ) -> Awaitable[_T]:
        return self.aioloop.run_in_executor(None, callback, *args)

    async def create_socket_connection(
        self, address: Tuple[str, int], timeout: Optional[float] = None
    ) -> socket.socket:
        host, port = address
        """
        async port of socket.create_connection()
        """
        loop = self.aioloop
        err = None
        ainfo = await loop.getaddrinfo(
            host, port, family=0, type=socket.SOCK_STREAM
        )
        for res in ainfo:
            af, socktype, proto, _cannon_name, _sa = res
            sock = None
            try:
                sock = socket.socket(af, socktype, proto)
                sock.settimeout(0)
                sock.setblocking(False)
                await asyncio.wait_for(
                    loop.sock_connect(sock, (host, port)), timeout
                )
                # Break explicitly a reference cycle
                err = None
                return sock
            except (socket.error, asyncio.TimeoutError) as _:
                err = _
                if sock is not None:
                    loop.remove_writer(sock.fileno())
                    sock.close()
        if err is not None:
            try:
                raise err
            finally:
                # Break explicitly a reference cycle
                err = None
        else:
            raise socket.error("getaddrinfo returns an empty list")

    def close(self):
        self.aioloop.close()

class FlexTimer:
    def __init__(self,
                 eventloop: EventLoop,
                 callback: TimerCallback
                 ) -> None:
        self.eventloop = eventloop
        self.callback = callback
        self.timer_handle: Optional[asyncio.TimerHandle] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.running: bool = False

    def in_callback(self) -> bool:
        return self.timer_task is not None and not self.timer_task.done()

    def start(self, delay: float = 0.):
        if self.running:
            return
        self.running = True
        if self.in_callback():
            return
        call_time = self.eventloop.get_loop_time() + delay
        self.timer_handle = self.eventloop.call_at(
            call_time, self._schedule_task)

    def stop(self):
        if not self.running:
            return
        self.running = False
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

    async def wait_timer_done(self) -> None:
        if self.timer_task is None:
            return
        await self.timer_task

    def _schedule_task(self):
        self.timer_handle = None
        self.timer_task = self.eventloop.create_task(self._call_wrapper())

    def is_running(self) -> bool:
        return self.running

    async def _call_wrapper(self):
        if not self.running:
            return
        try:
            ret = self.callback(self.eventloop.get_loop_time())
            if isinstance(ret, Awaitable):
                ret = await ret
        except Exception:
            self.running = False
            raise
        finally:
            self.timer_task = None
        if self.running:
            self.timer_handle = self.eventloop.call_at(ret, self._schedule_task)
