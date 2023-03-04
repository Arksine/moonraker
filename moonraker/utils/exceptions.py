# Moonraker Exceptions
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

class ServerError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        Exception.__init__(self, message)
        self.status_code = status_code
