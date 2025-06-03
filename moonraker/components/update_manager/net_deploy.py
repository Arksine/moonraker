# Net Hosted Application Deployment implementation
#
# Copyright (C) 2024  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import pathlib
import shutil
import zipfile
import logging
import stat
from .app_deploy import AppDeploy
from .common import Channel, AppType
from ...utils import source_info
from ...utils import json_wrapper as jsonw

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Optional,
    Dict,
    List,
    Union,
    cast
)
if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..file_manager.file_manager import FileManager

class NetDeploy(AppDeploy):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, "Zip Application")
        self._configure_path(config, False)
        if self.type == AppType.ZIP:
            self._configure_virtualenv(config)
            self._configure_dependencies(config)
            self._configure_managed_services(config)
        elif self.type == AppType.WEB:
            self.prefix = f"Web Client {self.name}: "
        elif self.type == AppType.EXECUTABLE:
            self.prefix = f"Executable {self.name}: "
            self._configure_sysdeps(config)
            self._configure_managed_services(config)
        self.repo = config.get('repo').strip().strip("/")
        self.owner, self.project_name = self.repo.split("/", 1)
        self.asset_name: Optional[str] = None
        self.persistent_files: List[str] = []
        self.warnings: List[str] = []
        self.anomalies: List[str] = []
        self.version: str = "?"
        self.remote_version: str = "?"
        self.rollback_version: str = "?"
        self.rollback_repo: str = "?"
        self.last_error: str = "?"
        self._dl_info: Tuple[str, str, int] = ("?", "?", 0)
        self._is_fallback: bool = False
        self._is_prerelease: bool = False
        self._path_writable: bool = False
        self._configure_persistent_files(config)

    def _configure_persistent_files(self, config: ConfigHelper) -> None:
        if self.type == AppType.EXECUTABLE:
            # executable types do not wipe the entire directory,
            # so no need for persistent files
            return
        pfiles = config.getlist('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip("/") for pf in pfiles]
            for fname in (".version", "release_info.json"):
                if fname in self.persistent_files:
                    raise config.error(
                        "Invalid value for option 'persistent_files': "
                        f"'{fname}' can not be persistent."
                    )
        if (
            self.type == AppType.ZIP and
            self.virtualenv is not None and
            self.virtualenv in self.path.parents
        ):
            rel_path = str(self.virtualenv.relative_to(self.path))
            if rel_path not in self.persistent_files:
                self.persistent_files.append(rel_path)
        if self.persistent_files:
            self.log_info(f"Configured persistent files: {self.persistent_files}")

    async def _validate_release_info(self) -> None:
        self._is_valid = False
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
                    uinfo: Dict[str, str] = jsonw.loads(data)
                    project_name = uinfo["project_name"]
                    owner = uinfo["project_owner"]
                    self.version = uinfo["version"]
                    self.asset_name = uinfo.get("asset_name", None)
                except Exception:
                    logging.exception("Failed to load release_info.json.")
                else:
                    self._is_valid = True
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
                    if self.type == AppType.EXECUTABLE:
                        if self.asset_name is None:
                            self.warnings.append(
                                "Executable types require the 'asset_name' field in "
                                "release_info.json"
                            )
                            self._is_valid = False
                        else:
                            fname = self.asset_name
                            exec_file = self.path.joinpath(fname)
                            if not exec_file.exists():
                                self.warnings.append(
                                    f"File {fname} not found in configured path for "
                                    "executable type"
                                )
                                self._is_valid = False
            elif self.type == AppType.WEB:
                version_path = self.path.joinpath(".version")
                if version_path.is_file():
                    version = await eventloop.run_in_thread(version_path.read_text)
                    self.version = version.strip()
                self._is_valid = await self._detect_fallback()
        if not self._is_valid:
            self.warnings.append("Failed to validate installation")
            if self.server.is_debug_enabled():
                self.log_info("Debug Enabled, overriding validity checks")

    async def _detect_fallback(self) -> bool:
        # Only used by "web" app types to fallback on the previous version info
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
                    mdata: Dict[str, Any] = jsonw.loads(mtext)
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
        storage = await super().initialize()
        fm: FileManager = self.server.lookup_component("file_manager")
        self._path_writable = not fm.check_reserved_path(
            self.path, need_write=True, raise_error=False
        )
        if self._path_writable and not self.path.joinpath(".writeable").is_file():
            fm.add_reserved_path(f"update_manager {self.name}", self.path)
        await self._validate_release_info()
        if self.version == "?":
            self.version = storage.get("version", "?")
        self.remote_version = storage.get('remote_version', "?")
        self.rollback_version = storage.get('rollback_version', self.version)
        self.rollback_repo = storage.get(
            'rollback_repo', self.repo if self._is_valid else "?"
        )
        self.last_error = storage.get('last_error', "")
        dl_info: List[Any] = storage.get('dl_info', ["?", "?", 0])
        self.dl_info = cast(Tuple[str, str, int], tuple(dl_info))
        if not self.needs_refresh():
            self._log_app_info()
        return storage

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage.update({
            "version": self.version,
            "remote_version": self.remote_version,
            "rollback_version": self.rollback_version,
            "rollback_repo": self.rollback_repo,
            "dl_info": list(self.dl_info),
            "last_error": self.last_error
        })
        return storage

    async def refresh(self) -> None:
        try:
            await self._validate_release_info()
            await self._get_remote_version()
        except Exception:
            logging.exception("Error Refreshing Client")
        self._log_app_info()
        self._save_state()

    async def _fetch_github_version(
        self, repo: Optional[str] = None, tag: Optional[str] = None
    ) -> Dict[str, Any]:
        if repo is None:
            if not self._is_valid:
                self.log_info("Invalid Installation, aborting remote refresh")
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
        assets: List[Dict[str, Any]] = result.get("assets", [{}])
        release_asset: Dict[str, Any] = assets[0] if assets else {}
        if self.asset_name is not None:
            for asset in assets:
                if asset.get("name", "") == self.asset_name:
                    release_asset = asset
                    break
            else:
                logging.info(f"Asset '{self.asset_name}' not found")
        dl_url: str = release_asset.get('browser_download_url', "?")
        content_type: str = release_asset.get('content_type', "?")
        size: int = release_asset.get('size', 0)
        self.dl_info = (dl_url, content_type, size)
        self._is_prerelease = result.get('prerelease', False)

    def _log_app_info(self):
        warn_str = ""
        if self.warnings or self.anomalies:
            warn_str = "\nWarnings:\n"
            warn_str += "\n".join(
                [f" {item}" for item in self.warnings + self.anomalies]
            )
        dl_url, content_type, size = self.dl_info
        self.log_info(
            f"Detected\n"
            f"Repo: {self.repo}\n"
            f"Channel: {self.channel}\n"
            f"Path: {self.path}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"Valid: {self._is_valid}\n"
            f"Fallback Detected: {self._is_fallback}\n"
            f"Pre-release: {self._is_prerelease}\n"
            f"Download Url: {dl_url}\n"
            f"Download Size: {size}\n"
            f"Content Type: {content_type}\n"
            f"Rollback Version: {self.rollback_version}\n"
            f"Rollback Repo: {self.rollback_repo}"
            f"{warn_str}"
        )

    def _extract_release(
        self, persist_dir: pathlib.Path, release_file: pathlib.Path
    ) -> None:
        if not persist_dir.exists():
            persist_dir.mkdir()
        if self.path.is_dir():
            # find and move persistent files
            for src_path in self.path.iterdir():
                fname = src_path.name
                if fname in self.persistent_files:
                    dest_path = persist_dir.joinpath(fname)
                    dest_dir = dest_path.parent
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src_path), str(dest_path))
            shutil.rmtree(self.path)
        self.path.mkdir()
        with zipfile.ZipFile(release_file) as zf:
            for zip_entry in zf.filelist:
                dest = pathlib.Path(zf.extract(zip_entry, str(self.path)))
                dest.chmod((zip_entry.external_attr >> 16) & 0o777)
        # Move temporary files back into
        for src_path in persist_dir.iterdir():
            dest_path = self.path.joinpath(src_path.name)
            dest_dir = dest_path.parent
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dest_path))

    def _set_exec_perms(self, exec: pathlib.Path) -> None:
        req_perms = stat.S_IXUSR | stat.S_IXGRP
        kest_perms = stat.S_IMODE(exec.stat().st_mode)
        if req_perms & kest_perms != req_perms:
            try:
                exec.chmod(kest_perms | req_perms)
            except OSError:
                logging.exception(
                    f"Failed to set executable permission for file {exec}"
                )

    async def _finalize_executable(self, tmp_file: pathlib.Path, new_ver: str) -> None:
        if not self.type == AppType.EXECUTABLE:
            return
        # Remove existing binary
        exec_path = self.path.joinpath(tmp_file.name)
        if exec_path.is_file():
            exec_path.unlink()
        eventloop = self.server.get_event_loop()
        # move download to the configured path
        dest = await eventloop.run_in_thread(
            shutil.move, str(tmp_file), str(self.path)
        )
        dest_path = pathlib.Path(dest)
        # give file executable permissions
        self._set_exec_perms(dest_path)
        # Update release_info.json.  This is required as executable distributions
        # can't be bundled with release info.
        rinfo = self.path.joinpath("release_info.json")
        if not rinfo.is_file():
            return
        eventloop = self.server.get_event_loop()
        # If the new version does not match the version in release_info.json,
        # update it.
        data = await eventloop.run_in_thread(rinfo.read_text)
        uinfo: Dict[str, Any] = jsonw.loads(data)
        if uinfo["version"] != new_ver:
            uinfo["version"] = new_ver
            await eventloop.run_in_thread(rinfo.write_bytes, jsonw.dumps(uinfo))

    async def update(
        self,
        rollback_info: Optional[Tuple[str, str, int]] = None,
        is_recover: bool = False,
        force_dep_update: bool = False
    ) -> bool:
        if not self._is_valid:
            raise self.server.error(
                f"{self.prefix}Invalid install detected, aborting update"
            )
        if rollback_info is not None:
            dl_url, content_type, size = rollback_info
            start_msg = "Rolling Back..." if not is_recover else "Recovering..."
            new_ver = self.rollback_version if not is_recover else self.version
        else:
            if self.remote_version == "?":
                await self._get_remote_version()
                if self.remote_version == "?":
                    raise self.server.error(
                        f"{self.prefix}Unable to locate update"
                    )
            new_ver = self.remote_version
            dl_url, content_type, size = self.dl_info
            if self.version == self.remote_version:
                # Already up to date
                return False
            start_msg = "Updating..."
        if dl_url == "?":
            raise self.server.error(f"{self.prefix}Invalid download url")
        current_version = self.version
        event_loop = self.server.get_event_loop()
        self.notify_status(start_msg)
        self.notify_status("Downloading Release...")
        dep_info: Optional[Dict[str, Any]] = None
        if self.type in (AppType.ZIP, AppType.EXECUTABLE):
            dep_info = await self._collect_dependency_info()
        td = await self.cmd_helper.create_tempdir(self.name, "app")
        try:
            tempdir = pathlib.Path(td.name)
            if self.asset_name is not None:
                temp_download_file = tempdir.joinpath(self.asset_name)
            else:
                temp_download_file = tempdir.joinpath(f"{self.name}.zip")
            temp_persist_dir = tempdir.joinpath(self.name)
            client = self.cmd_helper.get_http_client()
            await client.download_file(
                dl_url, content_type, temp_download_file, size,
                self.cmd_helper.on_download_progress
            )
            self.notify_status(
                f"Download Complete, extracting release to '{self.path}'"
            )
            if self.type == AppType.EXECUTABLE:
                await self._finalize_executable(temp_download_file, new_ver)
            else:
                await event_loop.run_in_thread(
                    self._extract_release, temp_persist_dir, temp_download_file
                )
        finally:
            await event_loop.run_in_thread(td.cleanup)
        if dep_info is not None:
            await self._update_dependencies(dep_info, force_dep_update)
        self.version = new_ver
        await self._validate_release_info()
        if self._is_valid and rollback_info is None:
            self.rollback_version = current_version
            self.rollback_repo = self.repo
        self._log_app_info()
        self._save_state()
        await self.restart_service()
        msg = "Update Finished..." if rollback_info is None else "Rollback Complete"
        self.notify_status(msg, is_complete=True)
        return True

    async def recover(
        self, hard: bool = False, force_dep_update: bool = False
    ) -> None:
        await self.update(self.dl_info, True, force_dep_update)

    async def rollback(self) -> bool:
        if self.rollback_version == "?" or self.rollback_repo == "?":
            raise self.server.error("Incomplete Rollback Data", False)
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

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        anomalies = self.anomalies if self.report_anomalies else []
        status.update({
            'name': self.name,
            'repo_name': self.project_name,
            'owner': self.owner,
            'version': self.version,
            'remote_version': self.remote_version,
            'rollback_version': self.rollback_version,
            'last_error': self.last_error,
            'warnings': self.warnings,
            'anomalies': anomalies
        })
        return status
