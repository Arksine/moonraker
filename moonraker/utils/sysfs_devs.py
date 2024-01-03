# Utilities for enumerating devices using sysfs
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
from __future__ import annotations
import pathlib
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

OPTIONAL_USB_INFO = ["manufacturer", "product", "serial"]
NULL_DESCRIPTIONS = [
    "?", "none", "undefined", "reserved/undefined", "unused", "no subclass"
]

def read_item(parent: pathlib.Path, filename: str) -> str:
    return parent.joinpath(filename).read_text().strip()

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
    usb_devs_by_path: Dict[str, str] = {}
    usb_devs_by_id: Dict[str, str] = {}
    usb_by_path_dir = pathlib.Path(SER_BYPTH_PATH)
    usb_by_id_dir = pathlib.Path(SER_BYID_PATH)
    dev_root_folder = pathlib.Path("/dev")
    if usb_by_path_dir.is_dir():
        usb_devs_by_path = {
            dev.resolve().name: str(dev) for dev in usb_by_path_dir.iterdir()
        }
    if usb_by_id_dir.is_dir():
        usb_devs_by_id = {
            dev.resolve().name: str(dev) for dev in usb_by_id_dir.iterdir()
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
            "driver_name": driver_name
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
            usb_location: Optional[str] = None
            while usb_path.is_dir() and usb_path.name:
                dnum_file = usb_path.joinpath("devnum")
                bnum_file = usb_path.joinpath("busnum")
                if not dnum_file.is_file() or not bnum_file.is_file():
                    usb_path = usb_path.parent
                    continue
                devnum = int(dnum_file.read_text().strip())
                busnum = int(bnum_file.read_text().strip())
                usb_location = f"{busnum}:{devnum}"
                break
            device_info["path_by_hardware"] = usb_devs_by_path.get(device_name)
            device_info["path_by_id"] = usb_devs_by_id.get(device_name)
            device_info["usb_location"] = None
            if usb_location is not None:
                device_info["device_type"] = "usb"
                device_info["usb_location"] = usb_location

        serial_devs.append(device_info)
    return serial_devs
