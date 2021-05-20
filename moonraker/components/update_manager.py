# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import glob
import re
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

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
    Coroutine,
)
if TYPE_CHECKING:
    from tornado.httpclient import HTTPResponse
    from moonraker import Server
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from utils import ServerError
    from . import klippy_apis
    from . import shell_command
    from . import database
    APIComp = klippy_apis.KlippyAPI
    SCMDComp = shell_command.ShellCommandFactory
    DBComp = database.MoonrakerDatabase

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../.."))
SUPPLEMENTAL_CFG_PATH = os.path.join(
    MOONRAKER_PATH, "scripts/update_manager.conf")
APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"
SUPPORTED_DISTROS = ["debian"]

# Check To see if Updates are necessary each hour
UPDATE_REFRESH_INTERVAL_MS = 3600000
# Perform auto refresh no sooner than 12 hours apart
MIN_REFRESH_TIME = 43200
# Perform auto refresh no later than 4am
MAX_PKG_UPDATE_HOUR = 4

class UpdateManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.config = config
        self.config.read_supplemental_config(SUPPLEMENTAL_CFG_PATH)
        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        self.distro = config.get('distro', "debian").lower()
        if self.distro not in SUPPORTED_DISTROS:
            raise config.error(f"Unsupported distro: {self.distro}")
        self.cmd_helper = CommandHelper(config)
        env = sys.executable
        mooncfg = self.config[f"update_manager static {self.distro} moonraker"]
        self.updaters: Dict[str, BaseUpdater] = {
            "system": PackageUpdater(config, self.cmd_helper),
            "moonraker": GitUpdater(mooncfg, self.cmd_helper,
                                    MOONRAKER_PATH, env)
        }
        # TODO: The below check may be removed when invalid config options
        # raise a config error.
        if config.get("client_repo", None) is not None or \
                config.get('client_path', None) is not None:
            raise config.error(
                "The deprecated 'client_repo' and 'client_path' options\n"
                "have been removed.  See Moonraker's configuration docs\n"
                "for details on client configuration.")
        client_sections = self.config.get_prefix_sections(
            "update_manager client")
        for section in client_sections:
            cfg = self.config[section]
            name = section.split()[-1]
            if name in self.updaters:
                raise config.error("Client repo named %s already added"
                                   % (name,))
            client_type = cfg.get("type")
            if client_type == "git_repo":
                self.updaters[name] = GitUpdater(cfg, self.cmd_helper)
            elif client_type == "web":
                self.updaters[name] = WebUpdater(cfg, self.cmd_helper)
            else:
                raise config.error("Invalid type '%s' for section [%s]"
                                   % (client_type, section))

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
                                  initial_updaters: List[BaseUpdater]
                                  ) -> None:
        async with self.cmd_request_lock:
            self.is_refreshing = True
            await self.cmd_helper.init_api_rate_limit()
            for updater in initial_updaters:
                if isinstance(updater, PackageUpdater):
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
        env: str = kinfo['python_path']
        kupdater = self.updaters.get('klipper', None)
        if kupdater is not None:
            assert isinstance(kupdater, GitUpdater)
            if kupdater.repo_path == kpath and \
                    kupdater.env == env:
                # Current Klipper Updater is valid
                return
        kcfg = self.config[f"update_manager static {self.distro} klipper"]
        need_notification = "klipper" not in self.updaters
        self.updaters['klipper'] = GitUpdater(kcfg, self.cmd_helper, kpath, env)
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
        elif not isinstance(updater, GitUpdater):
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
            logging.warn("UPDATE MANAGER: REPO DEBUG ENABLED")
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

    def close(self) -> None:
        self.http_client.close()

class BaseUpdater:
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        self.server = config.get_server()
        self.cmd_helper = cmd_helper

    def refresh(self) -> Coroutine:
        raise NotImplementedError

    def update(self) -> Coroutine:
        raise NotImplementedError

    def get_update_status(self) -> Dict[str, Any]:
        raise NotImplementedError

class GitUpdater(BaseUpdater):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper,
                 path: Optional[str] = None,
                 env: Optional[str] = None
                 ) -> None:
        super().__init__(config, cmd_helper)
        self.name = config.get_name().split()[-1]
        self.is_valid: bool = False
        if path is None:
            path = os.path.expanduser(config.get('path'))
        self.primary_branch = config.get("primary_branch", "master")
        self.repo_path: str = path
        origin: str = config.get("origin").lower()
        self.repo = GitRepo(cmd_helper, path, self.name, origin)
        self.debug = self.cmd_helper.is_debug_enabled()
        self.env = config.get("env", env)
        self.npm_pkg_json: Optional[str] = None
        if config.get("enable_node_updates", False):
            self.npm_pkg_json = os.path.join(
                self.repo_path, "package-lock.json")
            if not os.path.isfile(self.npm_pkg_json):
                raise config.error(
                    f"Cannot enable node updates, no file "
                    f"{self.npm_pkg_json}")
        dist_packages: Optional[str] = None
        self.python_reqs: Optional[str] = None
        if self.env is not None:
            self.env = os.path.expanduser(self.env)
            dist_packages = config.get('python_dist_packages', None)
            self.python_reqs = os.path.join(
                self.repo_path, config.get("requirements"))
        self.install_script = config.get('install_script', None)
        if self.install_script is not None:
            self.install_script = os.path.abspath(os.path.join(
                self.repo_path, self.install_script))
        self.venv_args: Optional[str] = config.get('venv_args', None)
        self.python_dist_packages: Optional[List[str]] = None
        self.python_dist_path: Optional[str] = None
        self.env_package_path: Optional[str] = None
        if dist_packages is not None:
            self.python_dist_packages = [
                p.strip() for p in dist_packages.split('\n')
                if p.strip()]
            self.python_dist_path = os.path.abspath(
                config.get('python_dist_path'))
            assert self.env is not None
            env_package_path = os.path.abspath(os.path.join(
                os.path.dirname(self.env), "..",
                config.get('env_package_path')))
            matches = glob.glob(env_package_path)
            if len(matches) == 1:
                self.env_package_path = matches[0]
            else:
                raise config.error("No match for 'env_package_path': %s"
                                   % (env_package_path,))
        for opt in ["repo_path", "env", "python_reqs", "install_script",
                    "python_dist_path", "env_package_path"]:
            val = getattr(self, opt)
            if val is None:
                continue
            if not os.path.exists(val):
                raise config.error("Invalid path for option '%s': %s"
                                   % (val, opt))

    def _get_version_info(self) -> Dict[str, Any]:
        ver_path = os.path.join(self.repo_path, "scripts/version.txt")
        vinfo: Dict[str, Any] = {}
        if os.path.isfile(ver_path):
            data = ""
            with open(ver_path, 'r') as f:
                data = f.read()
            try:
                entries = [e.strip() for e in data.split('\n') if e.strip()]
                vinfo = dict([i.split('=') for i in entries])  # type: ignore
                vinfo = {k: tuple(re.findall(r"\d+", v)) for k, v in
                         vinfo.items()}
            except Exception:
                pass
            else:
                self._log_info(f"Version Info Found: {vinfo}")
        vinfo['version'] = self.repo.get_version()
        return vinfo

    def _log_exc(self, msg: str, traceback: bool = True) -> ServerError:
        log_msg = f"Repo {self.name}: {msg}"
        if traceback:
            logging.exception(log_msg)
        else:
            logging.info(log_msg)
        return self.server.error(msg)

    def _log_info(self, msg: str) -> None:
        log_msg = f"Repo {self.name}: {msg}"
        logging.info(log_msg)

    def _notify_status(self, msg: str, is_complete: bool = False) -> None:
        log_msg = f"Git Repo {self.name}: {msg}"
        logging.debug(log_msg)
        self.cmd_helper.notify_update_response(log_msg, is_complete)

    async def refresh(self) -> None:
        try:
            await self._update_repo_state()
        except Exception:
            logging.exception("Error Refreshing git state")

    async def _update_repo_state(self, need_fetch: bool = True) -> None:
        self.is_valid = False
        await self.repo.initialize(need_fetch=need_fetch)
        invalids = self.repo.report_invalids(self.primary_branch)
        if invalids:
            msgs = '\n'.join(invalids)
            self._log_info(
                f"Repo validation checks failed:\n{msgs}")
            if self.debug:
                self.is_valid = True
                self._log_info(
                    "Repo debug enabled, overriding validity checks")
            else:
                self._log_info("Updates on repo disabled")
        else:
            self.is_valid = True
            self._log_info("Validity check for git repo passed")

    async def update(self) -> None:
        await self.repo.wait_for_init()
        if not self.is_valid:
            raise self._log_exc("Update aborted, repo not valid", False)
        if self.repo.is_dirty():
            raise self._log_exc(
                "Update aborted, repo has been modified", False)
        if self.repo.is_current():
            # No need to update
            return
        inst_mtime = self._get_file_mtime(self.install_script)
        pyreqs_mtime = self._get_file_mtime(self.python_reqs)
        npm_mtime = self._get_file_mtime(self.npm_pkg_json)
        await self._pull_repo()
        # Check Semantic Versions
        await self._update_dependencies(inst_mtime, pyreqs_mtime, npm_mtime)
        # Refresh local repo state
        await self._update_repo_state(need_fetch=False)
        if self.name == "moonraker":
            # Launch restart async so the request can return
            # before the server restarts
            self._notify_status("Update Finished...",
                                is_complete=True)
            IOLoop.current().call_later(
                .1, self.restart_service)  # type: ignore
        else:
            await self.restart_service()
            self._notify_status("Update Finished...", is_complete=True)

    async def _pull_repo(self) -> None:
        self._notify_status("Updating Repo...")
        try:
            if self.repo.is_detached():
                await self.repo.fetch()
                await self.repo.checkout()
            else:
                await self.repo.pull()
        except Exception:
            raise self._log_exc("Error running 'git pull'")

    async def _update_dependencies(self,
                                   inst_mtime: Optional[float],
                                   pyreqs_mtime: Optional[float],
                                   npm_mtime: Optional[float],
                                   force: bool = False
                                   ) -> None:
        vinfo = self._get_version_info()
        cur_version: Tuple = vinfo.get('version', ())
        need_env_rebuild = cur_version < vinfo.get('env_version', ())
        if force or self._check_need_update(inst_mtime, self.install_script):
            await self._install_packages()
        if force or self._check_need_update(pyreqs_mtime, self.python_reqs):
            await self._update_virtualenv(need_env_rebuild)
        if force or self._check_need_update(npm_mtime, self.npm_pkg_json):
            if self.npm_pkg_json is not None:
                self._notify_status("Updating Node Packages...")
                try:
                    await self.cmd_helper.run_cmd(
                        "npm ci --only=prod", notify=True, timeout=600.,
                        cwd=self.repo_path)
                except Exception:
                    self._notify_status("Node Package Update failed")

    def _get_file_mtime(self, filename: Optional[str]) -> Optional[float]:
        if filename is None or not os.path.isfile(filename):
            return None
        return os.path.getmtime(filename)

    def _check_need_update(self,
                           prev_mtime: Optional[float],
                           filename: Optional[str]
                           ) -> bool:
        cur_mtime = self._get_file_mtime(filename)
        if prev_mtime is None or cur_mtime is None:
            return False
        return cur_mtime != prev_mtime

    async def _install_packages(self) -> None:
        if self.install_script is None:
            return
        # Open install file file and read
        inst_path: str = self.install_script
        if not os.path.isfile(inst_path):
            self._log_info(f"Unable to open install script: {inst_path}")
            return
        with open(inst_path, 'r') as f:
            data = f.read()
        packages: List[str] = re.findall(r'PKGLIST="(.*)"', data)
        packages = [p.lstrip("${PKGLIST}").strip() for p in packages]
        if not packages:
            self._log_info(f"No packages found in script: {inst_path}")
            return
        pkgs = " ".join(packages)
        logging.debug(f"Repo {self.name}: Detected Packages: {pkgs}")
        self._notify_status("Installing system dependencies...")
        # Install packages with apt-get
        try:
            await self.cmd_helper.run_cmd(
                f"{APT_CMD} update", timeout=300., notify=True)
            await self.cmd_helper.run_cmd(
                f"{APT_CMD} install --yes {pkgs}", timeout=3600.,
                notify=True)
        except Exception:
            self._log_exc("Error updating packages via apt-get")
            return

    async def _update_virtualenv(self, rebuild_env: bool = False) -> None:
        if self.env is None:
            return
        # Update python dependencies
        bin_dir = os.path.dirname(self.env)
        env_path = os.path.normpath(os.path.join(bin_dir, ".."))
        if rebuild_env:
            self._notify_status(f"Creating virtualenv at: {env_path}...")
            if os.path.exists(env_path):
                shutil.rmtree(env_path)
            try:
                await self.cmd_helper.run_cmd(
                    f"virtualenv {self.venv_args} {env_path}", timeout=300.)
            except Exception:
                self._log_exc(f"Error creating virtualenv")
                return
            if not os.path.exists(self.env):
                raise self._log_exc("Failed to create new virtualenv", False)
        reqs = self.python_reqs
        if reqs is None or not os.path.isfile(reqs):
            self._log_exc(f"Invalid path to requirements_file '{reqs}'")
            return
        pip = os.path.join(bin_dir, "pip")
        self._notify_status("Updating python packages...")
        try:
            await self.cmd_helper.run_cmd(
                f"{pip} install -r {reqs}", timeout=1200., notify=True,
                retries=3)
        except Exception:
            self._log_exc("Error updating python requirements")
        self._install_python_dist_requirements()

    def _install_python_dist_requirements(self) -> None:
        dist_reqs = self.python_dist_packages
        if dist_reqs is None:
            return
        dist_path = self.python_dist_path
        site_path = self.env_package_path
        assert dist_path is not None
        assert site_path is not None
        for pkg in dist_reqs:
            for f in os.listdir(dist_path):
                if f.startswith(pkg):
                    src = os.path.join(dist_path, f)
                    dest = os.path.join(site_path, f)
                    self._notify_status(f"Linking to dist package: {pkg}")
                    if os.path.islink(dest):
                        os.remove(dest)
                    elif os.path.exists(dest):
                        self._notify_status(
                            f"Error symlinking dist package: {pkg}, "
                            f"file already exists: {dest}")
                        continue
                    os.symlink(src, dest)
                    break

    async def restart_service(self) -> None:
        self._notify_status("Restarting Service...")
        try:
            await self.cmd_helper.run_cmd(
                f"sudo systemctl restart {self.name}")
        except Exception:
            if self.name == "moonraker":
                # We will always get an error when restarting moonraker
                # from within the child process, so ignore it
                return
            raise self._log_exc("Error restarting service")

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        self._notify_status("Attempting Repo Recovery...")
        inst_mtime = self._get_file_mtime(self.install_script)
        pyreqs_mtime = self._get_file_mtime(self.python_reqs)
        npm_mtime = self._get_file_mtime(self.npm_pkg_json)

        if hard:
            await self.repo.clone()
            await self._update_repo_state()
        else:
            self._notify_status("Resetting Git Repo...")
            await self.repo.reset()
            await self._update_repo_state()

        if self.repo.is_dirty() or not self.is_valid:
            raise self.server.error(
                "Recovery attempt failed, repo state not pristine", 500)
        await self._update_dependencies(inst_mtime, pyreqs_mtime, npm_mtime,
                                        force=force_dep_update)
        if self.name == "moonraker":
            IOLoop.current().call_later(
                .1, self.restart_service)  # type: ignore
        else:
            await self.restart_service()
        self._notify_status("Recovery Complete", is_complete=True)

    def get_update_status(self) -> Dict[str, Any]:
        status = self.repo.get_repo_status()
        status['is_valid'] = self.is_valid
        status['debug_enabled'] = self.debug
        return status


GIT_ASYNC_TIMEOUT = 300.
GIT_ENV_VARS = {
    'GIT_HTTP_LOW_SPEED_LIMIT': "1000",
    'GIT_HTTP_LOW_SPEED_TIME ': "20"
}
GIT_MAX_LOG_CNT = 100
GIT_LOG_FMT = \
    "\"sha:%H%x1Dauthor:%an%x1Ddate:%ct%x1Dsubject:%s%x1Dmessage:%b%x1E\""

class GitRepo:
    def __init__(self,
                 cmd_helper: CommandHelper,
                 git_path: str,
                 alias: str,
                 origin_url: str
                 ) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper
        self.alias = alias
        self.git_path = git_path
        git_dir, git_base = os.path.split(self.git_path)
        self.backup_path = os.path.join(git_dir, f".{git_base}_repo_backup")
        self.origin_url = origin_url
        self.valid_git_repo: bool = False
        self.git_owner: str = "?"
        self.git_remote: str = "?"
        self.git_branch: str = "?"
        self.current_version: str = "?"
        self.upstream_version: str = "?"
        self.current_commit: str = "?"
        self.upstream_commit: str = "?"
        self.upstream_url: str = "?"
        self.full_version_string: str = "?"
        self.branches: List[str] = []
        self.dirty: bool = False
        self.head_detached: bool = False
        self.git_messages: List[str] = []
        self.commits_behind: List[Dict[str, Any]] = []
        self.recovery_message = \
            f"""
            Manually restore via SSH with the following commands:
            sudo service {self.alias} stop
            cd {git_dir}
            rm -rf {git_base}
            git clone {self.origin_url}
            sudo service {self.alias} start
            """

        self.init_condition: Optional[Condition] = None
        self.initialized: bool = False
        self.git_operation_lock = Lock()
        self.fetch_timeout_handle: Optional[object] = None
        self.fetch_input_recd: bool = False

    async def initialize(self, need_fetch: bool = True) -> None:
        if self.init_condition is not None:
            # No need to initialize multiple requests
            await self.init_condition.wait()
            if self.initialized:
                return
        self.initialized = False
        self.init_condition = Condition()
        self.git_messages.clear()
        try:
            await self.update_repo_status()
            self._verify_repo()
            if not self.head_detached:
                # lookup remote via git config
                self.git_remote = await self.get_config_item(
                    f"branch.{self.git_branch}.remote")

            # Populate list of current branches
            blist = await self.list_branches()
            self.branches = []
            for branch in blist:
                branch = branch.strip()
                if branch[0] == "*":
                    branch = branch[2:]
                if branch[0] == "(":
                    continue
                self.branches.append(branch)

            if need_fetch:
                await self.fetch()
            await self.run_fsck()

            self.upstream_url = await self.remote("get-url")
            self.current_commit = await self.rev_parse("HEAD")
            self.upstream_commit = await self.rev_parse(
                f"{self.git_remote}/{self.git_branch}")
            current_version = await self.describe(
                "--always --tags --long --dirty")
            self.full_version_string = current_version.strip()
            upstream_version = await self.describe(
                f"{self.git_remote}/{self.git_branch} "
                "--always --tags --long")

            # Store current remote in the database if in a detached state
            if self.head_detached:
                mrdb: DBComp = self.server.lookup_component("database")
                db_key = f"update_manager.git_repo_{self.alias}" \
                    ".detached_remote"
                mrdb.insert_item(
                    "moonraker", db_key,
                    [self.current_commit, self.git_remote, self.git_branch])

            # Parse GitHub Owner from URL
            owner_match = re.match(r"https?://[^/]+/([^/]+)", self.upstream_url)
            self.git_owner = "?"
            if owner_match is not None:
                self.git_owner = owner_match.group(1)
            self.dirty = current_version.endswith("dirty")

            # Parse Version Info
            versions = []
            for ver in [current_version, upstream_version]:
                tag_version = "?"
                ver_match = re.match(r"v\d+\.\d+\.\d-\d+", ver)
                if ver_match:
                    tag_version = ver_match.group()
                versions.append(tag_version)
            self.current_version, self.upstream_version = versions

            # Get Commits Behind
            self.commits_behind = []
            cbh = await self.get_commits_behind()
            if cbh:
                tagged_commits = await self.get_tagged_commits()
                debug_msg = '\n'.join([f"{k}: {v}" for k, v in
                                       tagged_commits.items()])
                logging.debug(f"Git Repo {self.alias}: Tagged Commits\n"
                              f"{debug_msg}")
                for i, commit in enumerate(cbh):
                    tag = tagged_commits.get(commit['sha'], None)
                    if i < 30 or tag is not None:
                        commit['tag'] = tag
                        self.commits_behind.append(commit)

            self.log_repo_info()
        except Exception:
            logging.exception(f"Git Repo {self.alias}: Initialization failure")
            raise
        else:
            self.initialized = True
        finally:
            self.init_condition.notify_all()
            self.init_condition = None

    async def wait_for_init(self) -> None:
        if self.init_condition is not None:
            await self.init_condition.wait()
            if not self.initialized:
                raise self.server.error(
                    f"Git Repo {self.alias}: Initialization failure")

    async def update_repo_status(self) -> bool:
        async with self.git_operation_lock:
            if not os.path.isdir(os.path.join(self.git_path, ".git")):
                logging.info(
                    f"Git Repo {self.alias}: path '{self.git_path}'"
                    " is not a valid git repo")
                return False
            await self._wait_for_lock_release()
            self.valid_git_repo = False
            try:
                resp = await self._run_git_cmd("status -u no")
            except Exception:
                return False
            resp = resp.strip().split('\n', 1)[0]
            self.head_detached = resp.startswith("HEAD detached")
            branch_info = resp.split()[-1]
            if self.head_detached:
                bparts = branch_info.split("/", 1)
                if len(bparts) == 2:
                    self.git_remote, self.git_branch = bparts
                else:
                    mrdb: DBComp = self.server.lookup_component("database")
                    db_key = f"update_manager.git_repo_{self.alias}" \
                        ".detached_remote"
                    detached_remote: List[str] = mrdb.get_item(
                        "moonraker", db_key, ["", "?", "?"])
                    if detached_remote[0].startswith(branch_info):
                        self.git_remote = detached_remote[1]
                        self.git_branch = detached_remote[2]
                        msg = "Using remote stored in database:"\
                            f" {self.git_remote}/{self.git_branch}"
                    elif self.git_remote == "?":
                        msg = "Resolve by manually checking out" \
                            " a branch via SSH."
                    else:
                        msg = "Defaulting to previously tracked " \
                            f"{self.git_remote}/{self.git_branch}."
                    logging.info(
                        f"Git Repo {self.alias}: HEAD detached on untracked "
                        f"commit {branch_info}. {msg}")
            else:
                self.git_branch = branch_info
            self.valid_git_repo = True
            return True

    def log_repo_info(self) -> None:
        logging.info(
            f"Git Repo {self.alias} Detected:\n"
            f"Owner: {self.git_owner}\n"
            f"Path: {self.git_path}\n"
            f"Remote: {self.git_remote}\n"
            f"Branch: {self.git_branch}\n"
            f"Remote URL: {self.upstream_url}\n"
            f"Current Commit SHA: {self.current_commit}\n"
            f"Upstream Commit SHA: {self.upstream_commit}\n"
            f"Current Version: {self.current_version}\n"
            f"Upstream Version: {self.upstream_version}\n"
            f"Is Dirty: {self.dirty}\n"
            f"Is Detached: {self.head_detached}\n"
            f"Commits Behind: {len(self.commits_behind)}")

    def report_invalids(self, primary_branch: str) -> List[str]:
        invalids: List[str] = []
        upstream_url = self.upstream_url.lower()
        if upstream_url[-4:] != ".git":
            upstream_url += ".git"
        if upstream_url != self.origin_url:
            invalids.append(f"Unofficial remote url: {self.upstream_url}")
        if self.git_branch != primary_branch or self.git_remote != "origin":
            invalids.append(
                "Repo not on valid remote branch, expected: "
                f"origin/{primary_branch}, detected: "
                f"{self.git_remote}/{self.git_branch}")
        if self.head_detached:
            invalids.append("Detached HEAD detected")
        return invalids

    def _verify_repo(self, check_remote: bool = False) -> None:
        if not self.valid_git_repo:
            raise self.server.error(
                f"Git Repo {self.alias}: repo not initialized")
        if check_remote:
            if self.git_remote == "?":
                raise self.server.error(
                    f"Git Repo {self.alias}: No valid git remote detected")

    async def reset(self) -> None:
        if self.git_remote == "?" or self.git_branch == "?":
            raise self.server.error("Cannot reset, unknown remote/branch")
        async with self.git_operation_lock:
            await self._run_git_cmd("clean -d -f", retries=2)
            await self._run_git_cmd(
                f"reset --hard {self.git_remote}/{self.git_branch}",
                retries=2)

    async def fetch(self) -> None:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            await self._run_git_cmd_async(
                f"fetch {self.git_remote} --prune --progress")


    async def pull(self) -> None:
        self._verify_repo()
        if self.head_detached:
            raise self.server.error(
                f"Git Repo {self.alias}: Cannot perform pull on a "
                "detached HEAD")
        async with self.git_operation_lock:
            await self._run_git_cmd_async("pull --progress")

    async def list_branches(self) -> List[str]:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd("branch --list")
            return resp.strip().split("\n")

    async def remote(self, command: str) -> str:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(
                f"remote {command} {self.git_remote}")
            return resp.strip()

    async def describe(self, args: str = "") -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"describe {args}".strip())
            return resp.strip()

    async def rev_parse(self, args: str = "") -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"rev-parse {args}".strip())
            return resp.strip()

    async def get_config_item(self, item: str) -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"config --get {item}")
            return resp.strip()

    async def checkout(self, branch: Optional[str] = None) -> None:
        self._verify_repo()
        async with self.git_operation_lock:
            branch = branch or f"{self.git_remote}/{self.git_branch}"
            await self._run_git_cmd(f"checkout {branch} -q")

    async def run_fsck(self) -> None:
        async with self.git_operation_lock:
            await self._run_git_cmd("fsck --full", timeout=300., retries=1)

    async def clone(self) -> None:
        async with self.git_operation_lock:
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Starting Clone Recovery...")
            if os.path.exists(self.backup_path):
                shutil.rmtree(self.backup_path)
            self._check_lock_file_exists(remove=True)
            git_cmd = f"clone {self.origin_url} {self.backup_path}"
            try:
                await self._run_git_cmd_async(git_cmd, 1, False, False)
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Git Repo {self.alias}: Git Clone Failed")
                raise self.server.error("Git Clone Error") from e
            if os.path.exists(self.git_path):
                shutil.rmtree(self.git_path)
            shutil.move(self.backup_path, self.git_path)
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Git Clone Complete")

    async def get_commits_behind(self) -> List[Dict[str, Any]]:
        self._verify_repo()
        if self.is_current():
            return []
        async with self.git_operation_lock:
            branch = f"{self.git_remote}/{self.git_branch}"
            resp = await self._run_git_cmd(
                f"log {self.current_commit}..{branch} "
                f"--format={GIT_LOG_FMT} --max-count={GIT_MAX_LOG_CNT}")
            commits_behind: List[Dict[str, Any]] = []
            for log_entry in resp.split('\x1E'):
                log_entry = log_entry.strip()
                if not log_entry:
                    continue
                log_items = [li.strip() for li in log_entry.split('\x1D')
                             if li.strip()]
                cbh = [li.split(':', 1) for li in log_items]
                commits_behind.append(dict(cbh))  # type: ignore
            return commits_behind

    async def get_tagged_commits(self) -> Dict[str, Any]:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"show-ref --tags -d")
            tagged_commits: Dict[str, Any] = {}
            tags = [tag.strip() for tag in resp.split('\n') if tag.strip()]
            for tag in tags:
                sha, ref = tag.split(' ', 1)
                ref = ref.split('/')[-1]
                if ref[-3:] == "^{}":
                    # Dereference this commit and overwrite any existing tag
                    ref = ref[:-3]
                    tagged_commits[ref] = sha
                elif ref not in tagged_commits:
                    # This could be a lightweight tag pointing to a commit.  If
                    # it is an annotated tag it will be overwritten by the
                    # dereferenced tag
                    tagged_commits[ref] = sha
            # Return tagged commits as SHA keys mapped to tag values
            return {v: k for k, v in tagged_commits.items()}

    def get_repo_status(self) -> Dict[str, Any]:
        return {
            'remote_alias': self.git_remote,
            'branch': self.git_branch,
            'owner': self.git_owner,
            'version': self.current_version,
            'remote_version': self.upstream_version,
            'current_hash': self.current_commit,
            'remote_hash': self.upstream_commit,
            'is_dirty': self.dirty,
            'detached': self.head_detached,
            'commits_behind': self.commits_behind,
            'git_messages': self.git_messages,
            'full_version_string': self.full_version_string
        }

    def get_version(self, upstream: bool = False) -> Tuple[Any, ...]:
        version = self.upstream_version if upstream else self.current_version
        return tuple(re.findall(r"\d+", version))

    def is_detached(self) -> bool:
        return self.head_detached

    def is_dirty(self) -> bool:
        return self.dirty

    def is_current(self) -> bool:
        return self.current_commit == self.upstream_commit

    def _check_lock_file_exists(self, remove: bool = False) -> bool:
        lock_path = os.path.join(self.git_path, ".git/index.lock")
        if os.path.isfile(lock_path):
            if remove:
                logging.info(f"Git Repo {self.alias}: Git lock file found "
                             "after git process exited, removing")
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
            return True
        return False

    async def _wait_for_lock_release(self, timeout: int = 60) -> None:
        while timeout:
            if self._check_lock_file_exists():
                if not timeout % 10:
                    logging.info(f"Git Repo {self.alias}: Git lock file "
                                 f"exists, {timeout} seconds remaining "
                                 "before removal.")
                await tornado.gen.sleep(1.)
                timeout -= 1
            else:
                return
        self._check_lock_file_exists(remove=True)

    async def _run_git_cmd_async(self,
                                 cmd: str,
                                 retries: int = 5,
                                 need_git_path: bool = True,
                                 fix_loose: bool = True
                                 ) -> None:
        # Fetch and pull require special handling.  If the request
        # gets delayed we do not want to terminate it while the command
        # is processing.
        await self._wait_for_lock_release()
        env = os.environ.copy()
        env.update(GIT_ENV_VARS)
        if need_git_path:
            git_cmd = f"git -C {self.git_path} {cmd}"
        else:
            git_cmd = f"git {cmd}"
        scmd = self.cmd_helper.build_shell_command(
            git_cmd, callback=self._handle_process_output,
            env=env)
        while retries:
            self.git_messages.clear()
            ioloop = IOLoop.current()
            self.fetch_input_recd = False
            self.fetch_timeout_handle = ioloop.call_later(
                GIT_ASYNC_TIMEOUT, self._check_process_active,  # type: ignore
                scmd, cmd)
            try:
                await scmd.run(timeout=0)
            except Exception:
                pass
            ioloop.remove_timeout(self.fetch_timeout_handle)
            ret = scmd.get_return_code()
            if ret == 0:
                self.git_messages.clear()
                return
            elif fix_loose and "loose object" in "\n".join(self.git_messages):
                # attempt to remove corrupt objects
                try:
                    await self.cmd_helper.run_cmd_with_response(
                        "find .git/objects/ -type f -empty | xargs rm",
                        timeout=10., retries=1, cwd=self.git_path)
                except self.server.error:
                    pass
            retries -= 1
            await tornado.gen.sleep(.5)
            self._check_lock_file_exists(remove=True)
        raise self.server.error(f"Git Command '{cmd}' failed")

    def _handle_process_output(self, output: bytes) -> None:
        self.fetch_input_recd = True
        out = output.decode().strip()
        if out:
            self.git_messages.append(out)
            self.cmd_helper.notify_update_response(out)
            logging.debug(
                f"Git Repo {self.alias}: {out}")

    async def _check_process_active(self,
                                    scmd: shell_command.ShellCommand,
                                    cmd_name: str
                                    ) -> None:
        ret = scmd.get_return_code()
        if ret is not None:
            logging.debug(f"Git Repo {self.alias}: {cmd_name} returned")
            return
        if self.fetch_input_recd:
            # Received some input, reschedule timeout
            logging.debug(
                f"Git Repo {self.alias}: {cmd_name} active, rescheduling")
            ioloop = IOLoop.current()
            self.fetch_input_recd = False
            self.fetch_timeout_handle = ioloop.call_later(
                GIT_ASYNC_TIMEOUT, self._check_process_active,  # type: ignore
                scmd, cmd_name)
        else:
            # Request has timed out with no input, terminate it
            logging.debug(f"Git Repo {self.alias}: {cmd_name} timed out")
            # Cancel with SIGKILL
            await scmd.cancel(2)

    async def _run_git_cmd(self,
                           git_args: str,
                           timeout: float = 20.,
                           retries: int = 5,
                           env: Optional[Dict[str, str]] = None
                           ) -> str:
        try:
            return await self.cmd_helper.run_cmd_with_response(
                f"git -C {self.git_path} {git_args}",
                timeout=timeout, retries=retries, env=env, sig_idx=2)
        except self.cmd_helper.scmd_error as e:
            stdout = e.stdout.decode().strip()
            stderr = e.stderr.decode().strip()
            if stdout:
                self.git_messages.append(stdout)
            if stderr:
                self.git_messages.append(stderr)
            raise

class PackageUpdater(BaseUpdater):
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

class WebUpdater(BaseUpdater):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper)
        self.repo = config.get('repo').strip().strip("/")
        self.owner = self.repo.split("/", 1)[0]
        self.name = config.get_name().split()[-1]
        self.path: str = os.path.realpath(os.path.expanduser(
            config.get("path")))
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
        version_path = os.path.join(self.path, ".version")
        if os.path.isfile(os.path.join(self.path, ".version")):
            with open(version_path, "r") as f:
                v = f.read()
            self.version = v.strip()

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
                suffix=self.name, prefix="client") as tempdir:
            if os.path.isdir(self.path):
                # find and move persistent files
                for fname in os.listdir(self.path):
                    src_path = os.path.join(self.path, fname)
                    if fname in self.persistent_files:
                        dest_dir = os.path.dirname(
                            os.path.join(tempdir, fname))
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.move(src_path, dest_dir)
                shutil.rmtree(self.path)
            os.mkdir(self.path)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                zf.extractall(self.path)
            # Move temporary files back into
            for fname in os.listdir(tempdir):
                src_path = os.path.join(tempdir, fname)
                dest_dir = os.path.dirname(os.path.join(self.path, fname))
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src_path, dest_dir)
        self.version = self.remote_version
        version_path = os.path.join(self.path, ".version")
        if not os.path.exists(version_path):
            with open(version_path, "w") as f:
                f.write(self.version)
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
