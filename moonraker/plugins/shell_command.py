# linux shell command execution utility
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import shlex
import subprocess
import logging
import tornado
from tornado import gen
from tornado.ioloop import IOLoop

class ShellCommand:
    def __init__(self, cmd, callback=None):
        self.io_loop = IOLoop.current()
        self.name = cmd
        self.output_cb = callback
        cmd = os.path.expanduser(cmd)
        self.command = shlex.split(cmd)
        self.partial_output = b""
        self.cancelled = False

    def _process_output(self, fd, events):
        if events & IOLoop.ERROR:
            return
        try:
            data = os.read(fd, 4096)
        except Exception:
            return
        data = self.partial_output + data
        if b'\n' not in data:
            self.partial_output = data
            return
        elif data[-1] != b'\n':
            split = data.rfind(b'\n') + 1
            self.partial_output = data[split:]
            data = data[:split]
        try:
            self.output_cb(data)
        except Exception:
            logging.exception("Error writing command output")

    def cancel(self):
        self.cancelled = True

    async def run(self, timeout=2., verbose=True):
        fd = None
        if timeout is None:
            # Never timeout
            timeout = 9999999999999999.
        if not timeout or self.output_cb is None:
            # Fire and forget commands cannot be verbose as we can't
            # clean up after the process terminates
            verbose = False
        try:
            proc = subprocess.Popen(
                self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except Exception:
            logging.exception(
                f"shell_command: Command ({self.name}) failed")
            return
        if verbose:
            fd = proc.stdout.fileno()
            self.io_loop.add_handler(
                fd, self._process_output, IOLoop.READ | IOLoop.ERROR)
        elif not timeout:
            # fire and forget, return from execution
            return True
        sleeptime = 0
        complete = False
        while sleeptime < timeout:
            await gen.sleep(.05)
            sleeptime += .05
            if proc.poll() is not None:
                complete = True
                break
            if self.cancelled:
                break
        if not complete:
            proc.terminate()
        if verbose:
            if self.partial_output:
                self.output_cb(self.partial_output)
                self.partial_output = b""
            if complete:
                msg = f"Command ({self.name}) finished"
            elif self.cancelled:
                msg = f"Command ({self.name}) cancelled"
            else:
                msg = f"Command ({self.name}) timed out"
            logging.info(msg)
            self.io_loop.remove_handler(fd)
        return complete

    async def run_with_response(self, timeout=2.):
        result = []

        def cb(data):
            data = data.strip()
            if data:
                result.append(data.decode())
        prev_cb = self.output_cb
        self.output_cb = cb
        await self.run(timeout)
        self.output_cb = prev_cb
        return "\n".join(result)


class ShellCommandFactory:
    def build_shell_command(self, cmd, callback):
        return ShellCommand(cmd, callback)

def load_plugin(config):
    return ShellCommandFactory()
