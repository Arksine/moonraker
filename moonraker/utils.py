# General Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging
import logging.handlers
import os
import subprocess
import asyncio
from queue import SimpleQueue as Queue

class ServerError(Exception):
    def __init__(self, message, status_code=400):
        Exception.__init__(self, message)
        self.status_code = status_code

# Coroutine friendly QueueHandler courtesy of Martjin Pieters:
# https://www.zopatista.com/python/2019/05/11/asyncio-logging/
class LocalQueueHandler(logging.handlers.QueueHandler):
    def emit(self, record: logging.LogRecord) -> None:
        # Removed the call to self.prepare(), handle task cancellation
        try:
            self.enqueue(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.handleError(record)

class MoonrakerLoggingHandler(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, filename, **kwargs):
        super(MoonrakerLoggingHandler, self).__init__(filename, **kwargs)
        self.header = "Moonraker Log Start...\n"
        self.header += "Git Version: " + get_software_version() + "\n"
        self.header += "="*80 + "\n"
        if self.stream is not None:
            self.stream.write(self.header)

    def doRollover(self):
        super(MoonrakerLoggingHandler, self).doRollover()
        if self.stream is not None:
            self.stream.write(self.header)

# Parse the git version from the command line.  This code
# is borrowed from Klipper.
def get_software_version():
    moonraker_path = os.path.join(
        os.path.dirname(__file__), '..')

    # Obtain version info from "git" program
    prog = ('git', '-C', moonraker_path, 'describe', '--always',
            '--tags', '--long', '--dirty')
    try:
        process = subprocess.Popen(prog, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        ver, err = process.communicate()
        retcode = process.wait()
        if retcode == 0:
            version = ver.strip()
            if isinstance(version, bytes):
                version = version.decode()
            return version
        else:
            logging.debug(f"Error getting git version: {err}")
    except OSError:
        logging.exception("Error runing git describe")

    return "?"

def setup_logging(log_file):
    root_logger = logging.getLogger()
    queue = Queue()
    queue_handler = LocalQueueHandler(queue)
    root_logger.addHandler(queue_handler)
    root_logger.setLevel(logging.INFO)
    file_hdlr = MoonrakerLoggingHandler(
        log_file, when='midnight', backupCount=2)
    formatter = logging.Formatter(
        '%(asctime)s [%(filename)s:%(funcName)s()] - %(message)s')
    file_hdlr.setFormatter(formatter)
    listener = logging.handlers.QueueListener(queue, file_hdlr)
    listener.start()
