from __future__ import annotations
import os
import logging
from typing import Dict, Optional, List, Tuple

class GpioException(Exception):
    pass

class MockGpiod:
    LINE_REQ_DIR_OUT = 3
    LINE_REQ_EV_BOTH_EDGES = 6
    LINE_REQ_FLAG_ACTIVE_LOW = 1 << 2
    LINE_REQ_FLAG_BIAS_DISABLE = 1 << 3
    LINE_REQ_FLAG_BIAS_PULL_DOWN = 1 << 4
    LINE_REQ_FLAG_BIAS_PULL_UP = 1 << 5

    def __init__(self, version: str = "1.2") -> None:
        self.version = version
        self.Chip = MockChipWrapper(self)
        self.LineEvent = MockLineEvent
        self.chips: Dict[str, MockChip] = {}

    def version_string(self) -> str:
        return self.version

    def version_tuple(self) -> Tuple[int, ...]:
        return tuple([int(v) for v in self.version.split(".")])

    def get_chip(self, chip_name) -> Optional[MockChip]:
        return self.chips.get(chip_name, None)

    def add_chip(self, chip: MockChip):
        self.chips[chip.name] = chip

    def pop_chip(self, name: str):
        self.chips.pop(name, None)

    def find_line(self, chip_id: str, pin_id: str) -> MockLine:
        if chip_id not in self.chips:
            raise GpioException(f"Unable to find chip {chip_id}")
        return self.chips[chip_id].find_line(pin_id)

class MockChipWrapper:
    OPEN_BY_NAME = 2
    def __init__(self, gpiod: MockGpiod) -> None:
        self.mock_gpiod = gpiod

    def __call__(self, chip_name: str, flags: int) -> MockChip:
        if chip_name in self.mock_gpiod.chips:
            return self.mock_gpiod.chips[chip_name]
        chip = MockChip(chip_name, flags, self.mock_gpiod)
        self.mock_gpiod.add_chip(chip)
        return chip

class MockChip:
    def __init__(self,
                 chip_name: str,
                 flags: int,
                 mock_gpiod: MockGpiod
                 ) -> None:
        self.name = chip_name
        self.flags = flags
        self.mock_gpiod = mock_gpiod
        self.requested_lines: Dict[str, MockLine] = {}

    def get_line(self, pin_id: str) -> MockLine:
        if pin_id in self.requested_lines:
            raise GpioException(f"Line {pin_id} already reserved")
        line = MockLine(self, pin_id, self.mock_gpiod)
        self.requested_lines[pin_id] = line
        return line

    def find_line(self, pin_id: str) -> MockLine:
        if pin_id not in self.requested_lines:
            raise GpioException(f"Unable to find line {pin_id}")
        return self.requested_lines[pin_id]

    def pop_line(self, name: str) -> None:
        self.requested_lines.pop(name, None)

    def close(self) -> None:
        for line in list(self.requested_lines.values()):
            line.release()
        self.requested_lines = {}
        self.mock_gpiod.pop_chip(self.name)

class MockLine:
    def __init__(self,
                 chip: MockChip,
                 name: str,
                 mock_gpiod: MockGpiod
                 ) -> None:
        self.mock_gpiod = mock_gpiod
        self.chip = chip
        self.name = name
        self.consumer_name: str = ""
        self.is_event = False
        self.invert = False
        self.value = 0
        self.read_pipe: Optional[int] = None
        self.write_pipe: Optional[int] = None
        self.bias = "not_configured"

    def request(self,
                consumer: str,
                type: int,
                flags: int = 0,
                default_vals: Optional[List[int]] = None,
                default_val: Optional[int] = None
                ) -> None:
        self.consumer_name = consumer
        version = self.mock_gpiod.version_tuple()
        if type == MockGpiod.LINE_REQ_DIR_OUT:
            self.is_event = False
            if default_vals is not None:
                if version > (1, 2):
                    logging.warn("default_vals is deprecated in gpiod 1.3+")
                self.value = default_vals[0]
            elif default_val is not None:
                if version < (1, 3):
                    raise GpioException(
                        "default_val not available in gpiod < 1.3")
                self.value = default_val
        elif type == MockGpiod.LINE_REQ_EV_BOTH_EDGES:
            self.is_event = True
            if version >= (1, 5):
                if flags & MockGpiod.LINE_REQ_FLAG_BIAS_DISABLE:
                    self.bias = "disabled"
                elif flags & MockGpiod.LINE_REQ_FLAG_BIAS_PULL_DOWN:
                    self.bias = "pulldown"
                elif flags & MockGpiod.LINE_REQ_FLAG_BIAS_PULL_UP:
                    self.bias = "pullup"
            self.read_pipe, self.write_pipe = os.pipe2(os.O_NONBLOCK)
        else:
            raise GpioException("Unsupported GPIO Type")
        if flags & MockGpiod.LINE_REQ_FLAG_ACTIVE_LOW:
            self.invert = True

    def release(self) -> None:
        if self.read_pipe is not None:
            try:
                os.close(self.read_pipe)
            except Exception:
                pass
        if self.write_pipe is not None:
            try:
                os.close(self.write_pipe)
            except Exception:
                pass
        self.chip.pop_line(self.name)

    def set_value(self, value: int) -> None:
        if self.is_event:
            raise GpioException("Cannot set the value for an input pin")
        self.value = int(not not value)

    def get_value(self) -> int:
        return self.value

    def event_read(self) -> MockLineEvent:
        if self.read_pipe is None:
            raise GpioException
        try:
            data = os.read(self.read_pipe, 64)
        except Exception:
            pass
        else:
            value = int(not not data[-1])
            self.value = value
        return MockLineEvent(self.value)

    def event_get_fd(self) -> int:
        if self.read_pipe is None:
            raise GpioException("Event not configured")
        return self.read_pipe

    def simulate_line_event(self, value: int) -> None:
        if self.write_pipe is None:
            raise GpioException("Event not configured")
        val = bytes([int(not not value)])
        try:
            os.write(self.write_pipe, val)
        except Exception:
            pass

class MockLineEvent:
    RISING_EDGE = 1
    FALLING_EDGE = 2
    def __init__(self, value: int) -> None:
        if value == 1:
            self.type = self.RISING_EDGE
        else:
            self.type = self.FALLING_EDGE
