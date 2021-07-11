# Deploy updates for applications managed by Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import pathlib
import shutil
import hashlib
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
        super().__init__(config, cmd_helper)
        self.config = config
        self.app_params = app_params
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
        self.venv_args: Optional[str] = None
        if executable is not None:
            self.executable = pathlib.Path(executable).expanduser().resolve()
            self._verify_path(config, 'env', self.executable)
            self.venv_args = config.get('venv_args', None)

        self.is_service = config.getboolean("is_system_service", True)
        self.need_channel_update = False
        self._is_valid = False

        # We need to fetch all potential options for an Application.  Not
        # all options apply to each subtype, however we can't limit the
        # options in children if we want to switch between channels and
        # satisfy the confighelper's requirements.
        self.origin: str = config.get('origin')
        self.primary_branch = config.get("primary_branch", "master")
        self.npm_pkg_json: Optional[pathlib.Path] = None
        if config.get("enable_node_updates", False):
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
        return self.path.samefile(app_path) and \
            self.executable.samefile(executable)

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        raise NotImplementedError

    async def reinstall(self):
        raise NotImplementedError

    async def restart_service(self):
        if not self.is_service:
            self.notify_status(
                "Application not configured as service, skipping restart")
            return
        if self.name == "moonraker":
            # Launch restart async so the request can return
            # before the server restarts
            event_loop = self.server.get_event_loop()
            event_loop.delay_callback(.1, self._do_restart)
        else:
            await self._do_restart()

    async def _do_restart(self) -> None:
        self.notify_status("Restarting Service...")
        try:
            await self.cmd_helper.run_cmd(
                f"sudo systemctl restart {self.name}")
        except Exception:
            if self.name == "moonraker":
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
            'configured_type': self.type
        }

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
        if self.executable is None:
            return
        # Update python dependencies
        bin_dir = self.executable.parent
        if isinstance(requirements, pathlib.Path):
            if not requirements.is_file():
                self.log_info(
                    f"Invalid path to requirements_file '{requirements}'")
                return
            args = f"-r {requirements}"
        else:
            args = " ".join(requirements)
        pip = bin_dir.joinpath("pip")
        self.notify_status("Updating python packages...")
        try:
            # First attempt to update pip
            await self.cmd_helper.run_cmd(
                f"{pip} install -U pip", timeout=1200., notify=True,
                retries=3)
            await self.cmd_helper.run_cmd(
                f"{pip} install {args}", timeout=1200., notify=True,
                retries=3)
        except Exception:
            self.log_exc("Error updating python requirements")

    async def _build_virtualenv(self) -> None:
        if self.executable is None or self.venv_args is None:
            return
        bin_dir = self.executable.parent
        env_path = bin_dir.joinpath("..").resolve()
        self.notify_status(f"Creating virtualenv at: {env_path}...")
        if env_path.exists():
            shutil.rmtree(env_path)
        try:
            await self.cmd_helper.run_cmd(
                f"virtualenv {self.venv_args} {env_path}", timeout=300.)
        except Exception:
            self.log_exc(f"Error creating virtualenv")
            return
        if not self.executable.exists():
            raise self.log_exc("Failed to create new virtualenv", False)
