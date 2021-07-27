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
    from confighelper import ConfigHelper
    from components import database
    from components import shell_command
    from .update_manager import CommandHelper
    DBComp = database.MoonrakerDatabase


class GitDeploy(AppDeploy):
    def __init__(self,
                 config: ConfigHelper,
                 cmd_helper: CommandHelper,
                 app_params: Optional[Dict[str, Any]] = None
                 ) -> None:
        super().__init__(config, cmd_helper, app_params)
        self.repo = GitRepo(cmd_helper, self.path, self.name, self.origin)
        if self.type != 'git_repo':
            self.need_channel_update = True

    @staticmethod
    async def from_application(app: AppDeploy) -> GitDeploy:
        new_app = GitDeploy(app.config, app.cmd_helper, app.app_params)
        await new_app.reinstall()
        return new_app

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
            if self.debug:
                self._is_valid = True
                self.log_info(
                    "Repo debug enabled, overriding validity checks")
            else:
                self.log_info("Updates on repo disabled")
        else:
            self._is_valid = True
            self.log_info("Validity check for git repo passed")

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
        inst_hash = await self._get_file_hash(self.install_script)
        pyreqs_hash = await self._get_file_hash(self.python_reqs)
        npm_hash = await self._get_file_hash(self.npm_pkg_json)
        await self._pull_repo()
        # Check Semantic Versions
        await self._update_dependencies(inst_hash, pyreqs_hash, npm_hash)
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
        inst_hash = await self._get_file_hash(self.install_script)
        pyreqs_hash = await self._get_file_hash(self.python_reqs)
        npm_hash = await self._get_file_hash(self.npm_pkg_json)

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
        await self._update_dependencies(inst_hash, pyreqs_hash, npm_hash,
                                        force=force_dep_update)
        await self.restart_service()
        self.notify_status("Reinstall Complete", is_complete=True)

    async def reinstall(self):
        await self.recover(True, True)

    def get_update_status(self) -> Dict[str, Any]:
        status = super().get_update_status()
        status.update(self.repo.get_repo_status())
        return status

    async def _pull_repo(self) -> None:
        self.notify_status("Updating Repo...")
        try:
            if self.repo.is_detached():
                await self.repo.fetch()
                await self.repo.checkout()
            else:
                await self.repo.pull()
        except Exception:
            raise self.log_exc("Error running 'git pull'")

    async def _update_dependencies(self,
                                   inst_hash: Optional[str],
                                   pyreqs_hash: Optional[str],
                                   npm_hash: Optional[str],
                                   force: bool = False
                                   ) -> None:
        ret = await self._check_need_update(inst_hash, self.install_script)
        if force or ret:
            package_list = await self._parse_install_script()
            if package_list is not None:
                await self._install_packages(package_list)
        ret = await self._check_need_update(pyreqs_hash, self.python_reqs)
        if force or ret:
            if self.python_reqs is not None:
                await self._update_virtualenv(self.python_reqs)
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

    async def _parse_install_script(self) -> Optional[List[str]]:
        if self.install_script is None:
            return None
        # Open install file file and read
        inst_path: pathlib.Path = self.install_script
        if not inst_path.is_file():
            self.log_info(f"Unable to open install script: {inst_path}")
            return None
        event_loop = self.server.get_event_loop()
        data = await event_loop.run_in_thread(inst_path.read_text)
        packages: List[str] = re.findall(r'PKGLIST="(.*)"', data)
        packages = [p.lstrip("${PKGLIST}").strip() for p in packages]
        if not packages:
            self.log_info(f"No packages found in script: {inst_path}")
            return None
        logging.debug(f"Repo {self.name}: Detected Packages: {repr(packages)}")
        return packages


GIT_ASYNC_TIMEOUT = 300.
GIT_ENV_VARS = {
    'GIT_HTTP_LOW_SPEED_LIMIT': "1000",
    'GIT_HTTP_LOW_SPEED_TIME ': "20"
}
GIT_MAX_LOG_CNT = 100
GIT_LOG_FMT = \
    "\"sha:%H%x1Dauthor:%an%x1Ddate:%ct%x1Dsubject:%s%x1Dmessage:%b%x1E\""
GIT_OBJ_ERR = "fatal: loose object"

class GitRepo:
    def __init__(self,
                 cmd_helper: CommandHelper,
                 git_path: pathlib.Path,
                 alias: str,
                 origin_url: str
                 ) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper
        self.alias = alias
        self.git_path = git_path
        git_dir = git_path.parent
        git_base = git_path.name
        self.backup_path = git_dir.joinpath(f".{git_base}_repo_backup")
        self.origin_url = origin_url
        self.valid_git_repo: bool = False
        self.git_owner: str = "?"
        self.git_remote: str = "?"
        self.git_branch: str = "?"
        self.current_version: str = "?"
        self.upstream_version: str = "?"
        self.current_commit: str = "?"
        self.upstream_commit: str = "?"
        self.upstream_url: str = "?"
        self.full_version_string: str = "?"
        self.branches: List[str] = []
        self.dirty: bool = False
        self.head_detached: bool = False
        self.git_messages: List[str] = []
        self.commits_behind: List[Dict[str, Any]] = []
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

            if need_fetch:
                await self.fetch()

            self.upstream_url = await self.remote(f"get-url {self.git_remote}")
            self.current_commit = await self.rev_parse("HEAD")
            self.upstream_commit = await self.rev_parse(
                f"{self.git_remote}/{self.git_branch}")
            current_version = await self.describe(
                "--always --tags --long --dirty")
            self.full_version_string = current_version.strip()
            upstream_version = await self.describe(
                f"{self.git_remote}/{self.git_branch} "
                "--always --tags --long")

            # Store current remote in the database if in a detached state
            if self.head_detached:
                mrdb: DBComp = self.server.lookup_component("database")
                db_key = f"update_manager.git_repo_{self.alias}" \
                    ".detached_remote"
                mrdb.insert_item(
                    "moonraker", db_key,
                    [self.current_commit, self.git_remote, self.git_branch])

            # Parse GitHub Owner from URL
            owner_match = re.match(r"https?://[^/]+/([^/]+)", self.upstream_url)
            self.git_owner = "?"
            if owner_match is not None:
                self.git_owner = owner_match.group(1)
            self.dirty = current_version.endswith("dirty")

            # Parse Version Info
            versions = []
            for ver in [current_version, upstream_version]:
                tag_version = "?"
                ver_match = re.match(r"v\d+\.\d+\.\d-\d+", ver)
                if ver_match:
                    tag_version = ver_match.group()
                versions.append(tag_version)
            self.current_version, self.upstream_version = versions

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
        finally:
            self.init_evt.set()
            self.init_evt = None

    async def wait_for_init(self) -> None:
        if self.init_evt is not None:
            await self.init_evt.wait()
            if not self.initialized:
                raise self.server.error(
                    f"Git Repo {self.alias}: Initialization failure")

    async def update_repo_status(self) -> bool:
        async with self.git_operation_lock:
            if not self.git_path.joinpath(".git").is_dir():
                logging.info(
                    f"Git Repo {self.alias}: path '{self.git_path}'"
                    " is not a valid git repo")
                return False
            await self._wait_for_lock_release()
            self.valid_git_repo = False
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
                    if retries and GIT_OBJ_ERR in "\n".join(self.git_messages):
                        ret = await self._repair_loose_objects()
                        if not ret:
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
                    mrdb: DBComp = self.server.lookup_component("database")
                    db_key = f"update_manager.git_repo_{self.alias}" \
                        ".detached_remote"
                    detached_remote: List[str] = mrdb.get_item(
                        "moonraker", db_key, ["", "?", "?"])
                    if detached_remote[0].startswith(branch_info):
                        self.git_remote = detached_remote[1]
                        self.git_branch = detached_remote[2]
                        msg = "Using remote stored in database:"\
                            f" {self.git_remote}/{self.git_branch}"
                    elif self.git_remote == "?":
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

    def log_repo_info(self) -> None:
        logging.info(
            f"Git Repo {self.alias} Detected:\n"
            f"Owner: {self.git_owner}\n"
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
            f"Commits Behind: {len(self.commits_behind)}")

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
            await self._run_git_cmd("clean -d -f", retries=2)
            await self._run_git_cmd(
                f"reset --hard {self.git_remote}/{self.git_branch}",
                retries=2)

    async def fetch(self) -> None:
        self._verify_repo(check_remote=True)
        async with self.git_operation_lock:
            await self._run_git_cmd_async(
                f"fetch {self.git_remote} --prune --progress")


    async def pull(self) -> None:
        self._verify_repo()
        if self.head_detached:
            raise self.server.error(
                f"Git Repo {self.alias}: Cannot perform pull on a "
                "detached HEAD")
        cmd = "pull --progress"
        if self.cmd_helper.is_debug_enabled():
            cmd = "pull --progress --rebase"
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

    async def get_config_item(self, item: str) -> str:
        self._verify_repo()
        async with self.git_operation_lock:
            resp = await self._run_git_cmd(f"config --get {item}")
            return resp.strip()

    async def checkout(self, branch: Optional[str] = None) -> None:
        self._verify_repo()
        async with self.git_operation_lock:
            branch = branch or f"{self.git_remote}/{self.git_branch}"
            await self._run_git_cmd(f"checkout {branch} -q")

    async def run_fsck(self) -> None:
        async with self.git_operation_lock:
            await self._run_git_cmd("fsck --full", timeout=300., retries=1)

    async def clone(self) -> None:
        async with self.git_operation_lock:
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
            self.cmd_helper.notify_update_response(
                f"Git Repo {self.alias}: Git Clone Complete")

    async def get_commits_behind(self) -> List[Dict[str, Any]]:
        self._verify_repo()
        if self.is_current():
            return []
        async with self.git_operation_lock:
            branch = f"{self.git_remote}/{self.git_branch}"
            resp = await self._run_git_cmd(
                f"log {self.current_commit}..{branch} "
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
            resp = await self._run_git_cmd(f"show-ref --tags -d")
            tagged_commits: Dict[str, Any] = {}
            tags = [tag.strip() for tag in resp.split('\n') if tag.strip()]
            for tag in tags:
                sha, ref = tag.split(' ', 1)
                ref = ref.split('/')[-1]
                if ref[-3:] == "^{}":
                    # Dereference this commit and overwrite any existing tag
                    ref = ref[:-3]
                    tagged_commits[ref] = sha
                elif ref not in tagged_commits:
                    # This could be a lightweight tag pointing to a commit.  If
                    # it is an annotated tag it will be overwritten by the
                    # dereferenced tag
                    tagged_commits[ref] = sha
            # Return tagged commits as SHA keys mapped to tag values
            return {v: k for k, v in tagged_commits.items()}

    def get_repo_status(self) -> Dict[str, Any]:
        return {
            'detected_type': "git_repo",
            'remote_alias': self.git_remote,
            'branch': self.git_branch,
            'owner': self.git_owner,
            'version': self.current_version,
            'remote_version': self.upstream_version,
            'current_hash': self.current_commit,
            'remote_hash': self.upstream_commit,
            'is_dirty': self.dirty,
            'detached': self.head_detached,
            'commits_behind': self.commits_behind,
            'git_messages': self.git_messages,
            'full_version_string': self.full_version_string,
            'pristine': not self.dirty
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

    async def _repair_loose_objects(self) -> bool:
        try:
            await self.cmd_helper.run_cmd_with_response(
                "find .git/objects/ -type f -empty | xargs rm",
                timeout=10., retries=1, cwd=str(self.git_path))
            await self._run_git_cmd_async(
                "fetch --all -p", retries=1, fix_loose=False)
            await self._run_git_cmd("fsck --full", timeout=300., retries=1)
        except Exception:
            logging.exception("Attempt to repair loose objects failed")
            return False
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
            elif fix_loose:
                if GIT_OBJ_ERR in "\n".join(self.git_messages):
                    ret = await self._repair_loose_objects()
                    if ret:
                        break
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
                           env: Optional[Dict[str, str]] = None
                           ) -> str:
        try:
            return await self.cmd_helper.run_cmd_with_response(
                f"git -C {self.git_path} {git_args}",
                timeout=timeout, retries=retries, env=env, sig_idx=2)
        except self.cmd_helper.scmd_error as e:
            stdout = e.stdout.decode().strip()
            stderr = e.stderr.decode().strip()
            if stdout:
                self.git_messages.append(stdout)
            if stderr:
                self.git_messages.append(stderr)
            raise
