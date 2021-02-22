# Configuration Helper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import configparser
import os
import logging

class ConfigError(Exception):
    pass

class Sentinel:
    pass

class ConfigHelper:
    error = ConfigError
    def __init__(self, server, config, section, orig_sects, parsed={}):
        self.server = server
        self.config = config
        self.section = section
        self.orig_sections = orig_sects
        self.parsed = parsed
        self.sections = config.sections
        self.has_section = config.has_section

    def get_server(self):
        return self.server

    def __getitem__(self, key):
        return self.getsection(key)

    def __contains__(self, key):
        return key in self.config

    def get_name(self):
        return self.section

    def get_options(self):
        return dict(self.config[self.section])

    def get_prefix_sections(self, prefix):
        return [s for s in self.sections() if s.startswith(prefix)]

    def getsection(self, section):
        if section not in self.config:
            raise ConfigError(f"No section [{section}] in config")
        return ConfigHelper(self.server, self.config, section,
                            self.orig_sections, self.parsed)

    def _get_option(self, func, option, default):
        try:
            val = func(option, default)
        except Exception:
            raise ConfigError(
                f"Error parsing option ({option}) from "
                f"section [{self.section}]")
        if val == Sentinel:
            raise ConfigError(
                f"No option found ({option}) in section [{self.section}]")
        if self.section in self.orig_sections:
            # Only track sections included in the original config
            self.parsed.setdefault(self.section, {})[option] = val
        return val

    def get(self, option, default=Sentinel):
        return self._get_option(
            self.config[self.section].get, option, default)

    def getint(self, option, default=Sentinel):
        return self._get_option(
            self.config[self.section].getint, option, default)

    def getboolean(self, option, default=Sentinel):
        return self._get_option(
            self.config[self.section].getboolean, option, default)

    def getfloat(self, option, default=Sentinel):
        return self._get_option(
            self.config[self.section].getfloat, option, default)

    def read_supplemental_config(self, file_name):
        cfg_file_path = os.path.normpath(os.path.expanduser(file_name))
        if not os.path.isfile(cfg_file_path):
            raise ConfigError(
                f"Configuration File Not Found: '{cfg_file_path}''")
        try:
            self.config.read(cfg_file_path)
        except Exception:
            raise ConfigError(f"Error Reading Config: '{cfg_file_path}'")

    def write_config(self, file_obj):
        self.config.write(file_obj)

    def get_parsed_config(self):
        return dict(self.parsed)

    def validate_config(self):
        for sect in self.orig_sections:
            if sect not in self.parsed:
                logging.warn(
                    f"Invalid config section [{sect}] detected. In "
                    "the future this will result in a startup error")
                continue
            parsed_opts = self.parsed[sect]
            for opt, val in self.config.items(sect):
                if opt not in parsed_opts:
                    logging.warn(
                        f"Invalid option '{opt}' detected in section "
                        f"[{sect}].  In the future this will result in a "
                        "startup error.")

def get_configuration(server, system_args):
    cfg_file_path = os.path.normpath(os.path.expanduser(
        system_args.configfile))
    if not os.path.isfile(cfg_file_path):
        raise ConfigError(f"Configuration File Not Found: '{cfg_file_path}''")
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(cfg_file_path)
    except Exception:
        raise ConfigError(f"Error Reading Config: '{cfg_file_path}'") from None

    try:
        server_cfg = config['server']
    except KeyError:
        raise ConfigError("No section [server] in config")

    orig_sections = config.sections()
    try:
        orig_sections.remove("DEFAULT")
    except Exception:
        pass

    if server_cfg.getboolean('enable_debug_logging', True):
        logging.getLogger().setLevel(logging.DEBUG)

    config['system_args'] = {
        'configfile': system_args.configfile,
        'logfile': system_args.logfile,
        'software_version': system_args.software_version}
    return ConfigHelper(server, config, 'server', orig_sections)
