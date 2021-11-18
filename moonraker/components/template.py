# Template Factory helper
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
import jinja2
import json

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict
)

if TYPE_CHECKING:
    from moonraker import Server
    from confighelper import ConfigHelper

class TemplateFactory:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.jenv = jinja2.Environment('{%', '%}', '{', '}')
        self.jenv.add_extension("jinja2.ext.do")
        self.jenv.filters['fromjson'] = json.loads
        self.add_environment_global('raise_error', self._raise_error)

    def add_environment_global(self, name: str, value: Any):
        if name in self.jenv.globals:
            raise self.server.error(
                f"Jinja 2 environment already contains global {name}")
        self.jenv.globals[name] = value

    def _raise_error(self, err_msg: str, err_code: int = 400) -> None:
        raise self.server.error(err_msg, err_code)

    def create_template(self, source: str) -> JinjaTemplate:
        return JinjaTemplate(source, self.server, self.jenv)

class JinjaTemplate:
    def __init__(self,
                 source: str,
                 server: Server,
                 env: jinja2.Environment
                 ) -> None:
        self.server = server
        self.orig_source = source.strip()
        try:
            self.template = env.from_string(self.orig_source)
        except Exception:
            logging.exception(f"Error creating template from source:\n{source}")
            raise

    def render(self, context: Dict[str, Any] = {}) -> str:
        try:
            return self.template.render(context).strip()
        except Exception as e:
            raise self.server.error("Error rendering template") from e

    def __str__(self) -> str:
        return self.orig_source

def load_component(config: ConfigHelper) -> TemplateFactory:
    return TemplateFactory(config)
