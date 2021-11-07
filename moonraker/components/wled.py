# WLED neopixel support
#
# Copyright (C) 2021  Richard Mitchell <richardjm+moonraker@gmail.com>
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
from tornado.httpclient import AsyncHTTPClient
from tornado.httpclient import HTTPRequest
from tornado.escape import json_decode

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Type,
    List,
    Any,
    Optional,
    Dict,
    Coroutine,
    Tuple,
    Union,
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import klippy_apis
    APIComp = klippy_apis.KlippyAPI

class ColorOrder(str, Enum):
    RGB: str = "RGB"
    RGBW: str = "RGBW"

    def Elem_Size(self):
        if self is ColorOrder.RGB:
            return 3
        return 4

class OnOff(str, Enum):
    on: str = "on"
    off: str = "off"

class Strip:
    def __init__(self,
                 name: str,
                 color_order: ColorOrder,
                 cfg: ConfigHelper):
        self.server = cfg.get_server()
        self.client = AsyncHTTPClient()
        self.request_mutex = asyncio.Lock()

        self.name = name
        self.color_order = color_order

        # Read the uri information
        addr: str = cfg.get("address")
        port: int = cfg.getint("port", 80)
        protocol: str = cfg.get("protocol", "http")
        self.url = f"{protocol}://{addr}:{port}/json"

        self.timeout: float = cfg.getfloat("timeout", 2.)

        self.initial_preset: int = cfg.getint("initial_preset", -1)
        self.initial_red: float = cfg.getfloat("initial_red", 0.5)
        self.initial_green: float = cfg.getfloat("initial_green", 0.5)
        self.initial_blue: float = cfg.getfloat("initial_blue", 0.5)
        self.initial_white: float = cfg.getfloat("initial_white", 0.5)
        self.chain_count: int = cfg.getint("chain_count", 1)

        self._chain_data = bytearray(self.chain_count * color_order.Elem_Size())

        self.onoff = OnOff.off
        self.preset = self.initial_preset

    def get_strip_info(self) -> Dict[str, Any]:
        return {
            "strip": self.name,
            "status": self.onoff,
            "chain_count": self.chain_count,
            "preset": self.preset,
            "color_order": self.color_order,
            "error": self.error_state,
            "info": self.wled_info,
            "effects": self.wled_effects,
            "palettes": self.wled_palettes
        }

    async def initialize(self):
        self.send_full_chain_data = True
        self.onoff = OnOff.on
        self.preset = self.initial_preset
        if self.initial_preset >= 0:
            self._update_color_data(self.initial_red,
                                    self.initial_green,
                                    self.initial_blue,
                                    self.initial_white)
            await self.wled_on(self.initial_preset)
        else:
            await self.set_wled(self.initial_red,
                                self.initial_green,
                                self.initial_blue,
                                self.initial_white)

    def _update_color_data(self, red, green, blue, white, index=None):
        red = int(red * 255. + .5)
        blue = int(blue * 255. + .5)
        green = int(green * 255. + .5)
        white = int(white * 255. + .5)
        if self.color_order is ColorOrder.RGB:
            led_data = [red, green, blue]
        else:
            led_data = [red, green, blue, white]

        if index is None:
            self._chain_data[:] = led_data * self.chain_count
        else:
            elem_size = len(led_data)
            self._chain_data[(index-1)*elem_size:index*elem_size] = led_data

    async def _send_wled_command(self,
                                 state: Dict[str, Any]):
        async with self.request_mutex:
            try:
                if not hasattr(self, "wled_info"):
                    state['v'] = True

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

                # Generally ignore response unless fetching information
                if not hasattr(self, "wled_info"):
                    data = json_decode(response.body)

                    logging.debug(f"WLED: url:{self.url} data:{data}")

                    self.wled_info = data['info']

                self.error_state = None
            except Exception as e:
                msg = f"WLED: Error {e}"
                self.error_state = msg
                logging.exception(msg)
                raise self.server.error(msg)

    async def wled_on(self, preset):
        self.onoff = OnOff.on
        logging.debug(f"WLED: on {self.name} PRESET={preset}")
        if preset < 0:
            # WLED_ON STRIP=strip (no args) - reset to default
            await self.initialize()
        else:
            self.send_full_chain_data = True
            self.preset = preset
            await self._send_wled_command({"on": True, "ps": preset})

    async def wled_off(self):
        logging.debug(f"WLED: off {self.name}")
        self.onoff = OnOff.off
        await self._send_wled_command({"on": False})

    async def set_wled(self, red, green, blue, white,
                       index=None, transmit=1):
        logging.debug(
            f"WLED: {self.name} R={red} G={green} B={blue} W={white} "
            f"INDEX={index} TRANSMIT={transmit}")
        self._update_color_data(red, green, blue, white, index)
        if transmit:
            elem_size = self.color_order.Elem_Size()
            if self.onoff == OnOff.off:
                # Without a separate On call individual led control doesn"t
                # turn the led strip back on
                self.onoff = OnOff.on
                await self._send_wled_command({"on": True})
            if index is None:
                # All pixels same color only send range command
                elem = []
                for p in self._chain_data[0:elem_size]:
                    elem.append(p)
                self.send_full_chain_data = False
                await self._send_wled_command(
                    {"seg": {"i": [0, self.chain_count-1, elem]}})
            elif self.send_full_chain_data:
                # Send a full set of color data (e.g. previous preset)
                state = {"seg": {"i": []}}
                cdata = []
                for i in range(self.chain_count):
                    idx = i * elem_size
                    elem = []
                    for p in self._chain_data[idx: idx+elem_size]:
                        elem.append(p)
                    cdata.append(elem)
                state["seg"]["i"] = cdata
                self.send_full_chain_data = False
                await self._send_wled_command(state)
            else:
                # Only one pixel has changed so send just that one
                elem = []
                for p in self._chain_data[(index-1)*elem_size:
                                          (index-1)*elem_size+elem_size]:
                    elem.append(p)
                await self._send_wled_command({"seg": {"i": [index, elem]}})
        elif index is not None:
            # If not transmitting this time easiest just to send all data when
            # next transmitting
            self.send_full_chain_data = True

class WLED:
    def __init__(self, config: ConfigHelper) -> None:
        try:
            # root_logger = logging.getLogger()
            # root_logger.setLevel(logging.DEBUG)

            self.server = config.get_server()
            prefix_sections = config.get_prefix_sections("wled")
            logging.info(f"WLED component loading strips: {prefix_sections}")
            color_orders = {
                "RGB": ColorOrder.RGB,
                "RGBW": ColorOrder.RGBW
            }
            self.strips = {}
            for section in prefix_sections:
                cfg = config[section]

                name_parts = cfg.get_name().split(maxsplit=1)
                if len(name_parts) != 2:
                    raise cfg.error(
                        f"Invalid Section Name: {cfg.get_name()}")
                name: str = name_parts[1]

                logging.info(f"WLED strip: {name}")

                color_order_cfg: str = cfg.get("color_order", "RGB")
                color_order = color_orders.get(color_order_cfg)
                if color_order is None:
                    raise config.error(
                        f"Color order not supported: {color_order_cfg}")

                self.strips[name] = Strip(name, color_order, cfg)

            event_loop = self.server.get_event_loop()
            event_loop.register_callback(
                self._initalize_strips, list(self.strips.values()))

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

        except Exception as e:
            logging.exception(e)

    async def _initalize_strips(self,
                                initial_strips: List[Strip]
                                ) -> None:
        try:
            logging.debug("Initializing wled")
            event_loop = self.server.get_event_loop()
            cur_time = event_loop.get_loop_time()
            endtime = cur_time + 120.
            query_strips = initial_strips
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

    # Full control of wled, True, False, "on", "off", preset 42 as int or
    # preset "42" as string
    async def set_wled_state(self,
                   strip: str,
                   state: str
                   ) -> None:
        status = None
        preset = -1
        if isinstance(state, bool):
            status = OnOff.on if state else OnOff.off
        elif isinstance(state, int):
            status = OnOff.on
            preset = state
        elif isinstance(state, str):
            status = state.lower()
            if status in ["true", "false"]:
                status = OnOff.on if status == "true" else OnOff.off
            else:
                try:
                    preset = int(state)
                    status = OnOff.on
                except ValueError:
                    status = None

        if status is None:
            logging.info(f"Invalid state received: {state}")
            return

        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return

        if status == OnOff.off:
            # All other arguments are ignored
            await self.strips[strip].wled_off()
        else:
            await self.strips[strip].wled_on(preset)   

    # Individual pixel control, due to non-transmission this is kept as a
    # separate call
    async def set_wled(self, strip: str,
                       red=0., green=0., blue=0., white=0.,
                       index=None, transmit=1) -> None:
        if strip not in self.strips:
            logging.info(f"Unknown WLED strip: {strip}")
            return
        if index < 0:
            index = None
        await self.strips[strip].set_wled(red, green, blue, white,
                                          index, transmit)

    async def _handle_list_strips(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        strips = {name: strip.get_strip_info()
                  for name, strip in self.strips.items()}
        output = {"strips": strips}
        return output

    async def _handle_single_wled_request(self,
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

    async def _handle_batch_wled_request(self,
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
                result[name] = "strip_not_found"
        return result

    async def _process_request(self,
                               strip: Strip,
                               req: str,
                               preset: int
                               ) -> str:
        strip_onoff = strip.onoff

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
            return strip_onoff

        raise self.server.error(f"Unsupported wled request: {req}")

def load_component_multi(config: ConfigHelper) -> WLED:
    return WLED(config)
