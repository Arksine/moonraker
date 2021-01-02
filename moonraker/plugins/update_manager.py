# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import re
import logging
import json
import sys
import shutil
import zipfile
import io
import asyncio
import time
import tornado.gen
from tornado.ioloop import IOLoop
from tornado.httpclient import AsyncHTTPClient
from tornado.locks import Event

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../.."))
SUPPLEMENTAL_CFG_PATH = os.path.join(
    MOONRAKER_PATH, "scripts/update_manager.conf")
APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"
SUPPORTED_DISTROS = ["debian"]

class UpdateManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.config = config
        self.config.read_supplemental_config(SUPPLEMENTAL_CFG_PATH)
        AsyncHTTPClient.configure(None, defaults=dict(user_agent="Moonraker"))
        self.http_client = AsyncHTTPClient()
        self.repo_debug = config.getboolean('enable_repo_debug', False)
        self.distro = config.get('distro', "debian").lower()
        if self.distro not in SUPPORTED_DISTROS:
            raise config.error(f"Unsupported distro: {self.distro}")
        if self.repo_debug:
            logging.warn("UPDATE MANAGER: REPO DEBUG ENABLED")
        env = sys.executable
        self.updaters = {
            "system": PackageUpdater(self),
            "moonraker": GitUpdater(self, "moonraker", MOONRAKER_PATH, env)
        }
        self.current_update = None
        client_repo = config.get("client_repo", None)
        if client_repo is not None:
            client_path = os.path.expanduser(config.get("client_path"))
            if os.path.islink(client_path):
                raise config.error(
                    "Option 'client_path' cannot be set to a symbolic link")
            self.updaters['client'] = ClientUpdater(
                self, client_repo, client_path)

        # GitHub API Rate Limit Tracking
        self.gh_rate_limit = None
        self.gh_limit_remaining = None
        self.gh_limit_reset_time = None
        self.gh_init_evt = Event()

        self.server.register_endpoint(
            "/machine/update/moonraker", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/klipper", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/system", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/client", ["POST"],
            self._handle_update_request)
        self.server.register_endpoint(
            "/machine/update/status", ["GET"],
            self._handle_status_request)

        # Register Ready Event
        self.server.register_event_handler(
            "server:klippy_identified", self._set_klipper_repo)
        # Initialize GitHub API Rate Limits
        IOLoop.current().spawn_callback(self._init_api_rate_limit)

    async def _set_klipper_repo(self):
        kinfo = self.server.get_klippy_info()
        if not kinfo:
            logging.info("No valid klippy info received")
            return
        kpath = kinfo['klipper_path']
        env = kinfo['python_path']
        kupdater = self.updaters.get('klipper', None)
        if kupdater is not None and kupdater.repo_path == kpath and \
                kupdater.env == env:
            # Current Klipper Updater is valid
            return
        self.updaters['klipper'] = GitUpdater(self, "klipper", kpath, env)

    async def _handle_update_request(self, web_request):
        app = web_request.get_endpoint().split("/")[-1]
        inc_deps = web_request.get_boolean('include_deps', False)
        if self.current_update:
            raise self.server.error("A current update is in progress")
        updater = self.updaters.get(app, None)
        if updater is None:
            raise self.server.error(f"Updater {app} not available")
        self.current_update = (app, id(web_request))
        try:
            await updater.update(inc_deps)
        except Exception:
            self.current_update = None
            raise
        self.current_update = None
        return "ok"

    async def _handle_status_request(self, web_request):
        refresh = web_request.get_boolean('refresh', False)
        vinfo = {}
        for name, updater in self.updaters.items():
            await updater.check_initialized(120.)
            if refresh:
                ret = updater.refresh()
                if asyncio.iscoroutine(ret):
                    await ret
            if hasattr(updater, "get_update_status"):
                vinfo[name] = updater.get_update_status()
        return {
            'version_info': vinfo,
            'github_rate_limit': self.gh_rate_limit,
            'github_requests_remaining': self.gh_limit_remaining,
            'github_limit_reset_time': self.gh_limit_reset_time,
            'busy': self.current_update is not None}

    async def execute_cmd(self, cmd, timeout=10., notify=False, retries=1):
        shell_command = self.server.lookup_plugin('shell_command')
        cb = self.notify_update_response if notify else None
        scmd = shell_command.build_shell_command(cmd, callback=cb)
        while retries:
            if await scmd.run(timeout=timeout, verbose=notify):
                break
            retries -= 1
        if not retries:
            raise self.server.error("Shell Command Error")

    async def execute_cmd_with_response(self, cmd, timeout=10.):
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        result = await scmd.run_with_response(timeout, retries=5)
        if result is None:
            raise self.server.error(f"Error Running Command: {cmd}")
        return result

    async def _init_api_rate_limit(self):
        url = "https://api.github.com/rate_limit"
        while 1:
            try:
                resp = await self.github_api_request(url, is_init=True)
                core = resp['resources']['core']
                self.gh_rate_limit = core['limit']
                self.gh_limit_remaining = core['remaining']
                self.gh_limit_reset_time = core['reset']
            except Exception:
                logging.exception("Error Initializing GitHub API Rate Limit")
                await tornado.gen.sleep(30.)
            else:
                reset_time = time.ctime(self.gh_limit_reset_time)
                logging.info(
                    "GitHub API Rate Limit Initialized\n"
                    f"Rate Limit: {self.gh_rate_limit}\n"
                    f"Rate Limit Remaining: {self.gh_limit_remaining}\n"
                    f"Rate Limit Reset Time: {reset_time}, "
                    f"Seconds Since Epoch: {self.gh_limit_reset_time}")
                break
        self.gh_init_evt.set()

    async def github_api_request(self, url, etag=None, is_init=False):
        if not is_init:
            timeout = time.time() + 30.
            try:
                await self.gh_init_evt.wait(timeout)
            except Exception:
                raise self.server.error(
                    "Timeout while waiting for GitHub "
                    "API Rate Limit initialization")
        if self.gh_limit_remaining == 0:
            curtime = time.time()
            if curtime < self.gh_limit_reset_time:
                raise self.server.error(
                    f"GitHub Rate Limit Reached\nRequest: {url}\n"
                    f"Limit Reset Time: {time.ctime(self.gh_limit_remaining)}")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if etag is not None:
            headers['If-None-Match'] = etag
        retries = 5
        while retries:
            try:
                resp = await self.http_client.fetch(
                    url, headers=headers, connect_timeout=5.,
                    request_timeout=5., raise_error=False)
            except Exception:
                retries -= 1
                msg = f"Error Processing GitHub API request: {url}"
                if not retries:
                    raise self.server.error(msg)
                logging.exception(msg)
                await tornado.gen.sleep(1.)
                continue
            etag = resp.headers.get('etag', None)
            if etag is not None:
                if etag[:2] == "W/":
                    etag = etag[2:]
            logging.info(
                "GitHub API Request Processed\n"
                f"URL: {url}\n"
                f"Response Code: {resp.code}\n"
                f"Response Reason: {resp.reason}\n"
                f"ETag: {etag}")
            if resp.code == 403:
                raise self.server.error(
                    f"Forbidden GitHub Request: {resp.reason}")
            elif resp.code == 304:
                logging.info(f"Github Request not Modified: {url}")
                return None
            if resp.code != 200:
                retries -= 1
                if not retries:
                    raise self.server.error(
                        f"Github Request failed: {resp.code} {resp.reason}")
                logging.info(
                    f"Github request error, {retries} retries remaining")
                await tornado.gen.sleep(1.)
                continue
            # Update rate limit on return success
            if 'X-Ratelimit-Limit' in resp.headers and not is_init:
                self.gh_rate_limit = int(resp.headers['X-Ratelimit-Limit'])
                self.gh_limit_remaining = int(
                    resp.headers['X-Ratelimit-Remaining'])
                self.gh_limit_reset_time = float(
                    resp.headers['X-Ratelimit-Reset'])
            decoded = json.loads(resp.body)
            decoded['etag'] = etag
            return decoded

    async def http_download_request(self, url):
        retries = 5
        while retries:
            try:
                resp = await self.http_client.fetch(
                    url, headers={"Accept": "application/zip"},
                    connect_timeout=5., request_timeout=120.)
            except Exception:
                retries -= 1
                logging.exception("Error Processing Download")
                if not retries:
                    raise
                await tornado.gen.sleep(1.)
                continue
            return resp.body

    def notify_update_response(self, resp, is_complete=False):
        resp = resp.strip()
        if isinstance(resp, bytes):
            resp = resp.decode()
        notification = {
            'message': resp,
            'application': None,
            'proc_id': None,
            'complete': is_complete}
        if self.current_update is not None:
            notification['application'] = self.current_update[0]
            notification['proc_id'] = self.current_update[1]
        self.server.send_event(
            "update_manager:update_response", notification)

    def close(self):
        self.http_client.close()


class GitUpdater:
    def __init__(self, umgr, name, path, env):
        self.server = umgr.server
        self.execute_cmd = umgr.execute_cmd
        self.execute_cmd_with_response = umgr.execute_cmd_with_response
        self.notify_update_response = umgr.notify_update_response
        distro = umgr.distro
        config = umgr.config
        self.repo_info = config[f"repo_info {name}"].get_options()
        self.dist_info = config[f"dist_info {distro} {name}"].get_options()
        self.name = name
        self.repo_path = path
        self.env = env
        self.version = self.cur_hash = "?"
        self.remote_version = self.remote_hash = "?"
        self.init_evt = Event()
        self.debug = umgr.repo_debug
        self.remote = "origin"
        self.branch = "master"
        self.is_valid = self.is_dirty = self.detached = False
        IOLoop.current().spawn_callback(self.refresh)

    def _get_version_info(self):
        ver_path = os.path.join(self.repo_path, "scripts/version.txt")
        vinfo = {}
        if os.path.isfile(ver_path):
            data = ""
            with open(ver_path, 'r') as f:
                data = f.read()
            try:
                entries = [e.strip() for e in data.split('\n') if e.strip()]
                vinfo = dict([i.split('=') for i in entries])
                vinfo = {k: tuple(re.findall(r"\d+", v)) for k, v in
                         vinfo.items()}
            except Exception:
                pass
            else:
                self._log_info(f"Version Info Found: {vinfo}")
        vinfo['version'] = tuple(re.findall(r"\d+", self.version))
        return vinfo

    def _log_exc(self, msg, traceback=True):
        log_msg = f"Repo {self.name}: {msg}"
        if traceback:
            logging.exception(log_msg)
        else:
            logging.info(log_msg)
        return self.server.error(msg)

    def _log_info(self, msg):
        log_msg = f"Repo {self.name}: {msg}"
        logging.info(log_msg)

    def _notify_status(self, msg, is_complete=False):
        log_msg = f"Repo {self.name}: {msg}"
        logging.debug(log_msg)
        self.notify_update_response(log_msg, is_complete)

    async def check_initialized(self, timeout=None):
        if self.init_evt.is_set():
            return
        if timeout is not None:
            timeout = IOLoop.current().time() + timeout
        await self.init_evt.wait(timeout)

    async def refresh(self):
        await self._check_version()
        self.init_evt.set()

    async def _check_version(self, need_fetch=True):
        self.is_valid = self.detached = False
        self.cur_hash = self.branch = self.remote = "?"
        self.version = self.remote_version = "?"
        try:
            blist = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} branch --list")
            if blist.startswith("fatal:"):
                self._log_info(f"Invalid git repo at path '{self.repo_path}'")
                return
            branch = None
            for b in blist.split("\n"):
                b = b.strip()
                if b[0] == "*":
                    branch = b[2:]
                    break
            if branch is None:
                self._log_info(
                    "Unable to retreive current branch from branch list\n"
                    f"{blist}")
                return
            if "HEAD detached" in branch:
                bparts = branch.split()[-1].strip("()")
                self.remote, self.branch = bparts.split("/")
                self.detached = True
            else:
                self.branch = branch.strip()
                self.remote = await self.execute_cmd_with_response(
                    f"git -C {self.repo_path} config --get"
                    f" branch.{self.branch}.remote")
            if need_fetch:
                await self.execute_cmd(
                    f"git -C {self.repo_path} fetch {self.remote} --prune -q",
                    retries=3)
            remote_url = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} remote get-url {self.remote}")
            cur_hash = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} rev-parse HEAD")
            remote_hash = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} rev-parse "
                f"{self.remote}/{self.branch}")
            repo_version = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} describe --always "
                "--tags --long --dirty")
            remote_version = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} describe {self.remote}/{self.branch}"
                " --always --tags --long")
        except Exception:
            self._log_exc("Error retreiving git info")
            return

        self.is_dirty = repo_version.endswith("dirty")
        versions = []
        for ver in [repo_version, remote_version]:
            tag_version = "?"
            ver_match = re.match(r"v\d+\.\d+\.\d-\d+", ver)
            if ver_match:
                tag_version = ver_match.group()
            versions.append(tag_version)
        self.version, self.remote_version = versions
        self.cur_hash = cur_hash.strip()
        self.remote_hash = remote_hash.strip()
        self._log_info(
            f"Repo Detected:\nPath: {self.repo_path}\nRemote: {self.remote}\n"
            f"Branch: {self.branch}\nRemote URL: {remote_url}\n"
            f"Current SHA: {self.cur_hash}\n"
            f"Remote SHA: {self.remote_hash}\nVersion: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"Is Dirty: {self.is_dirty}\nIs Detached: {self.detached}")
        if self.debug:
            self.is_valid = True
            self._log_info("Debug enabled, bypassing official repo check")
        elif self.branch == "master" and self.remote == "origin":
            if self.detached:
                self._log_info("Detached HEAD detected, repo invalid")
                return
            remote_url = remote_url.lower()
            if remote_url[-4:] != ".git":
                remote_url += ".git"
            if remote_url == self.repo_info['origin']:
                self.is_valid = True
                self._log_info("Validity check for git repo passed")
            else:
                self._log_info(f"Invalid git origin url '{remote_url}'")
        else:
            self._log_info(
                "Git repo not on offical remote/branch: "
                f"{self.remote}/{self.branch}")

    async def update(self, update_deps=False):
        if not self.is_valid:
            raise self._log_exc("Update aborted, repo is not valid", False)
        if self.is_dirty:
            raise self._log_exc(
                "Update aborted, repo is has been modified", False)
        if self.remote_hash == self.cur_hash:
            # No need to update
            return
        self._notify_status("Updating Repo...")
        try:
            if self.detached:
                await self.execute_cmd(
                    f"git -C {self.repo_path} fetch {self.remote} -q",
                    retries=3)
                await self.execute_cmd(
                    f"git -C {self.repo_path} checkout"
                    f" {self.remote}/{self.branch} -q")
            else:
                await self.execute_cmd(
                    f"git -C {self.repo_path} pull -q", retries=3)
        except Exception:
            raise self._log_exc("Error running 'git pull'")
        # Check Semantic Versions
        vinfo = self._get_version_info()
        cur_version = vinfo.get('version', ())
        update_deps |= cur_version < vinfo.get('deps_version', ())
        need_env_rebuild = cur_version < vinfo.get('env_version', ())
        if update_deps:
            await self._install_packages()
            await self._update_virtualenv(need_env_rebuild)
        elif need_env_rebuild:
            await self._update_virtualenv(True)
        # Refresh local repo state
        await self._check_version(need_fetch=False)
        if self.name == "moonraker":
            # Launch restart async so the request can return
            # before the server restarts
            self._notify_status("Update Finished...",
                                is_complete=True)
            IOLoop.current().call_later(.1, self.restart_service)
        else:
            await self.restart_service()
            self._notify_status("Update Finished...", is_complete=True)

    async def _install_packages(self):
        # Open install file file and read
        inst_script = self.dist_info['install_script']
        inst_path = os.path.join(self.repo_path, inst_script)
        if not os.path.isfile(inst_path):
            self._log_info(f"Unable to open install script: {inst_path}")
            return
        with open(inst_path, 'r') as f:
            data = f.read()
        packages = re.findall(r'PKGLIST="(.*)"', data)
        packages = [p.lstrip("${PKGLIST}").strip() for p in packages]
        if not packages:
            self._log_info(f"No packages found in script: {inst_path}")
            return
        # TODO: Log and notify that packages will be installed
        pkgs = " ".join(packages)
        logging.debug(f"Repo {self.name}: Detected Packages: {pkgs}")
        self._notify_status("Installing system dependencies...")
        # Install packages with apt-get
        try:
            await self.execute_cmd(
                f"{APT_CMD} update", timeout=300., notify=True)
            await self.execute_cmd(
                f"{APT_CMD} install --yes {pkgs}", timeout=3600.,
                notify=True)
        except Exception:
            self._log_exc("Error updating packages via apt-get")
            return

    async def _update_virtualenv(self, rebuild_env=False):
        # Update python dependencies
        bin_dir = os.path.dirname(self.env)
        env_path = os.path.normpath(os.path.join(bin_dir, ".."))
        if rebuild_env:
            env_args = self.repo_info['venv_args']
            self._notify_status(f"Creating virtualenv at: {env_path}...")
            if os.path.exists(env_path):
                shutil.rmtree(env_path)
            try:
                await self.execute_cmd(
                    f"virtualenv {env_args} {env_path}", timeout=300.)
            except Exception:
                self._log_exc(f"Error creating virtualenv")
                return
            if not os.path.expanduser(self.env):
                raise self._log_exc("Failed to create new virtualenv", False)
        reqs = os.path.join(
            self.repo_path, self.repo_info['requirements'])
        if not os.path.isfile(reqs):
            self._log_exc(f"Invalid path to requirements_file '{reqs}'")
            return
        pip = os.path.join(bin_dir, "pip")
        self._notify_status("Updating python packages...")
        try:
            await self.execute_cmd(
                f"{pip} install -r {reqs}", timeout=1200., notify=True,
                retries=3)
        except Exception:
            self._log_exc("Error updating python requirements")
        self._install_python_dist_requirements(env_path)

    def _install_python_dist_requirements(self, env_path):
        dist_reqs = self.dist_info.get('python_dist_packages', None)
        if dist_reqs is None:
            return
        dist_reqs = [r.strip() for r in dist_reqs.split("\n")
                     if r.strip()]
        dist_path = self.dist_info['python_dist_path']
        site_path = os.path.join(env_path, self.dist_info['env_package_path'])
        for pkg in dist_reqs:
            for f in os.listdir(dist_path):
                if f.startswith(pkg):
                    src = os.path.join(dist_path, f)
                    dest = os.path.join(site_path, f)
                    self._notify_status(f"Linking to dist package: {pkg}")
                    if os.path.islink(dest):
                        os.remove(dest)
                    elif os.path.exists(dest):
                        self._notify_status(
                            f"Error symlinking dist package: {pkg}, "
                            f"file already exists: {dest}")
                        continue
                    os.symlink(src, dest)
                    break

    async def restart_service(self):
        self._notify_status("Restarting Service...")
        try:
            await self.execute_cmd(f"sudo systemctl restart {self.name}")
        except Exception:
            raise self._log_exc("Error restarting service")

    def get_update_status(self):
        return {
            'remote_alias': self.remote,
            'branch': self.branch,
            'version': self.version,
            'remote_version': self.remote_version,
            'current_hash': self.cur_hash,
            'remote_hash': self.remote_hash,
            'is_dirty': self.is_dirty,
            'is_valid': self.is_valid,
            'detached': self.detached,
            'debug_enabled': self.debug}


class PackageUpdater:
    def __init__(self, umgr):
        self.server = umgr.server
        self.execute_cmd = umgr.execute_cmd
        self.execute_cmd_with_response = umgr.execute_cmd_with_response
        self.notify_update_response = umgr.notify_update_response
        self.available_packages = []
        self.init_evt = Event()
        IOLoop.current().spawn_callback(self.refresh)

    async def refresh(self):
        # TODO: Use python-apt python lib rather than command line for updates
        try:
            await self.execute_cmd(f"{APT_CMD} update", timeout=300.)
            res = await self.execute_cmd_with_response(
                "apt list --upgradable")
        except Exception:
            logging.exception("Error Refreshing System Packages")
        else:
            pkg_list = [p.strip() for p in res.split("\n") if p.strip()]
            if pkg_list:
                pkg_list = pkg_list[2:]
                self.available_packages = [p.split("/", maxsplit=1)[0]
                                           for p in pkg_list]
            pkg_list = "\n".join(self.available_packages)
            logging.info(
                f"Detected {len(self.available_packages)} package updates:"
                f"\n{pkg_list}")
        self.init_evt.set()

    async def check_initialized(self, timeout=None):
        if self.init_evt.is_set():
            return
        if timeout is not None:
            timeout = IOLoop.current().time() + timeout
        await self.init_evt.wait(timeout)

    async def update(self, *args):
        self.notify_update_response("Updating packages...")
        try:
            await self.execute_cmd(
                f"{APT_CMD} update", timeout=300., notify=True)
            await self.execute_cmd(
                f"{APT_CMD} upgrade --yes", timeout=3600., notify=True)
        except Exception:
            raise self.server.error("Error updating system packages")
        self.available_packages = []
        self.notify_update_response("Package update finished...",
                                    is_complete=True)

    def get_update_status(self):
        return {
            'package_count': len(self.available_packages),
            'package_list': self.available_packages
        }

class ClientUpdater:
    def __init__(self, umgr, repo, path):
        self.umgr = umgr
        self.server = umgr.server
        self.notify_update_response = umgr.notify_update_response
        self.repo = repo.strip().strip("/")
        self.name = self.repo.split("/")[-1]
        self.path = path
        self.version = self.remote_version = self.dl_url = "?"
        self.etag = None
        self.init_evt = Event()
        self._get_local_version()
        logging.info(f"\nInitializing Client Updater: '{self.name}',"
                     f"\nversion: {self.version}"
                     f"\npath: {self.path}")
        IOLoop.current().spawn_callback(self.refresh)

    def _get_local_version(self):
        version_path = os.path.join(self.path, ".version")
        if os.path.isfile(os.path.join(self.path, ".version")):
            with open(version_path, "r") as f:
                v = f.read()
            self.version = v.strip()

    async def check_initialized(self, timeout=None):
        if self.init_evt.is_set():
            return
        if timeout is not None:
            timeout = IOLoop.current().time() + timeout
        await self.init_evt.wait(timeout)

    async def refresh(self):
        # Local state
        self._get_local_version()

        # Remote state
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            result = await self.umgr.github_api_request(url, etag=self.etag)
        except Exception:
            logging.exception(f"Client {self.repo}: Github Request Error")
            result = {}
        if result is None:
            # No change, update not necessary
            return None
        self.etag = result.get('etag', None)
        self.remote_version = result.get('name', "?")
        release_assets = result.get('assets', [{}])[0]
        self.dl_url = release_assets.get('browser_download_url', "?")
        logging.info(
            f"Github client Info Received:\nRepo: {self.name}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"url: {self.dl_url}")
        self.init_evt.set()

    async def update(self, *args):
        if self.remote_version == "?":
            await self.refresh()
            if self.remote_version == "?":
                raise self.server.error(
                    f"Client {self.repo}: Unable to locate update")
        if self.dl_url == "?":
            raise self.server.error(
                f"Client {self.repo}: Invalid download url")
        if self.version == self.remote_version:
            # Already up to date
            return
        if os.path.isdir(self.path):
            shutil.rmtree(self.path)
        os.mkdir(self.path)
        self.notify_update_response(f"Downloading Client: {self.name}")
        archive = await self.umgr.http_download_request(self.dl_url)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(self.path)
        self.version = self.remote_version
        version_path = os.path.join(self.path, ".version")
        if not os.path.exists(version_path):
            with open(version_path, "w") as f:
                f.write(self.version)
        self.notify_update_response(f"Client Update Finished: {self.name}",
                                    is_complete=True)

    def get_update_status(self):
        return {
            'name': self.name,
            'version': self.version,
            'remote_version': self.remote_version
        }

def load_plugin(config):
    return UpdateManager(config)
