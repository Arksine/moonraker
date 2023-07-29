# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import pathlib
import logging
import shutil
import zipfile
import json
from ...utils import source_info
from .common import AppType, Channel
from .base_deploy import BaseDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Tuple,
    Union,
    Dict,
    List,
    cast
)
if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..file_manager.file_manager import FileManager
    from .update_manager import CommandHelper
    JsonType = Union[List[Any], Dict[str, Any]]


class WebClientDeploy(BaseDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper
                 ) -> None:
        super().__init__(config, cmd_helper, prefix="Web Client")
        self.repo = config.get('repo').strip().strip("/")
        self.owner, self.project_name = self.repo.split("/", 1)
        self.path = pathlib.Path(config.get("path")).expanduser().resolve()
        self.type = AppType.from_string(config.get('type'))
        self.channel = Channel.from_string(config.get("channel", "stable"))
        if self.channel == Channel.DEV:
            self.server.add_warning(
                f"Invalid Channel '{self.channel}' for config "
                f"section [{config.get_name()}], type: {self.type}. "
                f"Must be one of the following: stable, beta. "
                f"Falling back to beta channel"
            )
            self.channel = Channel.BETA
        self.info_tags: List[str] = config.getlist("info_tags", [])
        self.persistent_files: List[str] = []
        self.warnings: List[str] = []
        self.anomalies: List[str] = []
        self.version: str = "?"
        pfiles = config.getlist('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip("/") for pf in pfiles]
            if ".version" in self.persistent_files:
                raise config.error(
                    "Invalid value for option 'persistent_files': "
                    "'.version' can not be persistent")
        self._valid: bool = True
        self._is_prerelease: bool = False
        self._is_fallback: bool = False
        self._path_writable: bool = False

    async def _validate_client_info(self) -> None:
        self._valid = False
        self._is_fallback = False
        eventloop = self.server.get_event_loop()
        self.warnings.clear()
        repo_parent = source_info.find_git_repo(self.path)
        homedir = pathlib.Path("~").expanduser()
        if not self._path_writable:
            self.warnings.append(
                f"Location at option 'path: {self.path}' is not writable."
            )
        elif not self.path.is_dir():
            self.warnings.append(
                f"Location at option 'path: {self.path}' is not a directory."
            )
        elif repo_parent is not None and repo_parent != homedir:
            self.warnings.append(
                f"Location at option 'path: {self.path}' is within a git repo. Found "
                f".git folder at '{repo_parent.joinpath('.git')}'"
            )
        else:
            rinfo = self.path.joinpath("release_info.json")
            if rinfo.is_file():
                try:
                    data = await eventloop.run_in_thread(rinfo.read_text)
                    uinfo: Dict[str, str] = json.loads(data)
                    project_name = uinfo["project_name"]
                    owner = uinfo["project_owner"]
                    self.version = uinfo["version"]
                except Exception:
                    logging.exception("Failed to load release_info.json.")
                else:
                    self._valid = True
                    detected_repo = f"{owner}/{project_name}"
                    if self.repo.lower() != detected_repo.lower():
                        self.anomalies.append(
                            f"Value at option 'repo: {self.repo}' does not match "
                            f"detected repo '{detected_repo}', falling back to "
                            "detected version."
                        )
                        self.repo = detected_repo
                        self.owner = owner
                        self.project_name = project_name
            else:
                version_path = self.path.joinpath(".version")
                if version_path.is_file():
                    version = await eventloop.run_in_thread(version_path.read_text)
                    self.version = version.strip()
                self._valid = await self._detect_fallback()
        if not self._valid:
            self.warnings.append("Failed to validate client installation")
            if self.server.is_debug_enabled():
                self.log_info("Debug Enabled, overriding validity checks")

    async def _detect_fallback(self) -> bool:
        fallback_defs = {
            "mainsail": "mainsail-crew",
            "fluidd": "fluidd-core"
        }
        for fname in ("manifest.json", "manifest.webmanifest"):
            manifest = self.path.joinpath(fname)
            eventloop = self.server.get_event_loop()
            if manifest.is_file():
                try:
                    mtext = await eventloop.run_in_thread(manifest.read_text)
                    mdata: Dict[str, Any] = json.loads(mtext)
                    proj_name: str = mdata["name"].lower()
                except Exception:
                    self.log_exc(f"Failed to load json from {manifest}")
                    continue
                if proj_name in fallback_defs:
                    owner = fallback_defs[proj_name]
                    detected_repo = f"{owner}/{proj_name}"
                    if detected_repo != self.repo.lower():
                        self.anomalies.append(
                            f"Value at option 'repo: {self.repo}' does not match "
                            f"detected repo '{detected_repo}', falling back to "
                            "detected version."
                        )
                        self.repo = detected_repo
                        self.owner = owner
                        self.project_name = proj_name
                    self._is_fallback = True
                    return True
        return False

    async def initialize(self) -> Dict[str, Any]:
        fm: FileManager = self.server.lookup_component("file_manager")
        self._path_writable = not fm.check_reserved_path(
            self.path, need_write=True, raise_error=False
        )
        if self._path_writable:
            fm.add_reserved_path(f"update_manager {self.name}", self.path)
        await self._validate_client_info()
        storage = await super().initialize()
        if self.version == "?":
            self.version = storage.get("version", "?")
        self.remote_version: str = storage.get('remote_version', "?")
        self.rollback_version: str = storage.get('rollback_version', self.version)
        self.rollback_repo: str = storage.get(
            'rollback_repo', self.repo if self._valid else "?"
        )
        self.last_error: str = storage.get('last_error', "")
        dl_info: List[Any] = storage.get('dl_info', ["?", "?", 0])
        self.dl_info: Tuple[str, str, int] = cast(
            Tuple[str, str, int], tuple(dl_info))
        if not self.needs_refresh():
            self._log_client_info()
        return storage

    def _log_client_info(self) -> None:
        warn_str = ""
        if self.warnings or self.anomalies:
            warn_str = "\nWarnings:\n"
            warn_str += "\n".join(
                [f" {item}" for item in self.warnings + self.anomalies]
            )
        dl_url, content_type, size = self.dl_info
        logging.info(
            f"Web Client {self.name} Detected:\n"
            f"Repo: {self.repo}\n"
            f"Channel: {self.channel}\n"
            f"Path: {self.path}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"Valid: {self._valid}\n"
            f"Fallback Client Detected: {self._is_fallback}\n"
            f"Pre-release: {self._is_prerelease}\n"
            f"Download Url: {dl_url}\n"
            f"Download Size: {size}\n"
            f"Content Type: {content_type}\n"
            f"Rollback Version: {self.rollback_version}\n"
            f"Rollback Repo: {self.rollback_repo}"
            f"{warn_str}"
        )

    async def refresh(self) -> None:
        try:
            if not self._valid:
                await self._validate_client_info()
            await self._get_remote_version()
        except Exception:
            logging.exception("Error Refreshing Client")
        self._log_client_info()
        self._save_state()

    async def _fetch_github_version(
        self, repo: Optional[str] = None, tag: Optional[str] = None
    ) -> Dict[str, Any]:
        if repo is None:
            if not self._valid:
                self.log_info("Invalid Web Installation, aborting remote refresh")
                return {}
            repo = self.repo
        if tag is not None:
            resource = f"repos/{repo}/releases/tags/{tag}"
        elif self.channel == Channel.STABLE:
            resource = f"repos/{repo}/releases/latest"
        else:
            resource = f"repos/{repo}/releases?per_page=1"
        client = self.cmd_helper.get_http_client()
        resp = await client.github_api_request(
            resource, attempts=3, retry_pause_time=.5
        )
        release: Union[List[Any], Dict[str, Any]] = {}
        if resp.status_code == 304:
            if resp.content:
                # Not modified, however we need to restore state from
                # cached content
                release = resp.json()
            else:
                # Either not necessary or not possible to restore from cache
                return {}
        elif resp.has_error():
            self.log_info(f"Github Request Error - {resp.error}")
            self.last_error = str(resp.error)
            return {}
        else:
            release = resp.json()
        result: Dict[str, Any] = {}
        if isinstance(release, list):
            if release:
                result = release[0]
        else:
            result = release
        self.last_error = ""
        return result

    async def _get_remote_version(self) -> None:
        result = await self._fetch_github_version()
        if not result:
            return
        self.remote_version = result.get('name', "?")
        release_asset: Dict[str, Any] = result.get('assets', [{}])[0]
        dl_url: str = release_asset.get('browser_download_url', "?")
        content_type: str = release_asset.get('content_type', "?")
        size: int = release_asset.get('size', 0)
        self.dl_info = (dl_url, content_type, size)
        self._is_prerelease = result.get('prerelease', False)

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['version'] = self.version
        storage['remote_version'] = self.remote_version
        storage['rollback_version'] = self.rollback_version
        storage['rollback_repo'] = self.rollback_repo
        storage['dl_info'] = list(self.dl_info)
        storage['last_error'] = self.last_error
        return storage

    async def update(
        self, rollback_info: Optional[Tuple[str, str, int]] = None
    ) -> bool:
        if not self._valid:
            raise self.server.error(
                f"Web Client {self.name}: Invalid install detected, aborting update"
            )
        if rollback_info is not None:
            dl_url, content_type, size = rollback_info
            start_msg = f"Rolling Back Web Client {self.name}..."
        else:
            if self.remote_version == "?":
                await self._get_remote_version()
                if self.remote_version == "?":
                    raise self.server.error(
                        f"Client {self.repo}: Unable to locate update"
                    )
            dl_url, content_type, size = self.dl_info
            if self.version == self.remote_version:
                # Already up to date
                return False
            start_msg = f"Updating Web Client {self.name}..."
        if dl_url == "?":
            raise self.server.error(f"Client {self.repo}: Invalid download url")
        current_version = self.version
        event_loop = self.server.get_event_loop()
        self.cmd_helper.notify_update_response(start_msg)
        self.cmd_helper.notify_update_response(f"Downloading Client: {self.name}")
        td = await self.cmd_helper.create_tempdir(self.name, "client")
        try:
            tempdir = pathlib.Path(td.name)
            temp_download_file = tempdir.joinpath(f"{self.name}.zip")
            temp_persist_dir = tempdir.joinpath(self.name)
            client = self.cmd_helper.get_http_client()
            await client.download_file(
                dl_url, content_type, temp_download_file, size,
                self.cmd_helper.on_download_progress)
            self.cmd_helper.notify_update_response(
                f"Download Complete, extracting release to '{self.path}'")
            await event_loop.run_in_thread(
                self._extract_release, temp_persist_dir,
                temp_download_file)
        finally:
            await event_loop.run_in_thread(td.cleanup)
        self.version = self.remote_version
        await self._validate_client_info()
        if self._valid and rollback_info is None:
            self.rollback_version = current_version
            self.rollback_repo = self.repo
        msg = f"Client Update Finished: {self.name}"
        if rollback_info is not None:
            msg = f"Rollback Complete: {self.name}"
        self.cmd_helper.notify_update_response(msg, is_complete=True)
        self._log_client_info()
        self._save_state()
        return True

    async def rollback(self) -> bool:
        if self.rollback_version == "?" or self.rollback_repo == "?":
            raise self.server.error("Incomplete Rollback Data")
        if self.rollback_version == self.version:
            return False
        result = await self._fetch_github_version(
            self.rollback_repo, self.rollback_version
        )
        if not result:
            raise self.server.error("Failed to retrieve release asset data")
        release_asset: Dict[str, Any] = result.get('assets', [{}])[0]
        dl_url: str = release_asset.get('browser_download_url', "?")
        content_type: str = release_asset.get('content_type', "?")
        size: int = release_asset.get('size', 0)
        dl_info = (dl_url, content_type, size)
        return await self.update(dl_info)

    def _extract_release(self,
                         persist_dir: pathlib.Path,
                         release_file: pathlib.Path
                         ) -> None:
        if not persist_dir.exists():
            os.mkdir(persist_dir)
        if self.path.is_dir():
            # find and move persistent files
            for fname in os.listdir(self.path):
                src_path = self.path.joinpath(fname)
                if fname in self.persistent_files:
                    dest_dir = persist_dir.joinpath(fname).parent
                    os.makedirs(dest_dir, exist_ok=True)
                    shutil.move(str(src_path), str(dest_dir))
            shutil.rmtree(self.path)
        os.mkdir(self.path)
        with zipfile.ZipFile(release_file) as zf:
            zf.extractall(self.path)
        # Move temporary files back into
        for fname in os.listdir(persist_dir):
            src_path = persist_dir.joinpath(fname)
            dest_dir = self.path.joinpath(fname).parent
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(str(src_path), str(dest_dir))

    def get_update_status(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'repo_name': self.project_name,
            'owner': self.owner,
            'version': self.version,
            'remote_version': self.remote_version,
            'rollback_version': self.rollback_version,
            'configured_type': str(self.type),
            'channel': str(self.channel),
            'info_tags': self.info_tags,
            'last_error': self.last_error,
            'is_valid': self._valid,
            'warnings': self.warnings,
            'anomalies': self.anomalies
        }
