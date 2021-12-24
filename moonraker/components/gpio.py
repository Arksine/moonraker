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
    Dict
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
        pparams = self._parse_pin(pin_name)
        full_name = pparams['full_name']
        if full_name in self.reserved_gpios:
            raise self.server.error(f"GPIO {full_name} already reserved")
        try:
            chip = self._get_gpio_chip(pparams['chip_id'])
            line = chip.get_line(pparams['pin_id'])
            args: Dict[str, Any] = {
                'consumer': "moonraker",
                'type': self.gpiod.LINE_REQ_DIR_OUT
            }
            if pparams['invert']:
                args['flags'] = self.gpiod.LINE_REQ_FLAG_ACTIVE_LOW
            if self.gpiod_version < (1, 3):
                args['default_vals'] = [initial_value]
            else:
                args['default_val'] = initial_value
            line.request(**args)
        except Exception:
            logging.exception(
                f"Unable to init {full_name}.  Make sure the gpio is not in "
                "use by another program or exported by sysfs.")
            raise
        gpio_out = GpioOutputPin(line, pparams, initial_value)
        self.reserved_gpios[full_name] = gpio_out
        return gpio_out

    def _parse_pin(self, pin_name: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            'orig': pin_name,
            'invert': False,
        }
        pin = pin_name
        if pin[0] == "!":
            pin = pin[1:]
            params['invert'] = True
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

class GpioOutputPin:
    def __init__(self,
                 line: Any,
                 pin_params: Dict[str, Any],
                 initial_val: int
                 ) -> None:
        self.orig = pin_params['orig']
        self.name = pin_params['full_name']
        self.line = line
        self.inverted = pin_params['invert']
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
