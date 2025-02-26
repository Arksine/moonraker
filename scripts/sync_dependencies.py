#! /usr/bin/python3
# Script for syncing package dependencies and python reqs
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import argparse
import pathlib
import tomllib
import json
import ast
from io import StringIO, TextIOBase
from typing import Dict, List, Iterator

MAX_LINE_LENGTH = 88
SCRIPTS_PATH = pathlib.Path(__file__).parent
INST_PKG_HEADER = "# *** AUTO GENERATED OS PACKAGE SCRIPT START ***"
INST_PKG_FOOTER = "# *** AUTO GENERATED OS PACKAGE SCRIPT END ***"
DEPS_HEADER = "# *** SYSTEM DEPENDENCIES START ***"
DEPS_FOOTER = "# *** SYSTEM DEPENDENCIES END ***"

def gen_pkg_list(values: List[str], indent: int = 0) -> Iterator[str]:
    idt = " " * indent
    if not values:
        return
    current_line = f"{idt}\"{values.pop(0)}\","
    for val in values:
        if len(current_line) + len(val) + 4 > MAX_LINE_LENGTH:
            yield current_line + "\n"
            current_line = f"{idt}\"{val}\","
        else:
            current_line += f" \"{val}\","
    yield current_line.rstrip(",") + "\n"

def write_parser_script(sys_deps: Dict[str, List[str]], out_hdl: TextIOBase) -> None:
    parser_file = SCRIPTS_PATH.parent.joinpath("moonraker/utils/sysdeps_parser.py")
    out_hdl.write("    get_pkgs_script=$(cat << EOF\n")
    with parser_file.open("r") as f:
        for line in f:
            if not line.strip().startswith("#"):
                out_hdl.write(line)
    out_hdl.write(f"{DEPS_HEADER}\n")
    out_hdl.write("system_deps = {\n")
    for distro, packages in sys_deps.items():
        indent = " " * 4
        out_hdl.write(f"{indent}\"{distro}\": [\n")
        # Write packages
        for line in gen_pkg_list(packages, 8):
            out_hdl.write(line)
        out_hdl.write(f"{indent}],\n")
    out_hdl.write("}\n")
    out_hdl.write(f"{DEPS_FOOTER}\n")
    out_hdl.writelines("""
parser = SysDepsParser()
pkgs = parser.parse_dependencies(system_deps)
if pkgs:
    print(' '.join(pkgs), end="")
exit(0)
EOF
)
""".lstrip())

def sync_packages() -> int:
    inst_script = SCRIPTS_PATH.joinpath("install-moonraker.sh")
    sys_deps_file = SCRIPTS_PATH.joinpath("system-dependencies.json")
    prev_deps: Dict[str, List[str]] = {}
    new_deps: Dict[str, List[str]] = json.loads(sys_deps_file.read_bytes())
    # Copy install script in memory.
    install_data = StringIO()
    prev_deps_str: str = ""
    skip_data = False
    collect_deps = False
    with inst_script.open("r") as inst_file:
        for line in inst_file:
            cur_line = line.strip()
            if not skip_data:
                install_data.write(line)
            else:
                # parse current dependencies
                if collect_deps:
                    if line.rstrip() == DEPS_FOOTER:
                        collect_deps = False
                    else:
                        prev_deps_str += line
                elif line.rstrip() == DEPS_HEADER:
                    collect_deps = True
            if cur_line == INST_PKG_HEADER:
                skip_data = True
            elif cur_line == INST_PKG_FOOTER:
                skip_data = False
                install_data.write(line)
    if prev_deps_str:
        try:
            # start at the beginning of the dict literal
            idx = prev_deps_str.find("{")
            if idx > 0:
                prev_deps = ast.literal_eval(prev_deps_str[idx:])
        except Exception:
            pass
    print(f"Previous Dependencies:\n{prev_deps}")
    # Check if an update is necessary
    if set(prev_deps.keys()) == set(new_deps.keys()):
        for distro, prev_pkgs in prev_deps.items():
            new_pkgs = new_deps[distro]
            if set(prev_pkgs) != set(new_pkgs):
                break
        else:
            # Dependencies match, exit
            print("System package dependencies match")
            return 0
    install_data.seek(0)
    print("Writing new system dependencies to install script...")
    with inst_script.open("w+") as inst_file:
        # Find and replace old package defs
        for line in install_data:
            inst_file.write(line)
            if line.strip() == INST_PKG_HEADER:
                write_parser_script(new_deps, inst_file)
    return 1

def check_reqs_changed(reqs_file: pathlib.Path, new_reqs: List[str]) -> bool:
    req_list = []
    for requirement in reqs_file.read_text().splitlines():
        requirement = requirement.strip()
        if not requirement or requirement[0] in ("-", "#"):
            continue
        req_list.append(requirement)
    return set(new_reqs) != set(req_list)

def sync_requirements() -> int:
    ret: int = 0
    src_path = SCRIPTS_PATH.parent
    proj_file = src_path.joinpath("pyproject.toml")
    with proj_file.open("rb") as f:
        data = tomllib.load(f)
    python_deps = data["project"]["dependencies"]
    optional_deps = data["project"]["optional-dependencies"]
    reqs_path = SCRIPTS_PATH.joinpath("moonraker-requirements.txt")
    if check_reqs_changed(reqs_path, python_deps):
        print("Syncing Moonraker Python Requirements...")
        ret = 1
        with reqs_path.open("w+") as req_file:
            req_file.write("# Python dependencies for Moonraker\n")
            req_file.write("--find-links=python_wheels\n")
            for requirement in python_deps:
                req_file.write(f"{requirement}\n")
    else:
        print("Moonraker Python requirements match")
    # sync speedups
    speedups_path = SCRIPTS_PATH.joinpath("moonraker-speedups.txt")
    speedup_deps = optional_deps["speedups"]
    if check_reqs_changed(speedups_path, speedup_deps):
        print("Syncing speedup requirements...")
        ret = 1
        with speedups_path.open("w+") as req_file:
            for requirement in speedup_deps:
                req_file.write(f"{requirement}\n")
    else:
        print("Speedup sequirements match")
    # sync dev dependencies
    dev_reqs_path = SCRIPTS_PATH.joinpath("moonraker-dev-reqs.txt")
    dev_deps = optional_deps["dev"]
    if check_reqs_changed(dev_reqs_path, dev_deps):
        print("Syncing dev requirements")
        ret = 1
        with dev_reqs_path.open("r+") as req_file:
            for requirement in dev_deps:
                req_file.write(f"{requirement}\n")
    else:
        print("Dev requirements match")
    return ret

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "filename", default="", nargs="?",
        help="The name of the dependency file to sync"
    )
    args = parser.parse_args()
    fname: str = args.filename
    if not fname:
        ret = sync_requirements()
        ret += sync_packages()
        return 1 if ret > 0 else 0
    elif fname == "pyproject.toml":
        return sync_requirements()
    elif fname == "scripts/system-dependencies.json":
        return sync_packages()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
