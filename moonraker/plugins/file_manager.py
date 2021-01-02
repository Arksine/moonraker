# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import shutil
import io
import zipfile
import logging
import json
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.locks import Event

VALID_GCODE_EXTS = ['.gcode', '.g', '.gco']
FULL_ACCESS_ROOTS = ["gcodes", "config"]
METADATA_SCRIPT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "../../scripts/extract_metadata.py"))

class FileManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_paths = {}
        self.gcode_metadata = MetadataStorage(self.server)
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
            log_path = os.path.normpath(os.path.expanduser(log_file))
            self.server.register_static_file_handler(
                "klippy.log", log_path, force=True)

    def register_directory(self, root, path):
        if path is None:
            return False
        path = os.path.normpath(os.path.expanduser(path))
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
                # scan metadata
                self.gcode_metadata.update_gcode_path(path)
                try:
                    self.get_file_list("gcodes")
                except Exception:
                    logging.exception(
                        f"Unable to initialize gcode metadata")
        return True

    def get_sd_directory(self):
        return self.file_paths.get('gcodes', "")

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
            dir_info = self._list_directory(dir_path)
            # Check to see if a filelist update is necessary
            for f in dir_info['files']:
                fname = os.path.join(rel_path, f['filename'])
                ext = os.path.splitext(f['filename'])[-1].lower()
                if root != 'gcodes' or ext not in VALID_GCODE_EXTS:
                    continue
                self.gcode_metadata.parse_metadata(
                    fname, f['size'], f['modified'], notify=True)
                metadata = self.gcode_metadata.get(fname, None)
                if metadata is not None and is_extended:
                    f.update(metadata)
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

    def _list_directory(self, path):
        if not os.path.isdir(path):
            raise self.server.error(
                f"Directory does not exist ({path})")
        flist = {'dirs': [], 'files': []}
        for fname in os.listdir(path):
            full_path = os.path.join(path, fname)
            path_info = self._get_path_info(full_path)
            if os.path.isdir(full_path):
                path_info['dirname'] = fname
                flist['dirs'].append(path_info)
            elif os.path.isfile(full_path):
                path_info['filename'] = fname
                flist['files'].append(path_info)
        usage = shutil.disk_usage(path)
        flist['disk_usage'] = usage._asdict()
        return flist

    def _get_path_info(self, path):
        modified = os.path.getmtime(path)
        size = os.path.getsize(path)
        path_info = {'modified': modified, 'size': size}
        return path_info

    async def process_file_upload(self, request):
        # lookup root file path
        root = self._get_argument(request, 'root', "gcodes")
        if root == "gcodes":
            result = await self._do_gcode_upload(request)
        elif root in FULL_ACCESS_ROOTS:
            result = self._do_standard_upload(request, root)
        else:
            raise self.server.error(f"Invalid root request: {root}")
        return result

    async def _do_gcode_upload(self, request):
        start_print = print_ongoing = False
        root_path = self.file_paths.get("gcodes", "")
        if not root_path:
            raise self.server.error("Gcodes root not available")
        start_print = self._get_argument(request, 'print', "false") == "true"
        upload = self._get_upload_info(request, root_path)
        fparts = os.path.splitext(upload['full_path'])
        is_ufp = fparts[-1].lower() == ".ufp"
        # Verify that the operation can be done if attempting to upload a gcode
        try:
            check_path = upload['full_path']
            if is_ufp:
                check_path = fparts[0] + ".gcode"
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
        self._write_file(upload, is_ufp)
        # Fetch Metadata
        finfo = self._get_path_info(upload['full_path'])
        evt = self.gcode_metadata.parse_metadata(
            upload['filename'], finfo['size'], finfo['modified'])
        await evt.wait()
        if start_print:
            # Make a Klippy Request to "Start Print"
            klippy_apis = self.server.lookup_plugin('klippy_apis')
            try:
                await klippy_apis.start_print(upload['filename'])
            except self.server.error:
                # Attempt to start print failed
                start_print = False
        self.notify_filelist_changed(
            'upload_file', upload['filename'], "gcodes")
        return {'result': upload['filename'], 'print_started': start_print}

    def _do_standard_upload(self, request, root):
        path = self.file_paths.get(root, None)
        if path is None:
            raise self.server.error(f"Unknown root path: {root}")
        upload = self._get_upload_info(request, path)
        self._write_file(upload)
        self.notify_filelist_changed('upload_file', upload['filename'], root)
        return {'result': upload['filename']}

    def _get_argument(self, request, name, default=None):
        args = request.arguments.get(name, None)
        if args is not None:
            return args[0].decode().strip()
        return default

    def _get_upload_info(self, request, root_path):
        # check relative path
        dir_path = self._get_argument(request, 'path', "")
        # fetch the upload from the request
        if len(request.files) != 1:
            raise self.server.error(
                "Bad Request, can only process a single file upload")
        f_list = list(request.files.values())[0]
        if len(f_list) != 1:
            raise self.server.error(
                "Bad Request, can only process a single file upload")
        upload = f_list[0]
        if os.path.isfile(root_path):
            filename = os.path.basename(root_path)
            full_path = root_path
            dir_path = ""
        else:
            filename = upload['filename'].strip().lstrip("/")
            if dir_path:
                filename = os.path.join(dir_path, filename)
            full_path = os.path.normpath(os.path.join(root_path, filename))
        # Validate the path.  Don't allow uploads to a parent of the root
        if not full_path.startswith(root_path):
            raise self.server.error(
                f"Cannot write to path: {full_path}")
        return {
            'filename': filename,
            'body': upload['body'],
            'dir_path': dir_path,
            'full_path': full_path}

    def _write_file(self, upload, unzip_ufp=False):
        try:
            if upload['dir_path']:
                os.makedirs(os.path.dirname(
                    upload['full_path']), exist_ok=True)
            if unzip_ufp:
                self._unzip_ufp(upload)
            else:
                with open(upload['full_path'], 'wb') as fh:
                    fh.write(upload['body'])
        except Exception:
            raise self.server.error("Unable to save file", 500)

    # UFP Extraction Implementation inspired by by GitHub user @cdkeito
    def _unzip_ufp(self, upload):
        base_name = os.path.splitext(
            os.path.basename(upload['filename']))[0]
        working_dir = os.path.dirname(upload['full_path'])
        thumb_dir = os.path.join(working_dir, "thumbs")
        ufp_bytes = io.BytesIO(upload['body'])
        gc_bytes = img_bytes = None
        with zipfile.ZipFile(ufp_bytes) as zf:
            gc_bytes = zf.read("/3D/model.gcode")
            try:
                img_bytes = zf.read("/Metadata/thumbnail.png")
            except Exception:
                img_bytes = None
        if gc_bytes is not None:
            gc_name = base_name + ".gcode"
            gc_path = os.path.join(working_dir, gc_name)
            with open(gc_path, "wb") as gc_file:
                gc_file.write(gc_bytes)
            # update upload file name to extracted gcode file
            upload['full_path'] = gc_path
            upload['filename'] = os.path.join(
                os.path.dirname(upload['filename']), gc_name)
        else:
            raise self.server.error(
                f"UFP file {upload['filename']} does not "
                "contain a gcode file")
        if img_bytes is not None:
            thumb_name = base_name + ".png"
            thumb_path = os.path.join(thumb_dir, thumb_name)
            try:
                if not os.path.exists(thumb_dir):
                    os.mkdir(thumb_dir)
                with open(thumb_path, "wb") as thumb_file:
                    thumb_file.write(img_bytes)
            except Exception:
                logging.exception("Unable to write Image")

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

        flist = self.get_file_list("gcodes")
        return self.gcode_metadata.get(filename, flist.get(filename, {}))

    def list_dir(self, directory, simple_format=False):
        # List a directory relative to its root.  Currently the only
        # Supported root is "gcodes"
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

class MetadataStorage:
    def __init__(self, server):
        self.server = server
        self.metadata = {}
        self.pending_requests = {}
        self.events = {}
        self.script_response = None
        self.busy = False
        self.gc_path = os.path.expanduser("~")
        self.prune_cb = PeriodicCallback(
            self.prune_metadata, METADATA_PRUNE_TIME)

    def update_gcode_path(self, path):
        if path == self.gc_path:
            return
        self.metadata = {}
        self.gc_path = path
        if not self.prune_cb.is_running():
            self.prune_cb.start()

    def close(self):
        self.prune_cb.stop()

    def get(self, key, default=None):
        if key not in self.metadata:
            return default
        return dict(self.metadata[key])

    def __getitem__(self, key):
        return dict(self.metadata[key])

    def _handle_script_response(self, result):
        try:
            proc_resp = json.loads(result.strip())
        except Exception:
            logging.exception("file_manager: unable to load metadata")
            logging.debug(result)
            return
        proc_log = proc_resp.get('log', [])
        for log_msg in proc_log:
            logging.info(log_msg)
        if 'file' in proc_resp:
            self.script_response = proc_resp

    def prune_metadata(self):
        for fname in list(self.metadata.keys()):
            fpath = os.path.join(self.gc_path, fname)
            if not os.path.exists(fpath):
                del self.metadata[fname]
                logging.info(f"Pruned file: {fname}")
                continue

    def _has_valid_data(self, fname, fsize, modified):
        mdata = self.metadata.get(fname, {'size': "", 'modified': 0})
        return mdata['size'] == fsize and mdata['modified'] == modified

    def remove_file(self, fname):
        self.metadata.pop(fname)

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
                self.metadata[fname] = {'size': fsize, 'modified': modified}
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
        scmd = shell_command.build_shell_command(
            cmd, self._handle_script_response)
        self.script_response = None
        await scmd.run(timeout=10.)
        if self.script_response is None:
            raise self.server.error("Unable to extract metadata")
        path = self.script_response['file']
        metadata = self.script_response['metadata']
        if not metadata:
            # This indicates an error, do not add metadata for this
            raise self.server.error("Unable to extract metadata")
        self.metadata[path] = dict(metadata)
        metadata['filename'] = path
        if notify:
            self.server.send_event(
                "file_manager:metadata_update", metadata)

def load_plugin(config):
    return FileManager(config)
