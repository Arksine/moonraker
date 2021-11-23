# GPIO Factory helper
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
from __future__ import annotations
import logging
from utils import load_system_module

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Tuple,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper

class GpioFactory:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.gpiod: Any = load_system_module("gpiod")
        self.chips: Dict[str, Any] = {}
        self.reserved_gpios: Dict[str, GpioOutputPin] = {}
        version: str = self.gpiod.version_string()
        self.gpiod_version = tuple(int(v) for v in version.split('.'))

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
        pin_id, chip_id, invert = self._parse_pin(pin_name)
        full_name = f"{pin_id}:{chip_id}"
        if full_name in self.reserved_gpios:
            raise self.server.error(f"GPIO {full_name} already reserved")
        try:
            chip = self._get_gpio_chip(chip_id)
            line = chip.get_line(pin_id)
            args: Dict[str, Any] = {
                'consumer': "moonraker",
                'type': self.gpiod.LINE_REQ_DIR_OUT
            }
            if invert:
                args['flags'] = self.gpiod.LINE_REQ_FLAG_ACTIVE_LOW
            if self.gpiod_version < (1, 3):
                args['default_vals'] = [initial_value]
            else:
                args['default_val'] = initial_value
            line.request(**args)
        except Exception:
            logging.exception(
                f"Unable to init {pin_id}.  Make sure the gpio is not in "
                "use by another program or exported by sysfs.")
            raise
        gpio_out = GpioOutputPin(pin_name, full_name, line, invert,
                                 initial_value)
        self.reserved_gpios[full_name] = gpio_out
        return gpio_out

    def _parse_pin(self, pin_name: str) -> Tuple[int, str, bool]:
        pin = pin_name
        invert = False
        if pin[0] == "!":
            pin = pin[1:]
            invert = True
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
        return pin_id, chip_id, invert

    def close(self) -> None:
        for output_pin in self.reserved_gpios.values():
            output_pin.release()
        for chip in self.chips.values():
            chip.close()

class GpioOutputPin:
    def __init__(self,
                 orig_name: str,
                 name: str,
                 line: Any,
                 inverted: bool,
                 initial_val: int
                 ) -> None:
        self.orig = orig_name
        self.name = name
        self.line = line
        self.inverted = inverted
        self.value = initial_val
        self.release = line.release

    def write(self, value: int) -> None:
        self.value = int(not not value)
        self.line.set_value(self.value)

    def is_inverted(self) -> bool:
        return self.inverted

    def get_value(self) -> int:
        return self.value

    def get_name(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.orig

def load_component(config: ConfigHelper) -> GpioFactory:
    return GpioFactory(config)
