# General Server Utilities
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging
import os
import subprocess

class ServerError(Exception):
    def __init__(self, message, status_code=400):
        Exception.__init__(self, message)
        self.status_code = status_code

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
            logging.debug("Error getting git version: %s", err)
    except OSError:
        logging.exception("Error runing git describe")

    return "?"
