# Base Deployment Interface
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import time
from ...utils import pretty_print_time

from typing import TYPE_CHECKING, Dict, Any, Optional, Coroutine
if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ...utils import ServerError
    from .update_manager import CommandHelper

class BaseDeploy:
    cmd_helper: CommandHelper
    def __init__(
        self,
        config: ConfigHelper,
        name: Optional[str] = None,
        prefix: str = "",
        cfg_hash: Optional[str] = None
    ) -> None:
        if name is None:
            name = self.parse_name(config)
        self.name = name
        if prefix:
            prefix = f"{prefix} {self.name}: "
        self.prefix = prefix
        self.server = config.get_server()
        self.refresh_interval = self.cmd_helper.get_refresh_interval()
        refresh_interval = config.getint('refresh_interval', None)
        if refresh_interval is not None:
            self.refresh_interval = refresh_interval * 60 * 60
        if cfg_hash is None:
            cfg_hash = config.get_hash().hexdigest()
        self.cfg_hash = cfg_hash

    @staticmethod
    def parse_name(config: ConfigHelper) -> str:
        name = config.get_name().split(maxsplit=1)[-1]
        if name.startswith("client "):
            # allow deprecated [update_manager client app] style names
            name = name[7:]
        return name

    @staticmethod
    def set_command_helper(cmd_helper: CommandHelper) -> None:
        BaseDeploy.cmd_helper = cmd_helper

    async def initialize(self) -> Dict[str, Any]:
        umdb = self.cmd_helper.get_umdb()
        storage: Dict[str, Any] = await umdb.get(self.name, {})
        self.last_refresh_time: float = storage.get('last_refresh_time', 0.0)
        self.last_cfg_hash: str = storage.get('last_config_hash', "")
        return storage

    def needs_refresh(self, log_remaining_time: bool = False) -> bool:
        next_refresh_time = self.last_refresh_time + self.refresh_interval
        remaining_time = int(next_refresh_time - time.time() + .5)
        if self.cfg_hash != self.last_cfg_hash or remaining_time <= 0:
            return True
        if log_remaining_time:
            self.log_info(f"Next refresh in: {pretty_print_time(remaining_time)}")
        return False

    def get_last_refresh_time(self) -> float:
        return self.last_refresh_time

    async def refresh(self) -> None:
        pass

    async def update(self) -> bool:
        return False

    async def rollback(self) -> bool:
        raise self.server.error(f"Rollback not available for {self.name}")

    def get_update_status(self) -> Dict[str, Any]:
        return {}

    def get_persistent_data(self) -> Dict[str, Any]:
        return {
            'last_config_hash': self.cfg_hash,
            'last_refresh_time': self.last_refresh_time
        }

    def _save_state(self) -> None:
        umdb = self.cmd_helper.get_umdb()
        self.last_refresh_time = time.time()
        self.last_cfg_hash = self.cfg_hash
        umdb[self.name] = self.get_persistent_data()

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

    def log_debug(self, msg: str) -> None:
        log_msg = f"{self.prefix}{msg}"
        logging.debug(log_msg)

    def notify_status(self, msg: str, is_complete: bool = False) -> None:
        log_msg = f"{self.prefix}{msg}"
        logging.debug(log_msg)
        self.cmd_helper.notify_update_response(log_msg, is_complete)

    def close(self) -> Optional[Coroutine]:
        return None
