# Moonraker/Klipper update configuration
#
# Copyright (C) 2022  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import sys
import copy
from typing import (
    TYPE_CHECKING,
    Dict
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from components.database import MoonrakerDatabase

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../../.."))
KLIPPER_DEFAULT_PATH = os.path.expanduser("~/klipper")
KLIPPER_DEFAULT_EXEC = os.path.expanduser("~/klippy-env/bin/python")

BASE_CONFIG: Dict[str, Dict[str, str]] = {
    "moonraker": {
        "origin": "https://github.com/arksine/moonraker.git",
        "requirements": "scripts/moonraker-requirements.txt",
        "venv_args": "-p python3",
        "install_script": "scripts/install-moonraker.sh",
        "host_repo": "arksine/moonraker",
        "env": sys.executable,
        "path": MOONRAKER_PATH,
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

def get_base_configuration(config: ConfigHelper, channel: str) -> ConfigHelper:
    server = config.get_server()
    base_cfg = copy.deepcopy(BASE_CONFIG)
    app_type = "zip" if channel == "stable" else "git_repo"
    base_cfg["moonraker"]["channel"] = channel
    base_cfg["moonraker"]["type"] = app_type
    base_cfg["klipper"]["channel"] = channel
    base_cfg["klipper"]["type"] = app_type
    db: MoonrakerDatabase = server.lookup_component('database')
    base_cfg["klipper"]["path"] = db.get_item(
        "moonraker", "update_manager.klipper_path", KLIPPER_DEFAULT_PATH
    ).result()
    base_cfg["klipper"]["env"] = db.get_item(
        "moonraker", "update_manager.klipper_exec", KLIPPER_DEFAULT_EXEC
    ).result()
    return config.read_supplemental_dict(base_cfg)
