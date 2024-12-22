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
import re
from typing import Dict, List, Tuple

MAX_LINE_LENGTH = 88
SCRIPTS_PATH = pathlib.Path(__file__).parent
INST_PKG_HEADER = "# *** AUTO GENERATED OS PACKAGE DEPENDENCIES START ***"
INST_PKG_FOOTER = "# *** AUTO GENERATED OS PACKAGE DEPENDENCIES END ***"

def gen_multline_var(
    var_name: str,
    values: List[str],
    indent: int = 0,
    is_first: bool = True
) -> str:
    idt = " " * indent
    if not values:
        return f'{idt}{var_name}=""'
    line_list: List[str] = []
    if is_first:
        current_line = f"{idt}{var_name}=\"{values.pop(0)}"
    else:
        current_line = (f"{idt}{var_name}=\"${{{var_name}}} {values.pop(0)}")
    for val in values:
        if len(current_line) + len(val) + 2 > MAX_LINE_LENGTH:
            line_list.append(f'{current_line}"')
            current_line = (f"{idt}{var_name}=\"${{{var_name}}} {val}")
        else:
            current_line += f" {val}"
    line_list.append(f'{current_line}"')
    return "\n".join(line_list)

def parse_sysdeps_file() -> Dict[str, List[Tuple[str, str, str]]]:
    sys_deps_file = SCRIPTS_PATH.joinpath("system-dependencies.json")
    base_deps: Dict[str, List[str]] = json.loads(sys_deps_file.read_bytes())
    parsed_deps: Dict[str, List[Tuple[str, str, str]]] = {}
    for distro, pkgs in base_deps.items():
        parsed_deps[distro] = []
        for dep in pkgs:
            parts = dep.split(";", maxsplit=1)
            if len(parts) == 1:
                parsed_deps[distro].append((dep.strip(), "", ""))
            else:
                pkg_name = parts[0].strip()
                dep_parts = re.split(r"(==|!=|<=|>=|<|>)", parts[1].strip())
                comp_var = dep_parts[0].strip().lower()
                if len(dep_parts) != 3 or comp_var != "distro_version":
                    continue
                operator = dep_parts[1].strip()
                req_version = dep_parts[2].strip()
                parsed_deps[distro].append((pkg_name, operator, req_version))
    return parsed_deps

def sync_packages() -> int:
    inst_script = SCRIPTS_PATH.joinpath("install-moonraker.sh")
    new_deps = parse_sysdeps_file()
    # Copy install script in memory.
    install_data: List[str] = []
    prev_deps: Dict[str, List[Tuple[str, str, str]]] = {}
    distro_name = ""
    cur_spec: Tuple[str, str] | None = None
    skip_data = False
    with inst_script.open("r") as inst_file:
        for line in inst_file:
            cur_line = line.strip()
            if not skip_data:
                install_data.append(line)
            else:
                # parse current dependencies
                distro_match = re.match(
                    r"(?:el)?if \[ \$\{DISTRIBUTION\} = \"([a-z0-9._-]+)\" \]; then",
                    cur_line
                )
                if distro_match is not None:
                    distro_name = distro_match.group(1)
                    prev_deps[distro_name] = []
                else:
                    if cur_spec is not None and cur_line == "fi":
                        cur_spec = None
                    else:
                        req_match = re.match(
                            r"if \( compare_version \"(<|>|==|!=|<=|>=)\" "
                            r"\"([a-zA-Z0-9._-]+)\" \); then",
                            cur_line
                        )
                        if req_match is not None:
                            parts = req_match.groups()
                            cur_spec = (parts[0], parts[1])
                        elif cur_line.startswith("PACKAGES"):
                            pkgs = cur_line.split("=", maxsplit=1)[1].strip('"')
                            pkg_list = pkgs.split()
                            if pkg_list and pkg_list[0] == "${PACKAGES}":
                                pkg_list.pop(0)
                            operator, req_version = "", ""
                            if cur_spec is not None:
                                operator, req_version = cur_spec
                            for pkg in pkg_list:
                                prev_deps[distro_name].append(
                                    (pkg, operator, req_version)
                                )
            if cur_line == INST_PKG_HEADER:
                skip_data = True
            elif cur_line == INST_PKG_FOOTER:
                skip_data = False
                install_data.append(line)
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
    print("Writing new system dependencies to install script...")
    with inst_script.open("w+") as inst_file:
        # Find and replace old package defs
        for line in install_data:
            inst_file.write(line)
            if line.strip() == INST_PKG_HEADER:
                indent_count = len(line) - len(line.lstrip())
                idt = " " * indent_count
                # Write Package data
                first_distro = True
                for distro, packages in new_deps.items():
                    prefix = f"{idt}if" if first_distro else f"{idt}elif"
                    first_distro = False
                    inst_file.write(
                        f'{prefix} [ ${{DISTRIBUTION}} = "{distro}" ]; then\n'
                    )
                    pkgs_by_op: Dict[Tuple[str, str], List[str]] = {}
                    base_list: List[str] = []
                    for pkg_spec in packages:
                        if not pkg_spec[1] or not pkg_spec[2]:
                            base_list.append(pkg_spec[0])
                        else:
                            key = (pkg_spec[1], pkg_spec[2])
                            pkgs_by_op.setdefault(key, []).append(pkg_spec[0])
                    is_first = True
                    if base_list:
                        pkg_var = gen_multline_var(
                            "PACKAGES", base_list, indent_count + 4
                        )
                        inst_file.write(pkg_var)
                        inst_file.write("\n")
                        is_first = False
                    if pkgs_by_op:
                        for (operator, req_ver), pkg_list in pkgs_by_op.items():
                            req_idt = idt + " " * 4
                            inst_file.write(
                                f"{req_idt}if ( compare_version \"{operator}\" "
                                f"\"{req_ver}\" ); then\n"
                            )
                            req_pkgs = gen_multline_var(
                                "PACKAGES", pkg_list, indent_count + 8, is_first
                            )
                            inst_file.write(req_pkgs)
                            inst_file.write("\n")
                            inst_file.write(f"{req_idt}fi\n")
                inst_file.write(f"{idt}fi\n")
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
