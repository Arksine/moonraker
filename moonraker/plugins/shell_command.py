# linux shell command execution utility
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import shlex
import logging
import signal
import asyncio
from tornado import gen

class SCProcess(asyncio.subprocess.Process):
    def initialize(self, program_name, std_out_cb, std_err_cb, log_stderr):
        self.program_name = program_name
        self.std_out_cb = std_out_cb
        self.std_err_cb = std_err_cb
        self.log_stderr = log_stderr
        self.partial_data = b""
        self.cancel_requested = False

    async def _read_stream_with_cb(self, fd):
        transport = self._transport.get_pipe_transport(fd)
        if fd == 2:
            stream = self.stderr
            cb = self.std_err_cb
        else:
            assert fd == 1
            stream = self.stdout
            cb = self.std_out_cb
        while not stream.at_eof():
            output = await stream.readline()
            if not output:
                break
            if fd == 2 and self.log_stderr:
                logging.info(f"{self.program_name}: {output.decode()}")
            output = output.rstrip(b'\n')
            if output and cb is not None:
                cb(output)
        transport.close()
        return output

    async def cancel(self):
        if self.cancel_requested:
            return
        self.cancel_requested = True
        exit_success = False
        for sig in [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]:
            self.send_signal(sig)
            try:
                ret = self.wait()
                await asyncio.wait_for(ret, timeout=2.)
            except asyncio.TimeoutError:
                continue
            logging.debug(f"Command '{self.program_name}' exited with "
                          f"signal: {sig.name}")
            exit_success = True
            break
        if not exit_success:
            logging.info(f"WARNING: {self.program_name} did not cleanly exit")
        if self.stdout is not None:
            self.stdout.feed_eof()
        if self.stderr is not None:
            self.stderr.feed_eof()

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
    def __init__(self, cmd, std_out_callback, std_err_callback,
                 env=None, log_stderr=False):
        self.name = cmd
        self.std_out_cb = std_out_callback
        self.std_err_cb = std_err_callback
        cmd = os.path.expanduser(cmd)
        self.command = shlex.split(cmd)
        self.log_stderr = log_stderr
        self.env = env
        self.proc = None
        self.cancelled = False
        self.return_code = None

    async def cancel(self):
        self.cancelled = True
        if self.proc is not None:
            await self.proc.cancel()

    def get_return_code(self):
        return self.return_code

    async def run(self, timeout=2., verbose=True, log_complete=True):
        self.return_code = self.proc = None
        self.cancelled = False
        if not timeout:
            # Never timeout
            timeout = 9999999999999999.
        if self.std_out_cb is None and self.std_err_cb is None:
            # No callbacks set so output cannot be verbose
            verbose = False
        if not await self._create_subprocess():
            return False
        try:
            if verbose:
                ret = self.proc.communicate_with_cb()
            else:
                ret = self.proc.wait()
            await asyncio.wait_for(ret, timeout=timeout)
        except asyncio.TimeoutError:
            complete = False
            await self.proc.cancel()
        else:
            complete = not self.cancelled
        return self._check_proc_success(complete, log_complete)

    async def run_with_response(self, timeout=2., retries=1,
                                log_complete=True):
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
                    await self.proc.cancel()
                else:
                    complete = not self.cancelled
                    if self.log_stderr and stderr:
                        logging.info(f"{self.command[0]}: {stderr.decode()}")
                if self._check_proc_success(complete, log_complete):
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
            if self.std_err_cb is not None or self.log_stderr:
                errpipe = asyncio.subprocess.PIPE
            else:
                errpipe = asyncio.subprocess.STDOUT
            transport, protocol = await loop.subprocess_exec(
                protocol_factory, *self.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=errpipe, env=self.env)
            self.proc = SCProcess(transport, protocol, loop)
            self.proc.initialize(self.command[0], self.std_out_cb,
                                 self.std_err_cb, self.log_stderr)
        except Exception:
            logging.exception(
                f"shell_command: Command ({self.name}) failed")
            return False
        return True

    def _check_proc_success(self, complete, log_complete):
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
        if log_complete:
            logging.info(msg)
        return success

class ShellCommandFactory:
    def build_shell_command(self, cmd, callback=None, std_err_callback=None,
                            env=None, log_stderr=False):
        return ShellCommand(cmd, callback, std_err_callback, env, log_stderr)

def load_plugin(config):
    return ShellCommandFactory()
