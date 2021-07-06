# linux shell command execution utility
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import shlex
import logging
import signal
import asyncio
from tornado import gen
from tornado.locks import Lock
from utils import ServerError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Optional,
    Callable,
    Coroutine,
    Dict,
    Set,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from asyncio import BaseTransport
    OutputCallback = Optional[Callable[[bytes], None]]

class ShellCommandError(ServerError):
    def __init__(self,
                 message: str,
                 return_code: Optional[int],
                 stdout: Optional[bytes] = b"",
                 stderr: Optional[bytes] = b"",
                 status_code: int = 500
                 ) -> None:
        super().__init__(message, status_code=status_code)
        self.stdout = stdout or b""
        self.stderr = stderr or b""
        self.return_code = return_code

class SCProcess(asyncio.subprocess.Process):
    def initialize(self,
                   program_name: str,
                   std_out_cb: OutputCallback,
                   std_err_cb: OutputCallback,
                   log_stderr: bool
                   ) -> None:
        self.program_name = program_name
        self.std_out_cb = std_out_cb
        self.std_err_cb = std_err_cb
        self.log_stderr = log_stderr
        self.cancel_requested = False

    async def _read_stream_with_cb(self, fd: int) -> bytes:
        transport: BaseTransport = \
            self._transport.get_pipe_transport(fd)  # type: ignore
        if fd == 2:
            stream = self.stderr
            cb = self.std_err_cb
        else:
            assert fd == 1
            stream = self.stdout
            cb = self.std_out_cb
        assert stream is not None
        while not stream.at_eof():
            output = await stream.readline()
            if not output:
                break
            if fd == 2 and self.log_stderr:
                logging.info(
                    f"{self.program_name}: "
                    f"{output.decode(errors='ignore')}")
            output = output.rstrip(b'\n')
            if output and cb is not None:
                cb(output)
        transport.close()
        return output

    async def cancel(self, sig_idx: int = 1) -> None:
        if self.cancel_requested:
            return
        self.cancel_requested = True
        exit_success = False
        sig_idx = min(2, max(0, sig_idx))
        sigs = [signal.SIGINT, signal.SIGTERM, signal.SIGKILL][sig_idx:]
        for sig in sigs:
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

    async def communicate_with_cb(self,
                                  input: Optional[bytes] = None
                                  ) -> None:
        if input is not None:
            stdin: Coroutine = self._feed_stdin(input)  # type: ignore
        else:
            stdin = self._noop()  # type: ignore
        if self.stdout is not None and self.std_out_cb is not None:
            stdout: Coroutine = self._read_stream_with_cb(1)
        else:
            stdout = self._noop()  # type: ignore
        has_err_output = self.std_err_cb is not None or self.log_stderr
        if self.stderr is not None and has_err_output:
            stderr: Coroutine = self._read_stream_with_cb(2)
        else:
            stderr = self._noop()  # type: ignore
        stdin, stdout, stderr = await asyncio.tasks.gather(
            stdin, stdout, stderr, loop=self._loop)  # type: ignore
        await self.wait()

class ShellCommand:
    IDX_SIGINT = 0
    IDX_SIGTERM = 1
    IDX_SIGKILL = 2
    def __init__(self,
                 factory: ShellCommandFactory,
                 cmd: str,
                 std_out_callback: OutputCallback,
                 std_err_callback: OutputCallback,
                 env: Optional[Dict[str, str]] = None,
                 log_stderr: bool = False,
                 cwd: Optional[str] = None
                 ) -> None:
        self.factory = factory
        self.name = cmd
        self.std_out_cb = std_out_callback
        self.std_err_cb = std_err_callback
        cmd = os.path.expanduser(cmd)
        self.command = shlex.split(cmd)
        self.log_stderr = log_stderr
        self.env = env
        self.cwd = cwd
        self.proc: Optional[SCProcess] = None
        self.cancelled = False
        self.return_code: Optional[int] = None
        self.run_lock = Lock()

    async def cancel(self, sig_idx: int = 1) -> None:
        self.cancelled = True
        if self.proc is not None:
            await self.proc.cancel(sig_idx)

    def get_return_code(self) -> Optional[int]:
        return self.return_code

    def _reset_command_data(self) -> None:
        self.return_code = self.proc = None
        self.cancelled = False

    async def run(self,
                  timeout: float = 2.,
                  verbose: bool = True,
                  log_complete: bool = True,
                  sig_idx: int = 1
                  ) -> bool:
        async with self.run_lock:
            self.factory.add_running_command(self)
            self._reset_command_data()
            if not timeout:
                # Never timeout
                timeout = 9999999999999999.
            if self.std_out_cb is None and self.std_err_cb is None and \
                    not self.log_stderr:
                # No callbacks set so output cannot be verbose
                verbose = False
            if not await self._create_subprocess():
                self.factory.remove_running_command(self)
                return False
            assert self.proc is not None
            try:
                if verbose:
                    ret: Coroutine = self.proc.communicate_with_cb()
                else:
                    ret = self.proc.wait()
                await asyncio.wait_for(ret, timeout=timeout)
            except asyncio.TimeoutError:
                complete = False
                await self.proc.cancel(sig_idx)
            else:
                complete = not self.cancelled
            self.factory.remove_running_command(self)
            return self._check_proc_success(complete, log_complete)

    async def run_with_response(self,
                                timeout: float = 2.,
                                retries: int = 1,
                                log_complete: bool = True,
                                sig_idx: int = 1
                                ) -> str:
        async with self.run_lock:
            self.factory.add_running_command(self)
            retries = max(1, retries)
            while retries > 0:
                self._reset_command_data()
                timed_out = False
                stdout = stderr = b""
                if await self._create_subprocess():
                    assert self.proc is not None
                    try:
                        ret = self.proc.communicate()
                        stdout, stderr = await asyncio.wait_for(
                            ret, timeout=timeout)
                    except asyncio.TimeoutError:
                        complete = False
                        timed_out = True
                        await self.proc.cancel(sig_idx)
                    else:
                        complete = not self.cancelled
                        if self.log_stderr and stderr:
                            logging.info(
                                f"{self.command[0]}: "
                                f"{stderr.decode(errors='ignore')}")
                    if self._check_proc_success(complete, log_complete):
                        self.factory.remove_running_command(self)
                        return stdout.decode(errors='ignore').rstrip("\n")
                    if stdout:
                        logging.debug(
                            f"Shell command '{self.name}' output:"
                            f"\n{stdout.decode(errors='ignore')}")
                    if self.cancelled and not timed_out:
                        break
                retries -= 1
                await gen.sleep(.5)
            self.factory.remove_running_command(self)
            raise ShellCommandError(
                f"Error running shell command: '{self.command}'",
                self.return_code, stdout, stderr)

    async def _create_subprocess(self) -> bool:
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
                stderr=errpipe, env=self.env, cwd=self.cwd)
            self.proc = SCProcess(transport, protocol, loop)
            self.proc.initialize(self.command[0], self.std_out_cb,
                                 self.std_err_cb, self.log_stderr)
        except Exception:
            logging.exception(
                f"shell_command: Command ({self.name}) failed")
            return False
        return True

    def _check_proc_success(self,
                            complete: bool,
                            log_complete: bool
                            ) -> bool:
        assert self.proc is not None
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
    error = ShellCommandError
    def __init__(self, config: ConfigHelper) -> None:
        self.running_commands: Set[ShellCommand] = set()

    def add_running_command(self, cmd: ShellCommand) -> None:
        self.running_commands.add(cmd)

    def remove_running_command(self, cmd: ShellCommand) -> None:
        try:
            self.running_commands.remove(cmd)
        except KeyError:
            pass

    def build_shell_command(self,
                            cmd: str,
                            callback: OutputCallback = None,
                            std_err_callback: OutputCallback = None,
                            env: Optional[Dict[str, str]] = None,
                            log_stderr: bool = False,
                            cwd: Optional[str] = None
                            ) -> ShellCommand:
        return ShellCommand(self, cmd, callback, std_err_callback, env,
                            log_stderr, cwd)

    async def close(self) -> None:
        for cmd in self.running_commands:
            await cmd.cancel()

def load_component(config: ConfigHelper) -> ShellCommandFactory:
    return ShellCommandFactory(config)
