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
import utils
import asyncio
from tornado import iostream, gen
from tornado.ioloop import IOLoop
from tornado.util import TimeoutError
from tornado.locks import Event
from app import MoonrakerApp
from utils import ServerError

INIT_TIME = .25
LOG_ATTEMPT_INTERVAL = int(2. / INIT_TIME + .5)
MAX_LOG_ATTEMPTS = 10 * LOG_ATTEMPT_INTERVAL

CORE_PLUGINS = [
    'file_manager', 'klippy_apis', 'machine',
    'data_store', 'shell_command']

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
        self.klippy_connection = KlippyConnection(
            self.process_command, self.on_connection_closed)
        self.init_list = []
        self.init_handle = None
        self.init_attempts = 0
        self.klippy_state = "disconnected"
        self.subscriptions = {}

        # Server/IOLoop
        self.server_running = False
        self.moonraker_app = app = MoonrakerApp(config)
        self.register_endpoint = app.register_local_handler
        self.register_static_file_handler = app.register_static_file_handler
        self.register_upload_handler = app.register_upload_handler
        self.ioloop = IOLoop.current()

        self.register_endpoint(
            "/server/info", ['GET'], self._handle_info_request)
        self.register_endpoint(
            "/server/restart", ['POST'], self._handle_server_restart)

        # Setup remote methods accessable to Klippy.  Note that all
        # registered remote methods should be of the notification type,
        # they do not return a response to Klippy after execution
        self.pending_requests = {}
        self.remote_methods = {}
        self.klippy_reg_methods = []
        self.register_remote_method(
            'process_gcode_response', self._process_gcode_response,
            need_klippy_reg=False)
        self.register_remote_method(
            'process_status_update', self._process_status_update,
            need_klippy_reg=False)

        # Plugin initialization
        self.plugins = {}
        self.klippy_apis = self.load_plugin(config, 'klippy_apis')
        self._load_plugins(config)

    def start(self):
        hostname, hostport = self.get_host_info()
        logging.info(
            f"Starting Moonraker on ({self.host}, {hostport}), "
            f"Hostname: {hostname}")
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
            self.load_plugin(config, section, None)

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
            if plugin_name not in CORE_PLUGINS:
                config = config[plugin_name]
            load_func = getattr(module, "load_plugin")
            plugin = load_func(config)
        except Exception:
            msg = f"Unable to load plugin ({plugin_name})"
            logging.exception(msg)
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

    def register_remote_method(self, method_name, cb, need_klippy_reg=True):
        if method_name in self.remote_methods:
            # XXX - may want to raise an exception here
            logging.info(f"Remote method ({method_name}) already registered")
            return
        self.remote_methods[method_name] = cb
        if need_klippy_reg:
            # These methods need to be registered with Klippy
            self.klippy_reg_methods.append(method_name)

    def get_host_info(self):
        hostname = socket.gethostname()
        return hostname, self.port

    # ***** Klippy Connection *****
    async def _connect_klippy(self):
        if not self.server_running:
            return
        ret = await self.klippy_connection.connect(self.klippy_address)
        if not ret:
            self.ioloop.call_later(.25, self._connect_klippy)
            return
        # begin server iniialization
        self.ioloop.spawn_callback(self._initialize)

    def process_command(self, cmd):
        method = cmd.get('method', None)
        if method is not None:
            # This is a remote method called from klippy
            if method in self.remote_methods:
                params = cmd.get('params', {})
                self.ioloop.spawn_callback(
                    self._execute_method, method, **params)
            else:
                logging.info(f"Unknown method received: {method}")
            return
        # This is a response to a request, process
        req_id = cmd.get('id', None)
        request = self.pending_requests.pop(req_id, None)
        if request is None:
            logging.info(
                f"No request matching request ID: {req_id}, "
                f"response: {cmd}")
            return
        if 'result' in cmd:
            result = cmd['result']
            if not result:
                result = "ok"
        else:
            err = cmd.get('error', "Malformed Klippy Response")
            result = ServerError(err, 400)
        request.notify(result)

    async def _execute_method(self, method_name, **kwargs):
        try:
            ret = self.remote_methods[method_name](**kwargs)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception:
            logging.exception(f"Error running remote method: {method_name}")

    def on_connection_closed(self):
        self.init_list = []
        self.klippy_state = "disconnected"
        for request in self.pending_requests.values():
            request.notify(ServerError("Klippy Disconnected", 503))
        self.pending_requests = {}
        self.subscriptions = {}
        logging.info("Klippy Connection Removed")
        self.send_event("server:klippy_disconnect")
        if self.init_handle is not None:
            self.ioloop.remove_timeout(self.init_handle)
        if self.server_running:
            self.ioloop.call_later(.25, self._connect_klippy)

    async def _initialize(self):
        if not self.server_running:
            return
        await self._check_ready()
        await self._request_endpoints()
        # Subscribe to "webhooks"
        # Register "webhooks" subscription
        if "webhooks_sub" not in self.init_list:
            try:
                await self.klippy_apis.subscribe_objects({'webhooks': None})
            except ServerError as e:
                logging.info(f"{e}\nUnable to subscribe to webhooks object")
            else:
                logging.info("Webhooks Subscribed")
                self.init_list.append("webhooks_sub")
        # Subscribe to Gcode Output
        if "gcode_output_sub" not in self.init_list:
            try:
                await self.klippy_apis.subscribe_gcode_output()
            except ServerError as e:
                logging.info(
                    f"{e}\nUnable to register gcode output subscription")
            else:
                logging.info("GCode Output Subscribed")
                self.init_list.append("gcode_output_sub")
        if "klippy_ready" in self.init_list or \
                not self.klippy_connection.is_connected():
            # Either Klippy is ready or the connection dropped
            # during initialization.  Exit initialization
            self.init_attempts = 0
            self.init_handle = None
        else:
            self.init_attempts += 1
            self.init_handle = self.ioloop.call_later(
                INIT_TIME, self._initialize)

    async def _request_endpoints(self):
        result = await self.klippy_apis.list_endpoints(default=None)
        if result is None:
            return
        endpoints = result.get('endpoints', {})
        for ep in endpoints:
            self.moonraker_app.register_remote_handler(ep)

    async def _check_ready(self):
        send_id = "identified" not in self.init_list
        try:
            result = await self.klippy_apis.get_klippy_info(send_id)
        except ServerError as e:
            if self.init_attempts % LOG_ATTEMPT_INTERVAL == 0 and \
                    self.init_attempts <= MAX_LOG_ATTEMPTS:
                logging.info(
                    f"{e}\nKlippy info request error.  This indicates that\n"
                    f"Klippy may have experienced an error during startup.\n"
                    f"Please check klippy.log for more information")
            return
        if send_id:
            self.init_list.append("identified")
        # Update filemanager fixed paths
        fixed_paths = {k: result[k] for k in
                       ['klipper_path', 'python_path',
                        'log_file', 'config_file']}
        file_manager = self.lookup_plugin('file_manager')
        file_manager.update_fixed_paths(fixed_paths)
        self.klippy_state = result.get('state', "unknown")
        if self.klippy_state == "ready":
            await self._verify_klippy_requirements()
            logging.info("Klippy ready")
            self.init_list.append('klippy_ready')
            # register methods with klippy
            for method in self.klippy_reg_methods:
                try:
                    await self.klippy_apis.register_method(method)
                except ServerError:
                    logging.exception(f"Unable to register method '{method}'")
            self.send_event("server:klippy_ready")
        elif self.init_attempts % LOG_ATTEMPT_INTERVAL == 0 and \
                self.init_attempts <= MAX_LOG_ATTEMPTS:
            msg = result.get('state_message', "Klippy Not Ready")
            logging.info("\n" + msg)

    async def _verify_klippy_requirements(self):
        result = await self.klippy_apis.get_object_list(default=None)
        if result is None:
            logging.info(
                f"Unable to retreive Klipper Object List")
            return
        req_objs = set(["virtual_sdcard", "display_status", "pause_resume"])
        missing_objs = req_objs - set(result)
        if missing_objs:
            err_str = ", ".join([f"[{o}]" for o in missing_objs])
            logging.info(
                f"\nWarning, unable to detect the following printer "
                f"objects:\n{err_str}\nPlease add the the above sections "
                f"to printer.cfg for full Moonraker functionality.")
        if "virtual_sdcard" not in missing_objs:
            # Update the gcode path
            result = await self.klippy_apis.query_objects(
                {'configfile': None}, default=None)
            if result is None:
                logging.info(f"Unable to set SD Card path")
            else:
                config = result.get('configfile', {}).get('config', {})
                vsd_config = config.get('virtual_sdcard', {})
                vsd_path = vsd_config.get('path', None)
                if vsd_path is not None:
                    file_manager = self.lookup_plugin('file_manager')
                    file_manager.register_directory('gcodes', vsd_path)
                else:
                    logging.info(
                        "Configuration for [virtual_sdcard] not found,"
                        " unable to set SD Card path")

    def _process_gcode_response(self, response):
        self.send_event("server:gcode_response", response)

    def _process_status_update(self, eventtime, status):
        if 'webhooks' in status:
            # XXX - process other states (startup, ready, error, etc)?
            state = status['webhooks'].get('state', None)
            if state is not None:
                if state == "shutdown":
                    logging.info("Klippy has shutdown")
                    self.send_event("server:klippy_shutdown")
                self.klippy_state = state
        for conn, sub in self.subscriptions.items():
            conn_status = {}
            for name, fields in sub.items():
                if name in status:
                    val = status[name]
                    if fields is None:
                        conn_status[name] = dict(val)
                    else:
                        conn_status[name] = {
                            k: v for k, v in val.items() if k in fields}
            conn.send_status(conn_status)

    async def make_request(self, web_request):
        rpc_method = web_request.get_endpoint()
        if rpc_method == "objects/subscribe":
            return await self._request_subscripton(web_request)
        else:
            return await self._request_standard(web_request)

    async def _request_subscripton(self, web_request):
        args = web_request.get_args()
        conn = web_request.get_connection()

        # Build the subscription request from a superset of all client
        # subscriptions
        sub = args.get('objects', {})
        if conn is None:
            raise self.error(
                "No connection associated with subscription request")
        self.subscriptions[conn] = sub
        all_subs = {}
        # request superset of all client subscriptions
        for sub in self.subscriptions.values():
            for obj, items in sub.items():
                if obj in all_subs:
                    pi = all_subs[obj]
                    if items is None or pi is None:
                        all_subs[obj] = None
                    else:
                        uitems = list(set(pi) | set(items))
                        all_subs[obj] = uitems
                else:
                    all_subs[obj] = items
        args['objects'] = all_subs
        args['response_template'] = {'method': "process_status_update"}

        result = await self._request_standard(web_request)

        # prune the status response
        pruned_status = {}
        all_status = result['status']
        sub = self.subscriptions.get(conn, {})
        for obj, fields in all_status.items():
            if obj in sub:
                valid_fields = sub[obj]
                if valid_fields is None:
                    pruned_status[obj] = fields
                else:
                    pruned_status[obj] = {k: v for k, v in fields.items()
                                          if k in valid_fields}
        result['status'] = pruned_status
        return result

    async def _request_standard(self, web_request):
        rpc_method = web_request.get_endpoint()
        args = web_request.get_args()
        # Create a base klippy request
        base_request = BaseRequest(rpc_method, args)
        self.pending_requests[base_request.id] = base_request
        self.ioloop.spawn_callback(
            self.klippy_connection.send_request, base_request)
        return await base_request.wait()

    def remove_subscription(self, conn):
        self.subscriptions.pop(conn, None)

    async def _stop_server(self):
        self.server_running = False
        for name, plugin in self.plugins.items():
            if hasattr(plugin, "close"):
                ret = plugin.close()
                if asyncio.iscoroutine(ret):
                    await ret
        self.klippy_connection.close()
        while self.klippy_state != "disconnected":
            await gen.sleep(.1)
        await self.moonraker_app.close()
        self.ioloop.stop()

    async def _handle_server_restart(self, web_request):
        self.ioloop.spawn_callback(self._stop_server)
        return "ok"

    async def _handle_info_request(self, web_request):
        return {
            'klippy_connected': self.klippy_connection.is_connected(),
            'klippy_state': self.klippy_state,
            'plugins': list(self.plugins.keys())}

class KlippyConnection:
    def __init__(self, on_recd, on_close):
        self.ioloop = IOLoop.current()
        self.iostream = None
        self.on_recd = on_recd
        self.on_close = on_close

    async def connect(self, address):
        ksock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        kstream = iostream.IOStream(ksock)
        try:
            await kstream.connect(address)
        except iostream.StreamClosedError:
            return False
        logging.info("Klippy Connection Established")
        self.iostream = kstream
        self.iostream.set_close_callback(self.on_close)
        self.ioloop.spawn_callback(self._read_stream, self.iostream)
        return True

    async def _read_stream(self, stream):
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
                self.on_recd(decoded_cmd)
            except Exception:
                logging.exception(
                    f"Error processing Klippy Host Response: {data.decode()}")

    async def send_request(self, request):
        if self.iostream is None:
            request.notify(ServerError("Klippy Host not connected", 503))
            return
        data = json.dumps(request.to_dict()).encode() + b"\x03"
        try:
            await self.iostream.write(data)
        except iostream.StreamClosedError:
            request.notify(ServerError("Klippy Host not connected", 503))

    def is_connected(self):
        return self.iostream is not None and not self.iostream.closed()

    def close(self):
        if self.iostream is not None and \
                not self.iostream.closed():
            self.iostream.close()

# Basic WebRequest class, easily converted to dict for json encoding
class BaseRequest:
    def __init__(self, rpc_method, params):
        self.id = id(self)
        self.rpc_method = rpc_method
        self.params = params
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
                logging.info(
                    f"Request '{self.rpc_method}' pending: "
                    f"{pending_time:.2f} seconds")
                self._event.clear()
                continue
            break
        if isinstance(self.response, ServerError):
            raise self.response
        return self.response

    def notify(self, response):
        self.response = response
        self._event.set()

    def to_dict(self):
        return {'id': self.id, 'method': self.rpc_method,
                'params': self.params}

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
    ql = utils.setup_logging(log_file)

    if sys.version_info < (3, 7):
        msg = f"Moonraker requires Python 3.7 or above.  " \
            f"Detected Version: {sys.version}"
        logging.info(msg)
        print(msg)
        ql.stop()
        exit(1)

    # Start IOLoop and Server
    io_loop = IOLoop.current()
    estatus = 0
    while True:
        try:
            server = Server(cmd_line_args)
        except Exception:
            logging.exception("Moonraker Error")
            estatus = 1
            break
        try:
            server.start()
            io_loop.start()
        except Exception:
            logging.exception("Server Running Error")
            estatus = 1
            break
        # Since we are running outside of the the server
        # it is ok to use a blocking sleep here
        time.sleep(.5)
        logging.info("Attempting Server Restart...")
    io_loop.close(True)
    logging.info("Server Shutdown")
    ql.stop()
    exit(estatus)


if __name__ == '__main__':
    main()
