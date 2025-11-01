# Python Package Update Deployment
#
# Copyright (C) 2024  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import re
import logging
from enum import Enum
from ...utils.source_info import normalize_project_name, load_distribution_info
from ...utils.versions import PyVersion, GitVersion
from ...utils.sysdeps_parser import SysDepsParser
from ...utils import pip_utils, json_wrapper
from .app_deploy import AppDeploy, Channel

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
    cast
)

if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ...utils.source_info import PackageInfo
    from ...components.file_manager.file_manager import FileManager

class PackageSource(Enum):
    PIP = 0
    GITHUB = 1
    UNKNOWN = 2


class PythonDeploy(AppDeploy):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, "Python Package")
        self._configure_virtualenv(config)
        if self.virtualenv is None:
            raise config.error(
                f"[{config.get_name()}]: Option 'virtualenv' must specify a valid "
                "the path to a Python virtualenv"
            )
        fm: FileManager = self.server.lookup_component("file_manager")
        fm.add_reserved_path(f"update_manager {self.name}", self.virtualenv)
        self._configure_managed_services(config)
        self.primary_branch = config.get("primary_branch", None)
        self.project_name = config.get("project_name", self.name)
        self.extras: str | None = None
        extras_match = re.match(r"([^[]+)\[([^]]+)\]", self.project_name)
        if extras_match is not None:
            self.project_name = extras_match.group(1)
            self.extras = extras_match.group(2)
        self.source: PackageSource = PackageSource.UNKNOWN
        self.repo_url: str = ""
        self.repo_owner: str = "?"
        self.repo_name: str = "?"
        self.current_version: PyVersion = PyVersion("?")
        self.git_version: GitVersion = GitVersion("?")
        self.current_sha: str = "?"
        self.upstream_version: PyVersion = self.current_version
        self.upstream_sha: str = "?"
        self.rollback_version: PyVersion = self.current_version
        self.rollback_ref: str = "?"
        self.warnings: List[str] = []
        package_info = load_distribution_info(self.virtualenv, self.project_name)
        self._detect_update_source(package_info)
        self._update_current_version(package_info)
        self.changelog: str = self._get_url(["changelog"], package_info)
        self.system_deps = self._parse_system_dependencies(package_info)
        self._is_valid = len(self.warnings) == 0

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.upstream_sha = storage.get("upstream_commit", "?")
        self.upstream_version = PyVersion(storage.get("upstream_version", "?"))
        self.rollback_ref = storage.get("rollback_ref", "?")
        self.rollback_version = PyVersion(storage.get("rollback_version", "?"))
        if not self.needs_refresh():
            self._log_package_info()
        return storage

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage["upstream_commit"] = self.upstream_sha
        storage["upstream_version"] = self.upstream_version.full_version
        storage["rollback_ref"] = self.rollback_ref
        storage["rollback_version"] = self.rollback_version.full_version
        return storage

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        status.update({
            "detected_type": "python_package",
            "name": self.name,
            "branch": self.primary_branch,
            "owner": self.repo_owner,
            "repo_name": self.repo_name,
            "version": self.current_version.short_version,
            "remote_version": self.upstream_version.short_version,
            "rollback_version": self.rollback_version.short_version,
            "current_hash": self.current_sha,
            "remote_hash": self.upstream_sha,
            "is_dirty": self.git_version.dirty,
            "changelog_url": self.changelog,
            "full_version_string": self.current_version.full_version,
            "pristine": not self.git_version.dirty,
            "warnings": self.warnings
        })
        return status

    def _add_warning(self, msg: str) -> None:
        self.warnings.append(msg)
        self.log_info(msg)

    def _detect_update_source(self, package_info: PackageInfo) -> None:
        self.source = PackageSource.UNKNOWN
        direct_url_data = package_info.direct_url_data
        if direct_url_data is None:
            self.source = PackageSource.PIP
            self.repo_url = self._get_url(["repository", "repo"], package_info)
            self._match_repo_url()
            return
        self.log_debug(f"Direct URL info: {direct_url_data}")
        vcs_info: Dict[str, str] = direct_url_data.get("vcs_info", {})
        if vcs_info.get("vcs", "") != "git":
            self._add_warning(
                "Package installed from source other than pypi or git: "
                f"{direct_url_data}"
            )
            return
        try:
            self.current_sha = vcs_info["commit_id"]
            self.repo_url = direct_url_data["url"]
        except KeyError:
            self._add_warning("Failed to retrieve direct_url vcs info")
            return
        if not self._match_repo_url():
            self._add_warning(f"Invalid repo url: {self.repo_url}")
            return
        self.source = PackageSource.GITHUB

    def _match_repo_url(self) -> bool:
        url_match = re.match(
            r"https://(?:www\.)?github\.com/(?P<owner>.+?)/(?P<proj>.+?)(?:\.git|$)",
            self.repo_url, re.IGNORECASE
        )
        if url_match is None:
            return False
        self.repo_owner = url_match["owner"] or "?"
        self.repo_name = url_match["proj"] or "?"
        return True

    def _get_url(self, keys: List[str], package_info: PackageInfo) -> str:
        release_info = package_info.release_info
        primary = keys[0]
        if release_info is not None:
            urls: Dict[str, Any] = release_info.get("urls", {})
            for name, url in urls.items():
                if name.lower() in keys:
                    return url
            self.log_debug(f"Unable to find {primary} url in release info")
        # Fallback to Metadata
        metadata = package_info.metadata
        md_urls: Optional[List[str]] = metadata.get_all("Project-URL", None)
        if md_urls is not None:
            for url in md_urls:
                key, url = url.split(",", maxsplit=1)
                key = key.lower().strip()
                if key in keys:
                    return url.strip()
        self.log_info(f"Unable to find {primary} url in metadata")
        return ""

    def _update_current_version(self, package_info: PackageInfo) -> bool:
        pkg_version = ""
        release_info = package_info.release_info
        metadata = package_info.metadata
        if release_info is not None:
            if self.current_sha == "?":
                self.current_sha = release_info.get("commit_sha", "?")
            self.git_version = GitVersion(release_info.get("git_version", "?"))
            pkg_version = release_info.get("package_version", "")
        if "Version" in metadata:
            pkg_version = metadata["Version"]
        if not pkg_version:
            self._add_warning("Failed to detect package version")
            return False
        self.current_version = PyVersion(pkg_version)
        if not self.current_version.is_valid_version():
            self._add_warning("Failed to parse package version")
            return False
        local = self.current_version.local
        if self.current_sha == "?":
            if self.source != PackageSource.GITHUB:
                self.current_sha = "not-specified"
            elif local:
                self.current_sha = local[1:].split(".", 1)[0]
        if not self.git_version.is_valid_version():
            self.git_version = self.current_version.convert_to_git()
        return True

    def _parse_system_dependencies(self, package_info: PackageInfo) -> List[str]:
        rinfo = package_info.release_info
        if rinfo is None:
            return []
        dep_info = rinfo.get("system_dependencies", {})
        parser = SysDepsParser()
        return parser.parse_dependencies(dep_info)

    async def _update_local_state(self) -> None:
        self.warnings.clear()
        eventloop = self.server.get_event_loop()
        try:
            assert self.virtualenv is not None
            package_info = await eventloop.run_in_thread(
                load_distribution_info, self.virtualenv, self.project_name
            )
        except self.server.error:
            self._add_warning("Failed to parse package info")
        else:
            self.git_version = GitVersion("?")
            self.current_sha = "?"
            self.current_version = PyVersion("?")
            self._detect_update_source(package_info)
            self._update_current_version(package_info)
            self.changelog = self._get_url(["changelog"], package_info)
            self.system_deps = self._parse_system_dependencies(package_info)
        self._is_valid = len(self.warnings) == 0

    async def refresh(self) -> None:
        try:
            await self._update_local_state()
            if self.source == PackageSource.PIP:
                await self._refresh_pip()
            elif self.source == PackageSource.GITHUB:
                await self._refresh_github()
            else:
                self.log_info("Cannot refresh, package source is unknown")
        except asyncio.CancelledError:
            raise
        except Exception:
            self.log_exc(f"Error Refreshing Python Package: {self.name}")
        self._log_package_info()
        self._save_state()

    async def _refresh_pip(self) -> None:
        # Perform a dry-run install to see if an update is available.
        # Currently this is the most reliable way to fetch the latest
        # version from an index, as we can't assume configurations
        # will use PyPI.
        self.log_info("Requesting package info via PIP...")
        norm_name = normalize_project_name(self.project_name)
        assert self.pip_cmd is not None
        pip_args = "install -U --quiet"
        if self.channel == Channel.BETA:
            pip_args = f"{pip_args} --pre"
        pip_args = f"{pip_args} --dry-run --no-deps --report - {norm_name}"
        pip_exec = pip_utils.AsyncPipExecutor(self.pip_cmd, self.server)
        await self._update_pip(pip_exec)
        resp = await pip_exec.call_pip_with_response(pip_args, timeout=1200.)
        data: Dict[str, Any] = json_wrapper.loads(resp)
        install_data: List[Dict[str, Any]] = data.get("install", [])
        if not install_data:
            # No update available
            self.upstream_version = self.current_version
            return
        metadata: Dict[str, Any] = install_data[0].get("metadata", {})
        name: str = normalize_project_name(metadata.get("name", ""))
        if len(install_data) > 1 and name != norm_name:
            for inst in install_data[1:]:
                md: Dict[str, Any] = inst.get("metadata", {})
                name = normalize_project_name(md.get("name", ""))
                if name == norm_name:
                    metadata = md
                    break
            else:
                raise self.server.error("Failed to find metadata for package")
        version: str = metadata.get("version", "?")
        self.upstream_version = PyVersion(version)
        if self.current_version < self.upstream_version:
            self.upstream_sha = "update-available"
        else:
            self.upstream_sha = self.current_sha

    async def _refresh_github(self) -> None:
        repo = f"{self.repo_owner}/{self.repo_name}"
        client = self.cmd_helper.get_http_client()
        if self.channel == Channel.DEV:
            resource = f"/repos/{repo}/commits?per_page=1"
            if self.primary_branch is not None:
                resource += f"&sha={self.primary_branch}"
            resp = await client.github_api_request(
                resource, attempts=3, retry_pause_time=.5
            )
            if resp.status_code != 304 and resp.has_error():
                self.log_info(f"Github Request Error - {resp.error}")
                return
            commit_list: List[Dict[str, Any]] = cast(list, resp.json())
            if not commit_list:
                self.log_info("No commits found")
                return
            self.upstream_sha = commit_list[0]["sha"]
            self.upstream_version = self.current_version
            if self.upstream_sha != self.current_sha:
                local_part = f"g{self.upstream_sha[:8]}"
                bumped = self.current_version.bump_local_version(local_part)
                self.upstream_version = bumped
            return
        if self.channel == Channel.STABLE:
            resource = f"repos/{repo}/releases/latest"
        else:
            resource = f"repos/{repo}/releases?per_page=1"
        resp = await client.github_api_request(
            resource, attempts=3, retry_pause_time=.5
        )
        if resp.status_code != 304 and resp.has_error():
            self.log_info(f"Github Request Error - {resp.error}")
            return
        release = resp.json()
        result: Dict[str, Any] = {}
        if isinstance(release, list):
            if release:
                result = release[0]
        else:
            result = release
        if not result:
            self.log_info("No releases found")
            self.upstream_sha = self.current_sha
            self.upstream_version = self.current_version
            return
        self.upstream_version = PyVersion(result["tag_name"])
        if self.upstream_version > self.current_version:
            self.upstream_sha = "update-available"

    async def update(self, rollback: bool = False) -> bool:
        project_name = normalize_project_name(self.project_name)
        if self.extras is not None:
            project_name = f"{project_name}[{self.extras}]"
        assert self.pip_cmd is not None
        current_version = self.current_version
        current_ref = self.current_version.tag
        install_ver = self.rollback_version if rollback else self.upstream_version
        if (
            not install_ver.is_valid_version() or
            (current_version.is_valid_version() and current_version == install_ver)
        ):
            # Invalid install version or requested version already installed
            return False
        pip_exec = pip_utils.AsyncPipExecutor(
            self.pip_cmd, self.server, self.cmd_helper.notify_update_response
        )
        pip_args = "install -U --upgrade-strategy eager"
        if self.source == PackageSource.PIP:
            # We can't depend on the SHA being available for PyPI packages,
            # so we must compare versions
            if self.channel == Channel.BETA:
                pip_args = f"{pip_args} --pre"
            pip_args = f"{pip_args} {project_name}"
            if rollback:
                pip_args += f"=={self.rollback_ref}"
        elif self.source == PackageSource.GITHUB:
            repo = f"{self.repo_owner}/{self.repo_name}"
            if rollback:
                repo += f"@{self.rollback_ref}"
            elif self.channel == Channel.DEV:
                current_ref = self.current_sha
                if self.primary_branch is not None:
                    repo += f"@{self.primary_branch}"
            else:
                repo += f"@{self.upstream_version.tag}"
            pip_args = f"{pip_args} '{project_name} @ git+https://github.com/{repo}'"
        else:
            raise self.server.error("Cannot update, package source is unknown")
        await self._update_pip(pip_exec)
        sys_deps = self.system_deps
        source = self.source.name
        self.notify_status(f"Updating Python Package {self.name} from {source}...")
        await pip_exec.call_pip(pip_args, 3600, sys_env_vars=self.pip_env_vars)
        await self._update_local_state()
        if not rollback:
            self.rollback_version = current_version
            self.rollback_ref = current_ref
            self.upstream_sha = self.current_sha
            self.upstream_version = self.current_version
        await self._update_sys_deps(sys_deps)
        self._log_package_info()
        self._save_state()
        await self.restart_service()
        self.notify_status("Update Finished...", is_complete=True)
        return True

    async def recover(
        self, hard: bool = False, force_dep_update: bool = False
    ) -> None:
        pass

    async def rollback(self) -> bool:
        if self.rollback_ref == "?" or not self.rollback_version.is_valid_version():
            return False
        await self.update(rollback=True)
        return True

    async def _update_sys_deps(self, prev_deps: List[str]) -> None:
        new_deps = self.system_deps
        deps_diff = list(set(new_deps) - set(prev_deps))
        if new_deps or prev_deps:
            self.log_debug(
                f"Pre-update system dependencies: {prev_deps}\n"
                f"Post-update system dependencies: {new_deps}\n"
                f"Difference to be installed: {deps_diff}"
            )
        if deps_diff:
            await self._install_packages(deps_diff)

    def _log_package_info(self) -> None:
        logging.info(
            f"Python Package {self.name} detected:\n"
            f"Channel: {self.channel}\n"
            f"Package Source: {self.source.name}\n"
            f"Repo Owner: {self.repo_owner}\n"
            f"Repo Name: {self.repo_name}\n"
            f"Repo URL: {self.repo_url}\n"
            f"Changelog URL: {self.changelog}\n"
            f"Full Version String: {self.current_version.full_version}\n"
            f"Current Version: {self.current_version.short_version}\n"
            f"Current Commit SHA: {self.current_sha}\n"
            f"Upstream Version: {self.upstream_version.short_version}\n"
            f"Upstream Commit SHA: {self.upstream_sha}\n"
            f"Converted Git Version: {self.git_version}\n"
            f"Rollback Version: {self.rollback_version}\n"
            f"Rollback Ref: {self.rollback_ref}\n"
        )
