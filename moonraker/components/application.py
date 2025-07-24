# Klipper Web Server Rest API
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import os
import mimetypes
import logging
import traceback
import ssl
import pathlib
import urllib.parse
import tornado
import tornado.iostream
import tornado.httputil
import tornado.web
from asyncio import Lock
from inspect import isclass
from tornado.escape import url_unescape, url_escape
from tornado.routing import Rule, PathMatches, RuleRouter
from tornado.http1connection import HTTP1Connection
from tornado.httpserver import HTTPServer
from tornado.log import access_log
from ..utils import ServerError, source_info, parse_ip_address
from ..common import (
    JsonRPC,
    WebRequest,
    APIDefinition,
    APITransport,
    TransportType,
    RequestType,
    KlippyState
)
from ..utils import json_wrapper as jsonw
from streaming_form_data import StreamingFormDataParser, ParseFailedException
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
    AsyncGenerator,
    Type
)
if TYPE_CHECKING:
    from tornado.websocket import WebSocketHandler
    from tornado.httputil import HTTPMessageDelegate, HTTPServerRequest
    from ..server import Server
    from ..eventloop import EventLoop
    from ..confighelper import ConfigHelper
    from ..common import UserInfo
    from .klippy_connection import KlippyConnection as Klippy
    from ..utils import IPAddress
    from .websockets import WebsocketManager, WebSocket
    from .file_manager.file_manager import FileManager
    from .announcements import Announcements
    from .machine import Machine
    from io import BufferedReader
    from .authorization import Authorization
    from .template import TemplateFactory, JinjaTemplate
    MessageDelgate = Optional[HTTPMessageDelegate]
    AuthComp = Optional[Authorization]
    APICallback = Callable[[WebRequest], Coroutine]

# mypy: disable-error-code="attr-defined,name-defined"

# 50 MiB Max Standard Body Size
MAX_BODY_SIZE = 50 * 1024 * 1024
MAX_WS_CONNS_DEFAULT = 50
EXCLUDED_ARGS = ["_", "token", "access_token", "connection_id"]
AUTHORIZED_EXTS = [".png", ".jpg"]
DEFAULT_KLIPPY_LOG_PATH = "/tmp/klippy.log"

class MutableRouter(RuleRouter):
    def __init__(self, application: tornado.web.Application) -> None:
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
                request, target, **target_params
            )
        return super(MutableRouter, self).get_target_delegate(
            target, request, **target_params)

    def has_rule(self, pattern: str) -> bool:
        return pattern in self.pattern_to_rule

    def add_handler(self,
                    pattern: str,
                    target: Any,
                    target_params: Optional[Dict[str, Any]] = None
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

class PrimaryRouter(MutableRouter):
    def __init__(self, config: ConfigHelper) -> None:
        server = config.get_server()
        max_ws_conns = config.getint('max_websocket_connections', MAX_WS_CONNS_DEFAULT)
        self.verbose_logging = server.is_verbose_enabled()
        tornado_ver = tornado.version_info
        app_args: Dict[str, Any] = {
            'serve_traceback': self.verbose_logging,
            'websocket_ping_interval': None if tornado_ver < (6, 5) else 10.,
            'server': server,
            'max_websocket_connections': max_ws_conns,
            'log_function': self.log_request
        }
        super().__init__(tornado.web.Application(**app_args))

    @property
    def tornado_app(self) -> tornado.web.Application:
        return self.application

    def find_handler(
        self, request: HTTPServerRequest, **kwargs: Any
    ) -> Optional[HTTPMessageDelegate]:
        hdlr = super().find_handler(request, **kwargs)
        if hdlr is not None:
            return hdlr
        return self.application.get_handler_delegate(request, AuthorizedErrorHandler)

    def log_request(self, handler: tornado.web.RequestHandler) -> None:
        status_code = handler.get_status()
        if (
            not self.verbose_logging and
            status_code in [200, 204, 206, 304]
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
        user: Optional[UserInfo] = handler.current_user
        username = "No User"
        if user is not None:
            username = user.username
        log_method(
            f"{status_code} {handler._request_summary()} "
            f"[{username}] {request_time:.2f}ms"
        )

class InternalTransport(APITransport):
    def __init__(self, server: Server) -> None:
        self.server = server

    async def call_method(self,
                          method_name: str,
                          request_arguments: Dict[str, Any] = {},
                          **kwargs
                          ) -> Any:
        rpc: JsonRPC = self.server.lookup_component("jsonrpc")
        method_info = rpc.get_method(method_name)
        if method_info is None:
            raise self.server.error(f"No method {method_name} available")
        req_type, api_definition = method_info
        if TransportType.INTERNAL not in api_definition.transports:
            raise self.server.error(f"No method {method_name} available")
        args = request_arguments or kwargs
        return await api_definition.request(args, req_type, self)

class MoonrakerApp:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.json_rpc = JsonRPC(self.server)
        self.http_server: Optional[HTTPServer] = None
        self.secure_server: Optional[HTTPServer] = None
        self.template_cache: Dict[str, JinjaTemplate] = {}
        self.registered_base_handlers: List[str] = [
            "/server/redirect",
            "/server/jsonrpc"
        ]
        self.max_upload_size = config.getint('max_upload_size', 1024)
        self.max_upload_size *= 1024 * 1024

        # SSL config
        self.cert_path: pathlib.Path = self._get_path_option(
            config, 'ssl_certificate_path')
        self.key_path: pathlib.Path = self._get_path_option(
            config, 'ssl_key_path')

        # Route Prefix
        home_pattern = "/"
        self._route_prefix: str = ""
        route_prefix = config.get("route_prefix", None)
        if route_prefix is not None:
            rparts = route_prefix.strip("/").split("/")
            rp = "/".join(
                [url_escape(part, plus=False) for part in rparts if part]
            )
            if not rp:
                raise config.error(
                    f"Invalid value for option 'route_prefix': {route_prefix}"
                )
            self._route_prefix = f"/{rp}"
            home_pattern = f"{self._route_prefix}/?"
        self.internal_transport = InternalTransport(self.server)

        mimetypes.add_type('text/plain', '.log')
        mimetypes.add_type('text/plain', '.gcode')
        mimetypes.add_type('text/plain', '.cfg')

        # Set up HTTP routing.  Our "mutable_router" wraps a Tornado Application
        logging.info(f"Detected Tornado Version {tornado.version}")
        self.mutable_router = PrimaryRouter(config)
        for (ptrn, hdlr) in (
            (home_pattern, WelcomeHandler),
            (f"{self._route_prefix}/server/redirect", RedirectHandler),
            (f"{self._route_prefix}/server/jsonrpc", RPCHandler)
        ):
            self.mutable_router.add_handler(ptrn, hdlr, None)

        # Register handlers
        logfile = self.server.get_app_args().get('log_file')
        if logfile:
            self.register_static_file_handler(
                "moonraker.log", logfile, force=True)
        self.register_static_file_handler(
            "klippy.log", DEFAULT_KLIPPY_LOG_PATH, force=True)
        self.register_upload_handler("/server/files/upload")

        # Register Server Components
        self.server.register_component("jsonrpc", self.json_rpc)
        self.server.register_component("internal_transport", self.internal_transport)

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

    @property
    def route_prefix(self):
        return self._route_prefix

    def parse_endpoint(self, http_path: str) -> str:
        if not self._route_prefix or not http_path.startswith(self._route_prefix):
            return http_path
        return http_path[len(self._route_prefix):]

    def listen(self, host: str, port: int, ssl_port: int) -> None:
        if host.lower() == "all":
            host = ""
        self.http_server = self._create_http_server(port, host)
        if self.https_enabled():
            if port == ssl_port:
                self.server.add_warning(
                    "Failed to start HTTPS server.  Server options 'port' and "
                    f"'ssl_port' match, both set to {port}.  Modify the "
                    "configuration to use different ports."
                )
                return
            logging.info(f"Starting secure server on port {ssl_port}")
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(self.cert_path, self.key_path)
            self.secure_server = self._create_http_server(
                ssl_port, host, ssl_options=ssl_ctx
            )
        else:
            logging.info(
                "SSL Certificate/Key not configured, aborting HTTPS Server startup"
            )

    def _create_http_server(
        self, port: int, address: str, **kwargs
    ) -> Optional[HTTPServer]:
        args: Dict[str, Any] = dict(max_body_size=MAX_BODY_SIZE, xheaders=True)
        args.update(kwargs)
        svr = HTTPServer(self.mutable_router, **args)
        try:
            svr.listen(port, address)
        except Exception as e:
            svr_type = "HTTPS" if "ssl_options" in args else "HTTP"
            self.server.add_warning(
                f"Failed to start {svr_type} server: {e}.  See moonraker.log "
                "for more details.", exc_info=e
            )
            return None
        return svr

    def get_server(self) -> Server:
        return self.server

    def https_enabled(self) -> bool:
        return self.cert_path.exists() and self.key_path.exists()

    async def close(self) -> None:
        if self.http_server is not None:
            self.http_server.stop()
            await self.http_server.close_all_connections()
        if self.secure_server is not None:
            self.secure_server.stop()
            await self.secure_server.close_all_connections()
        APIDefinition.reset_cache()

    def register_endpoint(
        self,
        endpoint: str,
        request_types: Union[List[str], RequestType],
        callback: APICallback,
        transports: Union[List[str], TransportType] = TransportType.all(),
        wrap_result: bool = True,
        content_type: Optional[str] = None,
        auth_required: bool = True,
        is_remote: bool = False
    ) -> None:
        if isinstance(request_types, list):
            request_types = RequestType.from_string_list(request_types)
        if isinstance(transports, list):
            transports = TransportType.from_string_list(transports)
        api_def = APIDefinition.create(
            endpoint, request_types, callback, transports, auth_required, is_remote
        )
        http_path = api_def.http_path
        if http_path in self.registered_base_handlers:
            if not is_remote:
                raise self.server.error(
                    f"Local endpoint '{endpoint}' already registered"
                )
            return
        logging.debug(f"Registering API: {api_def}")
        if TransportType.HTTP in transports:
            params: dict[str, Any] = {}
            params["api_definition"] = api_def
            params["wrap_result"] = wrap_result
            params["content_type"] = content_type
            self.mutable_router.add_handler(
                f"{self._route_prefix}{http_path}", DynamicRequestHandler, params
            )
        self.registered_base_handlers.append(http_path)
        for request_type, method_name in api_def.rpc_items():
            self.json_rpc.register_method(method_name, request_type, api_def)

    def register_static_file_handler(
        self, pattern: str, file_path: str, force: bool = False
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
        self.mutable_router.add_handler(
            f"{self._route_prefix}{pattern}", FileRequestHandler, params
        )

    def register_upload_handler(
        self, pattern: str, location_prefix: str = "server/files"
    ) -> None:
        params: Dict[str, Any] = {'max_upload_size': self.max_upload_size}
        location_prefix = location_prefix.strip("/")
        if self._route_prefix:
            location_prefix = f"{self._route_prefix.strip('/')}/{location_prefix}"
        params['location_prefix'] = location_prefix
        self.mutable_router.add_handler(
            f"{self._route_prefix}{pattern}", FileUploadHandler, params
        )

    def register_websocket_handler(
        self, pattern: str, handler: Type[WebSocketHandler]
    ) -> None:
        self.mutable_router.add_handler(
            f"{self._route_prefix}{pattern}", handler, None
        )

    def register_debug_endpoint(
        self,
        endpoint: str,
        request_types: Union[List[str], RequestType],
        callback: APICallback,
        transports: Union[List[str], TransportType] = TransportType.all(),
        wrap_result: bool = True
    ) -> None:
        if not self.server.is_debug_enabled():
            return
        if not endpoint.startswith("/debug"):
            raise self.server.error(
                "Debug Endpoints must be registered in the '/debug' path"
            )
        self.register_endpoint(
            endpoint, request_types, callback, transports, wrap_result
        )

    def remove_endpoint(self, endpoint: str) -> None:
        api_def = APIDefinition.pop_cached_def(endpoint)
        if api_def is not None:
            logging.debug(f"Removing Endpoint: {endpoint}")
            if api_def.http_path in self.registered_base_handlers:
                self.registered_base_handlers.remove(api_def.http_path)
            self.mutable_router.remove_handler(api_def.http_path)
            for method_name in api_def.rpc_methods:
                self.json_rpc.remove_method(method_name)

    async def load_template(self, asset_name: str) -> JinjaTemplate:
        if asset_name in self.template_cache:
            return self.template_cache[asset_name]
        eventloop = self.server.get_event_loop()
        asset = await eventloop.run_in_thread(
            source_info.read_asset, asset_name
        )
        if asset is None:
            raise tornado.web.HTTPError(404, "Asset Not Found")
        template: TemplateFactory = self.server.lookup_component("template")
        asset_tmpl = template.create_ui_template(asset)
        self.template_cache[asset_name] = asset_tmpl
        return asset_tmpl

def _set_cors_headers(req_hdlr: tornado.web.RequestHandler) -> None:
    request = req_hdlr.request
    origin: Optional[str] = request.headers.get("Origin")
    if origin is None:
        return
    req_hdlr.set_header("Access-Control-Allow-Origin", origin)
    if req_hdlr.request.method == "OPTIONS":
        req_hdlr.set_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS"
        )
        req_hdlr.set_header(
            "Access-Control-Allow-Headers",
            "Origin, Accept, Content-Type, X-Requested-With, "
            "X-CRSF-Token, Authorization, X-Access-Token, "
            "X-Api-Key"
        )
        req_pvt_header = req_hdlr.request.headers.get(
            "Access-Control-Request-Private-Network", None
        )
        if req_pvt_header == "true":
            req_hdlr.set_header("Access-Control-Allow-Private-Network", "true")


class AuthorizedRequestHandler(tornado.web.RequestHandler):
    def initialize(self) -> None:
        self.server: Server = self.settings['server']
        self.auth_required: bool = True
        self.cors_enabled = False

    def set_default_headers(self) -> None:
        if getattr(self, "cors_enabled", False):
            _set_cors_headers(self)

    async def prepare(self) -> None:
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None:
            origin: Optional[str] = self.request.headers.get("Origin")
            self.cors_enabled = await auth.check_cors(origin)
            if self.cors_enabled:
                _set_cors_headers(self)
            self.current_user = await auth.authenticate_request(
                self.request, self.auth_required
            )

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
                wsm: WebsocketManager = self.server.lookup_component("websockets")
                conn = wsm.get_client_ws(conn_id)
        return conn

    def write_error(self, status_code: int, **kwargs) -> None:
        err = {'code': status_code, 'message': self._reason}
        if 'exc_info' in kwargs:
            err['traceback'] = "\n".join(
                traceback.format_exception(*kwargs['exc_info']))
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(jsonw.dumps({'error': err}))

# Due to the way Python treats multiple inheritance its best
# to create a separate authorized handler for serving files
class AuthorizedFileHandler(tornado.web.StaticFileHandler):
    def initialize(self,
                   path: str,
                   default_filename: Optional[str] = None
                   ) -> None:
        super(AuthorizedFileHandler, self).initialize(path, default_filename)
        self.server: Server = self.settings['server']
        self.cors_enabled = False

    def set_default_headers(self) -> None:
        if getattr(self, "cors_enabled", False):
            _set_cors_headers(self)

    async def prepare(self) -> None:
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is not None:
            origin: Optional[str] = self.request.headers.get("Origin")
            self.cors_enabled = await auth.check_cors(origin)
            if self.cors_enabled:
                _set_cors_headers(self)
            self.current_user = await auth.authenticate_request(
                self.request, self._check_need_auth()
            )

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
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(jsonw.dumps({'error': err}))

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
        api_definition: Optional[APIDefinition] = None,
        wrap_result: bool = True,
        content_type: Optional[str] = None
    ) -> None:
        super(DynamicRequestHandler, self).initialize()
        assert api_definition is not None
        self.api_defintion = api_definition
        self.wrap_result = wrap_result
        self.content_type = content_type
        self.auth_required = api_definition.auth_required

    # Converts query string values with type hints
    def _convert_type(self, value: str, hint: str) -> Any:
        type_funcs: Dict[str, Callable] = {
            "int": int, "float": float,
            "bool": lambda x: x.lower() == "true",
            "json": jsonw.loads}
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
            if self.api_defintion.need_object_parser:
                args: Dict[str, Any] = self._object_parser()
            else:
                args = self._default_parser()
        except Exception:
            raise ServerError(
                "Error Parsing Request Arguments. "
                "Is the Content-Type correct?")
        content_type = self.request.headers.get('Content-Type', "").strip()
        if content_type.startswith("application/json"):
            try:
                args.update(jsonw.loads(self.request.body))
            except jsonw.JSONDecodeError:
                pass
        for key, value in self.path_kwargs.items():
            if value is not None:
                args[key] = value
        return args

    def _log_debug(self, header: str, args: Any) -> None:
        if self.server.is_verbose_enabled():
            resp = args
            endpoint = self.api_defintion.endpoint
            if isinstance(args, dict):
                if (
                    endpoint.startswith("/access") or
                    endpoint.startswith("/machine/sudo/password")
                ):
                    resp = {key: "<sanitized>" for key in args}
            elif isinstance(args, str):
                if args.startswith("<html>"):
                    resp = "<html>"
            logging.debug(f"{header}::{resp}")

    async def get(self, *args, **kwargs) -> None:
        await self._process_http_request(RequestType.GET)

    async def post(self, *args, **kwargs) -> None:
        await self._process_http_request(RequestType.POST)

    async def delete(self, *args, **kwargs) -> None:
        await self._process_http_request(RequestType.DELETE)

    async def _process_http_request(self, req_type: RequestType) -> None:
        if req_type not in self.api_defintion.request_types:
            raise tornado.web.HTTPError(405)
        args = self.parse_args()
        transport = self.get_associated_websocket()
        req = f"{self.request.method} {self.request.path}"
        self._log_debug(f"HTTP Request::{req}", args)
        try:
            ip = parse_ip_address(self.request.remote_ip or "")
            result = await self.api_defintion.request(
                args, req_type, transport, ip, self.current_user
            )
        except ServerError as e:
            if self.server.is_verbose_enabled():
                logging.exception("API Request Failure")
            raise tornado.web.HTTPError(
                e.status_code, reason=str(e)) from e
        if self.wrap_result:
            result = {'result': result}
        self._log_debug(f"HTTP Response::{req}", result)
        if result is None:
            self.set_status(204)
        elif isinstance(result, dict):
            self.set_header("Content-Type", "application/json; charset=UTF-8")
            result = jsonw.dumps(result)
        elif self.content_type is not None:
            self.set_header("Content-Type", self.content_type)
        self.finish(result)

class RPCHandler(AuthorizedRequestHandler, APITransport):
    def initialize(self) -> None:
        super(RPCHandler, self).initialize()
        self.auth_required = False

    @property
    def transport_type(self) -> TransportType:
        return TransportType.HTTP

    @property
    def user_info(self) -> Optional[UserInfo]:
        return self.current_user

    @property
    def ip_addr(self) -> Optional[IPAddress]:
        return parse_ip_address(self.request.remote_ip or "")

    def screen_rpc_request(
        self, api_def: APIDefinition, req_type: RequestType, args: Dict[str, Any]
    ) -> None:
        if self.current_user is None and api_def.auth_required:
            raise self.server.error("Unauthorized", 401)
        if api_def.endpoint == "objects/subscribe":
            raise self.server.error(
                "Subscriptions not available for HTTP transport", 404
            )

    def send_status(self, status: Dict[str, Any], eventtime: float) -> None:
        # Can't handle status updates.  This should not be called, but
        # we don't want to raise an exception if it is
        pass

    async def post(self, *args, **kwargs) -> None:
        content_type = self.request.headers.get('Content-Type', "").strip()
        if not content_type.startswith("application/json"):
            raise tornado.web.HTTPError(
                400, "Invalid content type, application/json required"
            )
        rpc: JsonRPC = self.server.lookup_component("jsonrpc")
        result = await rpc.dispatch(self.request.body, self)
        if result is not None:
            self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(result)

class FileRequestHandler(AuthorizedFileHandler):
    def set_extra_headers(self, path: str) -> None:
        # The call below should never return an empty string,
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
        app: MoonrakerApp = self.server.lookup_component("application")
        endpoint = app.parse_endpoint(self.request.path or "")
        path = endpoint.lstrip("/").split("/", 2)[-1]
        path = url_unescape(path, plus=False)
        file_manager: FileManager
        file_manager = self.server.lookup_component('file_manager')
        try:
            filename = await file_manager.delete_file(path)
        except self.server.error as e:
            raise tornado.web.HTTPError(e.status_code, str(e))
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(jsonw.dumps({'result': filename}))

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
        self.parse_lock = Lock()
        self.parse_failed: bool = False

    async def prepare(self) -> None:
        ret = super(FileUploadHandler, self).prepare()
        if ret is not None:
            await ret
        content_type: str = self.request.headers.get("Content-Type", "")
        logging.info(
            f"Upload Request Received from {self.request.remote_ip}\n"
            f"Content-Type: {content_type}"
        )
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
        if self.request.method == "POST" and not self.parse_failed:
            async with self.parse_lock:
                evt_loop = self.server.get_event_loop()
                try:
                    await evt_loop.run_in_thread(self._parser.data_received, chunk)
                except ParseFailedException:
                    logging.exception("Chunk Parsing Error")
                    self.parse_failed = True

    async def post(self) -> None:
        if self.parse_failed:
            self._file.on_finish()
            self._remove_temp_file()
            raise tornado.web.HTTPError(500, "File Upload Parsing Failed")
        form_args = {}
        chk_target = self._targets.pop('checksum')
        calc_chksum = self._sha256_target.value.lower()
        if chk_target.value:
            # Validate checksum
            recd_cksum = chk_target.value.decode().lower()
            if calc_chksum != recd_cksum:
                self._remove_temp_file()
                raise tornado.web.HTTPError(
                    422,
                    f"File checksum mismatch: expected {recd_cksum}, "
                    f"calculated {calc_chksum}"
                )
        mp_fname: Optional[str] = self._file.multipart_filename
        if mp_fname is None or not mp_fname.strip():
            self._remove_temp_file()
            raise tornado.web.HTTPError(400, "Multipart filename omitted")
        for name, target in self._targets.items():
            if target.value:
                form_args[name] = target.value.decode()
        form_args['filename'] = mp_fname
        form_args['tmp_file_path'] = self._file.filename
        debug_msg = "\nFile Upload Arguments:"
        for name, value in form_args.items():
            debug_msg += f"\n{name}: {value}"
        debug_msg += f"\nChecksum: {calc_chksum}"
        form_args["current_user"] = self.current_user
        logging.debug(debug_msg)
        logging.info(f"Processing Uploaded File: {mp_fname}")
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
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(jsonw.dumps(result))

    def _remove_temp_file(self) -> None:
        try:
            os.remove(self._file.filename)
        except Exception:
            pass

# Default Handler for unregistered endpoints
class AuthorizedErrorHandler(AuthorizedRequestHandler):
    async def prepare(self) -> None:
        ret = super(AuthorizedErrorHandler, self).prepare()
        if ret is not None:
            await ret
        self.set_status(404)
        raise tornado.web.HTTPError(404)

    def check_xsrf_cookie(self) -> None:
        pass

    def write_error(self, status_code: int, **kwargs) -> None:
        err = {'code': status_code, 'message': self._reason}
        if 'exc_info' in kwargs:
            err['traceback'] = "\n".join(
                traceback.format_exception(*kwargs['exc_info']))
        self.set_header("Content-Type", "application/json; charset=UTF-8")
        self.finish(jsonw.dumps({'error': err}))

class RedirectHandler(AuthorizedRequestHandler):
    def initialize(self) -> None:
        super().initialize()
        self.auth_required = False

    async def get(self, *args, **kwargs) -> None:
        url: Optional[str] = self.get_argument('url', None)
        if url is None:
            try:
                body_args: Dict[str, Any] = jsonw.loads(self.request.body)
            except jsonw.JSONDecodeError:
                body_args = {}
            if 'url' not in body_args:
                raise tornado.web.HTTPError(
                    400, "No url argument provided")
            url = body_args['url']
            assert url is not None
        # validate the url origin
        auth: AuthComp = self.server.lookup_component('authorization', None)
        if auth is None or not await auth.check_cors(url.rstrip("/")):
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
                await auth.authenticate_request(self.request)
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
        kconn: Klippy = self.server.lookup_component("klippy_connection")
        kstate = kconn.state
        if kstate != KlippyState.DISCONNECTED:
            summary.append(f"Klipper reports {kstate.message.lower()}")
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
        app: MoonrakerApp = self.server.lookup_component("application")
        welcome_template = await app.load_template("welcome.html")
        ret = await welcome_template.render_async(context)
        self.finish(ret)

def load_component(config: ConfigHelper) -> MoonrakerApp:
    return MoonrakerApp(config)
