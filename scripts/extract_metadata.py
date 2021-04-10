#!/usr/bin/env python3
# GCode metadata extraction utility
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import argparse
import re
import os
import sys
import base64
import traceback
import io
from PIL import Image

def log_to_stderr(msg):
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

# regex helpers
def _regex_find_floats(pattern, data, strict=False):
    # If strict is enabled, pattern requires a floating point
    # value, otherwise it can be an integer value
    fptrn = r'\d+\.\d*' if strict else r'\d+\.?\d*'
    matches = re.findall(pattern, data)
    if matches:
        # return the maximum height value found
        try:
            return [float(h) for h in re.findall(
                fptrn, " ".join(matches))]
        except Exception:
            pass
    return []

def _regex_find_ints(pattern, data):
    matches = re.findall(pattern, data)
    if matches:
        # return the maximum height value found
        try:
            return [int(h) for h in re.findall(
                r'\d+', " ".join(matches))]
        except Exception:
            pass
    return []

def _regex_find_first(pattern, data, cast=float):
    match = re.search(pattern, data)
    val = None
    if match:
        try:
            val = cast(match.group(1))
        except Exception:
            return None
    return val

# Slicer parsing implementations
class BaseSlicer(object):
    def __init__(self, file_path):
        self.path = file_path
        self.header_data = self.footer_data = None
        self.layer_height = None

    def set_data(self, header_data, footer_data, fsize):
        self.header_data = header_data
        self.footer_data = footer_data
        self.size = fsize

    def _parse_min_float(self, pattern, data, strict=False):
        result = _regex_find_floats(pattern, data, strict)
        if result:
            return min(result)
        else:
            return None

    def _parse_max_float(self, pattern, data, strict=False):
        result = _regex_find_floats(pattern, data, strict)
        if result:
            return max(result)
        else:
            return None

    def check_identity(self, data):
        return None

    def parse_gcode_start_byte(self):
        m = re.search(r"\n[MG]\d+\s.*\n", self.header_data)
        if m is None:
            return None
        return m.start()

    def parse_gcode_end_byte(self):
        rev_data = self.footer_data[::-1]
        m = re.search(r"\n.*\s\d+[MG]\n", rev_data)
        if m is None:
            return None
        return self.size - m.start()

    def parse_first_layer_height(self):
        return None

    def parse_layer_height(self):
        return None

    def parse_object_height(self):
        return None

    def parse_filament_total(self):
        return None

    def parse_estimated_time(self):
        return None

    def parse_first_layer_bed_temp(self):
        return None

    def parse_first_layer_extr_temp(self):
        return None

    def parse_thumbnails(self):
        return None

class UnknownSlicer(BaseSlicer):
    def check_identity(self, data):
        return {'slicer': "Unknown"}

    def parse_first_layer_height(self):
        return self._parse_min_float(r"G1\sZ\d+\.\d*", self.header_data)

    def parse_object_height(self):
        return self._parse_max_float(r"G1\sZ\d+\.\d*", self.footer_data)

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"M109 S(\d+\.?\d*)", self.header_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"M190 S(\d+\.?\d*)", self.header_data)

class PrusaSlicer(BaseSlicer):
    def check_identity(self, data):
        match = re.search(r"PrusaSlicer\s(.*)\son", data)
        if match:
            return {
                'slicer': "PrusaSlicer",
                'slicer_version': match.group(1)
            }
        return None

    def parse_first_layer_height(self):
        # Check percentage
        pct = _regex_find_first(
            r"; first_layer_height = (\d+)%", self.footer_data)
        if pct is not None:
            if self.layer_height is None:
                # Failed to parse the original layer height, so it is not
                # possible to calculate a percentage
                return None
            return round(pct / 100. * self.layer_height, 6)
        return _regex_find_first(
            r"; first_layer_height = (\d+\.?\d*)", self.footer_data)

    def parse_layer_height(self):
        self.layer_height = _regex_find_first(
            r"; layer_height = (\d+\.?\d*)", self.footer_data)
        return self.layer_height

    def parse_object_height(self):
        matches = re.findall(
            r";BEFORE_LAYER_CHANGE\n(?:.*\n)?;(\d+\.?\d*)", self.footer_data)
        if matches:
            try:
                matches = [float(m) for m in matches]
            except Exception:
                pass
            else:
                return max(matches)
        return self._parse_max_float(r"G1\sZ\d+\.\d*\sF", self.footer_data)

    def parse_filament_total(self):
        return _regex_find_first(
            r"filament\sused\s\[mm\]\s=\s(\d+\.\d*)", self.footer_data)

    def parse_estimated_time(self):
        time_match = re.search(
            r';\sestimated\sprinting\stime.*', self.footer_data)
        if not time_match:
            return None
        total_time = 0
        time_match = time_match.group()
        time_patterns = [(r"(\d+)d", 24*60*60), (r"(\d+)h", 60*60),
                         (r"(\d+)m", 60), (r"(\d+)s", 1)]
        try:
            for pattern, multiplier in time_patterns:
                t = re.search(pattern, time_match)
                if t:
                    total_time += int(t.group(1)) * multiplier
        except Exception:
            return None
        return round(total_time, 2)

    def parse_thumbnails(self):
        thumb_matches = re.findall(
            r"; thumbnail begin[;/\+=\w\s]+?; thumbnail end", self.header_data)
        if not thumb_matches:
            return None
        thumb_dir = os.path.join(os.path.dirname(self.path), "thumbs")
        if not os.path.exists(thumb_dir):
            try:
                os.mkdir(thumb_dir)
            except Exception:
                log_to_stderr(f"Unable to create thumb dir: {thumb_dir}")
                return
        thumb_base = os.path.splitext(os.path.basename(self.path))[0]
        parsed_matches = []
        for match in thumb_matches:
            lines = re.split(r"\r?\n", match.replace('; ', ''))
            info = _regex_find_ints(r".*", lines[0])
            data = "".join(lines[1:-1])
            if len(info) != 3:
                log_to_stderr(
                    f"MetadataError: Error parsing thumbnail"
                    f" header: {lines[0]}")
                continue
            if len(data) != info[2]:
                log_to_stderr(
                    f"MetadataError: Thumbnail Size Mismatch: "
                    f"detected {info[2]}, actual {len(data)}")
                continue
            thumb_name = f"{thumb_base}-{info[0]}x{info[1]}.png"
            thumb_path = os.path.join(thumb_dir, thumb_name)
            rel_thumb_path = os.path.join("thumbs", thumb_name)
            with open(thumb_path, "wb") as f:
                f.write(base64.b64decode(data.encode()))
            parsed_matches.append({
                'width': info[0], 'height': info[1],
                'size': info[2], 'data': data,
                'relative_path': rel_thumb_path})
        return parsed_matches

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"; first_layer_temperature = (\d+\.?\d*)", self.footer_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"; first_layer_bed_temperature = (\d+\.?\d*)", self.footer_data)

class Slic3rPE(PrusaSlicer):
    def check_identity(self, data):
        match = re.search(r"Slic3r\sPrusa\sEdition\s(.*)\son", data)
        if match:
            return {
                'slicer': "Slic3r PE",
                'slicer_version': match.group(1)
            }
        return None

    def parse_filament_total(self):
        return _regex_find_first(
            r"filament\sused\s=\s(\d+\.\d+)mm", self.footer_data)

    def parse_thumbnails(self):
        return None

class Slic3r(Slic3rPE):
    def check_identity(self, data):
        match = re.search(r"Slic3r\s(\d.*)\son", data)
        if match:
            return {
                'slicer': "Slic3r",
                'slicer_version': match.group(1)
            }
        return None

    def parse_estimated_time(self):
        return None

class SuperSlicer(PrusaSlicer):
    def check_identity(self, data):
        match = re.search(r"SuperSlicer\s(.*)\son", data)
        if match:
            return {
                'slicer': "SuperSlicer",
                'slicer_version': match.group(1)
            }
        return None

class Cura(PrusaSlicer):
    def check_identity(self, data):
        match = re.search(r"Cura_SteamEngine\s(.*)", data)
        if match:
            return {
                'slicer': "Cura",
                'slicer_version': match.group(1)
            }
        return None

    def parse_first_layer_height(self):
        return _regex_find_first(r";MINZ:(\d+\.?\d*)", self.header_data)

    def parse_layer_height(self):
        self.layer_height = _regex_find_first(
            r";Layer\sheight:\s(\d+\.?\d*)", self.header_data)
        return self.layer_height

    def parse_object_height(self):
        return _regex_find_first(r";MAXZ:(\d+\.?\d*)", self.header_data)

    def parse_filament_total(self):
        filament = _regex_find_first(
            r";Filament\sused:\s(\d+\.?\d*)m", self.header_data)
        if filament is not None:
            filament *= 1000
        return filament

    def parse_estimated_time(self):
        return self._parse_max_float(r";TIME:.*", self.header_data)

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"M109 S(\d+\.?\d*)", self.header_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"M190 S(\d+\.?\d*)", self.header_data)

    def parse_thumbnails(self):
        # Attempt to parse thumbnails from file metadata
        thumbs = super().parse_thumbnails()
        if thumbs is not None:
            return thumbs
        # Check for thumbnails extracted from the ufp
        thumb_dir = os.path.join(os.path.dirname(self.path), "thumbs")
        thumb_base = os.path.splitext(os.path.basename(self.path))[0]
        thumb_path = os.path.join(thumb_dir, f"{thumb_base}.png")
        rel_path_full = os.path.join("thumbs", f"{thumb_base}.png")
        rel_path_small = os.path.join("thumbs", f"{thumb_base}-32x32.png")
        thumb_path_small = os.path.join(thumb_dir, f"{thumb_base}-32x32.png")
        if not os.path.isfile(thumb_path):
            return None
        # read file
        thumbs = []
        try:
            with open(thumb_path, 'rb') as thumb_file:
                fbytes = thumb_file.read()
                with Image.open(io.BytesIO(fbytes)) as im:
                    thumb_full_b64 = base64.b64encode(fbytes).decode()
                    thumbs.append({
                        'width': im.width, 'height': im.height,
                        'size': len(thumb_full_b64), 'data': thumb_full_b64,
                        'relative_path': rel_path_full
                    })
                    # Create 32x32 thumbnail
                    im.thumbnail((32, 32), Image.ANTIALIAS)
                    tmp_thumb = io.BytesIO()
                    im.save(tmp_thumb, format="PNG")
                    im.save(thumb_path_small, format="PNG")
                    thumb_small_b64 = base64.b64encode(
                        tmp_thumb.getbuffer()).decode()
                    tmp_thumb.close()
                    thumbs.insert(0, {
                        'width': im.width, 'height': im.height,
                        'size': len(thumb_small_b64), 'data': thumb_small_b64,
                        'relative_path': rel_path_small
                    })
        except Exception as e:
            log_to_stderr(str(e))
            return None
        return thumbs

class Simplify3D(BaseSlicer):
    def check_identity(self, data):
        match = re.search(r"Simplify3D\(R\)\sVersion\s(.*)", data)
        if match:
            return {
                'slicer': "Simplify3D",
                'slicer_version': match.group(1)
            }
        return None

    def parse_first_layer_height(self):
        return self._parse_min_float(r"G1\sZ\d+\.\d*", self.header_data)

    def parse_layer_height(self):
        self.layer_height =  _regex_find_first(
            r";\s+layerHeight,(\d+\.?\d*)", self.header_data)
        return self.layer_height

    def parse_object_height(self):
        return self._parse_max_float(r"G1\sZ\d+\.\d*", self.footer_data)

    def parse_filament_total(self):
        return _regex_find_first(
            r";\s+Filament\slength:\s(\d+\.?\d*)\smm", self.footer_data)

    def parse_estimated_time(self):
        time_match = re.search(
            r';\s+Build time:.*', self.footer_data)
        if not time_match:
            return None
        total_time = 0
        time_match = time_match.group()
        time_patterns = [(r"(\d+)\shours", 60*60), (r"(\d+)\smin", 60),
                         (r"(\d+)\ssec", 1)]
        try:
            for pattern, multiplier in time_patterns:
                t = re.search(pattern, time_match)
                if t:
                    total_time += int(t.group(1)) * multiplier
        except Exception:
            return None
        return round(total_time, 2)

    def _get_temp_items(self, pattern):
        match = re.search(pattern, self.header_data)
        if match is None:
            return []
        return match.group().split(",")[1:]

    def _get_first_layer_temp(self, heater):
        heaters = self._get_temp_items(r"temperatureName.*")
        temps = self._get_temp_items(r"temperatureSetpointTemperatures.*")
        for h, temp in zip(heaters, temps):
            if h == heater:
                try:
                    return float(temp)
                except Exception:
                    return None
        return None

    def parse_first_layer_extr_temp(self):
        return self._get_first_layer_temp("Extruder 1")

    def parse_first_layer_bed_temp(self):
        return self._get_first_layer_temp("Heated Bed")

class KISSlicer(BaseSlicer):
    def check_identity(self, data):
        match = re.search(r";\sKISSlicer", data)
        if match:
            ident = {'slicer': "KISSlicer"}
            vmatch = re.search(r";\sversion\s(.*)", data)
            if vmatch:
                version = vmatch.group(1).replace(" ", "-")
                ident['slicer_version'] = version
            return ident
        return None

    def parse_first_layer_height(self):
        return _regex_find_first(
            r";\s+first_layer_thickness_mm\s=\s(\d+\.?\d*)", self.header_data)

    def parse_layer_height(self):
        self.layer_height = _regex_find_first(
            r";\s+max_layer_thickness_mm\s=\s(\d+\.?\d*)", self.header_data)
        return self.layer_height

    def parse_object_height(self):
        return self._parse_max_float(
            r";\sEND_LAYER_OBJECT\sz.*", self.footer_data)

    def parse_filament_total(self):
        filament = _regex_find_floats(
            r";\s+Ext\s.*mm", self.footer_data, strict=True)
        if filament:
            return sum(filament)
        return None

    def parse_estimated_time(self):
        time = _regex_find_first(
            r";\sCalculated.*Build\sTime:\s(\d+\.?\d*)\sminutes",
            self.footer_data)
        if time is not None:
            time *= 60
            return round(time, 2)
        return None

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"; first_layer_C = (\d+\.?\d*)", self.header_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"; bed_C = (\d+\.?\d*)", self.header_data)


class IdeaMaker(BaseSlicer):
    def check_identity(self, data):
        match = re.search(r"\sideaMaker\s(.*),", data)
        if match:
            return {
                'slicer': "IdeaMaker",
                'slicer_version': match.group(1)
            }
        return None

    def parse_first_layer_height(self):
        layer_info = _regex_find_floats(
            r";LAYER:0\s*.*\s*;HEIGHT.*", self.header_data)
        if len(layer_info) >= 3:
            return layer_info[2]
        return None

    def parse_layer_height(self):
        layer_info = _regex_find_floats(
            r";LAYER:1\s*.*\s*;HEIGHT.*", self.header_data)
        if len(layer_info) >= 3:
            self.layer_height = layer_info[2]
            return self.layer_height
        return None

    def parse_object_height(self):
        bounds = _regex_find_floats(
            r";Bounding Box:.*", self.header_data)
        if len(bounds) >= 6:
            return bounds[5]
        return None

    def parse_filament_total(self):
        filament = _regex_find_floats(
            r";Material.\d\sUsed:.*", self.footer_data, strict=True)
        if filament:
            return sum(filament)
        return None

    def parse_estimated_time(self):
        return _regex_find_first(
            r";Print\sTime:\s(\d+\.?\d*)", self.footer_data)

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"M109 T0 S(\d+\.?\d*)", self.header_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"M190 S(\d+\.?\d*)", self.header_data)

class IceSL(BaseSlicer):
    def check_identity(self, data):
        match = re.search(r"; <IceSL.*>", data)
        if match:
            return {'slicer': "IceSL"}
        return None

    def parse_first_layer_height(self):
        return _regex_find_first(
            r"; z_layer_height_first_layer_mm :\s+(\d+\.\d+)",
            self.header_data, float)

    def parse_layer_height(self):
        self.layer_height = _regex_find_first(
            r"; z_layer_height_mm :\s+(\d+\.\d+)",
            self.header_data, float)
        return self.layer_height

    def parse_object_height(self):
        return self._parse_max_float(
            r"G0 F\d+ Z\d+\.\d+", self.footer_data, strict=True)

    def parse_first_layer_extr_temp(self):
        return _regex_find_first(
            r"; extruder_temp_degree_c_0 :\s+(\d+\.?\d*)", self.header_data)

    def parse_first_layer_bed_temp(self):
        return _regex_find_first(
            r"; bed_temp_degree_c :\s+(\d+\.?\d*)", self.header_data)


READ_SIZE = 512 * 1024
SUPPORTED_SLICERS = [
    PrusaSlicer, Slic3rPE, Slic3r, SuperSlicer,
    Cura, Simplify3D, KISSlicer, IdeaMaker, IceSL]
SUPPORTED_DATA = [
    'layer_height', 'first_layer_height', 'object_height',
    'filament_total', 'estimated_time', 'thumbnails',
    'first_layer_bed_temp', 'first_layer_extr_temp',
    'gcode_start_byte', 'gcode_end_byte']

def extract_metadata(file_path):
    metadata = {}
    slicers = [s(file_path) for s in SUPPORTED_SLICERS]
    header_data = footer_data = slicer = None
    size = os.path.getsize(file_path)
    metadata['size'] = size
    metadata['modified'] = os.path.getmtime(file_path)
    with open(file_path, 'r') as f:
        # read the default size, which should be enough to
        # identify the slicer
        header_data = f.read(READ_SIZE)
        for s in slicers:
            ident = s.check_identity(header_data)
            if ident is not None:
                slicer = s
                metadata.update(ident)
                break
        if slicer is None:
            slicer = UnknownSlicer(file_path)
            metadata['slicer'] = "Unknown"
        if size > READ_SIZE * 2:
            f.seek(size - READ_SIZE)
            footer_data = f.read()
        elif size > READ_SIZE:
            remaining = size - READ_SIZE
            footer_data = header_data[remaining - READ_SIZE:] + f.read()
        else:
            footer_data = header_data
        slicer.set_data(header_data, footer_data, size)
        for key in SUPPORTED_DATA:
            func = getattr(slicer, "parse_" + key)
            result = func()
            if result is not None:
                metadata[key] = result
    return metadata

def main(path, filename):
    file_path = os.path.join(path, filename)
    metadata = {}
    if not os.path.isfile(file_path):
        log_to_stderr(f"File Not Found: {file_path}")
        sys.exit(-1)
    try:
        metadata = extract_metadata(file_path)
    except Exception:
        log_to_stderr(traceback.format_exc())
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
        "-f", "--filename", metavar='<filename>',
        help="name gcode file to parse")
    parser.add_argument(
        "-p", "--path", default=os.path.abspath(os.path.dirname(__file__)),
        metavar='<path>',
        help="optional absolute path for file"
    )
    args = parser.parse_args()
    main(args.path, args.filename)
