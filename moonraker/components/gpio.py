# GPIO Factory helper
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
from ..utils import load_system_module

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Optional
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..eventloop import EventLoop
    GPIO_CALLBACK = Callable[[float, float, int], Optional[Awaitable[None]]]

class GpioFactory:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.gpiod: Any = load_system_module("gpiod")
        GpioEvent.init_constants(self.gpiod)
        self.chips: Dict[str, Any] = {}
        self.reserved_gpios: Dict[str, GpioBase] = {}
        version: str = self.gpiod.version_string()
        self.gpiod_version = tuple(int(v) for v in version.split('.'))
        self.server.add_log_rollover_item(
            "gpiod_version", f"libgpiod version: {version}")

    def _get_gpio_chip(self, chip_name) -> Any:
        if chip_name in self.chips:
            return self.chips[chip_name]
        chip = self.gpiod.Chip(chip_name, self.gpiod.Chip.OPEN_BY_NAME)
        self.chips[chip_name] = chip
        return chip

    def setup_gpio_out(self,
                       pin_name: str,
                       initial_value: int = 0
                       ) -> GpioOutputPin:
        initial_value = int(not not initial_value)
        pparams = self._parse_pin(pin_name)
        pparams['initial_value'] = initial_value
        line = self._request_gpio(pparams)
        try:
            gpio_out = GpioOutputPin(line, pparams)
        except Exception:
            logging.exception("Error Instantiating GpioOutputPin")
            line.release()
            raise
        full_name = pparams['full_name']
        self.reserved_gpios[full_name] = gpio_out
        return gpio_out

    def register_gpio_event(self,
                            pin_name: str,
                            callback: GPIO_CALLBACK
                            ) -> GpioEvent:
        pin_params = self._parse_pin(pin_name, type="event")
        line = self._request_gpio(pin_params)
        event_loop = self.server.get_event_loop()
        try:
            gpio_event = GpioEvent(event_loop, line, pin_params, callback)
        except Exception:
            logging.exception("Error Instantiating GpioEvent")
            line.release()
            raise
        full_name = pin_params['full_name']
        self.reserved_gpios[full_name] = gpio_event
        return gpio_event

    def _request_gpio(self, pin_params: Dict[str, Any]) -> Any:
        full_name = pin_params['full_name']
        if full_name in self.reserved_gpios:
            raise self.server.error(f"GPIO {full_name} already reserved")
        try:
            chip = self._get_gpio_chip(pin_params['chip_id'])
            line = chip.get_line(pin_params['pin_id'])
            args: Dict[str, Any] = {
                'consumer': "moonraker",
                'type': pin_params['request_type']
            }
            if 'flags' in pin_params:
                args['flags'] = pin_params['flags']
            if 'initial_value' in pin_params:
                if self.gpiod_version < (1, 3):
                    args['default_vals'] = [pin_params['initial_value']]
                else:
                    args['default_val'] = pin_params['initial_value']
            line.request(**args)
        except Exception:
            logging.exception(
                f"Unable to init {full_name}.  Make sure the gpio is not in "
                "use by another program or exported by sysfs.")
            raise
        return line

    def _parse_pin(self,
                   pin_name: str,
                   type: str = "out"
                   ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            'orig': pin_name,
            'invert': False,
        }
        pin = pin_name
        if type == "event":
            params['request_type'] = self.gpiod.LINE_REQ_EV_BOTH_EDGES
            flag: str = "disable"
            if pin[0] == "^":
                pin = pin[1:]
                flag = "pullup"
            elif pin[0] == "~":
                pin = pin[1:]
                flag = "pulldown"
            if self.gpiod_version >= (1, 5):
                flag_to_enum = {
                    "disable": self.gpiod.LINE_REQ_FLAG_BIAS_DISABLE,
                    "pullup": self.gpiod.LINE_REQ_FLAG_BIAS_PULL_UP,
                    "pulldown": self.gpiod.LINE_REQ_FLAG_BIAS_PULL_DOWN
                }
                params['flags'] = flag_to_enum[flag]
            elif flag != "disable":
                raise self.server.error(
                    f"Flag {flag} configured for event GPIO '{pin_name}'"
                    " requires libgpiod version 1.5 or later.  "
                    f"Current Version: {self.gpiod.version_string()}")
        elif type == "out":
            params['request_type'] = self.gpiod.LINE_REQ_DIR_OUT
        if pin[0] == "!":
            pin = pin[1:]
            params['invert'] = True
            if 'flags' in params:
                params['flags'] |= self.gpiod.LINE_REQ_FLAG_ACTIVE_LOW
            else:
                params['flags'] = self.gpiod.LINE_REQ_FLAG_ACTIVE_LOW
        chip_id: str = "gpiochip0"
        pin_parts = pin.split("/")
        if len(pin_parts) == 2:
            chip_id, pin = pin_parts
        elif len(pin_parts) == 1:
            pin = pin_parts[0]
        # Verify pin
        if not chip_id.startswith("gpiochip") or \
                not chip_id[-1].isdigit() or \
                not pin.startswith("gpio") or \
                not pin[4:].isdigit():
            raise self.server.error(
                f"Invalid Gpio Pin: {pin_name}")
        pin_id = int(pin[4:])
        params['pin_id'] = pin_id
        params['chip_id'] = chip_id
        params['full_name'] = f"{chip_id}:{pin}"
        return params

    def close(self) -> None:
        for line in self.reserved_gpios.values():
            line.release()
        for chip in self.chips.values():
            chip.close()

class GpioBase:
    def __init__(self,
                 line: Any,
                 pin_params: Dict[str, Any]
                 ) -> None:
        self.orig: str = pin_params['orig']
        self.name: str = pin_params['full_name']
        self.inverted: bool = pin_params['invert']
        self.line: Any = line
        self.value: int = pin_params.get('initial_value', 0)

    def release(self) -> None:
        self.line.release()

    def is_inverted(self) -> bool:
        return self.inverted

    def get_value(self) -> int:
        return self.value

    def get_name(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.orig

class GpioOutputPin(GpioBase):
    def write(self, value: int) -> None:
        self.value = int(not not value)
        self.line.set_value(self.value)


MAX_ERRORS = 50
ERROR_RESET_TIME = 5.

class GpioEvent(GpioBase):
    EVENT_FALLING_EDGE = 0
    EVENT_RISING_EDGE = 1
    def __init__(self,
                 event_loop: EventLoop,
                 line: Any,
                 pin_params: Dict[str, Any],
                 callback: GPIO_CALLBACK
                 ) -> None:
        super().__init__(line, pin_params)
        self.event_loop = event_loop
        self.fd = line.event_get_fd()
        self.callback = callback
        self.on_error: Optional[Callable[[str], None]] = None
        self.min_evt_time = 0.
        self.last_event_time = 0.
        self.error_count = 0
        self.last_error_reset = 0.
        self.started = False

    @classmethod
    def init_constants(cls, gpiod: Any) -> None:
        cls.EVENT_RISING_EDGE = gpiod.LineEvent.RISING_EDGE
        cls.EVENT_FALLING_EDGE = gpiod.LineEvent.FALLING_EDGE

    def setup_debounce(self,
                       min_evt_time: float,
                       err_callback: Optional[Callable[[str], None]]
                       ) -> None:
        self.min_evt_time = max(min_evt_time, 0.)
        self.on_error = err_callback

    def start(self) -> None:
        if not self.started:
            self.value = self.line.get_value()
            self.last_event_time = self.event_loop.get_loop_time()
            self.event_loop.add_reader(self.fd, self._on_event_trigger)
            self.started = True
            logging.debug(f"GPIO {self.name}: Listening for events, "
                          f"current state: {self.value}")

    def stop(self) -> None:
        if self.started:
            self.event_loop.remove_reader(self.fd)
            self.started = False

    def release(self) -> None:
        self.stop()
        self.line.release()

    def _on_event_trigger(self) -> None:
        evt = self.line.event_read()
        last_val = self.value
        if evt.type == self.EVENT_RISING_EDGE:
            self.value = 1
        elif evt.type == self.EVENT_FALLING_EDGE:
            self.value = 0
        eventtime = self.event_loop.get_loop_time()
        evt_duration = eventtime - self.last_event_time
        if last_val == self.value or evt_duration < self.min_evt_time:
            self._increment_error(eventtime)
            return
        self.last_event_time = eventtime
        self.error_count = 0
        ret = self.callback(eventtime, evt_duration, self.value)
        if ret is not None:
            self.event_loop.create_task(ret)  # type: ignore

    def _increment_error(self, eventtime: float) -> None:
        if eventtime - self.last_error_reset > ERROR_RESET_TIME:
            self.error_count = 0
            self.last_error_reset = eventtime
        self.error_count += 1
        if self.error_count >= MAX_ERRORS:
            self.stop()
            if self.on_error is not None:
                self.on_error("Too Many Consecutive Errors, "
                              f"GPIO Event Disabled on {self.name}")


def load_component(config: ConfigHelper) -> GpioFactory:
    return GpioFactory(config)
