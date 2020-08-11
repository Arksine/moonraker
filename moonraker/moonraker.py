# Moonraker - HTTP/Websocket API Server for Klipper
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import argparse
import sys
import importlib
import os
import time
import socket
import logging
import json
import confighelper
from tornado import gen, iostream
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.util import TimeoutError
from tornado.locks import Event
from app import MoonrakerApp
from utils import ServerError, MoonrakerLoggingHandler

INIT_MS = 1000

CORE_PLUGINS = [
    'file_manager', 'gcode_apis', 'machine',
    'temperature_store', 'shell_command']

class Sentinel:
    pass

class Server:
    error = ServerError
    def __init__(self, args):
        config = confighelper.get_configuration(self, args)
        self.host = config.get('host', "0.0.0.0")
        self.port = config.getint('port', 7125)

        # Event initialization
        self.events = {}

        # Klippy Connection Handling
        self.klippy_address = config.get(
            'klippy_uds_address', "/tmp/klippy_uds")
        self.klippy_iostream = None
        self.is_klippy_ready = False

        # Server/IOLoop
        self.server_running = False
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.ioloop = IOLoop.current()
        self.init_cb = PeriodicCallback(self._initialize, INIT_MS)

        # Setup remote methods accessable to Klippy.  Note that all
        # registered remote methods should be of the notification type,
        # they do not return a response to Klippy after execution
        self.pending_requests = {}
        self.remote_methods = {}
        self.register_remote_method(
            'set_klippy_shutdown', self._set_klippy_shutdown)
        self.register_remote_method(
            'response', self._handle_klippy_response)
        self.register_remote_method(
            'process_gcode_response', self._process_gcode_response)
        self.register_remote_method(
            'process_status_update', self._process_status_update)

        # Plugin initialization
        self.plugins = {}
        self._load_plugins(config)

    def start(self):
        logging.info(
            "Starting Moonraker on (%s, %d)" %
            (self.host, self.port))
        self.moonraker_app.listen(self.host, self.port)
        self.server_running = True
        self.ioloop.spawn_callback(self._connect_klippy)

    # ***** Plugin Management *****
    def _load_plugins(self, config):
        # load core plugins
        for plugin in CORE_PLUGINS:
            self.load_plugin(config, plugin)

        # check for optional plugins
        opt_sections = set(config.sections()) - \
            set(['server', 'authorization', 'cmd_args'])
        for section in opt_sections:
            self.load_plugin(config[section], section, None)

    def load_plugin(self, config, plugin_name, default=Sentinel):
        if plugin_name in self.plugins:
            return self.plugins[plugin_name]
        # Make sure plugin exists
        mod_path = os.path.join(
            os.path.dirname(__file__), 'plugins', plugin_name + '.py')
        if not os.path.exists(mod_path):
            msg = f"Plugin ({plugin_name}) does not exist"
            logging.info(msg)
            if default == Sentinel:
                raise ServerError(msg)
            return default
        module = importlib.import_module("plugins." + plugin_name)
        try:
            load_func = getattr(module, "load_plugin")
            plugin = load_func(config)
        except Exception:
            msg = f"Unable to load plugin ({plugin_name})"
            logging.info(msg)
            if default == Sentinel:
                raise ServerError(msg)
            return default
        self.plugins[plugin_name] = plugin
        logging.info(f"Plugin ({plugin_name}) loaded")
        return plugin

    def lookup_plugin(self, plugin_name, default=Sentinel):
        plugin = self.plugins.get(plugin_name, default)
        if plugin == Sentinel:
            raise ServerError(f"Plugin ({plugin_name}) not found")
        return plugin

    def register_event_handler(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        events = self.events.get(event, [])
        for evt in events:
            self.ioloop.spawn_callback(evt, *args)

    def register_remote_method(self, method_name, cb):
        if method_name in self.remote_methods:
            # XXX - may want to raise an exception here
            logging.info(f"Remote method ({method_name}) already registered")
            return
        self.remote_methods[method_name] = cb

    # ***** Klippy Connection *****
    async def _connect_klippy(self):
        ksock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        kstream = iostream.IOStream(ksock)
        try:
            await kstream.connect(self.klippy_address)
        except iostream.StreamClosedError:
            # Klippy Socket Server not available
            self.ioloop.call_later(1., self._connect_klippy)
            return
        await gen.sleep(0.5)
        if kstream.closed():
            # Klippy Connection was rejected
            self.ioloop.call_later(1., self._connect_klippy)
            return
        logging.info("Klippy Connection Established")
        self.klippy_iostream = kstream
        self.klippy_iostream.set_close_callback(
            self._handle_stream_closed)
        self.ioloop.spawn_callback(
            self._read_klippy_stream, self.klippy_iostream)
        # begin server iniialization
        self.init_cb.start()

    async def _read_klippy_stream(self, stream):
        while not stream.closed():
            try:
                data = await stream.read_until(b'\x03')
            except iostream.StreamClosedError as e:
                return
            except Exception:
                logging.exception("Klippy Stream Read Error")
                continue
            try:
                decoded_cmd = json.loads(data[:-1])
                method = decoded_cmd.get('method')
                params = decoded_cmd.get('params', {})
                cb = self.remote_methods.get(method)
                if cb is not None:
                    cb(**params)
                else:
                    logging.info(f"Unknown command received: {data.decode()}")
            except Exception:
                logging.exception(
                    f"Error processing Klippy Host Response: {data.decode()}")

    def _handle_stream_closed(self):
        self.is_klippy_ready = False
        self.klippy_iostream = None
        self.init_cb.stop()
        for request in self.pending_requests.values():
            request.notify(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        logging.info("Klippy Connection Removed")
        self.send_event("server:klippy_state_changed", "disconnect")
        self.ioloop.call_later(1., self._connect_klippy)

    async def send_klippy_request(self, request):
        if self.klippy_iostream is None:
            request.notify(ServerError("Klippy Host not connected", 503))
            return
        data = json.dumps(request.to_dict()).encode() + b"\x03"
        try:
            await self.klippy_iostream.write(data)
        except iostream.StreamClosedError:
            request.notify(ServerError("Klippy Host not connected", 503))

    async def _initialize(self):
        await self._request_endpoints()
        if not self.is_klippy_ready:
            await self._check_ready()
        else:
            # Moonraker is enabled in the Klippy module
            # and Klippy is ready.  We can stop the init
            # procedure.
            await self._check_available_objects()
            self.init_cb.stop()

    async def _request_endpoints(self):
        request = self.make_request("list_endpoints", "GET", {})
        result = await request.wait()
        if not isinstance(result, ServerError):
            endpoints = result.get('hooks', {})
            static_paths = result.get('static_paths', {})
            for ep in endpoints:
                self.moonraker_app.register_remote_handler(ep)
            mutable_paths = {sp['resource_id']: sp['file_path']
                             for sp in static_paths}
            file_manager = self.lookup_plugin('file_manager')
            file_manager.update_mutable_paths(mutable_paths)

    async def _check_available_objects(self):
        request = self.make_request("objects/list", "GET", {})
        result = await request.wait()
        if not isinstance(result, ServerError):
            missing_objs = []
            for obj in ["virtual_sdcard", "display_status", "pause_resume"]:
                if obj not in result:
                    missing_objs.append(obj)
            if missing_objs:
                err_str = ", ".join([f"[{o}]" for o in missing_objs])
                logging.info(
                    f"\nWarning, unable to detect the following printer "
                    f"objects:\n{err_str}\nPlease add the the above sections "
                    f"to printer.cfg for full Moonraker functionality.")
        else:
            logging.info(
                f"{result}\nUnable to retreive Klipper Object List")

    async def _check_ready(self):
        request = self.make_request("info", "GET", {})
        result = await request.wait()
        if not isinstance(result, ServerError):
            is_ready = result.get("is_ready", False)
            if is_ready:
                self._set_klippy_ready()
            else:
                msg = result.get("message", "Klippy Not Ready")
                logging.info("\n" + msg)
        else:
            logging.info(
                f"{result}\nKlippy info request error.  This indicates that\n"
                f"Klippy may have experienced an error during startup.\n"
                f"Please check klippy.log for more information")

    def _handle_klippy_response(self, request_id, response):
        req = self.pending_requests.pop(request_id, None)
        if req is not None:
            if isinstance(response, dict) and 'error' in response:
                response = ServerError(response['message'], 400)
            req.notify(response)
        else:
            logging.info("No request matching response: " + str(response))

    def _set_klippy_ready(self):
        logging.info("Klippy ready")
        self.is_klippy_ready = True
        self.send_event("server:klippy_state_changed", "ready")

    def _set_klippy_shutdown(self):
        logging.info("Klippy has shutdown")
        self.is_klippy_ready = False
        self.send_event("server:klippy_state_changed", "shutdown")

    def _process_gcode_response(self, response):
        self.send_event("server:gcode_response", response)

    def _process_status_update(self, status):
        self.send_event("server:status_update", status)

    def make_request(self, path, method, args):
        base_request = BaseRequest(path, method, args)
        self.pending_requests[base_request.id] = base_request
        self.ioloop.spawn_callback(
            self.send_klippy_request, base_request)
        return base_request

    async def _kill_server(self):
        # XXX - Currently this function is not used.
        # Should I expose functionality to shutdown
        # or restart the server, or simply remove this?
        logging.info(
            "Shutting Down Webserver")
        for plugin in self.plugins:
            if hasattr(plugin, "close"):
                await plugin.close()
        if self.klippy_iostream is not None and \
                not self.klippy_iostream.closed():
            self.klippy_iostream.close()
        self.close_server_sock()
        if self.server_running:
            self.server_running = False
            await self.moonraker_app.close()
            self.ioloop.stop()

    def close_server_sock(self):
        try:
            self.remove_server_sock()
            self.klippy_server_sock.close()
            # XXX - remove server sock file (or use abstract?)
        except Exception:
            logging.exception("Error Closing Server Socket")

# Basic WebRequest class, easily converted to dict for json encoding
class BaseRequest:
    def __init__(self, path, method, args):
        self.id = id(self)
        self.path = path
        self.method = method
        self.args = args
        self._event = Event()
        self.response = None

    async def wait(self):
        # Log pending requests every 60 seconds
        start_time = time.time()
        while True:
            timeout = time.time() + 60.
            try:
                await self._event.wait(timeout=timeout)
            except TimeoutError:
                pending_time = time.time() - start_time
                logging.info("Request '%s %s' pending: %.2f seconds" %
                             (self.method, self.path, pending_time))
                self._event.clear()
                continue
            break
        return self.response

    def notify(self, response):
        self.response = response
        self._event.set()

    def to_dict(self):
        return {'id': self.id, 'path': self.path,
                'method': self.method, 'args': self.args}

def main():
    # Parse start arguments
    parser = argparse.ArgumentParser(
        description="Moonraker - Klipper API Server")
    parser.add_argument(
        "-c", "--configfile", default="~/moonraker.conf",
        metavar='<configfile>',
        help="Location of moonraker configuration file")
    parser.add_argument(
        "-l", "--logfile", default="/tmp/moonraker.log", metavar='<logfile>',
        help="log file name and location")
    cmd_line_args = parser.parse_args()

    # Setup Logging
    log_file = os.path.normpath(os.path.expanduser(cmd_line_args.logfile))
    cmd_line_args.logfile = log_file
    root_logger = logging.getLogger()
    file_hdlr = MoonrakerLoggingHandler(
        log_file, when='midnight', backupCount=2)
    root_logger.addHandler(file_hdlr)
    root_logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s [%(filename)s:%(funcName)s()] - %(message)s')
    file_hdlr.setFormatter(formatter)

    if sys.version_info < (3, 7):
        msg = f"Moonraker requires Python 3.7 or above.  " \
            f"Detected Version: {sys.version}"
        logging.info(msg)
        print(msg)
        exit(1)

    # Start IOLoop and Server
    io_loop = IOLoop.current()
    try:
        server = Server(cmd_line_args)
    except Exception:
        logging.exception("Moonraker Error")
        exit(1)
    try:
        server.start()
        io_loop.start()
    except Exception:
        logging.exception("Server Running Error")
    io_loop.close(True)
    logging.info("Server Shutdown")


if __name__ == '__main__':
    main()
