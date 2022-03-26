# Deploy updates for applications managed by Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import pathlib
import shutil
import hashlib
import json
import logging
from .base_deploy import BaseDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .update_manager import CommandHelper
    from ..machine import Machine

CHANNEL_TO_TYPE = {
    "stable": "zip",
    "beta": "zip_beta",
    "dev": "git_repo"
}
TYPE_TO_CHANNEL = {
    "zip": "stable",
    "zip_beta": "beta",
    "git_repo": "dev"
}

class AppDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper,
                 app_params: Optional[Dict[str, Any]]
                 ) -> None:
        self.config = config
        self.app_params = app_params
        cfg_hash = self._calc_config_hash()
        super().__init__(config, cmd_helper, prefix="Application",
                         cfg_hash=cfg_hash)
        self.debug = self.cmd_helper.is_debug_enabled()
        if app_params is not None:
            self.channel: str = app_params['channel']
            self.path: pathlib.Path = pathlib.Path(
                app_params['path']).expanduser().resolve()
            executable: Optional[str] = app_params['executable']
            self.type = CHANNEL_TO_TYPE[self.channel]
        else:
            self.type = config.get('type')
            self.channel = TYPE_TO_CHANNEL[self.type]
            self.path = pathlib.Path(
                config.get('path')).expanduser().resolve()
            executable = config.get('env', None)
        if self.channel not in CHANNEL_TO_TYPE.keys():
            raise config.error(
                f"Invalid Channel '{self.channel}' for config "
                f"section [{config.get_name()}]")
        self._verify_path(config, 'path', self.path)
        self.executable: Optional[pathlib.Path] = None
        self.pip_exe: Optional[pathlib.Path] = None
        self.venv_args: Optional[str] = None
        if executable is not None:
            self.executable = pathlib.Path(executable).expanduser()
            self.pip_exe = self.executable.parent.joinpath("pip")
            if not self.pip_exe.exists():
                self.server.add_warning(
                    f"Update Manger {self.name}: Unable to locate pip "
                    "executable")
            self._verify_path(config, 'env', self.executable)
            self.venv_args = config.get('venv_args', None)

        self.info_tags: List[str] = config.getlist("info_tags", [])
        self.managed_services: List[str] = []
        svc_default = []
        if config.getboolean("is_system_service", True):
            svc_default.append(self.name)
        svc_choices = [self.name, "klipper", "moonraker"]
        services: List[str] = config.getlist(
            "managed_services", svc_default, separator=None
        )
        for svc in services:
            if svc not in svc_choices:
                raw = " ".join(services)
                self.server.add_warning(
                    f"[{config.get_name()}]: Option 'restart_action: {raw}' "
                    f"contains an invalid value '{svc}'.  All values must be "
                    f"one of the following choices: {svc_choices}"
                )
                break
        for svc in svc_choices:
            if svc in services and svc not in self.managed_services:
                self.managed_services.append(svc)
        logging.debug(
            f"Extension {self.name} managed services: {self.managed_services}"
        )
        # We need to fetch all potential options for an Application.  Not
        # all options apply to each subtype, however we can't limit the
        # options in children if we want to switch between channels and
        # satisfy the confighelper's requirements.
        self.moved_origin: Optional[str] = config.get('moved_origin', None)
        self.origin: str = config.get('origin')
        self.primary_branch = config.get("primary_branch", "master")
        self.npm_pkg_json: Optional[pathlib.Path] = None
        if config.getboolean("enable_node_updates", False):
            self.npm_pkg_json = self.path.joinpath("package-lock.json")
            self._verify_path(config, 'enable_node_updates', self.npm_pkg_json)
        self.python_reqs: Optional[pathlib.Path] = None
        if self.executable is not None:
            self.python_reqs = self.path.joinpath(config.get("requirements"))
            self._verify_path(config, 'requirements', self.python_reqs)
        self.install_script: Optional[pathlib.Path] = None
        install_script = config.get('install_script', None)
        if install_script is not None:
            self.install_script = self.path.joinpath(install_script).resolve()
            self._verify_path(config, 'install_script', self.install_script)

    @staticmethod
    def _is_git_repo(app_path: Union[str, pathlib.Path]) -> bool:
        if isinstance(app_path, str):
            app_path = pathlib.Path(app_path).expanduser()
        return app_path.joinpath('.git').exists()

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.need_channel_update = storage.get('need_channel_upate', False)
        self._is_valid = storage.get('is_valid', False)
        return storage

    def _calc_config_hash(self) -> str:
        cfg_hash = self.config.get_hash()
        if self.app_params is None:
            return cfg_hash.hexdigest()
        else:
            app_bytes = json.dumps(self.app_params).encode()
            cfg_hash.update(app_bytes)
            return cfg_hash.hexdigest()

    def _verify_path(self,
                     config: ConfigHelper,
                     option: str,
                     file_path: pathlib.Path
                     ) -> None:
        if not file_path.exists():
            raise config.error(
                f"Invalid path for option `{option}` in section "
                f"[{config.get_name()}]: Path `{file_path}` does not exist")

    def check_need_channel_swap(self) -> bool:
        return self.need_channel_update

    def get_configured_type(self) -> str:
        return self.type

    def check_same_paths(self,
                         app_path: Union[str, pathlib.Path],
                         executable: Union[str, pathlib.Path]
                         ) -> bool:
        if isinstance(app_path, str):
            app_path = pathlib.Path(app_path)
        if isinstance(executable, str):
            executable = pathlib.Path(executable)
        app_path = app_path.expanduser()
        executable = executable.expanduser()
        if self.executable is None:
            return False
        try:
            return self.path.samefile(app_path) and \
                self.executable.samefile(executable)
        except Exception:
            return False

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        raise NotImplementedError

    async def reinstall(self):
        raise NotImplementedError

    async def restart_service(self):
        if not self.managed_services:
            return
        is_full = self.cmd_helper.is_full_update()
        for svc in self.managed_services:
            if is_full and svc != self.name:
                self.notify_status(f"Service {svc} restart postponed...")
                self.cmd_helper.add_pending_restart(svc)
                continue
            self.cmd_helper.remove_pending_restart(svc)
            self.notify_status(f"Restarting service {svc}...")
            if svc == "moonraker":
                # Launch restart async so the request can return
                # before the server restarts
                event_loop = self.server.get_event_loop()
                event_loop.delay_callback(.1, self._do_restart, svc)
            else:
                await self._do_restart(svc)

    async def _do_restart(self, svc_name: str) -> None:
        machine: Machine = self.server.lookup_component("machine")
        try:
            await machine.do_service_action("restart", svc_name)
        except Exception:
            if svc_name == "moonraker":
                # We will always get an error when restarting moonraker
                # from within the child process, so ignore it
                return
            raise self.log_exc("Error restarting service")

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'channel': self.channel,
            'debug_enabled': self.debug,
            'need_channel_update': self.need_channel_update,
            'is_valid': self._is_valid,
            'configured_type': self.type,
            'info_tags': self.info_tags
        }

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['is_valid'] = self._is_valid
        storage['need_channel_update'] = self.need_channel_update
        return storage

    async def _get_file_hash(self,
                             filename: Optional[pathlib.Path]
                             ) -> Optional[str]:
        if filename is None or not filename.is_file():
            return None

        def hash_func(f: pathlib.Path) -> str:
            return hashlib.sha256(f.read_bytes()).hexdigest()
        try:
            event_loop = self.server.get_event_loop()
            return await event_loop.run_in_thread(hash_func, filename)
        except Exception:
            return None

    async def _check_need_update(self,
                                 prev_hash: Optional[str],
                                 filename: Optional[pathlib.Path]
                                 ) -> bool:
        cur_hash = await self._get_file_hash(filename)
        if prev_hash is None or cur_hash is None:
            return False
        return prev_hash != cur_hash

    async def _install_packages(self, package_list: List[str]) -> None:
        self.notify_status("Installing system dependencies...")
        # Install packages with apt-get
        try:
            await self.cmd_helper.install_packages(
                package_list, timeout=3600., notify=True)
        except Exception:
            self.log_exc("Error updating packages")
            return

    async def _update_virtualenv(self,
                                 requirements: Union[pathlib.Path, List[str]]
                                 ) -> None:
        if self.pip_exe is None:
            return
        # Update python dependencies
        if isinstance(requirements, pathlib.Path):
            if not requirements.is_file():
                self.log_info(
                    f"Invalid path to requirements_file '{requirements}'")
                return
            args = f"-r {requirements}"
        else:
            args = " ".join(requirements)
        self.notify_status("Updating python packages...")
        try:
            # First attempt to update pip
            # await self.cmd_helper.run_cmd(
            #     f"{self.pip_exe} install -U pip", timeout=1200., notify=True,
            #     retries=3)
            await self.cmd_helper.run_cmd(
                f"{self.pip_exe} install {args}", timeout=1200., notify=True,
                retries=3)
        except Exception:
            self.log_exc("Error updating python requirements")

    async def _build_virtualenv(self) -> None:
        if self.pip_exe is None or self.venv_args is None:
            return
        bin_dir = self.pip_exe.parent
        env_path = bin_dir.parent.resolve()
        self.notify_status(f"Creating virtualenv at: {env_path}...")
        if env_path.exists():
            shutil.rmtree(env_path)
        try:
            await self.cmd_helper.run_cmd(
                f"virtualenv {self.venv_args} {env_path}", timeout=300.)
        except Exception:
            self.log_exc(f"Error creating virtualenv")
            return
        if not self.pip_exe.exists():
            raise self.log_exc("Failed to create new virtualenv", False)
