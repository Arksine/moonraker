from __future__ import annotations

import logging
import time

from moonraker.confighelper import ConfigHelper
from .gpio import GpioDevice


class RFDevice(GpioDevice):

    # Protocol definition
    # [1, 3] means HIGH is set for 1x pulse_len and LOW for 3x pulse_len
    ZERO_BIT = [1, 3]  # zero bit
    ONE_BIT = [3, 1]  # one bit
    SYNC_BIT = [1, 31]  # sync between
    PULSE_LEN = 0.00035  # length of a single pulse
    RETRIES = 10  # send the code this many times

    def __init__(self, config: ConfigHelper):
        super().__init__(config, initial_val=0)
        self.on = config.get("on_code").zfill(24)
        self.off = config.get("off_code").zfill(24)

    def _transmit_digit(self, waveform) -> None:
        self.gpio_out.write(1)
        time.sleep(waveform[0]*RFDevice.PULSE_LEN)
        self.gpio_out.write(0)
        time.sleep(waveform[1]*RFDevice.PULSE_LEN)

    def _transmit_code(self, code) -> None:
        for _ in range(RFDevice.RETRIES):
            for i in code:
                if i == "1":
                    self._transmit_digit(RFDevice.ONE_BIT)
                elif i == "0":
                    self._transmit_digit(RFDevice.ZERO_BIT)
            self._transmit_digit(RFDevice.SYNC_BIT)

    def set_power(self, state) -> None:
        try:
            if state == "on":
                code = self.on
            else:
                code = self.off
            self._transmit_code(code)
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state
        self._check_timer()
