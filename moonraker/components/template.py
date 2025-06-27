# Template Factory helper
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
import asyncio
import jinja2
from ..utils import json_wrapper as jsonw
from ..common import RenderableTemplate

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict
)

if TYPE_CHECKING:
    from ..server import Server
    from ..confighelper import ConfigHelper
    from .secrets import Secrets

class TemplateFactory:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        secrets: Secrets = self.server.load_component(config, 'secrets')
        self.jenv = jinja2.Environment('{%', '%}', '{', '}')
        self.async_env = jinja2.Environment(
            '{%', '%}', '{', '}', enable_async=True
        )
        self.ui_env = jinja2.Environment(enable_async=True)
        self.jenv.add_extension("jinja2.ext.do")
        self.jenv.filters['fromjson'] = jsonw.loads
        self.async_env.add_extension("jinja2.ext.do")
        self.async_env.filters['fromjson'] = jsonw.loads
        self.ui_env.add_extension("jinja2.ext.do")
        self.ui_env.filters['fromjson'] = jsonw.loads
        self.add_environment_global("raise_error", self._raise_error)
        self.add_environment_global("secrets", secrets)
        self.add_environment_global("log_debug", logging.debug)

    def add_environment_global(self, name: str, value: Any):
        if name in self.jenv.globals:
            raise self.server.error(
                f"Jinja 2 environment already contains global {name}")
        self.jenv.globals[name] = value
        self.async_env.globals[name] = value

    def _raise_error(self, err_msg: str, err_code: int = 400) -> None:
        raise self.server.error(err_msg, err_code)

    def create_template(self,
                        source: str,
                        is_async: bool = False
                        ) -> JinjaTemplate:
        env = self.async_env if is_async else self.jenv
        try:
            template = env.from_string(source)
        except Exception:
            logging.exception(f"Error creating template from source:\n{source}")
            raise
        return JinjaTemplate(source, self.server, template, is_async)

    def create_ui_template(self, source: str) -> JinjaTemplate:
        try:
            template = self.ui_env.from_string(source)
        except Exception:
            logging.exception(f"Error creating template from source:\n{source}")
            raise
        return JinjaTemplate(source, self.server, template, True)


class JinjaTemplate(RenderableTemplate):
    def __init__(self,
                 source: str,
                 server: Server,
                 template: jinja2.Template,
                 is_async: bool
                 ) -> None:
        self.server = server
        self.orig_source = source
        self.template = template
        self.is_async = is_async

    def __str__(self) -> str:
        return self.orig_source

    def render(self, context: Dict[str, Any] = {}) -> str:
        if self.is_async:
            raise self.server.error(
                "Cannot render async templates with the render() method"
                ", use render_async()")
        try:
            return self.template.render(context).strip()
        except Exception as e:
            msg = "Error rending Jinja2 Template"
            if self.server.is_configured():
                raise self.server.error(msg, 500) from e
            raise self.server.config_error(msg) from e

    async def render_async(self, context: Dict[str, Any] = {}) -> str:
        try:
            ret = await self.template.render_async(context)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            raise self.server.error("Error rending Jinja2 Template", 500) from e
        return ret.strip()

def load_component(config: ConfigHelper) -> TemplateFactory:
    return TemplateFactory(config)
