# Moonraker Exceptions
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
from typing import Any

class ServerError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        Exception.__init__(self, message)
        self.status_code = status_code

class AgentError(ServerError):
    def __init__(self, message: str, error_data: Any) -> None:
        ServerError.__init__(self, message, 424)
        self.error_data = error_data
