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
import shutil
import zipfile
import time
import tempfile
import re
from ...thirdparty.packagekit import enums as PkEnum
from . import base_config
from .base_deploy import BaseDeploy
from .app_deploy import AppDeploy
from .git_deploy import GitDeploy
from .zip_deploy import ZipDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    Dict,
    List,
    cast
)
if TYPE_CHECKING:
    from ...server import Server
    from ...confighelper import ConfigHelper
    from ...websockets import WebRequest
    from ...klippy_connection import KlippyConnection
    from ..shell_command import ShellCommandFactory as SCMDComp
    from ..database import MoonrakerDatabase as DBComp
    from ..database import NamespaceWrapper
    from ..dbus_manager import DbusManager
    from ..machine import Machine
    from ..http_client import HttpClient
    from ..file_manager.file_manager import FileManager
    from ...eventloop import FlexTimer
    from dbus_next import Variant
    from dbus_next.aio import ProxyInterface
    JsonType = Union[List[Any], Dict[str, Any]]

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
        self.kconn: KlippyConnection
        self.kconn = self.server.lookup_component("klippy_connection")
        self.channel = config.get('channel', "dev")
        if self.channel not in ["dev", "beta"]:
            raise config.error(
                f"Unsupported channel '{self.channel}' in section"
                " [update_manager]")
        self.app_config = base_config.get_base_configuration(
            config, self.channel
        )
        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        self.cmd_helper = CommandHelper(config, self.get_updaters)
        self.updaters: Dict[str, BaseDeploy] = {}
        if config.getboolean('enable_system_updates', True):
            self.updaters['system'] = PackageDeploy(config, self.cmd_helper)
        mcfg = self.app_config["moonraker"]
        kcfg = self.app_config["klipper"]
        mclass = get_deploy_class(mcfg.get("path"))
        self.updaters['moonraker'] = mclass(mcfg, self.cmd_helper)
        kclass = BaseDeploy
        if (
            os.path.exists(kcfg.get("path")) and
            os.path.exists(kcfg.get("env"))
        ):
            kclass = get_deploy_class(kcfg.get("path"))
        self.updaters['klipper'] = kclass(kcfg, self.cmd_helper)

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
                self.server.add_warning(
                    f"[update_manager]: Extension {name} already added"
                )
                continue
            try:
                client_type = cfg.get("type")
                if client_type in ["web", "web_beta"]:
                    self.updaters[name] = WebClientDeploy(cfg, self.cmd_helper)
                elif client_type in ["git_repo", "zip", "zip_beta"]:
                    path = os.path.expanduser(cfg.get('path'))
                    dclass = get_deploy_class(path)
                    self.updaters[name] = dclass(cfg, self.cmd_helper)
                else:
                    self.server.add_warning(
                        f"Invalid type '{client_type}' for section [{section}]")
            except Exception as e:
                self.server.add_warning(
                    f"[update_manager]: Failed to load extension {name}: {e}"
                )

        self.cmd_request_lock = asyncio.Lock()
        self.initial_refresh_complete: bool = False
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
            "/machine/update/refresh", ["POST"],
            self._handle_refresh_request)
        self.server.register_endpoint(
            "/machine/update/recover", ["POST"],
            self._handle_repo_recovery)
        self.server.register_notification("update_manager:update_response")
        self.server.register_notification("update_manager:update_refreshed")

        # Register Ready Event
        self.server.register_event_handler(
            "server:klippy_identified", self._set_klipper_repo)

    def get_updaters(self) -> Dict[str, BaseDeploy]:
        return self.updaters

    async def component_init(self) -> None:
        # Prune stale data from the database
        umdb = self.cmd_helper.get_umdb()
        db_keys = await umdb.keys()
        for key in db_keys:
            if key not in self.updaters:
                logging.info(f"Removing stale update_manager data: {key}")
                await umdb.pop(key, None)
        for updater in list(self.updaters.values()):
            await updater.initialize()
        if self.refresh_timer is not None:
            self.refresh_timer.start()
        else:
            self.event_loop.register_callback(
                self._handle_auto_refresh, self.event_loop.get_loop_time()
            )

    def _set_klipper_repo(self) -> None:
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
        # Update paths in the database
        db: DBComp = self.server.lookup_component('database')
        db.insert_item("moonraker", "update_manager.klipper_path", kpath)
        db.insert_item("moonraker", "update_manager.klipper_exec", executable)
        kcfg = self.app_config["klipper"]
        kcfg.set_option("path", kpath)
        kcfg.set_option("env", executable)
        need_notification = not isinstance(kupdater, AppDeploy)
        kclass = get_deploy_class(kpath)
        self.updaters['klipper'] = kclass(kcfg, self.cmd_helper)
        coro = self._update_klipper_repo(need_notification)
        self.event_loop.create_task(coro)

    async def _update_klipper_repo(self, notify: bool) -> None:
        async with self.cmd_request_lock:
            umdb = self.cmd_helper.get_umdb()
            await umdb.pop('klipper', None)
            await self.updaters['klipper'].initialize()
            await self.updaters['klipper'].refresh()
        if notify:
            self.cmd_helper.notify_update_refreshed()

    async def _handle_auto_refresh(self, eventtime: float) -> float:
        cur_hour = time.localtime(time.time()).tm_hour
        if self.initial_refresh_complete:
            # Update when the local time is between 12AM and 5AM
            if cur_hour >= MAX_UPDATE_HOUR:
                return eventtime + UPDATE_REFRESH_INTERVAL
            if self.kconn.is_printing():
                # Don't Refresh during a print
                logging.info("Klippy is printing, auto refresh aborted")
                return eventtime + UPDATE_REFRESH_INTERVAL
        need_notify = False
        machine: Machine = self.server.lookup_component("machine")
        if machine.validation_enabled():
            logging.info(
                "update_manger: Install validation pending, bypassing "
                "initial refresh"
            )
            self.initial_refresh_complete = True
            return eventtime + UPDATE_REFRESH_INTERVAL
        async with self.cmd_request_lock:
            try:
                for name, updater in list(self.updaters.items()):
                    if updater.needs_refresh():
                        await updater.refresh()
                        need_notify = True
            except Exception:
                logging.exception("Unable to Refresh Status")
                return eventtime + UPDATE_REFRESH_INTERVAL
            finally:
                self.initial_refresh_complete = True
        if need_notify:
            self.cmd_helper.notify_update_refreshed()
        return eventtime + UPDATE_REFRESH_INTERVAL

    async def _handle_update_request(self,
                                     web_request: WebRequest
                                     ) -> str:
        if self.kconn.is_printing():
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
                    f"Error updating {app}: {e}", is_complete=True)
                raise
            finally:
                self.cmd_helper.clear_update_info()
        return "ok"

    async def _handle_full_update_request(self,
                                          web_request: WebRequest
                                          ) -> str:
        async with self.cmd_request_lock:
            app_name = ""
            self.cmd_helper.set_update_info('full', id(web_request))
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
                    check_restart = True
                    if not await self._check_need_reinstall(app_name):
                        check_restart = await kupdater.update()
                    if self.cmd_helper.needs_service_restart(app_name):
                        await kupdater.restart_service()
                        check_restart = True
                    if check_restart:
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
                moon_updater = cast(AppDeploy, self.updaters["moonraker"])
                if not await self._check_need_reinstall(app_name):
                    await moon_updater.update()
                if self.cmd_helper.needs_service_restart(app_name):
                    await moon_updater.restart_service()
                self.cmd_helper.set_full_complete(True)
                self.cmd_helper.notify_update_response(
                    "Full Update Complete", is_complete=True)
            except Exception as e:
                self.cmd_helper.set_full_complete(True)
                self.cmd_helper.notify_update_response(
                    f"Error updating {app_name}: {e}", is_complete=True)
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
        #   - Validation is pending
        machine: Machine = self.server.lookup_component("machine")
        if (
            machine.validation_enabled() or
            self.cmd_helper.is_update_busy() or
            self.kconn.is_printing() or
            not self.initial_refresh_complete
        ):
            if check_refresh:
                logging.info("update_manager: bypassing refresh request")
            check_refresh = False

        if check_refresh:
            # Acquire the command request lock if we want force a refresh
            await self.cmd_request_lock.acquire()
            # Now that we have acquired the lock reject attempts to spam
            # the refresh request.
            lrt = max([upd.get_last_refresh_time()
                       for upd in self.updaters.values()])
            if time.time() < lrt + 60.:
                logging.debug("update_manager: refresh bypassed due to spam")
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
                .2, self.cmd_helper.notify_update_refreshed
            )
        return ret

    async def _handle_refresh_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        name: Optional[str] = web_request.get_str("name", None)
        if name is not None and name not in self.updaters:
            raise self.server.error(f"No updater registered for '{name}'")
        machine: Machine = self.server.lookup_component("machine")
        if (
            machine.validation_enabled() or
            self.cmd_helper.is_update_busy() or
            self.kconn.is_printing() or
            not self.initial_refresh_complete
        ):
            raise self.server.error(
                "Server is busy, cannot perform refresh", 503
            )
        async with self.cmd_request_lock:
            vinfo: Dict[str, Any] = {}
            for updater_name, updater in list(self.updaters.items()):
                if name is None or updater_name == name:
                    await updater.refresh()
                vinfo[updater_name] = updater.get_update_status()
            ret = self.cmd_helper.get_rate_limit_stats()
            ret['version_info'] = vinfo
            ret['busy'] = self.cmd_helper.is_update_busy()
            event_loop = self.server.get_event_loop()
            event_loop.delay_callback(
                .2, self.cmd_helper.notify_update_refreshed
            )
        return ret

    async def _handle_repo_recovery(self,
                                    web_request: WebRequest
                                    ) -> str:
        if self.kconn.is_printing():
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
        if self.refresh_timer is not None:
            self.refresh_timer.stop()

class CommandHelper:
    def __init__(
        self,
        config: ConfigHelper,
        get_updater_cb: Callable[[], Dict[str, BaseDeploy]]
    ) -> None:
        self.server = config.get_server()
        self.get_updaters = get_updater_cb
        self.http_client: HttpClient
        self.http_client = self.server.lookup_component("http_client")
        config.getboolean('enable_repo_debug', False, deprecate=True)
        if self.server.is_debug_enabled():
            logging.warning("UPDATE MANAGER: REPO DEBUG ENABLED")
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        self.scmd_error = shell_cmd.error
        self.build_shell_command = shell_cmd.build_shell_command
        self.run_cmd_with_response = shell_cmd.exec_cmd
        self.pkg_updater: Optional[PackageDeploy] = None

        # database management
        db: DBComp = self.server.lookup_component('database')
        db.register_local_namespace("update_manager")
        self.umdb = db.wrap_namespace("update_manager")

        # Refresh Time Tracking (default is to refresh every 7 days)
        reresh_interval = config.getint('refresh_interval', 168)
        # Convert to seconds
        self.refresh_interval = reresh_interval * 60 * 60

        # GitHub API Rate Limit Tracking
        self.gh_rate_limit: Optional[int] = None
        self.gh_limit_remaining: Optional[int] = None
        self.gh_limit_reset_time: Optional[float] = None

        # Update In Progress Tracking
        self.cur_update_app: Optional[str] = None
        self.cur_update_id: Optional[int] = None
        self.full_update: bool = False
        self.full_complete: bool = False
        self.pending_service_restarts: Set[str] = set()

    def get_server(self) -> Server:
        return self.server

    def get_http_client(self) -> HttpClient:
        return self.http_client

    def get_refresh_interval(self) -> float:
        return self.refresh_interval

    def get_umdb(self) -> NamespaceWrapper:
        return self.umdb

    def set_update_info(self, app: str, uid: int) -> None:
        self.cur_update_app = app
        self.cur_update_id = uid
        self.full_update = app == "full"
        self.full_complete = not self.full_update
        self.pending_service_restarts.clear()

    def is_full_update(self) -> bool:
        return self.full_update

    def add_pending_restart(self, svc_name: str) -> None:
        self.pending_service_restarts.add(svc_name)

    def remove_pending_restart(self, svc_name: str) -> None:
        if svc_name in self.pending_service_restarts:
            self.pending_service_restarts.remove(svc_name)

    def set_full_complete(self, complete: bool = False):
        self.full_complete = complete

    def clear_update_info(self) -> None:
        self.cur_update_app = self.cur_update_id = None
        self.full_update = False
        self.full_complete = False
        self.pending_service_restarts.clear()

    def needs_service_restart(self, svc_name: str) -> bool:
        return svc_name in self.pending_service_restarts

    def is_app_updating(self, app_name: str) -> bool:
        return self.cur_update_app == app_name

    def is_update_busy(self) -> bool:
        return self.cur_update_app is not None

    def set_package_updater(self, updater: PackageDeploy) -> None:
        self.pkg_updater = updater

    async def run_cmd(
        self,
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

    def notify_update_refreshed(self) -> None:
        vinfo: Dict[str, Any] = {}
        for name, updater in self.get_updaters().items():
            vinfo[name] = updater.get_update_status()
        uinfo = self.get_rate_limit_stats()
        uinfo['version_info'] = vinfo
        uinfo['busy'] = self.is_update_busy()
        self.server.send_event("update_manager:update_refreshed", uinfo)

    def notify_update_response(
        self, resp: Union[str, bytes], is_complete: bool = False
    ) -> None:
        if self.cur_update_app is None:
            return
        resp = resp.strip()
        if isinstance(resp, bytes):
            resp = resp.decode()
        done = is_complete
        if self.full_update:
            done &= self.full_complete
        notification = {
            'message': resp,
            'application': self.cur_update_app,
            'proc_id': self.cur_update_id,
            'complete': done}
        self.server.send_event(
            "update_manager:update_response", notification)

    async def install_packages(
        self, package_list: List[str], **kwargs
    ) -> None:
        if self.pkg_updater is None:
            return
        await self.pkg_updater.install_packages(package_list, **kwargs)

    def get_rate_limit_stats(self) -> Dict[str, Any]:
        return self.http_client.github_api_stats()

    def on_download_progress(
        self, progress: int, download_size: int, downloaded: int
    ) -> None:
        totals = (
            f"{downloaded // 1024} KiB / "
            f"{download_size // 1024} KiB"
        )
        self.notify_update_response(
            f"Downloading {self.cur_update_app}: {totals} [{progress}%]")

    async def create_tempdir(
        self, suffix: Optional[str] = None, prefix: Optional[str] = None
    ) -> tempfile.TemporaryDirectory[str]:
        def _createdir(sfx, pfx):
            return tempfile.TemporaryDirectory(suffix=sfx, prefix=pfx)

        eventloop = self.server.get_event_loop()
        return await eventloop.run_in_thread(_createdir, suffix, prefix)

class PackageDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper, "system", "", "")
        cmd_helper.set_package_updater(self)
        self.use_packagekit = config.getboolean("enable_packagekit", True)
        self.available_packages: List[str] = []

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.available_packages = storage.get('packages', [])
        provider: BasePackageProvider
        try_fallback = True
        if self.use_packagekit:
            try:
                provider = PackageKitProvider(self.cmd_helper)
                await provider.initialize()
            except Exception:
                pass
            else:
                logging.info("PackageDeploy: Using PackageKit Provider")
                try_fallback = False
        if try_fallback:
            # Check to see of the apt command is available
            fallback = await self._get_fallback_provider()
            if fallback is None:
                provider = BasePackageProvider(self.cmd_helper)
                machine: Machine = self.server.lookup_component("machine")
                dist_info = machine.get_system_info()['distribution']
                dist_id: str = dist_info['id'].lower()
                self.server.add_warning(
                    "Unable to initialize System Update Provider for "
                    f"distribution: {dist_id}")
            else:
                logging.info("PackageDeploy: Using APT CLI Provider")
                provider = fallback
        self.provider = provider
        return storage

    async def _get_fallback_provider(self) -> Optional[BasePackageProvider]:
        # Currently only the API Fallback provider is available
        shell_cmd: SCMDComp
        shell_cmd = self.server.lookup_component("shell_command")
        cmd = shell_cmd.build_shell_command("sh -c 'command -v apt'")
        try:
            ret = await cmd.run_with_response()
        except shell_cmd.error:
            return None
        # APT Command found should be available
        logging.debug(f"APT package manager detected: {ret.encode()}")
        provider = AptCliProvider(self.cmd_helper)
        try:
            await provider.initialize()
        except Exception:
            return None
        return provider

    async def refresh(self) -> None:
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
        # Update Persistent Storage
        self._save_state()

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['packages'] = self.available_packages
        return storage

    async def update(self) -> bool:
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
            await self.provider.refresh_packages(notify)

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
    def __init__(self, cmd_helper: CommandHelper) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper

    async def initialize(self) -> None:
        pass

    async def refresh_packages(self, notify: bool = False) -> None:
        raise self.server.error("Cannot refresh packages, no provider set")

    async def get_packages(self) -> List[str]:
        raise self.server.error("Cannot retrieve packages, no provider set")

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        raise self.server.error("Cannot install packages, no provider set")

    async def upgrade_system(self) -> None:
        raise self.server.error("Cannot upgrade packages, no provider set")

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

    async def resolve_packages(self, package_list: List[str]) -> List[str]:
        self.cmd_helper.notify_update_response("Resolving packages...")
        search_regex = "|".join([f"^{pkg}$" for pkg in package_list])
        cmd = f"apt-cache search --names-only \"{search_regex}\""
        ret = await self.cmd_helper.run_cmd_with_response(cmd, timeout=600.)
        resolved = [
            pkg.strip().split()[0] for pkg in ret.split("\n") if pkg.strip()
        ]
        return [avail for avail in package_list if avail in resolved]

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        timeout: float = kwargs.get('timeout', 300.)
        retries: int = kwargs.get('retries', 3)
        notify: bool = kwargs.get('notify', False)
        await self.refresh_packages(notify=notify)
        resolved = await self.resolve_packages(package_list)
        if not resolved:
            self.cmd_helper.notify_update_response("No packages detected")
            return
        logging.debug(f"Resolved packages: {resolved}")
        pkgs = " ".join(resolved)
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} install --yes {pkgs}", timeout=timeout,
            retries=retries, notify=notify)

    async def upgrade_system(self) -> None:
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} upgrade --yes", timeout=3600.,
            notify=True)

class PackageKitProvider(BasePackageProvider):
    def __init__(self, cmd_helper: CommandHelper) -> None:
        super().__init__(cmd_helper)
        dbus_mgr: DbusManager = self.server.lookup_component("dbus_manager")
        self.dbus_mgr = dbus_mgr
        self.pkgkit: Optional[ProxyInterface] = None

    async def initialize(self) -> None:
        if not self.dbus_mgr.is_connected():
            raise self.server.error("DBus Connection Not available")
        # Check for PolicyKit permissions
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.system-sources-refresh",
            "The Update Manager will fail to fetch package updates")
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.package-install",
            "The Update Manager will fail to install packages")
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.system-update",
            "The Update Manager will fail to update packages"
        )
        # Fetch the PackageKit DBus Inteface
        self.pkgkit = await self.dbus_mgr.get_interface(
            "org.freedesktop.PackageKit",
            "/org/freedesktop/PackageKit",
            "org.freedesktop.PackageKit")

    async def refresh_packages(self, notify: bool = False) -> None:
        await self.run_transaction("refresh_cache", False, notify=notify)

    async def get_packages(self) -> List[str]:
        flags = PkEnum.Filter.NONE
        pkgs = await self.run_transaction("get_updates", flags.value)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        return [pkg_id.split(";")[0] for pkg_id in pkg_ids]

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        notify: bool = kwargs.get('notify', False)
        await self.refresh_packages(notify=notify)
        flags = (
            PkEnum.Filter.NEWEST | PkEnum.Filter.NOT_INSTALLED |
            PkEnum.Filter.BASENAME | PkEnum.Filter.ARCH
        )
        pkgs = await self.run_transaction("resolve", flags.value, package_list)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        if pkg_ids:
            logging.debug(f"Installing Packages: {pkg_ids}")
            tflag = PkEnum.TransactionFlag.ONLY_TRUSTED
            await self.run_transaction("install_packages", tflag.value,
                                       pkg_ids, notify=notify)

    async def upgrade_system(self) -> None:
        # Get Updates, Install Packages
        flags = PkEnum.Filter.NONE
        pkgs = await self.run_transaction("get_updates", flags.value)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        if pkg_ids:
            logging.debug(f"Upgrading Packages: {pkg_ids}")
            tflag = PkEnum.TransactionFlag.ONLY_TRUSTED
            await self.run_transaction("update_packages", tflag.value,
                                       pkg_ids, notify=True)

    def create_transaction(self) -> PackageKitTransaction:
        if self.pkgkit is None:
            raise self.server.error("PackageKit Interface Not Available")
        return PackageKitTransaction(self.dbus_mgr, self.pkgkit,
                                     self.cmd_helper)

    async def run_transaction(self,
                              method: str,
                              *args,
                              notify: bool = False
                              ) -> Any:
        transaction = self.create_transaction()
        return await transaction.run(method, *args, notify=notify)

class PackageKitTransaction:
    GET_PKG_ROLES = (
        PkEnum.Role.RESOLVE | PkEnum.Role.GET_PACKAGES |
        PkEnum.Role.GET_UPDATES
    )
    QUERY_ROLES = GET_PKG_ROLES | PkEnum.Role.GET_REPO_LIST
    PROGRESS_STATUS = (
        PkEnum.Status.RUNNING | PkEnum.Status.INSTALL |
        PkEnum.Status.UPDATE
    )

    def __init__(self,
                 dbus_mgr: DbusManager,
                 pkgkit: ProxyInterface,
                 cmd_helper: CommandHelper
                 ) -> None:
        self.server = cmd_helper.get_server()
        self.eventloop = self.server.get_event_loop()
        self.cmd_helper = cmd_helper
        self.dbus_mgr = dbus_mgr
        self.pkgkit = pkgkit
        # Transaction Properties
        self.notify = False
        self._status = PkEnum.Status.UNKNOWN
        self._role = PkEnum.Role.UNKNOWN
        self._tflags = PkEnum.TransactionFlag.NONE
        self._percentage = 101
        self._dl_remaining = 0
        self.speed = 0
        self.elapsed_time = 0
        self.remaining_time = 0
        self.caller_active = False
        self.allow_cancel = True
        self.uid = 0
        # Transaction data tracking
        self.tfut: Optional[asyncio.Future] = None
        self.last_progress_notify_time: float = 0.
        self.result: List[Dict[str, Any]] = []
        self.err_msg: str = ""

    def run(self,
            method: str,
            *args,
            notify: bool = False
            ) -> Awaitable:
        if self.tfut is not None:
            raise self.server.error(
                "PackageKit transaction can only be used once")
        self.notify = notify
        self.tfut = self.eventloop.create_future()
        coro = self._start_transaction(method, *args)
        self.eventloop.create_task(coro)
        return self.tfut

    async def _start_transaction(self,
                                 method: str,
                                 *args
                                 ) -> None:
        assert self.tfut is not None
        try:
            # Create Transaction
            tid = await self.pkgkit.call_create_transaction()  # type: ignore
            transaction, props = await self.dbus_mgr.get_interfaces(
                "org.freedesktop.PackageKit", tid,
                ["org.freedesktop.PackageKit.Transaction",
                 "org.freedesktop.DBus.Properties"])
            # Set interface callbacks
            transaction.on_package(self._on_package_signal)    # type: ignore
            transaction.on_repo_detail(                        # type: ignore
                self._on_repo_detail_signal)
            transaction.on_item_progress(                      # type: ignore
                self._on_item_progress_signal)
            transaction.on_error_code(self._on_error_signal)   # type: ignore
            transaction.on_finished(self._on_finished_signal)  # type: ignore
            props.on_properties_changed(                       # type: ignore
                self._on_properties_changed)
            # Run method
            logging.debug(f"PackageKit: Running transaction call_{method}")
            func = getattr(transaction, f"call_{method}")
            await func(*args)
        except Exception as e:
            self.tfut.set_exception(e)

    def _on_package_signal(self,
                           info_code: int,
                           package_id: str,
                           summary: str
                           ) -> None:
        info = PkEnum.Info.from_index(info_code)
        if self._role in self.GET_PKG_ROLES:
            pkg_data = {
                'package_id': package_id,
                'info': info.desc,
                'summary': summary
            }
            self.result.append(pkg_data)
        else:
            self._notify_package(info, package_id)

    def _on_repo_detail_signal(self,
                               repo_id: str,
                               description: str,
                               enabled: bool
                               ) -> None:
        if self._role == PkEnum.Role.GET_REPO_LIST:
            repo_data = {
                "repo_id": repo_id,
                "description": description,
                "enabled": enabled
            }
            self.result.append(repo_data)
        else:
            self._notify_repo(repo_id, description)

    def _on_item_progress_signal(self,
                                 item_id: str,
                                 status_code: int,
                                 percent_complete: int
                                 ) -> None:
        status = PkEnum.Status.from_index(status_code)
        # NOTE: This signal doesn't seem to fire predictably,
        # nor does it seem to provide a consistent "percent complete"
        # parameter.
        # logging.debug(
        #    f"Role {self._role.name}: Item Progress Signal Received\n"
        #    f"Item ID: {item_id}\n"
        #    f"Percent Complete: {percent_complete}\n"
        #    f"Status: {status.desc}")

    def _on_error_signal(self,
                         error_code: int,
                         details: str
                         ) -> None:
        err = PkEnum.Error.from_index(error_code)
        self.err_msg = f"{err.name}: {details}"

    def _on_finished_signal(self, exit_code: int, run_time: int) -> None:
        if self.tfut is None:
            return
        ext = PkEnum.Exit.from_index(exit_code)
        secs = run_time / 1000.
        if ext == PkEnum.Exit.SUCCESS:
            self.tfut.set_result(self.result)
        else:
            err = self.err_msg or ext.desc
            server = self.cmd_helper.get_server()
            self.tfut.set_exception(server.error(err))
        msg = f"Transaction {self._role.desc}: Exit {ext.desc}, " \
              f"Run time: {secs:.2f} seconds"
        if self.notify:
            self.cmd_helper.notify_update_response(msg)
        logging.debug(msg)

    def _on_properties_changed(self,
                               iface_name: str,
                               changed_props: Dict[str, Variant],
                               invalid_props: Dict[str, Variant]
                               ) -> None:
        for name, var in changed_props.items():
            formatted = re.sub(r"(\w)([A-Z])", r"\g<1>_\g<2>", name).lower()
            setattr(self, formatted, var.value)

    def _notify_package(self, info: PkEnum.Info, package_id: str) -> None:
        if self.notify:
            if info == PkEnum.Info.FINISHED:
                return
            pkg_parts = package_id.split(";")
            msg = f"{info.desc}: {pkg_parts[0]} ({pkg_parts[1]})"
            self.cmd_helper.notify_update_response(msg)

    def _notify_repo(self, repo_id: str, description: str) -> None:
        if self.notify:
            if not repo_id.strip():
                repo_id = description
            # TODO: May want to eliminate dups
            msg = f"GET: {repo_id}"
            self.cmd_helper.notify_update_response(msg)

    def _notify_progress(self) -> None:
        if self.notify and self._percentage <= 100:
            msg = f"{self._status.desc}...{self._percentage}%"
            if self._status == PkEnum.Status.DOWNLOAD and self._dl_remaining:
                if self._dl_remaining < 1024:
                    msg += f", Remaining: {self._dl_remaining} B"
                elif self._dl_remaining < 1048576:
                    msg += f", Remaining: {self._dl_remaining // 1024} KiB"
                else:
                    msg += f", Remaining: {self._dl_remaining // 1048576} MiB"
                if self.speed:
                    speed = self.speed // 8
                    if speed < 1024:
                        msg += f", Speed: {speed} B/s"
                    elif speed < 1048576:
                        msg += f", Speed: {speed // 1024} KiB/s"
                    else:
                        msg += f", Speed: {speed // 1048576} MiB/s"
            self.cmd_helper.notify_update_response(msg)

    @property
    def role(self) -> PkEnum.Role:
        return self._role

    @role.setter
    def role(self, role_code: int) -> None:
        self._role = PkEnum.Role.from_index(role_code)
        if self._role in self.QUERY_ROLES:
            # Never Notify Queries
            self.notify = False
        if self.notify:
            msg = f"Transaction {self._role.desc} started..."
            self.cmd_helper.notify_update_response(msg)
        logging.debug(f"PackageKit: Current Role: {self._role.desc}")

    @property
    def status(self) -> PkEnum.Status:
        return self._status

    @status.setter
    def status(self, status_code: int) -> None:
        self._status = PkEnum.Status.from_index(status_code)
        self._percentage = 101
        self.speed = 0
        logging.debug(f"PackageKit: Current Status: {self._status.desc}")

    @property
    def transaction_flags(self) -> PkEnum.TransactionFlag:
        return self._tflags

    @transaction_flags.setter
    def transaction_flags(self, bits: int) -> None:
        self._tflags = PkEnum.TransactionFlag(bits)

    @property
    def percentage(self) -> int:
        return self._percentage

    @percentage.setter
    def percentage(self, percent: int) -> None:
        self._percentage = percent
        if self._status in self.PROGRESS_STATUS:
            self._notify_progress()

    @property
    def download_size_remaining(self) -> int:
        return self._dl_remaining

    @download_size_remaining.setter
    def download_size_remaining(self, bytes_remaining: int) -> None:
        self._dl_remaining = bytes_remaining
        self._notify_progress()

class WebClientDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper, prefix="Web Client")
        self.repo = config.get('repo').strip().strip("/")
        self.owner = self.repo.split("/", 1)[0]
        self.path = pathlib.Path(config.get("path")).expanduser().resolve()
        fm: FileManager = self.server.lookup_component("file_manager")
        fm.add_reserved_path(f"update_manager {self.name}", self.path)
        self.type = config.get('type')
        def_channel = "stable"
        if self.type == "web_beta":
            def_channel = "beta"
            self.server.add_warning(
                f"Config Section [{config.get_name()}], option 'type': "
                "web_beta', value 'web_beta' is deprecated.  Set 'type' to "
                "web and 'channel' to 'beta'")
            self.type = "zip"
        self.channel = config.get("channel", def_channel)
        if self.channel not in ["stable", "beta"]:
            raise config.error(
                f"Invalid Channel '{self.channel}' for config "
                f"section [{config.get_name()}], type: {self.type}. "
                f"Must be one of the following: stable, beta")
        self.info_tags: List[str] = config.getlist("info_tags", [])
        self.persistent_files: List[str] = []
        pfiles = config.getlist('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip("/") for pf in pfiles]
            if ".version" in self.persistent_files:
                raise config.error(
                    "Invalid value for option 'persistent_files': "
                    "'.version' can not be persistent")

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.version: str = storage.get('version', "?")
        self.remote_version: str = storage.get('remote_version', "?")
        self.last_error: str = storage.get('last_error', "")
        dl_info: List[Any] = storage.get('dl_info', ["?", "?", 0])
        self.dl_info: Tuple[str, str, int] = cast(
            Tuple[str, str, int], tuple(dl_info))
        logging.info(f"\nInitializing Client Updater: '{self.name}',"
                     f"\nChannel: {self.channel}"
                     f"\npath: {self.path}")
        return storage

    async def _get_local_version(self) -> None:
        version_path = self.path.joinpath(".version")
        if version_path.is_file():
            event_loop = self.server.get_event_loop()
            version = await event_loop.run_in_thread(version_path.read_text)
            self.version = version.strip()
        else:
            self.version = "?"

    async def refresh(self) -> None:
        try:
            await self._get_local_version()
            await self._get_remote_version()
        except Exception:
            logging.exception("Error Refreshing Client")
        self._save_state()

    async def _get_remote_version(self) -> None:
        # Remote state
        if self.channel == "stable":
            resource = f"repos/{self.repo}/releases/latest"
        else:
            resource = f"repos/{self.repo}/releases?per_page=1"
        client = self.cmd_helper.get_http_client()
        resp = await client.github_api_request(
            resource, attempts=3, retry_pause_time=.5
        )
        release: Union[List[Any], Dict[str, Any]] = {}
        if resp.status_code == 304:
            if self.remote_version == "?" and resp.content:
                # Not modified, however we need to restore state from
                # cached content
                release = resp.json()
            else:
                # Either not necessary or not possible to restore from cache
                return
        elif resp.has_error():
            logging.info(
                f"Client {self.repo}: Github Request Error - {resp.error}")
            self.last_error = str(resp.error)
            return
        else:
            release = resp.json()
        result: Dict[str, Any] = {}
        if isinstance(release, list):
            if release:
                result = release[0]
        else:
            result = release
        self.last_error = ""
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
        storage['last_error'] = self.last_error
        return storage

    async def update(self) -> bool:
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
        td = await self.cmd_helper.create_tempdir(self.name, "client")
        try:
            tempdir = pathlib.Path(td.name)
            temp_download_file = tempdir.joinpath(f"{self.name}.zip")
            temp_persist_dir = tempdir.joinpath(self.name)
            client = self.cmd_helper.get_http_client()
            await client.download_file(
                dl_url, content_type, temp_download_file, size,
                self.cmd_helper.on_download_progress)
            self.cmd_helper.notify_update_response(
                f"Download Complete, extracting release to '{self.path}'")
            await event_loop.run_in_thread(
                self._extract_release, temp_persist_dir,
                temp_download_file)
        finally:
            await event_loop.run_in_thread(td.cleanup)
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
            'channel': self.channel,
            'info_tags': self.info_tags,
            'last_error': self.last_error
        }

def load_component(config: ConfigHelper) -> UpdateManager:
    return UpdateManager(config)
