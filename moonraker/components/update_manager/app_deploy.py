# Deploy updates for applications managed by Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import pathlib
import shutil
import hashlib
import logging
import re
from .base_deploy import BaseDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
    Dict,
    List,
    Tuple
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .update_manager import CommandHelper
    from ..machine import Machine
    from ..file_manager.file_manager import FileManager

MIN_PIP_VERSION = (23, 0)

SUPPORTED_CHANNELS = {
    "zip": ["stable", "beta"],
    "git_repo": ["dev", "beta"]
}
TYPE_TO_CHANNEL = {
    "zip": "stable",
    "zip_beta": "beta",
    "git_repo": "dev"
}

class AppDeploy(BaseDeploy):
    def __init__(self, config: ConfigHelper, cmd_helper: CommandHelper) -> None:
        super().__init__(config, cmd_helper, prefix="Application")
        self.config = config
        type_choices = list(TYPE_TO_CHANNEL.keys())
        self.type = config.get('type').lower()
        if self.type not in type_choices:
            raise config.error(
                f"Config Error: Section [{config.get_name()}], Option "
                f"'type: {self.type}': value must be one "
                f"of the following choices: {type_choices}"
            )
        self.channel = config.get(
            "channel", TYPE_TO_CHANNEL[self.type]
        )
        if self.type == "zip_beta":
            self.server.add_warning(
                f"Config Section [{config.get_name()}], Option 'type: "
                "zip_beta', value 'zip_beta' is deprecated.  Set 'type' "
                "to zip and 'channel' to 'beta'")
            self.type = "zip"
        self.path = pathlib.Path(
            config.get('path')).expanduser().resolve()
        if (
            self.name not in ["moonraker", "klipper"]
            and not self.path.joinpath(".writeable").is_file()
        ):
            fm: FileManager = self.server.lookup_component("file_manager")
            fm.add_reserved_path(f"update_manager {self.name}", self.path)
        executable = config.get('env', None)
        if self.channel not in SUPPORTED_CHANNELS[self.type]:
            raise config.error(
                f"Invalid Channel '{self.channel}' for config "
                f"section [{config.get_name()}], type: {self.type}")
        self._verify_path(config, 'path', self.path, check_file=False)
        self.executable: Optional[pathlib.Path] = None
        self.pip_cmd: Optional[str] = None
        self.pip_version: Tuple[int, ...] = tuple()
        self.venv_args: Optional[str] = None
        if executable is not None:
            self.executable = pathlib.Path(executable).expanduser()
            self._verify_path(config, 'env', self.executable, check_exe=True)
            # Detect if executable is actually located in a virtualenv
            # by checking the parent for the activation script
            act_path = self.executable.parent.joinpath("activate")
            while not act_path.is_file():
                if self.executable.is_symlink():
                    self.executable = pathlib.Path(os.readlink(self.executable))
                    act_path = self.executable.parent.joinpath("activate")
                else:
                    break
            if act_path.is_file():
                venv_dir = self.executable.parent.parent
                self.log_info(f"Detected virtualenv: {venv_dir}")
                pip_exe = self.executable.parent.joinpath("pip")
                if pip_exe.is_file():
                    self.pip_cmd = f"{self.executable} -m pip"
                else:
                    self.log_info("Unable to locate pip executable")
            else:
                self.log_info(
                    f"Unable to detect virtualenv at: {executable}"
                )
                self.executable = pathlib.Path(executable).expanduser()
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
                    f"[{config.get_name()}]: Option 'managed_services: {raw}' "
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
        self.need_channel_update = storage.get("need_channel_update", False)
        self._is_valid = storage.get("is_valid", False)
        self.pip_version = tuple(storage.get("pip_version", []))
        if self.pip_version:
            ver_str = ".".join([str(part) for part in self.pip_version])
            self.log_info(f"Stored pip version: {ver_str}")
        return storage

    def _verify_path(
        self,
        config: ConfigHelper,
        option: str,
        path: pathlib.Path,
        check_file: bool = True,
        check_exe: bool = False
    ) -> None:
        base_msg = (
            f"Invalid path for option `{option}` in section "
            f"[{config.get_name()}]: Path `{path}`"
        )
        if not path.exists():
            raise config.error(f"{base_msg} does not exist")
        if check_file and not path.is_file():
            raise config.error(f"{base_msg} is not a file")
        if check_exe and not os.access(path, os.X_OK):
            raise config.error(f"{base_msg} is not executable")

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
            'debug_enabled': self.server.is_debug_enabled(),
            'need_channel_update': self.need_channel_update,
            'is_valid': self._is_valid,
            'configured_type': self.type,
            'info_tags': self.info_tags
        }

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['is_valid'] = self._is_valid
        storage['need_channel_update'] = self.need_channel_update
        storage['pip_version'] = list(self.pip_version)
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

    async def _update_python_requirements(
        self, requirements: Union[pathlib.Path, List[str]]
    ) -> None:
        if self.pip_cmd is None:
            return
        await self._update_pip()
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
            await self.cmd_helper.run_cmd(
                f"{self.pip_cmd} install {args}", timeout=1200., notify=True,
                retries=3
            )
        except Exception:
            self.log_exc("Error updating python requirements")

    async def _update_pip(self) -> None:
        if self.pip_cmd is None:
            return
        update_ver = await self._check_pip_version()
        if update_ver is None:
            return
        cur_vstr = ".".join([str(part) for part in self.pip_version])
        self.notify_status(
            f"Updating pip from version {cur_vstr} to {update_ver}..."
        )
        try:
            await self.cmd_helper.run_cmd(
                f"{self.pip_cmd} install pip=={update_ver}",
                timeout=1200., notify=True, retries=3
            )
        except Exception:
            self.log_exc("Error updating python pip")

    async def _check_pip_version(self) -> Optional[str]:
        if self.pip_cmd is None:
            return None
        self.notify_status("Checking pip version...")
        try:
            data: str = await self.cmd_helper.run_cmd_with_response(
                f"{self.pip_cmd} --version", timeout=30., retries=3
            )
            match = re.match(
                r"^pip ([0-9.]+) from .+? \(python ([0-9.]+)\)$", data.strip()
            )
            if match is None:
                return None
            pipver_str: str = match.group(1)
            pyver_str: str = match.group(2)
            pipver = tuple([int(part) for part in pipver_str.split(".")])
            pyver = tuple([int(part) for part in pyver_str.split(".")])
        except Exception:
            self.log_exc("Error Getting Pip Version")
            return None
        self.pip_version = pipver
        if not self.pip_version:
            return None
        self.log_info(
            f"Dectected pip version: {pipver_str}, Python {pyver_str}"
        )
        if pyver < (3, 7):
            return None
        if self.pip_version < MIN_PIP_VERSION:
            return ".".join([str(ver) for ver in MIN_PIP_VERSION])
        return None

    async def _build_virtualenv(self) -> None:
        if self.executable is None or self.venv_args is None:
            return
        bin_dir = self.executable.parent
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
        if not self.executable.exists():
            raise self.log_exc("Failed to create new virtualenv", False)
