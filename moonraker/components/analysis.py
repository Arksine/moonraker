# Printer GCode Analysis using Klipper Estimator
#
# Copyright (C) 2025 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import sys
import os
import platform
import pathlib
import stat
import re
import logging
import asyncio
from ..common import RequestType
from ..utils import json_wrapper as jsonw
from typing import (
    TYPE_CHECKING,
    Union,
    Optional,
    Dict,
    Any,
    Tuple
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .update_manager.update_manager import UpdateManager
    from .klippy_connection import KlippyConnection
    from .authorization import Authorization
    from .file_manager.file_manager import FileManager
    from .machine import Machine
    from .shell_command import ShellCommandFactory
    from .http_client import HttpClient
    StrOrPath = Union[str, pathlib.Path]

ESTIMATOR_URL = (
    "https://github.com/Annex-Engineering/klipper_estimator/"
    "releases/latest/download/{asset}"
)
UPDATE_CONFIG = {
    "type": "executable",
    "channel": "stable",
    "repo": "Annex-Engineering/klipper_estimator",
    "is_system_service": "False",
    "path": ""
}
RELEASE_INFO = {
    "project_name": "klipper_estimator",
    "project_owner": "Annex-Engineering",
    "version": "",
    "asset_name": ""
}

class GcodeAnalysis:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.cmd_lock = asyncio.Lock()
        self.file_manger: FileManager = self.server.lookup_component("file_manager")
        data_path = self.server.get_app_args()["data_path"]
        tool_folder = pathlib.Path(data_path).joinpath("tools/klipper_estimator")
        if not tool_folder.exists():
            tool_folder.mkdir(parents=True)
        self.estimator_timeout = config.getint("estimator_timeout", 600)
        self.auto_dump_defcfg = config.getboolean("auto_dump_default_config", False)
        self.default_config = tool_folder.joinpath("default_estimator_cfg.json")
        self.estimator_config = self.default_config
        est_config = config.get("estimator_config", None)
        if est_config is not None:
            est_path = self.file_manger.get_full_path("config", est_config.strip("/"))
            if ".." in est_path.parts:
                raise config.error(
                    "Value for option 'estimator_config' must not contain "
                    "a '..' segment"
                )
            if not est_path.exists():
                raise config.error(
                    f"File '{est_config}' does not exist in 'config' root"
                )
            self.estimator_config = est_path
        if config.getboolean("enable_auto_analysis", False):
            self.server.register_event_handler(
                "file_manager:metadata_processed", self._on_metadata_processed
            )
        self.estimator_path: pathlib.Path | None = None
        self.estimator_ready: bool = False
        self.estimator_version: str = "?"
        pltform_choices = ["rpi", "linux", "osx", "auto"]
        pltform = config.getchoice("platform", pltform_choices, default_key="auto")
        if pltform == "auto":
            auto_pfrm = self._detect_platform()
            if auto_pfrm is not None:
                self.estimator_path = tool_folder.joinpath(
                    f"klipper_estimator_{auto_pfrm}"
                )
        else:
            exec_name = f"klipper_estimator_{pltform}"
            self.estimator_path = tool_folder.joinpath(exec_name)
        enable_updates = config.getboolean("enable_estimator_updates", False)
        self.updater_registered: bool = False
        if enable_updates:
            if self.estimator_path is None:
                logging.info(
                    "Klipper estimator platform not detected, updates disabled"
                )
            elif not config.has_section("update_manager"):
                logging.info("Update Manager not configured, updates disabled")
            else:
                try:
                    um: UpdateManager
                    um = self.server.load_component(config, "update_manager")
                    updater_cfg = UPDATE_CONFIG
                    updater_cfg["path"] = str(tool_folder)
                    um.register_updater("klipper_estimator", updater_cfg)
                except self.server.error:
                    logging.exception("Klipper Estimator update registration failed")
                else:
                    self.updater_registered = True
        if not self.updater_registered:
            # Add reserved path when updates are disabled
            self.file_manger.add_reserved_path("analysis", tool_folder, False)
        self.server.register_endpoint(
            "/server/analysis/status", RequestType.GET,
            self._handle_status_request
        )
        self.server.register_endpoint(
            "/server/analysis/estimate", RequestType.POST,
            self._handle_estimation_request
        )
        self.server.register_endpoint(
            "/server/analysis/dump_config", RequestType.POST,
            self._handle_dump_cfg_request
        )
        self.server.register_event_handler(
            "server:klippy_ready", self._on_klippy_ready
        )

    @property
    def estimator_version_tuple(self) -> Tuple[int, ...]:
        if self.estimator_version in ["?", ""]:
            return tuple()
        ver_string = self.estimator_version
        if ver_string[0] == "v":
            ver_string = ver_string[1:]
        return tuple([int(p) for p in ver_string.split(".")])

    async def _on_klippy_ready(self) -> None:
        if not self.estimator_ready:
            return
        if self.auto_dump_defcfg or not self.default_config.exists():
            logging.info(
                "Dumping default Klipper Estimator configuration to "
                f"{self.default_config}"
            )
            eventloop = self.server.get_event_loop()
            eventloop.create_task(self._dump_estimator_config(self.default_config))

    async def _on_metadata_processed(self, rel_gc_path: str) -> None:
        if not self.estimator_ready:
            logging.info("Klipper Estimator not available")
            return
        try:
            full_path = self.file_manger.get_full_path("gcodes", rel_gc_path)
            ret = await self.estimate_file(full_path)
            self._update_metadata_est_time(rel_gc_path, ret)
        except self.server.error:
            logging.exception("Klipper Estimator failure")

    def _update_metadata_est_time(
        self, gc_fname: str, est_data: Dict[str, Any]
    ) -> None:
        md_storage = self.file_manger.get_metadata_storage()
        gc_metadata = md_storage.get(gc_fname, None)
        if gc_metadata is not None:
            if "slicer_estimated_time" not in gc_metadata:
                prev_est = gc_metadata.get("estimated_time", 0)
                gc_metadata["slicer_estimated_time"] = prev_est
            gc_metadata["estimated_time"] = round(est_data["total_time"], 2)
            md_storage.insert(gc_fname, gc_metadata)

    async def component_init(self) -> None:
        if self.estimator_path is None:
            return
        if not self.estimator_path.exists():
            # Download Klipper Estimator
            await self._download_klipper_estimator(self.estimator_path)
        if not self._check_estimator_perms(self.estimator_path):
            self.server.add_warning(
                "[analysis]: Moonraker lacks permission to execute Klipper Estimator",
                "analysis_permission"
            )
            return
        else:
            await self._detect_estimator_version()
        if self.estimator_version == "?":
            logging.info("Failed to initialize Klipper Estimator")
        else:
            await self._check_release_info(self.estimator_path)
            self.estimator_ready = True
            logging.info(
                f"Klipper Estimator Version {self.estimator_version} detected"
            )

    def _detect_platform(self) -> Optional[str]:
        # Detect OS
        if sys.platform.startswith("darwin"):
            return "osx"
        elif sys.platform.startswith("linux"):
            # Get architecture
            arch: str = platform.machine()
            if not arch:
                self.server.add_warning(
                    "[analysis]: Failed to detect CPU architecture.  "
                    "Manual configuration of the 'platform' option is required.",
                    "analysis_estimator"
                )
                return None
            if arch == "x86_64":
                return "linux"
            elif arch in ("armv7l", "aarch64"):
                # TODO:  Other platforms may work, not sure
                return "rpi"
            else:
                self.server.add_warning(
                    f"[analysis]: Unsupported CPU architecture '{arch}'.  "
                    "Manual configuration of the 'platform' option is required.",
                    "analysis_estimator"
                )
                return None
        else:
            self.server.add_warning(
                f"[analysis]: Unsupported platform '{sys.platform}'. "
                "Manual configuration of the 'platform' option is required.",
                "analysis_estimator"
            )

    async def _download_klipper_estimator(self, estimator_path: pathlib.Path) -> None:
        """
        Download Klipper Estimator, set executable permissions, and generate
        the release_info.json file
        """
        async with self.cmd_lock:
            est_name = estimator_path.name
            logging.info(f"Downloading latest {est_name}...")
            url = ESTIMATOR_URL.format(asset=est_name)
            http_client: HttpClient = self.server.lookup_component("http_client")
            await http_client.download_file(
                url, "application/octet-stream", estimator_path
            )
            logging.info("Klipper Estimator download complete.")

    async def _detect_estimator_version(self) -> None:
        cmd = f"{self.estimator_path} --version"
        scmd: ShellCommandFactory = self.server.lookup_component("shell_command")
        ret = await scmd.exec_cmd(cmd, timeout=10.)
        ver_match = re.match(r"klipper_estimator (v?\d+(?:\.\d+)*)", ret)
        if ver_match is None:
            self.estimator_version = "?"
        else:
            self.estimator_version = ver_match.group(1)

    def _check_estimator_perms(self, estimator_path: pathlib.Path) -> bool:
        req_perms = stat.S_IXUSR | stat.S_IXGRP
        kest_perms = stat.S_IMODE(estimator_path.stat().st_mode)
        if req_perms & kest_perms != req_perms:
            logging.info("Setting excutable permissions for Klipper Estimator...")
            try:
                estimator_path.chmod(kest_perms | req_perms)
            except OSError:
                logging.exception(
                    "Failed to set Klipper Estimator Permissions"
                )
        return os.access(estimator_path, os.X_OK)

    async def _check_release_info(self, estimator_path: pathlib.Path) -> None:
        rinfo_file = estimator_path.parent.joinpath("release_info.json")
        if rinfo_file.is_file():
            return
        logging.info("Creating release_info.json for Klipper Estimator...")
        rinfo = dict(RELEASE_INFO)
        rinfo["version"] = self.estimator_version
        rinfo["asset_name"] = estimator_path.name
        eventloop = self.server.get_event_loop()
        await eventloop.run_in_thread(rinfo_file.write_bytes, jsonw.dumps(rinfo))
        if self.updater_registered:
            logging.info("Refreshing Klipper Estimator Updater Instance...")
            um: UpdateManager = self.server.lookup_component("update_manager")
            eventloop.create_task(um.refresh_updater("klipper_estimator", True))

    def _get_moonraker_url(self) -> str:
        machine: Machine = self.server.lookup_component("machine")
        host_info = self.server.get_host_info()
        host_addr: str = host_info["address"]
        if host_addr.lower() in ["all", "0.0.0.0"]:
            address = "127.0.0.1"
        elif host_addr.lower() == "::":
            address = "::1"
        else:
            address = machine.public_ip
        if not address:
            address = f"{host_info['hostname']}.local"
        elif ":" in address:
            # ipv6 address
            address = f"[{address}]"
        port = host_info["port"]
        return f"http://{address}:{port}/"

    def _gen_estimate_cmd(
        self, gc_path: pathlib.Path, est_cfg_path: pathlib.Path
    ) -> str:
        if self.estimator_path is None or not self.estimator_ready:
            raise self.server.error("Klipper Estimator not available")
        if not est_cfg_path.exists():
            raise self.server.error(
                f"Klipper Estimator config {est_cfg_path.name} does not exist"
            )
        cmd = str(self.estimator_path)
        escaped_cfg = str(est_cfg_path).replace("\"", "\\\"")
        cmd = f"{cmd} --config_file \"{escaped_cfg}\""
        escaped_gc = str(gc_path).replace("\"", "\\\"")
        cmd = f"{cmd} estimate -f json \"{escaped_gc}\""
        return cmd

    def _gen_dump_cmd(self) -> str:
        if self.estimator_path is None or not self.estimator_ready:
            raise self.server.error("Klipper Estimator not available")
        return f"{self.estimator_path} {self._gen_url_opts()} dump-config"

    def _gen_url_opts(self) -> str:
        url = self._get_moonraker_url()
        opts = f"--config_moonraker_url {url}"
        auth: Optional[Authorization]
        auth = self.server.lookup_component("authorization", None)
        api_key = auth.get_api_key() if auth is not None else None
        if api_key is not None:
            opts = f"{opts} --config_moonraker_api_key {api_key}"
        return opts

    async def _dump_estimator_config(self, dest: pathlib.Path) -> Dict[str, Any]:
        async with self.cmd_lock:
            kconn: KlippyConnection = self.server.lookup_component("klippy_connection")
            scmd: ShellCommandFactory = self.server.lookup_component("shell_command")
            eventloop = self.server.get_event_loop()
            if not kconn.is_ready():
                raise self.server.error(
                    "Klipper Estimator cannot dump configuration, Klippy not ready",
                    504
                )
            dump_cmd = self._gen_dump_cmd()
            try:
                ret = await scmd.exec_cmd(
                    dump_cmd, timeout=10., log_complete=False, log_stderr=True
                )
                await eventloop.run_in_thread(dest.write_text, ret)
            except scmd.error:
                raise self.server.error(
                    "Klipper Estimator dump-config failed", 500
                ) from None
            return jsonw.loads(ret)

    async def estimate_file(
        self, gc_path: pathlib.Path, est_config: Optional[pathlib.Path] = None
    ) -> Dict[str, Any]:
        async with self.cmd_lock:
            if est_config is None:
                # Fall back to estimator config specified in the [analysis]
                # section.
                est_config = self.estimator_config
            if not est_config.is_file():
                raise self.server.error(
                    f"Estimator config file '{est_config}' does not exist"
                )
            if not gc_path.is_file():
                raise self.server.error(f"GCode File '{gc_path}' does not exist")
            scmd: ShellCommandFactory = self.server.lookup_component("shell_command")
            est_cmd = self._gen_estimate_cmd(gc_path, est_config)
            ret = await scmd.exec_cmd(est_cmd, self.estimator_timeout)
            data = jsonw.loads(ret)
            return data["sequences"][0]

    async def _handle_status_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        est_exec = "unknown"
        if self.estimator_path is not None:
            est_exec = self.estimator_path.name
        is_default = self.estimator_config == self.default_config
        return {
            "estimator_executable": est_exec,
            "estimator_ready": self.estimator_ready,
            "estimator_version": self.estimator_version,
            "estimator_config_exists": self.estimator_config.exists(),
            "using_default_config": is_default
        }

    async def _handle_estimation_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        gcode_file = web_request.get_str("filename").strip("/")
        update_metadata = web_request.get_boolean("update_metadata", False)
        estimator_config = web_request.get_str("estimator_config", None)
        gc_path = self.file_manger.get_full_path("gcodes", gcode_file)
        if not gc_path.is_file():
            raise self.server.error(
                f"GCode File '{gcode_file}' does not exit in gcodes path"
            )
        est_cfg_path = self.estimator_config
        if estimator_config is not None:
            estimator_config = estimator_config.strip("/")
            est_cfg_path = self.file_manger.get_full_path("config", estimator_config)
            if ".." in est_cfg_path.parts:
                raise self.server.error(
                    "Invalid value for param 'estimator_config', '..' segments "
                    "are not allowed"
                )
        ret = await self.estimate_file(gc_path, est_cfg_path)
        if update_metadata:
            self._update_metadata_est_time(gcode_file, ret)
        return ret

    async def _handle_dump_cfg_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        dest = web_request.get_str("dest_config", None)
        root: str | None = None
        if dest is not None:
            root = "config"
            dest = dest.strip("/")
            if ".." in pathlib.Path(dest).parts:
                raise self.server.error(
                    "Parameter 'dest_config' may not contain '..' parts"
                )
            dest_config = self.file_manger.get_full_path("config", dest)
        else:
            dest = self.default_config.name
            dest_config = self.default_config
        result = await self._dump_estimator_config(dest_config)
        return {
            "dest_root": root,
            "dest_config_path": dest,
            "klipper_estimator_config": result
        }


def load_component(config: ConfigHelper) -> GcodeAnalysis:
    return GcodeAnalysis(config)
