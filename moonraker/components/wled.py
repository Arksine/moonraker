# WLED neopixel support
#
# Copyright (C) 2021-2022 Richard Mitchell <richardjm+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to control the wled neopixel home system from AirCookie
# Github at https://github.com/Aircoookie/WLED
# Wiki at https://kno.wled.ge/

from __future__ import annotations
import logging
import asyncio
import pprint
from ..utils import async_serial
from ..utils import json_wrapper as jsonw
from ..common import RequestType

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    List,
    Any,
    Optional,
    Dict,
    Coroutine,
    cast
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .http_client import HttpClient

class OnOff:
    on: str = "on"
    off: str = "off"

class Strip:
    _COLORSIZE: int = 4

    def __init__(self, name: str, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.request_mutex = asyncio.Lock()
        self.name = name
        self.initial_preset: int = config.getint("initial_preset", -1)
        self.initial_red: float = config.getfloat("initial_red", 0.5)
        self.initial_green: float = config.getfloat("initial_green", 0.5)
        self.initial_blue: float = config.getfloat("initial_blue", 0.5)
        self.initial_white: float = config.getfloat("initial_white", 0.5)
        self.chain_count: int = config.getint("chain_count", 1)

        # Supports rgbw always
        self._chain_data = bytearray(self.chain_count * self._COLORSIZE)
        self.onoff = OnOff.off
        self.preset = self.initial_preset

    def get_strip_info(self) -> Dict[str, Any]:
        return {
            "strip": self.name,
            "status": self.onoff,
            "chain_count": self.chain_count,
            "preset": self.preset,
            "brightness": self.brightness,
            "intensity": self.intensity,
            "speed": self.speed,
            "error": self.error_state
        }

    async def initialize(self) -> None:
        # Print current state
        ret = await self._send_wled_command({"v": True})
        logging.debug(
            f"Initial WLED State for strip {self.name}:\n"
            f"{pprint.pformat(ret, 2, 100, compact=True)}"
        )
        await self._set_initial_state()

    async def _set_initial_state(self) -> None:
        self.send_full_chain_data = True
        self.onoff = OnOff.on
        self.preset = self.initial_preset
        self.brightness = 255
        self.intensity = -1
        self.speed = -1
        if self.initial_preset >= 0:
            self._update_color_data(
                self.initial_red,
                self.initial_green,
                self.initial_blue,
                self.initial_white,
                None
            )
            await self.wled_on(self.initial_preset)
        else:
            await self.set_wled(
                self.initial_red,
                self.initial_green,
                self.initial_blue,
                self.initial_white,
                None,
                True
            )

    def _update_color_data(
        self,
        red: float,
        green: float,
        blue: float,
        white: float,
        index: Optional[int]
    ) -> None:
        red = int(red * 255. + .5)
        blue = int(blue * 255. + .5)
        green = int(green * 255. + .5)
        white = int(white * 255. + .5)
        led_data = [red, green, blue, white]
        if index is None:
            self._chain_data[:] = led_data * self.chain_count
        else:
            elem_size = len(led_data)
            self._chain_data[(index-1)*elem_size:index*elem_size] = led_data

    async def send_wled_command_impl(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("Children must implement send_wled_command")

    def close(self: Strip) -> Optional[Coroutine]:
        pass

    async def _send_wled_command(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.error_state = None
            return await self.send_wled_command_impl(state)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            msg = f"WLED: Error {e}"
            self.error_state = msg
            logging.exception(msg)
            raise self.server.error(msg)

    async def wled_on(self, preset: int) -> None:
        self.onoff = OnOff.on
        logging.debug(f"WLED: {self.name} on PRESET={preset}")
        if preset < 0:
            # WLED_ON STRIP=strip (no args) - reset to default
            await self._set_initial_state()
        else:
            self.send_full_chain_data = True
            self.preset = preset
            # Without reading the data back from wled we don't know the values
            self.brightness = -1
            self.intensity = -1
            self.speed = -1
            await self._send_wled_command({"on": True, "ps": preset})

    async def wled_off(self) -> None:
        logging.debug(f"WLED: {self.name} off")
        self.onoff = OnOff.off
        # Without this calling SET_WLED for a single pixel after WLED_OFF
        # would send just that pixel
        self.send_full_chain_data = True
        await self._send_wled_command({"on": False})

    async def wled_control(self, brightness: int, intensity: int, speed: int) -> None:
        logging.debug(
            f"WLED: {self.name} control {self.onoff} BRIGHTNESS={brightness} "
            f"INTENSITY={intensity} SPEED={speed} CURRENTPRESET={self.preset}")

        if self.onoff == OnOff.off:
            logging.info("wled control only permitted when strip is on")
            return

        # Even if a preset is not activated sending seg {} information will
        # turn it back on
        control: Dict[str, Any]
        if self.preset != -1:
            control = {"tt": 0, "seg": {}}
        else:
            control = {"tt": 0}

        shouldSend: bool = False
        # Using 0 is not recommended in wled docs
        if brightness > 0:
            if brightness > 255:
                logging.info("BRIGHTNESS should be between 1 and 255")
            else:
                shouldSend = True
                self.brightness = brightness
                control["bri"] = self.brightness
                # Brightness in seg {} - only if a preset is on
                if self.preset != -1:
                    control["seg"]["bri"] = self.brightness

        # Intensity - only if a preset is on
        if intensity > -1 and self.preset != -1:
            if intensity > 255:
                logging.info("INTENSITY should be between 0 and 255")
            else:
                shouldSend = True
                self.intensity = intensity
                control["seg"]["ix"] = self.intensity

        # Speed - only if a preset is on
        if speed > -1 and self.preset != -1:
            if speed > 255:
                logging.info("SPEED should be between 0 and 255")
            else:
                shouldSend = True
                self.speed = speed
                control["seg"]["sx"] = self.speed

        # Control brightness, intensity, and speed for segment
        # This will allow full control for effects such as "Percent"
        if shouldSend:
            await self._send_wled_command(control)

    def _wled_pixel(self, index: int) -> List[int]:
        led_color_data: List[int] = []
        for p in self._chain_data[(index-1)*self._COLORSIZE:
                                  (index)*self._COLORSIZE]:
            led_color_data.append(p)
        return led_color_data

    async def set_wled(
        self,
        red: float,
        green: float,
        blue: float,
        white: float,
        index: Optional[int],
        transmit: bool
    ) -> None:
        logging.debug(
            f"WLED: {self.name} R={red} G={green} B={blue} W={white} "
            f"INDEX={index} TRANSMIT={transmit}")
        self._update_color_data(red, green, blue, white, index)
        if transmit:
            # Clear preset (issues with sending seg{} will revert to preset)
            self.preset = -1

            # If we are coming from a preset without a wled_control
            # we don't know a brightness, this will also ensure
            # behaviour is consistent prior to introduction of wled_control
            if self.brightness == -1:
                self.brightness = 255

            # Base command for setting an led (for all active segments)
            # See https://kno.wled.ge/interfaces/json-api/
            state: Dict[str, Any] = {"on": True,
                                     "tt": 0,
                                     "bri": self.brightness,
                                     "seg": {"bri": self.brightness, "i": []}}
            if index is None:
                # All pixels same color only send range command of first color
                self.send_full_chain_data = False
                state["seg"]["i"] = [0, self.chain_count, self._wled_pixel(1)]
            elif self.send_full_chain_data:
                # Send a full set of color data (e.g. previous preset)
                self.send_full_chain_data = False
                cdata = []
                for i in range(self.chain_count):
                    cdata.append(self._wled_pixel(i+1))
                state["seg"]["i"] = cdata
            else:
                # Only one pixel has changed since last full data sent
                # so send just that one
                state["seg"]["i"] = [index-1, self._wled_pixel(index)]

            # Send wled control command
            await self._send_wled_command(state)

            if self.onoff == OnOff.off:
                # Without a repeated call individual led control doesn't
                # turn the led strip back on or doesn't set brightness
                # correctly from off
                # Confirmed as a bug:
                # https://discord.com/channels/473448917040758787/757254961640898622/934135556370202645
                self.onoff = OnOff.on
                await self._send_wled_command(state)
        else:
            # If not transmitting this time easiest just to send all data when
            # next transmitting
            self.send_full_chain_data = True

class StripHttp(Strip):
    def __init__(self, name: str, config: ConfigHelper):
        super().__init__(name, config)
        # Read the uri information
        addr: str = config.get("address")
        port: int = config.getint("port", 80)
        protocol: str = config.get("protocol", "http")
        self.url = f"{protocol}://{addr}:{port}/json"
        self.timeout: float = config.getfloat("timeout", 2.)
        self.client: HttpClient = self.server.lookup_component("http_client")

    async def send_wled_command_impl(self, state: Dict[str, Any]) -> Dict[str, Any]:
        async with self.request_mutex:
            logging.debug(f"WLED: url:{self.url} json:{state}")
            response = await self.client.post(
                self.url,
                state,
                attempts=3,
                connect_timeout=self.timeout,
                request_timeout=self.timeout
            )
            if not state.get("v", False):
                return {}
            return cast(dict, response.json())

class StripSerial(Strip):
    def __init__(self, name: str, config: ConfigHelper) -> None:
        super().__init__(name, config)
        # Read the serial information (requires wled 0.13 2108250 or greater)
        self.serial = async_serial.AsyncSerialConnection.from_config(config, 115200)
        self.enabled: bool = True
        self.serial_task: Optional[asyncio.Task] = None
        self.pending_response: Optional[asyncio.Future] = None

    async def run_serial(self) -> None:
        last_exc = Exception()
        while self.enabled:
            try:
                self.serial.open()
            except (self.serial.error, OSError) as e:
                if type(last_exc) is not type(e) and last_exc.args != e.args:
                    logging.exception("WLED Serial Open Error")
                    last_exc = e
                await asyncio.sleep(2.)
                continue
            reader = self.serial.reader
            # flush reader
            async for line in reader:
                try:
                    data = jsonw.loads(line.strip())
                except jsonw.JSONDecodeError:
                    logging.debug(f"Received Serial Data: {line!r}")
                else:
                    if (
                        self.pending_response is not None and
                        not self.pending_response.done()
                    ):
                        self.pending_response.set_result(data)
            if self.enabled:
                await asyncio.sleep(2.)
            last_exc = Exception()

    async def initialize(self) -> None:
        eventloop = self.server.get_event_loop()
        self.serial_task = eventloop.create_task(self.run_serial())
        for _ in range(5):
            await asyncio.sleep(.01)
            if self.serial.connected:
                break
        await super().initialize()

    async def send_wled_command_impl(self, state: Dict[str, Any]) -> Dict[str, Any]:
        async with self.request_mutex:
            eventloop = self.server.get_event_loop()
            logging.debug(f"WLED: serial:{self.serial.port} json:{state}")
            if state.get("v", False):
                fut = self.pending_response = eventloop.create_future()
                self.serial.send(jsonw.dumps(state))
                ret = await asyncio.wait_for(fut, 2.0)
                self.pending_response = None
                return ret
            else:
                self.serial.send(jsonw.dumps(state))
                return {}

    async def close(self):
        self.enabled = False
        await self.serial.close()
        if self.serial_task is not None:
            await self.serial_task
        if (
            self.pending_response is not None and
            not self.pending_response.done()
        ):
            self.pending_response.set_exception(self.server.error("Server Shutdown"))

class WLED:
    def __init__(self, config: ConfigHelper) -> None:
        # root_logger = logging.getLogger()
        # root_logger.setLevel(logging.DEBUG)

        self.server = config.get_server()
        prefix_sections = config.get_prefix_sections("wled")
        logging.info(f"WLED component loading strips: {prefix_sections}")

        strip_types = {
            "HTTP": StripHttp,
            "SERIAL": StripSerial
        }
        self.strips: Dict[str, Strip] = {}
        for section in prefix_sections:
            cfg = config[section]
            try:
                name_parts = cfg.get_name().split(maxsplit=1)
                if len(name_parts) != 2:
                    raise cfg.error(
                        f"Invalid Section Name: {cfg.get_name()}")
                name: str = name_parts[1]

                logging.info(f"WLED strip: {name}")

                # Discard old color_order setting, always support 4 color strips
                _ = cfg.get("color_order", "", deprecate=True)

                strip_type: str = cfg.get("type", "http")
                strip_class: Optional[Type[Strip]]
                strip_class = strip_types.get(strip_type.upper())
                if strip_class is None:
                    raise config.error(f"Unsupported Strip Type: {strip_type}")
                self.strips[name] = strip_class(name, cfg)
            except Exception as e:
                # Ensures errors such as "Color not supported" are visible
                msg = f"Failed to initialise strip [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg, exc_info=e)
                continue

        # Register two remote methods for GCODE
        self.server.register_remote_method("set_wled_state", self.set_wled_state)
        self.server.register_remote_method("set_wled", self.set_wled)

        # As moonraker is about making things a web api, let's try it
        # Yes, this is largely a cut-n-paste from power.py
        self.server.register_endpoint(
            "/machine/wled/strips", RequestType.GET, self._handle_list_strips
        )
        self.server.register_endpoint(
            "/machine/wled/status", RequestType.GET, self._handle_batch_wled_request
        )
        self.server.register_endpoint(
            "/machine/wled/on", RequestType.POST, self._handle_batch_wled_request
        )
        self.server.register_endpoint(
            "/machine/wled/off", RequestType.POST, self._handle_batch_wled_request
        )
        self.server.register_endpoint(
            "/machine/wled/toggle", RequestType.POST, self._handle_batch_wled_request
        )
        self.server.register_endpoint(
            "/machine/wled/strip", RequestType.GET | RequestType.POST,
            self._handle_single_wled_request
        )

    async def component_init(self) -> None:
        try:
            logging.debug("Initializing wled")
            event_loop = self.server.get_event_loop()
            cur_time = event_loop.get_loop_time()
            endtime = cur_time + 120.
            query_strips = list(self.strips.values())
            failed_strips: List[Strip] = []
            while cur_time < endtime:
                for strip in query_strips:
                    ret = strip.initialize()
                    if ret is not None:
                        await ret
                    if strip.error_state is not None:
                        failed_strips.append(strip)
                if not failed_strips:
                    logging.debug("All wled strips initialized")
                    return
                query_strips = failed_strips
                failed_strips = []
                await asyncio.sleep(2.)
                cur_time = event_loop.get_loop_time()
            if failed_strips:
                failed_names = [s.name for s in failed_strips]
                self.server.add_warning(
                    "The following wled strips failed init:"
                    f" {failed_names}")
        except Exception as e:
            logging.exception(e)

    async def wled_on(self, strip: str, preset: int) -> None:
        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return
        await self.strips[strip].wled_on(preset)

    # Full control of wled
    # state: True, False, "on", "off"
    # preset: wled preset (int) to use (ignored if state False or "Off")
    async def set_wled_state(
        self,
        strip: str,
        state: Optional[str] = None,
        preset: int = -1,
        brightness: int = -1,
        intensity: int = -1,
        speed: int = -1
    ) -> None:
        status = None

        if isinstance(state, bool):
            status = OnOff.on if state else OnOff.off
        elif isinstance(state, str):
            status = state.lower()
            if status in ["true", "false"]:
                status = OnOff.on if status == "true" else OnOff.off

        if status is None and preset == -1 and brightness == -1 and \
           intensity == -1 and speed == -1:
            logging.info(
                "Invalid state received but no control or preset data passed"
            )
            return

        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return

        # All other arguments are ignored
        if status == OnOff.off:
            await self.strips[strip].wled_off()

        # Turn on if on or a preset is specified
        if status == OnOff.on or preset != -1:
            await self.strips[strip].wled_on(preset)

        # Control
        if brightness != -1 or intensity != -1 or speed != -1:
            await self.strips[strip].wled_control(brightness, intensity, speed)

    # Individual pixel control, for compatibility with SET_LED
    async def set_wled(
        self,
        strip: str,
        red: float = 0.,
        green: float = 0.,
        blue: float = 0.,
        white: float = 0.,
        index: Optional[int] = None,
        transmit: int = 1
    ) -> None:
        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return
        if isinstance(index, int) and index < 0:
            index = None
        await self.strips[strip].set_wled(
            red, green, blue, white, index, True if transmit == 1 else False
        )

    async def _handle_list_strips(self, web_request: WebRequest) -> Dict[str, Any]:
        strips = {name: strip.get_strip_info()
                  for name, strip in self.strips.items()}
        output = {"strips": strips}
        return output

    async def _handle_single_wled_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        strip_name: str = web_request.get_str('strip')
        preset: int = web_request.get_int('preset', -1)
        brightness: int = web_request.get_int('brightness', -1)
        intensity: int = web_request.get_int('intensity', -1)
        speed: int = web_request.get_int('speed', -1)
        req_type = web_request.get_request_type()
        if strip_name not in self.strips:
            raise self.server.error(f"No valid strip named {strip_name}")
        strip = self.strips[strip_name]
        if req_type == RequestType.GET:
            return {strip_name: strip.get_strip_info()}
        elif req_type == RequestType.POST:
            action = web_request.get_str('action').lower()
            if action not in ["on", "off", "toggle", "control"]:
                raise self.server.error(f"Invalid requested action '{action}'")
            result = await self._process_request(
                strip, action, preset, brightness, intensity, speed
            )
        else:
            raise self.server.error("Request not supported", 405)
        return {strip_name: result}

    async def _handle_batch_wled_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        args = web_request.get_args()
        ep = web_request.get_endpoint()
        if not args:
            raise self.server.error("No arguments provided")
        requested_strips = {k: self.strips.get(k, None) for k in args}
        result = {}
        req = ep.split("/")[-1]
        for name, strip in requested_strips.items():
            if strip is not None:
                result[name] = await self._process_request(strip, req, -1,
                                                           -1, -1, -1)
            else:
                result[name] = {"error": "strip_not_found"}
        return result

    async def _process_request(
        self,
        strip: Strip,
        req: str,
        preset: int,
        brightness: int,
        intensity: int,
        speed: int
    ) -> Dict[str, Any]:
        strip_onoff = strip.onoff
        if req == "status":
            return strip.get_strip_info()
        if req == "toggle":
            req = "on" if strip_onoff == OnOff.off else "off"
        if req in ["on", "off", "control"]:
            # Always do something, could be turning off colors, or changing
            # preset, easier not to have to worry
            if req == "on" or req == "control":
                if req == "on":
                    strip_onoff = OnOff.on
                    await strip.wled_on(preset)

                if brightness != -1 or intensity != -1 or speed != -1:
                    await strip.wled_control(brightness, intensity, speed)
            else:
                strip_onoff = OnOff.off
                await strip.wled_off()
            return strip.get_strip_info()
        raise self.server.error(f"Unsupported wled request: {req}")

    async def close(self) -> None:
        for strip in self.strips.values():
            ret = strip.close()
            if ret is not None:
                await ret

def load_component(config: ConfigHelper) -> WLED:
    return WLED(config)
