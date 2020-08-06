# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import sys
import shutil
import time
import logging
import json
from tornado.ioloop import IOLoop
from tornado.locks import Lock

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']
FULL_ACCESS_ROOTS = ["gcodes", "config"]
METADATA_SCRIPT = os.path.join(
    os.path.dirname(__file__), "../../scripts/extract_metadata.py")

class FileManager:
    def __init__(self, config):
        self.server = config.get_server()
        self.file_paths = {}
        self.file_lists = {}
        self.gcode_metadata = {}
        self.metadata_lock = Lock()

        self.server.register_event_handler(
            "server:moonraker_available", self.update_mutable_paths)

        # Register file management endpoints
        self.server.register_endpoint(
            "/server/files/list", "file_list", ['GET'],
            self._handle_filelist_request)
        self.server.register_endpoint(
            "/server/files/metadata", "file_metadata", ['GET'],
            self._handle_metadata_request)
        self.server.register_endpoint(
            "/server/files/directory", "directory", ['GET', 'POST', 'DELETE'],
            self._handle_directory_request)
        self.server.register_endpoint(
            "/server/files/move", "file_move", ['POST'],
            self._handle_file_move_copy)
        self.server.register_endpoint(
            "/server/files/copy", "file_copy", ['POST'],
            self._handle_file_move_copy)
        # Register APIs to handle file uploads
        self.server.register_upload_handler("/server/files/upload")
        self.server.register_upload_handler("/api/files/local")

        # Register Klippy Configuration Path
        config_path = config.get('config_path', None)
        if config_path is not None:
            cfg_path = os.path.normpath(os.path.expanduser(config_path))
            if not os.path.isdir(cfg_path):
                raise config.error(
                    "Option 'config_path' is not a valid directory")
            self.file_paths['config'] = cfg_path
            self.server.register_static_file_handler(
                "/server/files/config/", cfg_path,
                can_delete=True)
            try:
                self._update_file_list(base='config')
            except Exception:
                logging.exception("Unable to initialize config file list")

    def update_mutable_paths(self, paths):
        # Update paths from Klippy.  The sd_path can potentially change
        # location on restart.
        logging.debug("Updating Mutable Paths: %s" % (str(paths)))
        sd = paths.get('sd_path', None)
        if sd is not None:
            sd = os.path.normpath(os.path.expanduser(sd))
            if sd != self.file_paths.get('gcodes', ""):
                self.file_paths['gcodes'] = sd
                self.server.register_static_file_handler(
                    '/server/files/gcodes/', sd, can_delete=True,
                    op_check_cb=self._handle_operation_check)
            try:
                self._update_file_list()
            except Exception:
                logging.exception("Unable to initialize gcode file list")
        # Register path for example configs
        klipper_path = paths.get('klipper_path', None)
        if klipper_path is not None:
            example_cfg_path = os.path.join(klipper_path, "config")
            if example_cfg_path != self.file_paths.get("config_examples", ""):
                self.file_paths['config_examples'] = example_cfg_path
                self.server.register_static_file_handler(
                    "/server/files/config_examples/", example_cfg_path)
            try:
                self._update_file_list(base='config_examples')
            except Exception:
                logging.exception(
                    "Unable to initialize config_examples file list")

    def get_sd_directory(self):
        return self.file_paths.get('gcodes', "")

    async def _handle_filelist_request(self, path, method, args):
        root = args.get('root', "gcodes")
        return self.get_file_list(format_list=True, base=root)

    async def _handle_metadata_request(self, path, method, args):
        requested_file = args.get('filename')
        metadata = self.gcode_metadata.get(requested_file)
        if metadata is None:
            raise self.server.error(
                "Metadata not available for <%s>" % (requested_file), 404)
        metadata['filename'] = requested_file
        return metadata

    async def _handle_directory_request(self, path, method, args):
        directory = args.get('path', "gcodes")
        base, url_path, dir_path = self._convert_path(directory)
        method = method.upper()
        if method == 'GET':
            # Get list of files and subdirectories for this target
            return self._list_directory(dir_path)
        elif method == 'POST' and base in FULL_ACCESS_ROOTS:
            # Create a new directory
            try:
                os.mkdir(dir_path)
            except Exception as e:
                raise self.server.error(str(e))
            self.notify_filelist_changed(url_path, "add_directory", base)
        elif method == 'DELETE' and base in FULL_ACCESS_ROOTS:
            # Remove a directory
            if directory.strip("/") == base:
                raise self.server.error(
                    "Cannot delete root directory")
            if not os.path.isdir(dir_path):
                raise self.server.error(
                    "Directory does not exist (%s)" % (directory))
            force = args.get('force', False)
            if isinstance(force, str):
                force = force.lower() == "true"
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
            self.notify_filelist_changed(url_path, "delete_directory", base)
        else:
            raise self.server.error("Operation Not Supported", 405)
        return "ok"

    async def _handle_operation_check(self, requested_path):
        # Get virtual_sdcard status
        request = self.server.make_request(
            "objects/status", 'GET', {'print_stats': []})
        result = await request.wait()
        if isinstance(result, self.server.error):
            raise result
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

    def _convert_path(self, url_path):
        parts = url_path.strip("/").split("/")
        if not parts:
            raise self.server.error("Invalid path: " % (url_path))
        base = parts[0]
        if base not in self.file_paths:
            raise self.server.error("Invalid base path (%s)" % (base))
        root_path = local_path = self.file_paths[base]
        url_path = ""
        if len(parts) > 1:
            url_path = "/".join(parts[1:])
            local_path = os.path.join(root_path, url_path)
        return base, url_path, local_path

    async def _handle_file_move_copy(self, path, method, args):
        source = args.get("source")
        destination = args.get("dest")
        if source is None:
            raise self.server.error("File move/copy request issing source")
        if destination is None:
            raise self.server.error(
                "File move/copy request missing destination")
        source_base, src_url_path, source_path = self._convert_path(source)
        dest_base, dst_url_path, dest_path = self._convert_path(destination)
        if dest_base not in FULL_ACCESS_ROOTS:
            raise self.server.error(
                "Destination path is read-only: %s" % (dest_base))
        if not os.path.exists(source_path):
            raise self.server.error("File %s does not exist" % (source_path))
        # make sure the destination is not in use
        if os.path.exists(dest_path):
            await self._handle_operation_check(dest_path)
        action = ""
        if path == "/server/files/move":
            if source_base not in FULL_ACCESS_ROOTS:
                raise self.server.error(
                    "Source path is read-only, cannot move: %s"
                    % (source_base))
            # if moving the file, make sure the source is not in use
            await self._handle_operation_check(source_path)
            try:
                shutil.move(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            action = "file_move"
        elif path == "/server/files/copy":
            try:
                if os.path.isdir(source_path):
                    shutil.copytree(source_path, dest_path)
                else:
                    shutil.copy2(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            action = "file_copy"
        self.notify_filelist_changed(
            dst_url_path, action, dest_base,
            {'prev_file': src_url_path, 'prev_root': source_base})
        return "ok"

    def _list_directory(self, path):
        if not os.path.isdir(path):
            raise self.server.error(
                "Directory does not exist (%s)" % (path))
        flist = {'dirs': [], 'files': []}
        for fname in os.listdir(path):
            full_path = os.path.join(path, fname)
            modified = time.ctime(os.path.getmtime(full_path))
            if os.path.isdir(full_path):
                flist['dirs'].append({
                    'dirname': fname,
                    'modified': modified
                })
            elif os.path.isfile(full_path):
                size = os.path.getsize(full_path)
                flist['files'].append(
                    {'filename': fname,
                     'modified': modified,
                     'size': size})
        return flist

    def _shell_proc_callback(self, result):
        try:
            proc_resp = json.loads(result.strip())
        except Exception:
            logging.exception("file_manager: unable to load metadata")
            logging.debug(result)
            return
        proc_log = proc_resp.get('log', [])
        for log_msg in proc_log:
            logging.info(log_msg)
        file_path = proc_resp.pop('file', None)
        if file_path is not None:
            self.gcode_metadata[file_path] = proc_resp.get('metadata')

    async def _update_metadata(self):
        async with self.metadata_lock:
            exisiting_data = {}
            update_list = []
            gc_files = dict(self.file_lists.get('gcodes', {}))
            gc_path = self.file_paths.get('gcodes', "")
            for fname, fdata in gc_files.items():
                mdata = self.gcode_metadata.get(fname, {})
                if mdata.get('size', "") == fdata.get('size') \
                        and mdata.get('modified', "") == fdata.get('modified'):
                    # file metadata has already been extracted
                    exisiting_data[fname] = mdata
                else:
                    update_list.append(fname)
            self.gcode_metadata = exisiting_data
            for fname in update_list:
                cmd = " ".join([sys.executable, METADATA_SCRIPT, "-p",
                                gc_path, "-f", "'" + fname + "'"])
                shell_command = self.server.lookup_plugin('shell_command')
                scmd = shell_command.build_shell_command(
                    cmd, self._shell_proc_callback)
                try:
                    await scmd.run(timeout=4.)
                except Exception:
                    logging.exception("Error running extract_metadata.py")

    def _update_file_list(self, base='gcodes'):
        # Use os.walk find files in sd path and subdirs
        path = self.file_paths.get(base, None)
        if path is None:
            msg = "No known path for root: %s" % (base)
            logging.info(msg)
            raise self.server.error(msg)
        elif not os.path.isdir(path):
            msg = "Cannot generate file list for root: %s" % (base)
            logging.info(msg)
            raise self.server.error(msg)
        logging.info("Updating File List <%s>..." % (base))
        new_list = {}
        for root, dirs, files in os.walk(path, followlinks=True):
            for name in files:
                ext = name[name.rfind('.')+1:]
                if base == 'gcodes' and ext not in VALID_GCODE_EXTS:
                    continue
                full_path = os.path.join(root, name)
                r_path = full_path[len(path) + 1:]
                size = os.path.getsize(full_path)
                modified = time.ctime(os.path.getmtime(full_path))
                new_list[r_path] = {'size': size, 'modified': modified}
        self.file_lists[base] = new_list
        if base == 'gcodes':
            ioloop = IOLoop.current()
            ioloop.spawn_callback(self._update_metadata)
        return dict(new_list)

    async def process_file_upload(self, request):
        # lookup root file path
        root = self._get_argument(request, 'root', "gcodes")
        if root == "gcodes":
            result = await self._do_gcode_upload(request)
        elif root in FULL_ACCESS_ROOTS:
            result = self._do_standard_upload(request, root)
        else:
            raise self.server.error("Invalid root request: %s" % (root))
        return result

    async def _do_gcode_upload(self, request):
        start_print = print_ongoing = False
        base_path = self.file_paths.get("gcodes", "")
        if not base_path:
            raise self.server.error("Gcodes root not available")
        start_print = self._get_argument(request, 'print', "false") == "true"
        upload = self._get_upload_info(request, base_path)
        # Verify that the operation can be done if attempting to upload a gcode
        try:
            print_ongoing = await self._handle_operation_check(
                upload['full_path'])
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
        self._write_file(upload)
        if start_print:
            # Make a Klippy Request to "Start Print"
            gcode_apis = self.server.lookup_plugin('gcode_apis')
            try:
                await gcode_apis.gcode_start_print(
                    request.path, 'POST', {'filename': upload['filename']})
            except self.server.error:
                # Attempt to start print failed
                start_print = False
        self.notify_filelist_changed(upload['filename'], 'added', "gcodes")
        return {'result': upload['filename'], 'print_started': start_print}

    def _do_standard_upload(self, request, root):
        path = self.file_paths.get(root, None)
        if path is None:
            raise self.server.error("Unknown root path: %s" % (root))
        upload = self._get_upload_info(request, path)
        self._write_file(upload)
        self.notify_filelist_changed(upload['filename'], 'added', root)
        return {'result': upload['filename']}

    def _get_argument(self, request, name, default=None):
        args = request.arguments.get(name, None)
        if args is not None:
            return args[0].decode().strip()
        return default

    def _get_upload_info(self, request, base_path):
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
        if os.path.isfile(base_path):
            filename = os.path.basename(base_path)
            full_path = base_path
            dir_path = ""
        else:
            filename = "_".join(upload['filename'].strip().split()).lstrip("/")
            if dir_path:
                filename = os.path.join(dir_path, filename)
            full_path = os.path.normpath(os.path.join(base_path, filename))
        # Validate the path.  Don't allow uploads to a parent of the root
        if not full_path.startswith(base_path):
            raise self.server.error(
                "Cannot write to path: %s" % (full_path))
        return {
            'filename': filename,
            'body': upload['body'],
            'dir_path': dir_path,
            'full_path': full_path}

    def _write_file(self, upload):
        try:
            if upload['dir_path']:
                os.makedirs(os.path.dirname(upload['full_path']), exist_ok=True)
            with open(upload['full_path'], 'wb') as fh:
                fh.write(upload['body'])
        except Exception:
            raise self.server.error("Unable to save file", 500)

    def get_file_list(self, format_list=False, base='gcodes'):
        try:
            filelist = self._update_file_list(base)
        except Exception:
            msg = "Unable to update file list"
            logging.exception(msg)
            raise self.server.error(msg)
        if format_list:
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

        flist = self.get_file_list()
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
                "Invalid Directory Request: %s" % (directory))
        path = self.file_paths[root]
        if len(parts) == 1:
            dir_path = path
        else:
            dir_path = os.path.join(path, parts[1])
        if not os.path.isdir(dir_path):
            raise self.server.error(
                "Directory does not exist (%s)" % (dir_path))
        flist = self._list_directory(dir_path)
        if simple_format:
            simple_list = []
            for dirobj in flist['dirs']:
                simple_list.append("*" + dirobj['dirname'])
            for fileobj in flist['files']:
                fname = fileobj['filename']
                ext = fname[fname.rfind('.')+1:]
                if root == "gcodes" and ext in VALID_GCODE_EXTS:
                    simple_list.append(fname)
            return simple_list
        return flist

    def delete_file(self, path):
        parts = path.split("/", 1)
        root = parts[0]
        if root not in self.file_paths or len(parts) != 2:
            raise self.server.error("Invalid file path: %s" % (path))
        root_path = self.file_paths[root]
        full_path = os.path.join(root_path, parts[1])
        if not os.path.isfile(full_path):
            raise self.server.error("Invalid file path: %s" % (path))
        os.remove(full_path)

    def notify_filelist_changed(self, fname, action, base, params={}):
        self._update_file_list(base)
        result = {'filename': fname, 'action': action, 'root': base}
        if params:
            result.update(params)
        self.server.send_event("file_manager:filelist_changed", result)

def load_plugin(config):
    return FileManager(config)
