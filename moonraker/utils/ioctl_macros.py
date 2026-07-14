# Methods to create IOCTL requests
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import ctypes
from typing import Union, Type, TYPE_CHECKING

"""
This module contains a Python port of the macros available in
"/include/uapi/asm-generic/ioctl.h" from the linux kernel.
"""

if TYPE_CHECKING:
    IOCParamSize = Union[int, str, Type[ctypes._CData]]

_IOC_NRBITS = 8
_IOC_TYPEBITS = 8

# NOTE: The following could be platform specific.
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRMASK = (1 << _IOC_NRBITS) - 1
_IOC_TYPEMASK = (1 << _IOC_TYPEBITS) - 1
_IOC_SIZEMASK = (1 << _IOC_SIZEBITS) - 1
_IOC_DIRMASK = (1 << _IOC_DIRBITS) - 1

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

# The constants below may also be platform specific
IOC_NONE = 0
IOC_WRITE = 1
IOC_READ = 2

def _check_value(val: int, name: str, maximum: int):
    if val > maximum:
        raise ValueError(f"Value '{val}' for '{name}' exceeds max of {maximum}")

def _IOC_TYPECHECK(param_size: IOCParamSize) -> int:
    if isinstance(param_size, int):
        return param_size
    elif isinstance(param_size, bytearray):
        return len(param_size)
    elif isinstance(param_size, str):
        ctcls = getattr(ctypes, param_size)
        return ctypes.sizeof(ctcls)
    return ctypes.sizeof(param_size)

def IOC(direction: int, cmd_type: int, cmd_number: int, param_size: int) -> int:
    _check_value(direction, "direction", _IOC_DIRMASK)
    _check_value(cmd_type, "cmd_type", _IOC_TYPEMASK)
    _check_value(cmd_number, "cmd_number", _IOC_NRMASK)
    _check_value(param_size, "ioc_size", _IOC_SIZEMASK)
    return (
        (direction << _IOC_DIRSHIFT) |
        (param_size << _IOC_SIZESHIFT) |
        (cmd_type << _IOC_TYPESHIFT) |
        (cmd_number << _IOC_NRSHIFT)
    )

def IO(cmd_type: int, cmd_number: int) -> int:
    return IOC(IOC_NONE, cmd_type, cmd_number, 0)

def IOR(cmd_type: int, cmd_number: int, param_size: IOCParamSize) -> int:
    return IOC(IOC_READ, cmd_type, cmd_number, _IOC_TYPECHECK(param_size))

def IOW(cmd_type: int, cmd_number: int, param_size: IOCParamSize) -> int:
    return IOC(IOC_WRITE, cmd_type, cmd_number, _IOC_TYPECHECK(param_size))

def IOWR(cmd_type: int, cmd_number: int, param_size: IOCParamSize) -> int:
    return IOC(IOC_READ | IOC_WRITE, cmd_type, cmd_number, _IOC_TYPECHECK(param_size))
