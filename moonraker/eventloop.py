# Wrapper around the asyncio eventloop
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import asyncio
import inspect
import functools
from concurrent.futures import ThreadPoolExecutor
from typing import (
    TYPE_CHECKING,
    Callable,
    Coroutine,
    Optional,
    TypeVar
)

if TYPE_CHECKING:
    _T = TypeVar("_T")
    FlexCallback = Callable[..., Optional[Coroutine]]

class EventLoop:
    TimeoutError = asyncio.TimeoutError
    def __init__(self) -> None:
        self.aioloop = asyncio.get_event_loop()
        self.add_signal_handler = self.aioloop.add_signal_handler
        self.remove_signal_handler = self.aioloop.remove_signal_handler
        self.add_reader = self.aioloop.add_reader
        self.add_writer = self.aioloop.add_writer
        self.remove_reader = self.aioloop.remove_reader
        self.remove_writer = self.aioloop.remove_writer
        self.get_loop_time = self.aioloop.time

    def register_callback(self,
                          callback: FlexCallback,
                          *args,
                          **kwargs
                          ) -> None:
        if inspect.iscoroutinefunction(callback):
            self.aioloop.create_task(callback(*args, **kwargs))  # type: ignore
        else:
            self.aioloop.call_soon(
                functools.partial(callback, *args, **kwargs))

    def delay_callback(self,
                       delay: float,
                       callback: FlexCallback,
                       *args,
                       **kwargs
                       ) -> asyncio.TimerHandle:
        if inspect.iscoroutinefunction(callback):
            return self.aioloop.call_later(
                delay, self._async_callback,
                functools.partial(callback, *args, **kwargs))
        else:
            return self.aioloop.call_later(
                delay, functools.partial(callback, *args, **kwargs))

    def _async_callback(self, callback: Callable[[], Coroutine]) -> None:
        # This wrapper delays creation of the coroutine object.  In the
        # event that a callback is cancelled this prevents "coroutine
        # was never awaited" warnings in asyncio
        self.aioloop.create_task(callback())

    async def run_in_thread(self,
                            callback: Callable[..., _T],
                            *args
                            ) -> _T:
        with ThreadPoolExecutor(max_workers=1) as tpe:
            return await self.aioloop.run_in_executor(tpe, callback, *args)

    def start(self):
        self.aioloop.run_forever()

    def stop(self):
        self.aioloop.stop()

    def close(self):
        self.aioloop.close()
