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
from .common import Channel
from ...utils.versions import GitVersion

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
)
if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..shell_command import ShellCommand
    from .update_manager import CommandHelper
    from ..http_client import HttpClient

class GitDeploy(AppDeploy):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, "Git Repo")
        self._configure_path(config)
        self._configure_virtualenv(config)
        self._configure_dependencies(config)
        self._configure_managed_services(config)
        self.origin: str = config.get('origin')
        self.moved_origin: Optional[str] = config.get('moved_origin', None)
        self.primary_branch = config.get("primary_branch", "master")
        pinned_commit = config.get("pinned_commit", None)
        if pinned_commit is not None:
            pinned_commit = pinned_commit.lower()
            # validate the hash length
            if len(pinned_commit) < 8:
                raise config.error(
                    f"[{config.get_name()}]: Value for option 'commit' must be "
                    "a minimum of 8 characters."
                )
        self.repo = GitRepo(
            self.cmd_helper, self.path, self.name, self.origin, self.moved_origin,
            self.primary_branch, self.channel, pinned_commit
        )

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        await self.repo.restore_state(storage)
        self._is_valid = storage.get("is_valid", self.repo.is_valid())
        if not self.needs_refresh():
            self.repo.log_repo_info()
        return storage

    async def refresh(self) -> None:
        await self._update_repo_state(raise_exc=False)

    async def _update_repo_state(
        self, need_fetch: bool = True, raise_exc: bool = True
    ) -> None:
        self._is_valid = False
        try:
            await self.repo.refresh_repo_state(need_fetch=need_fetch)
        except Exception as e:
            if raise_exc or isinstance(e, asyncio.CancelledError):
                raise
        else:
            self._is_valid = self.repo.is_valid()
        finally:
            self.log_info(f"Channel: {self.channel}")
            if not self._is_valid:
                self.log_info("Repo validation check failed, updates disabled")
            else:
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
            reset_ref = await self.repo.get_recovery_ref()
            if self.repo.is_dirty():
                # Try to restore modified files.  If the attempt fails we
                # can still try the reset
                try:
                    await self.repo.checkout("-- .")
                except self.server.error:
                    pass
            await self.repo.checkout(self.primary_branch)
            await self.repo.reset(reset_ref)
            await self._update_repo_state()
        self.repo.set_rollback_state(None)

        if self.repo.is_dirty() or not self._is_valid:
            raise self.server.error(
                "Recovery attempt failed, repo state not pristine", 500)
        await self._update_dependencies(dep_info, force=force_dep_update)
        await self.restart_service()
        self.notify_status("Reinstall Complete", is_complete=True)

    async def rollback(self) -> bool:
        dep_info = await self._collect_dependency_info()
        ret = await self.repo.rollback()
        if ret:
            await self._update_dependencies(dep_info)
            await self._update_repo_state(need_fetch=False)
            await self.restart_service()
            msg = "Rollback Complete"
        else:
            msg = "Rollback not performed"
        self.notify_status(msg, is_complete=True)
        return ret

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        status.update(self.repo.get_repo_status(self.report_anomalies))
        status["name"] = self.name
        return status

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage.update(self.repo.get_persistent_data())
        return storage

    async def _pull_repo(self) -> None:
        self.notify_status("Updating Repo...")
        rb_state = self.repo.capture_state_for_rollback()
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
        else:
            self.repo.set_rollback_state(rb_state)


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
    "'%(if)%(*objecttype)%(then)%(*objecttype) %(*objectname)"
    "%(else)%(objecttype) %(objectname)%(end) %(refname)'"
)
SRC_EXTS = (".py", ".c", ".cpp")

class GitRepo:
    def __init__(
        self,
        cmd_helper: CommandHelper,
        src_path: pathlib.Path,
        alias: str,
        origin_url: str,
        moved_origin_url: Optional[str],
        primary_branch: str,
        channel: Channel,
        pinned_commit: Optional[str]
    ) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper
        self.alias = alias
        self.src_path = src_path
        git_dir = src_path.parent
        git_base = src_path.name
        self.backup_path = git_dir.joinpath(f".{git_base}_repo_backup")
        self.git_folder_path = src_path.joinpath(".git")
        self.origin_url = origin_url
        if not self.origin_url.endswith(".git"):
            self.origin_url += ".git"
        self.moved_origin_url = moved_origin_url
        self.primary_branch = primary_branch
        self.recovery_message = \
            f"""
            Manually restore via SSH with the following commands:
            sudo service {self.alias} stop
            cd {git_dir}
            rm -rf {git_base}
            git clone {self.origin_url}
            sudo service {self.alias} start
            """

        self.repo_warnings: List[str] = []
        self.repo_anomalies: List[str] = []
        self.init_evt: Optional[asyncio.Event] = None
        self.initialized: bool = False
        self.git_operation_lock = asyncio.Lock()
        self.fetch_timeout_handle: Optional[asyncio.Handle] = None
        self.fetch_input_recd: bool = False
        self.channel = channel
        self.pinned_commit = pinned_commit
        self.is_shallow = False

    async def restore_state(self, storage: Dict[str, Any]) -> None:
        self.valid_git_repo: bool = storage.get('repo_valid', False)
        self.git_owner: str = storage.get('git_owner', "?")
        self.git_repo_name: str = storage.get('git_repo_name', "?")
        self.git_remote: str = storage.get('git_remote', "?")
        self.git_branch: str = storage.get('git_branch', "?")
        if "full_version_string" in storage:
            self.current_version = GitVersion(storage["full_version_string"])
        else:
            self.current_version = GitVersion(storage.get('current_version', "?"))
        self.upstream_version = GitVersion(storage.get('upstream_version', "?"))
        self.current_commit: str = storage.get('current_commit', "?")
        self.upstream_commit: str = storage.get('upstream_commit', "?")
        self.upstream_url: str = storage.get('upstream_url', "?")
        self.recovery_url: str = storage.get(
            'recovery_url',
            self.upstream_url if self.git_remote == "origin" else "?"
        )
        self.branches: List[str] = storage.get('branches', [])
        self.head_detached: bool = storage.get('head_detached', False)
        self.git_messages: List[str] = storage.get('git_messages', [])
        self.commits_behind: List[Dict[str, Any]] = storage.get('commits_behind', [])
        self.commits_behind_count: int = storage.get('cbh_count', 0)
        self.diverged: bool = storage.get("diverged", False)
        self.repo_corrupt: bool = storage.get('corrupt', False)
        self.modified_files: List[str] = storage.get("modified_files", [])
        self.untracked_files: List[str] = storage.get("untracked_files", [])
        def_rbs = self.capture_state_for_rollback()
        self.rollback_commit: str = storage.get('rollback_commit', self.current_commit)
        self.rollback_branch: str = storage.get('rollback_branch', def_rbs["branch"])
        rbv = storage.get('rollback_version', self.current_version)
        self.rollback_version = GitVersion(str(rbv))
        self.pinned_commit_valid: bool = storage.get('pinned_commit_valid', True)
        if not await self._detect_git_dir():
            self.valid_git_repo = False
        self._check_warnings()

    def get_persistent_data(self) -> Dict[str, Any]:
        return {
            'repo_valid': self.valid_git_repo,
            'git_owner': self.git_owner,
            'git_repo_name': self.git_repo_name,
            'git_remote': self.git_remote,
            'git_branch': self.git_branch,
            'current_version': self.current_version.full_version,
            'upstream_version': self.upstream_version.full_version,
            'current_commit': self.current_commit,
            'upstream_commit': self.upstream_commit,
            'rollback_commit': self.rollback_commit,
            'rollback_branch': self.rollback_branch,
            'rollback_version': self.rollback_version.full_version,
            'upstream_url': self.upstream_url,
            'recovery_url': self.recovery_url,
            'branches': self.branches,
            'head_detached': self.head_detached,
            'git_messages': self.git_messages,
            'commits_behind': self.commits_behind,
            'cbh_count': self.commits_behind_count,
            'diverged': self.diverged,
            'corrupt': self.repo_corrupt,
            'modified_files': self.modified_files,
            'untracked_files': self.untracked_files,
            'pinned_commit_valid': self.pinned_commit_valid
        }

    async def refresh_repo_state(self, need_fetch: bool = True) -> None:
        if self.init_evt is not None:
            # No need to initialize multiple requests
            await self.init_evt.wait()
            if self.initialized:
                return
        self.initialized = False
        self.pinned_commit_valid = True
        self.init_evt = asyncio.Event()
        self.git_messages.clear()
        try:
            await self._check_repo_status()
            self._verify_repo()
            await self._find_current_branch()

            # Fetch the upstream url.  If the repo has been moved,
            # set the new url
            self.upstream_url = await self.remote(f"get-url {self.git_remote}", True)
            if await self._check_moved_origin():
                need_fetch = True
            if self.git_remote == "origin":
                self.recovery_url = self.upstream_url
            else:
                remote_list = (await self.remote()).splitlines()
                logging.debug(
                    f"Git Repo {self.alias}: Detected Remotes - {remote_list}"
                )
                if "origin" in remote_list:
                    self.recovery_url = await self.remote("get-url origin")
                else:
                    logging.info(
                        f"Git Repo {self.alias}: Unable to detect recovery URL, "
                        "Hard Recovery not available"
                    )
                    self.recovery_url = "?"
            if need_fetch:
                await self.fetch()
            self.diverged = await self.check_diverged()

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
            git_desc = await self.describe("--always --tags --long --dirty --abbrev=8")
            cur_ver = GitVersion(git_desc.strip())
            upstream_ver = await self._get_upstream_version()
            await self._set_versions(cur_ver, upstream_ver)

            # Get Commits Behind
            self.commits_behind = []
            if self.commits_behind_count > 0:
                cbh = await self.get_commits_behind()
                tagged_commits = await self.get_tagged_commits()
                debug_msg = '\n'.join([f"{k}: {v}" for k, v in tagged_commits.items()])
                logging.debug(f"Git Repo {self.alias}: Tagged Commits\n{debug_msg}")
                for i, commit in enumerate(cbh):
                    tag = tagged_commits.get(commit['sha'], None)
                    if i < 30 or tag is not None:
                        commit['tag'] = tag
                        self.commits_behind.append(commit)
            self._check_warnings()
        except Exception:
            logging.exception(f"Git Repo {self.alias}: Initialization failure")
            self._check_warnings()
            raise
        else:
            self.initialized = True
            # If no exception was raised assume the repo is not corrupt
            self.repo_corrupt = False
            if self.rollback_commit == "?" or self.rollback_branch == "?":
                # Reset Rollback State
                self.set_rollback_state(None)
            self.log_repo_info()
        finally:
            self.init_evt.set()
            self.init_evt = None

    async def _check_repo_status(self) -> bool:
        async with self.git_operation_lock:
            self.valid_git_repo = False
            if not await self._detect_git_dir():
                logging.info(
                    f"Git Repo {self.alias}: path '{self.src_path}'"
                    " is not a valid git repo")
                return False
            await self._wait_for_lock_release()
            attempts = 3
            resp: Optional[str] = None
            while attempts:
                self.git_messages.clear()
                try:
                    cmd = "status --porcelain -b"
                    resp = await self._run_git_cmd(
                        cmd, attempts=1, corrupt_hdr="fatal:"
                    )
                except Exception:
                    attempts -= 1
                    resp = None
                    # Attempt to recover from "loose object" error
                    if attempts and self.repo_corrupt:
                        if not await self._repair_loose_objects():
                            # Since we are unable to recover, immediately
                            # return
                            return False
                else:
                    break
            if resp is None:
                return False
            self.modified_files.clear()
            self.untracked_files.clear()
            for line in resp.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) != 2:
                    continue
                prefix, fname = [p.strip() for p in parts]
                if prefix == "M":
                    # modified file
                    self.modified_files.append(fname)
                elif prefix == "??":
                    # untracked file
                    ext = pathlib.Path(fname).suffix
                    if ext in SRC_EXTS:
                        self.untracked_files.append(fname)
            self.valid_git_repo = True
        return True

    async def _detect_git_dir(self) -> bool:
        if self.git_folder_path.is_file():
            # Submodules have a file that contain the path to
            # the .git folder
            eventloop = self.server.get_event_loop()
            data = await eventloop.run_in_thread(self.git_folder_path.read_text)
            ident, _, gitdir = data.partition(":")
            gitdir = gitdir.strip()
            if ident.strip() != "gitdir" or not gitdir:
                logging.warning(f"not a .git file: '{ident}' '{gitdir}' in '{data}'")
                return False
            gitdir_path = pathlib.Path(gitdir).expanduser()
            resolved_path = (self.git_folder_path.parent / gitdir_path).resolve()
            logging.info(
                f"detecting git folder path '{self.git_folder_path}'"
                f" leads to '{gitdir}' resolves to '{resolved_path}'")
            self.git_folder_path = resolved_path
        if self.git_folder_path.is_dir():
            self.is_shallow = self.git_folder_path.joinpath("shallow").is_file()
            return True
        return False

    async def _find_current_branch(self) -> None:
        # Populate list of current branches
        blist = await self.list_branches()
        current_branch = ""
        self.branches = []
        for branch in blist:
            branch = branch.strip()
            if not branch:
                continue
            if branch[0] == "*":
                branch = branch[2:].strip()
                current_branch = branch
            if branch[0] == "(":
                continue
            self.branches.append(branch)
        if current_branch.startswith("(HEAD detached"):
            self.head_detached = True
            ref_name = current_branch.split()[-1][:-1]
            remote_list = (await self.remote()).splitlines()
            for remote in remote_list:
                remote = remote.strip()
                if not remote:
                    continue
                if ref_name.startswith(remote):
                    self.git_branch = ref_name[len(remote)+1:]
                    self.git_remote = remote
                    break
            else:
                if self.git_remote == "?":
                    msg = "Resolve by manually checking out a branch via SSH."
                else:
                    prev = f"{self.git_remote}/{self.git_branch}"
                    msg = f"Defaulting to previously tracked {prev}."
                logging.info(f"Git Repo {self.alias}: {current_branch} {msg}")
        else:
            self.head_detached = False
            self.git_branch = current_branch
            rkey = f"branch.{self.git_branch}.remote"
            self.git_remote = (await self.config_get(rkey)) or "?"

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
                    f"set-url {self.git_remote} {self.origin_url}", True
                )
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

    async def _get_upstream_version(self) -> GitVersion:
        self.commits_behind_count = 0
        if self.pinned_commit is not None:
            self.upstream_commit = self.current_commit
            if not self.current_commit.lower().startswith(self.pinned_commit):
                if not await self.check_commit_exists(self.pinned_commit):
                    self.pinned_commit_valid = False
                elif await self.is_ancestor(self.current_commit, self.pinned_commit):
                    self.upstream_commit = self.pinned_commit
            upstream_ver_str = await self.describe(
                f"{self.upstream_commit} --always --tags --long --abbrev=8",
            )
        elif self.channel == Channel.DEV:
            self.upstream_commit = await self.rev_parse(
                f"{self.git_remote}/{self.git_branch}"
            )
            upstream_ver_str = await self.describe(
                f"{self.git_remote}/{self.git_branch} --always --tags --long --abbrev=8"
            )
        else:
            tagged_commits = await self.get_tagged_commits()
            upstream_commit = upstream_ver_str = "?"
            for sha, tag in tagged_commits.items():
                ver = GitVersion(tag)
                if not ver.is_valid_version():
                    continue
                if (
                    (self.channel == Channel.STABLE and ver.is_final_release()) or
                    (self.channel == Channel.BETA and not ver.is_alpha_release())
                ):
                    upstream_commit = sha
                    upstream_ver_str = tag
                    break
            self.upstream_commit = upstream_commit
        if self.upstream_commit != "?":
            rl_args = f"HEAD..{self.upstream_commit} --count"
            self.commits_behind_count = int(await self.rev_list(rl_args))
        return GitVersion(upstream_ver_str)

    async def _set_versions(
        self, current_version: GitVersion, upstream_version: GitVersion
    ) -> None:
        if not current_version.is_valid_version():
            log_msg = (
                f"Git repo {self.alias}: Failed to detect current version, got "
                f"'{current_version}'. "
            )
            tag = upstream_version.infer_last_tag()
            count = await self.rev_list("HEAD --count")
            sha_part = ""
            if current_version.is_fallback():
                sha_part = f"-g{current_version}"
            elif self.current_commit not in ("?", ""):
                sha_part = f"-g{self.current_commit[:8]}"
            current_version = GitVersion(f"{tag}-{count}{sha_part}-inferred")
            log_msg += f"Falling back to inferred version: {current_version}"
            logging.info(log_msg)
        if self.channel == Channel.DEV:
            if not upstream_version.is_valid_version():
                log_msg = (
                    f"Git repo {self.alias}: Failed to detect upstream version, got "
                    f"'{upstream_version}'. "
                )
                tag = current_version.tag
                if current_version.inferred:
                    count = await self.rev_list(f"{self.upstream_commit} --count")
                else:
                    log_msg += "\nRemote has diverged, approximating dev count. "
                    count = str(self.commits_behind_count + current_version.dev_count)
                upstream_version = GitVersion(f"{tag}-{count}-inferred")
                log_msg += f"Falling back to inferred version: {upstream_version}"
                logging.info(log_msg)
        else:
            if not upstream_version.is_valid_version():
                self.upstream_commit = self.current_commit
                upstream_version = current_version
            elif upstream_version <= current_version:
                self.upstream_commit = self.current_commit
        self.current_version = current_version
        self.upstream_version = upstream_version

    async def wait_for_init(self) -> None:
        if self.init_evt is not None:
            await self.init_evt.wait()
            if not self.initialized:
                raise self.server.error(
                    f"Git Repo {self.alias}: Initialization failure")

    async def is_ancestor(
        self, ancestor_ref: str, descendent_ref: str, attempts: int = 3
    ) -> bool:
        self._verify_repo()
        cmd = f"merge-base --is-ancestor {ancestor_ref} {descendent_ref}"
        async with self.git_operation_lock:
            for _ in range(attempts):
                try:
                    await self._run_git_cmd(cmd, attempts=1, corrupt_hdr="error: ")
                except self.cmd_helper.get_shell_command().error as err:
                    if err.return_code == 1:
                        return False
                    if self.repo_corrupt:
                        raise
                else:
                    break
                await asyncio.sleep(.2)
            return True

    async def check_diverged(self) -> bool:
        self._verify_repo(check_remote=True)
        if self.head_detached:
            return False
        descendent = f"{self.git_remote}/{self.git_branch}"
        return not (await self.is_ancestor("HEAD", descendent))

    def log_repo_info(self) -> None:
        warnings = self._generate_warn_msg()
        if warnings:
            warnings = "\nRepo Warnings:\n" + warnings
        logging.info(
            f"Git Repo {self.alias} Detected:\n"
            f"Owner: {self.git_owner}\n"
            f"Repository Name: {self.git_repo_name}\n"
            f"Path: {self.src_path}\n"
            f"Remote: {self.git_remote}\n"
            f"Branch: {self.git_branch}\n"
            f"Remote URL: {self.upstream_url}\n"
            f"Recovery URL: {self.recovery_url}\n"
            f"Current Commit SHA: {self.current_commit}\n"
            f"Upstream Commit SHA: {self.upstream_commit}\n"
            f"Current Version: {self.current_version}\n"
            f"Upstream Version: {self.upstream_version}\n"
            f"Rollback Commit: {self.rollback_commit}\n"
            f"Rollback Branch: {self.rollback_branch}\n"
            f"Rollback Version: {self.rollback_version}\n"
            f"Is Dirty: {self.current_version.dirty}\n"
            f"Is Detached: {self.head_detached}\n"
            f"Is Shallow: {self.is_shallow}\n"
            f"Commits Behind Count: {self.commits_behind_count}\n"
            f"Diverged: {self.diverged}\n"
            f"Pinned Commit: {self.pinned_commit}"
            f"{warnings}"
        )

    def _check_warnings(self) -> None:
        self.repo_warnings.clear()
        self.repo_anomalies.clear()
        if self.pinned_commit is not None and not self.pinned_commit_valid:
            self.repo_anomalies.append(
                f"Pinned Commit {self.pinned_commit} does not exist"
            )
        if self.repo_corrupt:
            self.repo_warnings.append("Repo is corrupt")
        if self.git_branch == "?":
            self.repo_warnings.append("Failed to detect git branch")
        elif self.git_remote == "?":
            self.repo_warnings.append(
                f"Failed to detect tracking remote for branch {self.git_branch}"
            )
        if self.upstream_url == "?":
            self.repo_warnings.append("Failed to detect repo url")
            return
        upstream_url = self.upstream_url.lower()
        if upstream_url[-4:] != ".git":
            upstream_url += ".git"
        if upstream_url != self.origin_url.lower():
            self.repo_anomalies.append(f"Unofficial remote url: {self.upstream_url}")
        if self.git_branch != self.primary_branch or self.git_remote != "origin":
            self.repo_anomalies.append(
                "Repo not on official remote/branch, expected: "
                f"origin/{self.primary_branch}, detected: "
                f"{self.git_remote}/{self.git_branch}")
        if self.untracked_files:
            self.repo_anomalies.append(
                f"Repo has untracked source files: {self.untracked_files}"
            )
        if self.diverged:
            self.repo_anomalies.append("Repo has diverged from remote")
        if self.head_detached:
            msg = "Detached HEAD detected"
            if self.server.is_debug_enabled():
                self.repo_anomalies.append(msg)
            else:
                self.repo_warnings.append(msg)
        if self.is_dirty():
            self.repo_warnings.append(
                "Repo is dirty.  Detected the following modified files: "
                f"{self.modified_files}"
            )
        self._generate_warn_msg()

    def _generate_warn_msg(self) -> str:
        ro_msg = f"Git Repo {self.alias}: No warnings detected"
        warn_msg = ""
        if self.repo_warnings or self.repo_anomalies:
            ro_msg = f"Git Repo {self.alias}: Warnings detected:\n"
            warn_msg = "\n".join(
                [f"  {warn}" for warn in self.repo_warnings + self.repo_anomalies]
            )
            ro_msg += warn_msg
        self.server.add_log_rollover_item(f"umgr_{self.alias}_warn", ro_msg, log=False)
        return warn_msg

    def _verify_repo(self, check_remote: bool = False) -> None:
        if not self.valid_git_repo:
            raise self.server.error(
                f"Git Repo {self.alias}: repo not initialized")
        if check_remote:
            if self.git_remote == "?":
                raise self.server.error(
                    f"Git Repo {self.alias}: No valid git remote detected")

    async def reset(self, ref: Optional[str] = None) -> None:
        async with self.git_operation_lock:
            if ref is None:
                if self.channel != Channel.DEV or self.pinned_commit is not None:
                    ref = self.upstream_commit
                else:
                    if self.git_remote == "?" or self.git_branch == "?":
                        raise self.server.error("Cannot reset, unknown remote/branch")
                    ref = f"{self.git_remote}/{self.git_branch}"
            await self._run_git_cmd(f"reset --hard {ref}", attempts=2)
            self.repo_corrupt = False

    async def fetch(self) -> None:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            await self._run_git_cmd_async(
                f"fetch {self.git_remote} --prune --progress")

    async def clean(self) -> None:
        self._verify_repo()
        async with self.git_operation_lock:
            await self._run_git_cmd("clean -d -f", attempts=2)

    async def pull(self) -> None:
        self._verify_repo()
        if self.head_detached:
            raise self.server.error(
                f"Git Repo {self.alias}: Cannot perform pull on a "
                "detached HEAD")
        cmd = "pull --progress"
        if self.server.is_debug_enabled():
            cmd = f"{cmd} --rebase"
        if self.channel != Channel.DEV or self.pinned_commit is not None:
            cmd = f"{cmd} {self.git_remote} {self.upstream_commit}"
        async with self.git_operation_lock:
            await self._run_git_cmd_async(cmd)

    async def list_branches(self) -> List[str]:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd("branch --list --no-color")
            return resp.strip().split("\n")

    async def check_commit_exists(self, commit: str) -> bool:
        self._verify_repo()
        async with self.git_operation_lock:
            shell_cmd = self.cmd_helper.get_shell_command()
            try:
                await self._run_git_cmd(
                    f"cat-file -e {commit}^{{commit}}", attempts=1
                )
            except shell_cmd.error:
                return False
            return True

    async def remote(self, command: str = "", validate: bool = False) -> str:
        self._verify_repo(check_remote=validate)
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

    async def config_get(
        self,
        key: str,
        pattern: str = "",
        get_all: bool = False,
        local_only: bool = False
    ) -> Optional[str]:
        local = "--local " if local_only else ""
        cmd = f"{local}--get-all" if get_all else f"{local}--get"
        args = f"{cmd} {key} '{pattern}'" if pattern else f"{cmd} {key}"
        try:
            return await self.config_cmd(args)
        except self.cmd_helper.get_shell_command().error as e:
            if e.return_code == 1:
                return None
            raise

    async def config_set(self, key: str, value: str) -> None:
        await self.config_cmd(f"{key} '{value}'")

    async def config_add(self, key: str, value: str) -> None:
        await self.config_cmd(f"--add {key} '{value}'")

    async def config_unset(
        self, key: str, pattern: str = "", unset_all: bool = False
    ) -> None:
        cmd = "--unset-all" if unset_all else "--unset"
        args = f"{cmd} {key} '{pattern}'" if pattern else f"{cmd} {key}"
        await self.config_cmd(args)

    async def config_cmd(self, args: str) -> str:
        self._verify_repo()
        verbose = self.server.is_verbose_enabled()
        async with self.git_operation_lock:
            for attempt in range(3):
                try:
                    return await self._run_git_cmd(
                        f"config {args}", attempts=1, log_complete=verbose
                    )
                except self.cmd_helper.get_shell_command().error as e:
                    if 1 <= (e.return_code or 10) <= 6 or attempt == 2:
                        raise
            raise self.server.error("Failed to run git-config")


    async def checkout(self, branch: Optional[str] = None) -> None:
        self._verify_repo()
        reset_commit: Optional[str] = None
        async with self.git_operation_lock:
            if branch is None:
                # No branch is specified so we are checking out detached
                if self.channel != Channel.DEV or self.pinned_commit is not None:
                    reset_commit = self.upstream_commit
                branch = f"{self.git_remote}/{self.git_branch}"
            await self._run_git_cmd(f"checkout -q {branch}")
        if reset_commit is not None:
            await self.reset(reset_commit)

    async def run_fsck(self) -> None:
        async with self.git_operation_lock:
            await self._run_git_cmd("fsck --full", timeout=300., attempts=1)

    async def clone(self) -> None:
        if self.is_submodule_or_worktree():
            raise self.server.error(
                f"Cannot clone git repo {self.alias}, it is a {self.get_repo_type()} "
                "of another git repo."
            )
        async with self.git_operation_lock:
            if self.recovery_url == "?":
                raise self.server.error(
                    "Recovery url has not been detected, clone aborted"
                )
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Starting Clone Recovery...")
            event_loop = self.server.get_event_loop()
            if self.backup_path.exists():
                await event_loop.run_in_thread(shutil.rmtree, self.backup_path)
            await self._check_lock_file_exists(remove=True)
            cmd = (
                f"clone --branch {self.primary_branch} --filter=blob:none "
                f"{self.recovery_url} {self.backup_path}"
            )
            try:
                await self._run_git_cmd_async(cmd, 1, False, False)
            except Exception as e:
                self.cmd_helper.notify_update_response(
                    f"Git Repo {self.alias}: Git Clone Failed")
                raise self.server.error("Git Clone Error") from e
            if self.src_path.exists():
                await event_loop.run_in_thread(shutil.rmtree, self.src_path)
            await event_loop.run_in_thread(
                shutil.move, str(self.backup_path), str(self.src_path))
            self.repo_corrupt = False
            self.valid_git_repo = True
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Git Clone Complete")
        reset_commit = await self.get_recovery_ref("HEAD")
        if reset_commit != "HEAD":
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Moving HEAD to previous "
                f"commit {self.current_commit}"
            )
            await self.reset(reset_commit)

    async def rollback(self) -> bool:
        if self.rollback_commit == "?" or self.rollback_branch == "?":
            raise self.server.error("Incomplete rollback data stored, cannot rollback")
        if self.rollback_branch != self.git_branch:
            await self.checkout(self.rollback_branch)
        elif self.rollback_commit == self.current_commit:
            return False
        await self.reset(self.rollback_commit)
        return True

    def capture_state_for_rollback(self) -> Dict[str, Any]:
        branch = self.git_branch
        if self.head_detached:
            valid = "?" not in (self.git_remote, self.git_branch)
            branch = f"{self.git_remote}/{self.git_branch}" if valid else "?"
        return {
            "commit": self.current_commit,
            "branch": branch,
            "version": self.current_version
        }

    def set_rollback_state(self, rb_state: Optional[Dict[str, Any]]) -> None:
        if rb_state is None:
            rb_state = self.capture_state_for_rollback()
        self.rollback_commit = rb_state["commit"]
        self.rollback_branch = rb_state["branch"]
        self.rollback_version = rb_state["version"]

    async def get_commits_behind(self) -> List[Dict[str, Any]]:
        self._verify_repo()
        if self.is_current():
            return []
        async with self.git_operation_lock:
            if self.channel != Channel.DEV or self.pinned_commit is not None:
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

    async def get_tagged_commits(self, count: int = 100) -> Dict[str, str]:
        self._verify_repo(check_remote=True)
        tip = f"{self.git_remote}/{self.git_branch}"
        cnt_arg = f"--count={count} " if count > 0 else ""
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(
                f"for-each-ref {cnt_arg}--sort='-creatordate' --contains=HEAD "
                f"--merged={tip} --format={GIT_REF_FMT} 'refs/tags'"
            )
            tagged_commits: Dict[str, str] = {}
            for line in resp.split('\n'):
                parts = line.strip().split()
                if len(parts) != 3 or parts[0] != "commit":
                    continue
                sha, ref = parts[1:]
                tag = ref.split('/')[-1]
                tagged_commits[sha] = tag
            # Return tagged commits as SHA keys mapped to tag values
            return tagged_commits

    def get_repo_status(self, rpt_anomalies: bool) -> Dict[str, Any]:
        no_untrk_src = len(self.untracked_files) == 0
        anomalies = self.repo_anomalies if rpt_anomalies else []
        return {
            'detected_type': "git_repo",
            'remote_alias': self.git_remote,
            'branch': self.git_branch,
            'owner': self.git_owner,
            'repo_name': self.git_repo_name,
            'remote_url': self.upstream_url,
            'recovery_url': self.recovery_url,
            'version': self.current_version.short_version,
            'remote_version': self.upstream_version.short_version,
            'rollback_version': self.rollback_version.short_version,
            'current_hash': self.current_commit,
            'remote_hash': self.upstream_commit,
            'is_dirty': self.current_version.dirty,
            'detached': self.head_detached,
            'commits_behind': self.commits_behind,
            'commits_behind_count': self.commits_behind_count,
            'git_messages': self.git_messages,
            'full_version_string': self.current_version.full_version,
            'pristine': no_untrk_src and not self.current_version.dirty,
            'corrupt': self.repo_corrupt,
            'warnings': self.repo_warnings,
            'anomalies': anomalies
        }

    def get_version(self, upstream: bool = False) -> GitVersion:
        return self.upstream_version if upstream else self.current_version

    def is_detached(self) -> bool:
        return self.head_detached

    def is_dirty(self) -> bool:
        return self.current_version.dirty

    def is_current(self) -> bool:
        return self.current_commit == self.upstream_commit

    def is_submodule_or_worktree(self):
        return (
            self.src_path.joinpath(".git").is_file() and
            self.git_folder_path.parent.name in ("modules", "worktrees")
        )

    def is_valid(self) -> bool:
        return (
            not self.is_damaged() and
            not self.has_recoverable_errors()
        )

    def is_damaged(self) -> bool:
        # A damaged repo requires a clone to recover
        return not self.valid_git_repo or self.repo_corrupt

    def has_recoverable_errors(self) -> bool:
        # These errors should be recoverable using a git reset
        detached_err = False if self.server.is_debug_enabled() else self.head_detached
        return (
            self.diverged or
            self.is_dirty() or
            detached_err
        )

    def get_repo_type(self) -> str:
        type_name = self.git_folder_path.parent.name
        if type_name == "modules":
            return "submodule"
        elif type_name == "worktrees":
            return "worktree"
        return "repo"

    async def get_recovery_ref(self, upstream_ref: Optional[str] = None) -> str:
        """ Fetch the best reference for a 'reset' recovery attempt

        Returns the ref to reset to for "soft" recovery requests.  The
        preference is to reset to the current commit, however that is
        only possible if the commit is known and if it is an ancestor of
        the primary branch.
        """
        if upstream_ref is None:
            remote = await self.config_get(f"branch.{self.primary_branch}.remote")
            if remote is None:
                raise self.server.error(
                    f"Failed to find remote for primary branch '{self.primary_branch}'"
                )
            upstream_ref = f"{remote}/{self.primary_branch}"
        reset_commits: List[str] = []
        if self.pinned_commit is not None:
            reset_commits.append(self.pinned_commit)
        if self.current_commit != "?":
            reset_commits.append(self.current_commit)
        for commit in reset_commits:
            try:
                is_ancs = await self.is_ancestor(commit, upstream_ref, attempts=1)
            except self.server.error:
                is_ancs = False
            if is_ancs:
                return commit
        return upstream_ref

    async def _check_lock_file_exists(self, remove: bool = False) -> bool:
        lock_path = self.git_folder_path.joinpath("index.lock")
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
            shell_cmd = self.cmd_helper.get_shell_command()
            await shell_cmd.exec_cmd(
                "find .git/objects/ -type f -empty | xargs rm",
                timeout=10., attempts=1, cwd=str(self.src_path))
            await self._run_git_cmd_async(
                "fetch --all -p", attempts=1, fix_loose=False)
            await self._run_git_cmd("fsck --full", timeout=300., attempts=1)
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
                                 attempts: int = 5,
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
            git_cmd = f"git -C {self.src_path} {cmd}"
        else:
            git_cmd = f"git {cmd}"
        shell_cmd = self.cmd_helper.get_shell_command()
        scmd = shell_cmd.build_shell_command(
            git_cmd, callback=self._handle_process_output,
            env=env)
        while attempts:
            self.git_messages.clear()
            self.fetch_input_recd = False
            self.fetch_timeout_handle = event_loop.delay_callback(
                GIT_ASYNC_TIMEOUT, self._check_process_active,
                scmd, cmd)
            try:
                await scmd.run(timeout=0)
            except Exception:
                pass
            if self.fetch_timeout_handle is not None:
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
                    attempts = 2
                else:
                    # since the attempt to repair failed, bypass attempts
                    # and immediately raise an exception
                    raise self.server.error(
                        "Unable to repair loose objects, use hard recovery"
                    )
            attempts -= 1
            await asyncio.sleep(.5)
            await self._check_lock_file_exists(remove=True)
        raise self.server.error(f"Git Command '{cmd}' failed")

    def _handle_process_output(self, output: bytes) -> None:
        self.fetch_input_recd = True
        out = output.decode().strip()
        if out:
            if out.startswith("fatal: ") and "corrupt" in out:
                self.repo_corrupt = True
            self.git_messages.append(out)
            self.cmd_helper.notify_update_response(out)
            logging.debug(
                f"Git Repo {self.alias}: {out}")

    async def _check_process_active(
        self, scmd: ShellCommand, cmd_name: str
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

    async def _run_git_cmd(
        self,
        git_args: str,
        timeout: float = 20.,
        attempts: int = 5,
        env: Optional[Dict[str, str]] = None,
        corrupt_hdr: Optional[str] = None,
        log_complete: bool = True
    ) -> str:
        shell_cmd = self.cmd_helper.get_shell_command()
        try:
            return await shell_cmd.exec_cmd(
                f"git -C {self.src_path} {git_args}",
                timeout=timeout,
                attempts=attempts,
                env=env,
                sig_idx=2,
                log_complete=log_complete
            )
        except shell_cmd.error as e:
            stdout = e.stdout.decode().strip()
            stderr = e.stderr.decode().strip()
            msg_lines: List[str] = []
            if stdout:
                msg_lines.extend(stdout.split("\n"))
                self.git_messages.append(stdout)
            if stderr:
                msg_lines.extend(stdout.split("\n"))
                self.git_messages.append(stderr)
            if corrupt_hdr is not None:
                for line in msg_lines:
                    line = line.strip().lower()
                    if line.startswith(corrupt_hdr) and "corrupt" in line:
                        self.repo_corrupt = True
                        break
            raise
