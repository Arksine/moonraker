# Klipper Web Server Rest API
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import os
import mimetypes
import logging
import json
import tornado
import tornado.iostream
import tornado.httputil
from inspect import isclass
from tornado.escape import url_unescape
from tornado.routing import Rule, PathMatches, AnyMatches
from tornado.log import access_log
from utils import ServerError
from websockets import WebRequest, WebsocketManager, WebSocket
from authorization import AuthorizedRequestHandler, AuthorizedFileHandler
from authorization import Authorization
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.targets import FileTarget, ValueTarget

# These endpoints are reserved for klippy/server communication only and are
# not exposed via http or the websocket
RESERVED_ENDPOINTS = [
    "list_endpoints", "gcode/subscribe_output",
    "register_remote_method"
]

# 50 MiB Max Standard Body Size
MAX_BODY_SIZE = 50 * 1024 * 1024
EXCLUDED_ARGS = ["_", "token", "connection_id"]
DEFAULT_KLIPPY_LOG_PATH = "/tmp/klippy.log"

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
                logging.exception(f"Unable to remove rule: {pattern}")

class APIDefinition:
    def __init__(self, endpoint, http_uri, ws_methods,
                 request_methods, need_object_parser):
        self.endpoint = endpoint
        self.uri = http_uri
        self.ws_methods = ws_methods
        if not isinstance(request_methods, list):
            request_methods = [request_methods]
        self.request_methods = request_methods
        self.need_object_parser = need_object_parser

class MoonrakerApp:
    def __init__(self, config):
        self.server = config.get_server()
        self.tornado_server = None
        self.api_cache = {}
        self.registered_base_handlers = []
        self.max_upload_size = config.getint('max_upload_size', 1024)
        self.max_upload_size *= 1024 * 1024

        # Set Up Websocket and Authorization Managers
        self.wsm = WebsocketManager(self.server)
        self.auth = Authorization(config['authorization'])

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')
        mimetypes.add_type('text/plain', '.cfg')
        debug = config.getboolean('enable_debug_logging', False)
        log_level = logging.DEBUG if debug else logging.INFO
        logging.getLogger().setLevel(log_level)
        app_args = {
            'serve_traceback': debug,
            'websocket_ping_interval': 10,
            'websocket_ping_timeout': 30,
            'parent': self,
            'default_handler_class': AuthorizedErrorHandler,
            'default_handler_args': {}
        }
        if not debug:
            app_args['log_function'] = self.log_release_mode

        # Set up HTTP only requests
        self.mutable_router = MutableRouter(self)
        app_handlers = [
            (AnyMatches(), self.mutable_router),
            (r"/websocket", WebSocket)]
        self.app = tornado.web.Application(app_handlers, **app_args)
        self.get_handler_delegate = self.app.get_handler_delegate

        # Register handlers
        logfile = config['system_args'].get('logfile')
        if logfile:
            self.register_static_file_handler(
                "moonraker.log", logfile, force=True)
        self.register_static_file_handler(
            "klippy.log", DEFAULT_KLIPPY_LOG_PATH, force=True)
        self.auth.register_handlers(self)

    def listen(self, host, port):
        self.tornado_server = self.app.listen(
            port, address=host, max_body_size=MAX_BODY_SIZE,
            xheaders=True)

    def log_release_mode(self, handler):
        status_code = handler.get_status()
        if status_code in [200, 204]:
            # don't log OK and No Content
            return
        if status_code < 400:
            log_method = access_log.info
        elif status_code < 500:
            log_method = access_log.warning
        else:
            log_method = access_log.error
        request_time = 1000.0 * handler.request.request_time()
        log_method("%d %s %.2fms", status_code,
                   handler._request_summary(), request_time)

    def get_server(self):
        return self.server

    def get_auth(self):
        return self.auth

    def get_websocket_manager(self):
        return self.wsm

    async def close(self):
        if self.tornado_server is not None:
            self.tornado_server.stop()
            await self.tornado_server.close_all_connections()
        await self.wsm.close()
        self.auth.close()

    def register_remote_handler(self, endpoint):
        if endpoint in RESERVED_ENDPOINTS:
            return
        api_def = self._create_api_definition(endpoint)
        if api_def.uri in self.registered_base_handlers:
            # reserved handler or already registered
            return
        logging.info(
            f"Registering remote endpoint - "
            f"HTTP: ({' '.join(api_def.request_methods)}) {api_def.uri}; "
            f"Websocket: {', '.join(api_def.ws_methods)}")
        self.wsm.register_remote_handler(api_def)
        params = {}
        params['methods'] = api_def.request_methods
        params['callback'] = api_def.endpoint
        params['need_object_parser'] = api_def.need_object_parser
        self.mutable_router.add_handler(
            api_def.uri, DynamicRequestHandler, params)
        self.registered_base_handlers.append(api_def.uri)

    def register_local_handler(self, uri, request_methods,
                               callback, protocol=["http", "websocket"],
                               wrap_result=True):
        if uri in self.registered_base_handlers:
            return
        api_def = self._create_api_definition(
            uri, request_methods, is_remote=False)
        msg = "Registering local endpoint"
        if "http" in protocol:
            msg += f" - HTTP: ({' '.join(request_methods)}) {uri}"
            params = {}
            params['methods'] = request_methods
            params['callback'] = callback
            params['wrap_result'] = wrap_result
            params['is_remote'] = False
            self.mutable_router.add_handler(uri, DynamicRequestHandler, params)
            self.registered_base_handlers.append(uri)
        if "websocket" in protocol:
            msg += f" - Websocket: {', '.join(api_def.ws_methods)}"
            self.wsm.register_local_handler(api_def, callback)
        logging.info(msg)

    def register_static_file_handler(self, pattern, file_path, force=False):
        if pattern[0] != "/":
            pattern = "/server/files/" + pattern
        if os.path.isfile(file_path) or force:
            pattern += '()'
        elif os.path.isdir(file_path):
            if pattern[-1] != "/":
                pattern += "/"
            pattern += "(.*)"
        else:
            logging.info(f"Invalid file path: {file_path}")
            return
        logging.debug(f"Registering static file: ({pattern}) {file_path}")
        params = {'path': file_path}
        self.mutable_router.add_handler(pattern, FileRequestHandler, params)

    def register_upload_handler(self, pattern):
        self.mutable_router.add_handler(
            pattern, FileUploadHandler,
            {'max_upload_size': self.max_upload_size})

    def remove_handler(self, endpoint):
        api_def = self.api_cache.get(endpoint)
        if api_def is not None:
            self.wsm.remove_handler(api_def.uri)
            self.mutable_router.remove_handler(api_def.ws_method)

    def _create_api_definition(self, endpoint, request_methods=[],
                               is_remote=True):
        if endpoint in self.api_cache:
            return self.api_cache[endpoint]
        if endpoint[0] == '/':
            uri = endpoint
        elif is_remote:
            uri = "/printer/" + endpoint
        else:
            uri = "/server/" + endpoint
        ws_methods = []
        if is_remote:
            # Remote requests accept both GET and POST requests.  These
            # requests execute the same callback, thus they resolve to
            # only a single websocket method.
            ws_methods.append(uri[1:].replace('/', '.'))
            request_methods = ['GET', 'POST']
        else:
            name_parts = uri[1:].split('/')
            if len(request_methods) > 1:
                for req_mthd in request_methods:
                    func_name = req_mthd.lower() + "_" + name_parts[-1]
                    ws_methods.append(".".join(name_parts[:-1] + [func_name]))
            else:
                ws_methods.append(".".join(name_parts))
        if not is_remote and len(request_methods) != len(ws_methods):
            raise self.server.error(
                "Invalid API definition.  Number of websocket methods must "
                "match the number of request methods")
        need_object_parser = endpoint.startswith("objects/")
        api_def = APIDefinition(endpoint, uri, ws_methods,
                                request_methods, need_object_parser)
        self.api_cache[endpoint] = api_def
        return api_def

class DynamicRequestHandler(AuthorizedRequestHandler):
    def initialize(self, callback, methods, need_object_parser=False,
                   is_remote=True, wrap_result=True):
        super(DynamicRequestHandler, self).initialize()
        self.callback = callback
        self.methods = methods
        self.wrap_result = wrap_result
        self._do_request = self._do_remote_request if is_remote \
            else self._do_local_request
        self._parse_query = self._object_parser if need_object_parser \
            else self._default_parser

    # Converts query string values with type hints
    def _convert_type(self, value, hint):
        type_funcs = {
            "int": int, "float": float,
            "bool": lambda x: x.lower() == "true",
            "json": json.loads}
        if hint not in type_funcs:
            logging.info(f"No conversion method for type hint {hint}")
            return value
        func = type_funcs[hint]
        try:
            converted = func(value)
        except Exception:
            logging.exception("Argument conversion error: Hint: "
                              f"{hint}, Arg: {value}")
            return value
        return converted

    def _default_parser(self):
        args = {}
        for key in self.request.arguments.keys():
            if key in EXCLUDED_ARGS:
                continue
            key_parts = key.rsplit(":", 1)
            val = self.get_argument(key)
            if len(key_parts) == 1:
                args[key] = val
            else:
                args[key_parts[0]] = self._convert_type(val, key_parts[1])
        return args

    def _object_parser(self):
        args = {}
        for key in self.request.arguments.keys():
            if key in EXCLUDED_ARGS:
                continue
            val = self.get_argument(key)
            if not val:
                args[key] = None
            else:
                args[key] = val.split(',')
        logging.debug(f"Parsed Arguments: {args}")
        return {'objects': args}

    def parse_args(self):
        try:
            args = self._parse_query()
        except Exception:
            raise ServerError(
                "Error Parsing Request Arguments. "
                "Is the Content-Type correct?")
        content_type = self.request.headers.get('Content-Type', "").strip()
        if content_type.startswith("application/json"):
            try:
                args.update(json.loads(self.request.body))
            except json.JSONDecodeError:
                pass
        for key, value in self.path_kwargs.items():
            if value is not None:
                args[key] = value
        return args

    async def get(self, *args, **kwargs):
        await self._process_http_request()

    async def post(self, *args, **kwargs):
        await self._process_http_request()

    async def delete(self, *args, **kwargs):
        await self._process_http_request()

    async def _do_local_request(self, args, conn):
        return await self.callback(
            WebRequest(self.request.path, args, self.request.method,
                       conn=conn))

    async def _do_remote_request(self, args, conn):
        return await self.server.make_request(
            WebRequest(self.callback, args, conn=conn))

    async def _process_http_request(self):
        if self.request.method not in self.methods:
            raise tornado.web.HTTPError(405)
        conn = self.get_associated_websocket()
        args = self.parse_args()
        try:
            result = await self._do_request(args, conn)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, str(e)) from e
        if self.wrap_result:
            result = {'result': result}
        self.finish(result)

class FileRequestHandler(AuthorizedFileHandler):
    def set_extra_headers(self, path):
        # The call below shold never return an empty string,
        # as the path should have already been validated to be
        # a file
        basename = os.path.basename(self.absolute_path)
        self.set_header(
            "Content-Disposition", f"attachment; filename={basename}")

    async def delete(self, path):
        path = self.request.path.lstrip("/").split("/", 2)[-1]
        path = url_unescape(path, plus=False)
        file_manager = self.server.lookup_component('file_manager')
        try:
            filename = await file_manager.delete_file(path)
        except self.server.error as e:
            if e.status_code == 403:
                raise tornado.web.HTTPError(
                    403, "File is loaded, DELETE not permitted")
            else:
                raise tornado.web.HTTPError(e.status_code, str(e))
        self.finish({'result': filename})

    async def get(self, path: str, include_body: bool = True) -> None:
        # Set up our path instance variables.
        self.path = self.parse_url_path(path)
        del path  # make sure we don't refer to path instead of self.path again
        absolute_path = self.get_absolute_path(self.root, self.path)
        self.absolute_path = self.validate_absolute_path(
            self.root, absolute_path)
        if self.absolute_path is None:
            return

        self.modified = self.get_modified_time()
        self.set_headers()

        if self.should_return_304():
            self.set_status(304)
            return

        request_range = None
        range_header = self.request.headers.get("Range")
        if range_header:
            # As per RFC 2616 14.16, if an invalid Range header is specified,
            # the request will be treated as if the header didn't exist.
            request_range = tornado.httputil._parse_request_range(range_header)

        size = self.get_content_size()
        if request_range:
            start, end = request_range
            if start is not None and start < 0:
                start += size
                if start < 0:
                    start = 0
            if (
                start is not None
                and (start >= size or (end is not None and start >= end))
            ) or end == 0:
                # As per RFC 2616 14.35.1, a range is not satisfiable only: if
                # the first requested byte is equal to or greater than the
                # content, or when a suffix with length 0 is specified.
                # https://tools.ietf.org/html/rfc7233#section-2.1
                # A byte-range-spec is invalid if the last-byte-pos value is
                # present and less than the first-byte-pos.
                self.set_status(416)  # Range Not Satisfiable
                self.set_header("Content-Type", "text/plain")
                self.set_header("Content-Range", "bytes */%s" % (size,))
                return
            if end is not None and end > size:
                # Clients sometimes blindly use a large range to limit their
                # download size; cap the endpoint at the actual file size.
                end = size
            # Note: only return HTTP 206 if less than the entire range has been
            # requested. Not only is this semantically correct, but Chrome
            # refuses to play audio if it gets an HTTP 206 in response to
            # ``Range: bytes=0-``.
            if size != (end or size) - (start or 0):
                self.set_status(206)  # Partial Content
                self.set_header(
                    "Content-Range", tornado.httputil._get_content_range(
                        start, end, size)
                )
        else:
            start = end = None

        if start is not None and end is not None:
            content_length = end - start
        elif end is not None:
            content_length = end
        elif start is not None:
            content_length = size - start
        else:
            end = size
            content_length = size
        self.set_header("Content-Length", content_length)

        if include_body:
            content = self.get_content(self.absolute_path, start, end)
            if isinstance(content, bytes):
                content = [content]
            for chunk in content:
                try:
                    self.write(chunk)
                    await self.flush()
                except tornado.iostream.StreamClosedError:
                    return
        else:
            assert self.request.method == "HEAD"

    def should_return_304(self):
        # Disable file caching
        return False

@tornado.web.stream_request_body
class FileUploadHandler(AuthorizedRequestHandler):
    def initialize(self, max_upload_size):
        super(FileUploadHandler, self).initialize()
        self.file_manager = self.server.lookup_component('file_manager')
        self.max_upload_size = max_upload_size

    def prepare(self):
        if self.request.method == "POST":
            self.request.connection.set_max_body_size(self.max_upload_size)
            tmpname = self.file_manager.gen_temp_upload_path()
            self._targets = {
                'root': ValueTarget(),
                'print': ValueTarget(),
                'path': ValueTarget(),
            }
            self._file = FileTarget(tmpname)
            self._parser = StreamingFormDataParser(self.request.headers)
            self._parser.register('file', self._file)
            for name, target in self._targets.items():
                self._parser.register(name, target)

    def data_received(self, chunk):
        if self.request.method == "POST":
            self._parser.data_received(chunk)

    async def post(self):
        form_args = {}
        for name, target in self._targets.items():
            if target.value:
                form_args[name] = target.value.decode()
        form_args['filename'] = self._file.multipart_filename
        form_args['tmp_file_path'] = self._file.filename
        debug_msg = "\nFile Upload Arguments:"
        for name, value in form_args.items():
            debug_msg += f"\n{name}: {value}"
        logging.debug(debug_msg)
        try:
            result = await self.file_manager.finalize_upload(form_args)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, str(e))
        self.finish(result)

# Default Handler for unregistered endpoints
class AuthorizedErrorHandler(AuthorizedRequestHandler):
    def prepare(self):
        super(AuthorizedRequestHandler, self).prepare()
        self.set_status(404)
        raise tornado.web.HTTPError(404)

    def check_xsrf_cookie(self):
        pass
