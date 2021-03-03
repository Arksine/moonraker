# linux shell command execution utility
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import shlex
import logging
import asyncio
from tornado import gen

class SCProcess(asyncio.subprocess.Process):
    def initialize(self, cb, log_stderr, program):
        self.callback = cb
        self.log_stderr = log_stderr
        self.program = program
        self.partial_data = b""

    async def _read_stream_with_cb(self, fd):
        transport = self._transport.get_pipe_transport(fd)
        if fd == 2:
            stream = self.stderr
        else:
            assert fd == 1
            stream = self.stdout
        while not stream.at_eof():
            output = await stream.readline()
            if not output:
                break
            if fd == 2 and self.log_stderr:
                logging.info(f"{self.program}: {output.decode()}")
            else:
                output = output.rstrip(b'\n')
                if output:
                    self.callback(output)
        transport.close()
        return output

    def cancel(self):
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.terminate()

    async def communicate_with_cb(self, input=None):
        if input is not None:
            stdin = self._feed_stdin(input)
        else:
            stdin = self._noop()
        if self.stdout is not None:
            stdout = self._read_stream_with_cb(1)
        else:
            stdout = self._noop()
        if self.stderr is not None:
            stderr = self._read_stream_with_cb(2)
        else:
            stderr = self._noop()
        stdin, stdout, stderr = await asyncio.tasks.gather(
            stdin, stdout, stderr, loop=self._loop)
        await self.wait()

class ShellCommand:
    def __init__(self, cmd, callback, log_stderr=False):
        self.name = cmd
        self.output_cb = callback
        cmd = os.path.expanduser(cmd)
        self.command = shlex.split(cmd)
        self.program = self.command[0]
        self.log_stderr = log_stderr
        self.proc = None
        self.cancelled = False
        self.return_code = None

    def cancel(self):
        self.cancelled = True
        if self.proc is not None:
            self.proc.cancel()

    def get_return_code(self):
        return self.return_code

    async def run(self, timeout=2., verbose=True):
        self.return_code = self.proc = None
        self.cancelled = False
        if timeout is None:
            # Never timeout
            timeout = 9999999999999999.
        if not timeout or self.output_cb is None:
            # Fire and forget commands cannot be verbose as we can't
            # clean up after the process terminates
            verbose = False
        if not await self._create_subprocess():
            return False
        if not timeout:
            # fire and forget, return from execution
            return True
        try:
            if verbose:
                ret = self.proc.communicate_with_cb()
            else:
                ret = self.proc.wait()
            await asyncio.wait_for(ret, timeout=timeout)
        except asyncio.TimeoutError:
            complete = False
            self.proc.terminate()
        else:
            complete = not self.cancelled
        return self._check_proc_success(complete)

    async def run_with_response(self, timeout=2., retries=1):
        self.return_code = self.proc = None
        self.cancelled = False
        while retries > 0:
            stdout = stderr = None
            if await self._create_subprocess():
                try:
                    ret = self.proc.communicate()
                    stdout, stderr = await asyncio.wait_for(
                        ret, timeout=timeout)
                except asyncio.TimeoutError:
                    complete = False
                    self.proc.terminate()
                else:
                    complete = not self.cancelled
                    if self.log_stderr:
                        logging.info(f"{self.program}: {stderr.decode()}")
                if self._check_proc_success(complete):
                    return stdout.decode().rstrip("\n")
                elif stdout:
                    logging.debug(
                        f"Shell command '{self.name}' output:"
                        f"\n{stdout.decode()}")
            retries -= 1
            await gen.sleep(.5)
        return None

    async def _create_subprocess(self):
        loop = asyncio.get_event_loop()

        def protocol_factory():
            return asyncio.subprocess.SubprocessStreamProtocol(
                limit=2**20, loop=loop)
        try:
            errpipe = asyncio.subprocess.PIPE if self.log_stderr \
                else asyncio.subprocess.STDOUT
            transport, protocol = await loop.subprocess_exec(
                protocol_factory, *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=errpipe)
            self.proc = SCProcess(transport, protocol, loop)
            self.proc.initialize(self.output_cb, self.log_stderr, self.program)
        except Exception:
            logging.exception(
                f"shell_command: Command ({self.name}) failed")
            return False
        return True

    def _check_proc_success(self, complete):
        self.return_code = self.proc.returncode
        success = self.return_code == 0 and complete
        if success:
            msg = f"Command ({self.name}) successfully finished"
        elif self.cancelled:
            msg = f"Command ({self.name}) cancelled"
        elif not complete:
            msg = f"Command ({self.name}) timed out"
        else:
            msg = f"Command ({self.name}) exited with return code" \
                f" {self.return_code}"
        logging.info(msg)
        return success

class ShellCommandFactory:
    def build_shell_command(self, cmd, callback=None, log_stderr=False):
        return ShellCommand(cmd, callback, log_stderr)

def load_plugin(config):
    return ShellCommandFactory()
