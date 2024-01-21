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
from ..utils import ServerError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Awaitable,
    List,
    Optional,
    Callable,
    Coroutine,
    Dict,
    Set,
    cast
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    OutputCallback = Optional[Callable[[bytes], None]]

class ShellCommandError(ServerError):
    def __init__(
        self,
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

class ShellCommandProtocol(asyncio.subprocess.SubprocessStreamProtocol):
    def __init__(
        self,
        limit: int,
        loop: asyncio.events.AbstractEventLoop,
        std_out_cb: OutputCallback = None,
        std_err_cb: OutputCallback = None,
        log_stderr: bool = False
    ) -> None:
        self._loop = loop
        self._pipe_fds: List[int] = []
        super().__init__(limit, loop)
        self.std_out_cb = std_out_cb
        self.std_err_cb = std_err_cb
        self.log_stderr = log_stderr
        self.pending_data: List[bytes] = [b"", b""]

    def connection_made(
        self, transport: asyncio.transports.BaseTransport
    ) -> None:
        transport = cast(asyncio.SubprocessTransport, transport)
        self._transport = transport
        stdout_transport = transport.get_pipe_transport(1)
        if stdout_transport is not None:
            self._pipe_fds.append(1)

        stderr_transport = transport.get_pipe_transport(2)
        if stderr_transport is not None:
            self._pipe_fds.append(2)

        stdin_transport = transport.get_pipe_transport(0)
        if stdin_transport is not None:
            self.stdin = asyncio.streams.StreamWriter(
                stdin_transport,  # type: ignore
                protocol=self,
                reader=None,
                loop=self._loop
            )

    def pipe_data_received(self, fd: int, data: bytes | str) -> None:
        cb = None
        data_idx = fd - 1
        if fd == 1:
            cb = self.std_out_cb
        elif fd == 2:
            cb = self.std_err_cb
            if self.log_stderr:
                if isinstance(data, bytes):
                    msg = data.decode(errors='ignore')
                else:
                    msg = data
                logging.info(msg)
        if cb is not None:
            if isinstance(data, str):
                data = data.encode()
            lines = data.split(b'\n')
            lines[0] = self.pending_data[data_idx] + lines[0]
            self.pending_data[data_idx] = lines.pop()
            for line in lines:
                if not line:
                    continue
                cb(line)

    def pipe_connection_lost(
        self, fd: int, exc: Exception | None
    ) -> None:
        cb = None
        pending = b""
        if fd == 1:
            cb = self.std_out_cb
            pending = self.pending_data[0]
        elif fd == 2:
            cb = self.std_err_cb
            pending = self.pending_data[1]
        if pending and cb is not None:
            cb(pending)
        super().pipe_connection_lost(fd, exc)


class ShellCommand:
    IDX_SIGINT = 0
    IDX_SIGTERM = 1
    IDX_SIGKILL = 2
    def __init__(
        self,
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
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.cancelled = False
        self.return_code: Optional[int] = None
        self.run_lock = asyncio.Lock()

    async def cancel(self, sig_idx: int = 1) -> None:
        if self.cancelled:
            return
        self.cancelled = True
        if self.proc is not None:
            exit_success = False
            sig_idx = min(2, max(0, sig_idx))
            sigs = [signal.SIGINT, signal.SIGTERM, signal.SIGKILL][sig_idx:]
            for sig in sigs:
                try:
                    self.proc.send_signal(sig)
                    ret = self.proc.wait()
                    await asyncio.wait_for(ret, timeout=2.)
                except asyncio.TimeoutError:
                    continue
                except ProcessLookupError:
                    pass
                logging.debug(f"Command '{self.name}' exited with "
                              f"signal: {sig.name}")
                exit_success = True
                break
            if not exit_success:
                logging.info(f"WARNING: {self.name} did not cleanly exit")

    def get_return_code(self) -> Optional[int]:
        return self.return_code

    def _reset_command_data(self) -> None:
        self.return_code = self.proc = None
        self.cancelled = False

    async def run(
        self,
        timeout: float = 2.,
        verbose: bool = True,
        log_complete: bool = True,
        sig_idx: int = 1,
        proc_input: Optional[str] = None,
        success_codes: Optional[List[int]] = None
    ) -> bool:
        async with self.run_lock:
            self.factory.add_running_command(self)
            self._reset_command_data()
            if not timeout:
                # Never timeout
                timeout = 9999999999999999.
            if (
                self.std_out_cb is None
                and self.std_err_cb is None and
                not self.log_stderr
            ):
                # No callbacks set so output cannot be verbose
                verbose = False
            created = await self._create_subprocess(
                verbose, proc_input is not None)
            if not created:
                self.factory.remove_running_command(self)
                return False
            assert self.proc is not None
            try:
                if proc_input is not None:
                    ret: Coroutine = self.proc.communicate(
                        input=proc_input.encode())
                else:
                    ret = self.proc.wait()
                await asyncio.wait_for(ret, timeout=timeout)
            except asyncio.TimeoutError:
                complete = False
                await self.cancel(sig_idx)
            else:
                complete = not self.cancelled
            self.factory.remove_running_command(self)
            return self._check_proc_success(
                complete, log_complete, success_codes
            )

    async def run_with_response(
        self,
        timeout: float = 2.,
        attempts: int = 1,
        log_complete: bool = True,
        sig_idx: int = 1,
        proc_input: Optional[str] = None,
        success_codes: Optional[List[int]] = None
    ) -> str:
        async with self.run_lock:
            self.factory.add_running_command(self)
            attempts = max(1, attempts)
            stdin: Optional[bytes] = None
            if proc_input is not None:
                stdin = proc_input.encode()
            while attempts > 0:
                self._reset_command_data()
                timed_out = False
                stdout = stderr = b""
                if await self._create_subprocess(has_input=stdin is not None):
                    assert self.proc is not None
                    try:
                        ret = self.proc.communicate(input=stdin)
                        stdout, stderr = await asyncio.wait_for(
                            ret, timeout=timeout)
                    except asyncio.TimeoutError:
                        complete = False
                        timed_out = True
                        await self.cancel(sig_idx)
                    else:
                        complete = not self.cancelled
                        if self.log_stderr and stderr:
                            logging.info(
                                f"{self.command[0]}: "
                                f"{stderr.decode(errors='ignore')}")
                    if self._check_proc_success(
                        complete, log_complete, success_codes
                    ):
                        self.factory.remove_running_command(self)
                        return stdout.decode(errors='ignore').rstrip("\n")
                    if stdout:
                        logging.debug(
                            f"Shell command '{self.name}' output:"
                            f"\n{stdout.decode(errors='ignore')}")
                    if self.cancelled and not timed_out:
                        break
                attempts -= 1
                await asyncio.sleep(.5)
            self.factory.remove_running_command(self)
            raise ShellCommandError(
                f"Error running shell command: '{self.name}'",
                self.return_code, stdout, stderr)

    async def _create_subprocess(
        self,
        use_callbacks: bool = False,
        has_input: bool = False
    ) -> bool:
        loop = asyncio.get_running_loop()

        def protocol_factory():
            return ShellCommandProtocol(
                limit=2**20, loop=loop, std_out_cb=self.std_out_cb,
                std_err_cb=self.std_err_cb, log_stderr=self.log_stderr
            )
        try:
            stdpipe: Optional[int] = None
            if has_input:
                stdpipe = asyncio.subprocess.PIPE
            if self.std_err_cb is not None or self.log_stderr:
                errpipe = asyncio.subprocess.PIPE
            else:
                errpipe = asyncio.subprocess.STDOUT
            if use_callbacks:
                transport, protocol = await loop.subprocess_exec(
                    protocol_factory, *self.command, stdin=stdpipe,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=errpipe, env=self.env, cwd=self.cwd)
                self.proc = asyncio.subprocess.Process(
                    transport, protocol, loop)
            else:
                self.proc = await asyncio.create_subprocess_exec(
                    *self.command, stdin=stdpipe,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=errpipe, env=self.env, cwd=self.cwd)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(
                f"shell_command: Command ({self.name}) failed")
            return False
        return True

    def _check_proc_success(
        self,
        complete: bool,
        log_complete: bool,
        success_codes: Optional[List[int]] = None
    ) -> bool:
        assert self.proc is not None
        if success_codes is None:
            success_codes = [0]
        self.return_code = self.proc.returncode
        success = self.return_code in success_codes and complete
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

    def build_shell_command(
        self,
        cmd: str,
        callback: OutputCallback = None,
        std_err_callback: OutputCallback = None,
        env: Optional[Dict[str, str]] = None,
        log_stderr: bool = False,
        cwd: Optional[str] = None
    ) -> ShellCommand:
        return ShellCommand(
            self, cmd, callback, std_err_callback, env, log_stderr, cwd
        )

    def run_cmd_async(
        self,
        cmd: str,
        callback: OutputCallback = None,
        std_err_callback: OutputCallback = None,
        timeout: float = 2.,
        attempts: int = 1,
        verbose: bool = True,
        sig_idx: int = 1,
        proc_input: Optional[str] = None,
        log_complete: bool = True,
        log_stderr: bool = False,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        success_codes: Optional[List[int]] = None
    ) -> Awaitable[None]:
        """
        Runs a command and processes responses as they are received. Optional
        callbacks may be provided to handle stdout and stderr.
        """
        scmd = ShellCommand(
            self, cmd, callback, std_err_callback, env, log_stderr, cwd
        )
        attempts = max(1, attempts)
        async def _wrapper() -> None:
            for _ in range(attempts):
                if await scmd.run(
                    timeout, verbose, log_complete, sig_idx,
                    proc_input, success_codes
                ):
                    break
            else:
                ret_code = scmd.get_return_code()
                raise ShellCommandError(f"Error running command {cmd}", ret_code)
        return asyncio.create_task(_wrapper())

    def exec_cmd(
        self,
        cmd: str,
        timeout: float = 2.,
        attempts: int = 1,
        sig_idx: int = 1,
        proc_input: Optional[str] = None,
        log_complete: bool = True,
        log_stderr: bool = False,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
        success_codes: Optional[List[int]] = None
    ) -> Awaitable[str]:
        """
        Executes a command and returns UTF-8 decoded stdout upon completion.
        """
        scmd = ShellCommand(self, cmd, None, None, env,
                            log_stderr, cwd)
        coro = scmd.run_with_response(
            timeout, attempts, log_complete, sig_idx,
            proc_input, success_codes
        )
        return asyncio.create_task(coro)

    async def close(self) -> None:
        for cmd in self.running_commands:
            await cmd.cancel()

def load_component(config: ConfigHelper) -> ShellCommandFactory:
    return ShellCommandFactory(config)
