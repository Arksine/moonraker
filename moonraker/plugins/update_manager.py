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
import tornado.gen
from tornado.ioloop import IOLoop
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.locks import Event

MOONRAKER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../.."))

# TODO:  May want to attempt to look up the disto for the correct
# klippy install script or have the user configure it
APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"
REPO_PREFIX = "https://api.github.com/repos"
REPO_DATA = {
    'moonraker': {
        'repo_url': f"{REPO_PREFIX}/arksine/moonraker/branches/master",
        'origin': "https://github.com/arksine/moonraker.git",
        'install_script': "scripts/install-moonraker.sh",
        'requirements': "scripts/moonraker-requirements.txt",
        'venv_args': "-p python3",
        'dist_packages': ["gpiod"],
        'dist_dir': "/usr/lib/python3/dist-packages",
        'site_pkg_path': "lib/python3.7/site-packages",
    },
    'klipper': {
        'repo_url': f"{REPO_PREFIX}/kevinoconnor/klipper/branches/master",
        'origin': "https://github.com/kevinoconnor/klipper.git",
        'install_script': "scripts/install-octopi.sh",
        'requirements': "scripts/klippy-requirements.txt",
        'venv_args': "-p python2",
        'dist_packages': [],
        'dist_dir': "",
        'site_pkg_path': "",
    }
}

class UpdateManager:
    def __init__(self, config):
        self.server = config.get_server()
        AsyncHTTPClient.configure(None, defaults=dict(user_agent="Moonraker"))
        self.http_client = AsyncHTTPClient()
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
            "server:klippy_ready", self._set_klipper_repo)

    async def _set_klipper_repo(self):
        kinfo = self.server.get_klippy_info()
        if not kinfo:
            logging.info("No valid klippy info received")
            return
        kpath = kinfo['klipper_path']
        env = kinfo['python_path']
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
            await updater.check_initialized(10.)
            if refresh:
                ret = updater.refresh()
                if asyncio.iscoroutine(ret):
                    await ret
            if hasattr(updater, "get_update_status"):
                vinfo[name] = updater.get_update_status()
        return {
            'version_info': vinfo,
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
        return await scmd.run_with_response(timeout)

    async def github_request(self, url, is_download=False):
        cto = rto = 5.
        content_type = "application/vnd.github.v3+json"
        if is_download:
            content_type = "application/zip"
            rto = 120.
        timeout = cto + rto + 2.
        request = HTTPRequest(url, headers={"Accept": content_type},
                              connect_timeout=cto, request_timeout=rto)
        retries = 5
        while True:
            to = IOLoop.current().time() + timeout
            try:
                fut = self.http_client.fetch(request)
                resp = await tornado.gen.with_timeout(to, fut)
            except Exception as e:
                retries -= 1
                if not retries:
                    raise
                logging.info(f"Github request error, retrying: {e}")
                continue
            if is_download:
                return resp.body
            decoded = json.loads(resp.body)
            return decoded

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
        self.github_request = umgr.github_request
        self.name = name
        self.repo_path = path
        self.env = env
        self.version = self.cur_hash = self.remote_hash = "?"
        self.init_evt = Event()
        self.is_valid = self.is_dirty = False
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
            to = IOLoop.current().time() + timeout
        await self.init_evt.wait(to)

    async def refresh(self):
        await self._check_local_version()
        await self._check_remote_version()
        self.init_evt.set()

    async def _check_local_version(self):
        self.is_valid = False
        self.cur_hash = "?"
        try:
            branch = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} rev-parse --abbrev-ref HEAD")
            origin = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} remote get-url origin")
            hash = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} rev-parse HEAD")
            repo_version = await self.execute_cmd_with_response(
                f"git -C {self.repo_path} describe --always "
                "--tags --long --dirty")
        except Exception:
            self._log_exc("Error retreiving git info")
            return

        self.is_dirty = repo_version.endswith("dirty")
        tag_version = "?"
        ver_match = re.match(r"v\d+\.\d+\.\d-\d+", repo_version)
        if ver_match:
            tag_version = ver_match.group()
        self.version = tag_version

        if not branch.startswith("fatal:"):
            self.cur_hash = hash
            if branch == "master":
                origin = origin.lower()
                if origin[-4:] != ".git":
                    origin += ".git"
                if origin == REPO_DATA[self.name]['origin']:
                    self.is_valid = True
                    self._log_info("Validity check for git repo passed")
                else:
                    self._log_info(f"Invalid git origin '{origin}'")
            else:
                self._log_info("Git repo not on master branch")
        else:
            self._log_info(f"Invalid git repo at path '{self.repo_path}'")

    async def _check_remote_version(self):
        repo_url = REPO_DATA[self.name]['repo_url']
        try:
            branch_info = await self.github_request(repo_url)
        except Exception:
            raise self._log_exc(f"Error retreiving github info")
        commit_hash = branch_info.get('commit', {}).get('sha', None)
        if commit_hash is None:
            self.is_valid = False
            self.upstream_version = "?"
            raise self._log_exc(f"Invalid github response", False)
        self._log_info(f"Received latest commit hash: {commit_hash}")
        self.remote_hash = commit_hash

    async def update(self, update_deps=False):
        if not self.is_valid:
            raise self._log_exc("Update aborted, repo is not valid", False)
        if self.is_dirty:
            raise self._log_exc(
                "Update aborted, repo is has been modified", False)
        if self.remote_hash == "?":
            await self._check_remote_version()
        if self.remote_hash == self.cur_hash:
            # No need to update
            return
        self._notify_status("Updating Repo...")
        try:
            await self.execute_cmd(f"git -C {self.repo_path} pull -q")
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
        await self._check_local_version()
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
        inst_script = REPO_DATA[self.name]['install_script']
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
        if rebuild_env:
            env_path = os.path.normpath(os.path.join(bin_dir, ".."))
            env_args = REPO_DATA[self.name]['venv_args']
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
            dist_pkgs = REPO_DATA[self.name]['dist_packages']
            dist_dir = REPO_DATA[self.name]['dist_dir']
            site_path = REPO_DATA[self.name]['site_pkg_path']
            for pkg in dist_pkgs:
                for f in os.listdir(dist_dir):
                    if f.startswith(pkg):
                        src = os.path.join(dist_dir, f)
                        dest = os.path.join(env_path, site_path, f)
                        self._notify_status(f"Linking to dist package: {pkg}")
                        os.symlink(src, dest)
                        break
        reqs = os.path.join(
            self.repo_path, REPO_DATA[self.name]['requirements'])
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

    async def restart_service(self):
        self._notify_status("Restarting Service...")
        try:
            await self.execute_cmd(f"sudo systemctl restart {self.name}")
        except Exception:
            raise self._log_exc("Error restarting service")

    def get_update_status(self):
        return {
            'version': self.version,
            'current_hash': self.cur_hash,
            'remote_hash': self.remote_hash,
            'is_dirty': self.is_dirty,
            'is_valid': self.is_valid}


class PackageUpdater:
    def __init__(self, umgr):
        self.server = umgr.server
        self.execute_cmd = umgr.execute_cmd
        self.notify_update_response = umgr.notify_update_response

    def refresh(self):
        # TODO: We should be able to determine if packages need to be
        # updated here
        pass

    async def check_initialized(self, timeout=None):
        pass

    async def update(self, *args):
        self.notify_update_response("Updating packages...")
        try:
            await self.execute_cmd(
                f"{APT_CMD} update", timeout=300., notify=True)
            await self.execute_cmd(
                f"{APT_CMD} upgrade --yes", timeout=3600., notify=True)
        except Exception:
            raise self.server.error("Error updating system packages")
        self.notify_update_response("Package update finished...",
                                    is_complete=True)

class ClientUpdater:
    def __init__(self, umgr, repo, path):
        self.server = umgr.server
        self.github_request = umgr.github_request
        self.notify_update_response = umgr.notify_update_response
        self.repo = repo.strip().strip("/")
        self.name = self.repo.split("/")[-1]
        self.path = path
        self.version = self.remote_version = self.dl_url = "?"
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
            to = IOLoop.current().time() + timeout
        await self.init_evt.wait(to)

    async def refresh(self):
        # Local state
        self._get_local_version()

        # Remote state
        url = f"https://api.github.com/repos/{self.repo}/releases/latest"
        try:
            result = await self.github_request(url)
        except Exception:
            logging.exception(f"Client {self.repo}: Github Request Error")
            result = {}
        self.remote_version = result.get('name', "?")
        release_assets = result.get('assets', [{}])[0]
        self.dl_url = release_assets.get('browser_download_url', "?")
        logging.info(
            f"Github client Info Received: {self.name}, "
            f"version: {self.remote_version} "
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
        archive = await self.github_request(self.dl_url, is_download=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(self.path)
        self.version = self.remote_version
        version_path = os.path.join(self.path, ".version")
        if not os.path.exists(version_path):
            with open(version_path, "w") as f:
                f.write(self.version)
        self.notify_update_response(f"Client Updated Finished: {self.name}",
                                    is_complete=True)

    def get_update_status(self):
        return {
            'name': self.name,
            'version': self.version,
            'remote_version': self.remote_version
        }

def load_plugin(config):
    return UpdateManager(config)
