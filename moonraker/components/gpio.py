# GPIO Factory helper
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import os
import re
import asyncio
import pathlib
import logging
import periphery
from ..utils import KERNEL_VERSION

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

GpioEventCallback = Callable[[float, float, int], Optional[Awaitable[None]]]

GPIO_PATTERN = r"""
    (?P<bias>[~^])?
    (?P<inverted>!)?
    (?:(?P<chip_id>gpiochip[0-9]+)/)?
    (?P<pin_name>gpio(?P<pin_id>[0-9]+))
"""

BIAS_FLAG_TO_DESC: Dict[str, str] = {
    "^": "pull_up",
    "~": "pull_down",
    "*": "disable" if KERNEL_VERSION >= (5, 5) else "default"
}

class GpioFactory:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.reserved_gpios: Dict[str, GpioBase] = {}

    def setup_gpio_out(self, pin_name: str, initial_value: int = 0) -> GpioOutputPin:
        initial_value = int(not not initial_value)
        pparams = self._parse_pin(pin_name, initial_value)
        gpio = self._request_gpio(pparams)
        try:
            gpio_out = GpioOutputPin(gpio, pparams)
        except Exception:
            logging.exception("Error Instantiating GpioOutputPin")
            gpio.close()
            raise
        full_name = pparams["full_name"]
        self.reserved_gpios[full_name] = gpio_out
        return gpio_out

    def register_gpio_event(
        self, pin_name: str, callback: GpioEventCallback
    ) -> GpioEvent:
        pin_params = self._parse_pin(pin_name, req_type="event")
        gpio = self._request_gpio(pin_params)
        event_loop = self.server.get_event_loop()
        try:
            gpio_event = GpioEvent(event_loop, gpio, pin_params, callback)
        except Exception:
            logging.exception("Error Instantiating GpioEvent")
            gpio.close()
            raise
        full_name = pin_params["full_name"]
        self.reserved_gpios[full_name] = gpio_event
        return gpio_event

    def _request_gpio(self, pin_params: Dict[str, Any]) -> periphery.GPIO:
        full_name = pin_params["full_name"]
        if full_name in self.reserved_gpios:
            raise self.server.error(f"GPIO {full_name} already reserved")
        chip_path = pathlib.Path("/dev").joinpath(pin_params["chip_id"])
        if not chip_path.exists():
            raise self.server.error(f"Chip path {chip_path} does not exist")
        try:
            gpio = periphery.GPIO(
                str(chip_path),
                pin_params["pin_id"],
                pin_params["direction"],
                edge=pin_params.get("edge", "none"),
                bias=pin_params.get("bias", "default"),
                inverted=pin_params["inverted"],
                label="moonraker"
            )
        except Exception:
            logging.exception(
                f"Unable to init {full_name}.  Make sure the gpio is not in "
                "use by another program or exported by sysfs.")
            raise
        return gpio

    def _parse_pin(
        self, pin_desc: str, initial_value: int = 0, req_type: str = "out"
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "orig": pin_desc,
            "inverted": False,
            "request_type": req_type,
            "initial_value": initial_value
        }
        pin_match = re.match(GPIO_PATTERN, pin_desc, re.VERBOSE)
        if pin_match is None:
            raise self.server.error(
                f"Invalid pin format {pin_desc}. Refer to the configuration "
                "documentation for details on the pin format."
            )
        bias_flag: Optional[str] = pin_match.group("bias")
        params["inverted"] = pin_match.group("inverted") is not None
        if req_type == "event":
            params["direction"] = "in"
            params["edge"] = "both"
            params["bias"] = BIAS_FLAG_TO_DESC[bias_flag or "*"]
        elif req_type == "out":
            if bias_flag is not None:
                raise self.server.error(
                    f"Invalid pin format {pin_desc}.  Bias flag {bias_flag} "
                    "not available for output pins."
                )
            initial_state = bool(initial_value) ^ params["inverted"]
            params["direction"] = "low" if not initial_state else "high"
        chip_id: str = pin_match.group("chip_id") or "gpiochip0"
        pin_name: str = pin_match.group("pin_name")
        params["pin_id"] = int(pin_match.group("pin_id"))
        params["chip_id"] = chip_id
        params["full_name"] = f"{chip_id}:{pin_name}"
        return params

    def close(self) -> None:
        for gpio in self.reserved_gpios.values():
            gpio.close()

class GpioBase:
    def __init__(
        self, gpio: periphery.GPIO, pin_params: Dict[str, Any]
    ) -> None:
        self.orig: str = pin_params["orig"]
        self.name: str = pin_params["full_name"]
        self.inverted: bool = pin_params["inverted"]
        self.gpio = gpio
        self.value: int = pin_params.get("initial_value", 0)

    def close(self) -> None:
        self.gpio.close()

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
        self.gpio.write(bool(self.value))


MAX_ERRORS = 50
ERROR_RESET_TIME = 5.

class GpioEvent(GpioBase):
    def __init__(
        self,
        event_loop: EventLoop,
        gpio: periphery.GPIO,
        pin_params: Dict[str, Any],
        callback: GpioEventCallback
    ) -> None:
        super().__init__(gpio, pin_params)
        self.event_loop = event_loop
        self.callback = callback
        self.on_error: Optional[Callable[[str], None]] = None
        self.debounce_period: float = 0
        self.last_event_time: float = 0.
        self.error_count = 0
        self.last_error_reset = 0.
        self.started = False
        self.debounce_task: Optional[asyncio.Task] = None
        os.set_blocking(self.gpio.fd, False)

    def fileno(self) -> int:
        return self.gpio.fd

    def setup_debounce(
        self, debounce_period: float, err_callback: Optional[Callable[[str], None]]
    ) -> None:
        self.debounce_period = max(debounce_period, 0)
        self.on_error = err_callback

    def start(self) -> None:
        if not self.started:
            self.value = int(self.gpio.read())
            self.last_event_time = self.event_loop.get_loop_time()
            self.event_loop.add_reader(self.gpio.fd, self._on_event_trigger)
            self.started = True
            logging.debug(f"GPIO {self.name}: Listening for events, "
                          f"current state: {self.value}")

    def stop(self) -> None:
        if self.debounce_task is not None:
            self.debounce_task.cancel()
            self.debounce_task = None
        if self.started:
            self.event_loop.remove_reader(self.gpio.fd)
            self.started = False

    def close(self) -> None:
        self.stop()
        self.gpio.close()

    def _on_event_trigger(self) -> None:
        evt = self.gpio.read_event()
        last_value = self.value
        if evt.edge == "rising":     # type: ignore
            self.value = 1
        elif evt.edge == "falling":  # type: ignore
            self.value = 0
        else:
            return
        if self.debounce_period:
            if self.debounce_task is None:
                coro = self._debounce(last_value)
                self.debounce_task = self.event_loop.create_task(coro)
            else:
                self._increment_error()
        elif last_value != self.value:
            # No debounce period and change detected
            self._run_callback()

    async def _debounce(self, last_value: int) -> None:
        await asyncio.sleep(self.debounce_period)
        self.debounce_task = None
        if last_value != self.value:
            self._run_callback()

    def _run_callback(self) -> None:
        eventtime = self.event_loop.get_loop_time()
        evt_duration = eventtime - self.last_event_time
        self.last_event_time = eventtime
        ret = self.callback(eventtime, evt_duration, self.value)
        if ret is not None:
            self.event_loop.create_task(ret)  # type: ignore

    def _increment_error(self) -> None:
        eventtime = self.event_loop.get_loop_time()
        if eventtime - self.last_error_reset > ERROR_RESET_TIME:
            self.error_count = 0
            self.last_error_reset = eventtime
        self.error_count += 1
        if self.error_count >= MAX_ERRORS:
            self.stop()
            if self.on_error is not None:
                self.on_error(
                    f"Too Many Consecutive Errors, GPIO Event Disabled on {self.name}"
                )


def load_component(config: ConfigHelper) -> GpioFactory:
    return GpioFactory(config)
