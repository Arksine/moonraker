# Base Deployment Interface
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging

from typing import TYPE_CHECKING, Dict, Any
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from utils import ServerError
    from .update_manager import CommandHelper

class BaseDeploy:
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        name_parts = config.get_name().split()
        self.name = name_parts[-1]
        self.server = config.get_server()
        self.cmd_helper = cmd_helper
        if name_parts == 1:
            self.prefix: str = ""
        if config.get('type', "") == "web":
            self.prefix = f"Web Client {self.name}: "
        else:
            self.prefix = f"Application {self.name}: "

    async def refresh(self) -> None:
        pass

    async def update(self) -> bool:
        return False

    def get_update_status(self) -> Dict[str, Any]:
        return {}

    def log_exc(self, msg: str, traceback: bool = True) -> ServerError:
        log_msg = f"{self.prefix}{msg}"
        if traceback:
            logging.exception(log_msg)
        else:
            logging.info(log_msg)
        return self.server.error(msg)

    def log_info(self, msg: str) -> None:
        log_msg = f"{self.prefix}{msg}"
        logging.info(log_msg)

    def notify_status(self, msg: str, is_complete: bool = False) -> None:
        log_msg = f"{self.prefix}{msg}"
        logging.debug(log_msg)
        self.cmd_helper.notify_update_response(log_msg, is_complete)
