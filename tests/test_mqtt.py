# Tests for the MQTT client's reconnect handling
#
# Copyright (C) 2026 Kyle D <dev@non-ya.biz>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import asyncio
import socket
import pytest
import pytest_asyncio
from typing import AsyncIterator, Callable, List
from unittest.mock import MagicMock
from moonraker.eventloop import EventLoop
from moonraker.components.mqtt import MQTTClient

CONNACK_ACCEPTED = b"\x20\x02\x00\x00"
CONNACK_NOT_AUTHORIZED = b"\x20\x02\x00\x05"
# Moonraker waits 2 seconds between connection attempts
RETRY_WINDOW = 2.5


class FakeBroker:
    """Speaks just enough MQTT to answer each incoming CONNECT with a scripted
    action: "accept", "refuse", "drop" (close without a CONNACK) or "hold"
    (leave the CONNECT unanswered).
    """

    def __init__(self, actions: List[str]) -> None:
        self.actions = actions
        self.connections = 0
        self.writers: List[asyncio.StreamWriter] = []

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        action = self.actions[self.connections]
        self.connections += 1
        self.writers.append(writer)
        await reader.read(1024)
        if action == "drop":
            writer.close()
            return
        if action != "hold":
            refused = action == "refuse"
            writer.write(CONNACK_NOT_AUTHORIZED if refused else CONNACK_ACCEPTED)
        while await reader.read(1024):
            pass
        writer.close()

    def drop_clients(self) -> None:
        for writer in self.writers:
            writer.close()

    async def stop(self) -> None:
        self.drop_clients()
        self.server.close()


def make_client(port: int) -> MQTTClient:
    options = {"address": "127.0.0.1", "port": port, "enable_moonraker_api": False}

    def get_option(key: str, default=None, **kwargs):
        return options.get(key, default)

    server = MagicMock()
    server.get_event_loop.return_value = EventLoop()
    config = MagicMock()
    config.get_server.return_value = server
    for getter in ("get", "getint", "getboolean", "getfloat", "getdict", "gettemplate"):
        getattr(config, getter).side_effect = get_option
    return MQTTClient(config)


async def wait_until(condition: Callable[[], bool], timeout: float = 10.) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(.05)
    return True


@pytest_asyncio.fixture
async def broker_factory() -> AsyncIterator[Callable]:
    started = []

    async def start(actions: List[str]):
        broker = FakeBroker(actions)
        mqtt = make_client(await broker.start())
        started.append((broker, mqtt))
        return broker, mqtt

    yield start
    for broker, mqtt in started:
        await mqtt.close()
        await broker.stop()


@pytest.mark.asyncio
async def test_retries_when_broker_drops_before_connack(broker_factory) -> None:
    broker, mqtt = await broker_factory(["accept", "drop", "accept"])
    await mqtt.component_init()
    assert await mqtt.wait_connection(5.)
    broker.drop_clients()
    assert await wait_until(lambda: broker.connections == 3 and mqtt.is_connected())
    sent = [call.args[0] for call in mqtt.server.send_event.call_args_list]
    assert sent.count("mqtt:disconnected") == 1


@pytest.mark.asyncio
async def test_retries_when_first_connect_drops(broker_factory) -> None:
    broker, mqtt = await broker_factory(["drop", "accept"])
    await mqtt.component_init()
    assert await wait_until(lambda: broker.connections == 2 and mqtt.is_connected())


@pytest.mark.asyncio
async def test_refused_connection_is_not_retried(broker_factory) -> None:
    broker, mqtt = await broker_factory(["refuse", "accept"])
    await mqtt.component_init()
    assert await wait_until(lambda: mqtt.server.set_failed_component.called)
    await asyncio.sleep(RETRY_WINDOW)
    assert broker.connections == 1


@pytest.mark.asyncio
async def test_no_reconnect_after_close(broker_factory) -> None:
    broker, mqtt = await broker_factory(["hold", "accept"])
    await mqtt.component_init()
    assert await wait_until(lambda: broker.connections == 1)
    await asyncio.sleep(.1)
    await mqtt.close()
    broker.drop_clients()
    await asyncio.sleep(RETRY_WINDOW)
    assert broker.connections == 1


@pytest.mark.asyncio
async def test_reconnect_task_survives_detached_socket(broker_factory) -> None:
    def detach_like_tls(sock: socket.socket) -> None:
        # ssl.SSLContext.wrap_socket() detaches the socket it is given
        socket.socket(fileno=sock.detach()).close()

    _, mqtt = await broker_factory(["accept"])
    mqtt.client.reconnect = detach_like_tls
    await mqtt.component_init()
    task = mqtt.connect_task
    await task
    assert mqtt.connect_task is None
