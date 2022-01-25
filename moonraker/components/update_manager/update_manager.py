# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import os
import pathlib
import logging
import json
import sys
import shutil
import zipfile
import time
import tempfile
from tornado.httpclient import AsyncHTTPClient
from .base_deploy import BaseDeploy
from .app_deploy import AppDeploy
from .git_deploy import GitDeploy
from .zip_deploy import ZipDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Tuple,
    Type,
    Union,
    Dict,
    List,
    cast
)
if TYPE_CHECKING:
    from tornado.httpclient import HTTPResponse
    from moonraker import Server
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from components.klippy_apis import KlippyAPI as APIComp
    from components.shell_command import ShellCommandFactory as SCMDComp
    from components.database import MoonrakerDatabase as DBComp
    from components.database import NamespaceWrapper
    from eventloop import FlexTimer
    JsonType = Union[List[Any], Dict[str, Any]]

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../../.."))
SUPPLEMENTAL_CFG_PATH = os.path.join(
    os.path.dirname(__file__), "update_manager.conf")
KLIPPER_DEFAULT_PATH = os.path.expanduser("~/klipper")
KLIPPER_DEFAULT_EXEC = os.path.expanduser("~/klippy-env/bin/python")

# Check To see if Updates are necessary each hour
UPDATE_REFRESH_INTERVAL = 3600.
# Perform auto refresh no later than 4am
MAX_UPDATE_HOUR = 4

def get_deploy_class(app_path: str) -> Type:
    if AppDeploy._is_git_repo(app_path):
        return GitDeploy
    else:
        return ZipDeploy

class UpdateManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.app_config = config.read_supplemental_config(
            SUPPLEMENTAL_CFG_PATH)
        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        self.channel = config.get('channel', "dev")
        if self.channel not in ["dev", "beta"]:
            raise config.error(
                f"Unsupported channel '{self.channel}' in section"
                " [update_manager]")
        self.cmd_helper = CommandHelper(config)
        self.updaters: Dict[str, BaseDeploy] = {}
        if config.getboolean('enable_system_updates', True):
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
        if (
            config.get("client_repo", None) is not None or
            config.get('client_path', None) is not None
        ):
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
            if client_type in ["web", "web_beta"]:
                self.updaters[name] = WebClientDeploy(cfg, self.cmd_helper)
            elif client_type in ["git_repo", "zip", "zip_beta"]:
                path = os.path.expanduser(cfg.get('path'))
                self.updaters[name] = get_deploy_class(path)(
                    cfg, self.cmd_helper)
            else:
                raise config.error(
                    f"Invalid type '{client_type}' for section [{section}]")

        # Prune stale data from the database
        umdb = self.cmd_helper.get_umdb()
        db_keys = umdb.keys()
        for key in db_keys:
            if key not in self.updaters:
                logging.info(f"Removing stale update_manager data: {key}")
                umdb.pop(key, None)

        self.cmd_request_lock = asyncio.Lock()
        self.klippy_identified_evt: Optional[asyncio.Event] = None

        # Auto Status Refresh
        self.refresh_timer: Optional[FlexTimer] = None
        if auto_refresh_enabled:
            self.refresh_timer = self.event_loop.register_timer(
                self._handle_auto_refresh)

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
            "/machine/update/full", ["POST"],
            self._handle_full_update_request)
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

    async def component_init(self) -> None:
        async with self.cmd_request_lock:
            for updater in list(self.updaters.values()):
                if updater.needs_refresh():
                    ret = updater.refresh()
                    await ret
        if self.refresh_timer is not None:
            self.refresh_timer.start(delay=UPDATE_REFRESH_INTERVAL)

    async def _set_klipper_repo(self) -> None:
        if self.klippy_identified_evt is not None:
            self.klippy_identified_evt.set()
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

    async def _handle_auto_refresh(self, eventtime: float) -> float:
        cur_hour = time.localtime(time.time()).tm_hour
        # Update when the local time is between 12AM and 5AM
        if cur_hour >= MAX_UPDATE_HOUR:
            return eventtime + UPDATE_REFRESH_INTERVAL
        if await self._check_klippy_printing():
            # Don't Refresh during a print
            logging.info("Klippy is printing, auto refresh aborted")
            return eventtime + UPDATE_REFRESH_INTERVAL
        vinfo: Dict[str, Any] = {}
        need_notify = False
        async with self.cmd_request_lock:
            try:
                for name, updater in list(self.updaters.items()):
                    if updater.needs_refresh():
                        await updater.refresh()
                        need_notify = True
                    vinfo[name] = updater.get_update_status()
            except Exception:
                logging.exception("Unable to Refresh Status")
                return eventtime + UPDATE_REFRESH_INTERVAL
        if need_notify:
            uinfo = self.cmd_helper.get_rate_limit_stats()
            uinfo['version_info'] = vinfo
            uinfo['busy'] = self.cmd_helper.is_update_busy()
            self.server.send_event("update_manager:update_refreshed", uinfo)
        return eventtime + UPDATE_REFRESH_INTERVAL

    async def _handle_update_request(self,
                                     web_request: WebRequest
                                     ) -> str:
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
                if not await self._check_need_reinstall(app):
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

    async def _handle_full_update_request(self,
                                          web_request: WebRequest
                                          ) -> str:
        async with self.cmd_request_lock:
            app_name = ""
            self.cmd_helper.set_update_info('full', id(web_request),
                                            full_complete=False)
            self.cmd_helper.notify_update_response(
                "Preparing full software update...")
            try:
                # Perform system updates
                if 'system' in self.updaters:
                    app_name = 'system'
                    await self.updaters['system'].update()

                # Update clients
                for name, updater in self.updaters.items():
                    if name in ['klipper', 'moonraker', 'system']:
                        continue
                    app_name = name
                    if not await self._check_need_reinstall(app_name):
                        await updater.update()

                # Update Klipper
                app_name = 'klipper'
                kupdater = self.updaters.get('klipper')
                if isinstance(kupdater, AppDeploy):
                    self.klippy_identified_evt = asyncio.Event()
                    klippy_updated = True
                    if not await self._check_need_reinstall(app_name):
                        klippy_updated = await kupdater.update()
                    if klippy_updated:
                        self.cmd_helper.notify_update_response(
                            "Waiting for Klippy to reconnect (this may take"
                            " up to 2 minutes)...")
                        try:
                            await asyncio.wait_for(
                                self.klippy_identified_evt.wait(), 120.)
                        except asyncio.TimeoutError:
                            self.cmd_helper.notify_update_response(
                                "Klippy reconnect timed out...")
                        else:
                            self.cmd_helper.notify_update_response(
                                f"Klippy Reconnected")
                        self.klippy_identified_evt = None

                # Update Moonraker
                app_name = 'moonraker'
                if not await self._check_need_reinstall(app_name):
                    await self.updaters['moonraker'].update()
                self.cmd_helper.set_full_complete(True)
                self.cmd_helper.notify_update_response(
                    "Full Update Complete", is_complete=True)
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Error updating {app_name}")
                self.cmd_helper.set_full_complete(True)
                self.cmd_helper.notify_update_response(
                    str(e), is_complete=True)
            finally:
                self.cmd_helper.clear_update_info()
            return "ok"

    async def _check_need_reinstall(self, name: str) -> bool:
        if name not in self.updaters:
            return False
        updater = self.updaters[name]
        if not isinstance(updater, AppDeploy):
            return False
        if not updater.check_need_channel_swap():
            return False
        app_type = updater.get_configured_type()
        if app_type == "git_repo":
            deploy_class: Type = GitDeploy
        else:
            deploy_class = ZipDeploy
        if isinstance(updater, deploy_class):
            # Here the channel swap can be done without instantiating a new
            # class, as it will automatically be done when the user updates.
            return False
        # Instantiate the new updater.  This will perform a reinstallation
        new_updater = await deploy_class.from_application(updater)
        self.updaters[name] = new_updater
        return True

    async def _handle_status_request(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, Any]:
        check_refresh = web_request.get_boolean('refresh', False)
        # Override a request to refresh if:
        #   - An update is in progress
        #   - Klippy is printing
        if (
            self.cmd_helper.is_update_busy() or
            await self._check_klippy_printing()
        ):
            check_refresh = False

        if check_refresh:
            # Acquire the command request lock if we want force a refresh
            await self.cmd_request_lock.acquire()
            # Now that we have acquired the lock reject attempts to spam
            # the refresh request.
            lrt = max([upd.get_last_refresh_time()
                       for upd in self.updaters.values()])
            if time.time() < lrt + 60.:
                check_refresh = False
                self.cmd_request_lock.release()
        vinfo: Dict[str, Any] = {}
        try:
            for name, updater in list(self.updaters.items()):
                if check_refresh:
                    await updater.refresh()
                vinfo[name] = updater.get_update_status()
        except Exception:
            raise
        finally:
            if check_refresh:
                self.cmd_request_lock.release()
        ret = self.cmd_helper.get_rate_limit_stats()
        ret['version_info'] = vinfo
        ret['busy'] = self.cmd_helper.is_update_busy()
        if check_refresh:
            event_loop = self.server.get_event_loop()
            event_loop.delay_callback(
                .2, self.server.send_event,
                "update_manager:update_refreshed", ret)
        return ret

    async def _handle_repo_recovery(self,
                                    web_request: WebRequest
                                    ) -> str:
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
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

class CommandHelper:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.debug_enabled = config.getboolean('enable_repo_debug', False)
        if self.debug_enabled:
            logging.warning("UPDATE MANAGER: REPO DEBUG ENABLED")
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        self.scmd_error = shell_cmd.error
        self.build_shell_command = shell_cmd.build_shell_command
        self.pkg_updater: Optional[PackageDeploy] = None
        self.http_client = AsyncHTTPClient()
        self.github_request_cache: Dict[str, CachedGithubResponse] = {}

        # database management
        db: DBComp = self.server.lookup_component('database')
        db.register_local_namespace("update_manager")
        self.umdb = db.wrap_namespace("update_manager")

        # Refresh Time Tracking (default is to refresh every 28 days)
        reresh_interval = config.getint('refresh_interval', 672)
        # Convert to seconds
        self.refresh_interval = reresh_interval * 60 * 60

        # GitHub API Rate Limit Tracking
        self.gh_rate_limit: Optional[int] = None
        self.gh_limit_remaining: Optional[int] = None
        self.gh_limit_reset_time: Optional[float] = None

        # Update In Progress Tracking
        self.cur_update_app: Optional[str] = None
        self.cur_update_id: Optional[int] = None
        self.full_complete: bool = False

    def get_server(self) -> Server:
        return self.server

    def get_refresh_interval(self) -> float:
        return self.refresh_interval

    def get_umdb(self) -> NamespaceWrapper:
        return self.umdb

    def is_debug_enabled(self) -> bool:
        return self.debug_enabled

    def set_update_info(self,
                        app: str,
                        uid: int,
                        full_complete: bool = True
                        ) -> None:
        self.cur_update_app = app
        self.cur_update_id = uid
        self.full_complete = full_complete

    def set_full_complete(self, complete: bool = False):
        self.full_complete = complete

    def clear_update_info(self) -> None:
        self.cur_update_app = self.cur_update_id = None
        self.full_complete = False

    def is_app_updating(self, app_name: str) -> bool:
        return self.cur_update_app == app_name

    def is_update_busy(self) -> bool:
        return self.cur_update_app is not None

    def set_package_updater(self, updater: PackageDeploy) -> None:
        self.pkg_updater = updater

    def get_rate_limit_stats(self) -> Dict[str, Any]:
        return {
            'github_rate_limit': self.gh_rate_limit,
            'github_requests_remaining': self.gh_limit_remaining,
            'github_limit_reset_time': self.gh_limit_reset_time,
        }

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
        for _ in range(retries):
            if await scmd.run(timeout=timeout, sig_idx=sig_idx):
                break
        else:
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
                                 retries: int = 5
                                 ) -> JsonType:
        if (
            self.gh_limit_reset_time is not None and
            self.gh_limit_remaining == 0
        ):
            curtime = time.time()
            if curtime < self.gh_limit_reset_time:
                raise self.server.error(
                    f"GitHub Rate Limit Reached\nRequest: {url}\n"
                    f"Limit Reset Time: {time.ctime(self.gh_limit_remaining)}")
        if url in self.github_request_cache:
            cached_request = self.github_request_cache[url]
            etag: Optional[str] = cached_request.get_etag()
        else:
            cached_request = CachedGithubResponse()
            etag = None
            self.github_request_cache[url] = cached_request
        headers = {"Accept": "application/vnd.github.v3+json"}
        if etag is not None:
            headers['If-None-Match'] = etag
        for i in range(retries):
            error: Optional[Exception] = None
            try:
                fut = self.http_client.fetch(
                    url, headers=headers, connect_timeout=5.,
                    request_timeout=5., raise_error=False)
                resp: HTTPResponse
                resp = await asyncio.wait_for(fut, 10.)
            except Exception:
                logging.exception(
                    f"Error Processing GitHub API request: {url}")
                if i + 1 < retries:
                    await asyncio.sleep(1.)
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
                error = self.server.error(
                    f"Forbidden GitHub Request: {resp.reason}")
            elif resp.code == 304:
                logging.info(f"Github Request not Modified: {url}")
                return cached_request.get_cached_result()
            if resp.code != 200:
                logging.info(
                    f"Github Request failed: {resp.code} {resp.reason}")
                if i + 1 < retries:
                    await asyncio.sleep(1.)
                continue
            # Update rate limit on return success
            if 'X-Ratelimit-Limit' in resp.headers:
                self.gh_rate_limit = int(resp.headers['X-Ratelimit-Limit'])
                self.gh_limit_remaining = int(
                    resp.headers['X-Ratelimit-Remaining'])
                self.gh_limit_reset_time = float(
                    resp.headers['X-Ratelimit-Reset'])
            if error is not None:
                raise error
            decoded = json.loads(resp.body)
            if etag is not None:
                cached_request.update_result(etag, decoded)
            return decoded
        raise self.server.error(
            f"Retries exceeded for GitHub API request: {url}")

    async def http_download_request(self,
                                    url: str,
                                    content_type: str,
                                    timeout: float = 180.,
                                    retries: int = 5
                                    ) -> bytes:
        for i in range(retries):
            try:
                fut = self.http_client.fetch(
                    url, headers={"Accept": content_type},
                    connect_timeout=5., request_timeout=timeout)
                resp: HTTPResponse
                resp = await asyncio.wait_for(fut, timeout + 10.)
            except Exception:
                logging.exception("Error Processing Download")
                if i + 1 == retries:
                    raise
                await asyncio.sleep(1.)
                continue
            return resp.body
        raise self.server.error(
            f"Retries exceeded for GitHub API request: {url}")

    async def streaming_download_request(self,
                                         url: str,
                                         dest: Union[str, pathlib.Path],
                                         content_type: str,
                                         size: int,
                                         timeout: float = 180.,
                                         retries: int = 5
                                         ) -> None:
        if isinstance(dest, str):
            dest = pathlib.Path(dest)
        for i in range(retries):
            dl = StreamingDownload(self, dest, size)
            try:
                fut = self.http_client.fetch(
                    url, headers={"Accept": content_type},
                    connect_timeout=5., request_timeout=timeout,
                    streaming_callback=dl.on_chunk_recd)
                resp: HTTPResponse
                resp = await asyncio.wait_for(fut, timeout + 10.)
            except Exception:
                logging.exception("Error Processing Download")
                if i + 1 == retries:
                    raise
                await asyncio.sleep(1.)
                continue
            finally:
                await dl.close()
            if resp.code < 400:
                return
        raise self.server.error(f"Retries exceeded for request: {url}")

    def notify_update_response(self,
                               resp: Union[str, bytes],
                               is_complete: bool = False
                               ) -> None:
        if self.cur_update_app is None:
            return
        resp = resp.strip()
        if isinstance(resp, bytes):
            resp = resp.decode()
        done = is_complete and self.full_complete
        notification = {
            'message': resp,
            'application': self.cur_update_app,
            'proc_id': self.cur_update_id,
            'complete': done}
        self.server.send_event(
            "update_manager:update_response", notification)

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        if self.pkg_updater is None:
            return
        await self.pkg_updater.install_packages(package_list, **kwargs)

    def close(self) -> None:
        self.http_client.close()

class CachedGithubResponse:
    def __init__(self) -> None:
        self.etag: Optional[str] = None
        self.cached_result: JsonType = {}

    def get_etag(self) -> Optional[str]:
        return self.etag

    def get_cached_result(self) -> JsonType:
        return self.cached_result

    def update_result(self, etag: str, result: JsonType) -> None:
        self.etag = etag
        self.cached_result = result

class StreamingDownload:
    def __init__(self,
                 cmd_helper: CommandHelper,
                 dest: pathlib.Path,
                 download_size: int) -> None:
        self.cmd_helper = cmd_helper
        self.event_loop = cmd_helper.get_server().get_event_loop()
        self.name = dest.name
        self.file_hdl = dest.open('wb')
        self.download_size = download_size
        self.total_recd: int = 0
        self.last_pct: int = 0
        self.chunk_buffer: List[bytes] = []
        self.busy_evt: asyncio.Event = asyncio.Event()
        self.busy_evt.set()

    def on_chunk_recd(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.chunk_buffer.append(chunk)
        if not self.busy_evt.is_set():
            return
        self.busy_evt.clear()
        self.event_loop.register_callback(self._process_buffer)

    async def close(self):
        await self.busy_evt.wait()
        self.file_hdl.close()

    async def _process_buffer(self):
        while self.chunk_buffer:
            chunk = self.chunk_buffer.pop(0)
            self.total_recd += len(chunk)
            pct = int(self.total_recd / self.download_size * 100 + .5)
            await self.event_loop.run_in_thread(self.file_hdl.write, chunk)
            if pct >= self.last_pct + 5:
                self.last_pct = pct
                totals = f"{self.total_recd // 1024} KiB / " \
                         f"{self.download_size // 1024} KiB"
                self.cmd_helper.notify_update_response(
                    f"Downloading {self.name}: {totals} [{pct}%]")
        self.busy_evt.set()

class PackageDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper, "system", "", "")
        self.provider = AptCliProvider(config, cmd_helper)
        cmd_helper.set_package_updater(self)
        storage = self._load_storage()
        self.available_packages: List[str] = storage.get('packages', [])
        self.refresh_evt: Optional[asyncio.Event] = None
        self.mutex: asyncio.Lock = asyncio.Lock()

    async def refresh(self) -> None:
        # TODO: Use python-apt python lib rather than command line for updates
        if self.refresh_evt is not None:
            self.refresh_evt.wait()
            return
        async with self.mutex:
            self.refresh_evt = asyncio.Event()
            try:
                # Do not force a refresh until the server has started
                if self.server.is_running():
                    await self._update_package_cache(force=True)
                self.available_packages = await self.provider.get_packages()
                pkg_msg = "\n".join(self.available_packages)
                logging.info(
                    f"Detected {len(self.available_packages)} package updates:"
                    f"\n{pkg_msg}")
            except Exception:
                logging.exception("Error Refreshing System Packages")
            self.refresh_evt.set()
            self.refresh_evt = None
            # Update Persistent Storage
            self._save_state()

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['packages'] = self.available_packages
        return storage

    async def update(self) -> bool:
        async with self.mutex:
            if not self.available_packages:
                return False
            self.cmd_helper.notify_update_response("Updating packages...")
            try:
                await self._update_package_cache(force=True, notify=True)
                await self.provider.upgrade_system()
            except Exception:
                raise self.server.error("Error updating system packages")
            self.available_packages = []
            self._save_state()
            self.cmd_helper.notify_update_response(
                "Package update finished...", is_complete=True)
            return True

    async def _update_package_cache(self,
                                    force: bool = False,
                                    notify: bool = False
                                    ) -> None:
        curtime = time.time()
        if force or curtime > self.last_refresh_time + 3600.:
            # Don't update if a request was done within the last hour
            await self.provider.refresh_packages()

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        await self.provider.install_packages(package_list, **kwargs)

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'package_count': len(self.available_packages),
            'package_list': self.available_packages
        }

class BasePackageProvider:
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        self.server = config.get_server()
        self.cmd_helper = cmd_helper

    async def refresh_packages(self, notify: bool = False) -> None:
        raise NotImplementedError("Children must implement refresh_packages()")

    async def get_packages(self) -> List[str]:
        raise NotImplementedError("Children must implement getpackages()")

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        raise NotImplementedError("Children must implement install_packages()")

    async def upgrade_system(self) -> None:
        raise NotImplementedError("Children must implement upgrade_system()")

class AptCliProvider(BasePackageProvider):
    APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"

    async def refresh_packages(self, notify: bool = False) -> None:
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} update", timeout=600., notify=notify)

    async def get_packages(self) -> List[str]:
        res = await self.cmd_helper.run_cmd_with_response(
            "apt list --upgradable", timeout=60.)
        pkg_list = [p.strip() for p in res.split("\n") if p.strip()]
        if pkg_list:
            pkg_list = pkg_list[2:]
            return [p.split("/", maxsplit=1)[0] for p in pkg_list]
        return []

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        timeout: float = kwargs.get('timeout', 300.)
        retries: int = kwargs.get('retries', 3)
        notify: bool = kwargs.get('notify', False)
        pkgs = " ".join(package_list)
        await self.refresh_packages(notify=notify)
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} install --yes {pkgs}", timeout=timeout,
            retries=retries, notify=notify)

    async def upgrade_system(self) -> None:
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} upgrade --yes", timeout=3600.,
            notify=True)

class WebClientDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper, prefix="Web Client")
        self.repo = config.get('repo').strip().strip("/")
        self.owner = self.repo.split("/", 1)[0]
        self.path = pathlib.Path(config.get("path")).expanduser().resolve()
        self.type = config.get('type')
        self.channel = "stable" if self.type == "web" else "beta"
        self.persistent_files: List[str] = []
        pfiles = config.getlist('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip("/") for pf in pfiles]
            if ".version" in self.persistent_files:
                raise config.error(
                    "Invalid value for option 'persistent_files': "
                    "'.version' can not be persistent")
        storage = self._load_storage()
        self.version: str = storage.get('version', "?")
        self.remote_version: str = storage.get('remote_version', "?")
        dl_info: List[Any] = storage.get('dl_info', ["?", "?", 0])
        self.dl_info: Tuple[str, str, int] = cast(
            Tuple[str, str, int], tuple(dl_info))
        self.refresh_evt: Optional[asyncio.Event] = None
        self.mutex: asyncio.Lock = asyncio.Lock()
        logging.info(f"\nInitializing Client Updater: '{self.name}',"
                     f"\nChannel: {self.channel}"
                     f"\npath: {self.path}")

    async def _get_local_version(self) -> None:
        version_path = self.path.joinpath(".version")
        if version_path.is_file():
            event_loop = self.server.get_event_loop()
            version = await event_loop.run_in_thread(version_path.read_text)
            self.version = version.strip()
        else:
            self.version = "?"

    async def refresh(self) -> None:
        if self.refresh_evt is not None:
            self.refresh_evt.wait()
            return
        async with self.mutex:
            self.refresh_evt = asyncio.Event()
            try:
                await self._get_local_version()
                await self._get_remote_version()
            except Exception:
                logging.exception("Error Refreshing Client")
            self.refresh_evt.set()
            self.refresh_evt = None
            self._save_state()

    async def _get_remote_version(self) -> None:
        # Remote state
        url = f"https://api.github.com/repos/{self.repo}/releases"
        try:
            releases = await self.cmd_helper.github_api_request(url)
            assert isinstance(releases, list)
        except Exception:
            logging.exception(f"Client {self.repo}: Github Request Error")
            releases = []
        result: Dict[str, Any] = {}
        for release in releases:
            if self.channel == "stable":
                if not release['prerelease']:
                    result = release
                    break
            else:
                result = release
                break
        self.remote_version = result.get('name', "?")
        release_asset: Dict[str, Any] = result.get('assets', [{}])[0]
        dl_url: str = release_asset.get('browser_download_url', "?")
        content_type: str = release_asset.get('content_type', "?")
        size: int = release_asset.get('size', 0)
        self.dl_info = (dl_url, content_type, size)
        logging.info(
            f"Github client Info Received:\nRepo: {self.name}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"Pre-release: {result.get('prerelease', '?')}\n"
            f"url: {dl_url}\n"
            f"size: {size}\n"
            f"Content Type: {content_type}")

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['version'] = self.version
        storage['remote_version'] = self.remote_version
        storage['dl_info'] = list(self.dl_info)
        return storage

    async def update(self) -> bool:
        async with self.mutex:
            if self.remote_version == "?":
                await self._get_remote_version()
                if self.remote_version == "?":
                    raise self.server.error(
                        f"Client {self.repo}: Unable to locate update")
            dl_url, content_type, size = self.dl_info
            if dl_url == "?":
                raise self.server.error(
                    f"Client {self.repo}: Invalid download url")
            if self.version == self.remote_version:
                # Already up to date
                return False
            event_loop = self.server.get_event_loop()
            self.cmd_helper.notify_update_response(
                f"Updating Web Client {self.name}...")
            self.cmd_helper.notify_update_response(
                f"Downloading Client: {self.name}")
            with tempfile.TemporaryDirectory(
                    suffix=self.name, prefix="client") as tempdirname:
                tempdir = pathlib.Path(tempdirname)
                temp_download_file = tempdir.joinpath(f"{self.name}.zip")
                temp_persist_dir = tempdir.joinpath(self.name)
                await self.cmd_helper.streaming_download_request(
                    dl_url, temp_download_file, content_type, size)
                self.cmd_helper.notify_update_response(
                    f"Download Complete, extracting release to '{self.path}'")
                await event_loop.run_in_thread(
                    self._extract_release, temp_persist_dir,
                    temp_download_file)
            self.version = self.remote_version
            version_path = self.path.joinpath(".version")
            if not version_path.exists():
                await event_loop.run_in_thread(
                    version_path.write_text, self.version)
            self.cmd_helper.notify_update_response(
                f"Client Update Finished: {self.name}", is_complete=True)
            self._save_state()
            return True

    def _extract_release(self,
                         persist_dir: pathlib.Path,
                         release_file: pathlib.Path
                         ) -> None:
        if not persist_dir.exists():
            os.mkdir(persist_dir)
        if self.path.is_dir():
            # find and move persistent files
            for fname in os.listdir(self.path):
                src_path = self.path.joinpath(fname)
                if fname in self.persistent_files:
                    dest_dir = persist_dir.joinpath(fname).parent
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.move(str(src_path), str(dest_dir))
            shutil.rmtree(self.path)
        os.mkdir(self.path)
        with zipfile.ZipFile(release_file) as zf:
            zf.extractall(self.path)
        # Move temporary files back into
        for fname in os.listdir(persist_dir):
            src_path = persist_dir.joinpath(fname)
            dest_dir = self.path.joinpath(fname).parent
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(str(src_path), str(dest_dir))

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'owner': self.owner,
            'version': self.version,
            'remote_version': self.remote_version,
            'configured_type': self.type,
            'channel': self.channel
        }

def load_component(config: ConfigHelper) -> UpdateManager:
    return UpdateManager(config)
