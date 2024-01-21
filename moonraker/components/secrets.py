# Support for password/token secrets
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import pathlib
import logging
import configparser
from ..utils import json_wrapper as jsonw
from typing import (
    TYPE_CHECKING,
    Dict,
    Optional,
    Any
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper

class Secrets:
    def __init__(self, config: ConfigHelper) -> None:
        server = config.get_server()
        path: Optional[str] = config.get("secrets_path", None, deprecate=True)
        app_args = server.get_app_args()
        data_path = app_args["data_path"]
        fpath = pathlib.Path(data_path).joinpath("moonraker.secrets")
        if not fpath.is_file() and path is not None:
            fpath = pathlib.Path(path).expanduser().resolve()
        self.type = "invalid"
        self.values: Dict[str, Any] = {}
        self.secrets_file = fpath
        if fpath.is_file():
            data = self.secrets_file.read_text()
            vals = self._parse_json(data)
            if vals is not None:
                if not isinstance(vals, dict):
                    server.add_warning(
                        f"[secrets]: option 'secrets_path', top level item in"
                        f" json file '{self.secrets_file}' must be an Object.")
                    return
                self.values = vals
                self.type = "json"
            else:
                vals = self._parse_ini(data)
                if vals is None:
                    server.add_warning(
                        "[secrets]: option 'secrets_path', invalid file "
                        f"format, must be json or ini: '{self.secrets_file}'")
                    return
                self.values = vals
                self.type = "ini"
            logging.debug(f"[secrets]: Loaded {self.type} file: "
                          f"{self.secrets_file}")
        elif path is not None:
            server.add_warning(
                "[secrets]: option 'secrets_path', file does not exist: "
                f"'{self.secrets_file}'")
        else:
            logging.debug(
                "[secrets]: Option `secrets_path` not supplied")

    def get_secrets_file(self) -> pathlib.Path:
        return self.secrets_file

    def _parse_ini(self, data: str) -> Optional[Dict[str, Any]]:
        try:
            cfg = configparser.ConfigParser(interpolation=None)
            cfg.read_string(data)
            return {sec: dict(cfg.items(sec)) for sec in cfg.sections()}
        except Exception:
            return None

    def _parse_json(self, data: str) -> Optional[Dict[str, Any]]:
        try:
            return jsonw.loads(data)
        except jsonw.JSONDecodeError:
            return None

    def get_type(self) -> str:
        return self.type

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


def load_component(config: ConfigHelper) -> Secrets:
    return Secrets(config)
