# Deploy updates for applications managed by Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import pathlib
import hashlib
import logging
import re
import distro
import asyncio
import importlib
from .common import AppType, Channel
from .base_deploy import BaseDeploy
from ...utils import pip_utils
from ...utils import json_wrapper as jsonw

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
    from ...confighelper import ConfigHelper
    from ..klippy_connection import KlippyConnection as Klippy
    from .update_manager import CommandHelper
    from ..machine import Machine
    from ..file_manager.file_manager import FileManager

MIN_PIP_VERSION = (23, 3, 2)

SUPPORTED_CHANNELS = {
    AppType.WEB: [Channel.STABLE, Channel.BETA],
    AppType.ZIP: [Channel.STABLE, Channel.BETA],
    AppType.GIT_REPO: list(Channel)
}
TYPE_TO_CHANNEL = {
    AppType.WEB: Channel.STABLE,
    AppType.ZIP: Channel.STABLE,
    AppType.GIT_REPO: Channel.DEV
}

DISTRO_ALIASES = [distro.id()]
DISTRO_ALIASES.extend(distro.like().split())

class AppDeploy(BaseDeploy):
    def __init__(
            self, config: ConfigHelper, cmd_helper: CommandHelper, prefix: str
    ) -> None:
        super().__init__(config, cmd_helper, prefix=prefix)
        self.config = config
        type_choices = list(TYPE_TO_CHANNEL.keys())
        self.type = AppType.from_string(config.get('type'))
        if self.type not in type_choices:
            str_types = [str(t) for t in type_choices]
            raise config.error(
                f"Section [{config.get_name()}], Option 'type: {self.type}': "
                f"value must be one of the following choices: {str_types}"
            )
        self.channel = Channel.from_string(
            config.get("channel", str(TYPE_TO_CHANNEL[self.type]))
        )
        self.channel_invalid: bool = False
        if self.channel not in SUPPORTED_CHANNELS[self.type]:
            str_channels = [str(c) for c in SUPPORTED_CHANNELS[self.type]]
            self.channel_invalid = True
            invalid_channel = self.channel
            self.channel = TYPE_TO_CHANNEL[self.type]
            self.server.add_warning(
                f"[{config.get_name()}]: Invalid value '{invalid_channel}' for "
                f"option 'channel'. Type '{self.type}' supports the following "
                f"channels: {str_channels}.  Falling back to channel '{self.channel}'"
            )
        self._is_valid: bool = False
        self.virtualenv: Optional[pathlib.Path] = None
        self.py_exec: Optional[pathlib.Path] = None
        self.pip_cmd: Optional[str] = None
        self.pip_version: Tuple[int, ...] = tuple()
        self.venv_args: Optional[str] = None
        self.npm_pkg_json: Optional[pathlib.Path] = None
        self.python_reqs: Optional[pathlib.Path] = None
        self.install_script: Optional[pathlib.Path] = None
        self.system_deps_json: Optional[pathlib.Path] = None
        self.info_tags: List[str] = config.getlist("info_tags", [])
        self.managed_services: List[str] = []

    def _configure_path(self, config: ConfigHelper, reserve: bool = True) -> None:
        self.path = pathlib.Path(config.get('path')).expanduser().resolve()
        self._verify_path(config, 'path', self.path, check_file=False)
        if (
            reserve and self.name not in ["moonraker", "klipper"]
            and not self.path.joinpath(".writeable").is_file()
        ):
            fm: FileManager = self.server.lookup_component("file_manager")
            fm.add_reserved_path(f"update_manager {self.name}", self.path)

    def _configure_virtualenv(self, config: ConfigHelper) -> None:
        venv_path: Optional[pathlib.Path] = None
        if config.has_option("virtualenv"):
            venv_path = pathlib.Path(config.get("virtualenv")).expanduser()
            if not venv_path.is_absolute():
                venv_path = self.path.joinpath(venv_path)
            self._verify_path(config, 'virtualenv', venv_path, check_file=False)
        elif config.has_option("env"):
            # Deprecated
            if self.name != "klipper":
                self.log_info("Option 'env' is deprecated, use 'virtualenv' instead.")
            py_exec = pathlib.Path(config.get("env")).expanduser()
            self._verify_path(config, 'env', py_exec, check_exe=True)
            venv_path = py_exec.expanduser().parent.parent.resolve()
        if venv_path is not None:
            act_path = venv_path.joinpath("bin/activate")
            if not act_path.is_file():
                raise config.error(
                    f"[{config.get_name()}]: Invalid virtualenv at path {venv_path}. "
                    f"Verify that the 'virtualenv' option is set to a valid "
                    "virtualenv path."
                )
            self.py_exec = venv_path.joinpath("bin/python")
            if not (self.py_exec.is_file() and os.access(self.py_exec, os.X_OK)):
                raise config.error(
                    f"[{config.get_name()}]: Invalid python executable at "
                    f"{self.py_exec}. Verify that the 'virtualenv' option is set "
                    "to a valid virtualenv path."
                )
            self.log_info(f"Detected virtualenv: {venv_path}")
            self.virtualenv = venv_path
            pip_exe = self.virtualenv.joinpath("bin/pip")
            if pip_exe.is_file():
                self.pip_cmd = f"{self.py_exec} -m pip"
            else:
                self.log_info("Unable to locate pip executable")
        self.venv_args = config.get('venv_args', None)
        self.pip_env_vars = config.getdict("pip_environment_variables", None)

    def _configure_dependencies(
        self, config: ConfigHelper, node_only: bool = False
    ) -> None:
        if config.getboolean("enable_node_updates", False):
            self.npm_pkg_json = self.path.joinpath("package-lock.json")
            self._verify_path(config, 'enable_node_updates', self.npm_pkg_json)
        if node_only:
            return
        if self.py_exec is not None:
            self.python_reqs = self.path.joinpath(config.get("requirements"))
            self._verify_path(config, 'requirements', self.python_reqs)
        deps = config.get("system_dependencies", None)
        if deps is not None:
            self.system_deps_json = self.path.joinpath(deps).resolve()
            self._verify_path(config, 'system_dependencies', self.system_deps_json)
        else:
            # Fall back on deprecated "install_script" option if dependencies file
            # not present
            install_script = config.get('install_script', None)
            if install_script is not None:
                self.install_script = self.path.joinpath(install_script).resolve()
                self._verify_path(config, 'install_script', self.install_script)

    def _configure_managed_services(self, config: ConfigHelper) -> None:
        svc_default = []
        if config.getboolean("is_system_service", True):
            svc_default.append(self.name)
        svc_choices = [self.name, "klipper", "moonraker"]
        services: List[str] = config.getlist(
            "managed_services", svc_default, separator=None
        )
        if self.name in services:
            machine: Machine = self.server.lookup_component("machine")
            data_path: str = self.server.get_app_args()["data_path"]
            asvc = pathlib.Path(data_path).joinpath("moonraker.asvc")
            if not machine.is_service_allowed(self.name):
                self.server.add_warning(
                    f"[{config.get_name()}]: Moonraker is not permitted to "
                    f"restart service '{self.name}'.  To enable management "
                    f"of this service add {self.name} to the bottom of the "
                    f"file {asvc}.  To disable management for this service "
                    "set 'is_system_service: False' in the configuration "
                    "for this section."
                )
                services.clear()
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
        self.log_debug(
            f"Managed services: {self.managed_services}"
        )

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

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.pip_version = tuple(storage.get("pip_version", []))
        if self.pip_version:
            ver_str = ".".join([str(part) for part in self.pip_version])
            self.log_info(f"Stored pip version: {ver_str}")
        return storage

    def get_configured_type(self) -> AppType:
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
        if self.py_exec is None:
            return False
        try:
            return (
                self.path.samefile(app_path) and
                self.py_exec.samefile(executable)
            )
        except Exception:
            return False

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        raise NotImplementedError

    async def restart_service(self) -> None:
        if not self.managed_services:
            return
        machine: Machine = self.server.lookup_component("machine")
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
                machine.restart_moonraker_service()
            else:
                if svc == "klipper":
                    kconn: Klippy = self.server.lookup_component("klippy_connection")
                    svc = kconn.unit_name
                await machine.do_service_action("restart", svc)

    async def _read_system_dependencies(self) -> List[str]:
        eventloop = self.server.get_event_loop()
        if self.system_deps_json is not None:
            deps_json = self.system_deps_json
            try:
                ret = await eventloop.run_in_thread(deps_json.read_bytes)
                dep_info: Dict[str, List[str]] = jsonw.loads(ret)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception(f"Error reading system deps: {deps_json}")
                return []
            for distro_id in DISTRO_ALIASES:
                if distro_id in dep_info:
                    if not dep_info[distro_id]:
                        self.log_info(
                            f"Dependency file '{deps_json.name}' contains an empty "
                            f"package definition for linux distro '{distro_id}'"
                        )
                    return dep_info[distro_id]
            else:
                self.log_info(
                    f"Dependency file '{deps_json.name}' has no package definition "
                    f" for linux distro '{DISTRO_ALIASES[0]}'"
                )
                return []
        # Fall back on install script if configured
        if self.install_script is None:
            return []
        # Open install file file and read
        inst_path: pathlib.Path = self.install_script
        if not inst_path.is_file():
            self.log_info(f"Failed to open install script: {inst_path}")
            return []
        try:
            data = await eventloop.run_in_thread(inst_path.read_text)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(f"Error reading install script: {deps_json}")
            return []
        plines: List[str] = re.findall(r'PKGLIST="(.*)"', data)
        plines = [p.lstrip("${PKGLIST}").strip() for p in plines]
        packages: List[str] = []
        for line in plines:
            packages.extend(line.split())
        if not packages:
            self.log_info(f"No packages found in script: {inst_path}")
        return packages

    async def _read_python_reqs(self) -> List[str]:
        if self.python_reqs is None:
            return []
        pyreqs = self.python_reqs
        if not pyreqs.is_file():
            self.log_info(f"Failed to open python requirements file: {pyreqs}")
            return []
        eventloop = self.server.get_event_loop()
        return await eventloop.run_in_thread(
            pip_utils.read_requirements_file, self.python_reqs
        )

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'channel': str(self.channel),
            'debug_enabled': self.server.is_debug_enabled(),
            'channel_invalid': self.channel_invalid,
            'is_valid': self._is_valid,
            'configured_type': str(self.type),
            'info_tags': self.info_tags
        }

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['is_valid'] = self._is_valid
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
        if self.name == "moonraker":
            importlib.reload(pip_utils)
        pip_exec = pip_utils.AsyncPipExecutor(
            self.pip_cmd, self.server, self.cmd_helper.notify_update_response
        )
        # Check the current pip version
        self.notify_status("Checking pip version...")
        try:
            pip_ver = await pip_exec.get_pip_version()
            if pip_utils.check_pip_needs_update(pip_ver):
                cur_ver = pip_ver.pip_version_string
                update_ver = ".".join([str(part) for part in pip_utils.MIN_PIP_VERSION])
                self.notify_status(
                    f"Updating pip from version {cur_ver} to {update_ver}..."
                )
                await pip_exec.update_pip()
                self.pip_version = pip_utils.MIN_PIP_VERSION
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.notify_status(f"Pip Version Check Error: {e}")
            self.log_exc("Pip Version Check Error")
        self.notify_status("Updating python packages...")
        try:
            await pip_exec.install_packages(requirements, self.pip_env_vars)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_exc("Error updating python requirements")

    async def _collect_dependency_info(self) -> Dict[str, Any]:
        pkg_deps = await self._read_system_dependencies()
        pyreqs = await self._read_python_reqs()
        npm_hash = await self._get_file_hash(self.npm_pkg_json)
        logging.debug(
            f"\nApplication {self.name}: Pre-update dependencies:\n"
            f"Packages: {pkg_deps}\n"
            f"Python Requirements: {pyreqs}"
        )
        return {
            "system_packages": pkg_deps,
            "python_modules": pyreqs,
            "npm_hash": npm_hash
        }

    async def _update_dependencies(
        self, dep_info: Dict[str, Any], force: bool = False
    ) -> None:
        packages = await self._read_system_dependencies()
        modules = await self._read_python_reqs()
        logging.debug(
            f"\nApplication {self.name}: Post-update dependencies:\n"
            f"Packages: {packages}\n"
            f"Python Requirements: {modules}"
        )
        if not force:
            packages = list(set(packages) - set(dep_info["system_packages"]))
            modules = list(set(modules) - set(dep_info["python_modules"]))
        logging.debug(
            f"\nApplication {self.name}: Dependencies to install:\n"
            f"Packages: {packages}\n"
            f"Python Requirements: {modules}\n"
            f"Force All: {force}"
        )
        if packages:
            await self._install_packages(packages)
        if modules:
            await self._update_python_requirements(self.python_reqs or modules)
        npm_hash: Optional[str] = dep_info["npm_hash"]
        ret = await self._check_need_update(npm_hash, self.npm_pkg_json)
        if force or ret:
            if self.npm_pkg_json is not None:
                self.notify_status("Updating Node Packages...")
                try:
                    await self.cmd_helper.run_cmd(
                        "npm ci --only=prod", notify=True, timeout=600.,
                        cwd=str(self.path)
                    )
                except Exception:
                    self.notify_status("Node Package Update failed")
