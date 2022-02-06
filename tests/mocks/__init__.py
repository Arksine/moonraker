from __future__ import annotations
import asyncio
from utils import ServerError
from .mock_gpio import MockGpiod

__all__ = ("MockReader", "MockWriter", "MockComponent", "MockWebsocket",
           "MockGpiod")

class MockWriter:
    def __init__(self, wait_drain: bool = False) -> None:
        self.wait_drain = wait_drain

    def write(self, data: str) -> None:
        pass

    async def drain(self) -> None:
        if self.wait_drain:
            evt = asyncio.Event()
            await evt.wait()
        else:
            raise ServerError("TestError")

class MockReader:
    def __init__(self, action: str = "") -> None:
        self.action = action
        self.eof = False

    def at_eof(self) -> bool:
        return self.eof

    async def readuntil(self, stop: bytes) -> bytes:
        if self.action == "wait":
            evt = asyncio.Event()
            await evt.wait()
            return b""
        elif self.action == "raise_error":
            raise ServerError("TestError")
        else:
            self.eof = True
            return b"NotJsonDecodable"


class MockComponent:
    def __init__(self,
                 err_init: bool = False,
                 err_exit: bool = False,
                 err_close: bool = False
                 ) -> None:
        self.err_init = err_init
        self.err_exit = err_exit
        self.err_close = err_close

    async def component_init(self):
        if self.err_init:
            raise ServerError("test")

    async def on_exit(self):
        if self.err_exit:
            raise ServerError("test")

    async def close(self):
        if self.err_close:
            raise ServerError("test")

class MockWebsocket:
    def __init__(self, fut: asyncio.Future) -> None:
        self.future = fut

    def queue_message(self, data: str):
        self.future.set_result(data)
