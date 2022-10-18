from __future__ import annotations
import pytest
import asyncio
import pathlib
from typing import TYPE_CHECKING, Dict
from moonraker.server import ServerError
from moonraker.klippy_connection import KlippyRequest
from mocks import MockReader, MockWriter

if TYPE_CHECKING:
    from server import Server
    from conftest import KlippyProcess

@pytest.mark.usefixtures("klippy")
@pytest.mark.asyncio
async def test_klippy_startup(full_server: Server):
    evtloop = full_server.get_event_loop()
    futs = [evtloop.create_future() for _ in range(3)]
    events = {
        "server:klippy_identified": lambda: futs[0].set_result("id"),
        "server:klippy_started": lambda x: futs[1].set_result("started"),
        "server:klippy_ready": lambda: futs[2].set_result("ready")
    }
    for name, func in events.items():
        full_server.register_event_handler(name, func)
    await full_server.start_server()
    ret = await asyncio.wait_for(asyncio.gather(*futs), 4.)
    assert (
        ret == ["id", "started", "ready"] and
        full_server.klippy_connection.is_connected()
    )

@pytest.mark.asyncio
async def test_gcode_response(ready_server: Server,
                              klippy: KlippyProcess):
    evtloop = ready_server.get_event_loop()
    fut = evtloop.create_future()

    def on_gc_resp(resp: str):
        if not fut.done():
            fut.set_result(resp)
    ready_server.register_event_handler("server:gcode_response", on_gc_resp)
    klippy.send_gcode("M118 Moonraker Test")
    await asyncio.wait_for(fut, 1.)
    assert "Moonraker Test" in fut.result()

@pytest.mark.asyncio
async def test_klippy_shutdown(ready_server: Server, klippy: KlippyProcess):
    evtloop = ready_server.get_event_loop()
    fut = evtloop.create_future()

    def on_shutdown():
        if not fut.done():
            fut.set_result("shutdown")
    ready_server.register_event_handler("server:klippy_shutdown", on_shutdown)
    klippy.send_gcode("M112")
    await asyncio.wait_for(fut, 2.)
    assert fut.result() == "shutdown"

@pytest.mark.asyncio
async def test_klippy_reconnect(ready_server: Server, klippy: KlippyProcess):
    evtloop = ready_server.get_event_loop()
    futs = [evtloop.create_future() for _ in range(2)]
    events = {
        "server:klippy_disconnect": lambda: futs[0].set_result("disconnect"),
        "server:klippy_ready": lambda: futs[1].set_result("ready")
    }
    for name, func in events.items():
        ready_server.register_event_handler(name, func)
    klippy.restart()
    ret = await asyncio.wait_for(asyncio.gather(*futs), 6.)
    assert ret == ["disconnect", "ready"]

@pytest.mark.run_paths(klippy_uds="fake_uds")
@pytest.mark.asyncio
async def test_no_klippy_connection_error(full_server: Server):
    await full_server.start_server()
    with pytest.raises(ServerError):
        kapis = full_server.klippy_connection.klippy_apis
        await kapis.run_gcode("M115")

@pytest.mark.asyncio
async def test_status_update(ready_server: Server, klippy: KlippyProcess):
    evtloop = ready_server.get_event_loop()
    fut = evtloop.create_future()

    def on_status_update(data):
        if not fut.done():
            fut.set_result(data)
    ready_server.register_event_handler("server:status_update",
                                        on_status_update)
    kapis = ready_server.klippy_connection.klippy_apis
    await kapis.subscribe_objects({"toolhead": None})
    klippy.send_gcode("G28")
    await asyncio.wait_for(fut, 2.)
    assert isinstance(fut.result(), dict)

@pytest.mark.run_paths(printer_cfg="error_printer.cfg")
@pytest.mark.asyncio
async def test_klippy_error(ready_server: Server):
    kconn = ready_server.klippy_connection
    assert kconn.state == "error"

@pytest.mark.run_paths(printer_cfg="missing_reqs.cfg")
@pytest.mark.asyncio
async def test_missing_reqs(ready_server: Server):
    mreqs = sorted(ready_server.klippy_connection.missing_requirements)
    expected = ["display_status", "pause_resume", "virtual_sdcard"]
    assert mreqs == expected

@pytest.mark.asyncio
async def test_connection_close(full_server: Server):
    await full_server.start_server()
    # Test multiple close attempts, the second to enter
    # should wait and exit
    ret = full_server.klippy_connection.close(True)
    ret2 = full_server.klippy_connection.close(True)
    await asyncio.wait_for(asyncio.gather(ret, ret2), 4.)
    kconn = full_server.klippy_connection
    assert kconn.connection_task.cancelled()

@pytest.mark.asyncio
async def test_init_error(base_server: Server):
    base_server.server_running = True
    kconn = base_server.klippy_connection

    def mock_is_connected():
        return kconn.init_attempts < 3
    kconn.is_connected = mock_is_connected
    ret = await kconn._init_klippy_connection()
    assert ret is False

def test_connect_fail(base_server: Server):
    ret = base_server.klippy_connection.connect()
    assert ret.result() is False

@pytest.mark.asyncio
async def test_wait_connect_fail(base_server: Server):
    ret = await base_server.klippy_connection.wait_connected()
    assert ret is False

@pytest.mark.run_paths(klippy_uds="fake_uds")
@pytest.mark.asyncio
async def test_no_uds(base_server: Server):
    attempts = [1, 2, 3]

    def mock_is_running():
        attempts.pop(0)
        return len(attempts) > 0
    base_server.is_running = mock_is_running
    ret = await base_server.klippy_connection._do_connect()
    assert ret is False

@pytest.mark.run_paths(klippy_uds="fake_uds")
@pytest.mark.asyncio
async def test_no_uds_access(base_server: Server,
                             path_args: Dict[str, pathlib.Path]):
    attempts = [1, 2, 3]
    uds_path = path_args['klippy_uds_path']
    uds_path.write_text("test")
    uds_path.chmod(mode=222)

    def mock_is_running():
        attempts.pop(0)
        return len(attempts) > 0
    base_server.is_running = mock_is_running
    ret = await base_server.klippy_connection._do_connect()
    assert ret is False

@pytest.mark.asyncio
async def test_write_not_connected(base_server: Server):
    req = KlippyRequest("", {})
    kconn = base_server.klippy_connection
    await kconn._write_request(req)
    assert isinstance(req.response, ServerError)

@pytest.mark.asyncio
async def test_write_error(base_server: Server):
    req = KlippyRequest("", {})
    kconn = base_server.klippy_connection
    kconn.writer = MockWriter()
    await kconn._write_request(req)
    assert isinstance(req.response, ServerError)

@pytest.mark.asyncio
async def test_write_cancelled(base_server: Server):
    req = KlippyRequest("", {})
    kconn = base_server.klippy_connection
    kconn.writer = MockWriter(wait_drain=True)
    task = base_server.event_loop.create_task(kconn._write_request(req))
    base_server.event_loop.delay_callback(.01, task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task

@pytest.mark.asyncio
async def test_read_error(base_server: Server,
                          caplog: pytest.LogCaptureFixture):
    mock_reader = MockReader("raise_error")
    kconn = base_server.klippy_connection
    await kconn._read_stream(mock_reader)
    assert "Klippy Stream Read Error" == caplog.messages[-1]

@pytest.mark.asyncio
async def test_read_cancelled(base_server: Server):
    mock_reader = MockReader("wait")
    kconn = base_server.klippy_connection
    task = base_server.event_loop.create_task(
        kconn._read_stream(mock_reader))
    base_server.event_loop.delay_callback(.01, task.cancel)
    with pytest.raises(asyncio.CancelledError):
        await task

@pytest.mark.asyncio
async def test_read_decode_error(base_server: Server,
                                 caplog: pytest.LogCaptureFixture):
    mock_reader = MockReader()
    kconn = base_server.klippy_connection
    await kconn._read_stream(mock_reader)
    assert "Error processing Klippy Host Response:" in caplog.messages[-1]

def test_process_unknown_method(base_server: Server,
                                caplog: pytest.LogCaptureFixture):
    cmd = {"method": "test_unknown"}
    kconn = base_server.klippy_connection
    kconn._process_command(cmd)
    assert "Unknown method received: test_unknown" == caplog.messages[-1]

def test_process_unknown_request(base_server: Server,
                                 caplog: pytest.LogCaptureFixture):
    cmd = {"id": 4543}
    kconn = base_server.klippy_connection
    kconn._process_command(cmd)
    expected = f"No request matching request ID: 4543, response: {cmd}"
    assert expected == caplog.messages[-1]

def test_process_invalid_request(base_server: Server):
    req = KlippyRequest("", {})
    kconn = base_server.klippy_connection
    kconn.pending_requests[req.id] = req
    cmd = {"id": req.id}
    kconn._process_command(cmd)
    assert isinstance(req.response, ServerError)

# TODO: This can probably go in a class with test apis
@pytest.mark.asyncio
async def test_call_remote_method(base_server: Server,
                                  klippy: KlippyProcess):
    fut = base_server.get_event_loop().create_future()

    def method_test(result):
        fut.set_result(result)
    base_server.register_remote_method("moonraker_test", method_test)
    base_server.load_components()
    await base_server.server_init()
    ret = base_server.klippy_connection.wait_connected()
    await asyncio.wait_for(ret, 4.)
    klippy.send_gcode("TEST_REMOTE_METHOD")
    await fut
    await base_server._stop_server("terminate")
    assert fut.result() == "test"
