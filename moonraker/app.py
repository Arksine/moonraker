# Klipper Web Server Rest API
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import os
import mimetypes
import logging
import tornado
from inspect import isclass
from tornado.routing import Rule, PathMatches, AnyMatches
from utils import ServerError
from websockets import WebsocketManager, WebSocket
from authorization import AuthorizedRequestHandler, AuthorizedFileHandler
from authorization import Authorization

# Max Upload Size of 200MB
MAX_UPLOAD_SIZE = 200 * 1024 * 1024

# These endpoints are reserved for klippy/server communication only and are
# not exposed via http or the websocket
RESERVED_ENDPOINTS = [
    "list_endpoints", "moonraker/check_ready", "moonraker/get_configuration"
]


# Status objects require special parsing
def _status_parser(request):
    query_args = request.query_arguments
    args = {}
    for key, vals in query_args.items():
        parsed = []
        for v in vals:
            if v:
                parsed += v.decode().split(',')
        args[key] = parsed
    return args

# Built-in Query String Parser
def _default_parser(request):
    query_args = request.query_arguments
    args = {}
    for key, vals in query_args.items():
        if len(vals) != 1:
            raise tornado.web.HTTPError(404, "Invalid Query String")
        args[key] = vals[0].decode()
    return args

class MutableRouter(tornado.web.ReversibleRuleRouter):
    def __init__(self, application):
        self.application = application
        self.pattern_to_rule = {}
        super(MutableRouter, self).__init__(None)

    def get_target_delegate(self, target, request, **target_params):
        if isclass(target) and issubclass(target, tornado.web.RequestHandler):
            return self.application.get_handler_delegate(
                request, target, **target_params)

        return super(MutableRouter, self).get_target_delegate(
            target, request, **target_params)

    def has_rule(self, pattern):
        return pattern in self.pattern_to_rule

    def add_handler(self, pattern, target, target_params):
        if pattern in self.pattern_to_rule:
            self.remove_handler(pattern)
        new_rule = Rule(PathMatches(pattern), target, target_params)
        self.pattern_to_rule[pattern] = new_rule
        self.rules.append(new_rule)

    def remove_handler(self, pattern):
        rule = self.pattern_to_rule.pop(pattern, None)
        if rule is not None:
            try:
                self.rules.remove(rule)
            except Exception:
                logging.exception("Unable to remove rule: %s" % (pattern))

class APIDefinition:
    def __init__(self, endpoint, http_uri, ws_method,
                 request_methods, parser):
        self.endpoint = endpoint
        self.uri = http_uri
        self.ws_method = ws_method
        if not isinstance(request_methods, list):
            request_methods = [request_methods]
        self.request_methods = request_methods
        self.parser = parser

class MoonrakerApp:
    def __init__(self, server, args):
        self.server = server
        self.tornado_server = None
        self.api_cache = {}
        self.registered_base_handlers = []

        # Set Up Websocket and Authorization Managers
        self.wsm = WebsocketManager(server)
        self.auth = Authorization(args.apikey)

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')

        # Set up HTTP only requests
        self.mutable_router = MutableRouter(self)
        app_handlers = [
            (AnyMatches(), self.mutable_router),
            (r"/websocket", WebSocket,
             {'wsm': self.wsm, 'auth': self.auth}),
            (r"/api/version", EmulateOctoprintHandler,
             {'server': server, 'auth': self.auth})]

        self.app = tornado.web.Application(
            app_handlers,
            serve_traceback=args.debug,
            websocket_ping_interval=10,
            websocket_ping_timeout=30,
            enable_cors=False)
        self.get_handler_delegate = self.app.get_handler_delegate

        # Register handlers
        self.register_static_file_handler("moonraker.log", args.logfile)
        self.auth.register_handlers(self)

    def listen(self, host, port):
        self.tornado_server = self.app.listen(
            port, address=host, max_body_size=MAX_UPLOAD_SIZE,
            xheaders=True)

    async def close(self):
        if self.tornado_server is not None:
            self.tornado_server.stop()
        await self.wsm.close()
        self.auth.close()

    def load_config(self, config):
        if 'enable_cors' in config:
            self.app.settings['enable_cors'] = config['enable_cors']
        self.auth.load_config(config)

    def register_remote_handler(self, endpoint):
        if endpoint in RESERVED_ENDPOINTS:
            return
        api_def = self.api_cache.get(
            endpoint, self._create_api_definition(endpoint))
        if api_def.uri in self.registered_base_handlers:
            # reserved handler or already registered
            return
        logging.info("Registering remote endpoint: (%s) %s" % (
            " ".join(api_def.request_methods), api_def.uri))
        self.wsm.register_handler(api_def)
        params = {}
        params['server'] = self.server
        params['auth'] = self.auth
        params['methods'] = api_def.request_methods
        params['arg_parser'] = api_def.parser
        params['remote_callback'] = api_def.endpoint
        self.mutable_router.add_handler(
            api_def.uri, RemoteRequestHandler, params)
        self.registered_base_handlers.append(api_def.uri)

    def register_local_handler(self, uri, ws_method, request_methods,
                               callback, http_only=False):
        if uri in self.registered_base_handlers:
            return
        api_def = self._create_api_definition(
            uri, ws_method, request_methods)
        logging.info("Registering local endpoint: (%s) %s" % (
            " ".join(request_methods), uri))
        if not http_only:
            self.wsm.register_handler(api_def, callback)
        params = {}
        params['server'] = self.server
        params['auth'] = self.auth
        params['methods'] = request_methods
        params['arg_parser'] = api_def.parser
        params['callback'] = callback
        self.mutable_router.add_handler(uri, LocalRequestHandler, params)
        self.registered_base_handlers.append(uri)

    def register_static_file_handler(self, pattern, file_path,
                                     can_delete=False, op_check_cb=None):
        if pattern[0] != "/":
            pattern = "/server/files/" + pattern
        if os.path.isfile(file_path):
            pattern += '()'
        elif os.path.isdir(file_path):
            if pattern[-1] != "/":
                pattern += "/"
            pattern += "(.*)"
        else:
            logging.info("Invalid file path: %s" % (file_path))
            return
        methods = ['GET']
        if can_delete:
            methods.append('DELETE')
        params = {
            'server': self.server, 'auth': self.auth,
            'path': file_path, 'methods': methods, 'op_check_cb': op_check_cb}
        self.mutable_router.add_handler(pattern, FileRequestHandler, params)

    def register_upload_handler(self, pattern):
        params = {'server': self.server, 'auth': self.auth}
        self.mutable_router.add_handler(pattern, FileUploadHandler, params)

    def remove_handler(self, endpoint):
        api_def = self.api_cache.get(endpoint)
        if api_def is not None:
            self.wsm.remove_handler(api_def.uri)
            self.mutable_router.remove_handler(api_def.ws_method)

    def _create_api_definition(self, endpoint, ws_method=None,
                               request_methods=['GET', 'POST']):
        if endpoint in self.api_cache:
            return self.api_cache[endpoint]
        if endpoint[0] == '/':
            uri = endpoint
        else:
            uri = "/printer/" + endpoint
        if ws_method is None:
            ws_method = uri[1:].replace('/', '_')
        if endpoint.startswith("objects/"):
            parser = _status_parser
        else:
            parser = _default_parser
        api_def = APIDefinition(endpoint, uri, ws_method,
                                request_methods, parser)
        self.api_cache[endpoint] = api_def
        return api_def

# ***** Dynamic Handlers*****
class RemoteRequestHandler(AuthorizedRequestHandler):
    def initialize(self, remote_callback, server, auth,
                   methods, arg_parser):
        super(RemoteRequestHandler, self).initialize(server, auth)
        self.remote_callback = remote_callback
        self.methods = methods
        self.query_parser = arg_parser

    async def get(self):
        if 'GET' in self.methods:
            await self._process_http_request('GET')
        else:
            raise tornado.web.HTTPError(405)

    async def post(self):
        if 'POST' in self.methods:
            await self._process_http_request('POST')
        else:
            raise tornado.web.HTTPError(405)

    async def _process_http_request(self, method):
        args = {}
        if self.request.query:
            args = self.query_parser(self.request)
        request = self.server.make_request(
            self.remote_callback, method, args)
        result = await request.wait()
        if isinstance(result, ServerError):
            raise tornado.web.HTTPError(
                result.status_code, str(result))
        self.finish({'result': result})

class LocalRequestHandler(AuthorizedRequestHandler):
    def initialize(self, callback, server, auth,
                   methods, arg_parser):
        super(LocalRequestHandler, self).initialize(server, auth)
        self.callback = callback
        self.methods = methods
        self.query_parser = arg_parser

    async def get(self):
        if 'GET' in self.methods:
            await self._process_http_request('GET')
        else:
            raise tornado.web.HTTPError(405)

    async def post(self):
        if 'POST' in self.methods:
            await self._process_http_request('POST')
        else:
            raise tornado.web.HTTPError(405)

    async def delete(self):
        if 'DELETE' in self.methods:
            await self._process_http_request('DELETE')
        else:
            raise tornado.web.HTTPError(405)

    async def _process_http_request(self, method):
        args = {}
        if self.request.query:
            args = self.query_parser(self.request)
        try:
            result = await self.callback(self.request.path, method, args)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, str(e))
        self.finish({'result': result})


class FileRequestHandler(AuthorizedFileHandler):
    def initialize(self, server, auth, path, methods,
                   op_check_cb=None, default_filename=None):
        super(FileRequestHandler, self).initialize(
            server, auth, path, default_filename)
        self.methods = methods
        self.op_check_cb = op_check_cb

    def set_extra_headers(self, path):
        # The call below shold never return an empty string,
        # as the path should have already been validated to be
        # a file
        basename = os.path.basename(self.absolute_path)
        self.set_header(
            "Content-Disposition", "attachment; filename=%s" % (basename))

    async def delete(self, path):
        if 'DELETE' not in self.methods:
            raise tornado.web.HTTPError(405)

        # Use the same method Tornado uses to validate the path
        self.path = self.parse_url_path(path)
        del path  # make sure we don't refer to path instead of self.path again
        absolute_path = self.get_absolute_path(self.root, self.path)
        self.absolute_path = self.validate_absolute_path(
            self.root, absolute_path)

        if self.op_check_cb is not None:
            try:
                await self.op_check_cb(self.absolute_path)
            except ServerError as e:
                if e.status_code == 403:
                    raise tornado.web.HTTPError(
                        403, "File is loaded, DELETE not permitted")

        os.remove(self.absolute_path)
        base = self.request.path.lstrip("/").split("/")[2]
        filename = self.path.lstrip("/")
        file_manager = self.server.lookup_plugin('file_manager')
        file_manager.notify_filelist_changed(filename, 'removed', base)
        self.finish({'result': filename})

class FileUploadHandler(AuthorizedRequestHandler):
    def initialize(self, server, auth):
        super(FileUploadHandler, self).initialize(server, auth)

    async def post(self):
        file_manager = self.server.lookup_plugin('file_manager')
        try:
            result = await file_manager.process_file_upload(self.request)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, str(e))
        self.finish(result)


class EmulateOctoprintHandler(AuthorizedRequestHandler):
    def get(self):
        self.finish({
            'server': "1.1.1",
            'api': "0.1",
            'text': "OctoPrint Upload Emulator"})
