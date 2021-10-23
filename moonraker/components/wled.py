# PanelDue LCD display support
#
# Copyright (C) 2021  Richard Mitchell <richardjm+moonraker@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

# Component to control the wled neopixel home system from AirCookie
# Github at https://github.com/Aircoookie/WLED
# Wiki at https://kno.wled.ge/

from __future__ import annotations
from re import U
import sys
from enum import Enum
import glob
import logging
import json
import struct
import socket
import asyncio
import time
from tornado.iostream import IOStream
from tornado.httpclient import AsyncHTTPClient
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
        
        self.name = name
        self.color_order = color_order
        
        self.debug: bool = cfg.getboolean('debug', False)

        self.addr: str = cfg.get("address")
        self.port: int = cfg.getint("port", 80)
        self.protocol: str = cfg.get("protocol", 'http')

        self.timeout: int = cfg.getfloat('timeout', 2.)
        initial_preset: int = cfg.getint('initial_preset', -1)
        initial_red: float = cfg.getfloat('initial_red', 0.)
        initial_green: float = cfg.getfloat('initial_green', 0.)
        initial_blue: float = cfg.getfloat('initial_blue', 0.)
        initial_white: float = cfg.getfloat('initial_white', 0.)
        self.chain_count: int = cfg.getint('chain_count', 0)

        self._chain_data = bytearray(self.chain_count * color_order.Elem_Size())
        self._update_color_data(initial_red, initial_green, initial_blue,
                                initial_white)
    
    def _update_color_data(self, red, green, blue, white, index=None):
        red = int(red * 255. + .5)
        blue = int(blue * 255. + .5)
        green = int(green * 255. + .5)
        white = int(white * 255. + .5)
        if self._color_order is ColorOrder.RGB:
            led_data = [red, green, blue]
        else:
            led_data = [red, green, blue, white]

        if index is None:
            self._chain_data[:] = led_data * self._chain_count
        else:
            elem_size = len(led_data)
            self._chain_data[(index-1)*elem_size:index*elem_size] = led_data

class WLED:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        prefix_sections = config.get_prefix_sections("wled")
        logging.info(f"WLED component loading devices: {prefix_sections}")
        color_orders = {
            'RGB': ColorOrder.RGB,
            'RGBW' : ColorOrder.RGBW
        }
        for section in prefix_sections:
            cfg = config[section]
            name: str = cfg.get_name()

            color_order_cfg: str = cfg.get('color_order', 'RGB')
            color_order = color_orders.get(color_order_cfg)
            if color_order is None:
                raise config.error(
                    f"Color order not supported: {color_order_cfg}")

            self.strips[name] = Strip(name, color_order, cfg)
