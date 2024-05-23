# Moonraker/Klipper update configuration
#
# Copyright (C) 2022  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import sys
import copy
import pathlib
from ...common import ExtendedEnum
from ...utils import source_info
from typing import (
    TYPE_CHECKING,
    Dict,
    Union
)

if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..database import MoonrakerDatabase

KLIPPER_DEFAULT_PATH = os.path.expanduser("~/klipper")
KLIPPER_DEFAULT_EXEC = os.path.expanduser("~/klippy-env/bin/python")

BASE_CONFIG: Dict[str, Dict[str, str]] = {
    "moonraker": {
        "origin": "https://github.com/arksine/moonraker.git",
        "requirements": "scripts/moonraker-requirements.txt",
        "venv_args": "-p python3",
        "system_dependencies": "scripts/system-dependencies.json",
        "host_repo": "arksine/moonraker",
        "virtualenv": sys.exec_prefix,
        "pip_environment_variables": "SKIP_CYTHON=Y",
        "path": str(source_info.source_path()),
        "managed_services": "moonraker"
    },
    "klipper": {
        "moved_origin": "https://github.com/kevinoconnor/klipper.git",
        "origin": "https://github.com/Klipper3d/klipper.git",
        "requirements": "scripts/klippy-requirements.txt",
        "venv_args": "-p python2",
        "install_script": "scripts/install-octopi.sh",
        "host_repo": "arksine/moonraker",
        "managed_services": "klipper"
    }
}

class AppType(ExtendedEnum):
    NONE = 1
    WEB = 2
    GIT_REPO = 3
    ZIP = 4

class Channel(ExtendedEnum):
    STABLE = 1
    BETA = 2
    DEV = 3

def get_app_type(app_path: Union[str, pathlib.Path]) -> AppType:
    if isinstance(app_path, str):
        app_path = pathlib.Path(app_path).expanduser()
    # None type will perform checks on Moonraker
    if source_info.is_git_repo(app_path):
        return AppType.GIT_REPO
    else:
        return AppType.NONE

def get_base_configuration(config: ConfigHelper) -> ConfigHelper:
    server = config.get_server()
    base_cfg = copy.deepcopy(BASE_CONFIG)
    base_cfg["moonraker"]["type"] = str(get_app_type(source_info.source_path()))
    db: MoonrakerDatabase = server.lookup_component('database')
    base_cfg["klipper"]["path"] = db.get_item(
        "moonraker", "update_manager.klipper_path", KLIPPER_DEFAULT_PATH
    ).result()
    base_cfg["klipper"]["env"] = db.get_item(
        "moonraker", "update_manager.klipper_exec", KLIPPER_DEFAULT_EXEC
    ).result()
    base_cfg["klipper"]["type"] = str(get_app_type(base_cfg["klipper"]["path"]))
    channel = config.get("channel", "dev")
    base_cfg["moonraker"]["channel"] = channel
    base_cfg["klipper"]["channel"] = channel
    if config.has_section("update_manager moonraker"):
        mcfg = config["update_manager moonraker"]
        base_cfg["moonraker"]["channel"] = mcfg.get("channel", channel)
        commit = mcfg.get("pinned_commit", None)
        if commit is not None:
            base_cfg["moonraker"]["pinned_commit"] = commit
    if config.has_section("update_manager klipper"):
        kcfg = config["update_manager klipper"]
        base_cfg["klipper"]["channel"] = kcfg.get("channel", channel)
        commit = kcfg.get("pinned_commit", None)
        if commit is not None:
            base_cfg["klipper"]["pinned_commit"] = commit
    return config.read_supplemental_dict(base_cfg)
