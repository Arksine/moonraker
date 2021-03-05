# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import shutil
import zipfile
import logging
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.locks import Event

VALID_GCODE_EXTS = ['.gcode', '.g', '.gco', '.ufp']
FULL_ACCESS_ROOTS = ["gcodes", "config"]
METADATA_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "../../scripts/extract_metadata.py"))

class FileManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_paths = {}
        database = self.server.load_plugin(config, "database")
        gc_path = database.get_item("moonraker", "file_manager.gcode_path", "")
        self.gcode_metadata = MetadataStorage(self.server, gc_path, database)
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
                database = self.server.lookup_plugin(
                    "database").wrap_namespace("moonraker")
                database["file_manager.gcode_path"] = path
                # scan for metadata changes
                self.gcode_metadata.update_gcode_path(path)
                try:
                    self.get_file_list("gcodes")
                except Exception:
                    logging.exception(
                        f"Unable to initialize gcode metadata")
        return True

    def get_sd_directory(self):
        return self.file_paths.get('gcodes', "")

    def get_registered_dirs(self):
        return list(self.file_paths.keys())

    def get_fixed_path_args(self):
        return dict(self.fixed_path_args)

    async def _handle_filelist_request(self, web_request):
        root = web_request.get_str('root', "gcodes")
        return self.get_file_list(root, list_format=True, notify=True)

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
        root, rel_path, dir_path = self._convert_path(directory)
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
            self.notify_filelist_changed("create_dir", rel_path, root)
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
                if root == "gcodes":
                    self.gcode_metadata.prune_metadata()
            else:
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    raise self.server.error(str(e))
            self.notify_filelist_changed("delete_dir", rel_path, root)
        else:
            raise self.server.error("Operation Not Supported", 405)
        return "ok"

    async def _handle_operation_check(self, requested_path):
        # Get virtual_sdcard status
        klippy_apis = self.server.lookup_plugin('klippy_apis')
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

    def _convert_path(self, request_path):
        # Parse the root, relative path, and disk path from a remote request
        parts = request_path.strip("/").split("/")
        if not parts:
            raise self.server.error(f"Invalid path: {request_path}")
        root = parts[0]
        if root not in self.file_paths:
            raise self.server.error(f"Invalid root path ({root})")
        disk_path = self.file_paths[root]
        rel_path = ""
        if len(parts) > 1:
            rel_path = "/".join(parts[1:])
            disk_path = os.path.join(disk_path, rel_path)
        return root, rel_path, disk_path

    async def _handle_file_move_copy(self, web_request):
        source = web_request.get_str("source")
        destination = web_request.get_str("dest")
        ep = web_request.get_endpoint()
        if source is None:
            raise self.server.error("File move/copy request issing source")
        if destination is None:
            raise self.server.error(
                "File move/copy request missing destination")
        source_root, src_rel_path, source_path = self._convert_path(source)
        dest_root, dst_rel_path, dest_path = self._convert_path(destination)
        if dest_root not in FULL_ACCESS_ROOTS:
            raise self.server.error(
                f"Destination path is read-only: {dest_root}")
        if not os.path.exists(source_path):
            raise self.server.error(f"File {source_path} does not exist")
        # make sure the destination is not in use
        if os.path.exists(dest_path):
            await self._handle_operation_check(dest_path)
        action = op_result = ""
        if ep == "/server/files/move":
            if source_root not in FULL_ACCESS_ROOTS:
                raise self.server.error(
                    f"Source path is read-only, cannot move: {source_root}")
            # if moving the file, make sure the source is not in use
            await self._handle_operation_check(source_path)
            try:
                op_result = shutil.move(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            if source_root == "gcodes":
                if os.path.isdir(op_result):
                    self.gcode_metadata.prune_metadata()
                else:
                    self.gcode_metadata.remove_file(src_rel_path)
            action = "move_item"
        elif ep == "/server/files/copy":
            try:
                if os.path.isdir(source_path):
                    op_result = shutil.copytree(source_path, dest_path)
                else:
                    op_result = shutil.copy2(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            action = "copy_item"
        if op_result != dest_path:
            dst_rel_path = os.path.join(
                dst_rel_path, os.path.basename(op_result))
        self.notify_filelist_changed(
            action, dst_rel_path, dest_root,
            {'path': src_rel_path, 'root': source_root})
        return "ok"

    def _list_directory(self, path, is_extended=False):
        if not os.path.isdir(path):
            raise self.server.error(
                f"Directory does not exist ({path})")
        flist = {'dirs': [], 'files': []}
        for fname in os.listdir(path):
            full_path = os.path.join(path, fname)
            if not os.path.exists(full_path):
                continue
            path_info = self._get_path_info(full_path)
            if os.path.isdir(full_path):
                path_info['dirname'] = fname
                flist['dirs'].append(path_info)
            elif os.path.isfile(full_path):
                path_info['filename'] = fname
                # Check to see if a filelist update is necessary
                ext = os.path.splitext(fname)[-1].lower()
                gc_path = self.file_paths.get('gcodes', None)
                if gc_path is not None and full_path.startswith(gc_path) and \
                        ext in VALID_GCODE_EXTS:
                    if ext == ".ufp":
                        try:
                            full_path = self._process_ufp_from_refresh(
                                full_path)
                        except Exception:
                            logging.exception("Error processing ufp file")
                            continue
                        path_info = self._get_path_info(full_path)
                        path_info['filename'] = os.path.split(full_path)[-1]
                    rel_path = os.path.relpath(full_path, start=gc_path)
                    self.gcode_metadata.parse_metadata(
                        rel_path, path_info['size'], path_info['modified'],
                        notify=True)
                    metadata = self.gcode_metadata.get(rel_path, None)
                    if metadata is not None and is_extended:
                        path_info.update(metadata)
                flist['files'].append(path_info)
        usage = shutil.disk_usage(path)
        flist['disk_usage'] = usage._asdict()
        return flist

    def _get_path_info(self, path):
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
        ioloop = IOLoop.current()
        with ThreadPoolExecutor(max_workers=1) as tpe:
            await ioloop.run_in_executor(
                tpe, self._process_uploaded_file, upload_info)
        # Fetch Metadata
        finfo = self._get_path_info(upload_info['dest_path'])
        evt = self.gcode_metadata.parse_metadata(
            upload_info['filename'], finfo['size'], finfo['modified'])
        await evt.wait()
        if start_print:
            # Make a Klippy Request to "Start Print"
            klippy_apis = self.server.lookup_plugin('klippy_apis')
            try:
                await klippy_apis.start_print(upload_info['filename'])
            except self.server.error:
                # Attempt to start print failed
                start_print = False
        self.notify_filelist_changed(
            'upload_file', upload_info['filename'], "gcodes")
        return {
            'result': upload_info['filename'],
            'print_started': start_print
        }

    async def _finish_standard_upload(self, upload_info):
        ioloop = IOLoop.current()
        with ThreadPoolExecutor(max_workers=1) as tpe:
            await ioloop.run_in_executor(
                tpe, self._process_uploaded_file, upload_info)
        self.notify_filelist_changed(
            'upload_file', upload_info['filename'], upload_info['root'])
        return {'result': upload_info['filename']}

    def _process_uploaded_file(self, upload_info):
        try:
            if upload_info['dir_path']:
                os.makedirs(os.path.dirname(
                    upload_info['dest_path']), exist_ok=True)
            if upload_info['unzip_ufp']:
                self._unzip_ufp(upload_info['tmp_file_path'],
                                upload_info['dest_path'])
            else:
                shutil.move(upload_info['tmp_file_path'],
                            upload_info['dest_path'])
        except Exception:
            raise self.server.error("Unable to save file", 500)

    # UFP Extraction Implementation inspired by GitHub user @cdkeito
    def _unzip_ufp(self, ufp_path, dest_path):
        gc_bytes = img_bytes = None
        with zipfile.ZipFile(ufp_path) as zf:
            gc_bytes = zf.read("/3D/model.gcode")
            try:
                img_bytes = zf.read("/Metadata/thumbnail.png")
            except Exception:
                img_bytes = None
        if gc_bytes is not None:
            with open(dest_path, "wb") as gc_file:
                gc_file.write(gc_bytes)
        else:
            raise self.server.error(
                f"UFP file {dest_path} does not "
                "contain a gcode file")
        if img_bytes is not None:
            thumb_name = os.path.splitext(
                os.path.basename(dest_path))[0] + ".png"
            thumb_dir = os.path.join(os.path.dirname(dest_path), "thumbs")
            thumb_path = os.path.join(thumb_dir, thumb_name)
            try:
                if not os.path.exists(thumb_dir):
                    os.mkdir(thumb_dir)
                with open(thumb_path, "wb") as thumb_file:
                    thumb_file.write(img_bytes)
            except Exception:
                logging.exception("Unable to write Image")
        try:
            os.remove(ufp_path)
        except Exception:
            logging.exception(f"Error removing ufp file: {ufp_path}")

    def _process_ufp_from_refresh(self, ufp_path):
        dest_path = os.path.splitext(ufp_path)[0] + ".gcode"
        self._unzip_ufp(ufp_path, dest_path)
        return dest_path

    def get_file_list(self, root, list_format=False, notify=False):
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
                st = os.stat(os.path.join(dir_path, dname))
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
                if ext == ".ufp":
                    try:
                        full_path = self._process_ufp_from_refresh(full_path)
                    except Exception:
                        logging.exception("Error processing ufp file")
                        continue
                fname = full_path[len(path) + 1:]
                finfo = self._get_path_info(full_path)
                filelist[fname] = finfo
                if root == 'gcodes':
                    self.gcode_metadata.parse_metadata(
                        fname, finfo['size'], finfo['modified'], notify)
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
            self.gcode_metadata.remove_file(filename)
        os.remove(full_path)
        self.notify_filelist_changed('delete_file', filename, root)
        return filename

    def notify_filelist_changed(self, action, fname, root, source_item={}):
        flist = self.get_file_list(root, notify=True)
        file_info = flist.get(fname, {'size': 0, 'modified': 0})
        file_info.update({'path': fname, 'root': root})
        result = {'action': action, 'item': file_info}
        if source_item:
            result.update({'source_item': source_item})
        self.server.send_event("file_manager:filelist_changed", result)

    def close(self):
        self.gcode_metadata.close()


METADATA_PRUNE_TIME = 600000
METADATA_NAMESPACE = "gcode_metadata"

class MetadataStorage:
    def __init__(self, server, gc_path, database):
        self.server = server
        database.register_local_namespace(METADATA_NAMESPACE)
        self.mddb = database.wrap_namespace(
            METADATA_NAMESPACE, parse_keys=False)
        self.pending_requests = {}
        self.events = {}
        self.busy = False
        self.gc_path = gc_path
        self.prune_cb = PeriodicCallback(
            self.prune_metadata, METADATA_PRUNE_TIME)

    def update_gcode_path(self, path):
        if path == self.gc_path:
            return
        self.mddb.clear()
        self.gc_path = path
        if not self.prune_cb.is_running():
            self.prune_cb.start()

    def close(self):
        self.prune_cb.stop()

    def get(self, key, default=None):
        return self.mddb.get(key, default)

    def __getitem__(self, key):
        return self.mddb[key]

    def prune_metadata(self):
        for fname in list(self.mddb.keys()):
            fpath = os.path.join(self.gc_path, fname)
            if not os.path.exists(fpath):
                del self.mddb[fname]
                logging.info(f"Pruned file: {fname}")
                continue

    def _has_valid_data(self, fname, fsize, modified):
        mdata = self.mddb.get(fname, {'size': "", 'modified': 0})
        return mdata['size'] == fsize and mdata['modified'] == modified

    def remove_file(self, fname):
        try:
            del self.mddb[fname]
        except Exception:
            pass

    def parse_metadata(self, fname, fsize, modified, notify=False):
        evt = Event()
        if fname in self.pending_requests or \
                self._has_valid_data(fname, fsize, modified):
            # request already pending or not necessary
            evt.set()
            return evt
        self.pending_requests[fname] = (fsize, modified, notify, evt)
        if self.busy:
            return evt
        self.busy = True
        IOLoop.current().spawn_callback(self._process_metadata_update)
        return evt

    async def _process_metadata_update(self):
        while self.pending_requests:
            fname, (fsize, modified, notify, evt) = \
                self.pending_requests.popitem()
            if self._has_valid_data(fname, fsize, modified):
                evt.set()
                continue
            retries = 3
            while retries:
                try:
                    await self._run_extract_metadata(fname, notify)
                except Exception:
                    logging.exception("Error running extract_metadata.py")
                    retries -= 1
                else:
                    break
            else:
                self.mddb[fname] = {'size': fsize, 'modified': modified}
                logging.info(
                    f"Unable to extract medatadata from file: {fname}")
            evt.set()
        self.busy = False

    async def _run_extract_metadata(self, filename, notify):
        # Escape single quotes in the file name so that it may be
        # properly loaded
        filename = filename.replace("\"", "\\\"")
        cmd = " ".join([sys.executable, METADATA_SCRIPT, "-p",
                        self.gc_path, "-f", f"\"{filename}\""])
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, log_stderr=True)
        result = await scmd.run_with_response(timeout=10.)
        if result is None:
            raise self.server.error(f"Metadata extraction error")
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
        self.mddb[path] = dict(metadata)
        metadata['filename'] = path
        if notify:
            self.server.send_event(
                "file_manager:metadata_update", metadata)

def load_plugin(config):
    return FileManager(config)
