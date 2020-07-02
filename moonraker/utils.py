# General Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging
import json

DEBUG = True

class ServerError(Exception):
    def __init__(self, message, status_code=400):
        Exception.__init__(self, message)
        self.status_code = status_code

# XXX - Currently logging over the socket is not implemented.
# I don't think it would be wise to log everything over the
# socket, however it may be useful to log some specific items.
# Decide what to do, then either finish the implementation or
# remove this code
class SocketLoggingHandler(logging.Handler):
    def __init__(self, server_manager):
        super(SocketLoggingHandler, self).__init__()
        self.server_manager = server_manager

    def emit(self, record):
        record.msg = "[MOONRAKER]: " + record.msg
        # XXX - Convert log record to dict before sending,
        # the klippy_send function will handle serialization

        self.server_manager.klippy_send(record)
