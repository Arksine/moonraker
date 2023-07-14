# General Server Utilities
#
# Copyright (C) 2023 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import importlib.resources as ilr
import pathlib
import sys
import site

# Annotation imports
from typing import (
    Optional,
)

def package_path() -> pathlib.Path:
    return pathlib.Path(__file__).parent.parent

def source_path() -> pathlib.Path:
    return package_path().parent

def is_git_repo(src_path: Optional[pathlib.Path] = None) -> bool:
    if src_path is None:
        src_path = source_path()
    return src_path.joinpath(".git").is_dir()

def find_git_repo(src_path: Optional[pathlib.Path] = None) -> Optional[pathlib.Path]:
    if src_path is None:
        src_path = source_path()
    if src_path.joinpath(".git").is_dir():
        return src_path
    for parent in src_path.parents:
        if parent.joinpath(".git").is_dir():
            return parent
    return None

def is_dist_package(src_path: Optional[pathlib.Path] = None) -> bool:
    if src_path is None:
        # Check Moonraker's source path
        src_path = source_path()
        if hasattr(site, "getsitepackages"):
            # The site module is present, get site packages for Moonraker's venv.
            # This is more "correct" than the fallback method.
            site_dirs = site.getsitepackages()
            return str(src_path) in site_dirs
    # Make an assumption based on the source path.  If its name is
    # site-packages or dist-packages then presumably it is an
    # installed package
    return src_path.name in ["dist-packages", "site-packages"]

def package_version() -> Optional[str]:
    try:
        import moonraker.__version__ as ver  # type: ignore
        version = ver.__version__
    except Exception:
        pass
    else:
        if version:
            return version
    return None

def read_asset(asset_name: str) -> Optional[str]:
    if sys.version_info < (3, 10):
        with ilr.path("moonraker.assets", asset_name) as p:
            if not p.is_file():
                return None
            return p.read_text()
    else:
        files = ilr.files("moonraker.assets")
        with ilr.as_file(files.joinpath(asset_name)) as p:
            if not p.is_file():
                return None
            return p.read_text()

def get_asset_path() -> Optional[pathlib.Path]:
    if sys.version_info < (3, 10):
        with ilr.path("moonraker.assets", "__init__.py") as p:
            asset_path = p.parent
    else:
        files = ilr.files("moonraker.assets")
        with ilr.as_file(files.joinpath("__init__.py")) as p:
            asset_path = p.parent
    if not asset_path.is_dir():
        # Somehow running in a zipapp.  This is an error.
        return None
    return asset_path
