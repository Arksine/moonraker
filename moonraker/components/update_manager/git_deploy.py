# Git Deployment implementation
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import os
import pathlib
import shutil
import re
import logging
from .app_deploy import AppDeploy

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
    from ...confighelper import ConfigHelper
    from ...components import shell_command
    from .update_manager import CommandHelper
    from ..http_client import HttpClient

class GitDeploy(AppDeploy):
    def __init__(self, config: ConfigHelper, cmd_helper: CommandHelper) -> None:
        super().__init__(config, cmd_helper)
        self.repo = GitRepo(
            cmd_helper, self.path, self.name, self.origin,
            self.moved_origin, self.channel
        )
        if self.type != 'git_repo':
            self.need_channel_update = True

    @staticmethod
    async def from_application(app: AppDeploy) -> GitDeploy:
        new_app = GitDeploy(app.config, app.cmd_helper)
        await new_app.reinstall()
        return new_app

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.repo.restore_state(storage)
        return storage

    async def refresh(self) -> None:
        try:
            await self._update_repo_state()
        except Exception:
            logging.exception("Error Refreshing git state")

    async def _update_repo_state(self, need_fetch: bool = True) -> None:
        self._is_valid = False
        await self.repo.initialize(need_fetch=need_fetch)
        self.log_info(
            f"Channel: {self.channel}, "
            f"Need Channel Update: {self.need_channel_update}"
        )
        invalids = self.repo.report_invalids(self.primary_branch)
        if invalids:
            msgs = '\n'.join(invalids)
            self.log_info(
                f"Repo validation checks failed:\n{msgs}")
            if self.server.is_debug_enabled():
                self._is_valid = True
                self.log_info(
                    "Repo debug enabled, overriding validity checks")
            else:
                self.log_info("Updates on repo disabled")
        else:
            self._is_valid = True
            self.log_info("Validity check for git repo passed")
        self._save_state()

    async def update(self) -> bool:
        await self.repo.wait_for_init()
        if not self._is_valid:
            raise self.log_exc("Update aborted, repo not valid", False)
        if self.repo.is_dirty():
            raise self.log_exc(
                "Update aborted, repo has been modified", False)
        if self.repo.is_current():
            # No need to update
            return False
        self.cmd_helper.notify_update_response(
            f"Updating Application {self.name}...")
        dep_info = await self._collect_dependency_info()
        await self._pull_repo()
        # Check Semantic Versions
        await self._update_dependencies(dep_info)
        # Refresh local repo state
        await self._update_repo_state(need_fetch=False)
        await self.restart_service()
        self.notify_status("Update Finished...", is_complete=True)
        return True

    async def recover(self,
                      hard: bool = False,
                      force_dep_update: bool = False
                      ) -> None:
        self.notify_status("Attempting Repo Recovery...")
        dep_info = await self._collect_dependency_info()
        if hard:
            await self.repo.clone()
            await self._update_repo_state()
        else:
            self.notify_status("Resetting Git Repo...")
            await self.repo.reset()
            await self._update_repo_state()

        if self.repo.is_dirty() or not self._is_valid:
            raise self.server.error(
                "Recovery attempt failed, repo state not pristine", 500)
        await self._update_dependencies(dep_info, force=force_dep_update)
        await self.restart_service()
        self.notify_status("Reinstall Complete", is_complete=True)

    async def reinstall(self):
        # Clear the persistent storage prior to a channel swap.
        # After the next update is complete new data will be
        # restored.
        umdb = self.cmd_helper.get_umdb()
        await umdb.pop(self.name, None)
        await self.initialize()
        await self.recover(True, True)

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        status.update(self.repo.get_repo_status())
        return status

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage.update(self.repo.get_persistent_data())
        return storage

    async def _pull_repo(self) -> None:
        self.notify_status("Updating Repo...")
        try:
            await self.repo.fetch()
            if self.repo.is_detached():
                await self.repo.checkout()
            elif await self.repo.check_diverged():
                self.notify_status(
                    "Repo has diverged, attempting git reset"
                )
                await self.repo.reset()
            else:
                await self.repo.pull()
        except Exception as e:
            if self.repo.repo_corrupt:
                self._is_valid = False
                self._save_state()
                event_loop = self.server.get_event_loop()
                event_loop.delay_callback(
                    .2, self.cmd_helper.notify_update_refreshed
                )
            raise self.log_exc(str(e))

    async def _collect_dependency_info(self) -> Dict[str, Any]:
        pkg_deps = await self._parse_install_script()
        pyreqs = await self._parse_python_reqs()
        npm_hash = await self._get_file_hash(self.npm_pkg_json)
        logging.debug(
            f"\nApplication {self.name}: Pre-update dependencies:\n"
            f"Packages: {pkg_deps}\n"
            f"Python Requirements: {pyreqs}"
        )
        return {
            "system_packages": pkg_deps,
            "python_modules": pyreqs,
            "npm_hash": npm_hash
        }

    async def _update_dependencies(
        self, dep_info: Dict[str, Any], force: bool = False
    ) -> None:
        packages = await self._parse_install_script()
        modules = await self._parse_python_reqs()
        logging.debug(
            f"\nApplication {self.name}: Post-update dependencies:\n"
            f"Packages: {packages}\n"
            f"Python Requirements: {modules}"
        )
        if not force:
            packages = list(set(packages) - set(dep_info["system_packages"]))
            modules = list(set(modules) - set(dep_info["python_modules"]))
        logging.debug(
            f"\nApplication {self.name}: Dependencies to install:\n"
            f"Packages: {packages}\n"
            f"Python Requirements: {modules}\n"
            f"Force All: {force}"
        )
        if packages:
            await self._install_packages(packages)
        if modules:
            await self._update_python_requirements(modules)
        npm_hash: Optional[str] = dep_info["npm_hash"]
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

    async def _parse_install_script(self) -> List[str]:
        if self.install_script is None:
            return []
        # Open install file file and read
        inst_path: pathlib.Path = self.install_script
        if not inst_path.is_file():
            self.log_info(f"Failed to open install script: {inst_path}")
            return []
        event_loop = self.server.get_event_loop()
        data = await event_loop.run_in_thread(inst_path.read_text)
        plines: List[str] = re.findall(r'PKGLIST="(.*)"', data)
        plines = [p.lstrip("${PKGLIST}").strip() for p in plines]
        packages: List[str] = []
        for line in plines:
            packages.extend(line.split())
        if not packages:
            self.log_info(f"No packages found in script: {inst_path}")
        return packages

    async def _parse_python_reqs(self) -> List[str]:
        if self.python_reqs is None:
            return []
        pyreqs = self.python_reqs
        if not pyreqs.is_file():
            self.log_info(f"Failed to open python requirements file: {pyreqs}")
            return []
        eventloop = self.server.get_event_loop()
        data = await eventloop.run_in_thread(pyreqs.read_text)
        modules: List[str] = []
        for line in data.split("\n"):
            line = line.strip()
            if not line or line[0] == "#":
                continue
            modules.append(line)
        if not modules:
            self.log_info(
                f"No modules found in python requirements file: {pyreqs}"
            )
        return modules


GIT_ASYNC_TIMEOUT = 300.
GIT_ENV_VARS = {
    'GIT_HTTP_LOW_SPEED_LIMIT': "1000",
    'GIT_HTTP_LOW_SPEED_TIME ': "20"
}
GIT_MAX_LOG_CNT = 100
GIT_LOG_FMT = (
    "\"sha:%H%x1Dauthor:%an%x1Ddate:%ct%x1Dsubject:%s%x1Dmessage:%b%x1E\""
)
GIT_REF_FMT = (
    "'%(if)%(*objecttype)%(then)%(*objecttype) (*objectname)"
    "%(else)%(objecttype) %(objectname)%(end) %(refname)'"
)

class GitRepo:
    tag_r = re.compile(r"(v?\d+(?:\.\d+){1,2}(-(alpha|beta)(\.\d+)?)?)(-\d+)?")
    def __init__(self,
                 cmd_helper: CommandHelper,
                 git_path: pathlib.Path,
                 alias: str,
                 origin_url: str,
                 moved_origin_url: Optional[str],
                 channel: str
                 ) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper
        self.alias = alias
        self.git_path = git_path
        git_dir = git_path.parent
        git_base = git_path.name
        self.backup_path = git_dir.joinpath(f".{git_base}_repo_backup")
        self.origin_url = origin_url
        self.moved_origin_url = moved_origin_url
        self.recovery_message = \
            f"""
            Manually restore via SSH with the following commands:
            sudo service {self.alias} stop
            cd {git_dir}
            rm -rf {git_base}
            git clone {self.origin_url}
            sudo service {self.alias} start
            """

        self.init_evt: Optional[asyncio.Event] = None
        self.initialized: bool = False
        self.git_operation_lock = asyncio.Lock()
        self.fetch_timeout_handle: Optional[asyncio.Handle] = None
        self.fetch_input_recd: bool = False
        self.is_beta = channel == "beta"
        self.bound_repo = None
        if self.is_beta and self.alias == "klipper":
            # Bind Klipper Updates Moonraker
            self.bound_repo = "moonraker"

    def restore_state(self, storage: Dict[str, Any]) -> None:
        self.valid_git_repo: bool = storage.get('repo_valid', False)
        self.git_owner: str = storage.get('git_owner', "?")
        self.git_repo_name: str = storage.get('git_repo_name', "?")
        self.git_remote: str = storage.get('git_remote', "?")
        self.git_branch: str = storage.get('git_branch', "?")
        self.current_version: str = storage.get('current_version', "?")
        self.upstream_version: str = storage.get('upstream_version', "?")
        self.current_commit: str = storage.get('current_commit', "?")
        self.upstream_commit: str = storage.get('upstream_commit', "?")
        self.upstream_url: str = storage.get('upstream_url', "?")
        self.full_version_string: str = storage.get('full_version_string', "?")
        self.branches: List[str] = storage.get('branches', [])
        self.dirty: bool = storage.get('dirty', False)
        self.head_detached: bool = storage.get('head_detached', False)
        self.git_messages: List[str] = storage.get('git_messages', [])
        self.commits_behind: List[Dict[str, Any]] = storage.get(
            'commits_behind', [])
        self.tag_data: Dict[str, Any] = storage.get('tag_data', {})
        self.diverged: bool = storage.get("diverged", False)
        self.repo_verified: bool = storage.get(
            "verified", storage.get("is_valid", False)
        )
        self.repo_corrupt: bool = storage.get('corrupt', False)

    def get_persistent_data(self) -> Dict[str, Any]:
        return {
            'repo_valid': self.valid_git_repo,
            'git_owner': self.git_owner,
            'git_repo_name': self.git_repo_name,
            'git_remote': self.git_remote,
            'git_branch': self.git_branch,
            'current_version': self.current_version,
            'upstream_version': self.upstream_version,
            'current_commit': self.current_commit,
            'upstream_commit': self.upstream_commit,
            'upstream_url': self.upstream_url,
            'full_version_string': self.full_version_string,
            'branches': self.branches,
            'dirty': self.dirty,
            'head_detached': self.head_detached,
            'git_messages': self.git_messages,
            'commits_behind': self.commits_behind,
            'tag_data': self.tag_data,
            'diverged': self.diverged,
            'verified': self.repo_verified,
            'corrupt': self.repo_corrupt
        }

    async def initialize(self, need_fetch: bool = True) -> None:
        if self.init_evt is not None:
            # No need to initialize multiple requests
            await self.init_evt.wait()
            if self.initialized:
                return
        self.initialized = False
        self.init_evt = asyncio.Event()
        self.git_messages.clear()
        try:
            await self.update_repo_status()
            self._verify_repo()
            if not self.head_detached:
                # lookup remote via git config
                self.git_remote = await self.get_config_item(
                    f"branch.{self.git_branch}.remote")

            # Fetch the upstream url.  If the repo has been moved,
            # set the new url
            self.upstream_url = await self.remote(f"get-url {self.git_remote}")
            if await self._check_moved_origin():
                need_fetch = True

            if need_fetch:
                await self.fetch()
            self.diverged = await self.check_diverged()

            # Populate list of current branches
            blist = await self.list_branches()
            self.branches = []
            for branch in blist:
                branch = branch.strip()
                if branch[0] == "*":
                    branch = branch[2:]
                if branch[0] == "(":
                    continue
                self.branches.append(branch)

            # Parse GitHub Owner from URL
            owner_match = re.match(r"https?://[^/]+/([^/]+)", self.upstream_url)
            self.git_owner = "?"
            if owner_match is not None:
                self.git_owner = owner_match.group(1)

            # Parse GitHub Repository Name from URL
            repo_match = re.match(r".*\/([^\.]*).*", self.upstream_url)
            self.git_repo_name = "?"
            if repo_match is not None:
                self.git_repo_name = repo_match.group(1)
            self.current_commit = await self.rev_parse("HEAD")
            git_desc = await self.describe(
                "--always --tags --long --dirty")
            self.full_version_string = git_desc.strip()
            self.dirty = git_desc.endswith("dirty")
            self.tag_data = {}
            if self.is_beta and self.bound_repo is None:
                await self._get_beta_versions(git_desc)
            else:
                await self._get_dev_versions(git_desc)

            # Get Commits Behind
            self.commits_behind = []
            cbh = await self.get_commits_behind()
            if cbh:
                tagged_commits = await self.get_tagged_commits()
                debug_msg = '\n'.join([f"{k}: {v}" for k, v in
                                       tagged_commits.items()])
                logging.debug(f"Git Repo {self.alias}: Tagged Commits\n"
                              f"{debug_msg}")
                for i, commit in enumerate(cbh):
                    tag = tagged_commits.get(commit['sha'], None)
                    if i < 30 or tag is not None:
                        commit['tag'] = tag
                        self.commits_behind.append(commit)

            self.log_repo_info()
        except Exception:
            logging.exception(f"Git Repo {self.alias}: Initialization failure")
            raise
        else:
            self.initialized = True
            # If no exception was raised assume the repo is not corrupt
            self.repo_corrupt = False
        finally:
            self.init_evt.set()
            self.init_evt = None

    async def _check_moved_origin(self) -> bool:
        detected_origin = self.upstream_url.lower().strip()
        if not detected_origin.endswith(".git"):
            detected_origin += ".git"
        if (
            self.server.is_debug_enabled() or
            not detected_origin.startswith("http") or
            detected_origin == self.origin_url.lower()
        ):
            # Skip the moved origin check if:
            #  Repo Debug is enabled
            #  The detected origin url is not http(s)
            #  The detected origin matches the expected origin url
            return False
        moved = False
        client: HttpClient = self.server.lookup_component("http_client")
        check_url = detected_origin[:-4]
        logging.info(
            f"Git repo {self.alias}: Performing moved origin check - "
            f"{check_url}"
        )
        resp = await client.get(check_url, enable_cache=False)
        if not resp.has_error():
            final_url = resp.final_url.lower()
            if not final_url.endswith(".git"):
                final_url += ".git"
            logging.debug(f"Git repo {self.alias}: Resolved url - {final_url}")
            if final_url == self.origin_url.lower():
                logging.info(
                    f"Git Repo {self.alias}: Moved Repo Detected, Moving "
                    f"from {self.upstream_url} to {self.origin_url}")
                moved = True
                await self.remote(
                    f"set-url {self.git_remote} {self.origin_url}")
                self.upstream_url = self.origin_url
                if self.moved_origin_url is not None:
                    moved_origin = self.moved_origin_url.lower().strip()
                    if not moved_origin.endswith(".git"):
                        moved_origin += ".git"
                    if moved_origin != detected_origin:
                        self.server.add_warning(
                            f"Git Repo {self.alias}: Origin URL does not "
                            "not match configured 'moved_origin'option. "
                            f"Expected: {detected_origin}"
                        )
        else:
            logging.debug(f"Move Request Failed: {resp.error}")
        return moved

    async def _get_dev_versions(self, current_version: str) -> None:
        self.upstream_commit = await self.rev_parse(
            f"{self.git_remote}/{self.git_branch}")
        upstream_version = await self.describe(
            f"{self.git_remote}/{self.git_branch} "
            "--always --tags --long")
        # Get the latest tag as a fallback for shallow clones
        commit, tag = await self._parse_latest_tag()
        # Parse Version Info
        versions: List[str] = []
        for ver in [current_version, upstream_version]:
            tag_version = "?"
            ver_match = self.tag_r.match(ver)
            if ver_match:
                tag_version = ver_match.group()
            elif tag != "?":
                if len(versions) == 0:
                    count = await self.rev_list(f"{tag}..HEAD --count")
                    full_ver = f"{tag}-{count}-g{ver}-shallow"
                    self.full_version_string = full_ver
                else:
                    count = await self.rev_list(
                        f"{tag}..{self.upstream_commit} --count")
                tag_version = f"{tag}-{count}"
            versions.append(tag_version)
        self.current_version, self.upstream_version = versions
        if self.bound_repo is not None:
            await self._get_bound_versions(self.current_version)

    async def _get_beta_versions(self, current_version: str) -> None:
        upstream_commit, upstream_tag = await self._parse_latest_tag()
        ver_match = self.tag_r.match(current_version)
        current_tag = "?"
        if ver_match:
            current_tag = ver_match.group(1)
        elif upstream_tag != "?":
            count = await self.rev_list(f"{upstream_tag}..HEAD --count")
            full_ver = f"{upstream_tag}-{count}-g{current_version}-shallow"
            self.full_version_string = full_ver
            current_tag = upstream_tag
        self.upstream_commit = upstream_commit
        if current_tag == upstream_tag:
            self.upstream_commit = self.current_commit
        self.current_version = current_tag
        self.upstream_version = upstream_tag
        # Check the tag for annotations
        self.tag_data = await self.get_tag_data(upstream_tag)
        if self.tag_data:
            # TODO: need to force a repo update by resetting its refresh time?
            logging.debug(
                f"Git Repo {self.alias}: Found Tag Annotation: {self.tag_data}"
            )

    async def _get_bound_versions(self, current_version: str) -> None:
        if self.bound_repo is None:
            return
        umdb = self.cmd_helper.get_umdb()
        key = f"{self.bound_repo}.tag_data"
        tag_data: Dict[str, Any] = await umdb.get(key, {})
        if tag_data.get("repo", "") != self.alias:
            logging.info(
                f"Git Repo {self.alias}: Invalid bound tag data: "
                f"{tag_data}"
            )
            return
        if tag_data["branch"] != self.git_branch:
            logging.info(f"Git Repo {self.alias}: Repo not on bound branch")
            return
        bound_vlist: List[int] = tag_data["version_as_list"]
        current_vlist = self._convert_semver(current_version)
        if self.full_version_string.endswith("shallow"):
            # We need to recalculate the commit count for shallow clones
            if current_vlist[:4] == bound_vlist[:4]:
                commit = tag_data["commit"]
                tag = current_version.split("-")[0]
                try:
                    resp = await self.rev_list(f"{tag}..{commit} --count")
                    count = int(resp)
                except Exception:
                    count = 0
                bound_vlist[4] == count
        if current_vlist < bound_vlist:
            bound_ver_match = self.tag_r.match(tag_data["version"])
            if bound_ver_match is not None:
                self.upstream_commit = tag_data["commit"]
                self.upstream_version = bound_ver_match.group()
        else:
            # The repo is currently ahead of the bound tag/commmit,
            # so pin the version
            self.upstream_commit = self.current_commit
            self.upstream_version = self.current_version

    async def _parse_latest_tag(self) -> Tuple[str, str]:
        commit = tag = "?"
        try:
            commit = await self.rev_list("--tags --max-count=1")
            if not commit:
                return "?", "?"
            tag = await self.describe(f"--tags {commit}")
        except Exception:
            pass
        else:
            tag_match = self.tag_r.match(tag)
            if tag_match is not None:
                tag = tag_match.group(1)
            else:
                tag = "?"
        return commit, tag

    async def wait_for_init(self) -> None:
        if self.init_evt is not None:
            await self.init_evt.wait()
            if not self.initialized:
                raise self.server.error(
                    f"Git Repo {self.alias}: Initialization failure")

    async def update_repo_status(self) -> bool:
        async with self.git_operation_lock:
            self.valid_git_repo = False
            if not self.git_path.joinpath(".git").exists():
                logging.info(
                    f"Git Repo {self.alias}: path '{self.git_path}'"
                    " is not a valid git repo")
                return False
            await self._wait_for_lock_release()
            retries = 3
            while retries:
                self.git_messages.clear()
                try:
                    resp: Optional[str] = await self._run_git_cmd(
                        "status -u no", retries=1)
                except Exception:
                    retries -= 1
                    resp = None
                    # Attempt to recover from "loose object" error
                    if retries and self.repo_corrupt:
                        if not await self._repair_loose_objects():
                            # Since we are unable to recover, immediately
                            # return
                            return False
                else:
                    break
            if resp is None:
                return False
            resp = resp.strip().split('\n', 1)[0]
            self.head_detached = resp.startswith("HEAD detached")
            branch_info = resp.split()[-1]
            if self.head_detached:
                bparts = branch_info.split("/", 1)
                if len(bparts) == 2:
                    self.git_remote, self.git_branch = bparts
                else:
                    if self.git_remote == "?":
                        msg = "Resolve by manually checking out" \
                            " a branch via SSH."
                    else:
                        msg = "Defaulting to previously tracked " \
                            f"{self.git_remote}/{self.git_branch}."
                    logging.info(
                        f"Git Repo {self.alias}: HEAD detached on untracked "
                        f"commit {branch_info}. {msg}")
            else:
                self.git_branch = branch_info
            self.valid_git_repo = True
            return True

    async def check_diverged(self) -> bool:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            if self.head_detached:
                return False
            cmd = (
                "merge-base --is-ancestor HEAD "
                f"{self.git_remote}/{self.git_branch}"
            )
            for _ in range(3):
                try:
                    await self._run_git_cmd(
                        cmd, retries=1, corrupt_msg="error: "
                    )
                except self.cmd_helper.scmd_error as err:
                    if err.return_code == 1:
                        return True
                    if self.repo_corrupt:
                        raise
                else:
                    break
                await asyncio.sleep(.5)
            return False

    def log_repo_info(self) -> None:
        logging.info(
            f"Git Repo {self.alias} Detected:\n"
            f"Owner: {self.git_owner}\n"
            f"Repository Name: {self.git_repo_name}\n"
            f"Path: {self.git_path}\n"
            f"Remote: {self.git_remote}\n"
            f"Branch: {self.git_branch}\n"
            f"Remote URL: {self.upstream_url}\n"
            f"Current Commit SHA: {self.current_commit}\n"
            f"Upstream Commit SHA: {self.upstream_commit}\n"
            f"Current Version: {self.current_version}\n"
            f"Upstream Version: {self.upstream_version}\n"
            f"Is Dirty: {self.dirty}\n"
            f"Is Detached: {self.head_detached}\n"
            f"Commits Behind: {len(self.commits_behind)}\n"
            f"Tag Data: {self.tag_data}\n"
            f"Bound Repo: {self.bound_repo}\n"
            f"Diverged: {self.diverged}"
        )

    def report_invalids(self, primary_branch: str) -> List[str]:
        invalids: List[str] = []
        upstream_url = self.upstream_url.lower()
        if upstream_url[-4:] != ".git":
            upstream_url += ".git"
        if upstream_url != self.origin_url.lower():
            invalids.append(f"Unofficial remote url: {self.upstream_url}")
        if self.git_branch != primary_branch or self.git_remote != "origin":
            invalids.append(
                "Repo not on valid remote branch, expected: "
                f"origin/{primary_branch}, detected: "
                f"{self.git_remote}/{self.git_branch}")
        if self.head_detached:
            invalids.append("Detached HEAD detected")
        if self.diverged:
            invalids.append("Repo has diverged from remote")
        if not invalids:
            self.repo_verified = True
        return invalids

    def _verify_repo(self, check_remote: bool = False) -> None:
        if not self.valid_git_repo:
            raise self.server.error(
                f"Git Repo {self.alias}: repo not initialized")
        if check_remote:
            if self.git_remote == "?":
                raise self.server.error(
                    f"Git Repo {self.alias}: No valid git remote detected")

    async def reset(self) -> None:
        if self.git_remote == "?" or self.git_branch == "?":
            raise self.server.error("Cannot reset, unknown remote/branch")
        async with self.git_operation_lock:
            reset_cmd = f"reset --hard {self.git_remote}/{self.git_branch}"
            if self.is_beta:
                reset_cmd = f"reset --hard {self.upstream_commit}"
            await self._run_git_cmd(reset_cmd, retries=2)
            self.repo_corrupt = False

    async def fetch(self) -> None:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            await self._run_git_cmd_async(
                f"fetch {self.git_remote} --prune --progress")

    async def clean(self) -> None:
        self._verify_repo()
        async with self.git_operation_lock:
            await self._run_git_cmd("clean -d -f", retries=2)

    async def pull(self) -> None:
        self._verify_repo()
        if self.head_detached:
            raise self.server.error(
                f"Git Repo {self.alias}: Cannot perform pull on a "
                "detached HEAD")
        cmd = "pull --progress"
        if self.server.is_debug_enabled():
            cmd = f"{cmd} --rebase"
        if self.is_beta:
            cmd = f"{cmd} {self.git_remote} {self.upstream_commit}"
        async with self.git_operation_lock:
            await self._run_git_cmd_async(cmd)

    async def list_branches(self) -> List[str]:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd("branch --list")
            return resp.strip().split("\n")

    async def remote(self, command: str) -> str:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(
                f"remote {command}")
            return resp.strip()

    async def describe(self, args: str = "") -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"describe {args}".strip())
            return resp.strip()

    async def rev_parse(self, args: str = "") -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"rev-parse {args}".strip())
            return resp.strip()

    async def rev_list(self, args: str = "") -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"rev-list {args}".strip())
            return resp.strip()

    async def get_config_item(self, item: str) -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"config --get {item}")
            return resp.strip()

    async def checkout(self, branch: Optional[str] = None) -> None:
        self._verify_repo()
        async with self.git_operation_lock:
            if branch is None:
                if self.is_beta:
                    branch = self.upstream_commit
                else:
                    branch = f"{self.git_remote}/{self.git_branch}"
            await self._run_git_cmd(f"checkout -q {branch}")

    async def run_fsck(self) -> None:
        async with self.git_operation_lock:
            await self._run_git_cmd("fsck --full", timeout=300., retries=1)

    async def clone(self) -> None:
        async with self.git_operation_lock:
            if not self.repo_verified:
                raise self.server.error(
                    "Repo has not been verified, clone aborted"
                )
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Starting Clone Recovery...")
            event_loop = self.server.get_event_loop()
            if self.backup_path.exists():
                await event_loop.run_in_thread(shutil.rmtree, self.backup_path)
            await self._check_lock_file_exists(remove=True)
            git_cmd = f"clone {self.origin_url} {self.backup_path}"
            try:
                await self._run_git_cmd_async(git_cmd, 1, False, False)
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Git Repo {self.alias}: Git Clone Failed")
                raise self.server.error("Git Clone Error") from e
            if self.git_path.exists():
                await event_loop.run_in_thread(shutil.rmtree, self.git_path)
            await event_loop.run_in_thread(
                shutil.move, str(self.backup_path), str(self.git_path))
            self.repo_corrupt = False
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Git Clone Complete")

    async def get_commits_behind(self) -> List[Dict[str, Any]]:
        self._verify_repo()
        if self.is_current():
            return []
        async with self.git_operation_lock:
            if self.is_beta:
                ref = self.upstream_commit
            else:
                ref = f"{self.git_remote}/{self.git_branch}"
            resp = await self._run_git_cmd(
                f"log {self.current_commit}..{ref} "
                f"--format={GIT_LOG_FMT} --max-count={GIT_MAX_LOG_CNT}")
            commits_behind: List[Dict[str, Any]] = []
            for log_entry in resp.split('\x1E'):
                log_entry = log_entry.strip()
                if not log_entry:
                    continue
                log_items = [li.strip() for li in log_entry.split('\x1D')
                             if li.strip()]
                cbh = [li.split(':', 1) for li in log_items]
                commits_behind.append(dict(cbh))  # type: ignore
            return commits_behind

    async def get_tagged_commits(self) -> Dict[str, Any]:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(
                "for-each-ref --count=10 --sort='-creatordate' "
                f"--format={GIT_REF_FMT} 'refs/tags'")
            tagged_commits: Dict[str, Any] = {}
            for line in resp.split('\n'):
                parts = line.strip().split()
                if len(parts) != 3 or parts[0] != "commit":
                    continue
                sha, ref = parts[1:]
                tag = ref.split('/')[-1]
                tagged_commits[sha] = tag
            # Return tagged commits as SHA keys mapped to tag values
            return tagged_commits

    async def get_tag_data(self, tag: str) -> Dict[str, Any]:
        self._verify_repo()
        async with self.git_operation_lock:
            cmd = f"tag -l --format='%(contents)' {tag}"
            resp = (await self._run_git_cmd(cmd)).strip()
            req_fields = ["repo", "branch", "version", "commit"]
            tag_data: Dict[str, Any] = {}
            for line in resp.split("\n"):
                parts = line.strip().split(":", 1)
                if len(parts) != 2:
                    continue
                field, value = parts
                field = field.strip()
                if field not in req_fields:
                    continue
                tag_data[field] = value.strip()
            if len(tag_data) != len(req_fields):
                return {}
            vlist = self._convert_semver(tag_data["version"])
            tag_data["version_as_list"] = vlist
            return tag_data

    def get_repo_status(self) -> Dict[str, Any]:
        return {
            'detected_type': "git_repo",
            'remote_alias': self.git_remote,
            'branch': self.git_branch,
            'owner': self.git_owner,
            'repo_name': self.git_repo_name,
            'version': self.current_version,
            'remote_version': self.upstream_version,
            'current_hash': self.current_commit,
            'remote_hash': self.upstream_commit,
            'is_dirty': self.dirty,
            'detached': self.head_detached,
            'commits_behind': self.commits_behind,
            'git_messages': self.git_messages,
            'full_version_string': self.full_version_string,
            'pristine': not self.dirty,
            'corrupt': self.repo_corrupt
        }

    def get_version(self, upstream: bool = False) -> Tuple[Any, ...]:
        version = self.upstream_version if upstream else self.current_version
        return tuple(re.findall(r"\d+", version))

    def is_detached(self) -> bool:
        return self.head_detached

    def is_dirty(self) -> bool:
        return self.dirty

    def is_current(self) -> bool:
        return self.current_commit == self.upstream_commit

    def _convert_semver(self, version: str) -> List[int]:
        ver_match = self.tag_r.match(version)
        if ver_match is None:
            return []
        try:
            tag = ver_match.group(1)
            core = tag.split("-")[0]
            if core[0] == "v":
                core = core[1:]
            base_ver = [int(part) for part in core.split(".")]
            while len(base_ver) < 3:
                base_ver.append(0)
            base_ver.append({"alpha": 0, "beta": 1}.get(ver_match.group(3), 2))
            base_ver.append(int(ver_match.group(5)[1:]))
        except Exception:
            return []
        return base_ver

    async def _check_lock_file_exists(self, remove: bool = False) -> bool:
        lock_path = self.git_path.joinpath(".git/index.lock")
        if lock_path.is_file():
            if remove:
                logging.info(f"Git Repo {self.alias}: Git lock file found "
                             "after git process exited, removing")
                try:
                    event_loop = self.server.get_event_loop()
                    await event_loop.run_in_thread(os.remove, lock_path)
                except Exception:
                    pass
            return True
        return False

    async def _wait_for_lock_release(self, timeout: int = 60) -> None:
        while timeout:
            if await self._check_lock_file_exists():
                if not timeout % 10:
                    logging.info(f"Git Repo {self.alias}: Git lock file "
                                 f"exists, {timeout} seconds remaining "
                                 "before removal.")
                await asyncio.sleep(1.)
                timeout -= 1
            else:
                return
        await self._check_lock_file_exists(remove=True)

    async def _repair_loose_objects(self, notify: bool = False) -> bool:
        if notify:
            self.cmd_helper.notify_update_response(
                "Attempting to repair loose objects..."
            )
        try:
            await self.cmd_helper.run_cmd_with_response(
                "find .git/objects/ -type f -empty | xargs rm",
                timeout=10., retries=1, cwd=str(self.git_path))
            await self._run_git_cmd_async(
                "fetch --all -p", retries=1, fix_loose=False)
            await self._run_git_cmd("fsck --full", timeout=300., retries=1)
        except Exception:
            msg = (
                "Attempt to repair loose objects failed, "
                "hard recovery is required"
            )
            logging.exception(msg)
            if notify:
                self.cmd_helper.notify_update_response(msg)
            return False
        if notify:
            self.cmd_helper.notify_update_response("Loose objects repaired")
        self.repo_corrupt = False
        return True

    async def _run_git_cmd_async(self,
                                 cmd: str,
                                 retries: int = 5,
                                 need_git_path: bool = True,
                                 fix_loose: bool = True
                                 ) -> None:
        # Fetch and pull require special handling.  If the request
        # gets delayed we do not want to terminate it while the command
        # is processing.
        await self._wait_for_lock_release()
        event_loop = self.server.get_event_loop()
        env = os.environ.copy()
        env.update(GIT_ENV_VARS)
        if need_git_path:
            git_cmd = f"git -C {self.git_path} {cmd}"
        else:
            git_cmd = f"git {cmd}"
        scmd = self.cmd_helper.build_shell_command(
            git_cmd, callback=self._handle_process_output,
            env=env)
        while retries:
            self.git_messages.clear()
            self.fetch_input_recd = False
            self.fetch_timeout_handle = event_loop.delay_callback(
                GIT_ASYNC_TIMEOUT, self._check_process_active,
                scmd, cmd)
            try:
                await scmd.run(timeout=0)
            except Exception:
                pass
            self.fetch_timeout_handle.cancel()
            ret = scmd.get_return_code()
            if ret == 0:
                self.git_messages.clear()
                return
            elif self.repo_corrupt and fix_loose:
                if await self._repair_loose_objects(notify=True):
                    # Only attempt to repair loose objects once. Re-run
                    # the command once.
                    fix_loose = False
                    retries = 2
                else:
                    # since the attept to repair failed, bypass retries
                    # and immediately raise an exception
                    raise self.server.error(
                        f"Unable to repair loose objects, use hard recovery")
            retries -= 1
            await asyncio.sleep(.5)
            await self._check_lock_file_exists(remove=True)
        raise self.server.error(f"Git Command '{cmd}' failed")

    def _handle_process_output(self, output: bytes) -> None:
        self.fetch_input_recd = True
        out = output.decode().strip()
        if out:
            if out.startswith("fatal: "):
                self.repo_corrupt = True
            self.git_messages.append(out)
            self.cmd_helper.notify_update_response(out)
            logging.debug(
                f"Git Repo {self.alias}: {out}")

    async def _check_process_active(self,
                                    scmd: shell_command.ShellCommand,
                                    cmd_name: str
                                    ) -> None:
        ret = scmd.get_return_code()
        if ret is not None:
            logging.debug(f"Git Repo {self.alias}: {cmd_name} returned")
            return
        if self.fetch_input_recd:
            # Received some input, reschedule timeout
            logging.debug(
                f"Git Repo {self.alias}: {cmd_name} active, rescheduling")
            event_loop = self.server.get_event_loop()
            self.fetch_input_recd = False
            self.fetch_timeout_handle = event_loop.delay_callback(
                GIT_ASYNC_TIMEOUT, self._check_process_active,
                scmd, cmd_name)
        else:
            # Request has timed out with no input, terminate it
            logging.debug(f"Git Repo {self.alias}: {cmd_name} timed out")
            # Cancel with SIGKILL
            await scmd.cancel(2)

    async def _run_git_cmd(self,
                           git_args: str,
                           timeout: float = 20.,
                           retries: int = 5,
                           env: Optional[Dict[str, str]] = None,
                           corrupt_msg: str = "fatal: "
                           ) -> str:
        try:
            return await self.cmd_helper.run_cmd_with_response(
                f"git -C {self.git_path} {git_args}",
                timeout=timeout, retries=retries, env=env, sig_idx=2)
        except self.cmd_helper.scmd_error as e:
            stdout = e.stdout.decode().strip()
            stderr = e.stderr.decode().strip()
            msg_lines: List[str] = []
            if stdout:
                msg_lines.extend(stdout.split("\n"))
                self.git_messages.append(stdout)
            if stderr:
                msg_lines.extend(stdout.split("\n"))
                self.git_messages.append(stderr)
            for line in msg_lines:
                line = line.strip().lower()
                if line.startswith(corrupt_msg):
                    self.repo_corrupt = True
                    break
            raise
