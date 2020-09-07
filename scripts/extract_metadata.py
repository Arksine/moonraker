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
import time
import traceback

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

# Slicer parsing implementations
class BaseSlicer(object):
    def __init__(self, name, id_pattern):
        self.name = name
        self.id_pattern = id_pattern
        self.header_data = self.footer_data = self.log = None

    def set_data(self, header_data, footer_data, log):
        self.header_data = header_data
        self.footer_data = footer_data
        self.log = log

    def get_name(self):
        return self.name

    def get_id_pattern(self):
        return self.id_pattern

    def _parse_min_float(self, pattern, data):
        result = _regex_find_floats(pattern, data)
        if result:
            return min(result)
        else:
            return None

    def _parse_max_float(self, pattern, data):
        result = _regex_find_floats(pattern, data)
        if result:
            return max(result)
        else:
            return None

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
    def __init__(self, name="Unknown", id_pattern=r""):
        super(UnknownSlicer, self).__init__(name, id_pattern)

    def parse_first_layer_height(self):
        return self._parse_min_float(r"G1\sZ\d+\.\d*", self.header_data)

    def parse_object_height(self):
        return self._parse_max_float(r"G1\sZ\d+\.\d*", self.footer_data)

class PrusaSlicer(BaseSlicer):
    def __init__(self, name="PrusaSlicer", id_pattern=r"PrusaSlicer\s.*\son"):
        super(PrusaSlicer, self).__init__(name, id_pattern)

    def parse_first_layer_height(self):
        return self._parse_min_float(
            r"; first_layer_height =.*", self.footer_data)

    def parse_layer_height(self):
        return self._parse_min_float(r"; layer_height =.*", self.footer_data)

    def parse_object_height(self):
        return self._parse_max_float(r"G1\sZ\d+\.\d*\sF", self.footer_data)

    def parse_filament_total(self):
        return self._parse_max_float(
            r"filament\sused\s\[mm\]\s=\s\d+\.\d*", self.footer_data)

    def parse_estimated_time(self):
        time_matches = re.findall(
            r';\sestimated\sprinting\stime.*', self.footer_data)
        if not time_matches:
            return None
        total_time = 0
        time_match = time_matches[0]
        time_patterns = [(r"\d+d", 24*60*60), (r"\d+h", 60*60),
                         (r"\d+m", 60), (r"\d+s", 1)]
        for pattern, multiplier in time_patterns:
            t = _regex_find_ints(pattern, time_match)
            if t:
                total_time += max(t) * multiplier
        return round(total_time, 2)

    def parse_thumbnails(self):
        thumb_matches = re.findall(
            r"; thumbnail begin[;/\+=\w\s]+?; thumbnail end", self.header_data)
        if not thumb_matches:
            return None
        parsed_matches = []
        for match in thumb_matches:
            lines = re.split(r"\r?\n", match.replace('; ', ''))
            info = _regex_find_ints(r".*", lines[0])
            data = "".join(lines[1:-1])
            if len(info) != 3:
                self.log.append(
                    f"MetadataError: Error parsing thumbnail"
                    f" header: {lines[0]}")
                continue
            if len(data) != info[2]:
                self.log.append(
                    f"MetadataError: Thumbnail Size Mismatch: "
                    f"detected {info[2]}, actual {len(data)}")
                continue
            parsed_matches.append({
                'width': info[0], 'height': info[1],
                'size': info[2], 'data': data})

        return parsed_matches

class Slic3rPE(PrusaSlicer):
    def __init__(self, name="Slic3r PE",
                 id_pattern=r"Slic3r\sPrusa\sEdition\s.*\son"):
        super(Slic3rPE, self).__init__(name, id_pattern)

    def parse_filament_total(self):
        return self._parse_max_float(
            r"filament\sused\s=\s\d+\.\d+mm", self.footer_data)

    def parse_thumbnails(self):
        return None

class Slic3r(Slic3rPE):
    def __init__(self, name="Slic3r", id_pattern=r"Slic3r\s\d.*\son"):
        super(Slic3r, self).__init__(name, id_pattern)

    def parse_estimated_time(self):
        return None

class SuperSlicer(PrusaSlicer):
    def __init__(self, name="SuperSlicer", id_pattern=r"SuperSlicer\s.*\son"):
        super(SuperSlicer, self).__init__(name, id_pattern)

class Cura(BaseSlicer):
    def __init__(self, name="Cura", id_pattern=r"Cura_SteamEngine.*"):
        super(Cura, self).__init__(name, id_pattern)

    def parse_first_layer_height(self):
        return self._parse_min_float(r";MINZ:.*", self.header_data)

    def parse_layer_height(self):
        return self._parse_min_float(r";Layer\sheight:.*", self.header_data)

    def parse_object_height(self):
        return self._parse_max_float(r";MAXZ:.*", self.header_data)

    def parse_filament_total(self):
        filament = self._parse_max_float(
            r";Filament\sused:.*", self.header_data)
        if filament is not None:
            filament *= 1000
        return filament

    def parse_estimated_time(self):
        return self._parse_max_float(r";TIME:.*", self.header_data)


class Simplify3D(BaseSlicer):
    def __init__(self, name="Simplify3D", id_pattern=r"Simplify3D\(R\)"):
        super(Simplify3D, self).__init__(name, id_pattern)

    def parse_first_layer_height(self):
        return self._parse_min_float(r"G1\sZ\d+\.\d*", self.header_data)

    def parse_layer_height(self):
        return self._parse_min_float(r";\s+layerHeight,.*", self.header_data)

    def parse_object_height(self):
        return self._parse_max_float(r"G1\sZ\d+\.\d*", self.footer_data)

    def parse_filament_total(self):
        return self._parse_max_float(
            r";\s+Filament\slength:.*mm", self.footer_data)

    def parse_estimated_time(self):
        time_matches = re.findall(
            r';\s+Build time:.*', self.footer_data)
        if not time_matches:
            return None
        total_time = 0
        time_match = time_matches[0]
        time_patterns = [(r"\d+\shours", 60*60), (r"\d+\smin", 60),
                         (r"\d+\ssec", 1)]
        for pattern, multiplier in time_patterns:
            t = _regex_find_ints(pattern, time_match)
            if t:
                total_time += max(t) * multiplier
        return round(total_time, 2)


class KISSlicer(BaseSlicer):
    def __init__(self, name="KISSlicer", id_pattern=r";\sKISSlicer"):
        super(KISSlicer, self).__init__(name, id_pattern)

    def parse_first_layer_height(self):
        return self._parse_min_float(
            r";\s+first_layer_thickness_mm\s=\s\d.*", self.header_data)

    def parse_layer_height(self):
        return self._parse_min_float(
            r";\s+max_layer_thickness_mm\s=\s\d.*", self.header_data)

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
        time = self._parse_max_float(
            r";\sCalculated.*Build\sTime:.*", self.footer_data)
        if time is not None:
            time *= 60
        return round(time, 2)


class IdeaMaker(BaseSlicer):
    def __init__(self, name="IdeaMaker", id_pattern=r"\sideaMaker\s.*,",):
        super(IdeaMaker, self).__init__(name, id_pattern)

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
            return layer_info[2]
        return None

    def parse_object_height(self):
        bounds = _regex_find_floats(
            r";Bounding Box:.*", self.footer_data)
        if len(bounds) >= 6:
            return bounds[5]
        return None

    def parse_filament_total(self):
        filament = _regex_find_floats(
            r";Material.\d\sUsed:.*", self.header_data, strict=True)
        if filament:
            return sum(filament)
        return None

    def parse_estimated_time(self):
        return self._parse_max_float(r";Print\sTime:.*", self.footer_data)

    def parse_thumbnails(self):
        return None


READ_SIZE = 512 * 1024
SUPPORTED_SLICERS = [
    PrusaSlicer, Slic3rPE, Slic3r, SuperSlicer,
    Cura, Simplify3D, KISSlicer, IdeaMaker]
SUPPORTED_DATA = [
    'first_layer_height', 'layer_height', 'object_height',
    'filament_total', 'estimated_time', 'thumbnails']

def extract_metadata(file_path, log):
    metadata = {}
    slicers = [s() for s in SUPPORTED_SLICERS]
    header_data = footer_data = slicer = None
    size = os.path.getsize(file_path)
    metadata['size'] = size
    metadata['modified'] = time.ctime(os.path.getmtime(file_path))
    with open(file_path, 'r') as f:
        # read the default size, which should be enough to
        # identify the slicer
        header_data = f.read(READ_SIZE)
        for s in slicers:
            if re.search(s.get_id_pattern(), header_data) is not None:
                slicer = s
                break
        if slicer is None:
            slicer = UnknownSlicer()
        metadata['slicer'] = slicer.get_name()
        if size > READ_SIZE * 2:
            f.seek(size - READ_SIZE)
            footer_data = f.read()
        elif size > READ_SIZE:
            remaining = size - READ_SIZE
            footer_data = header_data[remaining - READ_SIZE:] + f.read()
        else:
            footer_data = header_data
        slicer.set_data(header_data, footer_data, log)
        for key in SUPPORTED_DATA:
            func = getattr(slicer, "parse_" + key)
            result = func()
            if result is not None:
                metadata[key] = result
    return metadata

def main(path, filename):
    file_path = os.path.join(path, filename)
    log = []
    metadata = {}
    if not os.path.isfile(file_path):
        log.append(f"File Not Found: {file_path}")
    else:
        try:
            metadata = extract_metadata(file_path, log)
        except Exception:
            log.append(traceback.format_exc())
    fd = sys.stdout.fileno()
    data = json.dumps(
        {'file': filename, 'log': log, 'metadata': metadata}).encode()
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
