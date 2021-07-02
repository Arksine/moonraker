# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import pathlib
import logging
import json
import sys
import shutil
import zipfile
import io
import time
import tempfile
import tornado.gen
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.httpclient import AsyncHTTPClient
from tornado.locks import Event, Condition, Lock
from .base_deploy import BaseDeploy
from .app_deploy import AppDeploy
from .git_deploy import GitDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Type,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from tornado.httpclient import HTTPResponse
    from moonraker import Server
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from components import klippy_apis
    from components import shell_command
    from components import database
    APIComp = klippy_apis.KlippyAPI
    SCMDComp = shell_command.ShellCommandFactory
    DBComp = database.MoonrakerDatabase

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../../.."))
SUPPLEMENTAL_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "update_manager.conf")
KLIPPER_DEFAULT_PATH = os.path.expanduser("~/klipper")
KLIPPER_DEFAULT_EXEC = os.path.expanduser("~/klippy-env/bin/python")
APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"

# Check To see if Updates are necessary each hour
UPDATE_REFRESH_INTERVAL_MS = 3600000
# Perform auto refresh no sooner than 12 hours apart
MIN_REFRESH_TIME = 43200
# Perform auto refresh no later than 4am
MAX_PKG_UPDATE_HOUR = 4

def get_deploy_class(app_path: str) -> Type:
    if AppDeploy._is_git_repo(app_path):
        return GitDeploy
    else:
        # TODO: This will be Zip deploy after implementation
        return GitDeploy

class UpdateManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.app_config = config.read_supplemental_config(
            SUPPLEMENTAL_CFG_PATH)
        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        enable_sys_updates = config.get('enable_system_updates', True)
        self.channel = config.get('channel', "dev")
        self.cmd_helper = CommandHelper(config)
        self.updaters: Dict[str, BaseDeploy] = {}
        if enable_sys_updates:
            self.updaters['system'] = PackageDeploy(config, self.cmd_helper)
        if (
            os.path.exists(KLIPPER_DEFAULT_PATH) and
            os.path.exists(KLIPPER_DEFAULT_EXEC)
        ):
            self.updaters['klipper'] = get_deploy_class(KLIPPER_DEFAULT_PATH)(
                self.app_config[f"update_manager klipper"], self.cmd_helper,
                {
                    'channel': self.channel,
                    'path': KLIPPER_DEFAULT_PATH,
                    'executable': KLIPPER_DEFAULT_EXEC
                })
        else:
            self.updaters['klipper'] = BaseDeploy(
                self.app_config[f"update_manager klipper"], self.cmd_helper)
        self.updaters['moonraker'] = get_deploy_class(MOONRAKER_PATH)(
            self.app_config[f"update_manager moonraker"], self.cmd_helper,
            {
                'channel': self.channel,
                'path': MOONRAKER_PATH,
                'executable': sys.executable
            })

        # TODO: The below check may be removed when invalid config options
        # raise a config error.
        if config.get("client_repo", None) is not None or \
                config.get('client_path', None) is not None:
            raise config.error(
                "The deprecated 'client_repo' and 'client_path' options\n"
                "have been removed.  See Moonraker's configuration docs\n"
                "for details on client configuration.")
        client_sections = config.get_prefix_sections("update_manager ")
        for section in client_sections:
            cfg = config[section]
            name = section.split()[-1]
            if name in self.updaters:
                raise config.error(f"Client repo {name} already added")
            client_type = cfg.get("type")
            if client_type == "git_repo":
                self.updaters[name] = GitDeploy(cfg, self.cmd_helper)
            elif client_type == "web":
                self.updaters[name] = WebClientDeploy(cfg, self.cmd_helper)
            else:
                raise config.error(
                    f"Invalid type '{client_type}' for section [{section}]")

        self.cmd_request_lock = Lock()
        self.initialized_lock = Event()
        self.is_refreshing: bool = False

        # Auto Status Refresh
        self.last_auto_update_time: float = 0
        self.refresh_cb: Optional[PeriodicCallback] = None
        if auto_refresh_enabled:
            self.refresh_cb = PeriodicCallback(
                self._handle_auto_refresh,  # type: ignore
                UPDATE_REFRESH_INTERVAL_MS)
            self.refresh_cb.start()

        self.server.register_endpoint(
            "/machine/update/moonraker", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/klipper", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/system", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/client", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/status", ["GET"],
            self._handle_status_request)
        self.server.register_endpoint(
            "/machine/update/recover", ["POST"],
            self._handle_repo_recovery)
        self.server.register_notification("update_manager:update_response")
        self.server.register_notification("update_manager:update_refreshed")

        # Register Ready Event
        self.server.register_event_handler(
            "server:klippy_identified", self._set_klipper_repo)
        # Initialize GitHub API Rate Limits and configured updaters
        IOLoop.current().spawn_callback(
            self._initalize_updaters, list(self.updaters.values()))

    async def _initalize_updaters(self,
                                  initial_updaters: List[BaseDeploy]
                                  ) -> None:
        async with self.cmd_request_lock:
            self.is_refreshing = True
            await self.cmd_helper.init_api_rate_limit()
            for updater in initial_updaters:
                if isinstance(updater, PackageDeploy):
                    ret = updater.refresh(False)
                else:
                    ret = updater.refresh()
                await ret
            self.is_refreshing = False
        self.initialized_lock.set()

    async def _set_klipper_repo(self) -> None:
        kinfo = self.server.get_klippy_info()
        if not kinfo:
            logging.info("No valid klippy info received")
            return
        kpath: str = kinfo['klipper_path']
        executable: str = kinfo['python_path']
        kupdater = self.updaters.get('klipper')
        if (
            isinstance(kupdater, AppDeploy) and
            kupdater.check_same_paths(kpath, executable)
        ):
            # Current Klipper Updater is valid
            return
        need_notification = not isinstance(kupdater, AppDeploy)
        self.updaters['klipper'] = get_deploy_class(kpath)(
            self.app_config[f"update_manager klipper"], self.cmd_helper,
            {
                'channel': self.channel,
                'path': kpath,
                'executable': executable
            })
        async with self.cmd_request_lock:
            await self.updaters['klipper'].refresh()
        if need_notification:
            vinfo: Dict[str, Any] = {}
            for name, updater in self.updaters.items():
                vinfo[name] = updater.get_update_status()
            uinfo = self.cmd_helper.get_rate_limit_stats()
            uinfo['version_info'] = vinfo
            uinfo['busy'] = self.cmd_helper.is_update_busy()
            self.server.send_event("update_manager:update_refreshed", uinfo)

    async def _check_klippy_printing(self) -> bool:
        kapi: APIComp = self.server.lookup_component('klippy_apis')
        result: Dict[str, Any] = await kapi.query_objects(
            {'print_stats': None}, default={})
        pstate: str = result.get('print_stats', {}).get('state', "")
        return pstate.lower() == "printing"

    async def _handle_auto_refresh(self) -> None:
        if await self._check_klippy_printing():
            # Don't Refresh during a print
            logging.info("Klippy is printing, auto refresh aborted")
            return
        cur_time = time.time()
        cur_hour = time.localtime(cur_time).tm_hour
        time_diff = cur_time - self.last_auto_update_time
        # Update packages if it has been more than 12 hours
        # and the local time is between 12AM and 5AM
        if time_diff < MIN_REFRESH_TIME or cur_hour >= MAX_PKG_UPDATE_HOUR:
            # Not within the update time window
            return
        self.last_auto_update_time = cur_time
        vinfo: Dict[str, Any] = {}
        need_refresh_all = not self.is_refreshing
        async with self.cmd_request_lock:
            self.is_refreshing = True
            try:
                for name, updater in list(self.updaters.items()):
                    if need_refresh_all:
                        await updater.refresh()
                    vinfo[name] = updater.get_update_status()
            except Exception:
                logging.exception("Unable to Refresh Status")
                return
            finally:
                self.is_refreshing = False
        uinfo = self.cmd_helper.get_rate_limit_stats()
        uinfo['version_info'] = vinfo
        uinfo['busy'] = self.cmd_helper.is_update_busy()
        self.server.send_event("update_manager:update_refreshed", uinfo)

    async def _handle_update_request(self,
                                     web_request: WebRequest
                                     ) -> str:
        await self.initialized_lock.wait()
        if await self._check_klippy_printing():
            raise self.server.error("Update Refused: Klippy is printing")
        app: str = web_request.get_endpoint().split("/")[-1]
        if app == "client":
            app = web_request.get('name')
        if self.cmd_helper.is_app_updating(app):
            return f"Object {app} is currently being updated"
        updater = self.updaters.get(app, None)
        if updater is None:
            raise self.server.error(f"Updater {app} not available", 404)
        async with self.cmd_request_lock:
            self.cmd_helper.set_update_info(app, id(web_request))
            try:
                await updater.update()
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Error updating {app}")
                self.cmd_helper.notify_update_response(
                    str(e), is_complete=True)
                raise
            finally:
                self.cmd_helper.clear_update_info()
        return "ok"

    async def _handle_status_request(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, Any]:
        await self.initialized_lock.wait()
        check_refresh = web_request.get_boolean('refresh', False)
        # Don't refresh if a print is currently in progress or
        # if an update is in progress.  Just return the current
        # state
        if self.cmd_helper.is_update_busy() or \
                await self._check_klippy_printing():
            check_refresh = False
        need_refresh = False
        if check_refresh:
            # If there is an outstanding request processing a
            # refresh, we don't need to do it again.
            need_refresh = not self.is_refreshing
            await self.cmd_request_lock.acquire()
            self.is_refreshing = True
        vinfo: Dict[str, Any] = {}
        try:
            for name, updater in list(self.updaters.items()):
                if need_refresh:
                    await updater.refresh()
                vinfo[name] = updater.get_update_status()
        except Exception:
            raise
        finally:
            if check_refresh:
                self.is_refreshing = False
                self.cmd_request_lock.release()
        ret = self.cmd_helper.get_rate_limit_stats()
        ret['version_info'] = vinfo
        ret['busy'] = self.cmd_helper.is_update_busy()
        return ret

    async def _handle_repo_recovery(self,
                                    web_request: WebRequest
                                    ) -> str:
        await self.initialized_lock.wait()
        if await self._check_klippy_printing():
            raise self.server.error(
                "Recovery Attempt Refused: Klippy is printing")
        app: str = web_request.get_str('name')
        hard = web_request.get_boolean("hard", False)
        update_deps = web_request.get_boolean("update_deps", False)
        updater = self.updaters.get(app, None)
        if updater is None:
            raise self.server.error(f"Updater {app} not available", 404)
        elif not isinstance(updater, GitDeploy):
            raise self.server.error(f"Upater {app} is not a Git Repo Type")
        async with self.cmd_request_lock:
            self.cmd_helper.set_update_info(f"recover_{app}", id(web_request))
            try:
                await updater.recover(hard, update_deps)
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Error Recovering {app}")
                self.cmd_helper.notify_update_response(
                    str(e), is_complete=True)
                raise
            finally:
                self.cmd_helper.clear_update_info()
        return "ok"

    def close(self) -> None:
        self.cmd_helper.close()
        if self.refresh_cb is not None:
            self.refresh_cb.stop()

class CommandHelper:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.debug_enabled = config.getboolean('enable_repo_debug', False)
        if self.debug_enabled:
            logging.warning("UPDATE MANAGER: REPO DEBUG ENABLED")
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        self.scmd_error = shell_cmd.error
        self.build_shell_command = shell_cmd.build_shell_command

        AsyncHTTPClient.configure(None, defaults=dict(user_agent="Moonraker"))
        self.http_client = AsyncHTTPClient()

        # GitHub API Rate Limit Tracking
        self.gh_rate_limit: Optional[int] = None
        self.gh_limit_remaining: Optional[int] = None
        self.gh_limit_reset_time: Optional[float] = None

        # Update In Progress Tracking
        self.cur_update_app: Optional[str] = None
        self.cur_update_id: Optional[int] = None

    def get_server(self) -> Server:
        return self.server

    def is_debug_enabled(self) -> bool:
        return self.debug_enabled

    def set_update_info(self, app: str, uid: int) -> None:
        self.cur_update_app = app
        self.cur_update_id = uid

    def clear_update_info(self) -> None:
        self.cur_update_app = self.cur_update_id = None

    def is_app_updating(self, app_name: str) -> bool:
        return self.cur_update_app == app_name

    def is_update_busy(self) -> bool:
        return self.cur_update_app is not None

    def get_rate_limit_stats(self) -> Dict[str, Any]:
        return {
            'github_rate_limit': self.gh_rate_limit,
            'github_requests_remaining': self.gh_limit_remaining,
            'github_limit_reset_time': self.gh_limit_reset_time,
        }

    async def init_api_rate_limit(self) -> None:
        url = "https://api.github.com/rate_limit"
        while 1:
            try:
                resp = await self.github_api_request(url, is_init=True)
                assert resp is not None
                core = resp['resources']['core']
                self.gh_rate_limit = core['limit']
                self.gh_limit_remaining = core['remaining']
                self.gh_limit_reset_time = core['reset']
            except Exception:
                logging.exception("Error Initializing GitHub API Rate Limit")
                await tornado.gen.sleep(30.)
            else:
                reset_time = time.ctime(self.gh_limit_reset_time)
                logging.info(
                    "GitHub API Rate Limit Initialized\n"
                    f"Rate Limit: {self.gh_rate_limit}\n"
                    f"Rate Limit Remaining: {self.gh_limit_remaining}\n"
                    f"Rate Limit Reset Time: {reset_time}, "
                    f"Seconds Since Epoch: {self.gh_limit_reset_time}")
                break

    async def run_cmd(self,
                      cmd: str,
                      timeout: float = 20.,
                      notify: bool = False,
                      retries: int = 1,
                      env: Optional[Dict[str, str]] = None,
                      cwd: Optional[str] = None,
                      sig_idx: int = 1
                      ) -> None:
        cb = self.notify_update_response if notify else None
        scmd = self.build_shell_command(cmd, callback=cb, env=env, cwd=cwd)
        while retries:
            if await scmd.run(timeout=timeout, sig_idx=sig_idx):
                break
            retries -= 1
        if not retries:
            raise self.server.error("Shell Command Error")

    async def run_cmd_with_response(self,
                                    cmd: str,
                                    timeout: float = 20.,
                                    retries: int = 5,
                                    env: Optional[Dict[str, str]] = None,
                                    cwd: Optional[str] = None,
                                    sig_idx: int = 1
                                    ) -> str:
        scmd = self.build_shell_command(cmd, None, env=env, cwd=cwd)
        result = await scmd.run_with_response(timeout, retries,
                                              sig_idx=sig_idx)
        return result

    async def github_api_request(self,
                                 url: str,
                                 etag: Optional[str] = None,
                                 is_init: Optional[bool] = False
                                 ) -> Optional[Dict[str, Any]]:
        if self.gh_limit_remaining == 0:
            curtime = time.time()
            assert self.gh_limit_reset_time is not None
            if curtime < self.gh_limit_reset_time:
                raise self.server.error(
                    f"GitHub Rate Limit Reached\nRequest: {url}\n"
                    f"Limit Reset Time: {time.ctime(self.gh_limit_remaining)}")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if etag is not None:
            headers['If-None-Match'] = etag
        retries = 5
        while retries:
            try:
                timeout = time.time() + 10.
                fut = self.http_client.fetch(
                    url, headers=headers, connect_timeout=5.,
                    request_timeout=5., raise_error=False)
                resp: HTTPResponse
                resp = await tornado.gen.with_timeout(timeout, fut)
            except Exception:
                retries -= 1
                if retries > 0:
                    logging.exception(
                        f"Error Processing GitHub API request: {url}")
                    await tornado.gen.sleep(1.)
                continue
            etag = resp.headers.get('etag', None)
            if etag is not None:
                if etag[:2] == "W/":
                    etag = etag[2:]
            logging.info(
                "GitHub API Request Processed\n"
                f"URL: {url}\n"
                f"Response Code: {resp.code}\n"
                f"Response Reason: {resp.reason}\n"
                f"ETag: {etag}")
            if resp.code == 403:
                raise self.server.error(
                    f"Forbidden GitHub Request: {resp.reason}")
            elif resp.code == 304:
                logging.info(f"Github Request not Modified: {url}")
                return None
            if resp.code != 200:
                retries -= 1
                if not retries:
                    raise self.server.error(
                        f"Github Request failed: {resp.code} {resp.reason}")
                logging.info(
                    f"Github request error, {retries} retries remaining")
                await tornado.gen.sleep(1.)
                continue
            # Update rate limit on return success
            if 'X-Ratelimit-Limit' in resp.headers and not is_init:
                self.gh_rate_limit = int(resp.headers['X-Ratelimit-Limit'])
                self.gh_limit_remaining = int(
                    resp.headers['X-Ratelimit-Remaining'])
                self.gh_limit_reset_time = float(
                    resp.headers['X-Ratelimit-Reset'])
            decoded = json.loads(resp.body)
            decoded['etag'] = etag
            return decoded
        raise self.server.error(
            f"Retries exceeded for GitHub API request: {url}")

    async def http_download_request(self, url: str) -> bytes:
        retries = 5
        while retries:
            try:
                timeout = time.time() + 130.
                fut = self.http_client.fetch(
                    url, headers={"Accept": "application/zip"},
                    connect_timeout=5., request_timeout=120.)
                resp: HTTPResponse
                resp = await tornado.gen.with_timeout(timeout, fut)
            except Exception:
                retries -= 1
                logging.exception("Error Processing Download")
                if not retries:
                    raise
                await tornado.gen.sleep(1.)
                continue
            return resp.body
        raise self.server.error(
            f"Retries exceeded for GitHub API request: {url}")

    def notify_update_response(self,
                               resp: Union[str, bytes],
                               is_complete: bool = False
                               ) -> None:
        if self.cur_update_app is None:
            return
        resp = resp.strip()
        if isinstance(resp, bytes):
            resp = resp.decode()
        notification = {
            'message': resp,
            'application': self.cur_update_app,
            'proc_id': self.cur_update_id,
            'complete': is_complete}
        self.server.send_event(
            "update_manager:update_response", notification)

    def get_system_update_command(self):
        return APT_CMD

    def close(self) -> None:
        self.http_client.close()

class PackageDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper)
        self.available_packages: List[str] = []
        self.refresh_condition: Optional[Condition] = None

    async def refresh(self, fetch_packages: bool = True) -> None:
        # TODO: Use python-apt python lib rather than command line for updates
        if self.refresh_condition is None:
            self.refresh_condition = Condition()
        else:
            self.refresh_condition.wait()
            return
        try:
            if fetch_packages:
                await self.cmd_helper.run_cmd(
                    f"{APT_CMD} update", timeout=300., retries=3)
            res = await self.cmd_helper.run_cmd_with_response(
                "apt list --upgradable", timeout=60.)
            pkg_list = [p.strip() for p in res.split("\n") if p.strip()]
            if pkg_list:
                pkg_list = pkg_list[2:]
                self.available_packages = [p.split("/", maxsplit=1)[0]
                                           for p in pkg_list]
            pkg_msg = "\n".join(self.available_packages)
            logging.info(
                f"Detected {len(self.available_packages)} package updates:"
                f"\n{pkg_msg}")
        except Exception:
            logging.exception("Error Refreshing System Packages")
        self.refresh_condition.notify_all()
        self.refresh_condition = None

    async def update(self) -> None:
        if self.refresh_condition is not None:
            self.refresh_condition.wait()
        self.cmd_helper.notify_update_response("Updating packages...")
        try:
            await self.cmd_helper.run_cmd(
                f"{APT_CMD} update", timeout=300., notify=True)
            await self.cmd_helper.run_cmd(
                f"{APT_CMD} upgrade --yes", timeout=3600., notify=True)
        except Exception:
            raise self.server.error("Error updating system packages")
        self.available_packages = []
        self.cmd_helper.notify_update_response("Package update finished...",
                                               is_complete=True)

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'package_count': len(self.available_packages),
            'package_list': self.available_packages
        }

class WebClientDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper)
        self.repo = config.get('repo').strip().strip("/")
        self.owner = self.repo.split("/", 1)[0]
        self.path = pathlib.Path(config.get("path")).expanduser().resolve()
        self.persistent_files: List[str] = []
        pfiles = config.get('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip().strip("/") for pf in
                                     pfiles.split("\n") if pf.strip()]
            if ".version" in self.persistent_files:
                raise config.error(
                    "Invalid value for option 'persistent_files': "
                    "'.version' can not be persistent")

        self.version: str = "?"
        self.remote_version: str = "?"
        self.dl_url: str = "?"
        self.etag: Optional[str] = None
        self.refresh_condition: Optional[Condition] = None
        self._get_local_version()
        logging.info(f"\nInitializing Client Updater: '{self.name}',"
                     f"\nversion: {self.version}"
                     f"\npath: {self.path}")

    def _get_local_version(self) -> None:
        version_path = self.path.joinpath(".version")
        if version_path.is_file():
            self.version = version_path.read_text().strip()

    async def refresh(self) -> None:
        if self.refresh_condition is None:
            self.refresh_condition = Condition()
        else:
            self.refresh_condition.wait()
            return
        try:
            self._get_local_version()
            await self._get_remote_version()
        except Exception:
            logging.exception("Error Refreshing Client")
        self.refresh_condition.notify_all()
        self.refresh_condition = None

    async def _get_remote_version(self) -> None:
        # Remote state
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            result = await self.cmd_helper.github_api_request(
                url, etag=self.etag)
        except Exception:
            logging.exception(f"Client {self.repo}: Github Request Error")
            result = {}
        if result is None:
            # No change, update not necessary
            return
        self.etag = result.get('etag', None)
        self.remote_version = result.get('name', "?")
        release_assets: Dict[str, Any] = result.get('assets', [{}])[0]
        self.dl_url = release_assets.get('browser_download_url', "?")
        logging.info(
            f"Github client Info Received:\nRepo: {self.name}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"url: {self.dl_url}")

    async def update(self) -> None:
        if self.refresh_condition is not None:
            # wait for refresh if in progess
            self.refresh_condition.wait()
        if self.remote_version == "?":
            await self.refresh()
            if self.remote_version == "?":
                raise self.server.error(
                    f"Client {self.repo}: Unable to locate update")
        if self.dl_url == "?":
            raise self.server.error(
                f"Client {self.repo}: Invalid download url")
        if self.version == self.remote_version:
            # Already up to date
            return
        self.cmd_helper.notify_update_response(
            f"Downloading Client: {self.name}")
        archive = await self.cmd_helper.http_download_request(self.dl_url)
        with tempfile.TemporaryDirectory(
                suffix=self.name, prefix="client") as tempdirname:
            tempdir = pathlib.Path(tempdirname)
            if self.path.is_dir():
                # find and move persistent files
                for fname in os.listdir(self.path):
                    src_path = self.path.joinpath(fname)
                    if fname in self.persistent_files:
                        dest_dir = tempdir.joinpath(fname).parent
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.move(src_path, dest_dir)
                shutil.rmtree(self.path)
            os.mkdir(self.path)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                zf.extractall(self.path)
            # Move temporary files back into
            for fname in os.listdir(tempdir):
                src_path = tempdir.joinpath(fname)
                dest_dir = self.path.joinpath(fname).parent
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src_path, dest_dir)
        self.version = self.remote_version
        version_path = self.path.joinpath(".version")
        if not version_path.exists():
            version_path.write_text(self.version)
        self.cmd_helper.notify_update_response(
            f"Client Update Finished: {self.name}", is_complete=True)

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'owner': self.owner,
            'version': self.version,
            'remote_version': self.remote_version
        }

def load_component(config: ConfigHelper) -> UpdateManager:
    return UpdateManager(config)
