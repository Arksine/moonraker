# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import base64
import uuid
import os
import time
import logging
import tornado
from tornado.ioloop import IOLoop, PeriodicCallback

TOKEN_TIMEOUT = 5
CONNECTION_TIMEOUT = 3600
PRUNE_CHECK_TIME = 300 * 1000

class Authorization:
    def __init__(self, api_key_file):
        self.api_key_loc = os.path.expanduser(api_key_file)
        self.api_key = self._read_api_key()
        self.auth_enabled = True
        self.trusted_ips = []
        self.trusted_ranges = []
        self.trusted_connections = {}
        self.access_tokens = {}

        self.prune_handler = PeriodicCallback(
            self._prune_conn_handler, PRUNE_CHECK_TIME)
        self.prune_handler.start()

    def load_config(self, config):
        self.auth_enabled = config.get("require_auth", self.auth_enabled)
        self.trusted_ips = config.get("trusted_ips", self.trusted_ips)
        self.trusted_ranges = config.get("trusted_ranges", self.trusted_ranges)
        self._reset_trusted_connections()
        logging.info(
            "Authorization Configuration Loaded\n"
            "Auth Enabled: %s\n"
            "Trusted IPs:\n%s\n"
            "Trusted IP Ranges:\n%s" %
            (self.auth_enabled,
             ('\n').join(self.trusted_ips),
             ('\n').join(self.trusted_ranges)))

    def register_handlers(self, app):
        # Register Authorization Endpoints
        app.register_local_handler(
            "/access/api_key", None, ['GET', 'POST'],
            self._handle_apikey_request, http_only=True)
        app.register_local_handler(
            "/access/oneshot_token", None, ['GET'],
            self._handle_token_request, http_only=True)

    async def _handle_apikey_request(self, path, method, args):
        if method.upper() == 'POST':
            self.api_key = self._create_api_key()
        return self.api_key

    async def _handle_token_request(self, path, method, args):
        return self.get_access_token()

    def _read_api_key(self):
        if os.path.exists(self.api_key_loc):
            with open(self.api_key_loc, 'r') as f:
                api_key = f.read()
            return api_key
        # API Key file doesn't exist.  Generate
        # a new api key and create the file.
        logging.info(
            "No API Key file found, creating new one at:\n%s"
            % (self.api_key_loc))
        return self._create_api_key()

    def _create_api_key(self):
        api_key = uuid.uuid4().hex
        with open(self.api_key_loc, 'w') as f:
            f.write(api_key)
        return api_key

    def _reset_trusted_connections(self):
        valid_conns = {}
        for ip, access_time in self.trusted_connections.items():
            if ip in self.trusted_ips or \
                    ip[:ip.rfind('.')] in self.trusted_ranges:
                valid_conns[ip] = access_time
            else:
                logging.info(
                    "Connection [%s] no longer trusted, removing" % (ip))
        self.trusted_connections = valid_conns

    def _prune_conn_handler(self):
        cur_time = time.time()
        expired_conns = []
        for ip, access_time in self.trusted_connections.items():
            if cur_time - access_time > CONNECTION_TIMEOUT:
                expired_conns.append(ip)
        for ip in expired_conns:
            self.trusted_connections.pop(ip)
            logging.info(
                "Trusted Connection Expired, IP: %s" % (ip))

    def _token_expire_handler(self, token):
        self.access_tokens.pop(token)

    def is_enabled(self):
        return self.auth_enabled

    def get_access_token(self):
        token = base64.b32encode(os.urandom(20)).decode()
        ioloop = IOLoop.current()
        self.access_tokens[token] = ioloop.call_later(
            TOKEN_TIMEOUT, self._token_expire_handler, token)
        return token

    def _check_trusted_connection(self, ip):
        if ip is not None:
            if ip in self.trusted_connections:
                self.trusted_connections[ip] = time.time()
                return True
            elif ip in self.trusted_ips or \
                    ip[:ip.rfind('.')] in self.trusted_ranges:
                logging.info(
                    "Trusted Connection Detected, IP: %s"
                    % (ip))
                self.trusted_connections[ip] = time.time()
                return True
        return False

    def _check_access_token(self, token):
        if token in self.access_tokens:
            token_handler = self.access_tokens.pop(token)
            IOLoop.current().remove_timeout(token_handler)
            return True
        else:
            return False

    def check_authorized(self, request):
        # Authorization is disabled, request may pass
        if not self.auth_enabled:
            return True

        # Check if IP is trusted
        ip = request.remote_ip
        if self._check_trusted_connection(ip):
            return True

        # Check API Key Header
        key = request.headers.get("X-Api-Key")
        if key and key == self.api_key:
            return True

        # Check one-shot access token
        token = request.arguments.get('token', [b""])[0].decode()
        if self._check_access_token(token):
            return True
        return False

    def close(self):
        self.prune_handler.stop()

class AuthorizedRequestHandler(tornado.web.RequestHandler):
    def initialize(self, server, auth):
        self.server = server
        self.auth = auth

    def prepare(self):
        if not self.auth.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")

    def set_default_headers(self):
        if self.settings['enable_cors']:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, OPTIONS")
            self.set_header(
                "Access-Control-Allow-Headers",
                "Origin, Accept, Content-Type, X-Requested-With, "
                "X-CRSF-Token")

    def options(self, *args, **kwargs):
        # Enable CORS if configured
        if self.settings['enable_cors']:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedRequestHandler, self).options()

# Due to the way Python treats multiple inheritance its best
# to create a separate authorized handler for serving files
class AuthorizedFileHandler(tornado.web.StaticFileHandler):
    def initialize(self, server, auth, path, default_filename=None):
        super(AuthorizedFileHandler, self).initialize(path, default_filename)
        self.server = server
        self.auth = auth

    def prepare(self):
        if not self.auth.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")

    def set_default_headers(self):
        if self.settings['enable_cors']:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, DELETE, OPTIONS")
            self.set_header(
                "Access-Control-Allow-Headers",
                "Origin, Accept, Content-Type, X-Requested-With, "
                "X-CRSF-Token")

    def options(self, *args, **kwargs):
        # Enable CORS if configured
        if self.settings['enable_cors']:
            self.set_status(204)
            self.finish()
        else:
            super(AuthorizedFileHandler, self).options()
