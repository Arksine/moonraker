#! /usr/bin/python3
# Create system dependencies json file from the install script
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
from __future__ import annotations
import argparse
import pathlib
import json
import re
from typing import List, Dict

def make_sysdeps(input: str, output: str, distro: str, truncate: bool) -> None:
    sysdeps: Dict[str, List[str]] = {}
    outpath = pathlib.Path(output).expanduser().resolve()
    if outpath.is_file() and not truncate:
        sysdeps = json.loads(outpath.read_bytes())
    inst_path: pathlib.Path = pathlib.Path(input).expanduser().resolve()
    if not inst_path.is_file():
        raise Exception(f"Unable to locate install script: {inst_path}")
    data = inst_path.read_text()
    plines: List[str] = re.findall(r'PKGLIST="(.*)"', data)
    plines = [p.lstrip("${PKGLIST}").strip() for p in plines]
    packages: List[str] = []
    for line in plines:
        packages.extend(line.split())
    sysdeps[distro] = packages
    outpath.write_text(json.dumps(sysdeps, indent=4))


if __name__ == "__main__":
    def_path = pathlib.Path(__file__).parent
    desc = (
        "make_sysdeps - generate system dependency json file from an install script"
    )
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        "-i", "--input", metavar="<install script>",
        help="path of the install script to read",
        default=f"{def_path}/install-moonraker.sh"
    )
    parser.add_argument(
        "-o", "--output", metavar="<output file>",
        help="path of the system dependency file to write",
        default=f"{def_path}/system-dependencies.json"
    )
    parser.add_argument(
        "-d", "--distro", metavar="<linux distro>",
        help="linux distro for dependencies", default="debian"
    )
    parser.add_argument(
        "-t", "--truncate", action="store_true",
        help="truncate output file"
    )
    args = parser.parse_args()
    make_sysdeps(args.input, args.output, args.distro, args.truncate)
