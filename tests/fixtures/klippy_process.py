from __future__ import annotations
import pytest
import os
import subprocess
import time
import pathlib
import shlex

from typing import Dict, Optional

class KlippyProcess:
    def __init__(self,
                 base_cmd: str,
                 path_args: Dict[str, pathlib.Path],
                 ) -> None:
        self.base_cmd = base_cmd
        self.config_path = path_args['printer.cfg']
        self.orig_config = self.config_path
        self.dict_path = path_args["klipper.dict"]
        self.pty_path = path_args["klippy_pty_path"]
        self.uds_path = path_args["klippy_uds_path"]
        self.proc: Optional[subprocess.Popen] = None
        self.fd: int = -1

    def start(self):
        if self.proc is not None:
            return
        args = (
            f"{self.config_path} -o /dev/null -d {self.dict_path} "
            f"-a {self.uds_path} -I {self.pty_path}"
        )
        cmd = f"{self.base_cmd} {args}"
        cmd_parts = shlex.split(cmd)
        self.proc = subprocess.Popen(cmd_parts)
        for _ in range(250):
            if self.pty_path.exists():
                try:
                    self.fd = os.open(
                        str(self.pty_path), os.O_RDWR | os.O_NONBLOCK)
                except Exception:
                    pass
                else:
                    break
            time.sleep(.01)
        else:
            self.stop()
            pytest.fail("Unable to start Klippy process")
            return False
        return True

    def send_gcode(self, gcode: str) -> None:
        if self.fd == -1:
            return
        try:
            os.write(self.fd, f"{gcode}\n".encode())
        except Exception:
            pass

    def restart(self):
        self.stop()
        self.start()

    def stop(self):
        if self.fd != -1:
            os.close(self.fd)
            self.fd = -1
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(2.)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def get_paths(self) -> Dict[str, pathlib.Path]:
        return {
            "printer.cfg": self.config_path,
            "klipper.dict": self.dict_path,
            "klippy_uds_path": self.uds_path,
            "klippy_pty_path": self.pty_path,
        }
