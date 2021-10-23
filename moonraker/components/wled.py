# WLED neopixel support
#
# Copyright (C) 2021  Richard Mitchell <richardjm+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to control the wled neopixel home system from AirCookie
# Github at https://github.com/Aircoookie/WLED
# Wiki at https://kno.wled.ge/

from __future__ import annotations
from re import U
from enum import Enum
import logging
import json
import asyncio
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
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
    from . import klippy_apis
    APIComp = klippy_apis.KlippyAPI

class ColorOrder(Enum):
    RGB = 1,
    RGBW = 2

    def Elem_Size(self):
        if self is ColorOrder.RGB:
            return 3
        return 4

class Strip:
    def __init__(self,
                name: str,
                color_order: ColorOrder,
                cfg: ConfigHelper):
        self.client = AsyncHTTPClient()
        self.request_mutex = asyncio.Lock()

        self.name = name
        self.color_order = color_order

        self.debug: bool = cfg.getboolean('debug', False)

        # Read the uri information
        addr: str = cfg.get("address")
        port: int = cfg.getint("port", 80)
        protocol: str = cfg.get("protocol", 'http')
        self.url = f"{protocol}://{addr}:{port}/json"

        self.timeout: int = cfg.getfloat('timeout', 2.)

        self.initial_preset: int = cfg.getint('initial_preset', -1)
        self.initial_red: float = cfg.getfloat('initial_red', 0.)
        self.initial_green: float = cfg.getfloat('initial_green', 0.)
        self.initial_blue: float = cfg.getfloat('initial_blue', 0.)
        self.initial_white: float = cfg.getfloat('initial_white', 0.)
        self.chain_count: int = cfg.getint('chain_count', 0)

        self._chain_data = bytearray(self.chain_count * color_order.Elem_Size())

        self.error_state = None

    async def initialize(self):
        self.send_on_color_data = True
        self.send_full_chain_data = True
        self._update_color_data(self.initial_red,
                                self.initial_green,
                                self.initial_blue,
                                self.initial_white)
        if self.initial_preset >= 0:
            await self.wled_on(self, self.initial_preset,
                               self.initial_red,
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
            self._chain_data[:] = led_data * self._chain_count
        else:
            elem_size = len(led_data)
            self._chain_data[(index-1)*elem_size:index*elem_size] = led_data

    async def _send_wled_command(self,
                                 state: str):
        async with self.request_mutex:
            try:
                if self.debug:
                    logging.debug(f'WLED: url:{self.url} json:{state}')

                request = HTTPRequest(url = self.url,
                            method = 'POST',
                            headers = { 'Content-Type': 'application/json'},
                            body = json.dumps(state),
                            connect_timeout = self.timeout,
                            request_timeout = self.timeout)
                response = await self.client.fetch(request)

                if self.debug:
                    logging.debug(
                        f'WLED: url:{self.url} status:{response.status_code}')

                self.error_state = None
            except Exception as e:
                msg = f"WLED: Error {e}"
                self.error_state = msg
                logging.exception(msg)
                raise self.server.error(msg)

    async def wled_on(self, preset = -1, red = 0., green = 0.,
                      blue = 0., white = 0.):
        self.send_full_chain_data = True
        desired_preset = preset|self.initial_preset
        if desired_preset >= 0:
            state = {'on': True, 'ps': desired_preset}
            await self._send_wled_command(state)
        else:
            await self.set_led(red, green, blue, white)

    async def wled_off(self):
        self.send_on_color_data = True
        await self._send_wled_command({'on': False })

    async def set_led(self, red = 0., green = 0., blue= 0., white = 0.,
                      index = None, transmit = 1):
        if self.debug:
            logging.debug(
                f'WLED: {self.name} R={red} G={green} B={blue} W={white} '
                 'INDEX={index} TRANSMIT={transmit}')
        self._update_color_data(red, green, blue, white, index)
        if transmit:
            elem_size = len(self.color_order)
            if self.send_on_color_data:
                # Without a separate On call individual led control doesn't
                # turn the led strip back on
                self.send_on_color_data = False
                await self._send_wled_command({'on': True})
            if index is None:
                # All pixels same color only send range command
                elem = []
                for p in self.color_data[0:elem_size]:
                    elem.append(p)
                self.send_full_chain_data = False
                await self._send_wled_command(
                    {'seg':{'i':[0,self.chain_count-1, elem]}})
            elif self.send_full_chain_data:
                # Send a full set of color data (e.g. previous preset)
                state = {'seg':{'i':[]}}
                cdata = []
                for i in range(self.chain_count):
                    idx = i * elem_size
                    elem = []
                    for p in self._chain_data[idx:idx+elem_size]:
                        elem.append(p)
                    cdata.append(elem)
                state['seg']['i'] = cdata
                self.send_full_chain_data = False
                await self._send_wled_command(state)
            else:
                # Only one pixel has changed so send just that one
                elem = []
                for p in self._chain_data[(index - 1) * elem_size
                                         :(index - 1) * elem_size + elem_size]:
                    elem.append(p)
                await self._send_wled_command({'seg':{'i':[index, elem]}})
        elif index is not None:
            self.send_full_chain_data = True

class WLED:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        prefix_sections = config.get_prefix_sections("wled")
        logging.info(f"WLED component loading devices: {prefix_sections}")
        color_orders = {
            'RGB': ColorOrder.RGB,
            'RGBW' : ColorOrder.RGBW
        }
        self.strips = []
        for section in prefix_sections:
            cfg = config[section]
            name: str = cfg.get_name()

            color_order_cfg: str = cfg.get('color_order', 'RGB')
            color_order = color_orders.get(color_order_cfg)
            if color_order is None:
                raise config.error(
                    f"Color order not supported: {color_order_cfg}")

            self.strips[name] = Strip(name, color_order, cfg)

        event_loop = self.server.get_event_loop()
        event_loop.register_callback(
            self._initalize_strips, list(self.strips.values()))

    async def _initalize_strips(self,
                                initial_strips: List[Strip]
                                 ) -> None:
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
                if not strip.error_state is None:
                    failed_strips.append(strip)
            if not failed_strips:
                logging.debug("All wled strips initialized")
                return
            query_strips = failed_devs
            failed_devs = []
            await asyncio.sleep(2.)
            cur_time = event_loop.get_loop_time()
        if failed_strips:
            failed_names = [s.name for s in failed_strips]
            self.server.add_warning(
                "The following wled strips failed init:"
                f" {failed_names}")
