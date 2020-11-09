# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import base64
import uuid
import os
import time
import ipaddress
import logging
import tornado
from tornado.ioloop import IOLoop, PeriodicCallback
from utils import ServerError

TOKEN_TIMEOUT = 5
CONNECTION_TIMEOUT = 3600
PRUNE_CHECK_TIME = 300 * 1000

class Authorization:
    def __init__(self, config):
        api_key_file = config.get('api_key_file', "~/.moonraker_api_key")
        self.api_key_file = os.path.expanduser(api_key_file)
        self.api_key = self._read_api_key()
        self.auth_enabled = config.getboolean('enabled', True)
        self.trusted_connections = {}
        self.access_tokens = {}

        # Get Trusted Clients
        self.trusted_ips = []
        self.trusted_ranges = []
        trusted_clients = config.get('trusted_clients', "")
        trusted_clients = [c.strip() for c in trusted_clients.split('\n')
                           if c.strip()]
        for ip in trusted_clients:
            # Check IP address
            try:
                tc = ipaddress.ip_address(ip)
            except ValueError:
                tc = None
            if tc is None:
                # Check ip network
                try:
                    tc = ipaddress.ip_network(ip)
                except ValueError:
                    raise ServerError(
                        f"Invalid option in trusted_clients: {ip}")
                self.trusted_ranges.append(tc)
            else:
                self.trusted_ips.append(tc)

        t_clients = "\n".join(
            [str(ip) for ip in self.trusted_ips] +
            [str(rng) for rng in self.trusted_ranges])

        logging.info(
            f"Authorization Configuration Loaded\n"
            f"Auth Enabled: {self.auth_enabled}\n"
            f"Trusted Clients:\n{t_clients}")

        self.prune_handler = PeriodicCallback(
            self._prune_conn_handler, PRUNE_CHECK_TIME)
        self.prune_handler.start()

    def register_handlers(self, app):
        # Register Authorization Endpoints
        app.register_local_handler(
            "/access/api_key", ['GET', 'POST'],
            self._handle_apikey_request, protocol=['http'])
        app.register_local_handler(
            "/access/oneshot_token", ['GET'],
            self._handle_token_request, protocol=['http'])

    async def _handle_apikey_request(self, web_request):
        action = web_request.get_action()
        if action.upper() == 'POST':
            self.api_key = self._create_api_key()
        return self.api_key

    async def _handle_token_request(self, web_request):
        return self.get_access_token()

    def _read_api_key(self):
        if os.path.exists(self.api_key_file):
            with open(self.api_key_file, 'r') as f:
                api_key = f.read()
            return api_key
        # API Key file doesn't exist.  Generate
        # a new api key and create the file.
        logging.info(
            f"No API Key file found, creating new one at:"
            f"\n{self.api_key_file}")
        return self._create_api_key()

    def _create_api_key(self):
        api_key = uuid.uuid4().hex
        with open(self.api_key_file, 'w') as f:
            f.write(api_key)
        return api_key

    def _check_authorized_ip(self, ip):
        if ip in self.trusted_ips:
            return True
        for rng in self.trusted_ranges:
            if ip in rng:
                return True
        return False

    def _prune_conn_handler(self):
        cur_time = time.time()
        expired_conns = []
        for ip, access_time in self.trusted_connections.items():
            if cur_time - access_time > CONNECTION_TIMEOUT:
                expired_conns.append(ip)
        for ip in expired_conns:
            self.trusted_connections.pop(ip, None)
            logging.info(
                f"Trusted Connection Expired, IP: {ip}")

    def _token_expire_handler(self, token):
        self.access_tokens.pop(token, None)

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
            elif self._check_authorized_ip(ip):
                logging.info(
                    f"Trusted Connection Detected, IP: {ip}")
                self.trusted_connections[ip] = time.time()
                return True
        return False

    def _check_access_token(self, token):
        if token in self.access_tokens:
            token_handler = self.access_tokens.pop(token, None)
            IOLoop.current().remove_timeout(token_handler)
            return True
        else:
            return False

    def check_authorized(self, request):
        # Authorization is disabled, request may pass
        if not self.auth_enabled:
            return True

        # Check if IP is trusted
        try:
            ip = ipaddress.ip_address(request.remote_ip)
        except ValueError:
            logging.exception(
                f"Unable to Create IP Address {request.remote_ip}")
            ip = None
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
