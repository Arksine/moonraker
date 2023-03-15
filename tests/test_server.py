from __future__ import annotations
import pytest
import pytest_asyncio
import asyncio
import socket
import pathlib
from collections import namedtuple

from moonraker.server import CORE_COMPONENTS, Server, API_VERSION
from moonraker.server import main as servermain
from moonraker.eventloop import EventLoop
from moonraker.utils import ServerError
from moonraker.confighelper import ConfigError
from moonraker.components.klippy_apis import KlippyAPI
from mocks import MockComponent, MockWebsocket

from typing import (
    TYPE_CHECKING,
    AsyncIterator,
    Dict,
    Optional
)

if TYPE_CHECKING:
    from fixtures import HttpClient, WebsocketClient

MockArgs = namedtuple('MockArgs', ["logfile", "nologfile", "configfile"])

@pytest.mark.run_paths(moonraker_conf="invalid_config.conf")
def test_invalid_config(path_args: Dict[str, pathlib.Path]):
    evtloop = EventLoop()
    args = {
        'config_file': str(path_args['moonraker.conf']),
        'log_file': "",
        'software_version': "moonraker-pytest"
    }
    with pytest.raises(ConfigError):
        Server(args, None, evtloop)

def test_config_and_log_warnings(path_args: Dict[str, pathlib.Path]):
    evtloop = EventLoop()
    args = {
        'config_file': str(path_args['moonraker.conf']),
        'log_file': "",
        'software_version': "moonraker-pytest",
        'log_warning': "Log Warning Test",
        'config_warning': "Config Warning Test"
    }
    expected = ["Log Warning Test", "Config Warning Test"]
    server = Server(args, None, evtloop)
    assert server.warnings == expected

@pytest.mark.run_paths(moonraker_conf="unparsed_server.conf")
@pytest.mark.asyncio
async def test_unparsed_config_items(full_server: Server):
    expected_warnings = [
        "Unparsed config section [machine unparsed] detected.",
        "Unparsed config option 'unknown_option: True' detected "
        "in section [server]."]
    warn_cnt = 0
    for warn in full_server.warnings:
        for expected in expected_warnings:
            if warn.startswith(expected):
                warn_cnt += 1
    assert warn_cnt == 2

@pytest.mark.run_paths(moonraker_log="moonraker.log")
@pytest.mark.asyncio
async def test_file_logger(base_server: Server,
                           path_args: Dict[str, pathlib.Path]):
    log_path = path_args.get("moonraker.log", None)
    assert log_path is not None and log_path.exists()

def test_signal_handler(base_server: Server,
                        event_loop: asyncio.AbstractEventLoop):
    base_server._handle_term_signal()
    event_loop.run_forever()
    assert base_server.exit_reason == "terminate"

class TestInstantiation:
    def test_running(self, base_server: Server):
        assert base_server.is_running() is False

    def test_app_args(self,
                      path_args: Dict[str, pathlib.Path],
                      base_server: Server):
        args = {
            'config_file': str(path_args['moonraker.conf']),
            'log_file': str(path_args.get("moonlog", "")),
            'software_version': "moonraker-pytest"
        }
        assert base_server.get_app_args() == args

    def test_api_version(self, base_server: Server):
        ver = base_server.get_api_version()
        assert ver == API_VERSION

    def test_pending_tasks(self, base_server: Server):
        loop = base_server.get_event_loop().aioloop
        assert len(asyncio.all_tasks(loop)) == 0

    def test_klippy_info(self, base_server: Server):
        assert base_server.get_klippy_info() == {}

    def test_klippy_state(self, base_server: Server):
        assert base_server.get_klippy_state() == "disconnected"

    def test_host_info(self, base_server: Server):
        hinfo = {
            'hostname': socket.gethostname(),
            'address': "0.0.0.0",
            'port': 7010,
            'ssl_port': 7011
        }
        assert base_server.get_host_info() == hinfo

    def test_klippy_connection(self, base_server: Server):
        assert base_server.klippy_connection.is_connected() is False

    def test_components(self, base_server: Server):
        key_list = sorted(list(base_server.components.keys()))
        assert key_list == [
            "application",
            "internal_transport",
            "klippy_connection",
            "websockets",
        ]

    def test_endpoint_registered(self, base_server: Server):
        app = base_server.moonraker_app
        assert "/server/info" in app.api_cache

    @pytest.mark.asyncio
    async def test_notification(self, base_server: Server):
        base_server.register_notification("test:test_event")
        fut = base_server.event_loop.create_future()
        wsm = base_server.lookup_component("websockets")
        wsm.websockets[1] = MockWebsocket(fut)
        base_server.send_event("test:test_event", "test")
        ret = await fut
        expected = {
            'jsonrpc': "2.0",
            'method': "notify_test_event",
            'params': ["test"]
        }
        assert expected == ret

class TestLoadComponent:
    def test_load_component_fail(self, base_server: Server):
        with pytest.raises(ServerError):
            base_server.load_component(
                base_server.config, "invalid_component")

    def test_failed_component_set(self, base_server: Server):
        assert "invalid_component" in base_server.failed_components

    def test_load_component_fail_with_default(self, base_server: Server):
        comp = base_server.load_component(
            base_server.config, "invalid_component", None)
        assert comp is None

    def test_lookup_failed(self, base_server: Server):
        with pytest.raises(ServerError):
            base_server.lookup_component("invalid_component")

    def test_lookup_failed_with_default(self, base_server: Server):
        comp = base_server.lookup_component("invalid_component", None)
        assert comp is None

    def test_load_component(self, base_server: Server):
        comp = base_server.load_component(base_server.config, "klippy_apis")
        assert isinstance(comp, KlippyAPI)

    def test_lookup_component(self, base_server: Server):
        comp = base_server.lookup_component('klippy_apis')
        assert isinstance(comp, KlippyAPI)

    def test_component_attr(self, base_server: Server):
        key_list = sorted(list(base_server.components.keys()))
        assert key_list == [
            "application",
            "internal_transport",
            "klippy_apis",
            "klippy_connection",
            "websockets",
        ]

class TestCoreServer:
    @pytest_asyncio.fixture(scope="class")
    async def core_server(self, base_server: Server) -> AsyncIterator[Server]:
        base_server.load_components()
        yield base_server
        await base_server._stop_server("terminate")

    def test_running(self, core_server: Server):
        assert core_server.is_running() is False

    def test_http_servers(self, core_server: Server):
        app = core_server.lookup_component("application")
        assert (
            app.http_server is None and
            app.secure_server is None
        )

    def test_warnings(self, core_server: Server):
        assert len(core_server.warnings) == 0

    def test_failed_components(self, core_server: Server):
        assert len(core_server.failed_components) == 0

    def test_lookup_components(self, core_server: Server):
        comps = []
        for comp_name in CORE_COMPONENTS:
            comps.append(core_server.lookup_component(comp_name, None))
        assert None not in comps

    def test_pending_tasks(self, core_server: Server):
        loop = core_server.get_event_loop().aioloop
        assert len(asyncio.all_tasks(loop)) == 0

    def test_register_component_fail(self, core_server: Server):
        with pytest.raises(ServerError):
            core_server.register_component("machine", object())

    def test_register_remote_method(self, core_server: Server):
        core_server.register_remote_method("moonraker_test", lambda: None)
        kconn = core_server.klippy_connection
        assert "moonraker_test" in kconn.remote_methods

    def test_register_method_exists(self, core_server: Server):
        with pytest.raises(ServerError):
            core_server.register_remote_method(
                "shutdown_machine", lambda: None)

class TestServerInit:
    def test_running(self, full_server: Server):
        assert full_server.is_running() is False

    def test_http_servers(self, full_server: Server):
        app = full_server.lookup_component("application")
        assert (
            app.http_server is None and
            app.secure_server is None
        )

    def test_warnings(self, full_server: Server):
        assert len(full_server.warnings) == 0

    def test_failed_components(self, full_server: Server):
        assert len(full_server.failed_components) == 0

    def test_lookup_components(self, full_server: Server):
        comps = []
        for comp_name in CORE_COMPONENTS:
            comps.append(full_server.lookup_component(comp_name, None))
        assert None not in comps

    def test_config_backup(self,
                           full_server: Server,
                           path_args: Dict[str, pathlib.Path]):
        cfg = path_args["config_path"].joinpath(".moonraker.conf.bkp")
        assert cfg.is_file()

class TestServerStart:
    @pytest_asyncio.fixture(scope="class")
    async def server(self, full_server: Server) -> Server:
        await full_server.start_server(connect_to_klippy=False)
        return full_server

    def test_running(self, server: Server):
        assert server.is_running() is True

    def test_http_servers(self, server: Server):
        app = server.lookup_component("application")
        assert (
            app.http_server is not None and
            app.secure_server is None
        )

@pytest.mark.run_paths(moonraker_conf="base_server_ssl.conf")
class TestSecureServerStart:
    @pytest_asyncio.fixture(scope="class")
    async def server(self, full_server: Server) -> Server:
        await full_server.start_server(connect_to_klippy=False)
        return full_server

    def test_running(self, server: Server):
        assert server.is_running() is True

    def test_http_servers(self, server: Server):
        app = server.lookup_component("application")
        assert (
            app.http_server is not None and
            app.secure_server is not None
        )

@pytest.mark.asyncio
async def test_component_init_error(base_server: Server):
    base_server.register_component("testcomp", MockComponent(err_init=True))
    await base_server.server_init(False)
    assert "testcomp" in base_server.failed_components

@pytest.mark.asyncio
async def test_component_exit_error(base_server: Server,
                                    caplog: pytest.LogCaptureFixture):
    base_server.register_component("testcomp", MockComponent(err_exit=True))
    await base_server._stop_server("terminate")
    expected = "Error executing 'on_exit()' for component: testcomp"
    assert expected in caplog.messages

@pytest.mark.asyncio
async def test_component_close_error(base_server: Server,
                                     caplog: pytest.LogCaptureFixture):
    base_server.register_component("testcomp", MockComponent(err_close=True))
    await base_server._stop_server("terminate")
    expected = "Error executing 'close()' for component: testcomp"
    assert expected in caplog.messages

def test_register_event(base_server: Server):
    def test_func():
        pass
    base_server.register_event_handler("test:my_test", test_func)
    assert base_server.events["test:my_test"] == [test_func]

def test_register_async_event(base_server: Server):
    async def test_func():
        pass
    base_server.register_event_handler("test:my_test", test_func)
    assert base_server.events["test:my_test"] == [test_func]

@pytest.mark.asyncio
async def test_send_event(full_server: Server):
    evtloop = full_server.get_event_loop()
    fut = evtloop.create_future()

    def test_func(arg):
        fut.set_result(arg)
    full_server.register_event_handler("test:my_test", test_func)
    full_server.send_event("test:my_test", "test")
    result = await fut
    assert result == "test"

@pytest.mark.asyncio
async def test_send_async_event(full_server: Server):
    evtloop = full_server.get_event_loop()
    fut = evtloop.create_future()

    async def test_func(arg):
        fut.set_result(arg)
    full_server.register_event_handler("test:my_test", test_func)
    full_server.send_event("test:my_test", "test")
    result = await fut
    assert result == "test"

@pytest.mark.asyncio
async def test_register_remote_method_running(full_server: Server):
    await full_server.start_server(connect_to_klippy=False)
    with pytest.raises(ServerError):
        full_server.register_remote_method(
            "moonraker_test", lambda: None)

@pytest.mark.usefixtures("event_loop")
def test_main(path_args: Dict[str, pathlib.Path],
              monkeypatch: pytest.MonkeyPatch,
              caplog: pytest.LogCaptureFixture):
    tries = [1]

    def mock_init(self: Server):
        reason = "terminate"
        if tries:
            reason = "restart"
            tries.pop(0)
        self.event_loop.delay_callback(.01, self._stop_server, reason)
    cfg_path = path_args["moonraker.conf"]
    args = MockArgs("", True, str(cfg_path))
    monkeypatch.setattr(Server, "server_init", mock_init)
    code: Optional[int] = None
    try:
        servermain(args)
    except SystemExit as e:
        code = e.code
    assert (
        code == 0 and
        "Attempting Server Restart..." in caplog.messages and
        "Server Shutdown" == caplog.messages[-1]
    )

@pytest.mark.run_paths(moonraker_conf="invalid_config.conf")
def test_main_config_error(path_args: Dict[str, pathlib.Path],
                           caplog: pytest.LogCaptureFixture):
    cfg_path = path_args["moonraker.conf"]
    args = MockArgs("", True, str(cfg_path))
    try:
        servermain(args)
    except SystemExit as e:
        code = e.code
    assert code == 1 and "Server Config Error" in caplog.messages

@pytest.mark.run_paths(moonraker_conf="invalid_config.conf",
                       moonraker_bkp=".moonraker.conf.bkp")
@pytest.mark.usefixtures("event_loop")
def test_main_restore_config(path_args: Dict[str, pathlib.Path],
                             monkeypatch: pytest.MonkeyPatch,
                             caplog: pytest.LogCaptureFixture):
    def mock_init(self: Server):
        reason = "terminate"
        self.event_loop.delay_callback(.01, self._stop_server, reason)

    cfg_path = path_args["moonraker.conf"]
    args = MockArgs("", True, str(cfg_path))
    monkeypatch.setattr(Server, "server_init", mock_init)
    code: Optional[int] = None
    try:
        servermain(args)
    except SystemExit as e:
        code = e.code
    assert (
        code == 0 and
        "Loaded server from most recent working configuration:" in caplog.text
    )

class TestEndpoints:
    @pytest_asyncio.fixture(scope="class")
    async def server(self, full_server: Server):
        await full_server.start_server()
        yield full_server

    @pytest.mark.asyncio
    async def test_http_server_info(self,
                                    server: Server,
                                    http_client: HttpClient):
        ret = await http_client.get("/server/info")
        comps = list(server.components.keys())
        expected = {
            'klippy_connected': False,
            'klippy_state': "disconnected",
            'components': comps,
            'failed_components': [],
            'registered_directories': ["config", "logs"],
            'warnings': [],
            'websocket_count': 0,
            'moonraker_version': "moonraker-pytest",
            'missing_klippy_requirements': [],
            'api_version': list(API_VERSION),
            'api_version_string': ".".join(str(v) for v in API_VERSION)
        }
        assert ret["result"] == expected

    @pytest.mark.asyncio
    async def test_http_server_config(self,
                                      server: Server,
                                      http_client: HttpClient):
        cfg = server.config.get_parsed_config()
        ret = await http_client.get("/server/config")
        assert ret["result"]["config"] == cfg

    @pytest.mark.asyncio
    async def test_websocket_server_info(self,
                                         server: Server,
                                         websocket_client: WebsocketClient):
        ret = await websocket_client.request("server.info")
        comps = list(server.components.keys())
        expected = {
            'klippy_connected': False,
            'klippy_state': "disconnected",
            'components': comps,
            'failed_components': [],
            'registered_directories': ["config", "logs"],
            'warnings': [],
            'websocket_count': 1,
            'moonraker_version': "moonraker-pytest",
            'missing_klippy_requirements': [],
            'api_version': list(API_VERSION),
            'api_version_string': ".".join(str(v) for v in API_VERSION)
        }
        assert ret == expected

    @pytest.mark.asyncio
    async def test_websocket_server_config(self,
                                           server: Server,
                                           websocket_client: WebsocketClient):
        cfg = server.config.get_parsed_config()
        ret = await websocket_client.request("server.config")
        assert ret["config"] == cfg

def test_server_restart(base_server: Server,
                        http_client: HttpClient,
                        event_loop: asyncio.AbstractEventLoop):
    result = {}

    async def do_restart():
        base_server.load_components()
        await base_server.start_server()
        ret = await http_client.post("/server/restart")
        result.update(ret)
    event_loop.create_task(do_restart())
    event_loop.run_forever()
    assert result["result"] == "ok" and base_server.exit_reason == "restart"

@pytest.mark.no_ws_connect
def test_websocket_restart(base_server: Server,
                           websocket_client: WebsocketClient,
                           event_loop: asyncio.AbstractEventLoop):
    result = {}

    async def do_restart():
        base_server.load_components()
        await base_server.start_server()
        await websocket_client.connect()
        ret = await websocket_client.request("server.restart")
        result["result"] = ret
    event_loop.create_task(do_restart())
    event_loop.run_forever()
    assert result["result"] == "ok" and base_server.exit_reason == "restart"


# TODO:
# test invalid cert, key (probably should do that in test_app.py)
