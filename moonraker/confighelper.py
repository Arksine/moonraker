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
    from io import TextIOWrapper
    _T = TypeVar("_T")
    ConfigVal = Union[None, int, float, bool, str, dict, list]

SENTINEL = SentinelClass.get_instance()
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
        self.config = config_source.config
        self.section = section
        self.fallback_section: Optional[str] = fallback_section
        self.parsed = parsed
        if self.section not in self.parsed:
            self.parsed[self.section] = {}
        self.sections = self.config.sections
        self.has_section = self.config.has_section

    def get_server(self) -> Server:
        return self.server

    def __getitem__(self, key: str) -> ConfigHelper:
        return self.getsection(key)

    def __contains__(self, key: str) -> bool:
        return key in self.config

    def has_option(self, option: str) -> bool:
        return self.config.has_option(self.section, option)

    def set_option(self, option: str, value: str) -> None:
        self.config[self.section][option] = value

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
                    default: Union[SentinelClass, _T],
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
            if isinstance(default, SentinelClass):
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
                 separators: Tuple[Optional[str], ...] = ('\n',),
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
                default: Union[SentinelClass, _T] = SENTINEL,
                separator: Optional[str] = '\n',
                count: Optional[int] = None,
                deprecate: bool = False
                ) -> Union[List[str], _T]:
        return self.getlists(option, default, str, (separator,), (count,),
                             deprecate=deprecate)

    def getintlist(self,
                   option: str,
                   default: Union[SentinelClass, _T] = SENTINEL,
                   separator: Optional[str] = '\n',
                   count: Optional[int] = None,
                   deprecate: bool = False
                   ) -> Union[List[int], _T]:
        return self.getlists(option, default, int, (separator,), (count,),
                             deprecate=deprecate)

    def getfloatlist(self,
                     option: str,
                     default: Union[SentinelClass, _T] = SENTINEL,
                     separator: Optional[str] = '\n',
                     count: Optional[int] = None,
                     deprecate: bool = False
                     ) -> Union[List[float], _T]:
        return self.getlists(option, default, float, (separator,), (count,),
                             deprecate=deprecate)

    def getdict(self,
                option: str,
                default: Union[SentinelClass, _T] = SENTINEL,
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

    def read_supplemental_dict(self, obj: Dict[str, Any]) -> ConfigHelper:
        if not obj:
            raise ConfigError(f"Cannot ready Empty Dict")
        source = ConfigSourceWrapper()
        source.read_dict(obj)
        sections = source.config.sections()
        return ConfigHelper(self.server, source, sections[0], {})

    def read_supplemental_config(self, file_name: str) -> ConfigHelper:
        fpath = pathlib.Path(file_name).expanduser().resolve()
        source = ConfigSourceWrapper()
        source.read_file(fpath)
        sections = source.config.sections()
        return ConfigHelper(self.server, source, sections[0], {})

    def write_config(self, file_obj: IO[str]) -> None:
        self.config.write(file_obj)

    def get_parsed_config(self) -> Dict[str, Dict[str, ConfigVal]]:
        return dict(self.parsed)

    def get_orig_config(self) -> Dict[str, Dict[str, str]]:
        return {
            key: dict(val) for key, val in self.config.items()
        }

    def get_file_sections(self) -> Dict[str, List[str]]:
        return self.source.get_file_sections(self.section)

    def get_config_files(self) -> List[str]:
        return [str(f) for f in self.source.files]

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

    def create_backup(self):
        cfg_path = self.server.get_app_args()["config_file"]
        cfg = pathlib.Path(cfg_path).expanduser().resolve()
        backup = cfg.parent.joinpath(f".{cfg.name}.bkp")
        backup_fp: Optional[TextIOWrapper] = None
        try:
            if backup.exists():
                cfg_mtime: int = 0
                for cfg in self.source.files:
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
    section_r = re.compile(r"\s*\[([^]]+)\]")

    def __init__(self) -> None:
        self.config = configparser.ConfigParser(interpolation=None)
        self.files: List[pathlib.Path] = []
        self.file_section_map: Dict[str, List[int]] = {}
        self.file_option_map: Dict[Tuple[str, str], List[int]] = {}

    def get_file_sections(self, section: str) -> Dict[str, List[str]]:
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
            lines = file_path.read_text().splitlines()
            last_section = ""
            opt_indent = -1
            for line in lines:
                if not line.strip():
                    # ignore a line that contains only whitespace
                    continue
                line = line.expandtabs(tabsize=4)
                # Remove inline comments
                for prefix in "#;":
                    icmt = line.find(prefix)
                    if icmt >= 0 and line.lstrip()[0] == prefix:
                        # This line is a comment, ignore it
                        continue
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
                        fsm = self.file_section_map
                        fsm.setdefault(section, []).insert(0, file_index)
                else:
                    # This line must specify an option
                    opt_indent = line_indent
                    option = re.split(r"[:=]", line, 1)[0].strip()
                    key = (last_section, option)
                    fom = self.file_option_map
                    fom.setdefault(key, []).insert(0, file_index)
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
        self.file_section_map.clear()
        self.file_option_map.clear()
        self._parse_file(main_conf, [])

    def read_dict(self, cfg: Dict[str, Any]) -> None:
        try:
            self.config.read_dict(cfg)
        except Exception as e:
            raise ConfigError("Error Reading config as dict") from e


def get_configuration(
    server: Server, app_args: Dict[str, Any]
) -> ConfigHelper:
    start_path = pathlib.Path(app_args['config_file']).expanduser().resolve()
    source = ConfigSourceWrapper()
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
