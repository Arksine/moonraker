# Package definition for the update_manager
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
from . import update_manager as um

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ...confighelper import ConfigHelper

def load_component(config: ConfigHelper) -> um.UpdateManager:
    return um.load_component(config)
