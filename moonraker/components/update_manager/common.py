# Moonraker/Klipper update configuration
#
# Copyright (C) 2022  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import sys
import copy
import pathlib
from ...common import ExtendedEnum
from ...utils import source_info
from typing import (
    TYPE_CHECKING,
    Dict,
    Union,
    List
)

if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..klippy_connection import KlippyConnection

BASE_CONFIG: Dict[str, Dict[str, str]] = {
    "moonraker": {
        "origin": "https://github.com/arksine/moonraker.git",
        "requirements": "scripts/moonraker-requirements.txt",
        "venv_args": "-p python3",
        "system_dependencies": "scripts/system-dependencies.json",
        "virtualenv": sys.exec_prefix,
        "pip_environment_variables": "SKIP_CYTHON=Y",
        "path": str(source_info.source_path()),
        "managed_services": "moonraker"
    },
    "klipper": {
        "moved_origin": "https://github.com/kevinoconnor/klipper.git",
        "origin": "https://github.com/Klipper3d/klipper.git",
        "requirements": "scripts/klippy-requirements.txt",
        "venv_args": "-p python3",
        "install_script": "scripts/install-octopi.sh",
        "managed_services": "klipper"
    }
}

OPTION_OVERRIDES = ("channel", "pinned_commit", "refresh_interval")

class AppType(ExtendedEnum):
    NONE = 1
    WEB = 2
    GIT_REPO = 3
    ZIP = 4
    PYTHON = 5

    @classmethod
    def detect(cls, app_path: Union[str, pathlib.Path, None] = None):
        # If app path is None, detect Moonraker
        if isinstance(app_path, str):
            app_path = pathlib.Path(app_path).expanduser()
        if source_info.is_git_repo(app_path):
            return AppType.GIT_REPO
        elif app_path is None and source_info.is_vitualenv_project():
            return AppType.PYTHON
        else:
            return AppType.NONE

    @classmethod
    def valid_types(cls) -> List[AppType]:
        all_types = list(cls)
        all_types.remove(AppType.NONE)
        return all_types

    @property
    def supported_channels(self) -> List[Channel]:
        if self == AppType.NONE:
            return []
        elif self in [AppType.WEB, AppType.ZIP]:
            return [Channel.STABLE, Channel.BETA]  # type: ignore
        else:
            return list(Channel)

    @property
    def default_channel(self) -> Channel:
        if self == AppType.GIT_REPO:
            return Channel.DEV  # type: ignore
        return Channel.STABLE  # type: ignore

class Channel(ExtendedEnum):
    STABLE = 1
    BETA = 2
    DEV = 3

def get_base_configuration(config: ConfigHelper) -> ConfigHelper:
    server = config.get_server()
    base_cfg = copy.deepcopy(BASE_CONFIG)
    kconn: KlippyConnection = server.lookup_component("klippy_connection")
    base_cfg["moonraker"]["type"] = str(AppType.detect())
    base_cfg["klipper"]["path"] = str(kconn.path)
    base_cfg["klipper"]["env"] = str(kconn.executable)
    base_cfg["klipper"]["type"] = str(AppType.detect(kconn.path))
    default_channel = config.get("channel", None)
    # Check for configuration overrides
    for app_name in base_cfg.keys():
        if default_channel is not None:
            base_cfg[app_name]["channel"] = default_channel
        override_section = f"update_manager {app_name}"
        if not config.has_section(override_section):
            continue
        app_cfg = config[override_section]
        for opt in OPTION_OVERRIDES:
            if app_cfg.has_option(opt):
                base_cfg[app_name][opt] = app_cfg.get(opt)
    return config.read_supplemental_dict(base_cfg)
