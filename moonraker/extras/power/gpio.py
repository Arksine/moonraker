from __future__ import annotations

import asyncio
import logging
from typing import Optional

from moonraker.components.power import PowerDevice, PrinterPower
from moonraker.confighelper import ConfigHelper


class GpioDevice(PowerDevice):
    def __init__(self,
                 config: ConfigHelper,
                 initial_val: Optional[int] = None
                 ) -> None:
        super().__init__(config)
        self.timer: Optional[float] = config.getfloat('timer', None)
        if self.timer is not None and self.timer < 0.000001:
            raise config.error(
                f"Option 'timer' in section [{config.get_name()}] must "
                "be above 0.0")
        self.timer_handle: Optional[asyncio.TimerHandle] = None
        if initial_val is None:
            initial_val = int(self.initial_state or 0)
        self.gpio_out = config.getgpioout('pin', initial_value=initial_val)

    async def init_state(self) -> None:
        if self.initial_state is None:
            self.set_power("off")
        else:
            self.set_power("on" if self.initial_state else "off")
            await self.process_bound_services()

    def refresh_status(self) -> None:
        pass

    def set_power(self, state) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None
        try:
            self.gpio_out.write(int(state == "on"))
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state
        self._check_timer()

    def _check_timer(self) -> None:
        if self.state == "on" and self.timer is not None:
            event_loop = self.server.get_event_loop()
            power: PrinterPower = self.server.lookup_component("power")
            self.timer_handle = event_loop.delay_callback(
                self.timer, power.set_device_power, self.name, "off")

    def close(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None
