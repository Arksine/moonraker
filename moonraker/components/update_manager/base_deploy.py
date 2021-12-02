# Base Deployment Interface
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import time

from typing import TYPE_CHECKING, Dict, Any, Optional
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from utils import ServerError
    from .update_manager import CommandHelper

class BaseDeploy:
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper,
                 name: Optional[str] = None,
                 prefix: str = "",
                 cfg_hash: Optional[str] = None
                 ) -> None:
        if name is None:
            name = config.get_name().split()[-1]
        self.name = name
        if prefix:
            prefix = f"{prefix} {self.name}: "
        self.prefix = prefix
        self.server = config.get_server()
        self.cmd_helper = cmd_helper
        self.refresh_interval = cmd_helper.get_refresh_interval()
        refresh_interval = config.getint('refresh_interval', None)
        if refresh_interval is not None:
            self.refresh_interval = refresh_interval * 60 * 60
        if cfg_hash is None:
            cfg_hash = config.get_hash().hexdigest()
        self.cfg_hash = cfg_hash
        storage: Dict[str, Any] = self._load_storage()
        self.last_refresh_time: float = storage.get('last_refresh_time', 0.0)

    def needs_refresh(self) -> bool:
        storage = self._load_storage()
        last_cfg_hash = storage.get('last_config_hash', "")
        next_refresh_time = self.last_refresh_time + self.refresh_interval
        return (
            self.cfg_hash != last_cfg_hash or
            time.time() > next_refresh_time
        )

    def get_last_refresh_time(self) -> float:
        return self.last_refresh_time

    def _load_storage(self) -> Dict[str, Any]:
        umdb = self.cmd_helper.get_umdb()
        return umdb.get(self.name, {})

    async def refresh(self) -> None:
        pass

    async def update(self) -> bool:
        return False

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

    def notify_status(self, msg: str, is_complete: bool = False) -> None:
        log_msg = f"{self.prefix}{msg}"
        logging.debug(log_msg)
        self.cmd_helper.notify_update_response(log_msg, is_complete)
