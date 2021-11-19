# Configuration Helper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import configparser
import os
from utils import SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    IO,
    Optional,
    TypeVar,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from moonraker import Server
    _T = TypeVar("_T")
    ConfigVal = Union[None, int, float, bool, str]

SENTINEL = SentinelClass.get_instance()

class ConfigError(Exception):
    pass


class ConfigHelper:
    error = ConfigError
    def __init__(self,
                 server: Server,
                 config: configparser.ConfigParser,
                 section: str,
                 orig_sects: List[str],
                 parsed: Dict[str, Dict[str, ConfigVal]] = {}
                 ) -> None:
        self.server = server
        self.config = config
        self.section = section
        self.orig_sections = orig_sects
        self.parsed = parsed
        if self.section not in self.parsed:
            self.parsed[self.section] = {}
        self.sections = config.sections
        self.has_section = config.has_section

    def get_server(self) -> Server:
        return self.server

    def __getitem__(self, key: str) -> ConfigHelper:
        return self.getsection(key)

    def __contains__(self, key: str) -> bool:
        return key in self.config

    def has_option(self, option: str) -> bool:
        return self.config.has_option(self.section, option)

    def get_name(self) -> str:
        return self.section

    def get_options(self) -> Dict[str, str]:
        return dict(self.config[self.section])

    def get_prefix_sections(self, prefix: str) -> List[str]:
        return [s for s in self.sections() if s.startswith(prefix)]

    def getsection(self, section: str) -> ConfigHelper:
        if section not in self.config:
            raise ConfigError(f"No section [{section}] in config")
        return ConfigHelper(self.server, self.config, section,
                            self.orig_sections, self.parsed)

    def _get_option(self,
                    func: Callable[..., Any],
                    option: str,
                    default: Union[SentinelClass, _T],
                    above: Optional[Union[int, float]] = None,
                    below: Optional[Union[int, float]] = None,
                    minval: Optional[Union[int, float]] = None,
                    maxval: Optional[Union[int, float]] = None
                    ) -> _T:
        try:
            val = func(self.section, option)
        except configparser.NoOptionError:
            if isinstance(default, SentinelClass):
                raise ConfigError(
                    f"No option found ({option}) in section [{self.section}]"
                ) from None
            return default
        except Exception:
            raise ConfigError(
                f"Error parsing option ({option}) from "
                f"section [{self.section}]")
        self._check_option(option, val, above, below, minval, maxval)
        if self.section in self.orig_sections:
            # Only track sections included in the original config
            self.parsed[self.section][option] = val
        return val

    def _check_option(self,
                      option: str,
                      value: Union[int, float],
                      above: Optional[Union[int, float]],
                      below: Optional[Union[int, float]],
                      minval: Optional[Union[int, float]],
                      maxval: Optional[Union[int, float]]
                      ) -> None:
        if above is not None and value <= above:
            raise self.error(
                f"Config Error: Section [{self.section}], Option "
                f"'{option}: {value}': value is not above {above}")
        if below is not None and value >= below:
            raise self.error(
                f"Config Error: Section [{self.section}], Option "
                f"'{option}: {value}': value is not below {below}")
        if minval is not None and value < minval:
            raise self.error(
                f"Config Error: Section [{self.section}], Option "
                f"'{option}: {value}': value is below minimum value {minval}")
        if maxval is not None and value > maxval:
            raise self.error(
                f"Config Error: Section [{self.section}], Option "
                f"'{option}: {value}': value is above maximum value {minval}")

    def get(self,
            option: str,
            default: Union[SentinelClass, _T] = SENTINEL
            ) -> Union[str, _T]:
        return self._get_option(
            self.config.get, option, default)

    def getint(self,
               option: str,
               default: Union[SentinelClass, _T] = SENTINEL,
               above: Optional[int] = None,
               below: Optional[int] = None,
               minval: Optional[int] = None,
               maxval: Optional[int] = None
               ) -> Union[int, _T]:
        return self._get_option(
            self.config.getint, option, default,
            above, below, minval, maxval)

    def getboolean(self,
                   option: str,
                   default: Union[SentinelClass, _T] = SENTINEL
                   ) -> Union[bool, _T]:
        return self._get_option(
            self.config.getboolean, option, default)

    def getfloat(self,
                 option: str,
                 default: Union[SentinelClass, _T] = SENTINEL,
                 above: Optional[float] = None,
                 below: Optional[float] = None,
                 minval: Optional[float] = None,
                 maxval: Optional[float] = None
                 ) -> Union[float, _T]:
        return self._get_option(
            self.config.getfloat, option, default,
            above, below, minval, maxval)

    def read_supplemental_config(self, file_name: str) -> ConfigHelper:
        cfg_file_path = os.path.normpath(os.path.expanduser(file_name))
        if not os.path.isfile(cfg_file_path):
            raise ConfigError(
                f"Configuration File Not Found: '{cfg_file_path}''")
        try:
            sup_cfg = configparser.ConfigParser(interpolation=None)
            sup_cfg.read(cfg_file_path)
        except Exception:
            raise ConfigError(f"Error Reading Config: '{cfg_file_path}'")
        sections = sup_cfg.sections()
        return ConfigHelper(self.server, sup_cfg, sections[0], sections)

    def write_config(self, file_obj: IO[str]) -> None:
        self.config.write(file_obj)

    def get_parsed_config(self) -> Dict[str, Dict[str, ConfigVal]]:
        return dict(self.parsed)

    def validate_config(self) -> None:
        for sect in self.orig_sections:
            if sect not in self.parsed:
                self.server.add_warning(
                    f"Unparsed config section [{sect}] detected.  This "
                    "may be the result of a component that failed to "
                    "load.  In the future this will result in a startup "
                    "error.")
                continue
            parsed_opts = self.parsed[sect]
            for opt, val in self.config.items(sect):
                if opt not in parsed_opts:
                    self.server.add_warning(
                        f"Unparsed config option '{opt}: {val}' detected in "
                        f"section [{sect}].  This may be an option no longer "
                        "available or could be the result of a module that "
                        "failed to load.  In the future this will result "
                        "in a startup error.")

def get_configuration(server: Server,
                      app_args: Dict[str, Any]
                      ) -> ConfigHelper:
    cfg_file_path: str = os.path.normpath(os.path.expanduser(
        app_args['config_file']))
    if not os.path.isfile(cfg_file_path):
        raise ConfigError(
            f"Configuration File Not Found: '{cfg_file_path}''")
    if not os.access(cfg_file_path, os.R_OK | os.W_OK):
        raise ConfigError(
            "Moonraker does not have Read/Write permission for "
            f"config file at path '{cfg_file_path}'")
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(cfg_file_path)
    except Exception as e:
        raise ConfigError(f"Error Reading Config: '{cfg_file_path}'") from e
    try:
        server_cfg = config['server']
    except KeyError:
        raise ConfigError("No section [server] in config")

    orig_sections = config.sections()
    try:
        orig_sections.remove("DEFAULT")
    except Exception:
        pass

    return ConfigHelper(server, config, 'server', orig_sections)
