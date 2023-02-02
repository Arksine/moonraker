# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import os
import sys
import pathlib
import shutil
import logging
import json
import tempfile
import asyncio
import zipfile
import time
from copy import deepcopy
from inotify_simple import INotify
from inotify_simple import flags as iFlags
from utils import MOONRAKER_PATH

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
    Set,
    Coroutine,
    Callable,
    TypeVar,
    cast,
)

if TYPE_CHECKING:
    from inotify_simple import Event as InotifyEvent
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from klippy_connection import KlippyConnection
    from components import database
    from components import klippy_apis
    from components import shell_command
    from components.job_queue import JobQueue
    from components.job_state import JobState
    StrOrPath = Union[str, pathlib.Path]
    DBComp = database.MoonrakerDatabase
    APIComp = klippy_apis.KlippyAPI
    SCMDComp = shell_command.ShellCommandFactory
    _T = TypeVar("_T")

VALID_GCODE_EXTS = ['.gcode', '.g', '.gco', '.ufp', '.nc']
METADATA_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "metadata.py"))
WATCH_FLAGS = iFlags.CREATE | iFlags.DELETE | iFlags.MODIFY \
    | iFlags.MOVED_TO | iFlags.MOVED_FROM | iFlags.ONLYDIR \
    | iFlags.CLOSE_WRITE

class FileManager:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.reserved_paths: Dict[str, Tuple[pathlib.Path, bool]] = {}
        self.full_access_roots: Set[str] = set()
        self.file_paths: Dict[str, str] = {}
        app_args = self.server.get_app_args()
        self.datapath = pathlib.Path(app_args["data_path"])
        self.add_reserved_path("moonraker", MOONRAKER_PATH, False)
        db: DBComp = self.server.load_component(config, "database")
        db_path = db.get_database_path()
        self.add_reserved_path("database", db_path, False)
        self.add_reserved_path("certs", self.datapath.joinpath("certs"), False)
        self.add_reserved_path(
            "systemd", self.datapath.joinpath("systemd"), False
        )
        self.gcode_metadata = MetadataStorage(config, db)
        self.inotify_handler = INotifyHandler(config, self,
                                              self.gcode_metadata)
        self.write_mutex = asyncio.Lock()
        self.notify_sync_lock: Optional[NotifySyncLock] = None
        self.fixed_path_args: Dict[str, Any] = {}
        self.queue_gcodes: bool = config.getboolean('queue_gcode_uploads',
                                                    False)

        # Register file management endpoints
        self.server.register_endpoint(
            "/server/files/list", ['GET'], self._handle_filelist_request)
        self.server.register_endpoint(
            "/server/files/metadata", ['GET'], self._handle_metadata_request)
        self.server.register_endpoint(
            "/server/files/thumbnails", ['GET'], self._handle_list_thumbs)
        self.server.register_endpoint(
            "/server/files/roots", ['GET'], self._handle_list_roots)
        self.server.register_endpoint(
            "/server/files/directory", ['GET', 'POST', 'DELETE'],
            self._handle_directory_request)
        self.server.register_endpoint(
            "/server/files/move", ['POST'], self._handle_file_move_copy)
        self.server.register_endpoint(
            "/server/files/copy", ['POST'], self._handle_file_move_copy)
        self.server.register_endpoint(
            "/server/files/zip", ['POST'], self._handle_zip_files)
        self.server.register_endpoint(
            "/server/files/delete_file", ['DELETE'], self._handle_file_delete,
            transports=["websocket"])
        # register client notificaitons
        self.server.register_notification("file_manager:filelist_changed")

        self.server.register_event_handler(
            "server:klippy_identified", self._update_fixed_paths)

        # Register Data Folders
        config.get('config_path', None, deprecate=True)
        self.register_data_folder("config", full_access=True)

        config.get('log_path', None, deprecate=True)
        self.register_data_folder("logs")
        gc_path = self.register_data_folder("gcodes", full_access=True)
        if gc_path.is_dir():
            prune: bool = True
            saved_gc_dir: str = db.get_item(
                "moonraker", "file_manager.gcode_path", ""
            ).result()
            is_empty = next(gc_path.iterdir(), None) is None
            if is_empty and saved_gc_dir:
                saved_path = pathlib.Path(saved_gc_dir)
                if (
                    saved_path.is_dir() and
                    next(saved_path.iterdir(), None) is not None
                ):
                    logging.info(
                        f"Legacy GCode Path found at '{saved_path}', "
                        "aborting metadata prune"
                    )
                    prune = False
            if prune:
                self.gcode_metadata.prune_storage()

    async def component_init(self):
        self.inotify_handler.initalize_roots()

    def _update_fixed_paths(self) -> None:
        kinfo = self.server.get_klippy_info()
        paths: Dict[str, Any] = \
            {k: kinfo.get(k) for k in
             ['klipper_path', 'python_path',
              'log_file', 'config_file']}
        if paths == self.fixed_path_args:
            # No change in fixed paths
            return
        self.fixed_path_args = paths
        str_paths = "\n".join([f"{k}: {v}" for k, v in paths.items()])
        logging.debug(f"\nUpdating Fixed Paths:\n{str_paths}")

        # Register path for example configs
        klipper_path = paths.get('klipper_path', None)
        if klipper_path is not None:
            self.reserved_paths.pop("klipper", None)
            self.add_reserved_path("klipper", klipper_path)
            example_cfg_path = os.path.join(klipper_path, "config")
            self.register_directory("config_examples", example_cfg_path)
            docs_path = os.path.join(klipper_path, "docs")
            self.register_directory("docs", docs_path)

        # Register log path
        log_file = paths.get('log_file')
        if log_file is not None:
            log_path: str = os.path.abspath(os.path.expanduser(log_file))
            self.server.register_static_file_handler(
                "klippy.log", log_path, force=True)

        # Validate config file
        cfg_file: Optional[str] = paths.get("config_file")
        cfg_parent = self.file_paths.get("config")
        if cfg_file is not None and cfg_parent is not None:
            cfg_path = pathlib.Path(cfg_file).resolve()
            par_path = pathlib.Path(cfg_parent).resolve()
            if par_path not in cfg_path.parents:
                self.server.add_warning(
                    "file_manager: Klipper configuration file not located in "
                    "'config' folder.\n\n"
                    f"Klipper Config Path: {cfg_path}\n\n"
                    f"Config Folder: {par_path}",
                    warn_id="klipper_config"
                )
            else:
                self.server.remove_warning("klipper_config")

    def validate_gcode_path(self, gc_path: str) -> None:
        gc_dir = pathlib.Path(gc_path).expanduser()
        if "gcodes" in self.file_paths:
            expected = self.file_paths["gcodes"]
            if not gc_dir.exists() or not gc_dir.samefile(expected):
                self.server.add_warning(
                    "GCode path received from Klipper does not match expected "
                    "location.\n\n"
                    f"Received: '{gc_dir}'\nExpected: '{expected}'\n\n"
                    "Modify the [virtual_sdcard] section Klipper's "
                    "configuration to correct this error.\n\n"
                    f"[virtual_sdcard]\npath: {expected}",
                    warn_id="gcode_path"
                )
            else:
                self.server.remove_warning("gcode_path")

    def register_data_folder(
        self, folder_name: str, full_access: bool = False
    ) -> pathlib.Path:
        new_path = self.datapath.joinpath(folder_name)
        if not new_path.exists():
            try:
                new_path.mkdir()
            except Exception:
                pass
        self.register_directory(folder_name, str(new_path), full_access)
        return new_path

    def disable_write_access(self):
        self.full_access_roots.clear()

    def check_write_enabled(self):
        if not self.full_access_roots:
            raise self.server.error(
                "Write access is currently disabled.  Check notifications "
                "for warnings."
            )

    def register_directory(self,
                           root: str,
                           path: Optional[str],
                           full_access: bool = False
                           ) -> bool:
        if path is None:
            return False
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.islink(path):
            path = os.path.realpath(path)
        if not os.path.isdir(path) or path == "/":
            self.server.add_warning(
                f"Supplied path ({path}) for ({root}) is invalid. Make sure\n"
                "that the path exists and is not the file system root.")
            return False
        permissions = os.R_OK
        if full_access:
            permissions |= os.W_OK
            self.full_access_roots.add(root)
        if not os.access(path, permissions):
            self.server.add_warning(
                f"Moonraker does not have permission to access path "
                f"({path}) for ({root}).")
            return False
        if path != self.file_paths.get(root, ""):
            self.file_paths[root] = path
            self.server.register_static_file_handler(root, path)
            if root == "gcodes":
                # scan for metadata changes
                self.gcode_metadata.update_gcode_path(path)
            if full_access:
                # Refresh the file list and add watches
                self.inotify_handler.add_root_watch(root, path)
            elif self.server.is_running():
                self.event_loop.register_callback(
                    self.inotify_handler.notify_filelist_changed,
                    "root_update", root, path)
        return True

    def check_reserved_path(
        self,
        req_path: StrOrPath,
        need_write: bool,
        raise_error: bool = True
    ) -> bool:
        if isinstance(req_path, str):
            req_path = pathlib.Path(req_path)
        req_path = req_path.expanduser().resolve()
        if ".git" in req_path.parts:
            if raise_error:
                raise self.server.error(
                    "Access to .git folders is forbidden", 403
                )
            return True
        for name, (res_path, can_read) in self.reserved_paths.items():
            if (
                (res_path == req_path or res_path in req_path.parents) and
                (need_write or not can_read)
            ):
                if not raise_error:
                    return True
                raise self.server.error(
                    f"Access to file {req_path.name} forbidden by reserved "
                    f"path '{name}'", 403
                )
        return False

    def add_reserved_path(
        self, name: str, res_path: StrOrPath, read_access: bool = True
    ) -> bool:
        if name in self.reserved_paths:
            return False
        if isinstance(res_path, str):
            res_path = pathlib.Path(res_path)
        res_path = res_path.expanduser().resolve()
        self.reserved_paths[name] = (res_path, read_access)
        return True

    def get_directory(self, root: str = "gcodes") -> str:
        return self.file_paths.get(root, "")

    def get_registered_dirs(self) -> List[str]:
        return list(self.file_paths.keys())

    def get_fixed_path_args(self) -> Dict[str, Any]:
        return dict(self.fixed_path_args)

    def get_relative_path(self, root: str, full_path: str) -> str:
        root_dir = self.file_paths.get(root, None)
        if root_dir is None or not full_path.startswith(root_dir):
            return ""
        return os.path.relpath(full_path, start=root_dir)

    def get_metadata_storage(self) -> MetadataStorage:
        return self.gcode_metadata

    def check_file_exists(self, root: str, filename: str) -> bool:
        root_dir = self.file_paths.get(root, "")
        file_path = os.path.join(root_dir, filename)
        return os.path.exists(file_path)

    def can_access_path(self, path: StrOrPath) -> bool:
        if isinstance(path, str):
            path = pathlib.Path(path)
        path = path.expanduser().resolve()
        for registered in self.file_paths.values():
            reg_root_path = pathlib.Path(registered).resolve()
            if reg_root_path in path.parents:
                return not self.check_reserved_path(path, False, False)
        return False

    def upload_queue_enabled(self) -> bool:
        return self.queue_gcodes

    def sync_inotify_event(self, path: str) -> Optional[NotifySyncLock]:
        if self.notify_sync_lock is None or \
                not self.notify_sync_lock.check_need_sync(path):
            return None
        return self.notify_sync_lock

    async def _handle_filelist_request(self,
                                       web_request: WebRequest
                                       ) -> List[Dict[str, Any]]:
        root = web_request.get_str('root', "gcodes")
        flist = self.get_file_list(root, list_format=True)
        return cast(List[Dict[str, Any]], flist)

    async def _handle_metadata_request(self,
                                       web_request: WebRequest
                                       ) -> Dict[str, Any]:
        requested_file: str = web_request.get_str('filename')
        metadata: Optional[Dict[str, Any]]
        metadata = self.gcode_metadata.get(requested_file, None)
        if metadata is None:
            raise self.server.error(
                f"Metadata not available for <{requested_file}>", 404)
        metadata['filename'] = requested_file
        return metadata

    async def _handle_list_roots(
        self, web_request: WebRequest
    ) -> List[Dict[str, Any]]:
        root_list: List[Dict[str, Any]] = []
        for name, path in self.file_paths.items():
            perms = "rw" if name in self.full_access_roots else "r"
            root_list.append({
                "name": name,
                "path": path,
                "permissions": perms
            })
        return root_list

    async def _handle_list_thumbs(
        self, web_request: WebRequest
    ) -> List[Dict[str, Any]]:
        requested_file: str = web_request.get_str("filename")
        metadata: Optional[Dict[str, Any]]
        metadata = self.gcode_metadata.get(requested_file, None)
        if metadata is None:
            return []
        if "thumbnails" not in metadata:
            return []
        thumblist: List[Dict[str, Any]] = metadata["thumbnails"]
        for info in thumblist:
            relpath: Optional[str] = info.pop("relative_path", None)
            if relpath is None:
                continue
            thumbpath = pathlib.Path(requested_file).parent.joinpath(relpath)
            info["thumbnail_path"] = str(thumbpath)
        return thumblist

    async def _handle_directory_request(self,
                                        web_request: WebRequest
                                        ) -> Dict[str, Any]:
        directory = web_request.get_str('path', "gcodes")
        root, dir_path = self._convert_request_path(directory)
        action = web_request.get_action()
        if action == 'GET':
            is_extended = web_request.get_boolean('extended', False)
            # Get list of files and subdirectories for this target
            dir_info = self._list_directory(dir_path, root, is_extended)
            return dir_info
        async with self.write_mutex:
            self.check_reserved_path(dir_path, True)
            result = {
                'item': {'path': directory, 'root': root},
                'action': "create_dir"}
            if action == 'POST' and root in self.full_access_roots:
                # Create a new directory
                try:
                    os.mkdir(dir_path)
                except Exception as e:
                    raise self.server.error(str(e))
            elif action == 'DELETE' and root in self.full_access_roots:
                # Remove a directory
                result['action'] = "delete_dir"
                if directory.strip("/") == root:
                    raise self.server.error(
                        "Cannot delete root directory")
                if not os.path.isdir(dir_path):
                    raise self.server.error(
                        f"Directory does not exist ({directory})")
                force = web_request.get_boolean('force', False)
                if force:
                    # Make sure that the directory does not contain a file
                    # loaded by the virtual_sdcard
                    self._handle_operation_check(dir_path)
                    self.notify_sync_lock = NotifySyncLock(dir_path)
                    try:
                        await self.event_loop.run_in_thread(
                            shutil.rmtree, dir_path)
                    except Exception:
                        self.notify_sync_lock.cancel()
                        self.notify_sync_lock = None
                        raise
                    await self.notify_sync_lock.wait(30.)
                    self.notify_sync_lock = None
                else:
                    try:
                        os.rmdir(dir_path)
                    except Exception as e:
                        raise self.server.error(str(e))
            else:
                raise self.server.error("Operation Not Supported", 405)
        return result

    def _handle_operation_check(self, requested_path: str) -> bool:
        if not self.get_relative_path("gcodes", requested_path):
            # Path not in the gcodes path
            return True
        kconn: KlippyConnection
        kconn = self.server.lookup_component("klippy_connection")
        job_state: JobState = self.server.lookup_component("job_state")
        last_stats = job_state.get_last_stats()
        loaded_file: str = last_stats.get('filename', "")
        state: str = last_stats.get('state', "")
        gc_path = self.file_paths.get('gcodes', "")
        full_path = os.path.join(gc_path, loaded_file)
        is_printing = kconn.is_ready() and state in ["printing", "paused"]
        if loaded_file and is_printing:
            if os.path.isdir(requested_path):
                # Check to see of the loaded file is in the request
                if full_path.startswith(requested_path):
                    raise self.server.error("File currently in use", 403)
            elif full_path == requested_path:
                raise self.server.error("File currently in use", 403)
        return not is_printing

    def _convert_request_path(self, request_path: str) -> Tuple[str, str]:
        # Parse the root, relative path, and disk path from a remote request
        parts = os.path.normpath(request_path).strip("/").split("/", 1)
        if not parts:
            raise self.server.error(f"Invalid path: {request_path}")
        root = parts[0]
        if root not in self.file_paths:
            raise self.server.error(f"Invalid root path ({root})")
        root_path = dest_path = self.file_paths[root]
        if len(parts) > 1:
            dest_path = os.path.abspath(os.path.join(dest_path, parts[1]))
            if not dest_path.startswith(root_path):
                raise self.server.error(
                    f"Invalid path request, '{request_path}'' is outside "
                    f"root '{root}'")
        return root, dest_path

    async def _handle_file_move_copy(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, Any]:
        source: str = web_request.get_str("source")
        destination: str = web_request.get_str("dest")
        ep = web_request.get_endpoint()
        source_root, source_path = self._convert_request_path(source)
        dest_root, dest_path = self._convert_request_path(destination)
        if dest_root not in self.full_access_roots:
            raise self.server.error(
                f"Destination path is read-only: {dest_root}")
        self.check_reserved_path(source_path, False)
        self.check_reserved_path(dest_path, True)
        async with self.write_mutex:
            result: Dict[str, Any] = {'item': {'root': dest_root}}
            if not os.path.exists(source_path):
                raise self.server.error(f"File {source_path} does not exist")
            # make sure the destination is not in use
            if os.path.exists(dest_path):
                self._handle_operation_check(dest_path)
            if ep == "/server/files/move":
                if source_root not in self.full_access_roots:
                    raise self.server.error(
                        f"Source path is read-only, cannot move: {source_root}")
                # if moving the file, make sure the source is not in use
                self._handle_operation_check(source_path)
                op_func: Callable[..., str] = shutil.move
                result['source_item'] = {
                    'path': source,
                    'root': source_root
                }
                result['action'] = "move_dir" if os.path.isdir(source_path) \
                    else "move_file"
            elif ep == "/server/files/copy":
                if os.path.isdir(source_path):
                    result['action'] = "create_dir"
                    op_func = shutil.copytree
                else:
                    result['action'] = "create_file"
                    op_func = shutil.copy2
            self.notify_sync_lock = NotifySyncLock(dest_path)
            try:
                full_dest = await self.event_loop.run_in_thread(
                    op_func, source_path, dest_path)
            except Exception as e:
                self.notify_sync_lock.cancel()
                self.notify_sync_lock = None
                raise self.server.error(str(e))
            self.notify_sync_lock.update_dest(full_dest)
            await self.notify_sync_lock.wait(600.)
            self.notify_sync_lock = None
        result['item']['path'] = self.get_relative_path(dest_root, full_dest)
        return result

    async def _handle_zip_files(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        async with self.write_mutex:
            store_only = web_request.get_boolean("store_only", False)
            suffix = time.strftime("%Y%m%d-%H%M%S", time.localtime())
            dest: str = web_request.get_str(
                "dest", f"config/collection-{suffix}.zip"
            )
            dest_root, dest_str_path = self._convert_request_path(dest)
            if dest_root not in self.full_access_roots:
                raise self.server.error(
                    f"Destination Root '{dest_root}' is read-only"
                )
            dest_path = pathlib.Path(dest_str_path)
            self.check_reserved_path(dest_path, True)
            if dest_path.is_dir():
                raise self.server.error(
                    f"Cannot create archive at '{dest_path}'.  Path exists "
                    "as a directory."
                )
            elif not dest_path.parent.exists():
                raise self.server.error(
                    f"Cannot create archive at '{dest_path}'.  Parent "
                    "directory does not exist."
                )
            items: Union[str, List[str]] = web_request.get("items")
            if isinstance(items, str):
                items = [
                    item.strip() for item in items.split(",") if item.strip()
                ]
            if not items:
                raise self.server.error(
                    "At least one file or directory must be specified"
                )
            await self.event_loop.run_in_thread(
                self._zip_files, items, dest_path, store_only
            )
        rel_dest = dest_path.relative_to(self.file_paths[dest_root])
        return {
            "destination": {"root": dest_root, "path": str(rel_dest)},
            "action": "zip_files"
        }

    def _zip_files(
        self,
        item_list: List[str],
        destination: StrOrPath,
        store_only: bool = False
    ) -> None:
        if isinstance(destination, str):
            destination = pathlib.Path(destination).expanduser().resolve()
        tmpdir = pathlib.Path(tempfile.gettempdir())
        temp_dest = tmpdir.joinpath(destination.name)
        processed: Set[Tuple[int, int]] = set()
        cptype = zipfile.ZIP_STORED if store_only else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(str(temp_dest), "w", compression=cptype) as zf:
            for item in item_list:
                root, str_path = self._convert_request_path(item)
                root_path = pathlib.Path(self.file_paths[root])
                item_path = pathlib.Path(str_path)
                self.check_reserved_path(item_path, False)
                if not item_path.exists():
                    raise self.server.error(
                        f"No file/directory exits at '{item}'"
                    )
                if item_path.is_file():
                    st = item_path.stat()
                    ident = (st.st_dev, st.st_ino)
                    if ident in processed:
                        continue
                    processed.add(ident)
                    rel_path = item_path.relative_to(root_path.parent)
                    zf.write(str(item_path), arcname=str(rel_path))
                    continue
                elif not item_path.is_dir():
                    raise self.server.error(
                        f"Item at path '{item}' is not a valid file or "
                        "directory"
                    )
                for child_path in item_path.iterdir():
                    if child_path.is_file():
                        if self.check_reserved_path(child_path, False, False):
                            continue
                        st = child_path.stat()
                        ident = (st.st_dev, st.st_ino)
                        if ident in processed:
                            continue
                        processed.add(ident)
                        rel_path = child_path.relative_to(root_path.parent)
                        try:
                            zf.write(str(child_path), arcname=str(rel_path))
                        except PermissionError:
                            continue
        shutil.move(str(temp_dest), str(destination))

    def _list_directory(self,
                        path: str,
                        root: str,
                        is_extended: bool = False
                        ) -> Dict[str, Any]:
        if not os.path.isdir(path):
            raise self.server.error(
                f"Directory does not exist ({path})")
        self.check_reserved_path(path, False)
        flist: Dict[str, Any] = {'dirs': [], 'files': []}
        for fname in os.listdir(path):
            full_path = os.path.join(path, fname)
            if not os.path.exists(full_path):
                continue
            path_info = self.get_path_info(full_path, root)
            if os.path.isdir(full_path):
                path_info['dirname'] = fname
                flist['dirs'].append(path_info)
            elif os.path.isfile(full_path):
                path_info['filename'] = fname
                # Check to see if a filelist update is necessary
                ext = os.path.splitext(fname)[-1].lower()
                if (
                    root == "gcodes" and
                    ext in VALID_GCODE_EXTS and
                    is_extended
                ):
                    rel_path = self.get_relative_path(root, full_path)
                    metadata: Dict[str, Any] = self.gcode_metadata.get(
                        rel_path, {})
                    path_info.update(metadata)
                flist['files'].append(path_info)
        usage = shutil.disk_usage(path)
        flist['disk_usage'] = usage._asdict()
        flist['root_info'] = {
            'name': root,
            'permissions': "rw" if root in self.full_access_roots else "r"
        }
        return flist

    def get_path_info(self, path: StrOrPath, root: str) -> Dict[str, Any]:
        if isinstance(path, str):
            path = pathlib.Path(path)
        real_path = path.resolve()
        fstat = path.stat()
        if ".git" in real_path.parts:
            permissions = ""
        else:
            permissions = "rw"
            if (
                root not in self.full_access_roots or
                (path.is_symlink() and path.is_file())
            ):
                permissions = "r"
            for name, (res_path, can_read) in self.reserved_paths.items():
                if (res_path == real_path or res_path in real_path.parents):
                    if not can_read:
                        permissions = ""
                        break
                    permissions = "r"
        return {
            'modified': fstat.st_mtime,
            'size': fstat.st_size,
            'permissions': permissions
        }

    def gen_temp_upload_path(self) -> str:
        loop_time = int(self.event_loop.get_loop_time())
        return os.path.join(
            tempfile.gettempdir(),
            f"moonraker.upload-{loop_time}.mru")

    async def finalize_upload(self,
                              form_args: Dict[str, Any]
                              ) -> Dict[str, Any]:
        # lookup root file path
        async with self.write_mutex:
            try:
                upload_info = self._parse_upload_args(form_args)
                self.check_reserved_path(upload_info["dest_path"], True)
                root = upload_info['root']
                if root not in self.full_access_roots:
                    raise self.server.error(f"Invalid root request: {root}")
                if root == "gcodes" and upload_info['ext'] in VALID_GCODE_EXTS:
                    result = await self._finish_gcode_upload(upload_info)
                else:
                    result = await self._finish_standard_upload(upload_info)
            except Exception:
                try:
                    os.remove(form_args['tmp_file_path'])
                except Exception:
                    pass
                raise
        return result

    def _parse_upload_args(self,
                           upload_args: Dict[str, Any]
                           ) -> Dict[str, Any]:
        if 'filename' not in upload_args:
            raise self.server.error(
                "No file name specifed in upload form")
        # check relative path
        root: str = upload_args.get('root', "gcodes").lower()
        if root not in self.file_paths:
            raise self.server.error(f"Root {root} not available")
        root_path = self.file_paths[root]
        dir_path: str = upload_args.get('path', "").lstrip("/")
        if os.path.isfile(root_path):
            filename: str = os.path.basename(root_path)
            dest_path = root_path
            dir_path = ""
        else:
            filename = upload_args['filename'].strip().lstrip("/")
            if dir_path:
                filename = os.path.join(dir_path, filename)
            dest_path = os.path.abspath(os.path.join(root_path, filename))
        # Validate the path.  Don't allow uploads to a parent of the root
        if not dest_path.startswith(root_path):
            raise self.server.error(
                f"Cannot write to path: {dest_path}")
        start_print: bool = upload_args.get('print', "false") == "true"
        f_ext = os.path.splitext(dest_path)[-1].lower()
        unzip_ufp = f_ext == ".ufp" and root == "gcodes"
        if unzip_ufp:
            filename = os.path.splitext(filename)[0] + ".gcode"
            dest_path = os.path.splitext(dest_path)[0] + ".gcode"
        if os.path.islink(dest_path):
            raise self.server.error(f"Cannot overwrite symlink: {dest_path}")
        if os.path.isfile(dest_path) and not os.access(dest_path, os.W_OK):
            raise self.server.error(f"File is read-only: {dest_path}")
        return {
            'root': root,
            'filename': filename,
            'dir_path': dir_path,
            'dest_path': dest_path,
            'tmp_file_path': upload_args['tmp_file_path'],
            'start_print': start_print,
            'unzip_ufp': unzip_ufp,
            'ext': f_ext
        }

    async def _finish_gcode_upload(self,
                                   upload_info: Dict[str, Any]
                                   ) -> Dict[str, Any]:
        # Verify that the operation can be done if attempting to upload a gcode
        can_start: bool = False
        try:
            check_path: str = upload_info['dest_path']
            can_start = self._handle_operation_check(check_path)
        except self.server.error as e:
            if e.status_code == 403:
                raise self.server.error(
                    "File is loaded, upload not permitted", 403)
        self.notify_sync_lock = NotifySyncLock(upload_info['dest_path'])
        finfo = await self._process_uploaded_file(upload_info)
        await self.gcode_metadata.parse_metadata(
            upload_info['filename'], finfo).wait()
        started: bool = False
        queued: bool = False
        if upload_info['start_print']:
            if can_start:
                kapis: APIComp = self.server.lookup_component('klippy_apis')
                try:
                    await kapis.start_print(upload_info['filename'])
                except self.server.error:
                    # Attempt to start print failed
                    pass
                else:
                    started = True
            if self.queue_gcodes and not started:
                job_queue: JobQueue = self.server.lookup_component('job_queue')
                await job_queue.queue_job(
                    upload_info['filename'], check_exists=False)
                queued = True

        await self.notify_sync_lock.wait(300.)
        self.notify_sync_lock = None
        return {
            'item': {
                'path': upload_info['filename'],
                'root': "gcodes"
            },
            'print_started': started,
            'print_queued': queued,
            'action': "create_file"
        }

    async def _finish_standard_upload(self,
                                      upload_info: Dict[str, Any]
                                      ) -> Dict[str, Any]:
        self.notify_sync_lock = NotifySyncLock(upload_info['dest_path'])
        await self._process_uploaded_file(upload_info)
        await self.notify_sync_lock.wait(5.)
        self.notify_sync_lock = None
        return {
            'item': {
                'path': upload_info['filename'],
                'root': upload_info['root']
            },
            'action': "create_file"
        }

    async def _process_uploaded_file(self,
                                     upload_info: Dict[str, Any]
                                     ) -> Dict[str, Any]:
        try:
            if upload_info['dir_path']:
                cur_path = self.file_paths[upload_info['root']]
                dirs: List[str]
                dirs = upload_info['dir_path'].strip('/').split('/')
                for subdir in dirs:
                    cur_path = os.path.join(cur_path, subdir)
                    if os.path.exists(cur_path):
                        continue
                    os.mkdir(cur_path)
                    # wait for inotify to create a watch before proceeding
                    await asyncio.sleep(.1)
            if upload_info['unzip_ufp']:
                tmp_path = upload_info['tmp_file_path']
                finfo = self.get_path_info(tmp_path, upload_info['root'])
                finfo['ufp_path'] = tmp_path
            else:
                shutil.move(upload_info['tmp_file_path'],
                            upload_info['dest_path'])
                finfo = self.get_path_info(upload_info['dest_path'],
                                           upload_info['root'])
        except Exception:
            logging.exception("Upload Write Error")
            raise self.server.error("Unable to save file", 500)
        return finfo

    def get_file_list(self,
                      root: str,
                      list_format: bool = False
                      ) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
        # Use os.walk find files in sd path and subdirs
        filelist: Dict[str, Any] = {}
        path = self.file_paths.get(root, None)
        if path is None or not os.path.isdir(path):
            msg = f"Failed to build file list, invalid path: {root}: {path}"
            logging.info(msg)
            raise self.server.error(msg)
        logging.info(f"Updating File List <{root}>...")
        st = os.stat(path)
        visited_dirs = {(st.st_dev, st.st_ino)}
        for dir_path, dir_names, files in os.walk(path, followlinks=True):
            scan_dirs: List[str] = []
            # Filter out directories that have already been visted. This
            # prevents infinite recrusion "followlinks" is set to True
            for dname in dir_names:
                full_path = os.path.join(dir_path, dname)
                if not os.path.exists(full_path):
                    continue
                st = os.stat(full_path)
                key = (st.st_dev, st.st_ino)
                if key not in visited_dirs:
                    visited_dirs.add(key)
                    if not self.check_reserved_path(full_path, False, False):
                        scan_dirs.append(dname)
            dir_names[:] = scan_dirs
            for name in files:
                ext = os.path.splitext(name)[-1].lower()
                if root == 'gcodes' and ext not in VALID_GCODE_EXTS:
                    continue
                full_path = os.path.join(dir_path, name)
                if not os.path.exists(full_path):
                    continue
                fname = full_path[len(path) + 1:]
                finfo = self.get_path_info(full_path, root)
                filelist[fname] = finfo
        if list_format:
            flist: List[Dict[str, Any]] = []
            for fname in sorted(filelist, key=str.lower):
                fdict: Dict[str, Any] = {'path': fname}
                fdict.update(filelist[fname])
                flist.append(fdict)
            return flist
        return filelist

    def get_file_metadata(self, filename: str) -> Dict[str, Any]:
        if filename[0] == '/':
            filename = filename[1:]

        # Remove "gcodes" of its added.  It is valid for a request to
        # include to the root or assume the root is gcodes
        if filename.startswith('gcodes/'):
            filename = filename[7:]

        return self.gcode_metadata.get(filename, {})

    def list_dir(self,
                 directory: str,
                 simple_format: bool = False
                 ) -> Union[Dict[str, Any], List[str]]:
        # List a directory relative to its root.
        if directory[0] == "/":
            directory = directory[1:]
        parts = directory.split("/", 1)
        root = parts[0]
        if root not in self.file_paths:
            raise self.server.error(
                f"Invalid Directory Request: {directory}")
        path = self.file_paths[root]
        if len(parts) == 1:
            dir_path = path
        else:
            dir_path = os.path.join(path, parts[1])
        if not os.path.isdir(dir_path):
            raise self.server.error(
                f"Directory does not exist ({dir_path})")
        flist = self._list_directory(dir_path, root)
        if simple_format:
            simple_list = []
            for dirobj in flist['dirs']:
                simple_list.append("*" + dirobj['dirname'])
            for fileobj in flist['files']:
                fname = fileobj['filename']
                ext = os.path.splitext(fname)[-1].lower()
                if root == "gcodes" and ext in VALID_GCODE_EXTS:
                    simple_list.append(fname)
            return simple_list
        return flist

    async def _handle_file_delete(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        file_path: str = web_request.get_str("path")
        return await self.delete_file(file_path)

    async def delete_file(self, path: str) -> Dict[str, Any]:
        async with self.write_mutex:
            root, full_path = self._convert_request_path(path)
            self.check_reserved_path(full_path, True)
            filename = self.get_relative_path(root, full_path)
            if root not in self.full_access_roots:
                raise self.server.error(
                    f"Path not available for DELETE: {path}", 405)
            if not os.path.isfile(full_path):
                raise self.server.error(f"Invalid file path: {path}")
            try:
                self._handle_operation_check(full_path)
            except self.server.error as e:
                if e.status_code == 403:
                    raise
            os.remove(full_path)
        return {
            'item': {'path': filename, 'root': root},
            'action': "delete_file"}

    def close(self) -> None:
        self.inotify_handler.close()


INOTIFY_BUNDLE_TIME = .25
INOTIFY_MOVE_TIME = 1.

class InotifyNode:
    def __init__(self,
                 ihdlr: INotifyHandler,
                 parent: InotifyNode,
                 name: str
                 ) -> None:
        self.ihdlr = ihdlr
        self.event_loop = ihdlr.event_loop
        self.name = name
        self.parent_node = parent
        self.child_nodes: Dict[str, InotifyNode] = {}
        self.watch_desc = self.ihdlr.add_watch(self)
        self.pending_node_events: Dict[str, asyncio.Handle] = {}
        self.pending_deleted_children: Set[Tuple[str, bool]] = set()
        self.pending_file_events: Dict[str, str] = {}
        self.is_processing_metadata = False

    async def _finish_create_node(self) -> None:
        # Finish a node's creation.  All children that were created
        # with this node (ie: a directory is copied) are bundled into
        # this notification.  We also scan the node to extract metadata
        # here, as we know all files have been copied.
        if "create_node" not in self.pending_node_events:
            return
        del self.pending_node_events['create_node']
        node_path = self.get_path()
        root = self.get_root()
        # Scan child nodes for unwatched directories and metadata
        self.is_processing_metadata = True
        mevts: List[asyncio.Event] = self.scan_node()
        if mevts:
            mfuts = [e.wait() for e in mevts]
            await asyncio.gather(*mfuts)
        self.is_processing_metadata = False
        self.ihdlr.log_nodes()
        self.ihdlr.notify_filelist_changed(
            "create_dir", root, node_path)

    def _finish_delete_child(self) -> None:
        # Items deleted in a child (node or file) are batched.
        # Individual files get notifications if their parent
        # node stil exists.  Otherwise notififications are
        # bundled into the topmost deleted parent.
        if "delete_child" not in self.pending_node_events:
            self.pending_deleted_children.clear()
            return
        del self.pending_node_events['delete_child']
        node_path = self.get_path()
        root = self.get_root()
        for (name, is_node) in self.pending_deleted_children:
            item_path = os.path.join(node_path, name)
            item_type = "dir" if is_node else "file"
            self.ihdlr.clear_metadata(root, item_path, is_node)
            self.ihdlr.notify_filelist_changed(
                f"delete_{item_type}", root, item_path)
        self.pending_deleted_children.clear()

    def scan_node(self,
                  visited_dirs: Set[Tuple[int, int]] = set()
                  ) -> List[asyncio.Event]:
        dir_path = self.get_path()
        st = os.stat(dir_path)
        if st in visited_dirs:
            return []
        metadata_events: List[asyncio.Event] = []
        visited_dirs.add((st.st_dev, st.st_ino))
        for fname in os.listdir(dir_path):
            item_path = os.path.join(dir_path, fname)
            if os.path.isdir(item_path):
                fm = self.ihdlr.file_manager
                if fm.check_reserved_path(item_path, True, False):
                    continue
                new_child = self.create_child_node(fname, False)
                if new_child is not None:
                    metadata_events.extend(new_child.scan_node(visited_dirs))
            elif os.path.isfile(item_path) and self.get_root() == "gcodes":
                mevt = self.ihdlr.parse_gcode_metadata(item_path)
                metadata_events.append(mevt)
        return metadata_events

    async def move_child_node(self,
                              child_name: str,
                              new_name: str,
                              new_parent: InotifyNode
                              ) -> None:
        self.flush_delete()
        child_node = self.pop_child_node(child_name)
        if child_node is None:
            logging.info(f"No child for node at path: {self.get_path()}")
            return
        prev_path = child_node.get_path()
        prev_root = child_node.get_root()
        child_node.name = new_name
        new_parent.add_child_node(child_node)
        new_path = child_node.get_path()
        new_root = child_node.get_root()
        logging.debug(f"Moving node from '{prev_path}' to '{new_path}'")
        # Attempt to move metadata
        move_success = await self.ihdlr.try_move_metadata(
            prev_root, new_root, prev_path, new_path, is_dir=True)
        if not move_success:
            # Need rescan
            mevts = child_node.scan_node()
            if mevts:
                mfuts = [e.wait() for e in mevts]
                await asyncio.gather(*mfuts)
        self.ihdlr.notify_filelist_changed(
            "move_dir", new_root, new_path,
            prev_root, prev_path)

    def schedule_file_event(self, file_name: str, evt_name: str) -> None:
        if file_name in self.pending_file_events:
            return
        pending_node = self.search_pending_event("create_node")
        if pending_node is not None:
            pending_node.stop_event("create_node")
        self.pending_file_events[file_name] = evt_name

    async def complete_file_write(self, file_name: str) -> None:
        self.flush_delete()
        evt_name = self.pending_file_events.pop(file_name, None)
        if evt_name is None:
            logging.info(f"Invalid file write event: {file_name}")
            return
        if self.is_processing():
            logging.debug("Metadata is processing, suppressing write "
                          f"event: {file_name}")
            return
        pending_node = self.search_pending_event("create_node")
        if pending_node is not None:
            # if this event was generated as a result of a created parent
            # node it should be ignored in favor of the parent event.
            pending_node.reset_event("create_node", INOTIFY_BUNDLE_TIME)
            return
        file_path = os.path.join(self.get_path(), file_name)
        root = self.get_root()
        if root == "gcodes":
            mevt = self.ihdlr.parse_gcode_metadata(file_path)
            if os.path.splitext(file_path)[1].lower() == ".ufp":
                # don't notify .ufp files
                return
            await mevt.wait()
        self.ihdlr.notify_filelist_changed(evt_name, root, file_path)

    def add_child_node(self, node: InotifyNode) -> None:
        self.child_nodes[node.name] = node
        node.parent_node = self

    def get_child_node(self, name: str) -> Optional[InotifyNode]:
        return self.child_nodes.get(name, None)

    def pop_child_node(self, name: str) -> Optional[InotifyNode]:
        return self.child_nodes.pop(name, None)

    def create_child_node(self,
                          name: str,
                          notify: bool = True
                          ) -> Optional[InotifyNode]:
        self.flush_delete()
        if name in self.child_nodes:
            return self.child_nodes[name]
        try:
            new_child = InotifyNode(self.ihdlr, self, name)
        except Exception:
            # This node is already watched under another root,
            # bypass creation
            return None
        self.child_nodes[name] = new_child
        if notify:
            pending_node = self.search_pending_event("create_node")
            if pending_node is None:
                # schedule a pending create event for the child
                new_child.add_event("create_node", INOTIFY_BUNDLE_TIME)
            else:
                pending_node.reset_event("create_node", INOTIFY_BUNDLE_TIME)
        return new_child

    def schedule_child_delete(self, child_name: str, is_node: bool) -> None:
        if is_node:
            child_node = self.child_nodes.pop(child_name, None)
            if child_node is None:
                return
            self.ihdlr.remove_watch(
                child_node.watch_desc, need_low_level_rm=False)
            child_node.remove_event("delete_child")
        self.pending_deleted_children.add((child_name, is_node))
        self.add_event("delete_child", INOTIFY_BUNDLE_TIME)

    def clear_watches(self) -> None:
        for cnode in self.child_nodes.values():
            # Delete all of the children's children
            cnode.clear_watches()
        self.ihdlr.remove_watch(self.watch_desc)

    def get_path(self) -> str:
        return os.path.join(self.parent_node.get_path(), self.name)

    def get_root(self) -> str:
        return self.parent_node.get_root()

    def is_processing(self) -> bool:
        if self.is_processing_metadata:
            return True
        return self.parent_node.is_processing()

    def add_event(self, evt_name: str, timeout: float) -> None:
        if evt_name in self.pending_node_events:
            self.reset_event(evt_name, timeout)
            return
        callback = getattr(self, f"_finish_{evt_name}")
        hdl = self.event_loop.delay_callback(timeout, callback)
        self.pending_node_events[evt_name] = hdl

    def reset_event(self, evt_name: str, timeout: float) -> None:
        if evt_name in self.pending_node_events:
            hdl = self.pending_node_events[evt_name]
            hdl.cancel()
            callback = getattr(self, f"_finish_{evt_name}")
            hdl = self.event_loop.delay_callback(timeout, callback)
            self.pending_node_events[evt_name] = hdl

    def stop_event(self, evt_name: str) -> None:
        if evt_name in self.pending_node_events:
            hdl = self.pending_node_events[evt_name]
            hdl.cancel()

    def remove_event(self, evt_name: str) -> None:
        hdl = self.pending_node_events.pop(evt_name, None)
        if hdl is not None:
            hdl.cancel()

    def flush_delete(self):
        if 'delete_child' not in self.pending_node_events:
            return
        hdl = self.pending_node_events['delete_child']
        hdl.cancel()
        self._finish_delete_child()

    def clear_events(self, include_children: bool = True) -> None:
        if include_children:
            for child in self.child_nodes.values():
                child.clear_events(include_children)
        for hdl in self.pending_node_events.values():
            hdl.cancel()
        self.pending_node_events.clear()
        self.pending_deleted_children.clear()
        self.pending_file_events.clear()

    def search_pending_event(self, name: str) -> Optional[InotifyNode]:
        if name in self.pending_node_events:
            return self
        return self.parent_node.search_pending_event(name)

class InotifyRootNode(InotifyNode):
    def __init__(self,
                 ihdlr: INotifyHandler,
                 root_name: str,
                 root_path: str
                 ) -> None:
        self.root_name = root_name
        super().__init__(ihdlr, self, root_path)

    def get_path(self) -> str:
        return self.name

    def get_root(self) -> str:
        return self.root_name

    def search_pending_event(self, name) -> Optional[InotifyNode]:
        if name in self.pending_node_events:
            return self
        return None

    def is_processing(self) -> bool:
        return self.is_processing_metadata

class NotifySyncLock:
    def __init__(self, dest_path: str) -> None:
        self.wait_fut: Optional[asyncio.Future] = None
        self.sync_event = asyncio.Event()
        self.dest_path = dest_path
        self.notified_paths: Set[str] = set()
        self.finished: bool = False

    def update_dest(self, dest_path: str) -> None:
        self.dest_path = dest_path

    def check_need_sync(self, path: str) -> bool:
        return self.dest_path in [path, os.path.dirname(path)] \
            and not self.finished

    async def wait(self, timeout: Optional[float] = None) -> None:
        if self.finished or self.wait_fut is not None:
            # Can only wait once
            return
        if self.dest_path not in self.notified_paths:
            self.wait_fut = asyncio.Future()
            if timeout is None:
                await self.wait_fut
            else:
                try:
                    await asyncio.wait_for(self.wait_fut, timeout)
                except asyncio.TimeoutError:
                    pass
        self.sync_event.set()
        self.finished = True

    async def sync(self, path, timeout: Optional[float] = None) -> None:
        if not self.check_need_sync(path):
            return
        self.notified_paths.add(path)
        if (
            self.wait_fut is not None and
            not self.wait_fut.done() and
            self.dest_path == path
        ):
            self.wait_fut.set_result(None)
        # Transfer control to waiter
        try:
            await asyncio.wait_for(self.sync_event.wait(), timeout)
        except Exception:
            pass
        else:
            # Sleep an additional 5ms to give HTTP requests a chance to
            # return prior to a notification
            await asyncio.sleep(.005)

    def cancel(self) -> None:
        if self.finished:
            return
        if self.wait_fut is not None and not self.wait_fut.done():
            self.wait_fut.set_result(None)
        self.sync_event.set()
        self.finished = True

class INotifyHandler:
    def __init__(self,
                 config: ConfigHelper,
                 file_manager: FileManager,
                 gcode_metadata: MetadataStorage
                 ) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.enable_warn = config.getboolean("enable_inotify_warnings", True)
        self.file_manager = file_manager
        self.gcode_metadata = gcode_metadata
        self.inotify = INotify(nonblocking=True)
        self.event_loop.add_reader(
            self.inotify.fileno(), self._handle_inotify_read)

        self.node_loop_busy: bool = False
        self.pending_inotify_events: List[InotifyEvent] = []

        self.watched_roots: Dict[str, InotifyRootNode] = {}
        self.watched_nodes: Dict[int, InotifyNode] = {}
        self.pending_moves: Dict[
            int, Tuple[InotifyNode, str, asyncio.Handle]] = {}
        self.create_gcode_notifications: Dict[str, Any] = {}
        self.initialized: bool = False

    def add_root_watch(self, root: str, root_path: str) -> None:
        # remove all exisiting watches on root
        if root in self.watched_roots:
            old_root = self.watched_roots.pop(root)
            old_root.clear_watches()
            old_root.clear_events()
        try:
            root_node = InotifyRootNode(self, root, root_path)
        except Exception:
            return
        self.watched_roots[root] = root_node
        if self.initialized:
            mevts = root_node.scan_node()
            self.log_nodes()
            self.event_loop.register_callback(
                self._notify_root_updated, mevts, root, root_path)

    def initalize_roots(self):
        for root, node in self.watched_roots.items():
            evts = node.scan_node()
            if not evts:
                continue
            root_path = node.get_path()
            self.event_loop.register_callback(
                self._notify_root_updated, evts, root, root_path)
        if self.watched_roots:
            self.log_nodes()
        self.initialized = True

    async def _notify_root_updated(self,
                                   mevts: List[asyncio.Event],
                                   root: str,
                                   root_path: str
                                   ) -> None:
        if mevts:
            mfuts = [e.wait() for e in mevts]
            await asyncio.gather(*mfuts)
        cur_path = self.watched_roots[root].get_path()
        if self.server.is_running() and cur_path == root_path:
            self.notify_filelist_changed("root_update", root, root_path)

    def add_watch(self, node: InotifyNode) -> int:
        dir_path = node.get_path()
        try:
            watch: int = self.inotify.add_watch(dir_path, WATCH_FLAGS)
        except Exception:
            msg = (
                f"Error adding inotify watch to root '{node.get_root()}', "
                f"path: {dir_path}"
            )
            logging.exception(msg)
            if self.enable_warn:
                msg = f"file_manager: {msg}"
                self.server.add_warning(msg, log=False)
            raise
        if watch in self.watched_nodes:
            root = node.get_root()
            cur_node = self.watched_nodes[watch]
            existing_root = cur_node.get_root()
            msg = (
                f"Inotify watch already exists for path '{dir_path}' in "
                f"root '{existing_root}', cannot add watch to requested root "
                f"'{root}'.  This indicates that the roots overlap."
            )
            if self.enable_warn:
                msg = f"file_manager: {msg}"
                self.server.add_warning(msg)
            else:
                logging.info(msg)
            raise self.server.error("Watch already exists")
        self.watched_nodes[watch] = node
        return watch

    def remove_watch(self,
                     wdesc: int,
                     need_low_level_rm: bool = True
                     ) -> None:
        node = self.watched_nodes.pop(wdesc, None)
        if need_low_level_rm and node is not None:
            try:
                self.inotify.rm_watch(wdesc)
            except Exception:
                logging.exception(f"Error removing watch: '{node.get_path()}'")

    def clear_metadata(self,
                       root: str,
                       path: str,
                       is_dir: bool = False
                       ) -> None:
        if root == "gcodes":
            rel_path = self.file_manager.get_relative_path(root, path)
            if is_dir:
                self.gcode_metadata.remove_directory_metadata(rel_path)
            else:
                self.gcode_metadata.remove_file_metadata(rel_path)

    async def try_move_metadata(self,
                                prev_root: str,
                                new_root: str,
                                prev_path: str,
                                new_path: str,
                                is_dir: bool = False
                                ) -> bool:
        if new_root == "gcodes":
            if prev_root == "gcodes":
                # moved within the gcodes root, move metadata
                prev_rel_path = self.file_manager.get_relative_path(
                    "gcodes", prev_path)
                new_rel_path = self.file_manager.get_relative_path(
                    "gcodes", new_path)
                if is_dir:
                    await self.gcode_metadata.move_directory_metadata(
                        prev_rel_path, new_rel_path)
                else:
                    return await self.gcode_metadata.move_file_metadata(
                        prev_rel_path, new_rel_path)
            else:
                # move from a non-gcodes root to gcodes root needs a rescan
                self.clear_metadata(prev_root, prev_path, is_dir)
                return False
        elif prev_root == "gcodes":
            # moved out of the gcodes root, remove metadata
            self.clear_metadata(prev_root, prev_path, is_dir)
        return True

    def log_nodes(self) -> None:
        if self.server.is_verbose_enabled():
            debug_msg = f"Inotify Watches After Scan:"
            for wdesc, node in self.watched_nodes.items():
                wdir = node.get_path()
                wroot = node.get_root()
                debug_msg += f"\nRoot: {wroot}, Directory: {wdir},  " \
                    f"Watch: {wdesc}"
            logging.debug(debug_msg)

    def parse_gcode_metadata(self, file_path: str) -> asyncio.Event:
        rel_path = self.file_manager.get_relative_path("gcodes", file_path)
        ext = os.path.splitext(rel_path)[-1].lower()
        try:
            path_info = self.file_manager.get_path_info(file_path, "gcodes")
        except Exception:
            path_info = {}
        if (
            ext not in VALID_GCODE_EXTS or
            path_info.get('size', 0) == 0
        ):
            evt = asyncio.Event()
            evt.set()
            return evt
        if ext == ".ufp":
            rel_path = os.path.splitext(rel_path)[0] + ".gcode"
            path_info['ufp_path'] = file_path
        return self.gcode_metadata.parse_metadata(rel_path, path_info)

    def _handle_move_timeout(self, cookie: int, is_dir: bool):
        if cookie not in self.pending_moves:
            return
        parent_node, name, hdl = self.pending_moves.pop(cookie)
        item_path = os.path.join(parent_node.get_path(), name)
        root = parent_node.get_root()
        self.clear_metadata(root, item_path, is_dir)
        action = "delete_file"
        if is_dir:
            # The supplied node is a child node
            child_node = parent_node.pop_child_node(name)
            if child_node is None:
                return
            child_node.clear_watches()
            child_node.clear_events(include_children=True)
            self.log_nodes()
            action = "delete_dir"
        self.notify_filelist_changed(action, root, item_path)

    def _schedule_pending_move(self,
                               evt: InotifyEvent,
                               parent_node: InotifyNode,
                               is_dir: bool
                               ) -> None:
        hdl = self.event_loop.delay_callback(
            INOTIFY_MOVE_TIME, self._handle_move_timeout,
            evt.cookie, is_dir)
        self.pending_moves[evt.cookie] = (parent_node, evt.name, hdl)

    def _handle_inotify_read(self) -> None:
        evt: InotifyEvent
        for evt in self.inotify.read(timeout=0):
            if evt.mask & iFlags.IGNORED:
                continue
            if evt.wd not in self.watched_nodes:
                flags = " ".join([str(f) for f in iFlags.from_mask(evt.mask)])
                logging.info(
                    f"Error, inotify watch descriptor {evt.wd} "
                    f"not currently tracked: name: {evt.name}, "
                    f"flags: {flags}")
                continue
            self.pending_inotify_events.append(evt)
            if not self.node_loop_busy:
                self.node_loop_busy = True
                self.event_loop.register_callback(self._process_inotify_events)

    async def _process_inotify_events(self) -> None:
        while self.pending_inotify_events:
            evt = self.pending_inotify_events.pop(0)
            node = self.watched_nodes[evt.wd]
            if evt.mask & iFlags.ISDIR:
                await self._process_dir_event(evt, node)
            else:
                await self._process_file_event(evt, node)
        self.node_loop_busy = False

    async def _process_dir_event(self,
                                 evt: InotifyEvent,
                                 node: InotifyNode
                                 ) -> None:
        if evt.name in ['.', ".."]:
            # ignore events for self and parent
            return
        root = node.get_root()
        node_path = node.get_path()
        if evt.mask & iFlags.CREATE:
            logging.debug(f"Inotify directory create: {root}, "
                          f"{node_path}, {evt.name}")
            node.create_child_node(evt.name)
        elif evt.mask & iFlags.DELETE:
            logging.debug(f"Inotify directory delete: {root}, "
                          f"{node_path}, {evt.name}")
            node.schedule_child_delete(evt.name, True)
        elif evt.mask & iFlags.MOVED_FROM:
            logging.debug(f"Inotify directory move from: {root}, "
                          f"{node_path}, {evt.name}")
            self._schedule_pending_move(evt, node, True)
        elif evt.mask & iFlags.MOVED_TO:
            logging.debug(f"Inotify directory move to: {root}, "
                          f"{node_path}, {evt.name}")
            moved_evt = self.pending_moves.pop(evt.cookie, None)
            if moved_evt is not None:
                # Moved from a currently watched directory
                prev_parent, child_name, hdl = moved_evt
                hdl.cancel()
                await prev_parent.move_child_node(child_name, evt.name, node)
            else:
                # Moved from an unwatched directory, for our
                # purposes this is the same as creating a
                # directory
                node.create_child_node(evt.name)

    async def _process_file_event(self,
                                  evt: InotifyEvent,
                                  node: InotifyNode
                                  ) -> None:
        ext: str = os.path.splitext(evt.name)[-1].lower()
        root = node.get_root()
        node_path = node.get_path()
        file_path = os.path.join(node_path, evt.name)
        if evt.mask & iFlags.CREATE:
            logging.debug(f"Inotify file create: {root}, "
                          f"{node_path}, {evt.name}")
            node.schedule_file_event(evt.name, "create_file")
            if os.path.islink(file_path):
                logging.debug(f"Inotify symlink create: {file_path}")
                await node.complete_file_write(evt.name)
        elif evt.mask & iFlags.DELETE:
            logging.debug(f"Inotify file delete: {root}, "
                          f"{node_path}, {evt.name}")
            if root == "gcodes" and ext == ".ufp":
                # Don't notify deleted ufp files
                return
            node.schedule_child_delete(evt.name, False)
        elif evt.mask & iFlags.MOVED_FROM:
            logging.debug(f"Inotify file move from: {root}, "
                          f"{node_path}, {evt.name}")
            self._schedule_pending_move(evt, node, False)
        elif evt.mask & iFlags.MOVED_TO:
            logging.debug(f"Inotify file move to: {root}, "
                          f"{node_path}, {evt.name}")
            node.flush_delete()
            moved_evt = self.pending_moves.pop(evt.cookie, None)
            # Don't emit file events if the node is processing metadata
            can_notify = not node.is_processing()
            if moved_evt is not None:
                # Moved from a currently watched directory
                prev_parent, prev_name, hdl = moved_evt
                hdl.cancel()
                prev_root = prev_parent.get_root()
                prev_path = os.path.join(prev_parent.get_path(), prev_name)
                move_success = await self.try_move_metadata(
                    prev_root, root, prev_path, file_path)
                if not move_success:
                    # Unable to move, metadata needs parsing
                    mevt = self.parse_gcode_metadata(file_path)
                    await mevt.wait()
                if can_notify:
                    self.notify_filelist_changed(
                        "move_file", root, file_path,
                        prev_root, prev_path)
            else:
                if root == "gcodes":
                    mevt = self.parse_gcode_metadata(file_path)
                    await mevt.wait()
                if can_notify:
                    self.notify_filelist_changed(
                        "create_file", root, file_path)
            if not can_notify:
                logging.debug("Metadata is processing, suppressing move "
                              f"notification: {file_path}")
        elif evt.mask & iFlags.MODIFY:
            node.schedule_file_event(evt.name, "modify_file")
        elif evt.mask & iFlags.CLOSE_WRITE:
            logging.debug(f"Inotify writable file closed: {file_path}")
            # Only process files that have been created or modified
            await node.complete_file_write(evt.name)

    def notify_filelist_changed(self,
                                action: str,
                                root: str,
                                full_path: str,
                                source_root: Optional[str] = None,
                                source_path: Optional[str] = None
                                ) -> None:
        rel_path = self.file_manager.get_relative_path(root, full_path)
        file_info: Dict[str, Any] = {'size': 0, 'modified': 0}
        is_valid = True
        if os.path.exists(full_path):
            try:
                file_info = self.file_manager.get_path_info(full_path, root)
            except Exception:
                is_valid = False
        elif action not in ["delete_file", "delete_dir"]:
            is_valid = False
        ext = os.path.splitext(rel_path)[-1].lower()
        if (
            is_valid and
            root == "gcodes" and
            ext in VALID_GCODE_EXTS and
            action == "create_file"
        ):
            prev_info = self.create_gcode_notifications.get(rel_path, {})
            if file_info == prev_info:
                logging.debug("Ignoring duplicate 'create_file' "
                              f"notification: {rel_path}")
                is_valid = False
            else:
                self.create_gcode_notifications[rel_path] = dict(file_info)
        elif rel_path in self.create_gcode_notifications:
            del self.create_gcode_notifications[rel_path]
        file_info['path'] = rel_path
        file_info['root'] = root
        result = {'action': action, 'item': file_info}
        if source_path is not None and source_root is not None:
            src_rel_path = self.file_manager.get_relative_path(
                source_root, source_path)
            result['source_item'] = {'path': src_rel_path, 'root': source_root}
        sync_lock = self.file_manager.sync_inotify_event(full_path)
        if sync_lock is not None:
            # Delay this notification so that it occurs after an item
            logging.debug(f"Syncing notification: {full_path}")
            self.event_loop.register_callback(
                self._sync_with_request, result,
                sync_lock.sync(full_path), is_valid)
        elif is_valid:
            self.server.send_event("file_manager:filelist_changed", result)

    async def _sync_with_request(self,
                                 result: Dict[str, Any],
                                 sync_fut: Coroutine,
                                 is_valid: bool
                                 ) -> None:
        await sync_fut
        if is_valid:
            self.server.send_event("file_manager:filelist_changed", result)

    def close(self) -> None:
        self.event_loop.remove_reader(self.inotify.fileno())
        for watch in self.watched_nodes.keys():
            try:
                self.inotify.rm_watch(watch)
            except Exception:
                pass


METADATA_NAMESPACE = "gcode_metadata"
METADATA_VERSION = 3

class MetadataStorage:
    def __init__(self,
                 config: ConfigHelper,
                 db: DBComp
                 ) -> None:
        self.server = config.get_server()
        self.enable_object_proc = config.getboolean(
            'enable_object_processing', False)
        self.gc_path = ""
        db.register_local_namespace(METADATA_NAMESPACE)
        self.mddb = db.wrap_namespace(
            METADATA_NAMESPACE, parse_keys=False)
        version = db.get_item(
            "moonraker", "file_manager.metadata_version", 0).result()
        if version != METADATA_VERSION:
            # Clear existing metadata when version is bumped
            self.mddb.clear()
            db.insert_item(
                "moonraker", "file_manager.metadata_version",
                METADATA_VERSION)
        # Keep a local cache of the metadata.  This allows for synchronous
        # queries.  Metadata is generally under 1KiB per entry, so even at
        # 1000 gcode files we are using < 1MiB of additional memory.
        # That said, in the future all components that access metadata should
        # be refactored to do so asynchronously.
        self.metadata: Dict[str, Any] = self.mddb.as_dict()
        self.pending_requests: Dict[
            str, Tuple[Dict[str, Any], asyncio.Event]] = {}
        self.busy: bool = False

    def prune_storage(self) -> None:
        # Check for removed gcode files while moonraker was shutdown
        if self.gc_path:
            del_keys: List[str] = []
            for fname in list(self.metadata.keys()):
                fpath = os.path.join(self.gc_path, fname)
                if not os.path.isfile(fpath):
                    del self.metadata[fname]
                    del_keys.append(fname)
                elif "thumbnails" in self.metadata[fname]:
                    # Check for any stale data entries and remove them
                    need_sync = False
                    for thumb in self.metadata[fname]['thumbnails']:
                        if 'data' in thumb:
                            del thumb['data']
                            need_sync = True
                    if need_sync:
                        self.mddb[fname] = self.metadata[fname]
            # Delete any removed keys from the database
            if del_keys:
                ret = self.mddb.delete_batch(del_keys).result()
                self._remove_thumbs(ret)
                pruned = '\n'.join(ret.keys())
                if pruned:
                    logging.info(f"Pruned metadata for the following:\n"
                                 f"{pruned}")

    def update_gcode_path(self, path: str) -> None:
        if path == self.gc_path:
            return
        if self.gc_path:
            self.metadata.clear()
            self.mddb.clear()
        self.gc_path = path

    def get(self,
            key: str,
            default: Optional[_T] = None
            ) -> Union[_T, Dict[str, Any]]:
        return deepcopy(self.metadata.get(key, default))

    def insert(self, key: str, value: Dict[str, Any]) -> None:
        val = deepcopy(value)
        self.metadata[key] = val
        self.mddb[key] = val

    def _has_valid_data(self,
                        fname: str,
                        path_info: Dict[str, Any]
                        ) -> bool:
        if path_info.get('ufp_path', None) is not None:
            # UFP files always need processing
            return False
        mdata: Dict[str, Any]
        mdata = self.metadata.get(fname, {'size': "", 'modified': 0})
        for field in ['size', 'modified']:
            if mdata[field] != path_info.get(field, None):
                return False
        return True

    def remove_directory_metadata(self, dir_name: str) -> None:
        if dir_name[-1] != "/":
            dir_name += "/"
        del_items: Dict[str, Any] = {}
        for fname in list(self.metadata.keys()):
            if fname.startswith(dir_name):
                md = self.metadata.pop(fname, None)
                if md:
                    del_items[fname] = md
        if del_items:
            # Remove items from persistent storage
            self.mddb.delete_batch(list(del_items.keys()))
            eventloop = self.server.get_event_loop()
            # Remove thumbs in a nother thread
            eventloop.run_in_thread(self._remove_thumbs, del_items)

    def remove_file_metadata(self, fname: str) -> None:
        md: Optional[Dict[str, Any]] = self.metadata.pop(fname, None)
        if md is None:
            return
        self.mddb.pop(fname, None)
        eventloop = self.server.get_event_loop()
        eventloop.run_in_thread(self._remove_thumbs, {fname: md})

    def _remove_thumbs(self, records: Dict[str, Dict[str, Any]]) -> None:
        for fname, metadata in records.items():
            # Delete associated thumbnails
            fdir = os.path.dirname(os.path.join(self.gc_path, fname))
            if "thumbnails" in metadata:
                thumb: Dict[str, Any]
                for thumb in metadata["thumbnails"]:
                    path: Optional[str] = thumb.get("relative_path", None)
                    if path is None:
                        continue
                    thumb_path = os.path.join(fdir, path)
                    if not os.path.isfile(thumb_path):
                        continue
                    try:
                        os.remove(thumb_path)
                    except Exception:
                        logging.debug(f"Error removing thumb at {thumb_path}")

    async def move_directory_metadata(self,
                                      prev_dir: str,
                                      new_dir: str
                                      ) -> None:
        if prev_dir[-1] != "/":
            prev_dir += "/"
        moved: List[Tuple[str, str, Dict[str, Any]]] = []
        for prev_fname in list(self.metadata.keys()):
            if prev_fname.startswith(prev_dir):
                new_fname = os.path.join(new_dir, prev_fname[len(prev_dir):])
                md: Optional[Dict[str, Any]]
                md = self.metadata.pop(prev_fname, None)
                if md is None:
                    continue
                self.metadata[new_fname] = md
                moved.append((prev_fname, new_fname, md))
        if moved:
            source = [m[0] for m in moved]
            dest = [m[1] for m in moved]
            self.mddb.move_batch(source, dest)
            eventloop = self.server.get_event_loop()
            await eventloop.run_in_thread(self._move_thumbnails, moved)

    async def move_file_metadata(self,
                                 prev_fname: str,
                                 new_fname: str,
                                 move_thumbs: bool = True
                                 ) -> bool:
        metadata: Optional[Dict[str, Any]]
        metadata = self.metadata.pop(prev_fname, None)
        if metadata is None:
            # If this move overwrites an existing file it is necessary
            # to rescan which requires that we remove any existing
            # metadata.
            if self.metadata.pop(new_fname, None) is not None:
                self.mddb.pop(new_fname, None)
            return False
        self.metadata[new_fname] = metadata
        self.mddb.move_batch([prev_fname], [new_fname])
        if move_thumbs:
            eventloop = self.server.get_event_loop()
            await eventloop.run_in_thread(
                self._move_thumbnails,
                [(prev_fname, new_fname, metadata)])
        return True

    def _move_thumbnails(self,
                         records: List[Tuple[str, str, Dict[str, Any]]]
                         ) -> None:
        for (prev_fname, new_fname, metadata) in records:
            prev_dir = os.path.dirname(os.path.join(self.gc_path, prev_fname))
            new_dir = os.path.dirname(os.path.join(self.gc_path, new_fname))
            if "thumbnails" in metadata:
                thumb: Dict[str, Any]
                for thumb in metadata["thumbnails"]:
                    path: Optional[str] = thumb.get("relative_path", None)
                    if path is None:
                        continue
                    thumb_path = os.path.join(prev_dir, path)
                    if not os.path.isfile(thumb_path):
                        continue
                    new_path = os.path.join(new_dir, path)
                    try:
                        os.makedirs(os.path.dirname(new_path), exist_ok=True)
                        shutil.move(thumb_path, new_path)
                    except Exception:
                        logging.debug(f"Error moving thumb from {thumb_path}"
                                      f" to {new_path}")

    def parse_metadata(self,
                       fname: str,
                       path_info: Dict[str, Any]
                       ) -> asyncio.Event:
        if fname in self.pending_requests:
            return self.pending_requests[fname][1]
        mevt = asyncio.Event()
        ext = os.path.splitext(fname)[1]
        if (
            ext not in VALID_GCODE_EXTS or
            self._has_valid_data(fname, path_info)
        ):
            # request already pending or not necessary
            mevt.set()
            return mevt
        self.pending_requests[fname] = (path_info, mevt)
        if self.busy:
            return mevt
        self.busy = True
        event_loop = self.server.get_event_loop()
        event_loop.register_callback(self._process_metadata_update)
        return mevt

    async def _process_metadata_update(self) -> None:
        while self.pending_requests:
            fname, (path_info, mevt) = \
                list(self.pending_requests.items())[0]
            if self._has_valid_data(fname, path_info):
                mevt.set()
                continue
            ufp_path: Optional[str] = path_info.get('ufp_path', None)
            retries = 3
            while retries:
                try:
                    await self._run_extract_metadata(fname, ufp_path)
                except Exception:
                    logging.exception("Error running extract_metadata.py")
                    retries -= 1
                else:
                    break
            else:
                if ufp_path is None:
                    self.metadata[fname] = {
                        'size': path_info.get('size', 0),
                        'modified': path_info.get('modified', 0),
                        'print_start_time': None,
                        'job_id': None
                    }
                    self.mddb[fname] = self.metadata[fname]
                logging.info(
                    f"Unable to extract medatadata from file: {fname}")
            self.pending_requests.pop(fname, None)
            mevt.set()
        self.busy = False

    async def _run_extract_metadata(self,
                                    filename: str,
                                    ufp_path: Optional[str]
                                    ) -> None:
        # Escape single quotes in the file name so that it may be
        # properly loaded
        filename = filename.replace("\"", "\\\"")
        cmd = " ".join([sys.executable, METADATA_SCRIPT, "-p",
                        self.gc_path, "-f", f"\"{filename}\""])
        timeout = 10.
        if ufp_path is not None and os.path.isfile(ufp_path):
            timeout = 300.
            ufp_path.replace("\"", "\\\"")
            cmd += f" -u \"{ufp_path}\""
        if self.enable_object_proc:
            timeout = 300.
            cmd += " --check-objects"
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        scmd = shell_cmd.build_shell_command(cmd, log_stderr=True)
        result = await scmd.run_with_response(timeout=timeout)
        try:
            decoded_resp: Dict[str, Any] = json.loads(result.strip())
        except Exception:
            logging.debug(f"Invalid metadata response:\n{result}")
            raise
        path: str = decoded_resp['file']
        metadata: Dict[str, Any] = decoded_resp['metadata']
        if not metadata:
            # This indicates an error, do not add metadata for this
            raise self.server.error("Unable to extract metadata")
        metadata.update({'print_start_time': None, 'job_id': None})
        self.metadata[path] = metadata
        self.mddb[path] = metadata

def load_component(config: ConfigHelper) -> FileManager:
    return FileManager(config)
