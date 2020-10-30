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

        # Register Klippy Configuration Path
        config_path = config.get('config_path', None)
        if config_path is not None:
            ret = self.register_directory('config', config_path)
            if not ret:
                raise config.error(
                    "Option 'config_path' is not a valid directory")

    def update_fixed_paths(self, paths):
        if paths == self.fixed_path_args:
            # No change in fixed paths
            return
        self.fixed_path_args = dict(paths)
        str_paths = "\n".join([f"{k}: {v}" for k, v in paths.items()])
        logging.debug(f"\nUpdating Fixed Paths:\n{str_paths}")

        # Register path for example configs
        klipper_path = paths.get('klipper_path', None)
        if klipper_path is not None:
            example_cfg_path = os.path.join(klipper_path, "config")
            self.register_directory("config_examples", example_cfg_path)

        # Register log path
        log_file = paths.get('log_file')
        log_path = os.path.normpath(os.path.expanduser(log_file))
        self.server.register_static_file_handler("klippy.log", log_path)

    def register_directory(self, base, path):
        if path is None:
            return False
        home = os.path.expanduser('~')
        path = os.path.normpath(os.path.expanduser(path))
        if not os.path.isdir(path) or not path.startswith(home) or \
                path == home:
            logging.info(
                f"\nSupplied path ({path}) for ({base}) not valid. Please\n"
                "check that the path exists and is a subfolder in the HOME\n"
                "directory. Note that the path may not BE the home directory.")
            return False
        if path != self.file_paths.get(base, ""):
            self.file_paths[base] = path
            self.server.register_static_file_handler(base, path)
            try:
                self._update_file_list(base=base)
            except Exception:
                logging.exception(
                    f"Unable to initialize file list: <{base}>")
        return True

    def get_sd_directory(self):
        return self.file_paths.get('gcodes', "")

    def get_fixed_path_args(self):
        return dict(self.fixed_path_args)

    async def _handle_filelist_request(self, path, method, args):
        root = args.get('root', "gcodes")
        return self.get_file_list(format_list=True, base=root)

    async def _handle_metadata_request(self, path, method, args):
        requested_file = args.get('filename')
        metadata = self.gcode_metadata.get(requested_file)
        if metadata is None:
            raise self.server.error(
                f"Metadata not available for <{requested_file}>", 404)
        metadata['filename'] = requested_file
        return metadata

    async def _handle_directory_request(self, path, method, args):
        directory = args.get('path', "gcodes")
        base, url_path, dir_path = self._convert_path(directory)
        method = method.upper()
        if method == 'GET':
            # Get list of files and subdirectories for this target
            dir_info = self._list_directory(dir_path)
            # Check to see if a filelist update is necessary
            for f in dir_info['files']:
                fname = os.path.join(url_path, f['filename'])
                ext = f['filename'][f['filename'].rfind('.')+1:]
                if base == 'gcodes' and ext not in VALID_GCODE_EXTS:
                    continue
                finfo = self.file_lists[base].get(fname, None)
                if finfo is None or f['modified'] != finfo['modified']:
                    # Either a new file found or file has changed, update
                    # internal file list
                    self._update_file_list(base, do_notify=True)
                    break
            return dir_info
        elif method == 'POST' and base in FULL_ACCESS_ROOTS:
            # Create a new directory
            try:
                os.mkdir(dir_path)
            except Exception as e:
                raise self.server.error(str(e))
            self.notify_filelist_changed("create_dir", url_path, base)
        elif method == 'DELETE' and base in FULL_ACCESS_ROOTS:
            # Remove a directory
            if directory.strip("/") == base:
                raise self.server.error(
                    "Cannot delete root directory")
            if not os.path.isdir(dir_path):
                raise self.server.error(
                    f"Directory does not exist ({directory})")
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
            self.notify_filelist_changed("delete_dir", url_path, base)
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

    def _convert_path(self, url_path):
        parts = url_path.strip("/").split("/")
        if not parts:
            raise self.server.error(f"Invalid path: {url_path}")
        base = parts[0]
        if base not in self.file_paths:
            raise self.server.error(f"Invalid base path ({base})")
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
                f"Destination path is read-only: {dest_base}")
        if not os.path.exists(source_path):
            raise self.server.error(f"File {source_path} does not exist")
        # make sure the destination is not in use
        if os.path.exists(dest_path):
            await self._handle_operation_check(dest_path)
        action = op_result = ""
        if path == "/server/files/move":
            if source_base not in FULL_ACCESS_ROOTS:
                raise self.server.error(
                    f"Source path is read-only, cannot move: {source_base}")
            # if moving the file, make sure the source is not in use
            await self._handle_operation_check(source_path)
            try:
                op_result = shutil.move(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            action = "move_item"
        elif path == "/server/files/copy":
            try:
                if os.path.isdir(source_path):
                    op_result = shutil.copytree(source_path, dest_path)
                else:
                    op_result = shutil.copy2(source_path, dest_path)
            except Exception as e:
                raise self.server.error(str(e))
            action = "copy_item"
        if op_result != dest_path:
            dst_url_path = os.path.join(
                dst_url_path, os.path.basename(op_result))
        self.notify_filelist_changed(
            action, dst_url_path, dest_base,
            {'path': src_url_path, 'root': source_base})
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
        return flist

    def _get_path_info(self, path):
        modified = os.path.getmtime(path)
        size = os.path.getsize(path)
        path_info = {'modified': modified, 'size': size}
        return path_info

    def _update_file_list(self, base='gcodes', do_notify=False):
        # Use os.walk find files in sd path and subdirs
        path = self.file_paths.get(base, None)
        if path is None:
            msg = f"No known path for root: {base}"
            logging.info(msg)
            raise self.server.error(msg)
        elif not os.path.isdir(path):
            msg = f"Cannot generate file list for root: {base}"
            logging.info(msg)
            raise self.server.error(msg)
        logging.info(f"Updating File List <{base}>...")
        new_list = {}
        for root, dirs, files in os.walk(path, followlinks=True):
            for name in files:
                ext = name[name.rfind('.')+1:]
                if base == 'gcodes' and ext not in VALID_GCODE_EXTS:
                    continue
                full_path = os.path.join(root, name)
                r_path = full_path[len(path) + 1:]
                new_list[r_path] = self._get_path_info(full_path)
        self.file_lists[base] = new_list
        if base == 'gcodes':
            self.gcode_metadata.refresh_metadata(new_list, path, do_notify)
        return dict(new_list)

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
        base_path = self.file_paths.get("gcodes", "")
        if not base_path:
            raise self.server.error("Gcodes root not available")
        start_print = self._get_argument(request, 'print', "false") == "true"
        upload = self._get_upload_info(request, base_path)
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
                ext = fname[fname.rfind('.')+1:]
                if root == "gcodes" and ext in VALID_GCODE_EXTS:
                    simple_list.append(fname)
            return simple_list
        return flist

    async def _handle_file_delete(self, path, method, args):
        file_path = args.get("path")
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
        self.notify_filelist_changed('delete_file', filename, root)
        return filename

    def notify_filelist_changed(self, action, fname, base, source_item={}):
        self._update_file_list(base, do_notify=True)
        file_info = dict(self.file_lists[base].get(
            fname, {'size': 0, 'modified': 0}))
        file_info.update({'path': fname, 'root': base})
        result = {'action': action, 'item': file_info}
        if source_item:
            result.update({'source_item': source_item})
        self.server.send_event("file_manager:filelist_changed", result)

class MetadataStorage:
    def __init__(self, server):
        self.server = server
        self.lock = Lock()
        self.metadata = {}
        self.script_response = None

    def get(self, key, default=None):
        return self.metadata.get(key, default)

    def __getitem__(self, key):
        return self.metadata[key]

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

    def refresh_metadata(self, filelist, gc_path, do_notify=False):
        IOLoop.current().spawn_callback(
            self._do_metadata_update, filelist, gc_path, do_notify)

    async def _do_metadata_update(self, filelist, gc_path, do_notify=False):
        async with self.lock:
            exisiting_data = {}
            update_list = []
            for fname, fdata in filelist.items():
                mdata = self.metadata.get(fname, {})
                if mdata.get('size', "") == fdata.get('size') \
                        and mdata.get('modified', 0) == fdata.get('modified'):
                    # file metadata has already been extracted
                    exisiting_data[fname] = mdata
                else:
                    update_list.append(fname)
            self.metadata = exisiting_data
            for fname in update_list:
                retries = 3
                while retries:
                    try:
                        await self._extract_metadata(fname, gc_path, do_notify)
                    except Exception:
                        logging.exception("Error running extract_metadata.py")
                        retries -= 1
                    else:
                        break
                else:
                    logging.info(
                        f"Unable to extract medatadata from file: {fname}")

    async def _extract_metadata(self, filename, path, do_notify=False):
        cmd = " ".join([sys.executable, METADATA_SCRIPT, "-p",
                        path, "-f", "'" + filename + "'"])
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(
            cmd, self._handle_script_response)
        self.script_response = None
        await scmd.run(timeout=4.)
        if self.script_response is None:
            raise self.server.error("Unable to extract metadata")
        path = self.script_response['file']
        metadata = self.script_response['metadata']
        if not metadata:
            # This indicates an error, do not add metadata for this
            raise self.server.error("Unable to extract metadata")
        self.metadata[path] = dict(metadata)
        metadata['filename'] = path
        if do_notify:
            self.server.send_event(
                "file_manager:metadata_update", metadata)

def load_plugin(config):
    return FileManager(config)
