# Utilities for enumerating devices using sysfs
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
from __future__ import annotations
import os
import fcntl
import ctypes
import pathlib
import enum
from ..common import ExtendedFlag
from . import ioctl_macros
from typing import (
    Dict,
    List,
    Any,
    Union,
    Optional
)

DEFAULT_USB_IDS_PATH = "/usr/share/misc/usb.ids"
USB_DEVICE_PATH = "/sys/bus/usb/devices"
TTY_PATH = "/sys/class/tty"
SER_BYPTH_PATH = "/dev/serial/by-path"
SER_BYID_PATH = "/dev/serial/by-id"
V4L_DEVICE_PATH = "/sys/class/video4linux"
V4L_BYPTH_PATH = "/dev/v4l/by-path"
V4L_BYID_PATH = "/dev/v4l/by-id"

OPTIONAL_USB_INFO = ["manufacturer", "product", "serial"]
NULL_DESCRIPTIONS = [
    "?", "none", "undefined", "reserved/undefined", "unused", "no subclass"
]

def read_item(parent: pathlib.Path, filename: str) -> str:
    return parent.joinpath(filename).read_text().strip()

def find_usb_folder(usb_path: pathlib.Path) -> Optional[str]:
    # Find the sysfs usb folder from a child folder
    while usb_path.is_dir() and usb_path.name:
        dnum_file = usb_path.joinpath("devnum")
        bnum_file = usb_path.joinpath("busnum")
        if not dnum_file.is_file() or not bnum_file.is_file():
            usb_path = usb_path.parent
            continue
        devnum = int(dnum_file.read_text().strip())
        busnum = int(bnum_file.read_text().strip())
        return f"{busnum}:{devnum}"
    return None

class UsbIdData:
    _usb_info_cache: Dict[str, str] = {
        "DI:1d50": "OpenMoko, Inc",
        "DI:1d50:614e": "Klipper 3d-Printer Firmware",
        "DI:1d50:6177": "Katapult Bootloader (CDC_ACM)"
    }

    def __init__(self, usb_id_path: Union[str, pathlib.Path]) -> None:
        if isinstance(usb_id_path, str):
            usb_id_path = pathlib.Path(usb_id_path)
        self.usb_id_path = usb_id_path.expanduser().resolve()
        self.parsed: bool = False
        self.usb_info: Dict[str, str] = {}

    def _is_hex(self, item: str) -> bool:
        try:
            int(item, 16)
        except ValueError:
            return False
        return True

    def get_item(self, key: str, check_null: bool = False) -> Optional[str]:
        item = self.usb_info.get(key, self._usb_info_cache.get(key))
        if item is None:
            if self.parsed:
                return None
            self.parse_usb_ids()
            item = self.usb_info.get(key)
            if item is None:
                return None
        self._usb_info_cache[key] = item
        if check_null and item.lower() in NULL_DESCRIPTIONS:
            return None
        return item

    def parse_usb_ids(self) -> None:
        self.parsed = True
        if not self.usb_id_path.is_file():
            return
        top_key: str = ""
        sub_key: str = ""
        with self.usb_id_path.open(encoding="latin-1") as f:
            while True:
                line = f.readline()
                if not line:
                    break
                stripped_line = line.strip()
                if not stripped_line or stripped_line[0] == "#":
                    continue
                if line[:2] == "\t\t":
                    if not sub_key:
                        continue
                    tertiary_id, desc = stripped_line.split(maxsplit=1)
                    self.usb_info[f"{sub_key}:{tertiary_id.lower()}"] = desc
                elif line[0] == "\t":
                    if not top_key:
                        continue
                    sub_id, desc = stripped_line.split(maxsplit=1)
                    sub_key = f"{top_key}:{sub_id.lower()}"
                    self.usb_info[sub_key] = desc
                else:
                    id_type, data = line.rstrip().split(maxsplit=1)
                    if len(id_type) == 4 and self._is_hex(id_type):
                        # This is a vendor ID
                        top_key = f"DI:{id_type.lower()}"
                        self.usb_info[top_key] = data
                    elif id_type:
                        # This is a subtype
                        num_id, desc = data.split(maxsplit=1)
                        top_key = f"{id_type}:{num_id.lower()}"
                        self.usb_info[top_key] = desc
                    else:
                        break

    def get_product_info(self, vendor_id: str, product_id: str) -> Dict[str, Any]:
        vendor_name = self.get_item(f"DI:{vendor_id}")
        if vendor_name is None:
            return {
                "description": None,
                "manufacturer": None,
                "product": None,
            }
        product_name = self.get_item(f"DI:{vendor_id}:{product_id}")
        return {
            "description": f"{vendor_name} {product_name or ''}".strip(),
            "manufacturer": vendor_name,
            "product": product_name,
        }

    def get_class_info(
        self, cls_id: str, subcls_id: str, proto_id: str
    ) -> Dict[str, Any]:
        cls_desc = self.get_item(f"C:{cls_id}")
        if cls_desc is None or cls_id == "00":
            return {
                "class": None,
                "subclass": None,
                "protocol": None
            }
        return {
            "class": cls_desc,
            "subclass": self.get_item(f"C:{cls_id}:{subcls_id}", True),
            "protocol": self.get_item(f"C:{cls_id}:{subcls_id}:{proto_id}", True)
        }

def find_usb_devices() -> List[Dict[str, Any]]:
    dev_folder = pathlib.Path(USB_DEVICE_PATH)
    if not dev_folder.is_dir():
        return []
    usb_devs: List[Dict[str, Any]] = []
    # Find sysfs usb device descriptors
    for dev_cfg_path in dev_folder.glob("*/bDeviceClass"):
        dev_folder = dev_cfg_path.parent
        device_info: Dict[str, Any] = {}
        try:
            device_info["device_num"] = int(read_item(dev_folder, "devnum"))
            device_info["bus_num"] = int(read_item(dev_folder, "busnum"))
            device_info["vendor_id"] = read_item(dev_folder, "idVendor").lower()
            device_info["product_id"] = read_item(dev_folder, "idProduct").lower()
            usb_location = f"{device_info['bus_num']}:{device_info['device_num']}"
            device_info["usb_location"] = usb_location
            dev_cls = read_item(dev_folder, "bDeviceClass").lower()
            dev_subcls = read_item(dev_folder, "bDeviceSubClass").lower()
            dev_proto = read_item(dev_folder, "bDeviceProtocol").lower()
            device_info["class_ids"] = [dev_cls, dev_subcls, dev_proto]
            for field in OPTIONAL_USB_INFO:
                if dev_folder.joinpath(field).is_file():
                    device_info[field] = read_item(dev_folder, field)
                elif field not in device_info:
                    device_info[field] = None
        except Exception:
            continue
        usb_devs.append(device_info)
    return usb_devs

def find_serial_devices() -> List[Dict[str, Any]]:
    serial_devs: List[Dict[str, Any]] = []
    devs_by_path: Dict[str, str] = {}
    devs_by_id: Dict[str, str] = {}
    by_path_dir = pathlib.Path(SER_BYPTH_PATH)
    by_id_dir = pathlib.Path(SER_BYID_PATH)
    dev_root_folder = pathlib.Path("/dev")
    if by_path_dir.is_dir():
        devs_by_path = {
            dev.resolve().name: str(dev) for dev in by_path_dir.iterdir()
        }
    if by_id_dir.is_dir():
        devs_by_id = {
            dev.resolve().name: str(dev) for dev in by_id_dir.iterdir()
        }
    tty_dir = pathlib.Path(TTY_PATH)
    for tty_path in tty_dir.iterdir():
        device_folder = tty_path.joinpath("device")
        if not device_folder.is_dir():
            continue
        uartclk_file = tty_path.joinpath("uartclk")
        port_file = tty_path.joinpath("port")
        device_name = tty_path.name
        driver_name = device_folder.joinpath("driver").resolve().name
        device_info: Dict[str, Any] = {
            "device_type": "unknown",
            "device_path": str(dev_root_folder.joinpath(device_name)),
            "device_name": device_name,
            "driver_name": driver_name,
            "path_by_hardware": devs_by_path.get(device_name),
            "path_by_id": devs_by_id.get(device_name),
            "usb_location": None
        }
        if uartclk_file.is_file() and port_file.is_file():
            # This is a potential hardware uart.  Need to
            # validate that "serial8250" devices have a port
            # number of zero
            if driver_name == "serial8250":
                portnum = int(port_file.read_text().strip(), 16)
                if portnum != 0:
                    # Not a usable UART
                    continue
            device_info["device_type"] = "hardware_uart"
        else:
            usb_path = device_folder.resolve()
            usb_location: Optional[str] = find_usb_folder(usb_path)
            if usb_location is not None:
                device_info["device_type"] = "usb"
                device_info["usb_location"] = usb_location
        serial_devs.append(device_info)
    return serial_devs

class struct_v4l2_capability(ctypes.Structure):
    _fields_ = [
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]

class struct_v4l2_fmtdesc(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("description", ctypes.c_char * 32),
        ("pixelformat", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 4)
    ]

class struct_v4l2_frmsize_discrete(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
    ]


class struct_v4l2_frmsize_stepwise(ctypes.Structure):
    _fields_ = [
        ("min_width", ctypes.c_uint32),
        ("max_width", ctypes.c_uint32),
        ("step_width", ctypes.c_uint32),
        ("min_height", ctypes.c_uint32),
        ("max_height", ctypes.c_uint32),
        ("step_height", ctypes.c_uint32),
    ]

class struct_v4l2_frmsize_union(ctypes.Union):
    _fields_ = [
        ("discrete", struct_v4l2_frmsize_discrete),
        ("stepwise", struct_v4l2_frmsize_stepwise)
    ]

class struct_v4l2_frmsizeenum(ctypes.Structure):
    _anonymous_ = ("size",)
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("pixel_format", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("size", struct_v4l2_frmsize_union),
        ("reserved", ctypes.c_uint32 * 2)
    ]

class V4L2Capability(ExtendedFlag):
    VIDEO_CAPTURE        = 0x00000001  # noqa: E221
    VIDEO_OUTPUT         = 0x00000002  # noqa: E221
    VIDEO_OVERLAY        = 0x00000004  # noqa: E221
    VBI_CAPTURE          = 0x00000010  # noqa: E221
    VBI_OUTPUT           = 0x00000020  # noqa: E221
    SLICED_VBI_CAPTURE   = 0x00000040  # noqa: E221
    SLICED_VBI_OUTPUT    = 0x00000080  # noqa: E221
    RDS_CAPTURE          = 0x00000100  # noqa: E221
    VIDEO_OUTPUT_OVERLAY = 0x00000200
    HW_FREQ_SEEK         = 0x00000400  # noqa: E221
    RDS_OUTPUT           = 0x00000800  # noqa: E221
    VIDEO_CAPTURE_MPLANE = 0x00001000
    VIDEO_OUTPUT_MPLANE  = 0x00002000  # noqa: E221
    VIDEO_M2M_MPLANE     = 0x00004000  # noqa: E221
    VIDEO_M2M            = 0x00008000  # noqa: E221
    TUNER                = 0x00010000  # noqa: E221
    AUDIO                = 0x00020000  # noqa: E221
    RADIO                = 0x00040000  # noqa: E221
    MODULATOR            = 0x00080000  # noqa: E221
    SDR_CAPTURE          = 0x00100000  # noqa: E221
    EXT_PIX_FORMAT       = 0x00200000  # noqa: E221
    SDR_OUTPUT           = 0x00400000  # noqa: E221
    META_CAPTURE         = 0x00800000  # noqa: E221
    READWRITE            = 0x01000000  # noqa: E221
    STREAMING            = 0x04000000  # noqa: E221
    META_OUTPUT          = 0x08000000  # noqa: E221
    TOUCH                = 0x10000000  # noqa: E221
    IO_MC                = 0x20000000  # noqa: E221
    SET_DEVICE_CAPS      = 0x80000000  # noqa: E221

class V4L2FrameSizeTypes(enum.IntEnum):
    DISCRETE = 1
    CONTINUOUS = 2
    STEPWISE = 3

class V4L2FormatFlags(ExtendedFlag):
    COMPRESSED = 0x0001
    EMULATED = 0x0002


V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_QUERYCAP = ioctl_macros.IOR(ord("V"), 0, struct_v4l2_capability)
V4L2_ENUM_FMT = ioctl_macros.IOWR(ord("V"), 2, struct_v4l2_fmtdesc)
V4L2_ENUM_FRAMESIZES = ioctl_macros.IOWR(ord("V"), 74, struct_v4l2_frmsizeenum)

def v4l2_fourcc_from_fmt(pixelformat: int) -> str:
    fmt = bytes([((pixelformat >> (8 * i)) & 0xFF) for i in range(4)])
    return fmt.decode(encoding="ascii", errors="ignore")

def v4l2_fourcc(format: str) -> int:
    assert len(format) == 4
    result: int = 0
    for idx, val in enumerate(format.encode()):
        result |= (val << (8 * idx)) & 0xFF
    return result

def _get_resolutions(fd: int, pixel_format: int) -> List[str]:
    res_info = struct_v4l2_frmsizeenum()
    result: List[str] = []
    for idx in range(128):
        res_info.index = idx
        res_info.pixel_format = pixel_format
        try:
            fcntl.ioctl(fd, V4L2_ENUM_FRAMESIZES, res_info)
        except OSError:
            break
        if res_info.type != V4L2FrameSizeTypes.DISCRETE:
            break
        width = res_info.discrete.width
        height = res_info.discrete.height
        result.append(f"{width}x{height}")
    return result

def _get_modes(fd: int) -> List[Dict[str, Any]]:
    pix_info = struct_v4l2_fmtdesc()
    result: List[Dict[str, Any]] = []
    for idx in range(128):
        pix_info.index = idx
        pix_info.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        try:
            fcntl.ioctl(fd, V4L2_ENUM_FMT, pix_info)
        except OSError:
            break
        desc: str = pix_info.description.decode()
        pixel_format: int = pix_info.pixelformat
        flags = V4L2FormatFlags(pix_info.flags)
        resolutions = _get_resolutions(fd, pixel_format)
        if not resolutions:
            continue
        result.append(
            {
                "format": v4l2_fourcc_from_fmt(pixel_format),
                "description": desc,
                "flags": [f.name for f in flags],
                "resolutions": resolutions
            }
        )
    return result

def find_video_devices() -> List[Dict[str, Any]]:
    v4lpath = pathlib.Path(V4L_DEVICE_PATH)
    if not v4lpath.is_dir():
        return []
    v4l_by_path_dir = pathlib.Path(V4L_BYPTH_PATH)
    v4l_by_id_dir = pathlib.Path(V4L_BYID_PATH)
    dev_root_folder = pathlib.Path("/dev")
    v4l_devs_by_path: Dict[str, str] = {}
    v4l_devs_by_id: Dict[str, str] = {}
    if v4l_by_path_dir.is_dir():
        v4l_devs_by_path = {
            dev.resolve().name: str(dev) for dev in v4l_by_path_dir.iterdir()
        }
    if v4l_by_id_dir.is_dir():
        v4l_devs_by_id = {
            dev.resolve().name: str(dev) for dev in v4l_by_id_dir.iterdir()
        }
    v4l_devices: List[Dict[str, Any]] = []
    for v4ldev_path in v4lpath.iterdir():
        devfs_name = v4ldev_path.name
        devfs_path = dev_root_folder.joinpath(devfs_name)
        # The video4linux sysfs implmentation provides limited device
        # info.  Use the VIDEOC_QUERYCAPS ioctl to retreive extended
        # information about the v4l2 device.
        fd: int = -1
        try:
            fd = os.open(str(devfs_path), os.O_RDONLY | os.O_NONBLOCK)
            cap_info = struct_v4l2_capability()
            fcntl.ioctl(fd, V4L2_QUERYCAP, cap_info)
            capabilities = V4L2Capability(cap_info.device_caps)
            if not capabilities & V4L2Capability.VIDEO_CAPTURE:
                # Skip devices that do not capture video
                continue
            modes = _get_modes(fd)
        except Exception:
            continue
        finally:
            if fd != -1:
                os.close(fd)
        ver_tuple = tuple(
            [str((cap_info.version >> (i)) & 0xFF) for i in range(16, -1, -8)]
        )
        video_device: Dict[str, Any] = {
            "device_name": devfs_name,
            "device_path": str(devfs_path),
            "camera_name": cap_info.card.decode(),
            "driver_name": cap_info.driver.decode(),
            "hardware_bus": cap_info.bus_info.decode(),
            "capabilities": [cap.name for cap in capabilities],
            "version": ".".join(ver_tuple),
            "path_by_hardware": v4l_devs_by_path.get(devfs_name),
            "path_by_id": v4l_devs_by_id.get(devfs_name),
            "alt_name": None,
            "usb_location": None,
            "modes": modes
        }
        name_file = v4ldev_path.joinpath("name")
        if name_file.is_file():
            video_device["alt_name"] = read_item(v4ldev_path, "name")
        device_path = v4ldev_path.joinpath("device")
        if device_path.is_dir():
            usb_location = find_usb_folder(device_path.resolve())
            if usb_location is not None:
                video_device["usb_location"] = usb_location
        v4l_devices.append(video_device)

    def idx_sorter(item: Dict[str, Any]) -> int:
        try:
            return int(item["device_name"][5:])
        except ValueError:
            return -1
    # Sort by string first, then index
    v4l_devices.sort(key=lambda item: item["device_name"])
    v4l_devices.sort(key=idx_sorter)
    return v4l_devices
