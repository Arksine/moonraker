# Klipper Web Server Rest API
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import mimetypes
import logging
import json
import traceback
import ssl
import pathlib
import urllib.parse
import tornado
import tornado.iostream
import tornado.httputil
import tornado.web
from inspect import isclass
from tornado.escape import url_unescape, url_escape
from tornado.routing import Rule, PathMatches, AnyMatches
from tornado.http1connection import HTTP1Connection
from tornado.log import access_log
from utils import ServerError
from websockets import (
    WebRequest,
    WebsocketManager,
    WebSocket,
    APITransport,
    BridgeSocket
)
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.targets import FileTarget, ValueTarget, SHA256Target

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Callable,
    Coroutine,
    Union,
    Dict,
    List,
    Tuple,
    AsyncGenerator,
)
if TYPE_CHECKING:
    from tornado.httpserver import HTTPServer
    from moonraker import Server
    from eventloop import EventLoop
    from confighelper import ConfigHelper
    from klippy_connection import KlippyConnection as Klippy
    from components.file_manager.file_manager import FileManager
    from components.announcements import Announcements
    from components.machine import Machine
    from io import BufferedReader
    import components.authorization
    MessageDelgate = Optional[tornado.httputil.HTTPMessageDelegate]
    AuthComp = Optional[components.authorization.Authorization]
    APICallback = Callable[[WebRequest], Coroutine]


# 50 MiB Max Standard Body Size
MAX_BODY_SIZE = 50 * 1024 * 1024
MAX_WS_CONNS_DEFAULT = 50
EXCLUDED_ARGS = ["_", "token", "access_token", "connection_id"]
AUTHORIZED_EXTS = [".png", ".jpg"]
DEFAULT_KLIPPY_LOG_PATH = "/tmp/klippy.log"
ALL_TRANSPORTS = ["http", "websocket", "mqtt", "internal"]
ASSET_PATH = pathlib.Path(__file__).parent.joinpath("assets")

class MutableRouter(tornado.web.ReversibleRuleRouter):
    def __init__(self, application: MoonrakerApp) -> None:
        self.application = application
        self.pattern_to_rule: Dict[str, Rule] = {}
        super(MutableRouter, self).__init__(None)

    def get_target_delegate(self,
                            target: Any,
                            request: tornado.httputil.HTTPServerRequest,
                            **target_params
                            ) -> MessageDelgate:
        if isclass(target) and issubclass(target, tornado.web.RequestHandler):
            return self.application.get_handler_delegate(
                request, target, **target_params)

        return super(MutableRouter, self).get_target_delegate(
            target, request, **target_params)

    def has_rule(self, pattern: str) -> bool:
        return pattern in self.pattern_to_rule

    def add_handler(self,
                    pattern: str,
                    target: Any,
                    target_params: Optional[Dict[str, Any]]
                    ) -> None:
        if pattern in self.pattern_to_rule:
            self.remove_handler(pattern)
        new_rule = Rule(PathMatches(pattern), target, target_params)
        self.pattern_to_rule[pattern] = new_rule
        self.rules.append(new_rule)

    def remove_handler(self, pattern: str) -> None:
        rule = self.pattern_to_rule.pop(pattern, None)
        if rule is not None:
            try:
                self.rules.remove(rule)
            except Exception:
                logging.exception(f"Unable to remove rule: {pattern}")

class APIDefinition:
    def __init__(self,
                 endpoint: str,
                 http_uri: str,
                 jrpc_methods: List[str],
                 request_methods: Union[str, List[str]],
                 transports: List[str],
                 callback: Optional[APICallback],
                 need_object_parser: bool):
        self.endpoint = endpoint
        self.uri = http_uri
        self.jrpc_methods = jrpc_methods
        if not isinstance(request_methods, list):
            request_methods = [request_methods]
        self.request_methods = request_methods
        self.supported_transports = transports
        self.callback = callback
        self.need_object_parser = need_object_parser

class InternalTransport(APITransport):
    def __init__(self, server: Server) -> None:
        self.server = server
        self.callbacks: Dict[str, Tuple[str, str, APICallback]] = {}

    def register_api_handler(self, api_def: APIDefinition) -> None:
        ep = api_def.endpoint
        cb = api_def.callback
        if cb is None:
            # Request to Klippy
            method = api_def.jrpc_methods[0]
            action = ""
            klippy: Klippy = self.server.lookup_component("klippy_connection")
            cb = klippy.request
            self.callbacks[method] = (ep, action, cb)
        else:
            for method, action in \
                    zip(api_def.jrpc_methods, api_def.request_methods):
                self.callbacks[method] = (ep, action, cb)

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        for method in api_def.jrpc_methods:
            self.callbacks.pop(method, None)

    async def call_method(self,
                          method_name: str,
                          request_arguments: Dict[str, Any] = {},
                          **kwargs
                          ) -> Any:
        if method_name not in self.callbacks:
            raise self.server.error(f"No method {method_name} available")
        ep, action, func = self.callbacks[method_name]
        # Request arguments can be suppplied either through a dict object
        # or via keyword arugments
        args = request_arguments or kwargs
        return await func(WebRequest(ep, dict(args), action))

class MoonrakerApp:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.http_server: Optional[HTTPServer] = None
        self.secure_server: Optional[HTTPServer] = None
        self.api_cache: Dict[str, APIDefinition] = {}
        self.registered_base_handlers: List[str] = []
        self.max_upload_size = config.getint('max_upload_size', 1024)
        self.max_upload_size *= 1024 * 1024
        max_ws_conns = config.getint(
            'max_websocket_connections', MAX_WS_CONNS_DEFAULT
        )

        # SSL config
        self.cert_path: pathlib.Path = self._get_path_option(
            config, 'ssl_certificate_path')
        self.key_path: pathlib.Path = self._get_path_option(
            config, 'ssl_key_path')

        # Set Up Websocket and Authorization Managers
        self.wsm = WebsocketManager(self.server)
        self.internal_transport = InternalTransport(self.server)
        self.api_transports: Dict[str, APITransport] = {
            "websocket": self.wsm,
            "internal": self.internal_transport
        }

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')
        mimetypes.add_type('text/plain', '.cfg')

        app_args: Dict[str, Any] = {
            'serve_traceback': self.server.is_verbose_enabled(),
            'websocket_ping_interval': 10,
            'websocket_ping_timeout': 30,
            'server': self.server,
            'max_websocket_connections': max_ws_conns,
            'default_handler_class': AuthorizedErrorHandler,
            'default_handler_args': {},
            'log_function': self.log_request,
            'compiled_template_cache': False,
        }

        # Set up HTTP only requests
        self.mutable_router = MutableRouter(self)
        app_handlers: List[Any] = [
            (AnyMatches(), self.mutable_router),
            (r"/", WelcomeHandler),
            (r"/websocket", WebSocket),
            (r"/klippysocket", BridgeSocket),
            (r"/server/redirect", RedirectHandler)
        ]
        self.app = tornado.web.Application(app_handlers, **app_args)
        self.get_handler_delegate = self.app.get_handler_delegate

        # Register handlers
        logfile = self.server.get_app_args().get('log_file')
        if logfile:
            self.register_static_file_handler(
                "moonraker.log", logfile, force=True)
        self.register_static_file_handler(
            "klippy.log", DEFAULT_KLIPPY_LOG_PATH, force=True)
        self.register_upload_handler("/server/files/upload")

        # Register Server Components
        self.server.register_component("application", self)
        self.server.register_component("websockets", self.wsm)
        self.server.register_component("internal_transport",
                                       self.internal_transport)

    def _get_path_option(
        self, config: ConfigHelper, option: str
    ) -> pathlib.Path:
        path: Optional[str] = config.get(option, None, deprecate=True)
        app_args = self.server.get_app_args()
        data_path = app_args["data_path"]
        certs_path = pathlib.Path(data_path).joinpath("certs")
        if not certs_path.exists():
            try:
                certs_path.mkdir()
            except Exception:
                pass
        ext = "key" if "key" in option else "cert"
        item = certs_path.joinpath(f"moonraker.{ext}")
        if item.exists() or path is None:
            return item
        item = pathlib.Path(path).expanduser().resolve()
        if not item.exists():
            raise self.server.error(
                f"Invalid path for option '{option}', "
                f"{path} does not exist"
            )
        return item

    def listen(self, host: str, port: int, ssl_port: int) -> None:
        if host.lower() == "all":
            host = ""
        self.http_server = self.app.listen(
            port, address=host, max_body_size=MAX_BODY_SIZE,
            xheaders=True)
        if self.https_enabled():
            logging.info(f"Starting secure server on port {ssl_port}")
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(self.cert_path, self.key_path)
            self.secure_server = self.app.listen(
                ssl_port, address=host, max_body_size=MAX_BODY_SIZE,
                xheaders=True, ssl_options=ssl_ctx)
        else:
            logging.info("SSL Certificate/Key not configured, "
                         "aborting HTTPS Server startup")

    def log_request(self, handler: tornado.web.RequestHandler) -> None:
        status_code = handler.get_status()
        if (
            not self.server.is_verbose_enabled()
            and status_code in [200, 204, 206, 304]
        ):
            # don't log successful requests in release mode
            return
        if status_code < 400:
            log_method = access_log.info
        elif status_code < 500:
            log_method = access_log.warning
        else:
            log_method = access_log.error
        request_time = 1000.0 * handler.request.request_time()
        user = handler.current_user
        username = "No User"
        if user is not None and 'username' in user:
            username = user['username']
        log_method(
            f"{status_code} {handler._request_summary()} "
            f"[{username}] {request_time:.2f}ms")

    def get_server(self) -> Server:
        return self.server

    def get_asset_path(self) -> pathlib.Path:
        return ASSET_PATH

    def https_enabled(self) -> bool:
        return self.cert_path.exists() and self.key_path.exists()

    async def close(self) -> None:
        if self.http_server is not None:
            self.http_server.stop()
            await self.http_server.close_all_connections()
        if self.secure_server is not None:
            self.secure_server.stop()
            await self.secure_server.close_all_connections()
        await self.wsm.close()

    def register_api_transport(self,
                               name: str,
                               transport: APITransport
                               ) -> Dict[str, APIDefinition]:
        self.api_transports[name] = transport
        return self.api_cache

    def register_remote_handler(self, endpoint: str) -> None:
        api_def = self._create_api_definition(endpoint)
        if api_def.uri in self.registered_base_handlers:
            # reserved handler or already registered
            return
        logging.info(
            f"Registering HTTP endpoint: "
            f"({' '.join(api_def.request_methods)}) {api_def.uri}")
        params: Dict[str, Any] = {}
        params['methods'] = api_def.request_methods
        params['callback'] = api_def.endpoint
        params['need_object_parser'] = api_def.need_object_parser
        self.mutable_router.add_handler(
            api_def.uri, DynamicRequestHandler, params)
        self.registered_base_handlers.append(api_def.uri)
        for name, transport in self.api_transports.items():
            transport.register_api_handler(api_def)

    def register_local_handler(self,
                               uri: str,
                               request_methods: List[str],
                               callback: APICallback,
                               transports: List[str] = ALL_TRANSPORTS,
                               wrap_result: bool = True
                               ) -> None:
        if uri in self.registered_base_handlers:
            return
        api_def = self._create_api_definition(
            uri, request_methods, callback, transports=transports)
        if "http" in transports:
            logging.info(
                f"Registering HTTP Endpoint: "
                f"({' '.join(request_methods)}) {uri}")
            params: dict[str, Any] = {}
            params['methods'] = request_methods
            params['callback'] = callback
            params['wrap_result'] = wrap_result
            params['is_remote'] = False
            self.mutable_router.add_handler(uri, DynamicRequestHandler, params)
        self.registered_base_handlers.append(uri)
        for name, transport in self.api_transports.items():
            if name in transports:
                transport.register_api_handler(api_def)

    def register_static_file_handler(self,
                                     pattern: str,
                                     file_path: str,
                                     force: bool = False
                                     ) -> None:
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

    def register_upload_handler(self,
                                pattern: str,
                                location_prefix: Optional[str] = None
                                ) -> None:
        params: Dict[str, Any] = {'max_upload_size': self.max_upload_size}
        if location_prefix is not None:
            params['location_prefix'] = location_prefix
        self.mutable_router.add_handler(pattern, FileUploadHandler, params)

    def register_debug_handler(
        self,
        uri: str,
        request_methods: List[str],
        callback: APICallback,
        transports: List[str] = ALL_TRANSPORTS,
        wrap_result: bool = True
    ) -> None:
        if not self.server.is_debug_enabled():
            return
        if not uri.startswith("/debug"):
            raise self.server.error(
                "Debug Endpoints must be registerd in the '/debug' path"
            )
        self.register_local_handler(
            uri, request_methods, callback, transports, wrap_result
        )

    def remove_handler(self, endpoint: str) -> None:
        api_def = self.api_cache.pop(endpoint, None)
        if api_def is not None:
            self.mutable_router.remove_handler(api_def.uri)
            for name, transport in self.api_transports.items():
                transport.remove_api_handler(api_def)

    def _create_api_definition(self,
                               endpoint: str,
                               request_methods: List[str] = [],
                               callback: Optional[APICallback] = None,
                               transports: List[str] = ALL_TRANSPORTS
                               ) -> APIDefinition:
        is_remote = callback is None
        if endpoint in self.api_cache:
            return self.api_cache[endpoint]
        if endpoint[0] == '/':
            uri = endpoint
        elif is_remote:
            uri = "/printer/" + endpoint
        else:
            uri = "/server/" + endpoint
        jrpc_methods = []
        if is_remote:
            # Remote requests accept both GET and POST requests.  These
            # requests execute the same callback, thus they resolve to
            # only a single websocket method.
            jrpc_methods.append(uri[1:].replace('/', '.'))
            request_methods = ['GET', 'POST']
        else:
            name_parts = uri[1:].split('/')
            if len(request_methods) > 1:
                for req_mthd in request_methods:
                    func_name = req_mthd.lower() + "_" + name_parts[-1]
                    jrpc_methods.append(".".join(
                        name_parts[:-1] + [func_name]))
            else:
                jrpc_methods.append(".".join(name_parts))
        if not is_remote and len(request_methods) != len(jrpc_methods):
            raise self.server.error(
                "Invalid API definition.  Number of websocket methods must "
                "match the number of request methods")
        need_object_parser = endpoint.startswith("objects/")
        api_def = APIDefinition(endpoint, uri, jrpc_methods, request_methods,
                                transports, callback, need_object_parser)
        self.api_cache[endpoint] = api_def
        return api_def

class AuthorizedRequestHandler(tornado.web.RequestHandler):
    def initialize(self) -> None:
        self.server: Server = self.settings['server']

    def set_default_headers(self) -> None:
        origin: Optional[str] = self.request.headers.get("Origin")
        # it is necessary to look up the parent app here,
        # as initialize() may not yet be called
        server: Server = self.settings['server']
        auth: AuthComp = server.lookup_component('authorization', None)
        self.cors_enabled = False
        if auth is not None:
            self.cors_enabled = auth.check_cors(origin, self)

    def prepare(self) -> None:
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None:
            self.current_user = auth.check_authorized(self.request)

    def options(self, *args, **kwargs) -> None:
        # Enable CORS if configured
        if self.cors_enabled:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedRequestHandler, self).options()

    def get_associated_websocket(self) -> Optional[WebSocket]:
        # Return associated websocket connection if an id
        # was provided by the request
        conn = None
        conn_id: Any = self.get_argument('connection_id', None)
        if conn_id is not None:
            try:
                conn_id = int(conn_id)
            except Exception:
                pass
            else:
                wsm: WebsocketManager = self.server.lookup_component(
                    "websockets")
                conn = wsm.get_client(conn_id)
        if not isinstance(conn, WebSocket):
            return None
        return conn

    def write_error(self, status_code: int, **kwargs) -> None:
        err = {'code': status_code, 'message': self._reason}
        if 'exc_info' in kwargs:
            err['traceback'] = "\n".join(
                traceback.format_exception(*kwargs['exc_info']))
        self.finish({'error': err})

# Due to the way Python treats multiple inheritance its best
# to create a separate authorized handler for serving files
class AuthorizedFileHandler(tornado.web.StaticFileHandler):
    def initialize(self,
                   path: str,
                   default_filename: Optional[str] = None
                   ) -> None:
        super(AuthorizedFileHandler, self).initialize(path, default_filename)
        self.server: Server = self.settings['server']

    def set_default_headers(self) -> None:
        origin: Optional[str] = self.request.headers.get("Origin")
        # it is necessary to look up the parent app here,
        # as initialize() may not yet be called
        server: Server = self.settings['server']
        auth: AuthComp = server.lookup_component('authorization', None)
        self.cors_enabled = False
        if auth is not None:
            self.cors_enabled = auth.check_cors(origin, self)

    def prepare(self) -> None:
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None and self._check_need_auth():
            self.current_user = auth.check_authorized(self.request)

    def options(self, *args, **kwargs) -> None:
        # Enable CORS if configured
        if self.cors_enabled:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedFileHandler, self).options()

    def write_error(self, status_code: int, **kwargs) -> None:
        err = {'code': status_code, 'message': self._reason}
        if 'exc_info' in kwargs:
            err['traceback'] = "\n".join(
                traceback.format_exception(*kwargs['exc_info']))
        self.finish({'error': err})

    def _check_need_auth(self) -> bool:
        if self.request.method != "GET":
            return True
        ext = os.path.splitext(self.request.path)[-1].lower()
        if ext in AUTHORIZED_EXTS:
            return False
        return True

class DynamicRequestHandler(AuthorizedRequestHandler):
    def initialize(
        self,
        callback: Union[str, Callable[[WebRequest], Coroutine]] = "",
        methods: List[str] = [],
        need_object_parser: bool = False,
        is_remote: bool = True,
        wrap_result: bool = True
    ) -> None:
        super(DynamicRequestHandler, self).initialize()
        self.callback = callback
        self.methods = methods
        self.wrap_result = wrap_result
        self._do_request = self._do_remote_request if is_remote \
            else self._do_local_request
        self._parse_query = self._object_parser if need_object_parser \
            else self._default_parser

    # Converts query string values with type hints
    def _convert_type(self, value: str, hint: str) -> Any:
        type_funcs: Dict[str, Callable] = {
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

    def _default_parser(self) -> Dict[str, Any]:
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

    def _object_parser(self) -> Dict[str, Dict[str, Any]]:
        args: Dict[str, Any] = {}
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

    def parse_args(self) -> Dict[str, Any]:
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

    def _log_debug(self, header: str, args: Any) -> None:
        if self.server.is_verbose_enabled():
            resp = args
            if isinstance(args, dict):
                if (
                    self.request.path.startswith("/access") or
                    self.request.path.startswith("/machine/sudo/password")
                ):
                    resp = {key: "<sanitized>" for key in args}
            elif isinstance(args, str):
                if args.startswith("<html>"):
                    resp = "<html>"
            logging.debug(f"{header}::{resp}")

    async def get(self, *args, **kwargs) -> None:
        await self._process_http_request()

    async def post(self, *args, **kwargs) -> None:
        await self._process_http_request()

    async def delete(self, *args, **kwargs) -> None:
        await self._process_http_request()

    async def _do_local_request(self,
                                args: Dict[str, Any],
                                conn: Optional[WebSocket]
                                ) -> Any:
        assert callable(self.callback)
        return await self.callback(
            WebRequest(self.request.path, args, self.request.method,
                       conn=conn, ip_addr=self.request.remote_ip or "",
                       user=self.current_user))

    async def _do_remote_request(self,
                                 args: Dict[str, Any],
                                 conn: Optional[WebSocket]
                                 ) -> Any:
        assert isinstance(self.callback, str)
        klippy: Klippy = self.server.lookup_component("klippy_connection")
        return await klippy.request(
            WebRequest(self.callback, args, conn=conn,
                       ip_addr=self.request.remote_ip or "",
                       user=self.current_user))

    async def _process_http_request(self) -> None:
        if self.request.method not in self.methods:
            raise tornado.web.HTTPError(405)
        conn = self.get_associated_websocket()
        args = self.parse_args()
        req = f"{self.request.method} {self.request.path}"
        self._log_debug(f"HTTP Request::{req}", args)
        try:
            result = await self._do_request(args, conn)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, reason=str(e)) from e
        if self.wrap_result:
            result = {'result': result}
        if result is None:
            self.set_status(204)
        self._log_debug(f"HTTP Response::{req}", result)
        self.finish(result)

class FileRequestHandler(AuthorizedFileHandler):
    def set_extra_headers(self, path: str) -> None:
        # The call below shold never return an empty string,
        # as the path should have already been validated to be
        # a file
        assert isinstance(self.absolute_path, str)
        basename = os.path.basename(self.absolute_path)
        ascii_basename = self._escape_filename_to_ascii(basename)
        utf8_basename = self._escape_filename_to_utf8(basename)
        self.set_header(
            "Content-Disposition",
            f"attachment; filename=\"{ascii_basename}\"; "
            f"filename*=UTF-8\'\'{utf8_basename}")

    async def delete(self, path: str) -> None:
        path = self.request.path.lstrip("/").split("/", 2)[-1]
        path = url_unescape(path, plus=False)
        file_manager: FileManager
        file_manager = self.server.lookup_component('file_manager')
        try:
            filename = await file_manager.delete_file(path)
        except self.server.error as e:
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
        file_manager: FileManager
        file_manager = self.server.lookup_component('file_manager')
        try:
            file_manager.check_reserved_path(self.absolute_path, False)
        except self.server.error as e:
            raise tornado.web.HTTPError(e.status_code, str(e))

        self.modified = self.get_modified_time()
        self.set_headers()

        self.request.headers.pop("If-None-Match", None)
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
            end = size
            content_length = size - start
        else:
            end = size
            content_length = size
        self.set_header("Content-Length", content_length)

        if include_body:
            evt_loop = self.server.get_event_loop()
            content = self.get_content_nonblock(
                evt_loop, self.absolute_path, start, end)
            async for chunk in content:
                try:
                    self.write(chunk)
                    await self.flush()
                except tornado.iostream.StreamClosedError:
                    return
        else:
            assert self.request.method == "HEAD"

    def _escape_filename_to_ascii(self, basename: str) -> str:
        ret = basename.encode("ascii", "replace").decode()
        return ret.replace('"', '\\"')

    def _escape_filename_to_utf8(self, basename: str) -> str:
        return urllib.parse.quote(basename, encoding="utf-8")

    @classmethod
    async def get_content_nonblock(
        cls,
        evt_loop: EventLoop,
        abspath: str,
        start: Optional[int] = None,
        end: Optional[int] = None
    ) -> AsyncGenerator[bytes, None]:
        file: BufferedReader = await evt_loop.run_in_thread(open, abspath, "rb")
        try:
            if start is not None:
                file.seek(start)
            if end is not None:
                remaining = end - (start or 0)  # type: Optional[int]
            else:
                remaining = None
            while True:
                chunk_size = 64 * 1024
                if remaining is not None and remaining < chunk_size:
                    chunk_size = remaining
                chunk = await evt_loop.run_in_thread(file.read, chunk_size)
                if chunk:
                    if remaining is not None:
                        remaining -= len(chunk)
                    yield chunk
                else:
                    if remaining is not None:
                        assert remaining == 0
                    return
        finally:
            await evt_loop.run_in_thread(file.close)

    @classmethod
    def _get_cached_version(cls, abs_path: str) -> Optional[str]:
        return None

@tornado.web.stream_request_body
class FileUploadHandler(AuthorizedRequestHandler):
    def initialize(self,
                   location_prefix: str = "server/files",
                   max_upload_size: int = MAX_BODY_SIZE
                   ) -> None:
        self.location_prefix = location_prefix
        super(FileUploadHandler, self).initialize()
        self.file_manager: FileManager = self.server.lookup_component(
            'file_manager')
        self.max_upload_size = max_upload_size

    def prepare(self) -> None:
        super(FileUploadHandler, self).prepare()
        fm: FileManager = self.server.lookup_component("file_manager")
        fm.check_write_enabled()
        if self.request.method == "POST":
            assert isinstance(self.request.connection, HTTP1Connection)
            self.request.connection.set_max_body_size(self.max_upload_size)
            tmpname = self.file_manager.gen_temp_upload_path()
            self._targets = {
                'root': ValueTarget(),
                'print': ValueTarget(),
                'path': ValueTarget(),
                'checksum': ValueTarget(),
            }
            self._file = FileTarget(tmpname)
            self._sha256_target = SHA256Target()
            self._parser = StreamingFormDataParser(self.request.headers)
            self._parser.register('file', self._file)
            self._parser.register('file', self._sha256_target)
            for name, target in self._targets.items():
                self._parser.register(name, target)

    async def data_received(self, chunk: bytes) -> None:
        if self.request.method == "POST":
            evt_loop = self.server.get_event_loop()
            await evt_loop.run_in_thread(self._parser.data_received, chunk)

    async def post(self) -> None:
        form_args = {}
        chk_target = self._targets.pop('checksum')
        calc_chksum = self._sha256_target.value.lower()
        if chk_target.value:
            # Validate checksum
            recd_cksum = chk_target.value.decode().lower()
            if calc_chksum != recd_cksum:
                # remove temporary file
                try:
                    os.remove(self._file.filename)
                except Exception:
                    pass
                raise self.server.error(
                    f"File checksum mismatch: expected {recd_cksum}, "
                    f"calculated {calc_chksum}", 422)
        for name, target in self._targets.items():
            if target.value:
                form_args[name] = target.value.decode()
        form_args['filename'] = self._file.multipart_filename
        form_args['tmp_file_path'] = self._file.filename
        debug_msg = "\nFile Upload Arguments:"
        for name, value in form_args.items():
            debug_msg += f"\n{name}: {value}"
        debug_msg += f"\nChecksum: {calc_chksum}"
        logging.debug(debug_msg)
        try:
            result = await self.file_manager.finalize_upload(form_args)
        except ServerError as e:
            raise tornado.web.HTTPError(
                e.status_code, str(e))
        # Return 201 and add the Location Header
        item: Dict[str, Any] = result.get('item', {})
        root: Optional[str] = item.get('root', None)
        fpath: Optional[str] = item.get('path', None)
        if root is not None and fpath is not None:
            path_parts = fpath.split("/")
            fpath = "/".join([url_escape(p, plus=False) for p in path_parts])
            proto = self.request.protocol
            if not isinstance(proto, str):
                proto = "http"
            host = self.request.host
            if not isinstance(host, str):
                si = self.server.get_host_info()
                port = si['port'] if proto == "http" else si['ssl_port']
                host = f"{si['address']}:{port}"
            location = f"{proto}://{host}/{self.location_prefix}/{root}/{fpath}"
            self.set_header("Location", location)
            logging.debug(f"Upload Location header set: {location}")
        self.set_status(201)
        self.finish(result)

# Default Handler for unregistered endpoints
class AuthorizedErrorHandler(AuthorizedRequestHandler):
    def prepare(self) -> None:
        super(AuthorizedRequestHandler, self).prepare()
        self.set_status(404)
        raise tornado.web.HTTPError(404)

    def check_xsrf_cookie(self) -> None:
        pass

    def write_error(self, status_code: int, **kwargs) -> None:
        err = {'code': status_code, 'message': self._reason}
        if 'exc_info' in kwargs:
            err['traceback'] = "\n".join(
                traceback.format_exception(*kwargs['exc_info']))
        self.finish({'error': err})

class RedirectHandler(AuthorizedRequestHandler):
    def get(self, *args, **kwargs) -> None:
        url: Optional[str] = self.get_argument('url', None)
        if url is None:
            try:
                body_args: Dict[str, Any] = json.loads(self.request.body)
            except json.JSONDecodeError:
                body_args = {}
            if 'url' not in body_args:
                raise tornado.web.HTTPError(
                    400, "No url argument provided")
            url = body_args['url']
            assert url is not None
        # validate the url origin
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is None or not auth.check_cors(url.rstrip("/")):
            raise tornado.web.HTTPError(
                400, f"Unauthorized URL redirect: {url}")
        self.redirect(url)

class WelcomeHandler(tornado.web.RequestHandler):
    def initialize(self) -> None:
        self.server: Server = self.settings['server']

    async def get(self) -> None:
        summary: List[str] = []
        auth: AuthComp = self.server.lookup_component("authorization", None)
        if auth is not None:
            try:
                user = auth.check_authorized(self.request)
            except tornado.web.HTTPError:
                authorized = False
            else:
                authorized = True
            if authorized:
                summary.append(
                    "Your device is authorized to access Moonraker's API."
                )
            else:
                summary.append(
                    "Your device is not authorized to access Moonraker's API. "
                    "This is normal if you intend to use API Key "
                    "authentication or log in as an authenticated user.  "
                    "Otherwise you need to add your IP address to the "
                    "'trusted_clients' option in the [authorization] section "
                    "of moonraker.conf."
                )
            cors_enabled = auth.cors_enabled()
            if cors_enabled:
                summary.append(
                    "CORS is enabled.  Cross origin requests will be allowed "
                    "for origins that match one of the patterns specified in "
                    "the 'cors_domain' option of the [authorization] section."
                )
            else:
                summary.append(
                    "All cross origin requests will be blocked by the browser. "
                    "The 'cors_domains' option in [authorization] must be  "
                    "configured to enable CORS."
                )
        else:
            authorized = True
            cors_enabled = False
            summary.append(
                "The [authorization] component is not enabled in "
                "moonraker.conf.  All connections will be considered trusted."
            )
            summary.append(
                "All cross origin requests will be blocked by the browser.  "
                "The [authorization] section in moonraker.conf must be "
                "configured to enable CORS."
            )
        kstate = self.server.get_klippy_state()
        if kstate != "disconnected":
            kinfo = self.server.get_klippy_info()
            kmsg = kinfo.get("state_message", kstate)
            summary.append(f"Klipper reports {kmsg.lower()}")
        else:
            summary.append(
                "Moonraker is not currently connected to Klipper.  Make sure "
                "that the klipper service has successfully started and that "
                "its unix is enabled."
            )
        ancomp: Announcements
        ancomp = self.server.lookup_component("announcements")
        wsm: WebsocketManager = self.server.lookup_component("websockets")
        machine: Machine = self.server.lookup_component("machine")
        svc_info = machine.get_moonraker_service_info()
        sudo_req_msg = "<br/>".join(machine.sudo_request_messages)
        context: Dict[str, Any] = {
            "remote_ip": self.request.remote_ip,
            "authorized": authorized,
            "cors_enabled": cors_enabled,
            "version": self.server.get_app_args()["software_version"],
            "ws_count": wsm.get_count(),
            "klippy_state": kstate,
            "warnings": self.server.get_warnings(),
            "summary": summary,
            "announcements": await ancomp.get_announcements(),
            "sudo_requested": machine.sudo_requested,
            "sudo_request_message": sudo_req_msg,
            "linux_user": machine.linux_user,
            "local_ip": machine.public_ip or "unknown",
            "service_name": svc_info.get("unit_name", "unknown"),
            "hostname": self.server.get_host_info()["hostname"],
        }
        self.render("welcome.html", **context)

    def get_template_path(self) -> Optional[str]:
        return str(ASSET_PATH)
