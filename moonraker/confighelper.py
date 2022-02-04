# Configuration Helper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import configparser
import os
import hashlib
import shutil
import filecmp
import pathlib
import logging
from utils import SentinelClass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    IO,
    Optional,
    Tuple,
    TypeVar,
    Union,
    Dict,
    List,
    Type,
)
if TYPE_CHECKING:
    from moonraker import Server
    from components.gpio import GpioFactory, GpioOutputPin
    from components.template import TemplateFactory, JinjaTemplate
    _T = TypeVar("_T")
    ConfigVal = Union[None, int, float, bool, str, dict, list]

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

    def get_hash(self) -> hashlib._Hash:
        hash = hashlib.sha256()
        for option in self.config[self.section]:
            hash.update(option.encode())
        return hash

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
                    maxval: Optional[Union[int, float]] = None,
                    deprecate: bool = False
                    ) -> _T:
        try:
            val = func(self.section, option)
        except configparser.NoOptionError:
            if isinstance(default, SentinelClass):
                raise ConfigError(
                    f"No option found ({option}) in section [{self.section}]"
                ) from None
            val = default
        except Exception:
            raise ConfigError(
                f"Error parsing option ({option}) from "
                f"section [{self.section}]")
        else:
            if deprecate:
                self.server.add_warning(
                    f"[{self.section}]: Option '{option}' in is "
                    "deprecated, see the configuration documention "
                    "at https://moonraker.readthedocs.io")
            self._check_option(option, val, above, below, minval, maxval)
        if self.section in self.orig_sections:
            # Only track sections included in the original config
            if (
                val is None or
                isinstance(val, (int, float, bool, str, dict, list))
            ):
                self.parsed[self.section][option] = val
            else:
                # If the item cannot be encoded to json serialize to a string
                self.parsed[self.section][option] = str(val)
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
            default: Union[SentinelClass, _T] = SENTINEL,
            deprecate: bool = False
            ) -> Union[str, _T]:
        return self._get_option(
            self.config.get, option, default,
            deprecate=deprecate)

    def getint(self,
               option: str,
               default: Union[SentinelClass, _T] = SENTINEL,
               above: Optional[int] = None,
               below: Optional[int] = None,
               minval: Optional[int] = None,
               maxval: Optional[int] = None,
               deprecate: bool = False
               ) -> Union[int, _T]:
        return self._get_option(
            self.config.getint, option, default,
            above, below, minval, maxval, deprecate)

    def getboolean(self,
                   option: str,
                   default: Union[SentinelClass, _T] = SENTINEL,
                   deprecate: bool = False
                   ) -> Union[bool, _T]:
        return self._get_option(
            self.config.getboolean, option, default,
            deprecate=deprecate)

    def getfloat(self,
                 option: str,
                 default: Union[SentinelClass, _T] = SENTINEL,
                 above: Optional[float] = None,
                 below: Optional[float] = None,
                 minval: Optional[float] = None,
                 maxval: Optional[float] = None,
                 deprecate: bool = False
                 ) -> Union[float, _T]:
        return self._get_option(
            self.config.getfloat, option, default,
            above, below, minval, maxval, deprecate)

    def getlists(self,
                 option: str,
                 default: Union[SentinelClass, _T] = SENTINEL,
                 list_type: Type = str,
                 separators: Tuple[str, ...] = ('\n',),
                 count: Optional[Tuple[Optional[int], ...]] = None,
                 deprecate: bool = False
                 ) -> Union[List[Any], _T]:
        if count is not None and len(count) != len(separators):
            raise ConfigError(
                f"Option '{option}' in section "
                f"[{self.section}]: length of 'count' argument must ",
                "match length of 'separators' argument")
        else:
            count = tuple(None for _ in range(len(separators)))

        def list_parser(value: str,
                        ltype: Type,
                        seps: Tuple[str, ...],
                        expected_cnt: Tuple[Optional[int], ...]
                        ) -> List[Any]:
            sep = seps[0]
            seps = seps[1:]
            cnt = expected_cnt[0]
            expected_cnt = expected_cnt[1:]
            ret: List[Any] = []
            if seps:
                sub_lists = [val.strip() for val in value.split(sep)
                             if val.strip()]
                for sub_list in sub_lists:
                    ret.append(list_parser(sub_list, ltype, seps,
                                           expected_cnt))
            else:
                ret = [ltype(val.strip()) for val in value.split(sep)
                       if val.strip()]
            if cnt is not None and len(ret) != cnt:
                raise ConfigError(
                    f"List length mismatch, expected {cnt}, "
                    f"parsed {len(ret)}")
            return ret

        def getlist_wrapper(sec: str, opt: str) -> List[Any]:
            val = self.config.get(sec, opt)
            assert count is not None
            return list_parser(val, list_type, separators, count)

        return self._get_option(getlist_wrapper, option, default,
                                deprecate=deprecate)


    def getlist(self,
                option: str,
                default: Union[SentinelClass, _T] = SENTINEL,
                separator: str = '\n',
                count: Optional[int] = None,
                deprecate: bool = False
                ) -> Union[List[str], _T]:
        return self.getlists(option, default, str, (separator,), (count,),
                             deprecate=deprecate)

    def getintlist(self,
                   option: str,
                   default: Union[SentinelClass, _T] = SENTINEL,
                   separator: str = '\n',
                   count: Optional[int] = None,
                   deprecate: bool = False
                   ) -> Union[List[int], _T]:
        return self.getlists(option, default, int, (separator,), (count,),
                             deprecate=deprecate)

    def getfloatlist(self,
                     option: str,
                     default: Union[SentinelClass, _T] = SENTINEL,
                     separator: str = '\n',
                     count: Optional[int] = None,
                     deprecate: bool = False
                     ) -> Union[List[float], _T]:
        return self.getlists(option, default, float, (separator,), (count,),
                             deprecate=deprecate)

    def getdict(self,
                option: str,
                default: Union[SentinelClass, _T] = SENTINEL,
                separators: Tuple[str, str] = ('\n', '='),
                dict_type: Type = str,
                allow_empty_fields: bool = False,
                deprecate: bool = False
                ) -> Union[Dict[str, Any], _T]:
        if len(separators) != 2:
            raise ConfigError(
                "The `separators` argument of getdict() must be a Tuple"
                "of length of 2")

        def getdict_wrapper(sec: str, opt: str) -> Dict[str, Any]:
            val = self.config.get(sec, opt)
            ret: Dict[str, Any] = {}
            for line in val.split(separators[0]):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(separators[1], 1)
                if len(parts) == 1:
                    if allow_empty_fields:
                        ret[parts[0].strip()] = None
                    else:
                        raise ConfigError(
                            f"Failed to parse dictionary field, {line}")
                else:
                    ret[parts[0].strip()] = dict_type(parts[1].strip())
            return ret

        return self._get_option(getdict_wrapper, option, default,
                                deprecate=deprecate)

    def getgpioout(self,
                   option: str,
                   default: Union[SentinelClass, _T] = SENTINEL,
                   initial_value: int = 0,
                   deprecate: bool = False
                   ) -> Union[GpioOutputPin, _T]:
        try:
            gpio: GpioFactory = self.server.load_component(self, 'gpio')
        except Exception:
            raise ConfigError(
                f"Section [{self.section}], option '{option}', "
                "GPIO Component not available")

        def getgpio_wrapper(sec: str, opt: str) -> GpioOutputPin:
            val = self.config.get(sec, opt)
            return gpio.setup_gpio_out(val, initial_value)
        return self._get_option(getgpio_wrapper, option, default,
                                deprecate=deprecate)

    def gettemplate(self,
                    option: str,
                    default: Union[SentinelClass, _T] = SENTINEL,
                    is_async: bool = False,
                    deprecate: bool = False
                    ) -> Union[JinjaTemplate, _T]:
        try:
            template: TemplateFactory
            template = self.server.load_component(self, 'template')
        except Exception:
            raise ConfigError(
                f"Section [{self.section}], option '{option}', "
                "Template Component not available")

        def gettemplate_wrapper(sec: str, opt: str) -> JinjaTemplate:
            val = self.config.get(sec, opt)
            return template.create_template(val.strip(), is_async)

        return self._get_option(gettemplate_wrapper, option, default,
                                deprecate=deprecate)

    def load_template(self,
                      option: str,
                      default: Union[SentinelClass, str] = SENTINEL,
                      is_async: bool = False,
                      deprecate: bool = False
                      ) -> JinjaTemplate:
        val = self.gettemplate(option, default, is_async, deprecate)
        if isinstance(val, str):
            template: TemplateFactory
            template = self.server.lookup_component('template')
            return template.create_template(val.strip(), is_async)
        return val

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
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(cfg_file_path)
    except Exception as e:
        if not os.path.isfile(cfg_file_path):
            raise ConfigError(
                f"Configuration File Not Found: '{cfg_file_path}''") from e
        if not os.access(cfg_file_path, os.R_OK | os.W_OK):
            raise ConfigError(
                "Moonraker does not have Read/Write permission for "
                f"config file at path '{cfg_file_path}'") from e
        raise ConfigError(f"Error Reading Config: '{cfg_file_path}'") from e
    if not config.has_section('server'):
        raise ConfigError("No section [server] in config")
    orig_sections = config.sections()
    return ConfigHelper(server, config, 'server', orig_sections)

def backup_config(cfg_path: str) -> None:
    cfg = pathlib.Path(cfg_path).expanduser().resolve()
    backup = cfg.parent.joinpath(f".{cfg.name}.bkp")
    try:
        if backup.exists() and filecmp.cmp(cfg, backup):
            # Backup already exists and is current
            return
        shutil.copy2(cfg, backup)
        logging.info(f"Backing up last working configuration to '{backup}'")
    except Exception:
        logging.exception("Failed to create a backup")

def find_config_backup(cfg_path: str) -> Optional[str]:
    cfg = pathlib.Path(cfg_path).expanduser().resolve()
    backup = cfg.parent.joinpath(f".{cfg.name}.bkp")
    if backup.is_file():
        return str(backup)
    return None
