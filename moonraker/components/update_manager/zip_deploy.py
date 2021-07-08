# Zip Application Deployment implementation
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import os
import pathlib
import json
import shutil
import re
import time
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from tornado.ioloop import IOLoop
from .app_deploy import AppDeploy
from utils import verify_source

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Optional,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from .update_manager import CommandHelper

RINFO_KEYS = [
    "git_version", "long_version", "commit_hash", "source_checksum",
    "ignored_exts", "ignored_dirs", "build_date", "channel",
    "owner_repo", "host_repo", "release_tag"
]

class ZipDeploy(AppDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper,
                 app_params: Optional[Dict[str, Any]]
                 ) -> None:
        super().__init__(config, cmd_helper, app_params)
        self.official_repo: str = "?"
        self.owner: str = "?"
        # Extract repo from origin for validation
        match = re.match(r"https?://(?:www\.)?github.com/([^/]+/[^.]+)",
                         self.origin)
        if match is not None:
            self.official_repo = match.group(1)
            self.owner = self.official_repo.split('/')[0]
        else:
            raise config.error(
                "Invalid url set for 'origin' option in section "
                f"[{config.get_name()}].  Unable to extract owner/repo.")
        self.host_repo: str = config.get('host_repo', self.official_repo)
        self.source_checksum: str = ""
        self.pristine = False
        self.verified = False
        self.build_date: int = 0
        self.full_version: str = "?"
        self.short_version: str = "?"
        self.commit_hash: str = "?"
        self.lastest_hash: str = "?"
        self.latest_version: str = "?"
        self.latest_checksum: str = "?"
        self.latest_build_date: int = 0
        self.commit_log: List[Dict[str, Any]] = []
        self.package_list: List[str] = []
        self.python_pkg_list: List[str] = []
        self.release_download_info: Tuple[str, str, int] = ("?", "?", 0)
        self.errors: List[str] = []
        self.mutex: asyncio.Lock = asyncio.Lock()
        self.refresh_event: Optional[asyncio.Event] = None

    @staticmethod
    async def from_application(app: AppDeploy) -> ZipDeploy:
        new_app = ZipDeploy(app.config, app.cmd_helper, app.app_params)
        await new_app.reinstall()
        return new_app

    async def _parse_info_file(self, file_name: str) -> Dict[str, Any]:
        try:
            info_file = self.path.joinpath(file_name)
            with ThreadPoolExecutor(max_workers=1) as tpe:
                info_bytes = await IOLoop.current().run_in_executor(
                    tpe, info_file.read_text)
            info: Dict[str, Any] = json.loads(info_bytes)
        except Exception:
            self.log_exc(f"Unable to parse info file {file_name}")
            info = {}
        return info

    def _get_tag_version(self, version_string: str) -> str:
        tag_version: str = "?"
        ver_match = re.match(r"v\d+\.\d+\.\d-\d+", version_string)
        if ver_match:
            tag_version = ver_match.group()
        return tag_version

    async def refresh(self) -> None:
        if self.refresh_event is not None:
            await self.refresh_event.wait()
            return
        async with self.mutex:
            self.refresh_event = asyncio.Event()
            try:
                await self._update_repo_state()
            except Exception:
                self.verified = False
                self.log_exc("Error refreshing application state")
            self.refresh_event.set()
            self.refresh_event = None

    async def _update_repo_state(self) -> None:
        self.errors = []
        self._is_valid = False
        self.verified = False
        release_info = await self._parse_info_file(".release_info")
        dep_info = await self._parse_info_file(".dependencies")
        for key in RINFO_KEYS:
            if key not in release_info:
                self._add_error(f"Missing release info item: {key}")
        local_channel = release_info.get('channel', "?")
        self.need_channel_update = self.channel != local_channel
        self.full_version = release_info.get('long_version', "?")
        self.short_version = self._get_tag_version(
            release_info.get('git_version', ""))
        self.commit_hash = release_info.get('commit_hash', "?")
        self.build_date = release_info.get('build_date', 0)
        owner_repo = release_info.get('owner_repo', "?")
        if self.official_repo != owner_repo:
            self._add_error(
                f"Owner repo mismatch. Received {owner_repo}, "
                f"official: {self.official_repo}")
        # validate the local source code
        with ThreadPoolExecutor(max_workers=1) as tpe:
            res = await IOLoop.current().run_in_executor(
                tpe, verify_source, self.path)
        if res is not None:
            self.source_checksum, self.pristine = res
            if self.name in ["moonraker", "klipper"]:
                self.server.add_log_rollover_item(
                    f"{self.name}_validation",
                    f"{self.name} checksum: {self.source_checksum}, "
                    f"pristine: {self.pristine}")
        else:
            self._add_error("Unable to validate source checksum")
            self.source_checksum = ""
            self.pristine = False
        self.package_list = sorted(dep_info.get(
            'debian', {}).get('packages', []))
        self.python_pkg_list = sorted(dep_info.get('python', []))
        # Retreive version info from github to check for updates and
        # validate local release info
        host_repo = release_info.get('host_repo', "?")
        release_tag = release_info.get('release_tag', "?")
        if host_repo != self.host_repo:
            self._add_error(
                f"Host repo mismatch, received: {host_repo}, "
                f"expected: {self.host_repo}. This could result in "
                " a failed update.")
        url = f"https://api.github.com/repos/{self.host_repo}/releases"
        current_release, latest_release = await self._fetch_github_releases(
            url, release_tag)
        await self._validate_current_release(release_info, current_release)
        if not self.errors:
            self.verified = True
        await self._process_latest_release(latest_release)
        self._log_zipapp_info()

    async def _fetch_github_releases(self,
                                     release_url: str,
                                     current_tag: Optional[str] = None
                                     ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        try:
            releases = await self.cmd_helper.github_api_request(release_url)
            assert isinstance(releases, list)
        except Exception:
            self.log_exc("Error fetching releases from GitHub")
            return {}, {}
        release: Dict[str, Any]
        latest_release: Dict[str, Any] = {}
        current_release: Dict[str, Any] = {}
        for release in releases:
            if not latest_release:
                if release['prerelease']:
                    if self.channel != "stable":
                        latest_release = release
                elif self.channel == "stable":
                    latest_release = release
            if current_tag is not None:
                if not current_release and release['tag_name'] == current_tag:
                    current_release = release
                if latest_release and current_release:
                    break
            elif latest_release:
                break
        return current_release, latest_release

    async def _validate_current_release(self,
                                        release_info: Dict[str, Any],
                                        release: Dict[str, Any]
                                        ) -> None:
        if not release:
            self._add_error("Unable to find current release on GitHub")
            return
        asset_info = self._get_asset_urls(release, ["RELEASE_INFO"])
        if "RELEASE_INFO" not in asset_info:
            self._add_error(
                "RELEASE_INFO not found in current release assets")
        info_url, content_type, size = asset_info['RELEASE_INFO']
        rinfo_bytes = await self.cmd_helper.http_download_request(
            info_url, content_type)
        github_rinfo: Dict[str, Any] = json.loads(rinfo_bytes)
        if github_rinfo.get(self.name, {}) != release_info:
            self._add_error(
                "Local release info does not match the remote")
        else:
            self.log_info("Current Release Info Validated")

    async def _process_latest_release(self, release: Dict[str, Any]):
        if not release:
            self._add_error("Unable to find latest release on GitHub")
            return
        zip_file_name = f"{self.name}.zip"
        asset_names = ["RELEASE_INFO", "COMMIT_LOG", zip_file_name]
        asset_info = self._get_asset_urls(release, asset_names)
        if "RELEASE_INFO" in asset_info:
            asset_url, content_type, size = asset_info['RELEASE_INFO']
            rinfo_bytes = await self.cmd_helper.http_download_request(
                asset_url, content_type)
            update_release_info: Dict[str, Any] = json.loads(rinfo_bytes)
            update_info = update_release_info.get(self.name, {})
            self.lastest_hash = update_info.get('commit_hash', "?")
            self.latest_checksum = update_info.get('source_checksum', "?")
            self.latest_version = self._get_tag_version(
                update_info.get('git_version', "?"))
            self.latest_build_date = update_info.get('build_date', 0)
        else:
            self._add_error(
                "RELEASE_INFO not found in latest release assets")
        self.commit_log = []
        if "COMMIT_LOG" in asset_info:
            asset_url, content_type, size = asset_info['COMMIT_LOG']
            commit_bytes = await self.cmd_helper.http_download_request(
                asset_url, content_type)
            commit_info: Dict[str, Any] = json.loads(commit_bytes)
            self.commit_log = commit_info.get(self.name, [])
        if zip_file_name in asset_info:
            self.release_download_info = asset_info[zip_file_name]
            self._is_valid = True
        else:
            self.release_download_info = ("?", "?", 0)
            self._add_error(f"Release asset {zip_file_name} not found")

    def _get_asset_urls(self,
                        release: Dict[str, Any],
                        filenames: List[str]
                        ) -> Dict[str, Tuple[str, str, int]]:
        asset_info: Dict[str, Tuple[str, str, int]] = {}
        asset: Dict[str, Any]
        for asset in release.get('assets', []):
            name = asset['name']
            if name in filenames:
                rinfo_url = asset['browser_download_url']
                content_type = asset['content_type']
                size = asset['size']
                asset_info[name] = (rinfo_url, content_type, size)
                filenames.remove(name)
                if not filenames:
                    break
        return asset_info

    def _add_error(self, warning: str):
        self.log_info(warning)
        self.errors.append(warning)

    def _log_zipapp_info(self):
        self.log_info(
            "\nZip Application Distribution Detected\n"
            f" Valid: {self._is_valid}\n"
            f" Verified: {self.verified}\n"
            f" Channel: {self.channel}\n"
            f" Repo: {self.official_repo}\n"
            f" Path: {self.path}\n"
            f" Pristine: {self.pristine}\n"
            f" Need Channel Update: {self.need_channel_update}\n"
            f" Commits Behind: {len(self.commit_log)}\n"
            f"Current Release Info:\n"
            f" Source Checksum: {self.source_checksum}\n"
            f" Commit SHA: {self.commit_hash}\n"
            f" Long Version: {self.full_version}\n"
            f" Short Version: {self.short_version}\n"
            f" Build Date: {time.ctime(self.build_date)}\n"
            f"Latest Available Release Info:\n"
            f" Source Checksum: {self.latest_checksum}\n"
            f" Commit SHA: {self.lastest_hash}\n"
            f" Version: {self.latest_version}\n"
            f" Build Date: {time.ctime(self.latest_build_date)}\n"
            f" Download URL: {self.release_download_info[0]}\n"
            f" Content Type: {self.release_download_info[1]}\n"
            f" Download Size: {self.release_download_info[2]}"
        )

    async def _update_dependencies(self,
                                   npm_hash,
                                   force: bool = False
                                   ) -> None:
        new_deps = await self._parse_info_file('.dependencies')
        system_pkgs = sorted(
            new_deps.get('debian', {}).get('packages', []))
        python_pkgs = sorted(new_deps.get('python', []))
        if system_pkgs:
            if force or system_pkgs != self.package_list:
                await self._install_packages(system_pkgs)
        if python_pkgs:
            if force or python_pkgs != self.python_pkg_list:
                await self._update_virtualenv(python_pkgs)
        ret = await self._check_need_update(npm_hash, self.npm_pkg_json)
        if force or ret:
            if self.npm_pkg_json is not None:
                self.notify_status("Updating Node Packages...")
                try:
                    await self.cmd_helper.run_cmd(
                        "npm ci --only=prod", notify=True, timeout=600.,
                        cwd=str(self.path))
                except Exception:
                    self.notify_status("Node Package Update failed")

    def _extract_release(self, release_zip: pathlib.Path) -> None:
        if self.path.is_dir():
            shutil.rmtree(self.path)
        os.mkdir(self.path)
        with zipfile.ZipFile(release_zip) as zf:
            zf.extractall(self.path)

    async def update(self, force_dep_update: bool = False) -> bool:
        async with self.mutex:
            if not self._is_valid:
                raise self.log_exc("Update aborted, repo not valid", False)
            if self.short_version == self.latest_version:
                # already up to date
                return False
            self.cmd_helper.notify_update_response(
                f"Updating Application {self.name}...")
            npm_hash = await self._get_file_hash(self.npm_pkg_json)
            dl_url, content_type, size = self.release_download_info
            self.notify_status("Starting Download...")
            with tempfile.TemporaryDirectory(
                    suffix=self.name, prefix="app") as tempdirname:
                tempdir = pathlib.Path(tempdirname)
                temp_download_file = tempdir.joinpath(f"{self.name}.zip")
                await self.cmd_helper.streaming_download_request(
                    dl_url, temp_download_file, content_type, size)
                self.notify_status(
                    f"Download Complete, extracting release to '{self.path}'")
                with ThreadPoolExecutor(max_workers=1) as tpe:
                    await IOLoop.current().run_in_executor(
                        tpe, self._extract_release, temp_download_file)
            await self._update_dependencies(npm_hash, force=force_dep_update)
            await self._update_repo_state()
            await self.restart_service()
            self.notify_status("Update Finished...", is_complete=True)
            return True

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        async with self.mutex:
            url = f"https://api.github.com/repos/{self.host_repo}/releases"
            releases = await self._fetch_github_releases(url)
            await self._process_latest_release(releases[1])
        await self.update(force_dep_update=force_dep_update)

    async def reinstall(self) -> None:
        await self.recover(force_dep_update=True)

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        # XXX - Currently this reports status matching
        # that of the git repo so as to not break existing
        # client functionality.  In the future it would be
        # good to report values that are specifc
        status.update({
            'remote_alias': "origin",
            'branch': "master",
            'owner': self.owner,
            'version': self.short_version,
            'remote_version': self.latest_version,
            'current_hash': self.commit_hash,
            'remote_hash': self.lastest_hash,
            'is_dirty': False,
            'detached': not self.verified,
            'commits_behind': self.commit_log,
            'git_messages': self.errors,
            'full_version_string': self.full_version,
            'pristine': self.pristine,
        })
        return status
