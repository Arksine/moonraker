# Enhanced gcode file management and analysis
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os
import shutil
import time
import logging
import json
from tornado.ioloop import IOLoop
from tornado.locks import Lock

VALID_GCODE_EXTS = ['gcode', 'g', 'gco']
PYTHON_BIN = os.path.expanduser("~/moonraker-env/bin/python")
METADATA_SCRIPT = os.path.join(
    os.path.dirname(__file__), "../../scripts/extract_metadata.py")

class FileManager:
    def __init__(self, server):
        self.server = server
        self.file_paths = {}
        self.file_lists = {}
        self.gcode_metadata = {}
        self.metadata_lock = Lock()
        self.server.register_endpoint(
            "/server/files/list", "file_list", ['GET'],
            self._handle_filelist_request)
        self.server.register_endpoint(
            "/server/files/metadata", "file_metadata", ['GET'],
            self._handle_metadata_request)
        self.server.register_endpoint(
            "/server/files/directory", "directory", ['GET', 'POST', 'DELETE'],
            self._handle_directory_request)

    def _register_static_files(self, gcode_path):
        self.server.register_static_file_handler(
            '/server/files/gcodes/', gcode_path, can_delete=True,
            op_check_cb=self._handle_operation_check)
        self.server.register_upload_handler(
            '/server/files/upload', gcode_path,
            op_check_cb=self._handle_operation_check)
        self.server.register_upload_handler(
            '/api/files/local', gcode_path,
            op_check_cb=self._handle_operation_check)

    def load_config(self, config):
        sd = config.get('sd_path', None)
        if sd is not None:
            sd = os.path.normpath(os.path.expanduser(sd))
            if sd != self.file_paths.get('gcodes', ""):
                self.file_paths['gcodes'] = sd
                self._update_file_list()
                self._register_static_files(sd)

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
        directory = args.get('path', "gcodes").strip('/')
        dir_parts = directory.split("/")
        base = dir_parts[0]
        target = "/".join(dir_parts[1:])
        if base not in self.file_paths:
            raise self.server.error("Invalid base path (%s)" % (base))
        root_path = self.file_paths[base]
        dir_path = os.path.join(root_path, target)
        method = method.upper()
        if method == 'GET':
            # Get list of files and subdirectories for this target
            return self._list_directory(dir_path)
        elif method == 'POST' and base == "gcodes":
            # Create a new directory
            try:
                os.mkdir(dir_path)
            except Exception as e:
                raise self.server.error(str(e))
        elif method == 'DELETE' and base == "gcodes":
            # Remove a directory
            if directory == base:
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
        else:
            raise self.server.error("Operation Not Supported", 405)
        return "ok"

    async def _handle_operation_check(self, requested_path):
        # Get virtual_sdcard status
        request = self.server.make_request(
            "objects/status", 'GET', {'virtual_sdcard': []})
        result = await request.wait()
        if isinstance(result, self.server.error):
            raise result
        vsd = result.get('virtual_sdcard', {})
        loaded_file = vsd.get('filename', "")
        gc_path = self.file_paths.get('gcodes', "")
        full_path = os.path.join(gc_path, loaded_file)
        if os.path.isdir(requested_path):
            # Check to see of the loaded file is in the reques
            if full_path.startswith(requested_path):
                raise self.server.error("File currently in use", 403)
        elif full_path == requested_path:
            raise self.server.error("File currently in use", 403)
        ongoing = vsd.get('total_duration', 0.) > 0.
        return ongoing

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
                cmd = " ".join([PYTHON_BIN, METADATA_SCRIPT, "-p",
                                gc_path, "-f", fname])
                shell_command = self.server.lookup_plugin('shell_command')
                scmd = shell_command.build_shell_command(
                    cmd, self._shell_proc_callback)
                try:
                    await scmd.run(timeout=4.)
                except Exception:
                    logging.exception("Error running extract_metadata.py")

    def _update_file_list(self, base='gcodes'):
        # Use os.walk find files in sd path and subdirs
        path = self.file_paths.get(base, "")
        if path is None:
            logging.info("No sd_path set, cannot update")
            return
        logging.info("Updating File List...")
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

def load_plugin(server):
    return FileManager(server)
