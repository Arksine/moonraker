# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import shutil
import logging
import json
import tempfile
import asyncio
from concurrent.futures import ThreadPoolExecutor
from tornado.ioloop import IOLoop
from tornado.locks import Event
from inotify_simple import INotify
from inotify_simple import flags as iFlags

VALID_GCODE_EXTS = ['.gcode', '.g', '.gco', '.ufp', '.nc']
FULL_ACCESS_ROOTS = ["gcodes", "config"]
METADATA_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "../../scripts/extract_metadata.py"))
WATCH_FLAGS = iFlags.CREATE | iFlags.DELETE | iFlags.MODIFY \
    | iFlags.MOVED_TO | iFlags.MOVED_FROM | iFlags.ONLYDIR \
    | iFlags.CLOSE_WRITE

class FileManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_paths = {}
        database = self.server.load_component(config, "database")
        gc_path = database.get_item("moonraker", "file_manager.gcode_path", "")
        self.gcode_metadata = MetadataStorage(self.server, gc_path, database)
        self.inotify_handler = INotifyHandler(config, self,
                                              self.gcode_metadata)
        self.fixed_path_args = {}

        # Register file management endpoints
        self.server.register_endpoint(
            "/server/files/list", ['GET'], self._handle_filelist_request)
        self.server.register_endpoint(
            "/server/files/metadata", ['GET'], self._handle_metadata_request)
        self.server.register_endpoint(
            "/server/files/directory", ['GET', 'POST', 'DELETE'],
            self._handle_directory_request)
        self.server.register_endpoint(
            "/server/files/move", ['POST'], self._handle_file_move_copy)
        self.server.register_endpoint(
            "/server/files/copy", ['POST'], self._handle_file_move_copy)
        self.server.register_endpoint(
            "/server/files/delete_file", ['DELETE'], self._handle_file_delete,
            protocol=["websocket"])
        # register client notificaitons
        self.server.register_notification("file_manager:filelist_changed")
        self.server.register_notification("file_manager:metadata_update")
        # Register APIs to handle file uploads
        self.server.register_upload_handler("/server/files/upload")
        self.server.register_upload_handler("/api/files/local")

        self.server.register_event_handler(
            "server:klippy_identified", self._update_fixed_paths)

        # Register Klippy Configuration Path
        config_path = config.get('config_path', None)
        if config_path is not None:
            ret = self.register_directory('config', config_path)
            if not ret:
                raise config.error(
                    "Option 'config_path' is not a valid directory")

        # If gcode path is in the database, register it
        if gc_path:
            self.register_directory('gcodes', gc_path)

    def _update_fixed_paths(self):
        kinfo = self.server.get_klippy_info()
        paths = {k: kinfo.get(k) for k in
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
            example_cfg_path = os.path.join(klipper_path, "config")
            self.register_directory("config_examples", example_cfg_path)
            docs_path = os.path.join(klipper_path, "docs")
            self.register_directory("docs", docs_path)

        # Register log path
        log_file = paths.get('log_file')
        if log_file is not None:
            log_path = os.path.abspath(os.path.expanduser(log_file))
            self.server.register_static_file_handler(
                "klippy.log", log_path, force=True)

    def register_directory(self, root, path):
        if path is None:
            return False
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.islink(path):
            path = os.path.realpath(path)
        if not os.path.isdir(path) or path == "/":
            logging.info(
                f"\nSupplied path ({path}) for ({root}) a valid. Make sure\n"
                "that the path exists and is not the file system root.")
            return False
        permissions = os.R_OK
        if root in FULL_ACCESS_ROOTS:
            permissions |= os.W_OK
        if not os.access(path, permissions):
            logging.info(
                f"\nMoonraker does not have permission to access path "
                f"({path}) for ({root}).")
            return False
        if path != self.file_paths.get(root, ""):
            self.file_paths[root] = path
            self.server.register_static_file_handler(root, path)
            if root == "gcodes":
                database = self.server.lookup_component(
                    "database").wrap_namespace("moonraker")
                database["file_manager.gcode_path"] = path
                # scan for metadata changes
                self.gcode_metadata.update_gcode_path(path)
            if root in FULL_ACCESS_ROOTS:
                # Refresh the file list and add watches
                self.inotify_handler.add_root_watch(root, path)
        return True

    def get_sd_directory(self):
        return self.file_paths.get('gcodes', "")

    def get_registered_dirs(self):
        return list(self.file_paths.keys())

    def get_fixed_path_args(self):
        return dict(self.fixed_path_args)

    def get_relative_path(self, root, full_path):
        root_dir = self.file_paths.get(root, None)
        if root_dir is None or not full_path.startswith(root_dir):
            return ""
        return os.path.relpath(full_path, start=root_dir)

    def check_file_exists(self, root, filename):
        root_dir = self.file_paths.get(root, "")
        file_path = os.path.join(root_dir, filename)
        return os.path.exists(file_path)

    async def _handle_filelist_request(self, web_request):
        root = web_request.get_str('root', "gcodes")
        return self.get_file_list(root, list_format=True)

    async def _handle_metadata_request(self, web_request):
        requested_file = web_request.get_str('filename')
        metadata = self.gcode_metadata.get(requested_file, None)
        if metadata is None:
            raise self.server.error(
                f"Metadata not available for <{requested_file}>", 404)
        metadata['filename'] = requested_file
        return metadata

    async def _handle_directory_request(self, web_request):
        directory = web_request.get_str('path', "gcodes")
        root, dir_path = self._convert_request_path(directory)
        action = web_request.get_action()
        if action == 'GET':
            is_extended = web_request.get_boolean('extended', False)
            # Get list of files and subdirectories for this target
            dir_info = self._list_directory(dir_path, is_extended)
            return dir_info
        elif action == 'POST' and root in FULL_ACCESS_ROOTS:
            # Create a new directory
            try:
                os.mkdir(dir_path)
            except Exception as e:
                raise self.server.error(str(e))
        elif action == 'DELETE' and root in FULL_ACCESS_ROOTS:
            # Remove a directory
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
                await self._handle_operation_check(dir_path)
                shutil.rmtree(dir_path)
            else:
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    raise self.server.error(str(e))
        else:
            raise self.server.error("Operation Not Supported", 405)
        return "ok"

    async def _handle_operation_check(self, requested_path):
        # Get virtual_sdcard status
        klippy_apis = self.server.lookup_component('klippy_apis')
        result = await klippy_apis.query_objects({'print_stats': None})
        pstats = result.get('print_stats', {})
        loaded_file = pstats.get('filename', "")
        state = pstats.get('state', "")
        gc_path = self.file_paths.get('gcodes', "")
        full_path = os.path.join(gc_path, loaded_file)
        if loaded_file and state != "complete":
            if os.path.isdir(requested_path):
                # Check to see of the loaded file is in the request
                if full_path.startswith(requested_path):
                    raise self.server.error("File currently in use", 403)
            elif full_path == requested_path:
                raise self.server.error("File currently in use", 403)
        ongoing = state in ["printing", "paused"]
        return ongoing

    def _convert_request_path(self, request_path):
        # Parse the root, relative path, and disk path from a remote request
        parts = request_path.strip("/").split("/", 1)
        if not parts:
            raise self.server.error(f"Invalid path: {request_path}")
        root = parts[0]
        if root not in self.file_paths:
            raise self.server.error(f"Invalid root path ({root})")
        disk_path = self.file_paths[root]
        if len(parts) > 1:
            disk_path = os.path.join(disk_path, parts[1])
        return root, disk_path

    async def _handle_file_move_copy(self, web_request):
        source = web_request.get_str("source")
        destination = web_request.get_str("dest")
        ep = web_request.get_endpoint()
        if source is None:
            raise self.server.error("File move/copy request issing source")
        if destination is None:
            raise self.server.error(
                "File move/copy request missing destination")
        source_root, source_path = self._convert_request_path(source)
        dest_root, dest_path = self._convert_request_path(destination)
        if dest_root not in FULL_ACCESS_ROOTS:
            raise self.server.error(
                f"Destination path is read-only: {dest_root}")
        if not os.path.exists(source_path):
            raise self.server.error(f"File {source_path} does not exist")
        # make sure the destination is not in use
        if os.path.exists(dest_path):
            await self._handle_operation_check(dest_path)
        if ep == "/server/files/move":
            if source_root not in FULL_ACCESS_ROOTS:
                raise self.server.error(
                    f"Source path is read-only, cannot move: {source_root}")
            # if moving the file, make sure the source is not in use
            await self._handle_operation_check(source_path)
            try:
                shutil.move(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
        elif ep == "/server/files/copy":
            ioloop = IOLoop.current()
            with ThreadPoolExecutor(max_workers=1) as tpe:
                await ioloop.run_in_executor(
                    tpe, self._do_copy, source_path, dest_path)
        return "ok"

    def _do_copy(self, source_path, dest_path):
        try:
            if os.path.isdir(source_path):
                shutil.copytree(source_path, dest_path)
            else:
                shutil.copy2(source_path, dest_path)
        except Exception as e:
            raise self.server.error(str(e))

    def _list_directory(self, path, is_extended=False):
        if not os.path.isdir(path):
            raise self.server.error(
                f"Directory does not exist ({path})")
        flist = {'dirs': [], 'files': []}
        for fname in os.listdir(path):
            full_path = os.path.join(path, fname)
            if not os.path.exists(full_path):
                continue
            path_info = self.get_path_info(full_path)
            if os.path.isdir(full_path):
                path_info['dirname'] = fname
                flist['dirs'].append(path_info)
            elif os.path.isfile(full_path):
                path_info['filename'] = fname
                # Check to see if a filelist update is necessary
                ext = os.path.splitext(fname)[-1].lower()
                gc_path = self.file_paths.get('gcodes', None)
                if gc_path is not None and full_path.startswith(gc_path) and \
                        ext in VALID_GCODE_EXTS and is_extended:
                    rel_path = os.path.relpath(full_path, start=gc_path)
                    metadata = self.gcode_metadata.get(rel_path, {})
                    path_info.update(metadata)
                flist['files'].append(path_info)
        usage = shutil.disk_usage(path)
        flist['disk_usage'] = usage._asdict()
        return flist

    def get_path_info(self, path):
        modified = os.path.getmtime(path)
        size = os.path.getsize(path)
        path_info = {'modified': modified, 'size': size}
        return path_info

    def gen_temp_upload_path(self):
        ioloop = IOLoop.current()
        return os.path.join(
            tempfile.gettempdir(),
            f"moonraker.upload-{int(ioloop.time())}.mru")

    async def finalize_upload(self, form_args):
        # lookup root file path
        try:
            upload_info = self._parse_upload_args(form_args)
            root = upload_info['root']
            if root == "gcodes":
                result = await self._finish_gcode_upload(upload_info)
            elif root in FULL_ACCESS_ROOTS:
                result = await self._finish_standard_upload(upload_info)
            else:
                raise self.server.error(f"Invalid root request: {root}")
        except Exception:
            try:
                os.remove(form_args['tmp_file_path'])
            except Exception:
                pass
            raise
        return result

    def _parse_upload_args(self, upload_args):
        if 'filename' not in upload_args:
            raise self.server.error(
                "No file name specifed in upload form")
        # check relative path
        root = upload_args.get('root', "gcodes").lower()
        if root not in self.file_paths:
            raise self.server.error(f"Root {root} not available")
        root_path = self.file_paths[root]
        dir_path = upload_args.get('path', "")
        if os.path.isfile(root_path):
            filename = os.path.basename(root_path)
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
        start_print = upload_args.get('print', "false") == "true"
        f_ext = os.path.splitext(dest_path)[-1].lower()
        unzip_ufp = f_ext == ".ufp" and root == "gcodes"
        if unzip_ufp:
            filename = os.path.splitext(filename)[0] + ".gcode"
            dest_path = os.path.splitext(dest_path)[0] + ".gcode"
        return {
            'root': root,
            'filename': filename,
            'dir_path': dir_path,
            'dest_path': dest_path,
            'tmp_file_path': upload_args['tmp_file_path'],
            'start_print': start_print,
            'unzip_ufp': unzip_ufp
        }

    async def _finish_gcode_upload(self, upload_info):
        print_ongoing = False
        start_print = upload_info['start_print']
        # Verify that the operation can be done if attempting to upload a gcode
        try:
            check_path = upload_info['dest_path']
            print_ongoing = await self._handle_operation_check(
                check_path)
        except self.server.error as e:
            if e.status_code == 403:
                raise self.server.error(
                    "File is loaded, upload not permitted", 403)
            else:
                # Couldn't reach Klippy, so it should be safe
                # to permit the upload but not start
                start_print = False
        # Don't start if another print is currently in progress
        start_print = start_print and not print_ongoing
        finfo = self._process_uploaded_file(upload_info)
        await self.gcode_metadata.parse_metadata(
            upload_info['filename'], finfo).wait()
        if start_print:
            # Make a Klippy Request to "Start Print"
            klippy_apis = self.server.lookup_component('klippy_apis')
            try:
                await klippy_apis.start_print(upload_info['filename'])
            except self.server.error:
                # Attempt to start print failed
                start_print = False
        return {
            'result': upload_info['filename'],
            'print_started': start_print
        }

    async def _finish_standard_upload(self, upload_info):
        self._process_uploaded_file(upload_info)
        return {'result': upload_info['filename']}

    def _process_uploaded_file(self, upload_info):
        try:
            if upload_info['dir_path']:
                os.makedirs(os.path.dirname(
                    upload_info['dest_path']), exist_ok=True)
            if upload_info['unzip_ufp']:
                tmp_path = upload_info['tmp_file_path']
                finfo = self.get_path_info(tmp_path)
                finfo['ufp_path'] = tmp_path
            else:
                shutil.move(upload_info['tmp_file_path'],
                            upload_info['dest_path'])
                finfo = self.get_path_info(upload_info['dest_path'])
        except Exception:
            logging.exception("Upload Write Error")
            raise self.server.error("Unable to save file", 500)
        return finfo

    def get_file_list(self, root, list_format=False):
        # Use os.walk find files in sd path and subdirs
        filelist = {}
        path = self.file_paths.get(root, None)
        if path is None or not os.path.isdir(path):
            msg = f"Failed to build file list, invalid path: {root}: {path}"
            logging.info(msg)
            raise self.server.error(msg)
        logging.info(f"Updating File List <{root}>...")
        st = os.stat(path)
        visited_dirs = {(st.st_dev, st.st_ino)}
        for dir_path, dir_names, files in os.walk(path, followlinks=True):
            scan_dirs = []
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
                finfo = self.get_path_info(full_path)
                filelist[fname] = finfo
        if list_format:
            flist = []
            for fname in sorted(filelist, key=str.lower):
                fdict = {'filename': fname}
                fdict.update(filelist[fname])
                flist.append(fdict)
            return flist
        return filelist

    def get_file_metadata(self, filename):
        if filename[0] == '/':
            filename = filename[1:]

        # Remove "gcodes" of its added.  It is valid for a request to
        # include to the root or assume the root is gcodes
        if filename.startswith('gcodes/'):
            filename = filename[7:]

        return self.gcode_metadata.get(filename, {})

    def list_dir(self, directory, simple_format=False):
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
        flist = self._list_directory(dir_path)
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

    async def _handle_file_delete(self, web_request):
        file_path = web_request.get_str("path")
        return await self.delete_file(file_path)

    async def delete_file(self, path):
        parts = path.lstrip("/").split("/", 1)
        if len(parts) != 2:
            raise self.server.error(
                f"Path not available for DELETE: {path}", 405)
        root = parts[0]
        filename = parts[1]
        if root not in self.file_paths or root not in FULL_ACCESS_ROOTS:
            raise self.server.error(
                f"Path not available for DELETE: {path}", 405)
        root_path = self.file_paths[root]
        full_path = os.path.join(root_path, filename)
        if not os.path.isfile(full_path):
            raise self.server.error(f"Invalid file path: {path}")
        if root == "gcodes":
            try:
                await self._handle_operation_check(full_path)
            except self.server.error as e:
                if e.status_code == 403:
                    raise
        os.remove(full_path)
        return filename

    def close(self):
        self.inotify_handler.close()


INOTIFY_BUNDLE_TIME = .25
INOTIFY_MOVE_TIME = 1.

class InotifyNode:
    def __init__(self, ihdlr, parent, name):
        self.ihdlr = ihdlr
        self.name = name
        self.parent_node = parent
        self.child_nodes = {}
        self.watch_desc = self.ihdlr.add_watch(self)
        self.pending_node_events = {}
        self.pending_deleted_children = set()
        self.pending_file_events = {}

    async def _finish_create_node(self):
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
        mevts = self.scan_node(notify_created=False)
        if mevts:
            mfuts = [e.wait() for e in mevts]
            await asyncio.gather(*mfuts)
        self.ihdlr.log_nodes()
        self.ihdlr.notify_filelist_changed(
            "create_dir", root, node_path)

    def _finish_delete_child(self):
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

    def scan_node(self, notify_created=True, visited_dirs=set()):
        dir_path = self.get_path()
        st = os.stat(dir_path)
        if st in visited_dirs:
            return []
        metadata_events = []
        visited_dirs.add((st.st_dev, st.st_ino))
        for fname in os.listdir(dir_path):
            if fname[0] == ".":
                continue
            item_path = os.path.join(dir_path, fname)
            ext = os.path.splitext(fname)[-1].lower()
            if os.path.isdir(item_path):
                new_child = self.create_child_node(fname, notify_created)
                metadata_events.extend(new_child.scan_node(
                    notify_created, visited_dirs))
            elif os.path.isfile(item_path) and \
                    self.get_root() == "gcodes" and \
                    ext in VALID_GCODE_EXTS:
                mevt = self.ihdlr.parse_gcode_metadata(item_path)
                metadata_events.append(mevt)
        return metadata_events

    async def move_child_node(self, child_name, new_name, new_parent):
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
        # TODO: It is possible to "move" metadata rather
        # than rescan.
        mevts = child_node.scan_node(notify_created=False)
        if mevts:
            mfuts = [e.wait() for e in mevts]
            await asyncio.gather(*mfuts)
        self.ihdlr.notify_filelist_changed(
            "move_dir", new_root, new_path,
            prev_root, prev_path)

    def schedule_file_event(self, file_name, evt_name):
        if file_name in self.pending_file_events:
            return
        pending_node = self.search_pending_event("create_node")
        if pending_node is not None:
            pending_node.stop_event("create_node")
        self.pending_file_events[file_name] = evt_name

    async def complete_file_write(self, file_name):
        evt_name = self.pending_file_events.pop(file_name, None)
        if evt_name is None:
            logging.info(f"Invalid file write event: {file_name}")
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

    def add_child_node(self, node):
        self.child_nodes[node.name] = node
        node.parent_node = self

    def get_child_node(self, name):
        return self.child_nodes.get(name, None)

    def pop_child_node(self, name):
        return self.child_nodes.pop(name, None)

    def create_child_node(self, name, notify=True):
        if name in self.child_nodes:
            return self.child_nodes[name]
        new_child = InotifyNode(self.ihdlr, self, name)
        self.child_nodes[name] = new_child
        if notify:
            pending_node = self.search_pending_event("create_node")
            if pending_node is None:
                # schedule a pending create event for the child
                new_child.add_event("create_node", INOTIFY_BUNDLE_TIME)
            else:
                pending_node.reset_event("create_node", INOTIFY_BUNDLE_TIME)
        return new_child

    def schedule_child_delete(self, child_name, is_node):
        if is_node:
            child_node = self.child_nodes.pop(child_name, None)
            if child_node is None:
                return
            self.ihdlr.remove_watch(
                child_node.watch_desc, need_low_level_rm=False)
            child_node.remove_event("delete_child")
        self.pending_deleted_children.add((child_name, is_node))
        self.add_event("delete_child", INOTIFY_BUNDLE_TIME)

    def clear_watches(self):
        for cnode in self.child_nodes.values():
            # Delete all of the children's children
            cnode.clear_watches()
        self.ihdlr.remove_watch(self.watch_desc)

    def get_path(self):
        return os.path.join(self.parent_node.get_path(), self.name)

    def get_root(self):
        return self.parent_node.get_root()

    def add_event(self, evt_name, timeout):
        if evt_name in self.pending_node_events:
            self.reset_event(evt_name, timeout)
            return
        callback = getattr(self, f"_finish_{evt_name}")
        hdl = IOLoop.current().call_later(timeout, callback)
        self.pending_node_events[evt_name] = hdl

    def reset_event(self, evt_name, timeout):
        if evt_name in self.pending_node_events:
            ioloop = IOLoop.current()
            hdl = self.pending_node_events[evt_name]
            ioloop.remove_timeout(hdl)
            callback = getattr(self, f"_finish_{evt_name}")
            hdl = ioloop.call_later(timeout, callback)
            self.pending_node_events[evt_name] = hdl

    def stop_event(self, evt_name):
        if evt_name in self.pending_node_events:
            hdl = self.pending_node_events[evt_name]
            IOLoop.current().remove_timeout(hdl)

    def remove_event(self, evt_name):
        hdl = self.pending_node_events.pop(evt_name, None)
        if hdl is not None:
            IOLoop.current().remove_timeout(hdl)

    def clear_events(self, include_children=True):
        if include_children:
            for child in self.child_nodes.values():
                child.clear_events(include_children)
        for hdl in self.pending_node_events.values():
            IOLoop.current().remove_timeout(hdl)
        self.pending_node_events.clear()
        self.pending_deleted_children.clear()
        self.pending_file_events.clear()

    def search_pending_event(self, name):
        if name in self.pending_node_events:
            return self
        if self.parent_node is None:
            return None
        return self.parent_node.search_pending_event(name)

class InotifyRootNode(InotifyNode):
    def __init__(self, ihdlr, root_name, root_path):
        self.root_name = root_name
        super().__init__(ihdlr, None, root_path)

    def get_path(self):
        return self.name

    def get_root(self):
        return self.root_name

class INotifyHandler:
    def __init__(self, config, file_manager, gcode_metadata):
        self.server = config.get_server()
        self.debug_enabled = config['server'].getboolean(
            'enable_debug_logging', False)
        self.file_manager = file_manager
        self.gcode_metadata = gcode_metadata
        self.inotify = INotify(nonblocking=True)
        IOLoop.current().add_handler(
            self.inotify.fileno(), self._handle_inotify_read,
            IOLoop.READ | IOLoop.ERROR)

        self.event_loop_busy = False
        self.pending_inotify_events = []

        self.watched_roots = {}
        self.watched_nodes = {}
        self.pending_moves = {}

    def add_root_watch(self, root, root_path):
        if root not in FULL_ACCESS_ROOTS:
            return
        # remove all exisiting watches on root
        if root in self.watched_roots:
            old_root = self.watched_roots.pop(root)
            old_root.clear_watches()
            old_root.clear_events()
        root_node = InotifyRootNode(self, root, root_path)
        self.watched_roots[root] = root_node
        root_node.scan_node(notify_created=False)
        self.log_nodes()

    def add_watch(self, node):
        dir_path = node.get_path()
        try:
            watch = self.inotify.add_watch(dir_path, WATCH_FLAGS)
        except OSError:
            logging.exception(
                f"Error adding watch, already exists: {dir_path}")
        self.watched_nodes[watch] = node
        return watch

    def remove_watch(self, wdesc, need_low_level_rm=True):
        node = self.watched_nodes.pop(wdesc, None)
        if need_low_level_rm:
            try:
                self.inotify.rm_watch(wdesc)
            except Exception:
                logging.exception(f"Error removing watch: '{node.get_path()}'")

    def clear_metadata(self, root, path, is_dir=False):
        if root == "gcodes":
            rel_path = self.file_manager.get_relative_path(root, path)
            if is_dir:
                self.gcode_metadata.remove_directory_metadata(rel_path)
            else:
                self.gcode_metadata.remove_file_metadata(rel_path)

    def log_nodes(self):
        if self.debug_enabled:
            debug_msg = f"Inotify Watches After Scan:"
            for wdesc, node in self.watched_nodes.items():
                wdir = node.get_path()
                wroot = node.get_root()
                debug_msg += f"\nRoot: {wroot}, Directory: {wdir},  " \
                    f"Watch: {wdesc}"
            logging.debug(debug_msg)

    def parse_gcode_metadata(self, file_path):
        rel_path = self.file_manager.get_relative_path("gcodes", file_path)
        if not rel_path:
            logging.info(
                f"File at path '{file_path}' is not in the gcode path"
                ", metadata extraction aborted")
            mevt = Event()
            mevt.set()
            return mevt
        path_info = self.file_manager.get_path_info(file_path)
        ext = os.path.splitext(file_path)[-1].lower()
        if ext == ".ufp":
            rel_path = os.path.splitext(rel_path)[0] + ".gcode"
            path_info['ufp_path'] = file_path
        return self.gcode_metadata.parse_metadata(
            rel_path, path_info, notify=True)

    def _handle_move_timeout(self, cookie, is_dir):
        if cookie not in self.pending_moves:
            return
        parent_node, name, hdl = self.pending_moves.pop(cookie)
        item_path = os.path.join(parent_node.get_path(), name)
        root = parent_node.get_root()
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

    def _schedule_pending_move(self, evt, parent_node, is_dir):
        item_path = os.path.join(parent_node.get_path(), evt.name)
        root = parent_node.get_root()
        self.clear_metadata(root, item_path, is_dir)
        hdl = IOLoop.current().call_later(
            INOTIFY_MOVE_TIME, self._handle_move_timeout,
            evt.cookie, is_dir)
        self.pending_moves[evt.cookie] = (parent_node, evt.name, hdl)

    def _handle_inotify_read(self, fd, events):
        if events & IOLoop.ERROR:
            logging.info("INotify Read Error")
            return
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
            if not self.event_loop_busy:
                self.event_loop_busy = True
                IOLoop.current().spawn_callback(self._process_inotify_events)

    async def _process_inotify_events(self):
        while self.pending_inotify_events:
            evt = self.pending_inotify_events.pop(0)
            node = self.watched_nodes[evt.wd]
            if evt.mask & iFlags.ISDIR:
                await self._process_dir_event(evt, node)
            else:
                await self._process_file_event(evt, node)
        self.event_loop_busy = False

    async def _process_dir_event(self, evt, node):
        if evt.name and evt.name[0] == ".":
            # ignore changes to the hidden directories
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
                IOLoop.current().remove_timeout(hdl)
                await prev_parent.move_child_node(child_name, evt.name, node)
            else:
                # Moved from an unwatched directory, for our
                # purposes this is the same as creating a
                # directory
                node.create_child_node(evt.name)

    async def _process_file_event(self, evt, node):
        ext = os.path.splitext(evt.name)[-1].lower()
        root = node.get_root()
        node_path = node.get_path()
        if root == "gcodes" and ext not in VALID_GCODE_EXTS:
            # Don't notify files with invalid gcode extensions
            return
        if evt.mask & iFlags.CREATE:
            logging.debug(f"Inotify file create: {root}, "
                          f"{node_path}, {evt.name}")
            node.schedule_file_event(evt.name, "create_file")
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
            file_path = os.path.join(node_path, evt.name)
            if root == "gcodes":
                mevt = self.parse_gcode_metadata(file_path)
                await mevt.wait()
            moved_evt = self.pending_moves.pop(evt.cookie, None)
            if moved_evt is not None:
                # Moved from a currently watched directory
                prev_parent, prev_name, hdl = moved_evt
                IOLoop.current().remove_timeout(hdl)
                prev_root = prev_parent.get_root()
                prev_path = os.path.join(prev_parent.get_path(), prev_name)
                self.notify_filelist_changed(
                    "move_file", root, file_path,
                    prev_root, prev_path)
            else:
                self.notify_filelist_changed(
                    "create_file", root, file_path)
        elif evt.mask & iFlags.MODIFY:
            node.schedule_file_event(evt.name, "modify_file")
        elif evt.mask & iFlags.CLOSE_WRITE:
            file_path = os.path.join(node_path, evt.name)
            logging.debug(f"Inotify writable file closed: {file_path}")
            # Only process files that have been created or modified
            await node.complete_file_write(evt.name)

    def notify_filelist_changed(self, action, root, full_path,
                                source_root=None, source_path=None):
        rel_path = self.file_manager.get_relative_path(root, full_path)
        file_info = {'size': 0, 'modified': 0}
        if os.path.exists(full_path):
            file_info = self.file_manager.get_path_info(full_path)
        file_info['path'] = rel_path
        file_info['root'] = root
        result = {'action': action, 'item': file_info}
        if source_path is not None and source_root is not None:
            src_rel_path = self.file_manager.get_relative_path(
                source_root, source_path)
            result['source_item'] = {'path': src_rel_path, 'root': source_root}
        self.server.send_event("file_manager:filelist_changed", result)

    def close(self):
        IOLoop.current().remove_handler(self.inotify.fileno())
        for watch in self.watched_nodes.keys():
            try:
                self.inotify.rm_watch(watch)
            except Exception:
                pass


METADATA_NAMESPACE = "gcode_metadata"
METADATA_VERSION = 3

class MetadataStorage:
    def __init__(self, server, gc_path, database):
        self.server = server
        self.gc_path = gc_path
        database.register_local_namespace(METADATA_NAMESPACE)
        self.mddb = database.wrap_namespace(
            METADATA_NAMESPACE, parse_keys=False)
        version = database.get_item(
            "moonraker", "file_manager.metadata_version", 0)
        if version != METADATA_VERSION:
            # Clear existing metadata when version is bumped
            for fname in self.mddb.keys():
                self.remove_file_metadata(fname)
            database.insert_item(
                "moonraker", "file_manager.metadata_version",
                METADATA_VERSION)
        self.pending_requests = {}
        self.events = {}
        self.busy = False
        if self.gc_path:
            # Check for removed gcode files while moonraker was shutdown
            for fname in list(self.mddb.keys()):
                fpath = os.path.join(self.gc_path, fname)
                if not os.path.isfile(fpath):
                    self.remove_file_metadata(fname)
                    logging.info(f"Pruned file: {fname}")
                    continue

    def update_gcode_path(self, path):
        if path == self.gc_path:
            return
        self.mddb.clear()
        self.gc_path = path

    def get(self, key, default=None):
        return self.mddb.get(key, default)

    def __getitem__(self, key):
        return self.mddb[key]

    def _has_valid_data(self, fname, path_info):
        if path_info.get('ufp_path', None) is not None:
            # UFP files always need processing
            return False
        mdata = self.mddb.get(fname, {'size': "", 'modified': 0})
        for field in ['size', 'modified']:
            if mdata[field] != path_info.get(field, None):
                return False
        return True

    def remove_directory_metadata(self, dir_name):
        if dir_name[-1] != "/":
            dir_name += "/"
        for fname in list(self.mddb.keys()):
            if fname.startswith(dir_name):
                self.remove_file_metadata(fname)

    def remove_file_metadata(self, fname):
        metadata = self.mddb.pop(fname, None)
        if metadata is None:
            return
        # Delete associated thumbnails
        fdir = os.path.dirname(os.path.join(self.gc_path, fname))
        if "thumbnails" in metadata:
            for thumb in metadata["thumbnails"]:
                path = thumb.get("relative_path", None)
                if path is None:
                    continue
                thumb_path = os.path.join(fdir, path)
                if not os.path.isfile(thumb_path):
                    continue
                try:
                    os.remove(thumb_path)
                except Exception:
                    logging.debug(f"Error removing thumb at {thumb_path}")

    def parse_metadata(self, fname, path_info, notify=False):
        mevt = Event()
        if fname in self.pending_requests or \
                self._has_valid_data(fname, path_info):
            # request already pending or not necessary
            mevt.set()
            return mevt
        self.pending_requests[fname] = (path_info, notify, mevt)
        if self.busy:
            return mevt
        self.busy = True
        IOLoop.current().spawn_callback(self._process_metadata_update)
        return mevt

    async def _process_metadata_update(self):
        while self.pending_requests:
            fname, (path_info, notify, mevt) = \
                self.pending_requests.popitem()
            if self._has_valid_data(fname, path_info):
                mevt.set()
                continue
            ufp_path = path_info.get('ufp_path', None)
            retries = 3
            while retries:
                try:
                    await self._run_extract_metadata(fname, ufp_path, notify)
                except Exception:
                    logging.exception("Error running extract_metadata.py")
                    retries -= 1
                else:
                    break
            else:
                if ufp_path is None:
                    self.mddb[fname] = {
                        'size': path_info.get('size', 0),
                        'modified': path_info.get('modified', 0),
                        'print_start_time': None,
                        'job_id': None
                    }
                logging.info(
                    f"Unable to extract medatadata from file: {fname}")
            mevt.set()
        self.busy = False

    async def _run_extract_metadata(self, filename, ufp_path, notify):
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
        shell_command = self.server.lookup_component('shell_command')
        scmd = shell_command.build_shell_command(cmd, log_stderr=True)
        result = await scmd.run_with_response(timeout=timeout)
        try:
            decoded_resp = json.loads(result.strip())
        except Exception:
            logging.debug(f"Invalid metadata response:\n{result}")
            raise
        path = decoded_resp['file']
        metadata = decoded_resp['metadata']
        if not metadata:
            # This indicates an error, do not add metadata for this
            raise self.server.error("Unable to extract metadata")
        metadata.update({'print_start_time': None, 'job_id': None})
        self.mddb[path] = dict(metadata)
        metadata['filename'] = path
        if notify:
            self.server.send_event(
                "file_manager:metadata_update", metadata)

def load_component(config):
    return FileManager(config)
