# Raspberry Pi Power Control
#
# Copyright (C) 2020 Jordan Ruthe <jordanruthe@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations

import importlib
import inspect
import logging
import asyncio

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    List,
    Any,
    Optional,
    Dict,
    Coroutine,
    Union,
    cast
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .machine import Machine
    from .klippy_apis import KlippyAPI as APIComp
    from .http_client import HttpClient
    from ..klippy_connection import KlippyConnection

class PrinterPower:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.devices: Dict[str, PowerDevice] = {}
        prefix_sections = config.get_prefix_sections("power")
        logging.info(f"Power component loading devices: {prefix_sections}")
        dev_types = {}
        extras_power = "moonraker.extras.power"
        module = importlib.import_module(extras_power)
        for submodule_name in module.__all__:
            submodule = importlib.import_module("." + submodule_name, extras_power)
            subclasses = inspect.getmembers(submodule, inspect.isclass)
            for subclass in subclasses:
                if PowerDevice.__subclasscheck__(subclass[1]):
                    dev_types[submodule_name] = subclass[1]

        # Load extras components

        for section in prefix_sections:
            cfg = config[section]
            dev_type: str = cfg.get("type")
            dev_class: Optional[Type[PowerDevice]]
            dev_class = dev_types.get(dev_type)
            if dev_class is None:
                raise config.error(f"Unsupported Device Type: {dev_type}")
            try:
                dev = dev_class(cfg)
            except Exception as e:
                msg = f"Failed to load power device [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg)
                continue
            self.devices[dev.get_name()] = dev

        self.server.register_endpoint(
            "/machine/device_power/devices", ['GET'],
            self._handle_list_devices)
        self.server.register_endpoint(
            "/machine/device_power/status", ['GET'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/on", ['POST'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/off", ['POST'],
            self._handle_batch_power_request)
        self.server.register_endpoint(
            "/machine/device_power/device", ['GET', 'POST'],
            self._handle_single_power_request)
        self.server.register_remote_method(
            "set_device_power", self.set_device_power)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_klippy_shutdown)
        self.server.register_event_handler(
            "job_queue:job_queue_changed", self._handle_job_queued)
        self.server.register_notification("power:power_changed")

    async def component_init(self) -> None:
        for dev in self.devices.values():
            if not dev.initialize():
                self.server.add_warning(
                    f"Power device '{dev.get_name()}' failed to initialize"
                )

    def _handle_klippy_shutdown(self) -> None:
        for dev in self.devices.values():
            dev.process_klippy_shutdown()

    async def _handle_job_queued(self, queue_info: Dict[str, Any]) -> None:
        if queue_info["action"] != "jobs_added":
            return
        for name, dev in self.devices.items():
            if dev.should_turn_on_when_queued():
                queue: List[Dict[str, Any]]
                queue = queue_info.get("updated_queue", [])
                fname = "unknown"
                if len(queue):
                    fname = queue[0].get("filename", "unknown")
                logging.info(
                    f"Power Device {name}: Job '{fname}' queued, powering on"
                )
                await dev.process_request("on")

    async def _handle_list_devices(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        dev_list = [d.get_device_info() for d in self.devices.values()]
        output = {"devices": dev_list}
        return output

    async def _handle_single_power_request(self,
                                           web_request: WebRequest
                                           ) -> Dict[str, Any]:
        dev_name: str = web_request.get_str('device')
        req_action = web_request.get_action()
        if dev_name not in self.devices:
            raise self.server.error(f"No valid device named {dev_name}")
        dev = self.devices[dev_name]
        if req_action == 'GET':
            action = "status"
        elif req_action == "POST":
            action = web_request.get_str('action').lower()
            if action not in ["on", "off", "toggle"]:
                raise self.server.error(
                    f"Invalid requested action '{action}'")
        result = await dev.process_request(action)
        return {dev_name: result}

    async def _handle_batch_power_request(self,
                                          web_request: WebRequest
                                          ) -> Dict[str, Any]:
        args = web_request.get_args()
        ep = web_request.get_endpoint()
        if not args:
            raise self.server.error("No arguments provided")
        requested_devs = {k: self.devices.get(k, None) for k in args}
        result = {}
        req = ep.split("/")[-1]
        for name, device in requested_devs.items():
            if device is not None:
                result[name] = await device.process_request(req)
            else:
                result[name] = "device_not_found"
        return result

    def set_device_power(
        self, device: str, state: Union[bool, str], force: bool = False
    ) -> None:
        request: str = ""
        if isinstance(state, bool):
            request = "on" if state else "off"
        elif isinstance(state, str):
            request = state.lower()
            if request in ["true", "false"]:
                request = "on" if request == "true" else "off"
        if request not in ["on", "off", "toggle"]:
            logging.info(f"Invalid state received: {state}")
            return
        if device not in self.devices:
            logging.info(f"No device found: {device}")
            return
        event_loop = self.server.get_event_loop()
        event_loop.register_callback(
            self.devices[device].process_request, request, force=force
        )

    async def add_device(self, name: str, device: PowerDevice) -> None:
        if name in self.devices:
            raise self.server.error(
                f"Device [{name}] already configured")
        success = device.initialize()
        if asyncio.iscoroutine(success):
            success = await success  # type: ignore
        if not success:
            self.server.add_warning(
                f"Power device '{device.get_name()}' failed to initialize"
            )
            return
        self.devices[name] = device

    async def close(self) -> None:
        for device in self.devices.values():
            ret = device.close()
            if ret is not None:
                await ret


class PowerDevice:
    def __init__(self, config: ConfigHelper) -> None:
        name_parts = config.get_name().split(maxsplit=1)
        if len(name_parts) != 2:
            raise config.error(f"Invalid Section Name: {config.get_name()}")
        self.server = config.get_server()
        self.name = name_parts[1]
        self.type: str = config.get('type')
        self.state: str = "init"
        self.request_lock = asyncio.Lock()
        self.init_task: Optional[asyncio.Task] = None
        self.locked_while_printing = config.getboolean(
            'locked_while_printing', False)
        self.off_when_shutdown = config.getboolean('off_when_shutdown', False)
        self.off_when_shutdown_delay = 0.
        if self.off_when_shutdown:
            self.off_when_shutdown_delay = config.getfloat(
                'off_when_shutdown_delay', 0., minval=0.)
        self.shutdown_timer_handle: Optional[asyncio.TimerHandle] = None
        self.restart_delay = 1.
        self.klipper_restart = config.getboolean(
            'restart_klipper_when_powered', False)
        if self.klipper_restart:
            self.restart_delay = config.getfloat(
                'restart_delay', 1., above=.000001
            )
            self.server.register_event_handler(
                "server:klippy_started", self._schedule_firmware_restart
            )
        self.bound_services: List[str] = []
        bound_services: List[str] = config.getlist('bound_services', [])
        if config.has_option('bound_service'):
            # The `bound_service` option is deprecated, however this minimal
            # change does not require a warning as it can be reliably resolved
            bound_services.append(config.get('bound_service'))
        for svc in bound_services:
            if svc.endswith(".service"):
                svc = svc.rsplit(".", 1)[0]
            if svc in self.bound_services:
                continue
            self.bound_services.append(svc)
        self.need_scheduled_restart = False
        self.on_when_queued = config.getboolean('on_when_job_queued', False)
        if config.has_option('on_when_upload_queued'):
            self.on_when_queued = config.getboolean('on_when_upload_queued',
                                                    False, deprecate=True)
        self.initial_state: Optional[bool] = config.getboolean(
            'initial_state', None
        )

    def _schedule_firmware_restart(self, state: str = "") -> None:
        if not self.need_scheduled_restart:
            return
        self.need_scheduled_restart = False
        if state == "ready":
            logging.info(
                f"Power Device {self.name}: Klipper reports 'ready', "
                "aborting FIRMWARE_RESTART"
            )
            return
        logging.info(
            f"Power Device {self.name}: Sending FIRMWARE_RESTART command "
            "to Klippy"
        )
        event_loop = self.server.get_event_loop()
        kapis: APIComp = self.server.lookup_component("klippy_apis")
        event_loop.delay_callback(
            self.restart_delay, kapis.do_restart,
            "FIRMWARE_RESTART", True
        )

    def get_name(self) -> str:
        return self.name

    def get_device_info(self) -> Dict[str, Any]:
        return {
            'device': self.name,
            'status': self.state,
            'locked_while_printing': self.locked_while_printing,
            'type': self.type
        }

    def notify_power_changed(self) -> None:
        dev_info = self.get_device_info()
        self.server.send_event("power:power_changed", dev_info)

    async def process_power_changed(self) -> None:
        self.notify_power_changed()
        if self.bound_services:
            await self.process_bound_services()
        if self.state == "on" and self.klipper_restart:
            self.need_scheduled_restart = True
            klippy_state = self.server.get_klippy_state()
            if klippy_state in ["disconnected", "startup"]:
                # If klippy is currently disconnected or hasn't proceeded past
                # the startup state, schedule the restart in the
                # "klippy_started" event callback.
                return
            self._schedule_firmware_restart(klippy_state)

    async def process_bound_services(self) -> None:
        if not self.bound_services:
            return
        machine_cmp: Machine = self.server.lookup_component("machine")
        action = "start" if self.state == "on" else "stop"
        for svc in self.bound_services:
            logging.info(
                f"Power Device {self.name}: Performing {action} action "
                f"on bound service {svc}"
            )
            await machine_cmp.do_service_action(action, svc)

    def process_klippy_shutdown(self) -> None:
        if not self.off_when_shutdown:
            return
        if self.off_when_shutdown_delay == 0.:
            self._power_off_on_shutdown()
        else:
            if self.shutdown_timer_handle is not None:
                self.shutdown_timer_handle.cancel()
                self.shutdown_timer_handle = None
            event_loop = self.server.get_event_loop()
            self.shutdown_timer_handle = event_loop.delay_callback(
                self.off_when_shutdown_delay, self._power_off_on_shutdown)

    def _power_off_on_shutdown(self) -> None:
        if self.server.get_klippy_state() != "shutdown":
            return
        logging.info(
            f"Powering off device '{self.name}' due to klippy shutdown")
        power: PrinterPower = self.server.lookup_component("power")
        power.set_device_power(self.name, "off")

    def should_turn_on_when_queued(self) -> bool:
        return self.on_when_queued and self.state == "off"

    def _setup_bound_services(self) -> None:
        if not self.bound_services:
            return
        machine_cmp: Machine = self.server.lookup_component("machine")
        sys_info = machine_cmp.get_system_info()
        avail_svcs: List[str] = sys_info.get('available_services', [])
        for svc in self.bound_services:
            if machine_cmp.unit_name == svc:
                raise self.server.error(
                    f"Power Device {self.name}: Cannot bind to Moonraker "
                    f"service {svc}."
                )
            if svc not in avail_svcs:
                raise self.server.error(
                    f"Bound Service {svc} is not available"
                )
        svcs = ", ".join(self.bound_services)
        logging.info(f"Power Device '{self.name}' bound to services: {svcs}")

    def init_state(self) -> Optional[Coroutine]:
        return None

    def initialize(self) -> bool:
        self._setup_bound_services()
        ret = self.init_state()
        if ret is not None:
            eventloop = self.server.get_event_loop()
            self.init_task = eventloop.create_task(ret)
        return self.state != "error"

    async def process_request(self, req: str, force: bool = False) -> str:
        if self.state == "init" and self.request_lock.locked():
            # return immediately if the device is initializing,
            # otherwise its possible for this to block indefinitely
            # while the device holds the lock
            return self.state
        async with self.request_lock:
            base_state: str = self.state
            ret = self.refresh_status()
            if ret is not None:
                await ret
            cur_state: str = self.state
            if req == "toggle":
                req = "on" if cur_state == "off" else "off"
            if req in ["on", "off"]:
                if req == cur_state:
                    # device is already in requested state, do nothing
                    if base_state != cur_state:
                        self.notify_power_changed()
                    return cur_state
                if not force:
                    kconn: KlippyConnection
                    kconn = self.server.lookup_component("klippy_connection")
                    if self.locked_while_printing and kconn.is_printing():
                        raise self.server.error(
                            f"Unable to change power for {self.name} "
                            "while printing")
                ret = self.set_power(req)
                if ret is not None:
                    await ret
                cur_state = self.state
                await self.process_power_changed()
            elif req != "status":
                raise self.server.error(f"Unsupported power request: {req}")
            elif base_state != cur_state:
                self.notify_power_changed()
            return cur_state

    def refresh_status(self) -> Optional[Coroutine]:
        raise NotImplementedError

    def set_power(self, state: str) -> Optional[Coroutine]:
        raise NotImplementedError

    def close(self) -> Optional[Coroutine]:
        if self.init_task is not None:
            self.init_task.cancel()
            self.init_task = None
        return None

class HTTPDevice(PowerDevice):
    def __init__(
        self,
        config: ConfigHelper,
        default_port: int = -1,
        default_user: str = "",
        default_password: str = "",
        default_protocol: str = "http",
        is_generic: bool = False
    ) -> None:
        super().__init__(config)
        self.client: HttpClient = self.server.lookup_component("http_client")
        if is_generic:
            return
        self.addr: str = config.get("address")
        self.port = config.getint("port", default_port)
        self.user = config.load_template("user", default_user).render()
        self.password = config.load_template(
            "password", default_password).render()
        self.protocol = config.get("protocol", default_protocol)

    async def init_state(self) -> None:
        async with self.request_lock:
            last_err: Exception = Exception()
            while True:
                try:
                    state = await self._send_status_request()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    if type(last_err) is not type(e) or last_err.args != e.args:
                        logging.exception(f"Device Init Error: {self.name}")
                        last_err = e
                    await asyncio.sleep(5.)
                    continue
                else:
                    self.init_task = None
                    self.state = state
                    if (
                        self.initial_state is not None and
                        state in ["on", "off"]
                    ):
                        new_state = "on" if self.initial_state else "off"
                        if new_state != state:
                            logging.info(
                                f"Power Device {self.name}: setting initial "
                                f"state to {new_state}"
                            )
                            await self.set_power(new_state)
                        await self.process_bound_services()
                    self.notify_power_changed()
                    return

    async def _send_http_command(
        self, url: str, command: str, retries: int = 3
    ) -> Dict[str, Any]:
        response = await self.client.get(
            url, request_timeout=20., attempts=retries,
            retry_pause_time=1., enable_cache=False)
        response.raise_for_status(
            f"Error sending '{self.type}' command: {command}")
        data = cast(dict, response.json())
        return data

    async def _send_power_request(self, state: str) -> str:
        raise NotImplementedError(
            "_send_power_request must be implemented by children")

    async def _send_status_request(self) -> str:
        raise NotImplementedError(
            "_send_status_request must be implemented by children")

    async def refresh_status(self) -> None:
        try:
            state = await self._send_status_request()
        except Exception:
            self.state = "error"
            msg = f"Error Refeshing Device Status: {self.name}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state

    async def set_power(self, state):
        try:
            state = await self._send_power_request(state)
        except Exception:
            self.state = "error"
            msg = f"Error Setting Device Status: {self.name} to {state}"
            logging.exception(msg)
            raise self.server.error(msg) from None
        self.state = state


# The power component has multiple configuration sections
def load_component(config: ConfigHelper) -> PrinterPower:
    return PrinterPower(config)
