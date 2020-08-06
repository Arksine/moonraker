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
import errno
import tornado
import tornado.netutil
import confighelper
from tornado import gen
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
        socketfile = config['cmd_args'].get('socketfile', "/tmp/moonraker")
        socketfile = os.path.normpath(os.path.expanduser(socketfile))
        self.klippy_server_sock = tornado.netutil.bind_unix_socket(
            socketfile, backlog=1)
        self.remove_server_sock = tornado.netutil.add_accept_handler(
            self.klippy_server_sock, self._handle_klippy_connection)
        self.klippy_sock = None
        self.is_klippy_connected = False
        self.is_klippy_ready = False
        self.moonraker_available = False
        self.partial_data = b""

        # Server/IOLoop
        self.server_running = False
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.io_loop = IOLoop.current()
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
            msg = "Plugin (%s) does not exist" % (plugin_name)
            logging.info(msg)
            if default == Sentinel:
                raise ServerError(msg)
            return default
        module = importlib.import_module("plugins." + plugin_name)
        try:
            load_func = getattr(module, "load_plugin")
            plugin = load_func(config)
        except Exception:
            msg = "Unable to load plugin (%s)" % (plugin_name)
            logging.info(msg)
            if default == Sentinel:
                raise ServerError(msg)
            return default
        self.plugins[plugin_name] = plugin
        logging.info("Plugin (%s) loaded" % (plugin_name))
        return plugin

    def lookup_plugin(self, plugin_name, default=Sentinel):
        plugin = self.plugins.get(plugin_name, default)
        if plugin == Sentinel:
            raise ServerError("Plugin (%s) not found" % (plugin_name))
        return plugin

    def register_event_handler(self, event, callback):
        self.events.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        events = self.events.get(event, [])
        for evt in events:
            self.io_loop.spawn_callback(evt, *args)

    def register_remote_method(self, method_name, cb):
        if method_name in self.remote_methods:
            # XXX - may want to raise an exception here
            logging.info("Remote method (%s) already registered"
                         % (method_name))
            return
        self.remote_methods[method_name] = cb

    # ***** Klippy Connection *****
    def _handle_klippy_connection(self, conn, addr):
        if self.is_klippy_connected:
            logging.info("New Connection received while Klippy Connected")
            self.close_client_sock()
        logging.info("Klippy Connection Established")
        self.is_klippy_connected = True
        conn.setblocking(0)
        self.klippy_sock = conn
        self.io_loop.add_handler(
            self.klippy_sock.fileno(), self._handle_klippy_data,
            IOLoop.READ | IOLoop.ERROR)
        # begin server iniialization
        self.init_cb.start()

    def _handle_klippy_data(self, fd, events):
        if events & IOLoop.ERROR:
            self.close_client_sock()
            return
        try:
            data = self.klippy_sock.recv(4096)
        except socket.error as e:
            # If bad file descriptor allow connection to be
            # closed by the data check
            if e.errno == errno.EBADF:
                data = b''
            else:
                return
        if data == b'':
            # Socket Closed
            self.close_client_sock()
            return
        commands = data.split(b'\x03')
        commands[0] = self.partial_data + commands[0]
        self.partial_data = commands.pop()
        for cmd in commands:
            try:
                decoded_cmd = json.loads(cmd)
                method = decoded_cmd.get('method')
                params = decoded_cmd.get('params', {})
                cb = self.remote_methods.get(method)
                if cb is not None:
                    cb(**params)
                else:
                    logging.info("Unknown command received %s" % cmd.decode())
            except Exception:
                logging.exception(
                    "Error processing Klippy Host Response: %s"
                    % (cmd.decode()))

    def klippy_send(self, data):
        # TODO: need a mutex or lock to make sure that multiple co-routines
        # do not attempt to send
        if not self.is_klippy_connected:
            return False
        retries = 10
        data = json.dumps(data).encode() + b"\x03"
        while data:
            try:
                sent = self.klippy_sock.send(data)
            except socket.error as e:
                if e.errno == errno.EBADF or e.errno == errno.EPIPE \
                        or not retries:
                    sent = 0
                else:
                    # XXX - Should pause for 1ms here
                    retries -= 1
                    continue
            retries = 10
            if sent > 0:
                data = data[sent:]
            else:
                logging.info("Error sending client data, closing socket")
                self.close_client_sock()
                return False
        return True

    async def _initialize(self):
        await self._request_endpoints()
        if not self.moonraker_available:
            await self._check_available()
        elif not self.is_klippy_ready:
            await self._check_ready()
        else:
            # Moonraker is enabled in the Klippy module
            # and Klippy is ready.  We can stop the init
            # procedure.
            self.init_cb.stop()

    async def _request_endpoints(self):
        request = self.make_request("list_endpoints", "GET", {})
        result = await request.wait()
        if not isinstance(result, ServerError):
            endpoints = result.get('hooks', {})
            static_paths = result.get('static_paths', {})
            for ep in endpoints:
                self.moonraker_app.register_remote_handler(ep)
            for sp in static_paths:
                self.moonraker_app.register_static_file_handler(
                    sp['resource_id'], sp['file_path'])

    async def _check_available(self):
        request = self.make_request(
            "moonraker/check_available", "GET", {})
        result = await request.wait()
        if not isinstance(result, ServerError):
            self.send_event("server:moonraker_available", result)
            self.moonraker_available = True
        else:
            logging.info(
                "%s\nUnable to detect Moonraker compatibility in Klipper.\n "
                "Repeated failures may indicate that the [moonraker] section\n "
                "has not been added to printer.cfg.  This may also indicate\n"
                "that Klippy has experienced an error during startup.  Check\n"
                "klippy.log for more info." % (str(result)))

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
                "%s\nKlippy info request error.  This indicates a that Klippy\n"
                "may have experienced an error during startup.  Please check\n "
                "klippy.log for more information" % (str(result)))

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
        ret = self.klippy_send(base_request.to_dict())
        if not ret:
            self.pending_requests.pop(base_request.id, None)
            base_request.notify(
                ServerError("Klippy Host not connected", 503))
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
        self.close_client_sock()
        self.close_server_sock()
        if self.server_running:
            self.server_running = False
            await self.moonraker_app.close()
            self.io_loop.stop()

    def close_client_sock(self):
        self.is_klippy_ready = False
        self.moonraker_available = False
        self.init_cb.stop()
        for request in self.pending_requests.values():
            request.notify(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        if self.is_klippy_connected:
            self.is_klippy_connected = False
            logging.info("Klippy Connection Removed")
            try:
                self.io_loop.remove_handler(self.klippy_sock.fileno())
                self.klippy_sock.close()
            except socket.error:
                logging.exception("Error Closing Client Socket")
            self.send_event("server:klippy_state_changed", "disconnect")

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
        "-s", "--socketfile", default="/tmp/moonraker", metavar='<socketfile>',
        help="file name and location for the Unix Domain Socket")
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
        msg = "Moonraker requires Python 3.7 or above.  Detected Version: %s" \
            % (sys.version)
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
