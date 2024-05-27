# Support for GPIO Button actions
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import asyncio
import logging

from typing import (
    TYPE_CHECKING,
    Any,
    Dict
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .application import InternalTransport as ITransport


class ButtonManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.buttons: Dict[str, GpioButton] = {}
        prefix_sections = config.get_prefix_sections("button")
        logging.info(f"Loading Buttons: {prefix_sections}")
        for section in prefix_sections:
            cfg = config[section]
            # Reserve the "type" option for future use
            btn_type = cfg.get('type', "gpio")  # noqa: F841
            try:
                btn = GpioButton(cfg)
            except Exception as e:
                msg = f"Failed to load button [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg, exc_info=e)
                continue
            self.buttons[btn.name] = btn
        self.server.register_notification("button:button_event")

    def component_init(self) -> None:
        for btn in self.buttons.values():
            btn.initialize()

class GpioButton:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.eventloop = self.server.get_event_loop()
        self.name = config.get_name().split()[-1]
        self.itransport: ITransport = self.server.lookup_component("internal_transport")
        self.mutex = asyncio.Lock()
        self.gpio_event = config.getgpioevent("pin", self._on_gpio_event)
        self.min_event_time = config.getfloat("minimum_event_time", 0, minval=0.0)
        debounce_period = config.getfloat("debounce_period", .05, minval=0.01)
        self.gpio_event.setup_debounce(debounce_period, self._on_gpio_error)
        self.press_template = config.gettemplate("on_press", None, is_async=True)
        self.release_template = config.gettemplate("on_release", None, is_async=True)
        if (
            self.press_template is None and
            self.release_template is None
        ):
            raise config.error(
                f"[{config.get_name()}]: No template option configured"
            )
        self.notification_sent: bool = False
        self.user_data: Dict[str, Any] = {}
        self.context: Dict[str, Any] = {
            'call_method': self.itransport.call_method,
            'send_notification': self._send_notification,
            'event': {
                'elapsed_time': 0.,
                'received_time': 0.,
                'render_time': 0.,
                'pressed': False,
            },
            'user_data': self.user_data
        }

    def initialize(self) -> None:
        self.gpio_event.start()
        self.context['event']['pressed'] = bool(self.gpio_event.get_value())

    def get_status(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'type': "gpio",
            'event': self.context['event'],
        }

    def _send_notification(self, result: Any = None) -> None:
        if self.notification_sent:
            # Only allow execution once per template
            return
        self.notification_sent = True
        data = self.get_status()
        data['aux'] = result
        self.server.send_event("button:button_event", data)

    async def _on_gpio_event(
        self, eventtime: float, elapsed_time: float, pressed: int
    ) -> None:
        if elapsed_time < self.min_event_time:
            return
        template = self.press_template if pressed else self.release_template
        if template is None:
            return
        async with self.mutex:
            self.notification_sent = False
            event_info: Dict[str, Any] = {
                'elapsed_time': elapsed_time,
                'received_time': eventtime,
                'render_time': self.eventloop.get_loop_time(),
                'pressed': bool(pressed)
            }
            self.context['event'] = event_info
            try:
                await template.render_async(self.context)
            except Exception:
                action = "on_press" if pressed else "on_release"
                logging.exception(
                    f"Button {self.name}: '{action}' template error")

    def _on_gpio_error(self, message: str) -> None:
        self.server.add_warning(f"Button {self.name}: {message}")

def load_component(config: ConfigHelper) -> ButtonManager:
    return ButtonManager(config)
