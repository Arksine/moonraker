#!/usr/bin/env python3
# GCode metadata extraction utility
#
# Copyright (C) 2020-2025 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import json
import argparse
import re
import os
import sys
import io
import base64
import traceback
import tempfile
import zipfile
import shutil
import uuid
import logging
import shlex
import subprocess
from PIL import Image

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
    Tuple,
    Type,
)
if TYPE_CHECKING:
    pass

READ_SIZE = 1024 * 1024  # 1 MiB
UFP_MODEL_PATH = "/3D/model.gcode"
UFP_THUMB_PATH = "/Metadata/thumbnail.png"
SUPPORTED_THUMB_FORMATS = ("png", "jpg", "qoi")
FMT_CONV_MAP = {
    "qoi": "png"
}

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("metadata")

# Regex helpers.  These methods take patterns with placeholders
# to insert the correct regex capture group for floats, ints,
# and strings:
#  Float: (%F) = (\d*\.?\d+)
#  Integer: (%D) = (\d+)
#  String: (%S) = (.+)
def regex_find_floats(pattern: str, data: str) -> List[float]:
    pattern = pattern.replace(r"(%F)", r"([0-9]*\.?[0-9]+)")
    matches = re.findall(pattern, data)
    if matches:
        # return the maximum height value found
        try:
            return [float(h) for h in matches]
        except Exception:
            pass
    return []

def regex_find_ints(pattern: str, data: str) -> List[int]:
    pattern = pattern.replace(r"(%D)", r"([0-9]+)")
    matches = re.findall(pattern, data)
    if matches:
        # return the maximum height value found
        try:
            return [int(h) for h in matches]
        except Exception:
            pass
    return []

def regex_find_strings(pattern: str, separators: str, data: str) -> List[str]:
    pattern = pattern.replace(r"(%S)", r"(.*)")
    match = re.search(pattern, data)
    if match and match.group(1):
        separators = re.escape(separators)
        pattern = rf'\s*(")(?:\\"|[^"])*"\s*|[^{separators}]+'
        parsed_matches: List[str] = []
        for m in re.finditer(pattern, match.group(1)):
            (val, sep) = m.group(0, 1)
            val = val.strip()
            if sep:
                val = val[1:-1].replace(rf'\{sep}', sep).strip()
            if val:
                parsed_matches.append(val)
        return parsed_matches
    return []

def regex_find_float(pattern: str, data: str) -> Optional[float]:
    pattern = pattern.replace(r"(%F)", r"([0-9]*\.?[0-9]+)")
    match = re.search(pattern, data)
    val: Optional[float] = None
    if match:
        try:
            val = float(match.group(1))
        except Exception:
            return None
    return val

def regex_find_int(pattern: str, data: str) -> Optional[int]:
    pattern = pattern.replace(r"(%D)", r"([0-9]+)")
    match = re.search(pattern, data)
    val: Optional[int] = None
    if match:
        try:
            val = int(match.group(1))
        except Exception:
            return None
    return val

def regex_find_string(pattern: str, data: str) -> Optional[str]:
    pattern = pattern.replace(r"(%S)", r"(.*)")
    match = re.search(pattern, data)
    if match:
        return match.group(1).strip('"')
    return None

def regex_find_min_float(pattern: str, data: str) -> Optional[float]:
    result = regex_find_floats(pattern, data)
    return min(result) if result else None

def regex_find_max_float(pattern: str, data: str) -> Optional[float]:
    result = regex_find_floats(pattern, data)
    return max(result) if result else None

# Slicer parsing implementations
class SlicerType(type):
    '''
    Slicer metaclass automating slicer class registration.  Performs
    and ordered insert ensuring that derived classes are inserted
    prior to their parents.  This is done to perform slicer identification
    on derived classes first.
    '''
    registered_slicers: List[Type[BaseSlicer]] = []
    def __new__(
        metacls,
        clsname: str,
        bases: Tuple[type, ...],
        cls_attrs: Dict[str, Any]
    ):
        cls = super().__new__(metacls, clsname, bases, cls_attrs)
        if clsname not in ("BaseSlicer", "UnknownSlicer"):
            # Do ordered insert, children before parents
            idx: int = len(metacls.registered_slicers)
            for base in bases:
                if base in metacls.registered_slicers:
                    cur_idx = metacls.registered_slicers.index(base)
                    idx = min(idx, cur_idx)
            metacls.registered_slicers.insert(idx, cls)  # type: ignore
        return cls

class BaseSlicer(metaclass=SlicerType):
    def __init__(
        self,
        file_path: str,
        file_size: int,
        data: str,
        name: str = "Unknown",
        version: str = "?"
    ) -> None:
        self.slicer_name: str = name
        self.slicer_version: str = version
        self.path = file_path
        self.layer_height: Optional[float] = None
        self.has_m486_objects: bool = False
        self._file_data = data
        self.header_data = data[:READ_SIZE]
        self.footer_data = data[-READ_SIZE:]
        self.size: int = file_size

    def _check_has_objects(self,
                           data: str,
                           pattern: Optional[str] = None
                           ) -> bool:
        match = re.search(
            r"\n((DEFINE_OBJECT)|(EXCLUDE_OBJECT_DEFINE)) NAME=",
            data
        )
        if match is not None:
            # Objects already processed
            fname = os.path.basename(self.path)
            logger.info(
                f"File '{fname}' currently supports cancellation, "
                "processing aborted"
            )
            if match.group(1).startswith("DEFINE_OBJECT"):
                logger.info(
                    "Legacy object processing detected.  This is not "
                    "compatible with official versions of Klipper."
                )
            return False
        # Always check M486
        patterns = [r"\nM486"]
        if pattern is not None:
            patterns.append(pattern)
        for regex in patterns:
            if re.search(regex, data) is not None:
                self.has_m486_objects = regex == r"\nM486"
                return True
        return False

    @classmethod
    def from_file(cls, file_path: str) -> BaseSlicer:
        header = tail = ""
        with open(file_path, 'rb') as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(0)
            header = f.read(READ_SIZE).decode(errors="ignore")
            if size > READ_SIZE * 2:
                f.seek(size - READ_SIZE)
            if size > READ_SIZE:
                tail = f.read().decode(errors="ignore")
        for slicercls in cls.registered_slicers:
            ident = slicercls.identify(header)
            if ident is not None:
                name, ver = ident
                return slicercls(file_path, size, header + tail, name, ver)
        return UnknownSlicer(file_path, size, header + tail)

    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        return None

    def run_parsers(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for name in vars(BaseSlicer).keys():
            if name.startswith("parse_"):
                func = getattr(self, name)
                if not callable(func):
                    continue
                val = func()
                if val is not None:
                    field = name[6:]
                    result[field] = val
        return result

    def check_gcode_processor(self, regex: str, location: str) -> Dict[str, Any] | None:
        data = self.header_data if location == "header" else self.footer_data
        proc_match = re.search(regex, data, re.MULTILINE)
        if proc_match is not None:
            return proc_match.groupdict()
        return None

    def has_objects(self) -> bool:
        return self._check_has_objects(self.header_data)

    def parse_gcode_start_byte(self) -> Optional[int]:
        m = re.search(r"\n[MG]\d+\s.*\n", self.header_data)
        if m is None:
            return None
        return len(self.header_data[:m.start()].encode())

    def parse_gcode_end_byte(self) -> Optional[int]:
        rev_data = self.footer_data[::-1]
        m = re.search(r"\n.*\s\d+[MG]\n", rev_data)
        if m is None:
            return None
        return self.size - len(rev_data[:m.start()].encode())

    def parse_layer_count(self) -> Optional[int]:
        return None

    def parse_object_height(self) -> Optional[float]:
        return None

    def parse_estimated_time(self) -> Optional[float]:
        return None

    def parse_nozzle_diameter(self) -> Optional[float]:
        return None

    def parse_layer_height(self) -> Optional[float]:
        return None

    def parse_first_layer_height(self) -> Optional[float]:
        return None

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return None

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return None

    def parse_chamber_temp(self) -> Optional[float]:
        return None

    def parse_filament_name(self) -> Optional[str]:
        return None

    def parse_filament_type(self) -> Optional[str]:
        return None

    def parse_filament_colors(self) -> Optional[List[str]]:
        return None

    def parse_filament_change_count(self) -> Optional[int]:
        return None

    def parse_extruder_colors(self) -> Optional[List[str]]:
        return None

    def parse_filament_temps(self) -> Optional[List[int]]:
        return None

    def parse_referenced_tools(self) -> Optional[List[int]]:
        return None

    def parse_mmu_print(self) -> Optional[int]:
        return None

    def parse_filament_total(self) -> Optional[float]:
        return None

    def parse_filament_weight_total(self) -> Optional[float]:
        return None

    def parse_filament_weights(self) -> Optional[List[float]]:
        return None

    def parse_thumbnails(self) -> Optional[List[Dict[str, Any]]]:
        parsed_matches: List[Dict[str, Any]] = []
        has_miniature: bool = False
        thumb_dir = os.path.join(os.path.dirname(self.path), ".thumbs")
        if not os.path.exists(thumb_dir):
            try:
                os.mkdir(thumb_dir)
            except Exception:
                logger.info(f"Unable to create thumb dir: {thumb_dir}")
                return None
        thumb_base = os.path.splitext(os.path.basename(self.path))[0]
        pattern = r"(thumbnail(?:_[A-Za-z0-9]+)?) begin([;/\+=\w\s]+?); \1 end"
        for match in re.finditer(pattern, self._file_data):
            ext = match.group(1).partition("_")[2].lower() or "png"
            if ext not in SUPPORTED_THUMB_FORMATS:
                logger.info(f"Unsupported thumbnail extension: {ext}")
                continue
            lines = re.split(r"\r?\n", match.group(2).replace('; ', ''))
            info = regex_find_ints(r"(%D)", lines[0])
            data = "".join(lines[1:-1])
            if len(info) != 3:
                logger.info(
                    f"MetadataError: Error parsing thumbnail header: {lines[0]}"
                )
                continue
            if len(data) != info[2]:
                logger.info(
                    f"MetadataError: Thumbnail Size Mismatch: "
                    f"detected {info[2]}, actual {len(data)}")
                continue
            dest_ext = FMT_CONV_MAP.get(ext, ext)
            thumb_name = f"{thumb_base}-{info[0]}x{info[1]}.{dest_ext}"
            thumb_path = os.path.join(thumb_dir, thumb_name)
            rel_thumb_path = os.path.join(".thumbs", thumb_name)
            if dest_ext != ext:
                # Convert image.  Format is determined by destination file
                # extension.  Only formats supported by Pillow should be used.
                with Image.open(io.BytesIO(base64.b64decode(data.encode()))) as im:
                    im.save(thumb_path)
            else:
                with open(thumb_path, "wb") as f:
                    f.write(base64.b64decode(data.encode()))
            parsed_matches.append({
                'width': info[0], 'height': info[1],
                'size': os.path.getsize(thumb_path),
                'relative_path': rel_thumb_path})
            if info[0] == 32 and info[1] == 32:
                has_miniature = True
        if not parsed_matches:
            return None
        if not has_miniature:
            # find the largest thumb index
            largest_match = parsed_matches[0]
            for item in parsed_matches:
                if item['size'] > largest_match['size']:
                    largest_match = item
            # Create miniature thumbnail if one does not exist
            thumb_full_name = largest_match['relative_path'].split("/")[-1]
            thumb_path = os.path.join(thumb_dir, f"{thumb_full_name}")
            rel_path_small = os.path.join(".thumbs", f"{thumb_base}-32x32.png")
            thumb_path_small = os.path.join(
                thumb_dir, f"{thumb_base}-32x32.png")
            # read file
            try:
                with Image.open(thumb_path) as im:
                    # Create 32x32 thumbnail
                    im.thumbnail((32, 32))
                    im.save(thumb_path_small, format="PNG")
                    parsed_matches.insert(0, {
                        'width': im.width, 'height': im.height,
                        'size': os.path.getsize(thumb_path_small),
                        'relative_path': rel_path_small
                    })
            except Exception as e:
                logger.info(str(e))
        return parsed_matches

class UnknownSlicer(BaseSlicer):
    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_min_float(r"G1\sZ(%F)\s", self.header_data)

    def parse_object_height(self) -> Optional[float]:
        return regex_find_max_float(r"G1\sZ(%F)\s", self.footer_data)

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(r"M109 S(%F)", self.header_data)

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(r"M190 S(%F)", self.header_data)

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(r"M191 S(%F)", self.header_data)

class PrusaSlicer(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        # Default base pattern for PrusaSlicer and its forks
        pattern = r"; generated by (.+?) on \d{4}-\d{2}-\d{2} at \d{2}:\d{2}:\d{2}"
        match = re.search(pattern, data)
        if match is None:
            # Fall back to search by known alias (legacy regex search)
            known_aliases = [
                "PrusaSlicer", "SuperSlicer", "OrcaSlicer", "MomentSlicer",
                "SliCR-3D", "A3dp-Slicer", "QIDISlicer"
            ]
            pattern = r"({ids})\s(.*)\son".format(ids="|".join(known_aliases))
            match = re.search(pattern, data)
            if match:
                logging.info(
                    f"Detected PrusaSlicer fork via fallback: {match[1]}"
                )
                return match.groups()  # type: ignore
            return None
        name, _, version = match.group(1).rpartition(" ")
        if name.lower() in cls.get_exclusions():
            # Make sure we don't identify a deriviative implementation,
            # since the generic regex pattern will return a match.
            return None
        return name, version

    @classmethod
    def regex_id(cls) -> str:
        return cls.__name__

    @classmethod
    def get_exclusions(cls) -> set[str]:
        exclusions: set[str] = set()
        subclasses = list(cls.__subclasses__())
        while subclasses:
            sc = subclasses.pop()
            subclasses.extend(list(sc.__subclasses__()))
            exclusions.add(sc.regex_id().lower())
        return exclusions

    @property
    def config_data(self) -> str:
        return self.footer_data

    def has_objects(self) -> bool:
        return self._check_has_objects(
            self.header_data, r"\n; printing object")

    def parse_first_layer_height(self) -> Optional[float]:
        # Check percentage
        pct = regex_find_float(r"; first_layer_height = (%F)%", self.config_data)
        if pct is not None:
            if self.layer_height is None:
                # Failed to parse the original layer height, so it is not
                # possible to calculate a percentage
                return None
            return round(pct / 100. * self.layer_height, 6)
        return regex_find_float(r"; first_layer_height = (%F)", self.config_data)

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r"; layer_height = (%F)", self.config_data
        )
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        matches = re.findall(
            r";BEFORE_LAYER_CHANGE\n(?:.*\n)?;(\d+\.?\d*)", self.footer_data)
        if matches:
            try:
                matches = [float(m) for m in matches]
            except Exception:
                pass
            else:
                return max(matches)
        return regex_find_max_float(r"G1\sZ(%F)\sF", self.footer_data)

    def parse_filament_total(self) -> Optional[float]:
        line = regex_find_string(r'filament\sused\s\[mm\]\s=\s(%S)\n', self.config_data)
        if line:
            filament = regex_find_floats(
                r"(%F)", line
            )
            if filament:
                return sum(filament)
        return None

    def parse_filament_weight_total(self) -> Optional[float]:
        return regex_find_float(
            r"total\sfilament\sused\s\[g\]\s=\s(%F)",
            self.config_data
        )

    def parse_filament_weights(self) -> Optional[List[float]]:
        line = regex_find_string(r'filament\sused\s\[g\]\s=\s(%S)\n', self.config_data)
        if line:
            weights = regex_find_floats(
                r"(%F)", line
            )
            if weights:
                return weights
        return None

    def parse_filament_type(self) -> Optional[str]:
        result = regex_find_strings(
            r";\sfilament_type\s=\s(%S)", ",;", self.config_data
        )
        if len(result) > 1:
            return json.dumps(result)
        elif result:
            return result[0]
        return None

    def parse_filament_name(self) -> Optional[str]:
        result = regex_find_strings(
            r";\sfilament_settings_id\s=\s(%S)", ",;", self.config_data
        )
        if len(result) > 1:
            return json.dumps(result)
        elif result:
            return result[0]
        return None

    def parse_filament_colors(self) -> Optional[List[str]]:
        return regex_find_strings(
            r";\sfilament_colour\s=\s(%S)", ",;", self.config_data
        )

    def parse_extruder_colors(self) -> Optional[List[str]]:
        return regex_find_strings(
            r";\sextruder_colour\s=\s(%S)", ",;", self.config_data
        )

    def parse_filament_temps(self) -> Optional[List[int]]:
        temps = regex_find_strings(
            r";\s(?:nozzle_)?temperature\s=\s(%S)", ",;", self.config_data
        )
        try:
            return [int(t) for t in temps]
        except ValueError:
            return None

    def parse_referenced_tools(self) -> Optional[List[int]]:
        tools = regex_find_strings(
            r";\sreferenced_tools\s=\s(%S)", ",;", self.config_data
        )
        try:
            return [int(t) for t in tools]
        except ValueError:
            return None

    def parse_mmu_print(self) -> Optional[int]:
        return regex_find_int(
            r";\ssingle_extruder_multi_material\s=\s(%D)", self.config_data
        )

    def parse_estimated_time(self) -> Optional[float]:
        time_match = re.search(
            r';\sestimated\sprinting\stime.*', self.config_data
        )
        if not time_match:
            return None
        total_time = 0
        time_group = time_match.group()
        time_patterns = [(r"(\d+)d", 24*60*60), (r"(\d+)h", 60*60),
                         (r"(\d+)m", 60), (r"(\d+)s", 1)]
        try:
            for pattern, multiplier in time_patterns:
                t = re.search(pattern, time_group)
                if t:
                    total_time += int(t.group(1)) * multiplier
        except Exception:
            return None
        return round(total_time, 2)

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; first_layer_temperature = (%F)", self.config_data
        )

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; first_layer_bed_temperature = (%F)", self.config_data
        )

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; chamber_temperature = (%F)", self.config_data
        )

    def parse_nozzle_diameter(self) -> Optional[float]:
        return regex_find_float(
            r";\snozzle_diameter\s=\s(%F)", self.config_data
        )

    def parse_layer_count(self) -> Optional[int]:
        return regex_find_int(r"; total layers count = (%D)", self.config_data)

    def parse_filament_change_count(self) -> Optional[int]:
        res = regex_find_int(r"; total toolchanges = (%D)", self.config_data)
        if res is not None:
            return res
        return regex_find_int(r"; total filament change = (%D)", self.config_data)

class Slic3rPE(PrusaSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"Slic3r\sPrusa\sEdition\s(.*)\son", data)
        if match:
            return "Slic3r PE", match.group(1)
        return None

    @classmethod
    def regex_id(cls) -> str:
        return "Slic3r Prusa Edition"

    def parse_filament_total(self) -> Optional[float]:
        return regex_find_float(r"filament\sused\s=\s(%F)mm", self.footer_data)

    def parse_thumbnails(self) -> Optional[List[Dict[str, Any]]]:
        return None

class Slic3r(PrusaSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"Slic3r\s(\d.*)\son", data)
        if match:
            return "Slic3r", match.group(1)
        return None

    def parse_filament_total(self) -> Optional[float]:
        filament = regex_find_float(
            r";\sfilament\_length\_m\s=\s(%F)", self.footer_data
        )
        if filament is not None:
            filament *= 1000
        return filament

    def parse_filament_weight_total(self) -> Optional[float]:
        return regex_find_float(r";\sfilament\smass\_g\s=\s(%F)", self.footer_data)

    def parse_estimated_time(self) -> Optional[float]:
        return None

    def parse_thumbnails(self) -> Optional[List[Dict[str, Any]]]:
        return None

class BambuStudio(PrusaSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"; BambuStudio[^ ]* ([^\n]+)", data)
        if match is not None:
            return "BambuStudio", match.group(1)
        return None

    @property
    def config_data(self) -> str:
        return self.header_data

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r"; initial_layer_print_height = (%F)", self.config_data
        )

    def parse_object_height(self) -> float | None:
        return regex_find_float(r"; max_z_height: (%F)", self.config_data)

    def parse_filament_total(self) -> float | None:
        return regex_find_float(
            r"; total filament length \[mm\] : (%F)", self.config_data
        )

    def parse_filament_weight_total(self) -> float | None:
        return regex_find_float(
            r"; total filament weight \[g\] : (%F)", self.config_data
        )

    def parse_filament_weights(self) -> Optional[List[float]]:
        return None

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; nozzle_temperature_initial_layer = (%F)", self.config_data
        )

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; hot_plate_temp_initial_layer = (%F)", self.config_data
        )

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; chamber_temperatures = (%F)", self.config_data
        )

    def parse_layer_count(self) -> Optional[int]:
        return regex_find_int(r"; total layer number: (%D)", self.config_data)

class Cura(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"Cura_SteamEngine\s(.*)", data)
        if match:
            return "Cura", match.group(1)
        return None

    def has_objects(self) -> bool:
        return self._check_has_objects(self.header_data, r"\n;MESH:")

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(r";MINZ:(%F)", self.header_data)

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r";Layer\sheight:\s(%F)", self.header_data
        )
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        return regex_find_float(r";MAXZ:(%F)", self.header_data)

    def parse_filament_total(self) -> Optional[float]:
        line = regex_find_string(r';Filament\sused:\s(%S)\n', self.header_data)
        if line:
            filament = regex_find_floats(
                r"(%F)", line
            )
            if filament:
                return sum(length * 1000 for length in filament)
        return None

    def parse_filament_weight_total(self) -> Optional[float]:
        filament_weights = self.parse_filament_weights()
        if filament_weights:
            return sum(filament_weights)
        return None

    def parse_filament_weights(self) -> Optional[List[float]]:
        line = regex_find_string(r';Filament\sweight\s=\s\[(%S)\]', self.header_data)
        if line:
            weights = regex_find_floats(
                r"(%F)", line
            )
            if weights:
                return weights
        return None

    def parse_filament_type(self) -> Optional[str]:
        return regex_find_string(r";Filament\stype\s=\s(%S)", self.header_data)

    def parse_filament_name(self) -> Optional[str]:
        return regex_find_string(r";Filament\sname\s=\s(%S)", self.header_data)

    def parse_estimated_time(self) -> Optional[float]:
        return regex_find_max_float(r";TIME:(%F)", self.header_data)

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(r"M109 S(%F)", self.header_data)

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(r"M190 S(%F)", self.header_data)

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(r"M191 S(%F)", self.header_data)

    def parse_layer_count(self) -> Optional[int]:
        return regex_find_int(r";LAYER_COUNT\:(%D)", self.header_data)

    def parse_nozzle_diameter(self) -> Optional[float]:
        return regex_find_float(r";Nozzle\sdiameter\s=\s(%F)", self.header_data)

    def parse_thumbnails(self) -> Optional[List[Dict[str, Any]]]:
        # Attempt to parse thumbnails from file metadata
        thumbs = super().parse_thumbnails()
        if thumbs is not None:
            return thumbs
        # Check for thumbnails extracted from the ufp
        thumb_dir = os.path.join(os.path.dirname(self.path), ".thumbs")
        thumb_base = os.path.splitext(os.path.basename(self.path))[0]
        thumb_path = os.path.join(thumb_dir, f"{thumb_base}.png")
        rel_path_full = os.path.join(".thumbs", f"{thumb_base}.png")
        rel_path_small = os.path.join(".thumbs", f"{thumb_base}-32x32.png")
        thumb_path_small = os.path.join(thumb_dir, f"{thumb_base}-32x32.png")
        if not os.path.isfile(thumb_path):
            return None
        # read file
        thumbs = []
        try:
            with Image.open(thumb_path) as im:
                thumbs.append({
                    'width': im.width, 'height': im.height,
                    'size': os.path.getsize(thumb_path),
                    'relative_path': rel_path_full
                })
                # Create 32x32 thumbnail
                im.thumbnail((32, 32), Image.Resampling.LANCZOS)
                im.save(thumb_path_small, format="PNG")
                thumbs.insert(0, {
                    'width': im.width, 'height': im.height,
                    'size': os.path.getsize(thumb_path_small),
                    'relative_path': rel_path_small
                })
        except Exception as e:
            logger.info(str(e))
            return None
        return thumbs

class Simplify3D(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"Simplify3D\(R\)\sVersion\s(.*)", data)
        if match:
            return "Simplify3D", match.group(1)
        return None

    @property
    def is_v5(self) -> bool:
        return self.slicer_version.startswith("5")

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_min_float(r"G1\sZ(%F)\s", self.header_data)

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r";\s+layerHeight,(%F)", self.header_data
        )
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        return regex_find_max_float(r"G1\sZ(%F)\s", self.footer_data)

    def parse_filament_total(self) -> Optional[float]:
        return regex_find_float(
            r";\s+(?:Filament\slength|Material\sLength):\s(%F)\smm",
            self.footer_data
        )

    def parse_filament_weight_total(self) -> Optional[float]:
        return regex_find_float(
            r";\s+(?:Plastic\sweight|Material\sWeight):\s(%F)\sg",
            self.footer_data
        )

    def parse_filament_name(self) -> Optional[str]:
        return regex_find_string(
            r";\s+printMaterial,(%S)", self.header_data)

    def parse_filament_type(self) -> Optional[str]:
        return regex_find_string(
            r";\s+makerBotModelMaterial,(%S)", self.footer_data)

    def parse_estimated_time(self) -> Optional[float]:
        time_match = re.search(r';\s+Build (t|T)ime:.*', self.footer_data)
        if not time_match:
            return None
        total_time = 0
        time_group = time_match.group()
        time_patterns = [(r"(\d+)\shours?", 60*60), (r"(\d+)\smin", 60),
                         (r"(\d+)\ssec", 1)]
        try:
            for pattern, multiplier in time_patterns:
                t = re.search(pattern, time_group)
                if t:
                    total_time += int(t.group(1)) * multiplier
        except Exception:
            return None
        return round(total_time, 2)

    def _get_temp_items(self, pattern: str) -> List[str]:
        match = re.search(pattern, self.header_data)
        if match is None:
            return []
        return match.group().split(",")[1:]

    def _get_first_layer_temp(self, heater: str) -> Optional[float]:
        heaters = self._get_temp_items(r"temperatureName.*")
        temps = self._get_temp_items(r"temperatureSetpointTemperatures.*")
        for h, temp in zip(heaters, temps):
            if h == heater:
                try:
                    return float(temp)
                except Exception:
                    return None
        return None

    def _get_first_layer_temp_v5(self, heater_type: str) -> Optional[float]:
        pattern = (
            r";\s+temperatureController,.+?"
            r";\s+temperatureType,"f"{heater_type}"r".+?"
            r";\s+temperatureSetpoints,\d+\|(\d+)"
        )
        match = re.search(pattern, self.header_data, re.MULTILINE | re.DOTALL)
        if match is not None:
            try:
                return float(match.group(1))
            except Exception:
                return None
        return None

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        if self.is_v5:
            return self._get_first_layer_temp_v5("extruder")
        else:
            return self._get_first_layer_temp("Extruder 1")

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        if self.is_v5:
            return self._get_first_layer_temp_v5("platform")
        else:
            return self._get_first_layer_temp("Heated Bed")

    def parse_nozzle_diameter(self) -> Optional[float]:
        return regex_find_float(
            r";\s+(?:extruderDiameter|nozzleDiameter),(%F)",
            self.header_data
        )

class KISSlicer(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r";\sKISSlicer", data)
        if match:
            version: str = "?"
            vmatch = re.search(r";\sversion\s(.*)", data)
            if vmatch:
                version = vmatch.group(1).replace(" ", "-")
            return "KISSlicer", version
        return None

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r";\s+first_layer_thickness_mm\s=\s(%F)", self.header_data)

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r";\s+max_layer_thickness_mm\s=\s(%F)", self.header_data)
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        return regex_find_max_float(
            r";\sEND_LAYER_OBJECT\sz=(%F)", self.footer_data)

    def parse_filament_total(self) -> Optional[float]:
        filament = regex_find_floats(
            r";\s+Ext #\d+\s+=\s+(%F)\s*mm", self.footer_data)
        if filament:
            return sum(filament)
        return None

    def parse_estimated_time(self) -> Optional[float]:
        time = regex_find_float(
            r";\sCalculated.*Build\sTime:\s(%F)\sminutes",
            self.footer_data)
        if time is not None:
            time *= 60
            return round(time, 2)
        return None

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(r"; first_layer_C = (%F)", self.header_data)

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(r"; bed_C = (%F)", self.header_data)

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(r"; chamber_C = (%F)", self.header_data)


class IdeaMaker(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"\sideaMaker\s(.*),", data)
        if match:
            return "IdeaMaker", match.group(1)
        return None

    def has_objects(self) -> bool:
        return self._check_has_objects(self.header_data, r"\n;PRINTING:")

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r";LAYER:0\s*.*\s*;HEIGHT:(%F)", self.header_data
        )

    def parse_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r";LAYER:1\s*.*\s*;HEIGHT:(%F)", self.header_data
        )

    def parse_object_height(self) -> Optional[float]:
        return regex_find_float(r";Bounding Box:(?:\s+(%F))+", self.header_data)

    def parse_filament_total(self) -> Optional[float]:
        filament = regex_find_floats(
            r";Material.\d\sUsed:\s+(%F)", self.footer_data
        )
        if filament:
            return sum(filament)
        return None

    def parse_filament_type(self) -> Optional[str]:
        return (
            regex_find_string(r";Filament\sType\s.\d:\s(%S)", self.header_data) or
            regex_find_string(r";Filament\stype\s=\s(%S)", self.header_data)
        )

    def parse_filament_name(self) -> Optional[str]:
        return (
            regex_find_string(r";Filament\sName\s.\d:\s(%S)", self.header_data) or
            regex_find_string(r";Filament\sname\s=\s(%S)", self.header_data)
        )

    def parse_filament_weight_total(self) -> Optional[float]:
        pi = 3.141592653589793
        length = regex_find_floats(
            r";Material.\d\sUsed:\s+(%F)", self.footer_data)
        diameter = regex_find_floats(
            r";Filament\sDiameter\s.\d:\s+(%F)", self.header_data)
        density = regex_find_floats(
            r";Filament\sDensity\s.\d:\s+(%F)", self.header_data)
        if len(length) == len(density) == len(diameter):
            # calc individual weight for each filament with m=pi/4*d²*l*rho
            weights = [(pi/4 * diameter[i]**2 * length[i] * density[i]/10**6)
                       for i in range(len(length))]
            return sum(weights)
        return None

    def parse_estimated_time(self) -> Optional[float]:
        return regex_find_float(r";Print\sTime:\s(%F)", self.footer_data)

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(r"M109 T0 S(%F)", self.header_data)

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(r"M190 S(%F)", self.header_data)

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(r"M191 S(%F)", self.header_data)

    def parse_nozzle_diameter(self) -> Optional[float]:
        return regex_find_float(
            r";Dimension:(?:\s\d+\.\d+){3}\s(%F)", self.header_data)

class IceSL(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        match = re.search(r"<IceSL\s(.*)>", data)
        if match:
            version = match.group(1) if match.group(1)[0].isdigit() else "-"
            return "IceSL", version
        return None

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r";\sz_layer_height_first_layer_mm\s:\s+(%F)",
            self.header_data)

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r";\sz_layer_height_mm\s:\s+(%F)",
            self.header_data)
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        return regex_find_float(
            r";\sprint_height_mm\s:\s+(%F)", self.header_data)

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(
            r";\sextruder_temp_degree_c_0\s:\s+(%F)", self.header_data)

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(
            r";\sbed_temp_degree_c\s:\s+(%F)", self.header_data)

    def parse_chamber_temp(self) -> Optional[float]:
        return regex_find_float(
            r";\schamber_temp_degree_c\s:\s+(%F)", self.header_data)

    def parse_filament_total(self) -> Optional[float]:
        return regex_find_float(
            r";\sfilament_used_mm\s:\s+(%F)", self.header_data)

    def parse_filament_weight_total(self) -> Optional[float]:
        return regex_find_float(
            r";\sfilament_used_g\s:\s+(%F)", self.header_data)

    def parse_filament_name(self) -> Optional[str]:
        return regex_find_string(
            r";\sfilament_name\s:\s+(%S)", self.header_data)

    def parse_filament_type(self) -> Optional[str]:
        return regex_find_string(
            r";\sfilament_type\s:\s+(%S)", self.header_data)

    def parse_estimated_time(self) -> Optional[float]:
        return regex_find_float(
            r";\sestimated_print_time_s\s:\s+(%F)", self.header_data)

    def parse_layer_count(self) -> Optional[int]:
        return regex_find_int(
            r";\slayer_count\s:\s+(%D)", self.header_data)

    def parse_nozzle_diameter(self) -> Optional[float]:
        return regex_find_float(
            r";\snozzle_diameter_mm_0\s:\s+(%F)", self.header_data)

class KiriMoto(BaseSlicer):
    @classmethod
    def identify(cls, data: str) -> Tuple[str, str] | None:
        variants: Dict[str, str] = {
            "Kiri:Moto": r"; Generated by Kiri:Moto (\d.+)",
            "SimplyPrint": r"; Generated by Kiri:Moto \(SimplyPrint\) (.+)"
        }
        for name, pattern in variants.items():
            match = re.search(pattern, data)
            if match:
                return name, match.group(1)
        return None

    def parse_first_layer_height(self) -> Optional[float]:
        return regex_find_float(
            r"; firstSliceHeight = (%F)", self.header_data
        )

    def parse_layer_height(self) -> Optional[float]:
        self.layer_height = regex_find_float(
            r"; sliceHeight = (%F)", self.header_data
        )
        return self.layer_height

    def parse_object_height(self) -> Optional[float]:
        return regex_find_max_float(
            r"G1 Z(%F) (?:; z-hop end|F\d+\n)", self.footer_data
        )

    def parse_layer_count(self) -> Optional[int]:
        matches = re.findall(
            r";; --- layer (\d+) \(.+", self.footer_data
        )
        if not matches:
            return None
        try:
            return int(matches[-1]) + 1
        except Exception:
            return None

    def parse_estimated_time(self) -> Optional[float]:
        return regex_find_int(r"; --- print time: (%D)s", self.footer_data)

    def parse_filament_total(self) -> Optional[float]:
        return regex_find_float(
            r"; --- filament used: (%F) mm", self.footer_data
        )

    def parse_first_layer_extr_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; firstLayerNozzleTemp = (%F)", self.header_data
        )

    def parse_first_layer_bed_temp(self) -> Optional[float]:
        return regex_find_float(
            r"; firstLayerBedTemp = (%F)", self.header_data
        )


PPC_REGEX = (
    r"^; Pre-Processed for Cancel-Object support "
    r"by preprocess_cancellation (?P<version>v?\d+(?:\.\d+)*)"
)

def process_objects(file_path: str, slicer: BaseSlicer) -> bool:
    name = slicer.slicer_name
    if not slicer.has_objects():
        return False
    try:
        from preprocess_cancellation import (
            preprocess_slicer,
            preprocess_cura,
            preprocess_ideamaker,
            preprocess_m486
        )
    except ImportError:
        logger.info("Module 'preprocess-cancellation' failed to load")
        return False
    fname = os.path.basename(file_path)
    logger.info(
        f"Performing Object Processing on file: {fname}, sliced by {name}"
    )
    with tempfile.TemporaryDirectory() as tmp_dir_name:
        tmp_file = os.path.join(tmp_dir_name, fname)
        with open(file_path, 'r') as in_file:
            with open(tmp_file, 'w') as out_file:
                try:
                    if slicer.has_m486_objects:
                        processor = preprocess_m486
                    elif isinstance(slicer, PrusaSlicer):
                        processor = preprocess_slicer
                    elif isinstance(slicer, Cura):
                        processor = preprocess_cura
                    elif isinstance(slicer, IdeaMaker):
                        processor = preprocess_ideamaker
                    else:
                        logger.info(
                            f"Object Processing Failed, slicer {name}"
                            "not supported"
                        )
                        return False
                    for line in processor(in_file):
                        out_file.write(line)
                except Exception as e:
                    logger.info(f"Object processing failed: {e}")
                    return False
        if os.path.islink(file_path):
            file_path = os.path.realpath(file_path)
        shutil.move(tmp_file, file_path)
    return True

def run_gcode_processors(
    gc_file_path: str, slicer: BaseSlicer, processors: List[Dict[str, Any]]
) -> Tuple[List[str], bool]:
    reload_slicer_data: bool = False
    finished_procs: List[str] = []
    short_name = os.path.basename(gc_file_path)
    for proc_cfg in processors:
        name: str = "Unknown"
        try:
            name = proc_cfg["name"]
            version = proc_cfg.get("version", "v?")
            ident: Dict[str, Any] = proc_cfg.get("ident", {})
            if ident:
                regex: str = ident["regex"]
                loc: str = ident["location"]
                data = slicer.check_gcode_processor(regex, loc)
                if data is not None:
                    ver = data.get("version", "v?")
                    logger.info(
                        f"File {short_name} previously processed by {name} {ver}"
                    )
                    finished_procs.append(name)
                    continue
            if not proc_cfg.get("enabled", True):
                logger.info(f"Processor {name} is disabled")
                continue
            arglist: List[str] = []
            command = proc_cfg["command"]
            if callable(command):
                # Local file processor (preprocess_cancellation)
                if command(gc_file_path, slicer):
                    finished_procs.append(name)
                    reload_slicer_data = True
                continue
            elif isinstance(command, str):
                arglist = shlex.split(command)
            else:
                arglist = command
            assert isinstance(arglist, list)
            for idx, arg in enumerate(arglist):
                assert isinstance(arg, str)
                if arg == "{gcode_file_path}":
                    arglist[idx] = gc_file_path
            timeout: float = proc_cfg.get("timeout", 120.)
            assert isinstance(timeout, (int, float)) and timeout > 0.
            logger.info(
                f"Running processor {name} {version} on file {short_name}..."
            )
            ret = subprocess.run(arglist, capture_output=True, timeout=timeout)
        except Exception:
            logger.info(f"Processor {name} failed with error")
            logger.info(traceback.format_exc())
            continue
        if ret.returncode != 0:
            logger.info(f"File processor {name} failed with code {ret.returncode}")
            stdout = ret.stdout.decode(errors="ignore")
            stderr = ret.stderr.decode(errors="ignore")
            if stdout:
                logger.info(stdout)
            if stderr:
                logger.info(stderr)
        else:
            logger.info(f"File processor {name} successfully complete")
            finished_procs.append(name)
            reload_slicer_data = True
    return finished_procs, reload_slicer_data

def extract_metadata(
    file_path: str, processors: List[Dict[str, Any]]
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    proc_list: List[str] = []
    slicer = BaseSlicer.from_file(file_path)
    if processors:
        proc_list, reload = run_gcode_processors(file_path, slicer, processors)
        if reload:
            slicer = BaseSlicer.from_file(file_path)
    metadata["size"] = os.path.getsize(file_path)
    metadata["modified"] = os.path.getmtime(file_path)
    metadata["uuid"] = str(uuid.uuid4())
    metadata["file_processors"] = proc_list
    metadata["slicer"] = slicer.slicer_name
    metadata["slicer_version"] = slicer.slicer_version
    metadata.update(slicer.run_parsers())
    return metadata

def extract_ufp(ufp_path: str, dest_path: str) -> None:
    if not os.path.isfile(ufp_path):
        logger.info(f"UFP file Not Found: {ufp_path}")
        sys.exit(-1)
    thumb_name = os.path.splitext(
        os.path.basename(dest_path))[0] + ".png"
    dest_thumb_dir = os.path.join(os.path.dirname(dest_path), ".thumbs")
    dest_thumb_path = os.path.join(dest_thumb_dir, thumb_name)
    try:
        with tempfile.TemporaryDirectory() as tmp_dir_name:
            tmp_thumb_path = ""
            with zipfile.ZipFile(ufp_path) as zf:
                tmp_model_path = zf.extract(
                    UFP_MODEL_PATH, path=tmp_dir_name)
                if UFP_THUMB_PATH in zf.namelist():
                    tmp_thumb_path = zf.extract(
                        UFP_THUMB_PATH, path=tmp_dir_name)
            if os.path.islink(dest_path):
                dest_path = os.path.realpath(dest_path)
            shutil.move(tmp_model_path, dest_path)
            if tmp_thumb_path:
                if not os.path.exists(dest_thumb_dir):
                    os.mkdir(dest_thumb_dir)
                shutil.move(tmp_thumb_path, dest_thumb_path)
    except Exception:
        logger.info(traceback.format_exc())
        sys.exit(-1)
    try:
        os.remove(ufp_path)
    except Exception:
        logger.info(f"Error removing ufp file: {ufp_path}")

def main(config: Dict[str, Any]) -> None:
    gc_path: str = config["gcode_dir"]
    filename: str = config["filename"]
    file_path = os.path.join(gc_path, filename)
    processors: List[Dict[str, Any]] = config.get("processors", [])
    processors.append(
        {
            "name": "preprocess_cancellation",
            "command": process_objects,
            "enabled": config.get("check_objects", False),
            "ident": {
                "regex": PPC_REGEX,
                "location": "header"
            }
        }
    )
    ufp = config.get("ufp_path")
    if ufp is not None:
        extract_ufp(ufp, file_path)
    metadata: Dict[str, Any] = {}
    if not os.path.isfile(file_path):
        logger.info(f"File Not Found: {file_path}")
        sys.exit(-1)
    try:
        metadata = extract_metadata(file_path, processors)
    except Exception:
        logger.info(traceback.format_exc())
        sys.exit(-1)
    fd = sys.stdout.fileno()
    data = json.dumps(
        {'file': filename, 'metadata': metadata}).encode()
    while data:
        try:
            ret = os.write(fd, data)
        except OSError:
            continue
        data = data[ret:]


if __name__ == "__main__":
    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="GCode Metadata Extraction Utility")
    parser.add_argument(
        "-c", "--config", metavar='<config_file>', default=None,
        help="Optional json configuration file for metadata.py"
    )
    parser.add_argument(
        "-f", "--filename", metavar='<filename>', default=None,
        help="name gcode file to parse")
    parser.add_argument(
        "-p", "--path", metavar='<path>', default=None,
        help="optional path to folder containing the file"
    )
    parser.add_argument(
        "-u", "--ufp", metavar="<ufp file>", default=None,
        help="optional path of ufp file to extract"
    )
    parser.add_argument(
        "-o", "--check-objects", dest='check_objects', action='store_true',
        help="process gcode file for exclude object functionality")
    args = parser.parse_args()
    config: Dict[str, Any] = {}
    if args.config is None:
        if args.filename is None:
            logger.info(
                "The '--filename' (-f) option must be specified when "
                " --config is not set"
            )
            sys.exit(-1)
        config["filename"] = args.filename
        config["gcode_dir"] = args.path
        config["ufp_path"] = args.ufp
        config["check_objects"] = args.check_objects
    else:
        # Config file takes priority over command line options
        try:
            with open(args.config, "r") as f:
                config = (json.load(f))
        except Exception:
            logger.info(traceback.format_exc())
            sys.exit(-1)
        if config.get("filename") is None:
            logger.info("The 'filename' field must be present in the configuration")
            sys.exit(-1)
    if config.get("gcode_dir") is None:
        config["gcode_dir"] = os.path.abspath(os.path.dirname(__file__))
    main(config)
