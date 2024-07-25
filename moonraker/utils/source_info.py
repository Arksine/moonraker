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
import re
import json
import logging
from dataclasses import dataclass
from importlib_metadata import Distribution, PathDistribution, PackageMetadata
from .exceptions import ServerError

# Annotation imports
from typing import (
    Optional,
    Dict,
    Any
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

def is_dist_package(item_path: Optional[pathlib.Path] = None) -> bool:
    """
    Check if the supplied path exists within a python dist installation or
    site installation.
    """
    if item_path is None:
        # Check Moonraker's package path
        item_path = package_path()
        if hasattr(site, "getsitepackages"):
            # The site module is present, get site packages for Moonraker's venv.
            # This is more "correct" than the fallback method.
            for site_dir in site.getsitepackages():
                site_path = pathlib.Path(site_dir)
                try:
                    if site_path.samefile(item_path.parent):
                        return True
                except Exception:
                    pass
    # Make an assumption based on the item and/or its parents.  If a folder
    # is named site-packages or dist-packages then presumably it is an
    # installed package
    if item_path.name in ("dist-packages", "site-packages"):
        return True
    for parent in item_path.parents:
        if parent.name in ("dist-packages", "site-packages"):
            return True
    return False

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

def _load_release_info_json(dist_info: Distribution) -> Optional[Dict[str, Any]]:
    files = dist_info.files
    if files is None:
        return None
    for dist_file in files:
        if dist_file.parts[0] in ["..", "/"]:
            continue
        if dist_file.name == "release_info":
            pkg = dist_file.parts[0]
            logging.info(f"Package {pkg}: Detected release_info json file")
            try:
                return json.loads(dist_file.read_text())
            except Exception:
                logging.exception(f"Failed to load release_info from {dist_file}")
    return None

def _load_direct_url_json(dist_info: Distribution) -> Optional[Dict[str, Any]]:
    ret: Optional[str] = dist_info.read_text("direct_url.json")
    if ret is None:
        return None
    try:
        direct_url: Dict[str, Any] = json.loads(ret)
    except json.JSONDecodeError:
        return None
    return direct_url

def normalize_project_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower().replace('-', '_')

def load_distribution_info(
    venv_path: pathlib.Path, project_name: str
) -> PackageInfo:
    proj_name_normalized = normalize_project_name(project_name)
    site_items = venv_path.joinpath("lib").glob("python*/site-packages/")
    lib_paths = [str(p) for p in site_items if p.is_dir()]
    for dist_info in Distribution.discover(name=project_name, path=lib_paths):
        metadata = dist_info.metadata
        if metadata is None:
            continue
        if not isinstance(dist_info, PathDistribution):
            logging.info(f"Project {dist_info.name} not a PathDistribution")
            continue
        metaname = normalize_project_name(metadata["Name"] or "")
        if metaname != proj_name_normalized:
            continue
        release_info = _load_release_info_json(dist_info)
        install_info = _load_direct_url_json(dist_info)
        return PackageInfo(
            dist_info, metadata, release_info, install_info
        )
    raise ServerError(f"Failed to find distribution info for project {project_name}")

def is_vitualenv_project(
    venv_path: Optional[pathlib.Path] = None,
    pkg_path: Optional[pathlib.Path] = None,
    project_name: str = "moonraker"
) -> bool:
    if venv_path is None:
        venv_path = pathlib.Path(sys.exec_prefix)
    if pkg_path is None:
        pkg_path = package_path()
    if not pkg_path.exists():
        return False
    try:
        pkg_info = load_distribution_info(venv_path, project_name)
    except Exception:
        return False
    site_path = pathlib.Path(str(pkg_info.dist_info.locate_file("")))
    for parent in pkg_path.parents:
        try:
            if site_path.samefile(parent):
                return True
        except Exception:
            pass
    return True

@dataclass(frozen=True)
class PackageInfo:
    dist_info: Distribution
    metadata: PackageMetadata
    release_info: Optional[Dict[str, Any]]
    direct_url_data: Optional[Dict[str, Any]]
