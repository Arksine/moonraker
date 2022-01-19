# WLED neopixel support
#
# Copyright (C) 2021-2022 Richard Mitchell <richardjm+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to control the wled neopixel home system from AirCookie
# Github at https://github.com/Aircoookie/WLED
# Wiki at https://kno.wled.ge/

from __future__ import annotations
from enum import Enum
import logging
import json
import asyncio
import serial_asyncio
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPRequest

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    List,
    Any,
    Optional,
    Dict,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import klippy_apis
    APIComp = klippy_apis.KlippyAPI

class ColorConfig(str, Enum):
    RGB: str = "RGB"
    RGBW: str = "RGBW"

    def Elem_Size(self):
        if self is ColorConfig.RGB:
            return 3
        return 4

class OnOff(str, Enum):
    on: str = "on"
    off: str = "off"

class Strip():
    def __init__(self: Strip,
                 name: str,
                 color_config: ColorConfig,
                 cfg: ConfigHelper):
        self.server = cfg.get_server()
        self.request_mutex = asyncio.Lock()

        self.name = name
        self.color_config = color_config

        self.initial_preset: int = cfg.getint("initial_preset", -1)
        self.initial_red: float = cfg.getfloat("initial_red", 0.5)
        self.initial_green: float = cfg.getfloat("initial_green", 0.5)
        self.initial_blue: float = cfg.getfloat("initial_blue", 0.5)
        self.initial_white: float = cfg.getfloat("initial_white", 0.5)
        self.chain_count: int = cfg.getint("chain_count", 1)

        self._chain_data = bytearray(
            self.chain_count * color_config.Elem_Size())

        self.onoff = OnOff.off
        self.preset = self.initial_preset

    def get_strip_info(self: Strip) -> Dict[str, Any]:
        return {
            "strip": self.name,
            "status": self.onoff,
            "chain_count": self.chain_count,
            "preset": self.preset,
            "color_config": self.color_config,
            "error": self.error_state
        }

    async def initialize(self: Strip) -> None:
        self.send_full_chain_data = True
        self.onoff = OnOff.on
        self.preset = self.initial_preset
        if self.initial_preset >= 0:
            self._update_color_data(self.initial_red,
                                    self.initial_green,
                                    self.initial_blue,
                                    self.initial_white,
                                    None)
            await self.wled_on(self.initial_preset)
        else:
            await self.set_wled(self.initial_red,
                                self.initial_green,
                                self.initial_blue,
                                self.initial_white,
                                None,
                                True)

    def _update_color_data(self: Strip,
                           red: float, green: float, blue: float, white: float,
                           index: Optional[int]) -> None:
        red = int(red * 255. + .5)
        blue = int(blue * 255. + .5)
        green = int(green * 255. + .5)
        white = int(white * 255. + .5)
        if self.color_config.Elem_Size() == 3:
            led_data = [red, green, blue]
        else:
            led_data = [red, green, blue, white]

        if index is None:
            self._chain_data[:] = led_data * self.chain_count
        else:
            elem_size = len(led_data)
            self._chain_data[(index-1)*elem_size:index*elem_size] = led_data

    async def send_wled_command_impl(self: Strip,
                                     state: Dict[str, Any]) -> None:
        pass

    def close(self: Strip):
        pass

    async def _send_wled_command(self: Strip,
                                 state: Dict[str, Any]) -> None:
        try:
            await self.send_wled_command_impl(state)

            self.error_state = None
        except Exception as e:
            msg = f"WLED: Error {e}"
            self.error_state = msg
            logging.exception(msg)
            raise self.server.error(msg)

    async def wled_on(self: Strip, preset: int) -> None:
        self.onoff = OnOff.on
        logging.debug(f"WLED: {self.name} on PRESET={preset}")
        if preset < 0:
            # WLED_ON STRIP=strip (no args) - reset to default
            await self.initialize()
        else:
            self.send_full_chain_data = True
            self.preset = preset
            await self._send_wled_command({"on": True, "ps": preset})

    async def wled_off(self: Strip) -> None:
        logging.debug(f"WLED: {self.name} off")
        self.onoff = OnOff.off
        await self._send_wled_command({"on": False})

    def _wled_pixel(self: Strip, index: int) -> List[int]:
        elem_size = self.color_config.Elem_Size()
        elem: List[int] = []
        for p in self._chain_data[(index-1)*elem_size: (index)*elem_size]:
            elem.append(p)
        return elem

    async def set_wled(self: Strip,
                       red: float, green: float, blue: float, white: float,
                       index: Optional[int], transmit: bool) -> None:
        logging.debug(
            f"WLED: {self.name} R={red} G={green} B={blue} W={white} "
            f"INDEX={index} TRANSMIT={transmit}")
        self._update_color_data(red, green, blue, white, index)
        if transmit:

            if self.onoff == OnOff.off or self.send_full_chain_data:
                # Without a separate On call individual led control doesn"t
                # turn the led strip back on or doesn't set brightness
                # correctly from off or a preset
                self.onoff = OnOff.on
                await self._send_wled_command({"on": True,
                                               "tt": 0,
                                               "bri": 255,
                                               "seg": {"bri": 255}})

            # Base command for setting an led (for all active segments)
            # See https://kno.wled.ge/interfaces/json-api/
            state: Dict[str, Any] = {"tt": 0,
                                     "seg": {"i": []}}
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
        else:
            # If not transmitting this time easiest just to send all data when
            # next transmitting
            self.send_full_chain_data = True

class StripHttp(Strip):
    def __init__(self: StripHttp,
                 name: str,
                 color_config: ColorConfig,
                 cfg: ConfigHelper):
        super().__init__(name, color_config, cfg)

        # Read the uri information
        addr: str = cfg.get("address")
        port: int = cfg.getint("port", 80)
        protocol: str = cfg.get("protocol", "http")
        self.url = f"{protocol}://{addr}:{port}/json"

        self.timeout: float = cfg.getfloat("timeout", 2.)
        self.client = AsyncHTTPClient()

    async def send_wled_command_impl(self: StripHttp,
                                     state: Dict[str, Any]) -> None:
        async with self.request_mutex:
            logging.debug(f"WLED: url:{self.url} json:{state}")

            headers = {"Content-Type": "application/json"}
            request = HTTPRequest(url=self.url,
                                  method="POST",
                                  headers=headers,
                                  body=json.dumps(state),
                                  connect_timeout=self.timeout,
                                  request_timeout=self.timeout)
            response = await self.client.fetch(request)

            logging.debug(
                f"WLED: url:{self.url} status:{response.code} "
                f"response:{response.body}")

class StripSerial(Strip):
    def __init__(self: StripSerial,
                 name: str,
                 color_config: ColorConfig,
                 cfg: ConfigHelper):
        super().__init__(name, color_config, cfg)

        # Read the serial information (requires wled 0.13 2108250 or greater)
        self.serialport: str = cfg.get("serial")
        self.baud: int = cfg.getint("baud", 115200, above=49)

    async def send_wled_command_impl(self: StripSerial,
                                     state: Dict[str, Any]) -> None:
        async with self.request_mutex:
            if not hasattr(self, 'ser'):
                _, self.ser = await serial_asyncio.open_serial_connection(
                    url=self.serialport, baudrate=self.baud)

            logging.debug(f"WLED: serial:{self.serialport} json:{state}")

            self.ser.write(json.dumps(state).encode())

    def close(self: StripSerial):
        if hasattr(self, 'ser'):
            self.ser.close()
            logging.info(f"WLED: Closing serial {self.serialport}")

class WLED:
    def __init__(self: WLED, config: ConfigHelper) -> None:
        # root_logger = logging.getLogger()
        # root_logger.setLevel(logging.DEBUG)

        self.server = config.get_server()
        prefix_sections = config.get_prefix_sections("wled")
        logging.info(f"WLED component loading strips: {prefix_sections}")
        # Allow unexpected color config strings to match klipper
        color_configs = {
            "GRB": ColorConfig.RGB,
            "RGB": ColorConfig.RGB,
            "BRG": ColorConfig.RGB,
            "GRBW": ColorConfig.RGBW,
            "RGBW": ColorConfig.RGBW
        }
        strip_types = {
            "HTTP": StripHttp,
            "SERIAL": StripSerial
        }
        self.strips = {}
        for section in prefix_sections:
            cfg = config[section]

            try:
                name_parts = cfg.get_name().split(maxsplit=1)
                if len(name_parts) != 2:
                    raise cfg.error(
                        f"Invalid Section Name: {cfg.get_name()}")
                name: str = name_parts[1]

                logging.info(f"WLED strip: {name}")

                # Public setting name change but support old name internally
                color_cfg: str = cfg.get("color_order", "RGB")
                color_cfg = cfg.get("color_config", color_cfg)
                color_config = color_configs.get(color_cfg.upper())
                if color_config is None:
                    raise config.error(
                        f"Color not supported: {color_cfg}")

                strip_type: str = cfg.get("type", "http")
                strip_class: Optional[Type[Strip]]
                strip_class = strip_types.get(strip_type.upper())
                if strip_class is None:
                    raise config.error(f"Unsupported Strip Type: {strip_type}")

                self.strips[name] = strip_class(name, color_config, cfg)

            except Exception as e:
                # Ensures errors such as "Color not supported" are visible
                msg = f"Failed to initialise strip [{cfg.get_name()}]\n{e}"
                self.server.add_warning(msg)
                continue

        # Register two remote methods for GCODE
        self.server.register_remote_method(
            "set_wled_state", self.set_wled_state)
        self.server.register_remote_method(
            "set_wled", self.set_wled)

        # As moonraker is about making things a web api, let's try it
        # Yes, this is largely a cut-n-paste from power.py
        self.server.register_endpoint(
            "/machine/wled/strips", ["GET"],
            self._handle_list_strips)
        self.server.register_endpoint(
            "/machine/wled/status", ["GET"],
            self._handle_batch_wled_request)
        self.server.register_endpoint(
            "/machine/wled/on", ["POST"],
            self._handle_batch_wled_request)
        self.server.register_endpoint(
            "/machine/wled/off", ["POST"],
            self._handle_batch_wled_request)
        self.server.register_endpoint(
            "/machine/wled/strip", ["GET", "POST"],
            self._handle_single_wled_request)

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

    async def wled_on(self: WLED, strip: str, preset: int) -> None:
        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return
        await self.strips[strip].wled_on(preset)

    # Full control of wled
    # state: True, False, "on", "off"
    # preset: wled preset (int) to use (ignored if state False or "Off")
    async def set_wled_state(self: WLED, strip: str, state: str,
                             preset: int = -1) -> None:
        status = None

        if isinstance(state, bool):
            status = OnOff.on if state else OnOff.off
        elif isinstance(state, str):
            status = state.lower()
            if status in ["true", "false"]:
                status = OnOff.on if status == "true" else OnOff.off

        if status is None and preset == -1:
            logging.info(
                f"Invalid state received but no preset passed: {state}")
            return

        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return

        if status == OnOff.off:
            # All other arguments are ignored
            await self.strips[strip].wled_off()
        else:
            await self.strips[strip].wled_on(preset)

    # Individual pixel control, for compatibility with SET_LED
    async def set_wled(self: WLED,
                       strip: str,
                       red: float = 0.,
                       green: float = 0.,
                       blue: float = 0.,
                       white: float = 0.,
                       index: Optional[int] = None,
                       transmit: int = 1) -> None:
        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return
        if isinstance(index, int) and index < 0:
            index = None
        await self.strips[strip].set_wled(red, green, blue, white,
                                          index,
                                          True if transmit == 1 else False)

    async def _handle_list_strips(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        strips = {name: strip.get_strip_info()
                  for name, strip in self.strips.items()}
        output = {"strips": strips}
        return output

    async def _handle_single_wled_request(self: WLED,
                                          web_request: WebRequest
                                          ) -> Dict[str, Any]:
        strip_name: str = web_request.get_str('strip')
        preset: int = web_request.get_int('preset', -1)

        req_action = web_request.get_action()
        if strip_name not in self.strips:
            raise self.server.error(f"No valid strip named {strip_name}")
        strip = self.strips[strip_name]
        if req_action == 'GET':
            return {strip_name: strip.get_strip_info()}
        elif req_action == "POST":
            action = web_request.get_str('action').lower()
            if action not in ["on", "off", "toggle"]:
                raise self.server.error(
                    f"Invalid requested action '{action}'")
            result = await self._process_request(strip, action, preset)
        return {strip_name: result}

    async def _handle_batch_wled_request(self: WLED,
                                         web_request: WebRequest
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
                result[name] = await self._process_request(strip, req, -1)
            else:
                result[name] = {"error": "strip_not_found"}
        return result

    async def _process_request(self: WLED,
                               strip: Strip,
                               req: str,
                               preset: int
                               ) -> Dict[str, Any]:
        strip_onoff = strip.onoff

        if req == "status":
            return strip.get_strip_info()
        if req == "toggle":
            req = "on" if strip_onoff == OnOff.off else "off"
        if req in ["on", "off"]:
            # Always do something, could be turning off colors, or changing
            # preset, easier not to have to worry
            if req == "on":
                strip_onoff = OnOff.on
                await strip.wled_on(preset)
            else:
                strip_onoff = OnOff.off
                await strip.wled_off()
            return strip.get_strip_info()

        raise self.server.error(f"Unsupported wled request: {req}")

    def close(self) -> None:
        for strip in self.strips.values():
            strip.close()

def load_component(config: ConfigHelper) -> WLED:
    return WLED(config)
