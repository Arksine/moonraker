from __future__ import annotations

import asyncio
import logging
from typing import Optional, Dict, Any, List

from moonraker.components.klippy_apis import KlippyAPI as APIComp
from moonraker.components.power import PowerDevice, PrinterPower
from moonraker.confighelper import ConfigHelper


class KlipperDevice(PowerDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        if self.off_when_shutdown:
            raise config.error(
                "Option 'off_when_shutdown' in section "
                f"[{config.get_name()}] is unsupported for 'klipper_device'")
        if self.klipper_restart:
            raise config.error(
                "Option 'restart_klipper_when_powered' in section "
                f"[{config.get_name()}] is unsupported for 'klipper_device'")
        for svc in self.bound_services:
            if svc.startswith("klipper"):
                # Klipper devices cannot be bound to an instance of klipper or
                # klipper_mcu
                raise config.error(
                    f"Option 'bound_services' must not contain service '{svc}'"
                    f" for 'klipper_device' [{config.get_name()}]")
        self.is_shutdown: bool = False
        self.update_fut: Optional[asyncio.Future] = None
        self.timer: Optional[float] = config.getfloat(
            'timer', None, above=0.000001)
        self.timer_handle: Optional[asyncio.TimerHandle] = None
        self.object_name = config.get('object_name')
        obj_parts = self.object_name.split()
        self.gc_cmd = f"SET_PIN PIN={obj_parts[-1]} "
        if obj_parts[0] == "gcode_macro":
            self.gc_cmd = obj_parts[-1]
        elif obj_parts[0] != "output_pin":
            raise config.error(
                "Klipper object must be either 'output_pin' or 'gcode_macro' "
                f"for option 'object_name' in section [{config.get_name()}]")

        self.server.register_event_handler(
            "server:klippy_ready", self._handle_ready)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect)

    def _status_update(self, data: Dict[str, Any], _: float) -> None:
        self._set_state_from_data(data)

    def get_device_info(self) -> Dict[str, Any]:
        dev_info = super().get_device_info()
        dev_info['is_shutdown'] = self.is_shutdown
        return dev_info

    async def _handle_ready(self) -> None:
        kapis: APIComp = self.server.lookup_component('klippy_apis')
        sub: Dict[str, Optional[List[str]]] = {self.object_name: None}
        data = await kapis.subscribe_objects(sub, self._status_update, None)
        if not self._validate_data(data):
            self.state == "error"
        else:
            assert data is not None
            self._set_state_from_data(data)
            if (
                self.initial_state is not None and
                self.state in ["on", "off"]
            ):
                new_state = "on" if self.initial_state else "off"
                if new_state != self.state:
                    logging.info(
                        f"Power Device {self.name}: setting initial "
                        f"state to {new_state}"
                    )
                    await self.set_power(new_state)
            self.notify_power_changed()

    async def _handle_disconnect(self) -> None:
        self.is_shutdown = False
        self._set_state("init")
        self._reset_timer()

    def process_klippy_shutdown(self) -> None:
        self.is_shutdown = True
        self._set_state("error")
        self._reset_timer()

    async def refresh_status(self) -> None:
        if self.is_shutdown or self.state in ["on", "off", "init"]:
            return
        kapis: APIComp = self.server.lookup_component('klippy_apis')
        req: Dict[str, Optional[List[str]]] = {self.object_name: None}
        data: Optional[Dict[str, Any]]
        data = await kapis.query_objects(req, None)
        if not self._validate_data(data):
            self.state = "error"
        else:
            assert data is not None
            self._set_state_from_data(data)

    async def set_power(self, state: str) -> None:
        if self.is_shutdown:
            raise self.server.error(
                f"Power Device {self.name}: Cannot set power for device "
                f"when Klipper is shutdown")
        self._reset_timer()
        eventloop = self.server.get_event_loop()
        self.update_fut = eventloop.create_future()
        try:
            kapis: APIComp = self.server.lookup_component('klippy_apis')
            value = "1" if state == "on" else "0"
            await kapis.run_gcode(f"{self.gc_cmd} VALUE={value}")
            await asyncio.wait_for(self.update_fut, 1.)
        except TimeoutError:
            self.state = "error"
            raise self.server.error(
                f"Power device {self.name}: Timeout "
                "waiting for device state update")
        except Exception:
            self.state = "error"
            msg = f"Error Toggling Device Power: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        finally:
            self.update_fut = None
        self._check_timer()

    def _validate_data(self, data: Optional[Dict[str, Any]]) -> bool:
        if data is None:
            logging.info("Error querying klipper object: "
                         f"{self.object_name}")
        elif self.object_name not in data:
            logging.info(
                f"[power]: Invalid Klipper Device {self.object_name}, "
                f"no response returned from subscription.")
        elif 'value' not in data[self.object_name]:
            logging.info(
                f"[power]: Invalid Klipper Device {self.object_name}, "
                f"response does not contain a 'value' parameter")
        else:
            return True
        return False

    def _set_state_from_data(self, data: Dict[str, Any]) -> None:
        if self.object_name not in data:
            return
        value = data[self.object_name].get('value')
        if value is not None:
            state = "on" if value else "off"
            self._set_state(state)
            if self.update_fut is not None:
                self.update_fut.set_result(state)

    def _set_state(self, state: str) -> None:
        in_event = self.update_fut is not None
        last_state = self.state
        self.state = state
        if last_state not in [state, "init"] and not in_event:
            self.notify_power_changed()

    def _check_timer(self) -> None:
        if self.state == "on" and self.timer is not None:
            event_loop = self.server.get_event_loop()
            power: PrinterPower = self.server.lookup_component("power")
            self.timer_handle = event_loop.delay_callback(
                self.timer, power.set_device_power, self.name, "off")

    def _reset_timer(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

    def close(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None
