# Utilities for managing python packages using Pip
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import re
import shlex
import subprocess
import pathlib
import shutil
import threading
from dataclasses import dataclass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Union,
    Dict,
    List,
    Tuple,
    Callable,
    IO
)

if TYPE_CHECKING:
    from ..server import Server
    from ..components.shell_command import ShellCommandFactory

MIN_PIP_VERSION = (24, 0)
MIN_PYTHON_VERSION = (3, 7)

# Synchronous Subprocess Helpers
def _run_subprocess_with_response(
    cmd: str,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None
) -> str:
    prog = shlex.split(cmd)
    proc = subprocess.run(
        prog, capture_output=True, timeout=timeout, env=env,
        check=True, text=True, errors="ignore", encoding="utf-8"
    )
    if proc.returncode == 0:
        return proc.stdout.strip()
    err = proc.stderr
    raise Exception(f"Failed to run pip command '{cmd}': {err}")

def _process_subproc_output(
    stdout: IO[str],
    callback: Callable[[str], None]
) -> None:
    for line in stdout:
        callback(line.rstrip("\n"))

def _run_subprocess(
    cmd: str,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
    response_cb: Optional[Callable[[str], None]] = None
) -> None:
    prog = shlex.split(cmd)
    params: Dict[str, Any] = {"errors": "ignore", "encoding": "utf-8"}
    if response_cb is not None:
        params = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}
    with subprocess.Popen(prog, text=True, env=env, **params) as process:
        if process.stdout is not None and response_cb is not None:
            reader_thread = threading.Thread(
                target=_process_subproc_output, args=(process.stdout, response_cb)
            )
            reader_thread.start()
            reader_thread.join(timeout)
            if reader_thread.is_alive():
                process.kill()
        elif timeout is not None:
            process.wait(timeout)
    ret = process.poll()
    if ret != 0:
        raise Exception(f"Failed to run pip command '{cmd}'")

@ dataclass(frozen=True)
class PipVersionInfo:
    pip_version_string: str
    python_version_string: str

    @property
    def pip_version(self) -> Tuple[int, ...]:
        return tuple(int(part) for part in self.pip_version_string.split("."))

    @property
    def python_version(self) -> Tuple[int, ...]:
        return tuple(int(part) for part in self.python_version_string.split("."))

class PipExecutor:
    def __init__(
        self, pip_cmd: str, response_handler: Optional[Callable[[str], None]] = None
    ) -> None:
        self.pip_cmd = pip_cmd
        self.response_hdlr = response_handler

    def call_pip_with_response(
        self,
        args: str,
        timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None
    ) -> str:
        return _run_subprocess_with_response(f"{self.pip_cmd} {args}", timeout, env)

    def call_pip(
        self,
        args: str,
        timeout: Optional[float] = None,
        env: Optional[Dict[str, str]] = None
    ) -> None:
        _run_subprocess(f"{self.pip_cmd} {args}", timeout, env, self.response_hdlr)

    def get_pip_version(self) -> PipVersionInfo:
        resp = self.call_pip_with_response("--version", 10.)
        return parse_pip_version(resp)

    def update_pip(self) -> None:
        pip_ver = ".".join([str(part) for part in MIN_PIP_VERSION])
        self.call_pip(f"install pip=={pip_ver}", 120.)

    def install_packages(
        self,
        packages: Union[pathlib.Path, List[str]],
        sys_env_vars: Optional[Dict[str, Any]] = None
    ) -> None:
        args = prepare_install_args(packages)
        env: Optional[Dict[str, str]] = None
        if sys_env_vars is not None:
            env = dict(os.environ)
            env.update(sys_env_vars)
        self.call_pip(f"install {args}", timeout=1200., env=env)

    def build_virtualenv(self, py_exec: pathlib.Path, args: str) -> None:
        bin_dir = py_exec.parent
        env_path = bin_dir.parent.resolve()
        if env_path.exists():
            shutil.rmtree(env_path)
        _run_subprocess(
            f"virtualenv {args} {env_path}",
            timeout=600.,
            response_cb=self.response_hdlr
        )
        if not py_exec.exists():
            raise Exception("Failed to create new virtualenv", 500)

class AsyncPipExecutor:
    def __init__(
        self,
        pip_cmd: str,
        server: Server,
        notify_callback: Optional[Callable[[bytes], None]] = None
    ) -> None:
        self.pip_cmd = pip_cmd
        self.server = server
        self.notify_callback = notify_callback

    def get_shell_cmd(self) -> ShellCommandFactory:
        return self.server.lookup_component("shell_command")

    async def call_pip_with_response(
        self,
        args: str,
        timeout: float = 30.,
        attempts: int = 3,
        sys_env_vars: Optional[Dict[str, Any]] = None
    ) -> str:
        env: Optional[Dict[str, str]] = None
        if sys_env_vars is not None:
            env = dict(os.environ)
            env.update(sys_env_vars)
        shell_cmd = self.get_shell_cmd()
        return await shell_cmd.exec_cmd(
            f"{self.pip_cmd} {args}", attempts=attempts,
            timeout=timeout, env=env, log_stderr=True
        )

    async def call_pip(
        self,
        args: str,
        timeout: float = 30.,
        attempts: int = 3,
        sys_env_vars: Optional[Dict[str, Any]] = None
    ) -> None:
        env: Optional[Dict[str, str]] = None
        if sys_env_vars is not None:
            env = dict(os.environ)
            env.update(sys_env_vars)
        shell_cmd = self.get_shell_cmd()
        await shell_cmd.run_cmd_async(
            f"{self.pip_cmd} {args}", self.notify_callback,
            timeout=timeout, attempts=attempts, env=env,
            log_stderr=True
        )

    async def get_pip_version(self) -> PipVersionInfo:
        resp: str = await self.get_shell_cmd().exec_cmd(
            f"{self.pip_cmd} --version", timeout=30., attempts=3, log_stderr=True
        )
        return parse_pip_version(resp)

    async def update_pip(self) -> None:
        pip_ver = ".".join([str(part) for part in MIN_PIP_VERSION])
        shell_cmd = self.get_shell_cmd()
        await shell_cmd.run_cmd_async(
            f"{self.pip_cmd} install pip=={pip_ver}",
            self.notify_callback, timeout=1200., attempts=3, log_stderr=True
        )

    async def install_packages(
        self,
        packages: Union[pathlib.Path, List[str]],
        sys_env_vars: Optional[Dict[str, Any]] = None
    ) -> None:
        # Update python dependencies
        args = prepare_install_args(packages)
        env: Optional[Dict[str, str]] = None
        if sys_env_vars is not None:
            env = dict(os.environ)
            env.update(sys_env_vars)
        shell_cmd = self.get_shell_cmd()
        await shell_cmd.run_cmd_async(
            f"{self.pip_cmd} install {args}", self.notify_callback,
            timeout=1200., attempts=3, env=env, log_stderr=True
        )

    async def build_virtualenv(self, py_exec: pathlib.Path, args: str) -> None:
        bin_dir = py_exec.parent
        env_path = bin_dir.parent.resolve()
        if env_path.exists():
            shutil.rmtree(env_path)
        shell_cmd = self.get_shell_cmd()
        await shell_cmd.exec_cmd(f"virtualenv {args} {env_path}", timeout=600.)
        if not py_exec.exists():
            raise self.server.error("Failed to create new virtualenv", 500)

def read_requirements_file(requirements_path: pathlib.Path) -> List[str]:
    if not requirements_path.is_file():
        raise FileNotFoundError(f"Requirements file {requirements_path} not found")
    data = requirements_path.read_text()
    modules: List[str] = []
    for line in data.split("\n"):
        line = line.strip()
        if not line or line[0] in "#-":
            continue
        match = re.search(r"\s#", line)
        if match is not None:
            line = line[:match.start()].strip()
        modules.append(line)
    return modules

def parse_pip_version(pip_response: str) -> PipVersionInfo:
    match = re.match(
        r"^pip ([0-9.]+) from .+? \(python ([0-9.]+)\)$", pip_response.strip()
    )
    if match is None:
        raise ValueError("Unable to parse pip version from response")
    pipver_str: str = match.group(1).strip()
    pyver_str: str = match.group(2).strip()
    return PipVersionInfo(pipver_str, pyver_str)

def check_pip_needs_update(version_info: PipVersionInfo) -> bool:
    if version_info.python_version < MIN_PYTHON_VERSION:
        return False
    return version_info.pip_version < MIN_PIP_VERSION

def prepare_install_args(packages: Union[pathlib.Path, List[str]]) -> str:
    if isinstance(packages, pathlib.Path):
        if not packages.is_file():
            raise FileNotFoundError(
                f"Invalid path to requirements_file '{packages}'"
            )
        return f"-r {packages}"
    reqs = [req.replace("\"", "'") for req in packages]
    return " ".join([f"\"{req}\"" for req in reqs])
