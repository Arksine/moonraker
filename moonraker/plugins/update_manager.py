# Provides updates for Klipper and Moonraker
#
# Copyright (C) 2020  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import glob
import re
import logging
import json
import sys
import shutil
import zipfile
import io
import asyncio
import time
import tempfile
import tornado.gen
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.httpclient import AsyncHTTPClient
from tornado.locks import Event, Condition, Lock

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../.."))
SUPPLEMENTAL_CFG_PATH = os.path.join(
    MOONRAKER_PATH, "scripts/update_manager.conf")
APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"
SUPPORTED_DISTROS = ["debian"]

# Check To see if Updates are necessary each hour
UPDATE_REFRESH_INTERVAL_MS = 3600000
# Perform auto refresh no sooner than 12 hours apart
MIN_REFRESH_TIME = 43200
# Perform auto refresh no later than 4am
MAX_PKG_UPDATE_HOUR = 4

class UpdateManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.config = config
        self.config.read_supplemental_config(SUPPLEMENTAL_CFG_PATH)
        self.repo_debug = config.getboolean('enable_repo_debug', False)
        auto_refresh_enabled = config.getboolean('enable_auto_refresh', False)
        self.distro = config.get('distro', "debian").lower()
        if self.distro not in SUPPORTED_DISTROS:
            raise config.error(f"Unsupported distro: {self.distro}")
        if self.repo_debug:
            logging.warn("UPDATE MANAGER: REPO DEBUG ENABLED")
        env = sys.executable
        mooncfg = self.config[f"update_manager static {self.distro} moonraker"]
        self.updaters = {
            "system": PackageUpdater(self),
            "moonraker": GitUpdater(self, mooncfg, MOONRAKER_PATH, env)
        }
        self.current_update = None
        # TODO: Check for client config in [update_manager].  This is
        # deprecated and will be removed.
        client_repo = config.get("client_repo", None)
        if client_repo is not None:
            client_path = config.get("client_path")
            name = client_repo.split("/")[-1]
            self.updaters[name] = WebUpdater(
                self, {'repo': client_repo, 'path': client_path})
        client_sections = self.config.get_prefix_sections(
            "update_manager client")
        for section in client_sections:
            cfg = self.config[section]
            name = section.split()[-1]
            if name in self.updaters:
                raise config.error("Client repo named %s already added"
                                   % (name,))
            client_type = cfg.get("type")
            if client_type == "git_repo":
                self.updaters[name] = GitUpdater(self, cfg)
            elif client_type == "web":
                self.updaters[name] = WebUpdater(self, cfg)
            else:
                raise config.error("Invalid type '%s' for section [%s]"
                                   % (client_type, section))

        # GitHub API Rate Limit Tracking
        self.gh_rate_limit = None
        self.gh_limit_remaining = None
        self.gh_limit_reset_time = None
        self.gh_init_evt = Event()
        self.cmd_request_lock = Lock()
        self.is_refreshing = False

        # Auto Status Refresh
        self.last_auto_update_time = 0
        self.refresh_cb = None
        if auto_refresh_enabled:
            self.refresh_cb = PeriodicCallback(
                self._handle_auto_refresh, UPDATE_REFRESH_INTERVAL_MS)
            self.refresh_cb.start()

        AsyncHTTPClient.configure(None, defaults=dict(user_agent="Moonraker"))
        self.http_client = AsyncHTTPClient()

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
        self.server.register_notification("update_manager:update_response")
        self.server.register_notification("update_manager:update_refreshed")

        # Register Ready Event
        self.server.register_event_handler(
            "server:klippy_identified", self._set_klipper_repo)
        # Initialize GitHub API Rate Limits and configured updaters
        IOLoop.current().spawn_callback(
            self._initalize_updaters, list(self.updaters.values()))

    async def _initalize_updaters(self, initial_updaters):
        self.is_refreshing = True
        await self._init_api_rate_limit()
        for updater in initial_updaters:
            if isinstance(updater, PackageUpdater):
                ret = updater.refresh(False)
            else:
                ret = updater.refresh()
            if asyncio.iscoroutine(ret):
                await ret
        self.is_refreshing = False

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
        kcfg = self.config[f"update_manager static {self.distro} klipper"]
        self.updaters['klipper'] = GitUpdater(self, kcfg, kpath, env)
        await self.updaters['klipper'].refresh()

    async def _check_klippy_printing(self):
        klippy_apis = self.server.lookup_plugin('klippy_apis')
        result = await klippy_apis.query_objects(
            {'print_stats': None}, default={})
        pstate = result.get('print_stats', {}).get('state', "")
        return pstate.lower() == "printing"

    async def _handle_auto_refresh(self):
        if await self._check_klippy_printing():
            # Don't Refresh during a print
            logging.info("Klippy is printing, auto refresh aborted")
            return
        cur_time = time.time()
        cur_hour = time.localtime(cur_time).tm_hour
        time_diff = cur_time - self.last_auto_update_time
        # Update packages if it has been more than 12 hours
        # and the local time is between 12AM and 5AM
        if time_diff < MIN_REFRESH_TIME or cur_hour >= MAX_PKG_UPDATE_HOUR:
            # Not within the update time window
            return
        self.last_auto_update_time = cur_time
        vinfo = {}
        need_refresh_all = not self.is_refreshing
        async with self.cmd_request_lock:
            self.is_refreshing = True
            try:
                for name, updater in list(self.updaters.items()):
                    if need_refresh_all:
                        ret = updater.refresh()
                        if asyncio.iscoroutine(ret):
                            await ret
                    if hasattr(updater, "get_update_status"):
                        vinfo[name] = updater.get_update_status()
            except Exception:
                logging.exception("Unable to Refresh Status")
                return
            finally:
                self.is_refreshing = False
        uinfo = {
            'version_info': vinfo,
            'github_rate_limit': self.gh_rate_limit,
            'github_requests_remaining': self.gh_limit_remaining,
            'github_limit_reset_time': self.gh_limit_reset_time,
            'busy': self.current_update is not None}
        self.server.send_event("update_manager:update_refreshed", uinfo)

    async def _handle_update_request(self, web_request):
        if await self._check_klippy_printing():
            raise self.server.error("Update Refused: Klippy is printing")
        app = web_request.get_endpoint().split("/")[-1]
        if app == "client":
            app = web_request.get('name')
        inc_deps = web_request.get_boolean('include_deps', False)
        if self.current_update is not None and \
                self.current_update[0] == app:
            return f"Object {app} is currently being updated"
        updater = self.updaters.get(app, None)
        if updater is None:
            raise self.server.error(f"Updater {app} not available")
        async with self.cmd_request_lock:
            self.current_update = (app, id(web_request))
            try:
                await updater.update(inc_deps)
            except Exception as e:
                self.notify_update_response(f"Error updating {app}")
                self.notify_update_response(str(e), is_complete=True)
                raise
            finally:
                self.current_update = None
        return "ok"

    async def _handle_status_request(self, web_request):
        check_refresh = web_request.get_boolean('refresh', False)
        # Don't refresh if a print is currently in progress or
        # if an update is in progress.  Just return the current
        # state
        if self.current_update is not None or \
                await self._check_klippy_printing():
            check_refresh = False
        need_refresh = False
        if check_refresh:
            # If there is an outstanding request processing a
            # refresh, we don't need to do it again.
            need_refresh = not self.is_refreshing
            await self.cmd_request_lock.acquire()
            self.is_refreshing = True
        vinfo = {}
        try:
            for name, updater in list(self.updaters.items()):
                await updater.check_initialized(120.)
                if need_refresh:
                    ret = updater.refresh()
                    if asyncio.iscoroutine(ret):
                        await ret
                if hasattr(updater, "get_update_status"):
                    vinfo[name] = updater.get_update_status()
        except Exception:
            raise
        finally:
            if check_refresh:
                self.is_refreshing = False
                self.cmd_request_lock.release()
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
                timeout = time.time() + 10.
                fut = self.http_client.fetch(
                    url, headers=headers, connect_timeout=5.,
                    request_timeout=5., raise_error=False)
                resp = await tornado.gen.with_timeout(timeout, fut)
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
                timeout = time.time() + 130.
                fut = self.http_client.fetch(
                    url, headers={"Accept": "application/zip"},
                    connect_timeout=5., request_timeout=120.)
                resp = await tornado.gen.with_timeout(timeout, fut)
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
        if self.refresh_cb is not None:
            self.refresh_cb.stop()


class GitUpdater:
    def __init__(self, umgr, config, path=None, env=None):
        self.server = umgr.server
        self.execute_cmd = umgr.execute_cmd
        self.execute_cmd_with_response = umgr.execute_cmd_with_response
        self.notify_update_response = umgr.notify_update_response
        self.name = config.get_name().split()[-1]
        self.owner = "?"
        self.repo_path = path
        if path is None:
            self.repo_path = config.get('path')
        self.env = config.get("env", env)
        dist_packages = None
        if self.env is not None:
            self.env = os.path.expanduser(self.env)
            dist_packages = config.get('python_dist_packages', None)
            self.python_reqs = os.path.join(
                self.repo_path, config.get("requirements"))
        self.origin = config.get("origin").lower()
        self.install_script = config.get('install_script', None)
        if self.install_script is not None:
            self.install_script = os.path.abspath(os.path.join(
                self.repo_path, self.install_script))
        self.venv_args = config.get('venv_args', None)
        self.python_dist_packages = None
        self.python_dist_path = None
        self.env_package_path = None
        if dist_packages is not None:
            self.python_dist_packages = [
                p.strip() for p in dist_packages.split('\n')
                if p.strip()]
            self.python_dist_path = os.path.abspath(
                config.get('python_dist_path'))
            env_package_path = os.path.abspath(os.path.join(
                os.path.dirname(self.env), "..",
                config.get('env_package_path')))
            matches = glob.glob(env_package_path)
            if len(matches) == 1:
                self.env_package_path = matches[0]
            else:
                raise config.error("No match for 'env_package_path': %s"
                                   % (env_package_path,))
        for opt in ["repo_path", "env", "python_reqs", "install_script",
                    "python_dist_path", "env_package_path"]:
            val = getattr(self, opt)
            if val is None:
                continue
            if not os.path.exists(val):
                raise config.error("Invalid path for option '%s': %s"
                                   % (val, opt))

        self.version = self.cur_hash = "?"
        self.remote_version = self.remote_hash = "?"
        self.init_evt = Event()
        self.refresh_condition = None
        self.debug = umgr.repo_debug
        self.remote = "origin"
        self.branch = "master"
        self.is_valid = self.is_dirty = self.detached = False

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
        if self.refresh_condition is None:
            self.refresh_condition = Condition()
        else:
            self.refresh_condition.wait()
            return
        try:
            await self._check_version()
        except Exception:
            logging.exception("Error Refreshing git state")
        self.init_evt.set()
        self.refresh_condition.notify_all()
        self.refresh_condition = None

    async def _check_version(self, need_fetch=True):
        self.is_valid = self.detached = False
        self.cur_hash = self.branch = self.remote = "?"
        self.version = self.remote_version = self.owner = "?"
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

        remote_url = remote_url.strip()
        owner_match = re.match(r"https?://.*/(.*)/", remote_url)
        if owner_match is not None:
            self.owner = owner_match.group(1)
        self.is_dirty = repo_version.endswith("dirty")
        versions = []
        for ver in [repo_version, remote_version]:
            tag_version = "?"
            ver_match = re.match(r"v\d+\.\d+\.\d-\d+", ver.strip())
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
            if remote_url == self.origin:
                self.is_valid = True
                self._log_info("Validity check for git repo passed")
            else:
                self._log_info(f"Invalid git origin url '{remote_url}'")
        else:
            self._log_info(
                "Git repo not on offical remote/branch: "
                f"{self.remote}/{self.branch}")

    async def update(self, update_deps=False):
        await self.check_initialized(20.)
        if self.refresh_condition is not None:
            self.refresh_condition.wait()
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
        if self.install_script is None:
            return
        # Open install file file and read
        inst_path = self.install_script
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
        if self.env is None:
            return
        # Update python dependencies
        bin_dir = os.path.dirname(self.env)
        env_path = os.path.normpath(os.path.join(bin_dir, ".."))
        if rebuild_env:
            self._notify_status(f"Creating virtualenv at: {env_path}...")
            if os.path.exists(env_path):
                shutil.rmtree(env_path)
            try:
                await self.execute_cmd(
                    f"virtualenv {self.venv_args} {env_path}", timeout=300.)
            except Exception:
                self._log_exc(f"Error creating virtualenv")
                return
            if not os.path.exists(self.env):
                raise self._log_exc("Failed to create new virtualenv", False)
        reqs = self.python_reqs
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
        self._install_python_dist_requirements()

    def _install_python_dist_requirements(self):
        dist_reqs = self.python_dist_packages
        if dist_reqs is None:
            return
        dist_path = self.python_dist_path
        site_path = self.env_package_path
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
            'owner': self.owner,
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
        self.refresh_condition = None

    async def refresh(self, fetch_packages=True):
        # TODO: Use python-apt python lib rather than command line for updates
        if self.refresh_condition is None:
            self.refresh_condition = Condition()
        else:
            self.refresh_condition.wait()
            return
        try:
            if fetch_packages:
                await self.execute_cmd(
                    f"{APT_CMD} update", timeout=300., retries=3)
            res = await self.execute_cmd_with_response(
                "apt list --upgradable", timeout=60.)
            pkg_list = [p.strip() for p in res.split("\n") if p.strip()]
            if pkg_list:
                pkg_list = pkg_list[2:]
                self.available_packages = [p.split("/", maxsplit=1)[0]
                                           for p in pkg_list]
            pkg_list = "\n".join(self.available_packages)
            logging.info(
                f"Detected {len(self.available_packages)} package updates:"
                f"\n{pkg_list}")
        except Exception:
            logging.exception("Error Refreshing System Packages")
        self.init_evt.set()
        self.refresh_condition.notify_all()
        self.refresh_condition = None

    async def check_initialized(self, timeout=None):
        if self.init_evt.is_set():
            return
        if timeout is not None:
            timeout = IOLoop.current().time() + timeout
        await self.init_evt.wait(timeout)

    async def update(self, *args):
        await self.check_initialized(20.)
        if self.refresh_condition is not None:
            self.refresh_condition.wait()
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

class WebUpdater:
    def __init__(self, umgr, config):
        self.umgr = umgr
        self.server = umgr.server
        self.notify_update_response = umgr.notify_update_response
        self.repo = config.get('repo').strip().strip("/")
        self.owner, self.name = self.repo.split("/", 1)
        if hasattr(config, "get_name"):
            self.name = config.get_name().split()[-1]
        self.path = os.path.realpath(os.path.expanduser(
            config.get("path")))
        self.persistent_files = []
        pfiles = config.get('persistent_files', None)
        if pfiles is not None:
            self.persistent_files = [pf.strip().strip("/") for pf in
                                     pfiles.split("\n") if pf.strip()]
            if ".version" in self.persistent_files:
                raise config.error(
                    "Invalid value for option 'persistent_files': "
                    "'.version' can not be persistent")

        self.version = self.remote_version = self.dl_url = "?"
        self.etag = None
        self.init_evt = Event()
        self.refresh_condition = None
        self._get_local_version()
        logging.info(f"\nInitializing Client Updater: '{self.name}',"
                     f"\nversion: {self.version}"
                     f"\npath: {self.path}")

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
        if self.refresh_condition is None:
            self.refresh_condition = Condition()
        else:
            self.refresh_condition.wait()
            return
        try:
            self._get_local_version()
            await self._get_remote_version()
        except Exception:
            logging.exception("Error Refreshing Client")
        self.init_evt.set()
        self.refresh_condition.notify_all()
        self.refresh_condition = None

    async def _get_remote_version(self):
        # Remote state
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            result = await self.umgr.github_api_request(url, etag=self.etag)
        except Exception:
            logging.exception(f"Client {self.repo}: Github Request Error")
            result = {}
        if result is None:
            # No change, update not necessary
            return
        self.etag = result.get('etag', None)
        self.remote_version = result.get('name', "?")
        release_assets = result.get('assets', [{}])[0]
        self.dl_url = release_assets.get('browser_download_url', "?")
        logging.info(
            f"Github client Info Received:\nRepo: {self.name}\n"
            f"Local Version: {self.version}\n"
            f"Remote Version: {self.remote_version}\n"
            f"url: {self.dl_url}")

    async def update(self, *args):
        await self.check_initialized(20.)
        if self.refresh_condition is not None:
            # wait for refresh if in progess
            self.refresh_condition.wait()
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
        with tempfile.TemporaryDirectory(
                suffix=self.name, prefix="client") as tempdir:
            if os.path.isdir(self.path):
                # find and move persistent files
                for fname in os.listdir(self.path):
                    src_path = os.path.join(self.path, fname)
                    if fname in self.persistent_files:
                        dest_dir = os.path.dirname(os.path.join(tempdir, fname))
                        os.makedirs(dest_dir, exist_ok=True)
                        shutil.move(src_path, dest_dir)
                shutil.rmtree(self.path)
            os.mkdir(self.path)
            self.notify_update_response(f"Downloading Client: {self.name}")
            archive = await self.umgr.http_download_request(self.dl_url)
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                zf.extractall(self.path)
            # Move temporary files back into
            for fname in os.listdir(tempdir):
                src_path = os.path.join(tempdir, fname)
                dest_dir = os.path.dirname(os.path.join(self.path, fname))
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src_path, dest_dir)
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
            'owner': self.owner,
            'version': self.version,
            'remote_version': self.remote_version
        }

def load_plugin(config):
    return UpdateManager(config)
