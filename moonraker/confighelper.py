# Configuration Helper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import configparser
import os
import hashlib
import pathlib
import re
import threading
import copy
import logging
from io import StringIO
from .utils import Sentinel
from .components.template import JinjaTemplate

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    IO,
    Optional,
    Set,
    Tuple,
    TypeVar,
    Union,
    Dict,
    List,
    Type,
    TextIO
)
if TYPE_CHECKING:
    from .server import Server
    from .components.gpio import GpioFactory, GpioOutputPin
    from .components.template import TemplateFactory
    _T = TypeVar("_T")
    ConfigVal = Union[None, int, float, bool, str, dict, list]

DOCS_URL = "https://moonraker.readthedocs.io/en/latest"

class ConfigError(Exception):
    pass


class ConfigHelper:
    error = ConfigError
    def __init__(self,
                 server: Server,
                 config_source: ConfigSourceWrapper,
                 section: str,
                 parsed: Dict[str, Dict[str, ConfigVal]],
                 fallback_section: Optional[str] = None
                 ) -> None:
        self.server = server
        self.source = config_source
        self.config = config_source.get_parser()
        self.section = section
        self.fallback_section: Optional[str] = fallback_section
        self.parsed = parsed
        if self.section not in self.parsed:
            self.parsed[self.section] = {}
        self.sections = self.config.sections
        self.has_section = self.config.has_section

    def get_server(self) -> Server:
        return self.server

    def get_source(self) -> ConfigSourceWrapper:
        return self.source

    def __getitem__(self, key: str) -> ConfigHelper:
        return self.getsection(key)

    def __contains__(self, key: str) -> bool:
        return key in self.config

    def has_option(self, option: str) -> bool:
        return self.config.has_option(self.section, option)

    def set_option(self, option: str, value: str) -> None:
        self.source.set_option(self.section, option, value)

    def get_name(self) -> str:
        return self.section

    def get_file(self) -> Optional[pathlib.Path]:
        return self.source.find_config_file(self.section)

    def get_options(self) -> Dict[str, str]:
        if self.section not in self.config:
            return {}
        return dict(self.config[self.section])

    def get_hash(self) -> hashlib._Hash:
        hash = hashlib.sha256()
        section = self.section
        if self.section not in self.config:
            return hash
        for option, val in self.config[section].items():
            hash.update(option.encode())
            hash.update(val.encode())
        return hash

    def get_prefix_sections(self, prefix: str) -> List[str]:
        return [s for s in self.sections() if s.startswith(prefix)]

    def getsection(
        self, section: str, fallback: Optional[str] = None
    ) -> ConfigHelper:
        return ConfigHelper(
            self.server, self.source, section, self.parsed, fallback
        )

    def _get_option(self,
                    func: Callable[..., Any],
                    option: str,
                    default: Union[Sentinel, _T],
                    above: Optional[Union[int, float]] = None,
                    below: Optional[Union[int, float]] = None,
                    minval: Optional[Union[int, float]] = None,
                    maxval: Optional[Union[int, float]] = None,
                    deprecate: bool = False
                    ) -> _T:
        section = self.section
        warn_fallback = False
        if (
            self.section not in self.config and
            self.fallback_section is not None
        ):
            section = self.fallback_section
            warn_fallback = True
        try:
            val = func(section, option)
        except (configparser.NoOptionError, configparser.NoSectionError) as e:
            if default is Sentinel.MISSING:
                raise ConfigError(str(e)) from None
            val = default
            section = self.section
        except Exception:
            raise ConfigError(
                f"Error parsing option ({option}) from "
                f"section [{self.section}]")
        else:
            if deprecate:
                self.server.add_warning(
                    f"[{self.section}]: Option '{option}' is "
                    "deprecated, see the configuration documention "
                    f"at {DOCS_URL}/configuration/")
            if warn_fallback:
                help = f"{DOCS_URL}/configuration/#option-moved-deprecations"
                self.server.add_warning(
                    f"[{section}]: Option '{option}' has been moved "
                    f"to section [{self.section}].  Please correct your "
                    f"configuration, see {help} for detailed documentation."
                )
            self._check_option(option, val, above, below, minval, maxval)
        if option not in self.parsed[section]:
            if (
                val is None or
                isinstance(val, (int, float, bool, str, dict, list))
            ):
                self.parsed[section][option] = val
            else:
                # If the item cannot be encoded to json serialize to a string
                self.parsed[section][option] = str(val)
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
            default: Union[Sentinel, _T] = Sentinel.MISSING,
            deprecate: bool = False
            ) -> Union[str, _T]:
        return self._get_option(
            self.config.get, option, default,
            deprecate=deprecate)

    def getint(self,
               option: str,
               default: Union[Sentinel, _T] = Sentinel.MISSING,
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
                   default: Union[Sentinel, _T] = Sentinel.MISSING,
                   deprecate: bool = False
                   ) -> Union[bool, _T]:
        return self._get_option(
            self.config.getboolean, option, default,
            deprecate=deprecate)

    def getfloat(self,
                 option: str,
                 default: Union[Sentinel, _T] = Sentinel.MISSING,
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
                 default: Union[Sentinel, _T] = Sentinel.MISSING,
                 list_type: Type = str,
                 separators: Tuple[Optional[str], ...] = ('\n',),
                 count: Optional[Tuple[Optional[int], ...]] = None,
                 deprecate: bool = False
                 ) -> Union[List[Any], _T]:
        if count is not None and len(count) != len(separators):
            raise ConfigError(
                f"Option '{option}' in section "
                f"[{self.section}]: length of 'count' argument must ",
                "match length of 'separators' argument")
        elif count is None:
            count = tuple(None for _ in range(len(separators)))

        def list_parser(value: str,
                        ltype: Type,
                        seps: Tuple[Optional[str], ...],
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
                default: Union[Sentinel, _T] = Sentinel.MISSING,
                separator: Optional[str] = '\n',
                count: Optional[int] = None,
                deprecate: bool = False
                ) -> Union[List[str], _T]:
        return self.getlists(option, default, str, (separator,), (count,),
                             deprecate=deprecate)

    def getintlist(self,
                   option: str,
                   default: Union[Sentinel, _T] = Sentinel.MISSING,
                   separator: Optional[str] = '\n',
                   count: Optional[int] = None,
                   deprecate: bool = False
                   ) -> Union[List[int], _T]:
        return self.getlists(option, default, int, (separator,), (count,),
                             deprecate=deprecate)

    def getfloatlist(self,
                     option: str,
                     default: Union[Sentinel, _T] = Sentinel.MISSING,
                     separator: Optional[str] = '\n',
                     count: Optional[int] = None,
                     deprecate: bool = False
                     ) -> Union[List[float], _T]:
        return self.getlists(option, default, float, (separator,), (count,),
                             deprecate=deprecate)

    def getdict(self,
                option: str,
                default: Union[Sentinel, _T] = Sentinel.MISSING,
                separators: Tuple[Optional[str], Optional[str]] = ('\n', '='),
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
                   default: Union[Sentinel, _T] = Sentinel.MISSING,
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
                    default: Union[Sentinel, _T] = Sentinel.MISSING,
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
                      default: Union[Sentinel, str] = Sentinel.MISSING,
                      is_async: bool = False,
                      deprecate: bool = False
                      ) -> JinjaTemplate:
        val = self.gettemplate(option, default, is_async, deprecate)
        if isinstance(val, str):
            template: TemplateFactory
            template = self.server.lookup_component('template')
            return template.create_template(val.strip(), is_async)
        return val

    def getpath(self,
                option: str,
                default: Union[Sentinel, _T] = Sentinel.MISSING,
                deprecate: bool = False
                ) -> Union[pathlib.Path, _T]:
        val = self.gettemplate(option, default, deprecate=deprecate)
        if isinstance(val, JinjaTemplate):
            ctx = {"data_path": self.server.get_app_args()["data_path"]}
            strpath = val.render(ctx)
            return pathlib.Path(strpath).expanduser().resolve()
        return val

    def read_supplemental_dict(self, obj: Dict[str, Any]) -> ConfigHelper:
        if not obj:
            raise ConfigError(f"Cannot ready Empty Dict")
        source = DictSourceWrapper()
        source.read_dict(obj)
        sections = source.config.sections()
        return ConfigHelper(self.server, source, sections[0], {})

    def read_supplemental_config(self, file_name: str) -> ConfigHelper:
        fpath = pathlib.Path(file_name).expanduser().resolve()
        source = FileSourceWrapper(self.server)
        source.read_file(fpath)
        sections = source.config.sections()
        return ConfigHelper(self.server, source, sections[0], {})

    def write_config(self, file_obj: IO[str]) -> None:
        self.config.write(file_obj)

    def get_parsed_config(self) -> Dict[str, Dict[str, ConfigVal]]:
        return dict(self.parsed)

    def get_orig_config(self) -> Dict[str, Dict[str, str]]:
        return self.source.as_dict()

    def get_file_sections(self) -> Dict[str, List[str]]:
        return self.source.get_file_sections()

    def get_config_files(self) -> List[str]:
        return [str(f) for f in self.source.get_files()]

    def validate_config(self) -> None:
        for sect in self.config.sections():
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

    def create_backup(self) -> None:
        cfg_path = self.server.get_app_args()["config_file"]
        cfg = pathlib.Path(cfg_path).expanduser().resolve()
        backup = cfg.parent.joinpath(f".{cfg.name}.bkp")
        backup_fp: Optional[TextIO] = None
        try:
            if backup.exists():
                cfg_mtime: int = 0
                for cfg in self.source.get_files():
                    cfg_mtime = max(cfg_mtime, cfg.stat().st_mtime_ns)
                backup_mtime = backup.stat().st_mtime_ns
                if backup_mtime >= cfg_mtime:
                    # Backup already exists and is current
                    return
            backup_fp = backup.open("w")
            self.config.write(backup_fp)
            logging.info(f"Backing up last working configuration to '{backup}'")
        except Exception:
            logging.exception("Failed to create a backup")
        finally:
            if backup_fp is not None:
                backup_fp.close()

class ConfigSourceWrapper:
    def __init__(self):
        self.config = configparser.ConfigParser(interpolation=None)

    def get_parser(self):
        return self.config

    def as_dict(self) -> Dict[str, Dict[str, str]]:
        return {key: dict(val) for key, val in self.config.items()}

    def write_to_string(self) -> str:
        sio = StringIO()
        self.config.write(sio)
        val = sio.getvalue()
        sio.close()
        return val

    def get_files(self) -> List[pathlib.Path]:
        return []

    def set_option(self, section: str, option: str, value: str) -> None:
        self.config.set(section, option, value)

    def remove_option(self, section: str, option: str) -> None:
        self.config.remove_option(section, option)

    def add_section(self, section: str) -> None:
        self.config.add_section(section)

    def remove_section(self, section: str) -> None:
        self.config.remove_section(section)

    def get_file_sections(self) -> Dict[str, List[str]]:
        return {}

    def find_config_file(
        self, section: str, option: Optional[str] = None
    ) -> Optional[pathlib.Path]:
        return None

class DictSourceWrapper(ConfigSourceWrapper):
    def __init__(self):
        super().__init__()

    def read_dict(self, cfg: Dict[str, Any]) -> None:
        try:
            self.config.read_dict(cfg)
        except Exception as e:
            raise ConfigError("Error Reading config as dict") from e

class FileSourceWrapper(ConfigSourceWrapper):
    section_r = re.compile(r"\s*\[([^]]+)\]")

    def __init__(self, server: Server) -> None:
        super().__init__()
        self.server = server
        self.files: List[pathlib.Path] = []
        self.raw_config_data: List[str] = []
        self.updates_pending: Set[int] = set()
        self.file_section_map: Dict[str, List[int]] = {}
        self.file_option_map: Dict[Tuple[str, str], List[int]] = {}
        self.save_lock = threading.Lock()
        self.backup: Dict[str, Any] = {}

    def get_files(self) -> List[pathlib.Path]:
        return self.files

    def is_in_transaction(self) -> bool:
        return (
            len(self.updates_pending) > 0 or
            self.save_lock.locked()
        )

    def backup_source(self) -> None:
        self.backup = {
            "raw_data": list(self.raw_config_data),
            "section_map": copy.deepcopy(self.file_section_map),
            "option_map": copy.deepcopy(self.file_option_map),
            "config": self.write_to_string()
        }

    def _acquire_save_lock(self) -> None:
        if not self.files:
            raise ConfigError(
                "Can only modify file backed configurations"
            )
        if not self.save_lock.acquire(blocking=False):
            raise ConfigError("Configuration locked, cannot modify")

    def set_option(self, section: str, option: str, value: str) -> None:
        self._acquire_save_lock()
        try:
            value = value.strip()
            try:
                if (self.config.get(section, option).strip() == value):
                    return
            except (configparser.NoSectionError, configparser.NoOptionError):
                pass
            file_idx: int = 0
            has_sec = has_opt = False
            if (section, option) in self.file_option_map:
                file_idx = self.file_option_map[(section, option)][0]
                has_sec = has_opt = True
            elif section in self.file_section_map:
                file_idx = self.file_section_map[section][0]
                has_sec = True
            buf = self.raw_config_data[file_idx].splitlines()
            new_opt_list = [f"{option}: {value}"]
            if "\n" in value:
                vals = [f"  {v}" for v in value.split("\n")]
                new_opt_list = [f"{option}:"] + vals
            sec_info = self._find_section_info(section, buf, raise_error=False)
            if sec_info:
                options: Dict[str, Any] = sec_info["options"]
                indent: int = sec_info["indent"]
                opt_start: int = sec_info["end"]
                opt_end: int = sec_info["end"]
                opt_info: Optional[Dict[str, Any]] = options.get(option)
                if opt_info is not None:
                    indent = opt_info["indent"]
                    opt_start = opt_info["start"]
                    opt_end = opt_info["end"]
                elif options:
                    # match indentation of last option in section
                    last_opt = list(options.values())[-1]
                    indent = last_opt["indent"]
                if indent:
                    padding = " " * indent
                    new_opt_list = [f"{padding}{v}" for v in new_opt_list]
                buf[opt_start:] = new_opt_list + buf[opt_end:]
            else:
                # Append new section to the end of the file
                new_opt_list.insert(0, f"[{section}]")
                if buf and buf[-1].strip() != "":
                    new_opt_list.insert(0, "")
                buf.extend(new_opt_list)
            buf.append("")
            updated_cfg = "\n".join(buf)
            # test changes to the configuration
            test_parser = configparser.ConfigParser(interpolation=None)
            try:
                test_parser.read_string(updated_cfg)
                if not test_parser.has_option(section, option):
                    raise ConfigError("Option not added")
            except Exception as e:
                raise ConfigError(
                    f"Failed to set option '{option}' in section "
                    f"[{section}], file: {self.files[file_idx]}"
                ) from e
            # Update local configuration/tracking
            self.raw_config_data[file_idx] = updated_cfg
            self.updates_pending.add(file_idx)
            if not has_sec:
                self.file_section_map[section] = [file_idx]
            if not has_opt:
                self.file_option_map[(section, option)] = [file_idx]
            if not self.config.has_section(section):
                self.config.add_section(section)
            self.config.set(section, option, value)
        finally:
            self.save_lock.release()

    def remove_option(self, section: str, option: str) -> None:
        self._acquire_save_lock()
        try:
            key = (section, option)
            if key not in self.file_option_map:
                return
            pending: List[Tuple[int, str]] = []
            file_indices = self.file_option_map[key]
            for idx in file_indices:
                buf = self.raw_config_data[idx].splitlines()
                try:
                    sec_info = self._find_section_info(section, buf)
                    opt_info = sec_info["options"][option]
                    start = opt_info["start"]
                    end = opt_info["end"]
                    if (
                        end < len(buf) and
                        not buf[start-1].strip()
                        and not buf[end].strip()
                    ):
                        end += 1
                    buf[start:] = buf[end:]
                    buf.append("")
                    updated_cfg = "\n".join(buf)
                    test_parser = configparser.ConfigParser(interpolation=None)
                    test_parser.read_string(updated_cfg)
                    if test_parser.has_option(section, option):
                        raise ConfigError("Option still exists")
                    pending.append((idx, updated_cfg))
                except Exception as e:
                    raise ConfigError(
                        f"Failed to remove option '{option}' from section "
                        f"[{section}], file: {self.files[idx]}"
                    ) from e
            # Update configuration/tracking
            for (idx, data) in pending:
                self.updates_pending.add(idx)
                self.raw_config_data[idx] = data
            del self.file_option_map[key]
            self.config.remove_option(section, option)
        finally:
            self.save_lock.release()

    def add_section(self, section: str) -> None:
        self._acquire_save_lock()
        try:
            if section in self.file_section_map:
                return
            # add section to end of primary file
            buf = self.raw_config_data[0].splitlines()
            if buf and buf[-1].strip() != "":
                buf.append("")
            buf.extend([f"[{section}]", ""])
            updated_cfg = "\n".join(buf)
            try:
                test_parser = configparser.ConfigParser(interpolation=None)
                test_parser.read_string(updated_cfg)
                if not test_parser.has_section(section):
                    raise ConfigError("Section not added")
            except Exception as e:
                raise ConfigError(
                    f"Failed to add section [{section}], file: {self.files[0]}"
                ) from e
            self.updates_pending.add(0)
            self.file_section_map[section] = [0]
            self.raw_config_data[0] = updated_cfg
            self.config.add_section(section)
        finally:
            self.save_lock.release()

    def remove_section(self, section: str) -> None:
        self._acquire_save_lock()
        try:
            if section not in self.file_section_map:
                return
            pending: List[Tuple[int, str]] = []
            file_indices = self.file_section_map[section]
            for idx in file_indices:
                buf = self.raw_config_data[idx].splitlines()
                try:
                    sec_info = self._find_section_info(section, buf)
                    start = sec_info["start"]
                    end = sec_info["end"]
                    if (
                        end < len(buf) and
                        not buf[start-1].strip()
                        and not buf[end].strip()
                    ):
                        end += 1
                    buf[start:] = buf[end:]
                    buf.append("")
                    updated_cfg = "\n".join(buf)
                    test_parser = configparser.ConfigParser(interpolation=None)
                    test_parser.read_string(updated_cfg)
                    if test_parser.has_section(section):
                        raise ConfigError("Section still exists")
                    pending.append((idx, updated_cfg))
                except Exception as e:
                    raise ConfigError(
                        f"Failed to remove section [{section}], "
                        f"file: {self.files[0]}"
                    ) from e
            for (idx, data) in pending:
                self.updates_pending.add(idx)
                self.raw_config_data[idx] = data
            del self.file_section_map[section]
            self.config.remove_section(section)
        finally:
            self.save_lock.release()

    def save(self) -> Awaitable[bool]:
        eventloop = self.server.get_event_loop()
        if self.server.is_running():
            fut = eventloop.run_in_thread(self._do_save)
        else:
            fut = eventloop.create_future()
            fut.set_result(self._do_save())
        return fut

    def _do_save(self) -> bool:
        with self.save_lock:
            self.backup.clear()
            if not self.updates_pending:
                return False
            for idx in self.updates_pending:
                fpath = self.files[idx]
                fpath.write_text(
                    self.raw_config_data[idx], encoding="utf-8"
                )
            self.updates_pending.clear()
            return True

    def cancel(self):
        self._acquire_save_lock()
        try:
            if not self.backup or not self.updates_pending:
                self.backup.clear()
                return
            self.raw_config_data = self.backup["raw_data"]
            self.file_option_map = self.backup["option_map"]
            self.file_section_map = self.backup["section_map"]
            self.config.clear()
            self.config.read_string(self.backup["config"])
            self.updates_pending.clear()
            self.backup.clear()
        finally:
            self.save_lock.release()

    def revert(self) -> Awaitable[bool]:
        eventloop = self.server.get_event_loop()
        if self.server.is_running():
            fut = eventloop.run_in_thread(self._do_revert)
        else:
            fut = eventloop.create_future()
            fut.set_result(self._do_revert())
        return fut

    def _do_revert(self) -> bool:
        with self.save_lock:
            if not self.updates_pending:
                return False
            self.backup.clear()
            entry = self.files[0]
            self.read_file(entry)
            return True

    def write_config(
        self, dest_folder: Union[str, pathlib.Path]
    ) -> Awaitable[None]:
        eventloop = self.server.get_event_loop()
        if self.server.is_running():
            fut = eventloop.run_in_thread(self._do_write, dest_folder)
        else:
            self._do_write(dest_folder)
            fut = eventloop.create_future()
            fut.set_result(None)
        return fut

    def _do_write(self, dest_folder: Union[str, pathlib.Path]) -> None:
        with self.save_lock:
            if isinstance(dest_folder, str):
                dest_folder = pathlib.Path(dest_folder)
            dest_folder = dest_folder.expanduser().resolve()
            cfg_parent = self.files[0].parent
            for i, path in enumerate(self.files):
                try:
                    rel_path = path.relative_to(cfg_parent)
                    dest_file = dest_folder.joinpath(rel_path)
                except ValueError:
                    dest_file = dest_folder.joinpath(
                        f"{path.parent.name}-{path.name}"
                    )
                os.makedirs(str(dest_file.parent), exist_ok=True)
                dest_file.write_text(self.raw_config_data[i])

    def _find_section_info(
        self, section: str, file_data: List[str], raise_error: bool = True
    ) -> Dict[str, Any]:
        options: Dict[str, Dict[str, Any]] = {}
        result: Dict[str, Any] = {
            "indent": -1,
            "start": -1,
            "end": -1,
            "options": options
        }
        last_option: str = ""
        opt_indent = -1
        for idx, line in enumerate(file_data):
            if not line.strip() or line.lstrip()[0] in "#;":
                # skip empty lines, whitespace, and comments
                continue
            line = line.expandtabs()
            line_indent = len(line) - len(line.strip())
            if opt_indent != -1 and line_indent > opt_indent:
                if last_option:
                    options[last_option]["end"] = idx + 1
                # Continuation of an option
                if result["start"] != -1:
                    result["end"] = idx + 1
                continue
            sec_match = self.section_r.match(line)
            if sec_match is not None:
                opt_indent = -1
                if result["start"] != -1:
                    break
                cursec = sec_match.group(1)
                if section == cursec:
                    result["indent"] = line_indent
                    result["start"] = idx
                    result["end"] = idx + 1
            else:
                # This is an option
                opt_indent = line_indent
                if result["start"] != -1:
                    result["end"] = idx + 1
                    last_option = re.split(r"[:=]", line, 1)[0].strip()
                    options[last_option] = {
                        "indent": line_indent,
                        "start": idx,
                        "end": idx + 1
                    }
        if result["start"] != -1:
            return result
        if raise_error:
            raise ConfigError(f"Unable to find section [{section}]")
        return {}

    def get_file_sections(self) -> Dict[str, List[str]]:
        sections_by_file: Dict[str, List[str]] = {
            str(fname): [] for fname in self.files
        }
        for section, idx_list in self.file_section_map.items():
            for idx in idx_list:
                fname = str(self.files[idx])
                sections_by_file[fname].append(section)
        return sections_by_file

    def find_config_file(
        self, section: str, option: Optional[str] = None
    ) -> Optional[pathlib.Path]:
        idx: int = -1
        if option is not None:
            key = (section, option)
            if key in self.file_option_map:
                idx = self.file_option_map[key][0]
        elif section in self.file_section_map:
            idx = self.file_section_map[section][0]
        if idx == -1:
            return None
        return self.files[idx]

    def _write_buffer(self, buffer: List[str], fpath: pathlib.Path) -> None:
        if not buffer:
            return
        self.config.read_string("\n".join(buffer), fpath.name)

    def _parse_file(
        self, file_path: pathlib.Path, visited: List[Tuple[int, int]]
    ) -> None:
        buffer: List[str] = []
        try:
            stat = file_path.stat()
            cur_stat = (stat.st_dev, stat.st_ino)
            if cur_stat in visited:
                raise ConfigError(
                    f"Recursive include directive detected, {file_path}"
                )
            visited.append(cur_stat)
            self.files.append(file_path)
            file_index = len(self.files) - 1
            cfg_data = file_path.read_text(encoding="utf-8")
            self.raw_config_data.append(cfg_data)
            lines = cfg_data.splitlines()
            last_section = ""
            opt_indent = -1
            for line in lines:
                if not line.strip() or line.lstrip()[0] in "#;":
                    # ignore lines that contain only whitespace/comments
                    continue
                line = line.expandtabs(tabsize=4)
                # Remove inline comments
                for prefix in "#;":
                    icmt = line.find(prefix)
                    if icmt > 0 and line[icmt-1] != "\\":
                        # inline comment, remove it
                        line = line[:icmt]
                        break
                line_indent = len(line) - len(line.lstrip())
                if opt_indent != -1 and line_indent > opt_indent:
                    # Multi-line value, append to buffer and resume parsing
                    buffer.append(line)
                    continue
                sect_match = self.section_r.match(line)
                if sect_match is not None:
                    # Section detected
                    opt_indent = -1
                    section = sect_match.group(1)
                    if section.startswith("include "):
                        inc_path = section[8:].strip()
                        if not inc_path:
                            raise ConfigError(
                                f"Invalid include directive: [{section}]"
                            )
                        if inc_path[0] == "/":
                            new_path = pathlib.Path(inc_path).resolve()
                            paths = sorted(new_path.parent.glob(new_path.name))
                        else:
                            paths = sorted(file_path.parent.glob(inc_path))
                        if not paths:
                            raise ConfigError(
                                "No files matching include directive "
                                f"[{section}]"
                            )
                        # Write out buffered data to the config before parsing
                        # included files
                        self._write_buffer(buffer, file_path)
                        buffer.clear()
                        for p in paths:
                            self._parse_file(p, visited)
                        # Don't add included sections to the configparser
                        continue
                    else:
                        last_section = section
                        if section not in self.file_section_map:
                            self.file_section_map[section] = []
                        elif file_index in self.file_section_map[section]:
                            raise ConfigError(
                                f"Duplicate section [{section}] in file "
                                f"{file_path}"
                            )
                        self.file_section_map[section].insert(0, file_index)
                else:
                    # This line must specify an option
                    opt_indent = line_indent
                    option = re.split(r"[:=]", line, 1)[0].strip()
                    key = (last_section, option)
                    if key not in self.file_option_map:
                        self.file_option_map[key] = []
                    elif file_index in self.file_option_map[key]:
                        raise ConfigError(
                            f"Duplicate option '{option}' in section "
                            f"[{last_section}], file {file_path} "
                        )
                    self.file_option_map[key].insert(0, file_index)
                buffer.append(line)
            self._write_buffer(buffer, file_path)
        except ConfigError:
            raise
        except Exception as e:
            if not file_path.is_file():
                raise ConfigError(
                    f"Configuration File Not Found: '{file_path}''") from e
            if not os.access(file_path, os.R_OK):
                raise ConfigError(
                    "Moonraker does not have Read/Write permission for "
                    f"config file at path '{file_path}'") from e
            raise ConfigError(f"Error Reading Config: '{file_path}'") from e

    def read_file(self, main_conf: pathlib.Path) -> None:
        self.config.clear()
        self.files.clear()
        self.raw_config_data.clear()
        self.updates_pending.clear()
        self.file_section_map.clear()
        self.file_option_map.clear()
        self._parse_file(main_conf, [])
        size = sum([len(rawcfg) for rawcfg in self.raw_config_data])
        logging.info(
            f"Configuration File '{main_conf}' parsed, total size: {size} B"
        )


def get_configuration(
    server: Server, app_args: Dict[str, Any]
) -> ConfigHelper:
    start_path = pathlib.Path(app_args['config_file']).expanduser().resolve()
    source = FileSourceWrapper(server)
    source.read_file(start_path)
    if not source.config.has_section('server'):
        raise ConfigError("No section [server] in config")
    return ConfigHelper(server, source, 'server', {})

def find_config_backup(cfg_path: str) -> Optional[str]:
    cfg = pathlib.Path(cfg_path).expanduser().resolve()
    backup = cfg.parent.joinpath(f".{cfg.name}.bkp")
    if backup.is_file():
        return str(backup)
    return None
