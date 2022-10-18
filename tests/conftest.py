from __future__ import annotations
import pytest
import pytest_asyncio
import asyncio
import shutil
import re
import pathlib
import sys
import shlex
import tempfile
import subprocess
from typing import Iterator, Dict, AsyncIterator, Any
from moonraker.server import Server
from moonraker.eventloop import EventLoop
from moonraker import utils
import dbtool
from fixtures import KlippyProcess, HttpClient, WebsocketClient

ASSETS = pathlib.Path(__file__).parent.joinpath("assets")

need_klippy_restart = pytest.StashKey[bool]()

def pytest_addoption(parser: pytest.Parser, pluginmanager):
    parser.addoption("--klipper-path", action="store", dest="klipper_path")
    parser.addoption("--klipper-exec", action="store", dest="klipper_exec")

def interpolate_config(source_path: pathlib.Path,
                       dest_path: pathlib.Path,
                       keys: Dict[str, Any]
                       ) -> None:
    def interp(match):
        return str(keys[match.group(1)])
    sub_data = re.sub(r"\${([^}]+)}", interp, source_path.read_text())
    dest_path.write_text(sub_data)

@pytest.fixture(scope="session", autouse=True)
def ssl_certs() -> Iterator[Dict[str, pathlib.Path]]:
    with tempfile.TemporaryDirectory(prefix="moonraker-certs-") as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        cert_path = tmp_path.joinpath("certificate.pem")
        key_path = tmp_path.joinpath("privkey.pem")
        cmd = (
            f"openssl req -newkey rsa:4096 -nodes -keyout {key_path} "
            f"-x509 -days 365 -out {cert_path} -sha256 "
            "-subj '/C=US/ST=NRW/L=Earth/O=Moonraker/OU=IT/"
            "CN=www.moonraker-test.com/emailAddress=mail@moonraker-test.com'"
        )
        args = shlex.split(cmd)
        subprocess.run(args, check=True)
        yield {
            "ssl_certificate_path": cert_path,
            "ssl_key_path": key_path,
        }

@pytest.fixture(scope="class")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def session_args(ssl_certs: Dict[str, pathlib.Path]
                 ) -> Iterator[Dict[str, pathlib.Path]]:
    mconf_asset = ASSETS.joinpath(f"moonraker/base_server.conf")
    secrets_asset = ASSETS.joinpath(f"moonraker/secrets.ini")
    pcfg_asset = ASSETS.joinpath(f"klipper/base_printer.cfg")
    with tempfile.TemporaryDirectory(prefix="moonraker-test") as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        secrets_dest = tmp_path.joinpath("secrets.ini")
        shutil.copy(secrets_asset, secrets_dest)
        cfg_path = tmp_path.joinpath("config")
        cfg_path.mkdir()
        log_path = tmp_path.joinpath("logs")
        log_path.mkdir()
        db_path = tmp_path.joinpath("database")
        db_path.mkdir()
        gcode_path = tmp_path.joinpath("gcode_files")
        gcode_path.mkdir()
        dest_paths = {
            "temp_path": tmp_path,
            "asset_path": ASSETS,
            "config_path": cfg_path,
            "database_path": db_path,
            "log_path": log_path,
            "gcode_path": gcode_path,
            "secrets_path": secrets_dest,
            "klippy_uds_path": tmp_path.joinpath("klippy_uds"),
            "klippy_pty_path": tmp_path.joinpath("klippy_pty"),
            "klipper.dict": ASSETS.joinpath("klipper/klipper.dict"),
            "mconf_asset": mconf_asset,
            "pcfg_asset": pcfg_asset,
        }
        dest_paths.update(ssl_certs)
        mconf_dest = cfg_path.joinpath("moonraker.conf")
        dest_paths["moonraker.conf"] = mconf_dest
        interpolate_config(mconf_asset, mconf_dest, dest_paths)
        pcfg_dest = cfg_path.joinpath("printer.cfg")
        dest_paths["printer.cfg"] = pcfg_dest
        interpolate_config(pcfg_asset, pcfg_dest, dest_paths)
        yield dest_paths

@pytest.fixture(scope="session")
def klippy_session(session_args: Dict[str, pathlib.Path],
                   pytestconfig: pytest.Config) -> Iterator[KlippyProcess]:
    pytestconfig.stash[need_klippy_restart] = False
    kpath = pytestconfig.getoption('klipper_path', "~/klipper")
    kexec = pytestconfig.getoption('klipper_exec', None)
    if kexec is None:
        kexec = sys.executable
    exec = pathlib.Path(kexec).expanduser()
    klipper_path = pathlib.Path(kpath).expanduser()
    base_cmd = f"{exec} {klipper_path}/klippy/klippy.py "
    kproc = KlippyProcess(base_cmd, session_args)
    kproc.start()
    yield kproc
    kproc.stop()

@pytest.fixture(scope="class")
def klippy(klippy_session: KlippyProcess,
           pytestconfig: pytest.Config):
    if pytestconfig.stash[need_klippy_restart]:
        pytestconfig.stash[need_klippy_restart] = False
        klippy_session.restart()
    return klippy_session

@pytest.fixture(scope="class")
def path_args(request: pytest.FixtureRequest,
              session_args: Dict[str, pathlib.Path],
              pytestconfig: pytest.Config
              ) -> Iterator[Dict[str, pathlib.Path]]:
    path_marker = request.node.get_closest_marker("run_paths")
    paths: Dict[str, Any] = {
        "moonraker_conf": "base_server.conf",
        "secrets": "secrets.ini",
        "printer_cfg": "base_printer.cfg",
        "klippy_uds": None,
    }
    if path_marker is not None:
        paths.update(path_marker.kwargs)
    tmp_path = session_args["temp_path"]
    cfg_path = session_args["config_path"]
    mconf_dest = session_args["moonraker.conf"]
    mconf_asset = ASSETS.joinpath(f"moonraker/{paths['moonraker_conf']}")
    pcfg_asset = ASSETS.joinpath(f"klipper/{paths['printer_cfg']}")
    last_uds = session_args["klippy_uds_path"]
    if paths["klippy_uds"] is not None:
        tmp_uds = tmp_path.joinpath(paths["klippy_uds"])
        session_args["klippy_uds_path"] = tmp_uds
    if (
        not mconf_asset.samefile(session_args["mconf_asset"]) or
        paths["klippy_uds"] is not None
    ):
        session_args['mconf_asset'] = mconf_asset
        interpolate_config(mconf_asset, mconf_dest, session_args)
    if not pcfg_asset.samefile(session_args["pcfg_asset"]):
        pcfg_dest = session_args["printer.cfg"]
        session_args["pcfg_asset"] = pcfg_asset
        interpolate_config(pcfg_asset, pcfg_dest, session_args)
        pytestconfig.stash[need_klippy_restart] = True
    if paths["secrets"] != session_args["secrets_path"].name:
        secrets_asset = ASSETS.joinpath(f"moonraker/{paths['secrets']}")
        secrets_dest = tmp_path.joinpath(paths['secrets'])
        shutil.copy(secrets_asset, secrets_dest)
        session_args["secrets_path"] = secrets_dest
    if "moonraker_log" in paths:
        log_path = session_args["log_path"]
        session_args['moonraker.log'] = log_path.joinpath(
            paths["moonraker_log"])
    bkp_dest: pathlib.Path = cfg_path.joinpath(".moonraker.conf.bkp")
    if "moonraker_bkp" in paths:
        bkp_source = ASSETS.joinpath("moonraker/base_server.conf")
        bkp_dest = cfg_path.joinpath(paths["moonraker_bkp"])
        interpolate_config(bkp_source, bkp_dest, session_args)
    if "database" in paths:
        db_source = ASSETS.joinpath(f"moonraker/{paths['database']}")
        db_dest = session_args["database_path"]
        db_args = {"input": str(db_source), "destination": db_dest}
        dbtool.restore(db_args)
    yield session_args
    log = session_args.pop("moonraker.log", None)
    if log is not None and log.is_file():
        log.unlink()
    if bkp_dest.is_file():
        bkp_dest.unlink()
    for item in session_args["database_path"].iterdir():
        if item.is_file():
            item.unlink()
    session_args["klippy_uds_path"] = last_uds
    if paths["klippy_uds"] is not None:
        # restore the original uds path
        interpolate_config(mconf_asset, mconf_dest, session_args)

@pytest.fixture(scope="class")
def base_server(path_args: Dict[str, pathlib.Path],
                event_loop: asyncio.AbstractEventLoop
                ) -> Iterator[Server]:
    evtloop = EventLoop()
    args = {
        'config_file': str(path_args['moonraker.conf']),
        'log_file': str(path_args.get("moonraker.log", "")),
        'software_version': "moonraker-pytest"
    }
    ql = logger = None
    if args["log_file"]:
        ql, logger, warning = utils.setup_logging(args)
        if warning:
            args["log_warning"] = warning
    yield Server(args, logger, evtloop)
    if ql is not None:
        ql.stop()

@pytest_asyncio.fixture(scope="class")
async def full_server(base_server: Server) -> AsyncIterator[Server]:
    base_server.load_components()
    ret = base_server.server_init(start_server=False)
    await asyncio.wait_for(ret, 4.)
    yield base_server
    if base_server.event_loop.aioloop.is_running():
        await base_server._stop_server(exit_reason="terminate")

@pytest_asyncio.fixture(scope="class")
async def ready_server(full_server: Server, klippy: KlippyProcess):
    ret = full_server.start_server(connect_to_klippy=False)
    await asyncio.wait_for(ret, 4.)
    ret = full_server.klippy_connection.connect()
    await asyncio.wait_for(ret, 4.)
    yield full_server

@pytest_asyncio.fixture(scope="class")
async def http_client() -> AsyncIterator[HttpClient]:
    client = HttpClient()
    yield client
    client.close()

@pytest_asyncio.fixture(scope="class")
async def websocket_client(request: pytest.FixtureRequest
                           ) -> AsyncIterator[WebsocketClient]:
    conn_marker = request.node.get_closest_marker("no_ws_connect")
    client = WebsocketClient()
    if conn_marker is None:
        await client.connect()
    yield client
    client.close()
