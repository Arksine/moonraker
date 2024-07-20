# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import os
import logging
import time
import tempfile
import pathlib
from .common import AppType, get_base_configuration
from .base_deploy import BaseDeploy
from .app_deploy import AppDeploy
from .git_deploy import GitDeploy
from .zip_deploy import ZipDeploy
from .python_deploy import PythonDeploy
from .system_deploy import PackageDeploy
from ...common import RequestType
from ...utils.filelock import AsyncExclusiveFileLock, LockTimeout

# Annotation imports
from typing import (
    TYPE_CHECKING,
    TypeVar,
    Any,
    Callable,
    Optional,
    Set,
    Type,
    Union,
    Dict,
    List,
    cast
)
if TYPE_CHECKING:
    from ...server import Server
    from ...confighelper import ConfigHelper
    from ...common import WebRequest
    from ..klippy_connection import KlippyConnection
    from ..shell_command import ShellCommandFactory as SCMDComp
    from ..database import MoonrakerDatabase as DBComp
    from ..database import NamespaceWrapper
    from ..machine import Machine
    from ..http_client import HttpClient
    from ...eventloop import FlexTimer
    JsonType = Union[List[Any], Dict[str, Any]]
    _T = TypeVar("_T")

# Check To see if Updates are necessary each hour
UPDATE_REFRESH_INTERVAL = 3600.

def get_deploy_class(
    app_type: Union[AppType, str], default: _T
) -> Union[Type[BaseDeploy], _T]:
    key = AppType.from_string(app_type) if isinstance(app_type, str) else app_type
    _deployers = {
        AppType.WEB: ZipDeploy,
        AppType.GIT_REPO: GitDeploy,
        AppType.ZIP: ZipDeploy,
        AppType.PYTHON: PythonDeploy
    }
    return _deployers.get(key, default)

class UpdateManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.instance_tracker = InstanceTracker(self.server)
        self.kconn: KlippyConnection
        self.kconn = self.server.lookup_component("klippy_connection")
        self.app_config = get_base_configuration(config)

        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        self.refresh_window = config.getintlist('refresh_window', [0, 5],
                                                separator='-', count=2)
        if (
            not (0 <= self.refresh_window[0] <= 23) or
            not (0 <= self.refresh_window[1] <= 23)
        ):
            raise config.error("The hours specified in 'refresh_window'"
                               " must be between 0 and 23.")
        if self.refresh_window[0] == self.refresh_window[1]:
            raise config.error("The start and end hours specified"
                               " in 'refresh_window' cannot be the same.")

        self.cmd_helper = CommandHelper(config, self.get_updaters)
        self.updaters: Dict[str, BaseDeploy] = {}
        if config.getboolean('enable_system_updates', True):
            self.updaters['system'] = PackageDeploy(config, self.cmd_helper)
        mcfg = self.app_config["moonraker"]
        kcfg = self.app_config["klipper"]
        mclass = get_deploy_class(mcfg.get("type"), BaseDeploy)
        self.updaters['moonraker'] = mclass(mcfg, self.cmd_helper)
        kclass = BaseDeploy
        if (
            os.path.exists(kcfg.get("path")) and
            os.path.exists(kcfg.get("env"))
        ):
            kclass = get_deploy_class(kcfg.get("type"), BaseDeploy)
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
            name = BaseDeploy.parse_name(cfg)
            if name in self.updaters:
                if name not in ["klipper", "moonraker"]:
                    self.server.add_warning(
                        f"[update_manager]: Extension {name} already added"
                    )
                continue
            try:
                client_type = cfg.get("type")
                deployer = get_deploy_class(client_type, None)
                if deployer is None:
                    self.server.add_warning(
                        f"Invalid type '{client_type}' for section [{section}]")
                else:
                    self.updaters[name] = deployer(cfg, self.cmd_helper)
            except Exception as e:
                self.server.add_warning(
                    f"[update_manager]: Failed to load extension {name}: {e}",
                    exc_info=e
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
            "/machine/update/moonraker", RequestType.POST, self._handle_update_request
        )
        self.server.register_endpoint(
            "/machine/update/klipper", RequestType.POST, self._handle_update_request
        )
        self.server.register_endpoint(
            "/machine/update/system", RequestType.POST, self._handle_update_request
        )
        self.server.register_endpoint(
            "/machine/update/client", RequestType.POST, self._handle_update_request
        )
        self.server.register_endpoint(
            "/machine/update/full", RequestType.POST, self._handle_full_update_request
        )
        self.server.register_endpoint(
            "/machine/update/status", RequestType.GET, self._handle_status_request
        )
        self.server.register_endpoint(
            "/machine/update/refresh", RequestType.POST, self._handle_refresh_request
        )
        self.server.register_endpoint(
            "/machine/update/recover", RequestType.POST, self._handle_repo_recovery
        )
        self.server.register_endpoint(
            "/machine/update/rollback", RequestType.POST, self._handle_rollback
        )
        self.server.register_notification("update_manager:update_response")
        self.server.register_notification("update_manager:update_refreshed")

        # Register Ready Event
        self.server.register_event_handler(
            "server:klippy_identified", self._set_klipper_repo)

    def get_updaters(self) -> Dict[str, BaseDeploy]:
        return self.updaters

    async def component_init(self) -> None:
        await self.instance_tracker.set_instance_id()
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

        kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
        kupdater = self.updaters.get('klipper')
        app_type = AppType.detect(kconn.path)
        if (
            (isinstance(kupdater, AppDeploy) and
             kupdater.check_same_paths(kconn.path, kconn.executable)) or
            (app_type == AppType.NONE and type(kupdater) is BaseDeploy)
        ):
            # Current Klipper Updater is valid or unnecessary
            return
        kcfg = self.app_config["klipper"]
        kcfg.set_option("path", str(kconn.path))
        kcfg.set_option("env", str(kconn.executable))
        kcfg.set_option("type", str(app_type))
        notify = not isinstance(kupdater, AppDeploy)
        kclass = get_deploy_class(app_type, BaseDeploy)
        coro = self._update_klipper_repo(kclass(kcfg, self.cmd_helper), notify)
        self.event_loop.create_task(coro)

    async def _update_klipper_repo(self, updater: BaseDeploy, notify: bool) -> None:
        async with self.cmd_request_lock:
            self.updaters['klipper'] = updater
            umdb = self.cmd_helper.get_umdb()
            await umdb.pop('klipper', None)
            await updater.initialize()
            await updater.refresh()
        if notify:
            self.cmd_helper.notify_update_refreshed()

    def _is_within_refresh_window(self) -> bool:
        cur_hour = time.localtime(time.time()).tm_hour
        if self.refresh_window[0] < self.refresh_window[1]:
            return self.refresh_window[0] <= cur_hour < self.refresh_window[1]
        return cur_hour >= self.refresh_window[0] or cur_hour < self.refresh_window[1]

    async def _handle_auto_refresh(self, eventtime: float) -> float:
        log_remaining_time = True
        if self.initial_refresh_complete:
            log_remaining_time = False
            # Update only if within the refresh window
            if not self._is_within_refresh_window():
                logging.debug("update_manager: current time is outside of"
                              " the refresh window, auto refresh rescheduled")
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
                    if updater.needs_refresh(log_remaining_time):
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
            app = web_request.get_str('name')
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
                    await updater.update()

                # Update Klipper
                app_name = 'klipper'
                kupdater = self.updaters.get('klipper')
                if isinstance(kupdater, AppDeploy):
                    self.klippy_identified_evt = asyncio.Event()
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
                                "Klippy Reconnected")
                        self.klippy_identified_evt = None

                # Update Moonraker
                app_name = 'moonraker'
                moon_updater = cast(AppDeploy, self.updaters["moonraker"])
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

    async def _handle_repo_recovery(self, web_request: WebRequest) -> str:
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

    async def _handle_rollback(self, web_request: WebRequest) -> str:
        if self.kconn.is_printing():
            raise self.server.error("Rollback Attempt Refused: Klippy is printing")
        app: str = web_request.get_str('name')
        updater = self.updaters.get(app, None)
        if updater is None:
            raise self.server.error(f"Updater {app} not available", 404)
        async with self.cmd_request_lock:
            self.cmd_helper.set_update_info(f"rollback_{app}", id(web_request))
            try:
                await updater.rollback()
            except Exception as e:
                self.cmd_helper.notify_update_response(f"Error Rolling Back {app}")
                self.cmd_helper.notify_update_response(str(e), is_complete=True)
                raise
            finally:
                self.cmd_helper.clear_update_info()
        return "ok"

    async def close(self) -> None:
        if self.refresh_timer is not None:
            self.refresh_timer.stop()
        await self.instance_tracker.close()
        for updater in self.updaters.values():
            ret = updater.close()
            if ret is not None:
                await ret

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

    def get_shell_command(self) -> SCMDComp:
        return self.server.lookup_component("shell_command")

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
        attempts: int = 1,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        sig_idx: int = 1,
        log_stderr: bool = False
    ) -> None:
        cb = self.notify_update_response if notify else None
        log_stderr |= self.server.is_verbose_enabled()
        await self.get_shell_command().run_cmd_async(
            cmd, cb, timeout=timeout, attempts=attempts,
            env=env, cwd=cwd, sig_idx=sig_idx, log_stderr=log_stderr
        )

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

class InstanceTracker:
    def __init__(self, server: Server) -> None:
        self.server = server
        self.inst_id = ""
        tmpdir = pathlib.Path(tempfile.gettempdir())
        self.inst_file_path = tmpdir.joinpath("moonraker_instance_ids")

    def get_instance_id(self) -> str:
        machine: Machine = self.server.lookup_component("machine")
        cur_name = "".join(machine.unit_name.split())
        cur_uuid: str = self.server.get_app_args()["instance_uuid"]
        pid = os.getpid()
        return f"{cur_name}:{cur_uuid}:{pid}"

    async def _read_instance_ids(self) -> List[str]:
        if not self.inst_file_path.exists():
            return []
        eventloop = self.server.get_event_loop()
        id_data = await eventloop.run_in_thread(self.inst_file_path.read_text)
        return [iid.strip() for iid in id_data.strip().splitlines() if iid.strip()]

    async def set_instance_id(self) -> None:
        try:
            async with AsyncExclusiveFileLock(self.inst_file_path, 2.):
                self.inst_id = self.get_instance_id()
                iids = await self._read_instance_ids()
                if self.inst_id not in iids:
                    iids.append(self.inst_id)
                iid_string = "\n".join(iids)
                if len(iids) > 1:
                    self.server.add_log_rollover_item(
                        "um_multi_instance_msg",
                        "Multiple instances of Moonraker have the update "
                        f"manager enabled.\n{iid_string}"
                    )
                eventloop = self.server.get_event_loop()
                await eventloop.run_in_thread(
                    self.inst_file_path.write_text, iid_string
                )
        except LockTimeout as e:
            logging.info(str(e))
        except Exception:
            logging.exception("Failed to set instance id")

    async def close(self) -> None:
        try:
            async with AsyncExclusiveFileLock(self.inst_file_path, 2.):
                # Remove current id
                iids = await self._read_instance_ids()
                if self.inst_id in iids:
                    iids.remove(self.inst_id)
                iid_string = "\n".join(iids)
                eventloop = self.server.get_event_loop()
                await eventloop.run_in_thread(
                    self.inst_file_path.write_text, iid_string
                )
        except LockTimeout as e:
            logging.info(str(e))
        except Exception:
            logging.exception("Failed to remove instance id")


def load_component(config: ConfigHelper) -> UpdateManager:
    return UpdateManager(config)
