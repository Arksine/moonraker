# Provides System Package Updates
#
# Copyright (C) 2023  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import logging
import time
import re
from ...thirdparty.packagekit import enums as PkEnum
from .base_deploy import BaseDeploy

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Optional,
    Union,
    Dict,
    List,
)

if TYPE_CHECKING:
    from ...confighelper import ConfigHelper
    from ..shell_command import ShellCommandFactory as SCMDComp
    from ..dbus_manager import DbusManager
    from ..machine import Machine
    from .update_manager import CommandHelper
    from dbus_fast import Variant
    from dbus_fast.aio import ProxyInterface
    JsonType = Union[List[Any], Dict[str, Any]]


class PackageDeploy(BaseDeploy):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, "system", "", "")
        self.cmd_helper.set_package_updater(self)
        self.use_packagekit = config.getboolean("enable_packagekit", True)
        self.available_packages: List[str] = []

    async def initialize(self) -> Dict[str, Any]:
        storage = await super().initialize()
        self.available_packages = storage.get('packages', [])
        provider: BasePackageProvider
        try_fallback = True
        if self.use_packagekit:
            try:
                provider = PackageKitProvider(self.cmd_helper)
                await provider.initialize()
            except Exception:
                pass
            else:
                self.log_info("PackageDeploy: PackageKit Provider Configured")
                self.prefix = "PackageKit: "
                try_fallback = False
        if try_fallback:
            # Check to see of the apt command is available
            fallback = await self._get_fallback_provider()
            if fallback is None:
                provider = BasePackageProvider(self.cmd_helper)
                machine: Machine = self.server.lookup_component("machine")
                dist_info = machine.get_system_info()['distribution']
                dist_id: str = dist_info['id'].lower()
                self.server.add_warning(
                    "Unable to initialize System Update Provider for "
                    f"distribution: {dist_id}")
            else:
                self.log_info("PackageDeploy: Using APT CLI Provider")
                self.prefix = "Package Manager APT: "
                provider = fallback
        self.provider = provider  # type: ignore
        return storage

    async def _get_fallback_provider(self) -> Optional[BasePackageProvider]:
        # Currently only the API Fallback provider is available
        shell_cmd: SCMDComp
        shell_cmd = self.server.lookup_component("shell_command")
        cmd = shell_cmd.build_shell_command("sh -c 'command -v apt'")
        try:
            ret = await cmd.run_with_response()
        except shell_cmd.error:
            return None
        # APT Command found should be available
        self.log_debug(f"APT package manager detected: {ret}")
        provider = AptCliProvider(self.cmd_helper)
        try:
            await provider.initialize()
        except Exception:
            return None
        return provider

    async def refresh(self) -> None:
        try:
            # Do not force a refresh until the server has started
            if self.server.is_running():
                await self._update_package_cache(force=True)
            self.available_packages = await self.provider.get_packages()
            pkg_msg = "\n".join(self.available_packages)
            self.log_info(
                f"Detected {len(self.available_packages)} package updates:"
                f"\n{pkg_msg}"
            )
        except Exception:
            self.log_exc("Error Refreshing System Packages")
        # Update Persistent Storage
        self._save_state()

    def get_persistent_data(self) -> Dict[str, Any]:
        storage = super().get_persistent_data()
        storage['packages'] = self.available_packages
        return storage

    async def update(self) -> bool:
        if not self.available_packages:
            return False
        self.cmd_helper.notify_update_response("Updating packages...")
        try:
            await self._update_package_cache(force=True, notify=True)
            await self.provider.upgrade_system()
        except Exception:
            raise self.server.error("Error updating system packages")
        self.available_packages = []
        self._save_state()
        self.cmd_helper.notify_update_response(
            "Package update finished...", is_complete=True)
        return True

    async def _update_package_cache(self,
                                    force: bool = False,
                                    notify: bool = False
                                    ) -> None:
        curtime = time.time()
        if force or curtime > self.last_refresh_time + 3600.:
            # Don't update if a request was done within the last hour
            await self.provider.refresh_packages(notify)

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        await self.provider.install_packages(package_list, **kwargs)

    def get_update_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "configured_type": "system",
            "package_count": len(self.available_packages),
            "package_list": self.available_packages
        }

class BasePackageProvider:
    def __init__(self, cmd_helper: CommandHelper) -> None:
        self.server = cmd_helper.get_server()
        self.cmd_helper = cmd_helper

    async def initialize(self) -> None:
        pass

    async def refresh_packages(self, notify: bool = False) -> None:
        raise self.server.error("Cannot refresh packages, no provider set")

    async def get_packages(self) -> List[str]:
        raise self.server.error("Cannot retrieve packages, no provider set")

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        raise self.server.error("Cannot install packages, no provider set")

    async def upgrade_system(self) -> None:
        raise self.server.error("Cannot upgrade packages, no provider set")

class AptCliProvider(BasePackageProvider):
    APT_CMD = "sudo DEBIAN_FRONTEND=noninteractive apt-get"

    async def refresh_packages(self, notify: bool = False) -> None:
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} update", timeout=600., notify=notify)

    async def get_packages(self) -> List[str]:
        shell_cmd = self.cmd_helper.get_shell_command()
        res = await shell_cmd.exec_cmd("apt list --upgradable", timeout=60.)
        pkg_list = [p.strip() for p in res.split("\n") if p.strip()]
        if pkg_list:
            pkg_list = pkg_list[2:]
            return [p.split("/", maxsplit=1)[0] for p in pkg_list]
        return []

    async def resolve_packages(self, package_list: List[str]) -> List[str]:
        self.cmd_helper.notify_update_response("Resolving packages...")
        search_regex = "|".join([f"^{pkg}$" for pkg in package_list])
        cmd = f"apt-cache search --names-only \"{search_regex}\""
        shell_cmd = self.cmd_helper.get_shell_command()
        ret = await shell_cmd.exec_cmd(cmd, timeout=600.)
        resolved = [
            pkg.strip().split()[0] for pkg in ret.split("\n") if pkg.strip()
        ]
        return [avail for avail in package_list if avail in resolved]

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        timeout: float = kwargs.get('timeout', 300.)
        retries: int = kwargs.get('retries', 3)
        notify: bool = kwargs.get('notify', False)
        await self.refresh_packages(notify=notify)
        resolved = await self.resolve_packages(package_list)
        if not resolved:
            self.cmd_helper.notify_update_response("No packages detected")
            return
        logging.debug(f"Resolved packages: {resolved}")
        pkgs = " ".join(resolved)
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} install --yes {pkgs}", timeout=timeout,
            attempts=retries, notify=notify)

    async def upgrade_system(self) -> None:
        await self.cmd_helper.run_cmd(
            f"{self.APT_CMD} upgrade --yes", timeout=3600.,
            notify=True)

class PackageKitProvider(BasePackageProvider):
    def __init__(self, cmd_helper: CommandHelper) -> None:
        super().__init__(cmd_helper)
        dbus_mgr: DbusManager = self.server.lookup_component("dbus_manager")
        self.dbus_mgr = dbus_mgr
        self.pkgkit: Optional[ProxyInterface] = None

    async def initialize(self) -> None:
        if not self.dbus_mgr.is_connected():
            raise self.server.error("DBus Connection Not available")
        # Check for PolicyKit permissions
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.system-sources-refresh",
            "The Update Manager will fail to fetch package updates")
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.package-install",
            "The Update Manager will fail to install packages")
        await self.dbus_mgr.check_permission(
            "org.freedesktop.packagekit.system-update",
            "The Update Manager will fail to update packages"
        )
        # Fetch the PackageKit DBus Interface
        self.pkgkit = await self.dbus_mgr.get_interface(
            "org.freedesktop.PackageKit",
            "/org/freedesktop/PackageKit",
            "org.freedesktop.PackageKit")

    async def refresh_packages(self, notify: bool = False) -> None:
        await self.run_transaction("refresh_cache", False, notify=notify)

    async def get_packages(self) -> List[str]:
        flags = PkEnum.Filter.NONE
        pkgs = await self.run_transaction("get_updates", flags.value)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        return [pkg_id.split(";")[0] for pkg_id in pkg_ids]

    async def install_packages(self,
                               package_list: List[str],
                               **kwargs
                               ) -> None:
        notify: bool = kwargs.get('notify', False)
        await self.refresh_packages(notify=notify)
        flags = (
            PkEnum.Filter.NEWEST | PkEnum.Filter.NOT_INSTALLED |
            PkEnum.Filter.BASENAME | PkEnum.Filter.ARCH
        )
        pkgs = await self.run_transaction("resolve", flags.value, package_list)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        if pkg_ids:
            logging.debug(f"Installing Packages: {pkg_ids}")
            tflag = PkEnum.TransactionFlag.ONLY_TRUSTED
            await self.run_transaction("install_packages", tflag.value,
                                       pkg_ids, notify=notify)

    async def upgrade_system(self) -> None:
        # Get Updates, Install Packages
        flags = PkEnum.Filter.NONE
        pkgs = await self.run_transaction("get_updates", flags.value)
        pkg_ids = [info['package_id'] for info in pkgs if 'package_id' in info]
        if pkg_ids:
            logging.debug(f"Upgrading Packages: {pkg_ids}")
            tflag = PkEnum.TransactionFlag.ONLY_TRUSTED
            await self.run_transaction("update_packages", tflag.value,
                                       pkg_ids, notify=True)

    def create_transaction(self) -> PackageKitTransaction:
        if self.pkgkit is None:
            raise self.server.error("PackageKit Interface Not Available")
        return PackageKitTransaction(self.dbus_mgr, self.pkgkit,
                                     self.cmd_helper)

    async def run_transaction(self,
                              method: str,
                              *args,
                              notify: bool = False
                              ) -> Any:
        transaction = self.create_transaction()
        return await transaction.run(method, *args, notify=notify)

class PackageKitTransaction:
    GET_PKG_ROLES = (
        PkEnum.Role.RESOLVE | PkEnum.Role.GET_PACKAGES |
        PkEnum.Role.GET_UPDATES
    )
    QUERY_ROLES = GET_PKG_ROLES | PkEnum.Role.GET_REPO_LIST
    PROGRESS_STATUS = (
        PkEnum.Status.RUNNING | PkEnum.Status.INSTALL |
        PkEnum.Status.UPDATE
    )

    def __init__(self,
                 dbus_mgr: DbusManager,
                 pkgkit: ProxyInterface,
                 cmd_helper: CommandHelper
                 ) -> None:
        self.server = cmd_helper.get_server()
        self.eventloop = self.server.get_event_loop()
        self.cmd_helper = cmd_helper
        self.dbus_mgr = dbus_mgr
        self.pkgkit = pkgkit
        # Transaction Properties
        self.notify = False
        self._status = PkEnum.Status.UNKNOWN
        self._role = PkEnum.Role.UNKNOWN
        self._tflags = PkEnum.TransactionFlag.NONE
        self._percentage = 101
        self._dl_remaining = 0
        self.speed = 0
        self.elapsed_time = 0
        self.remaining_time = 0
        self.caller_active = False
        self.allow_cancel = True
        self.uid = 0
        # Transaction data tracking
        self.tfut: Optional[asyncio.Future] = None
        self.last_progress_notify_time: float = 0.
        self.result: List[Dict[str, Any]] = []
        self.err_msg: str = ""

    def run(self,
            method: str,
            *args,
            notify: bool = False
            ) -> Awaitable:
        if self.tfut is not None:
            raise self.server.error(
                "PackageKit transaction can only be used once")
        self.notify = notify
        self.tfut = self.eventloop.create_future()
        coro = self._start_transaction(method, *args)
        self.eventloop.create_task(coro)
        return self.tfut

    async def _start_transaction(self,
                                 method: str,
                                 *args
                                 ) -> None:
        assert self.tfut is not None
        try:
            # Create Transaction
            tid = await self.pkgkit.call_create_transaction()  # type: ignore
            transaction, props = await self.dbus_mgr.get_interfaces(
                "org.freedesktop.PackageKit", tid,
                ["org.freedesktop.PackageKit.Transaction",
                 "org.freedesktop.DBus.Properties"])
            # Set interface callbacks
            transaction.on_package(self._on_package_signal)    # type: ignore
            transaction.on_repo_detail(                        # type: ignore
                self._on_repo_detail_signal)
            transaction.on_item_progress(                      # type: ignore
                self._on_item_progress_signal)
            transaction.on_error_code(self._on_error_signal)   # type: ignore
            transaction.on_finished(self._on_finished_signal)  # type: ignore
            props.on_properties_changed(                       # type: ignore
                self._on_properties_changed)
            # Run method
            logging.debug(f"PackageKit: Running transaction call_{method}")
            func = getattr(transaction, f"call_{method}")
            await func(*args)
        except Exception as e:
            self.tfut.set_exception(e)

    def _on_package_signal(self,
                           info_code: int,
                           package_id: str,
                           summary: str
                           ) -> None:
        info = PkEnum.Info.from_index(info_code & 0xFFFF)
        severity = PkEnum.Info.from_index((info_code >> 16) & 0xFFFF)
        if info == PkEnum.Info.UNKNOWN:
            info = severity
        if self._role in self.GET_PKG_ROLES:
            pkg_data = {
                'package_id': package_id,
                'info': info.desc,
                'summary': summary
            }
            self.result.append(pkg_data)
        else:
            self._notify_package(info, package_id)

    def _on_repo_detail_signal(self,
                               repo_id: str,
                               description: str,
                               enabled: bool
                               ) -> None:
        if self._role == PkEnum.Role.GET_REPO_LIST:
            repo_data = {
                "repo_id": repo_id,
                "description": description,
                "enabled": enabled
            }
            self.result.append(repo_data)
        else:
            self._notify_repo(repo_id, description)

    def _on_item_progress_signal(self,
                                 item_id: str,
                                 status_code: int,
                                 percent_complete: int
                                 ) -> None:
        status = PkEnum.Status.from_index(status_code)  # noqa: F841
        # NOTE: This signal doesn't seem to fire predictably,
        # nor does it seem to provide a consistent "percent complete"
        # parameter.
        # logging.debug(
        #    f"Role {self._role.name}: Item Progress Signal Received\n"
        #    f"Item ID: {item_id}\n"
        #    f"Percent Complete: {percent_complete}\n"
        #    f"Status: {status.desc}")

    def _on_error_signal(self,
                         error_code: int,
                         details: str
                         ) -> None:
        err = PkEnum.Error.from_index(error_code)
        self.err_msg = f"{err.name}: {details}"

    def _on_finished_signal(self, exit_code: int, run_time: int) -> None:
        if self.tfut is None:
            return
        ext = PkEnum.Exit.from_index(exit_code)
        secs = run_time / 1000.
        if ext == PkEnum.Exit.SUCCESS:
            self.tfut.set_result(self.result)
        else:
            err = self.err_msg or ext.desc
            server = self.cmd_helper.get_server()
            self.tfut.set_exception(server.error(err))
        msg = f"Transaction {self._role.desc}: Exit {ext.desc}, " \
              f"Run time: {secs:.2f} seconds"
        if self.notify:
            self.cmd_helper.notify_update_response(msg)
        logging.debug(msg)

    def _on_properties_changed(self,
                               iface_name: str,
                               changed_props: Dict[str, Variant],
                               invalid_props: Dict[str, Variant]
                               ) -> None:
        for name, var in changed_props.items():
            formatted = re.sub(r"(\w)([A-Z])", r"\g<1>_\g<2>", name).lower()
            setattr(self, formatted, var.value)

    def _notify_package(self, info: PkEnum.Info, package_id: str) -> None:
        if self.notify:
            if info == PkEnum.Info.FINISHED:
                return
            pkg_parts = package_id.split(";")
            msg = f"{info.desc}: {pkg_parts[0]} ({pkg_parts[1]})"
            self.cmd_helper.notify_update_response(msg)

    def _notify_repo(self, repo_id: str, description: str) -> None:
        if self.notify:
            if not repo_id.strip():
                repo_id = description
            # TODO: May want to eliminate dups
            msg = f"GET: {repo_id}"
            self.cmd_helper.notify_update_response(msg)

    def _notify_progress(self) -> None:
        if self.notify and self._percentage <= 100:
            msg = f"{self._status.desc}...{self._percentage}%"
            if self._status == PkEnum.Status.DOWNLOAD and self._dl_remaining:
                if self._dl_remaining < 1024:
                    msg += f", Remaining: {self._dl_remaining} B"
                elif self._dl_remaining < 1048576:
                    msg += f", Remaining: {self._dl_remaining // 1024} KiB"
                else:
                    msg += f", Remaining: {self._dl_remaining // 1048576} MiB"
                if self.speed:
                    speed = self.speed // 8
                    if speed < 1024:
                        msg += f", Speed: {speed} B/s"
                    elif speed < 1048576:
                        msg += f", Speed: {speed // 1024} KiB/s"
                    else:
                        msg += f", Speed: {speed // 1048576} MiB/s"
            self.cmd_helper.notify_update_response(msg)

    @property
    def role(self) -> PkEnum.Role:
        return self._role

    @role.setter
    def role(self, role_code: int) -> None:
        self._role = PkEnum.Role.from_index(role_code)
        if self._role in self.QUERY_ROLES:
            # Never Notify Queries
            self.notify = False
        if self.notify:
            msg = f"Transaction {self._role.desc} started..."
            self.cmd_helper.notify_update_response(msg)
        logging.debug(f"PackageKit: Current Role: {self._role.desc}")

    @property
    def status(self) -> PkEnum.Status:
        return self._status

    @status.setter
    def status(self, status_code: int) -> None:
        self._status = PkEnum.Status.from_index(status_code)
        self._percentage = 101
        self.speed = 0
        logging.debug(f"PackageKit: Current Status: {self._status.desc}")

    @property
    def transaction_flags(self) -> PkEnum.TransactionFlag:
        return self._tflags

    @transaction_flags.setter
    def transaction_flags(self, bits: int) -> None:
        self._tflags = PkEnum.TransactionFlag(bits)

    @property
    def percentage(self) -> int:
        return self._percentage

    @percentage.setter
    def percentage(self, percent: int) -> None:
        self._percentage = percent
        if self._status in self.PROGRESS_STATUS:
            self._notify_progress()

    @property
    def download_size_remaining(self) -> int:
        return self._dl_remaining

    @download_size_remaining.setter
    def download_size_remaining(self, bytes_remaining: int) -> None:
        self._dl_remaining = bytes_remaining
        self._notify_progress()
