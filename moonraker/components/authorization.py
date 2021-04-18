# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import base64
import uuid
import hashlib
import hmac
import secrets
import os
import time
import datetime
import ipaddress
import json
import re
import logging
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.web import HTTPError
from utils import ServerError

ONESHOT_TIMEOUT = 5
TRUSTED_CONNECTION_TIMEOUT = 3600
PRUNE_CHECK_TIME = 300 * 1000

HASH_ITER = 100000
API_USER = "_API_KEY_USER_"
TRUSTED_USER = "_TRUSTED_USER_"
RESERVED_USERS = [API_USER, TRUSTED_USER]
JWT_EXP_TIME = datetime.timedelta(hours=1)
JWT_HEADER = {
    'alg': "HS256",
    'typ': "JWT"
}

# Helpers for base64url encoding and decoding
def base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=")

def base64url_decode(data):
    pad_cnt = len(data) % 4
    if pad_cnt:
        data += b"=" * (4 - pad_cnt)
    return base64.urlsafe_b64decode(data)

class Authorization:
    def __init__(self, config):
        self.server = config.get_server()
        self.login_timeout = config.getint('login_timeout', 90)
        database = self.server.lookup_component('database')
        database.register_local_namespace('authorized_users', forbidden=True)
        self.users = database.wrap_namespace('authorized_users')
        api_user = self.users.get(API_USER, None)
        if api_user is None:
            self.api_key = uuid.uuid4().hex
            self.users[API_USER] = {
                'username': API_USER,
                'api_key': self.api_key,
                'created_on': time.time()
            }
        else:
            self.api_key = api_user['api_key']
        self.trusted_users = {}
        self.oneshot_tokens = {}
        self.permitted_paths = set()

        # Get allowed cors domains
        self.cors_domains = []
        cors_cfg = config.get('cors_domains', "").strip()
        cds = [d.strip() for d in cors_cfg.split('\n')if d.strip()]
        for domain in cds:
            bad_match = re.search(r"^.+\.[^:]*\*", domain)
            if bad_match is not None:
                raise config.error(
                    f"Unsafe CORS Domain '{domain}'.  Wildcards are not"
                    " permitted in the top level domain.")
            self.cors_domains.append(
                domain.replace(".", "\\.").replace("*", ".*"))

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
        c_domains = "\n".join(self.cors_domains)

        logging.info(
            f"Authorization Configuration Loaded\n"
            f"Trusted Clients:\n{t_clients}\n"
            f"CORS Domains:\n{c_domains}")

        self.prune_handler = PeriodicCallback(
            self._prune_conn_handler, PRUNE_CHECK_TIME)
        self.prune_handler.start()

        # Register Authorization Endpoints
        self.permitted_paths.add("/access/login")
        self.permitted_paths.add("/access/refresh_jwt")
        self.server.register_endpoint(
            "/access/login", ['POST'], self._handle_login)
        self.server.register_endpoint(
            "/access/logout", ['POST'], self._handle_logout)
        self.server.register_endpoint(
            "/access/refresh_jwt", ['POST'], self._handle_refresh_jwt)
        self.server.register_endpoint(
            "/access/user", ['GET', 'POST', 'DELETE'],
            self._handle_user_request)
        self.server.register_endpoint(
            "/access/user/password", ['POST'], self._handle_password_reset)
        self.server.register_endpoint(
            "/access/api_key", ['GET', 'POST'],
            self._handle_apikey_request, protocol=['http'])
        self.server.register_endpoint(
            "/access/oneshot_token", ['GET'],
            self._handle_token_request, protocol=['http'])

    async def _handle_apikey_request(self, web_request):
        action = web_request.get_action()
        if action.upper() == 'POST':
            self.api_key = uuid.uuid4().hex
            self.users[f'{API_USER}.api_key'] = self.api_key
        return self.api_key

    async def _handle_token_request(self, web_request):
        ip = web_request.get_ip_address()
        user_info = web_request.get_current_user()
        return self.get_oneshot_token(ip, user_info)

    async def _handle_login(self, web_request):
        return self._login_jwt_user(web_request)

    async def _handle_logout(self, web_request):
        user_info = web_request.get_current_user()
        if user_info is None:
            raise self.server.error("No user logged in")
        username = user_info['username']
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid log out request for user {username}")
        self.users.pop(f"{username}.jwt_secret", None)
        return {
            "username": username,
            "action": "user_logged_out"
        }

    async def _handle_refresh_jwt(self, web_request):
        refresh_token = web_request.get_str('refresh_token')
        user_info = self._decode_jwt(refresh_token, token_type="refresh")
        username = user_info['username']
        secret = bytes.fromhex(user_info['jwt_secret'])
        token = self._generate_jwt(username, secret)
        return {
            'username': username,
            'token': token,
            'action': 'user_jwt_refresh'
        }

    async def _handle_user_request(self, web_request):
        action = web_request.get_action()
        if action == "GET":
            user = web_request.get_current_user()
            if user is None:
                return {
                    'username': None,
                    'created_on': None,
                }
            else:
                return {
                    'username': user['username'],
                    'created_on': user.get('created_on')
                }
        elif action == "POST":
            # Create User
            return self._login_jwt_user(web_request, create=True)
        elif action == "DELETE":
            # Delete User
            return self._delete_jwt_user(web_request)

    async def _handle_password_reset(self, web_request):
        password = web_request.get_str('password')
        new_pass = web_request.get_str('new_password')
        user_info = web_request.get_current_user()
        if user_info is None:
            raise self.server.error("No Current User")
        username = user_info['username']
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Reset Request for user {username}")
        salt = bytes.fromhex(user_info['salt'])
        hashed_pass = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), salt, HASH_ITER).hex()
        if hashed_pass != user_info['password']:
            raise self.server.error("Invalid Password")
        new_hashed_pass = hashlib.pbkdf2_hmac(
            'sha256', new_pass.encode(), salt, HASH_ITER).hex()
        self.users[f'{username}.password'] = new_hashed_pass
        return {
            'username': username,
            'action': "user_password_reset"
        }

    def _login_jwt_user(self, web_request, create=False):
        username = web_request.get_str('username')
        password = web_request.get_str('password')
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Request for user {username}")
        if create:
            if username in self.users:
                raise self.server.error(f"User {username} already exists")
            salt = secrets.token_bytes(32)
            hashed_pass = hashlib.pbkdf2_hmac(
                'sha256', password.encode(), salt, HASH_ITER).hex()
            user_info = {
                'username': username,
                'password': hashed_pass,
                'salt': salt.hex(),
                'created_on': time.time()
            }
            self.users[username] = user_info
            action = "user_created"
        else:
            if username not in self.users:
                raise self.server.error(f"Unregistered User: {username}")
            user_info = self.users[username]
            salt = bytes.fromhex(user_info['salt'])
            hashed_pass = hashlib.pbkdf2_hmac(
                'sha256', password.encode(), salt, HASH_ITER).hex()
            action = "user_logged_in"
        if hashed_pass != user_info['password']:
            raise self.server.error("Invalid Password")
        jwt_secret = user_info.get('jwt_secret', None)
        if jwt_secret is None:
            jwt_secret = secrets.token_bytes(32)
            user_info['jwt_secret'] = jwt_secret.hex()
            self.users[username] = user_info
        else:
            jwt_secret = bytes.fromhex(jwt_secret)
        token = self._generate_jwt(username, jwt_secret)
        refresh_token = self._generate_jwt(
            username, jwt_secret, token_type="refresh",
            exp_time=datetime.timedelta(days=self.login_timeout))
        return {
            'username': username,
            'token': token,
            'refresh_token': refresh_token,
            'action': action
        }

    def _delete_jwt_user(self, web_request):
        password = web_request.get_str('password')
        user_info = web_request.get_current_user()
        if user_info is None:
            raise self.server.error("No Current User")
        username = user_info['username']
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid request for user {username}")
        salt = bytes.fromhex(user_info['salt'])
        hashed_pass = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), salt, HASH_ITER).hex()
        if hashed_pass != user_info['password']:
            raise self.server.error("Invalid Password")
        del self.users[username]
        return {
            "username": username,
            "action": "user_deleted"
        }

    def _generate_jwt(self, username, secret, token_type="auth",
                      exp_time=JWT_EXP_TIME):
        curtime = time.time()
        payload = {
            'iss': "Moonraker",
            'iat': curtime,
            'exp': curtime + exp_time.total_seconds(),
            'username': username,
            'token_type': token_type
        }
        enc_header = base64url_encode(json.dumps(JWT_HEADER).encode())
        enc_payload = base64url_encode(json.dumps(payload).encode())
        message = enc_header + b"." + enc_payload
        signature = base64url_encode(hmac.digest(secret, message, "sha256"))
        message += b"." + signature
        return message.decode()

    def _decode_jwt(self, jwt, token_type="auth"):
        parts = jwt.encode().split(b".")
        if len(parts) != 3:
            raise self.server.error(f"Invalid JWT length of {len(parts)}")
        header = json.loads(base64url_decode(parts[0]))
        payload = json.loads(base64url_decode(parts[1]))
        if header != JWT_HEADER:
            raise self.server.error("Invalid JWT header")
        recd_type = payload.get('token_type', "")
        if token_type != recd_type:
            raise self.server.error(
                f"JWT Token type mismatch: Expected {token_type}, "
                f"Recd: {recd_type}", 401)
        if time.time() > payload['exp']:
            raise self.server.error("JWT expired", 401)
        username = payload.get('username')
        user_info = self.users.get(username, None)
        if user_info is None:
            raise self.server.error(
                f"Invalid JWT, no registered user {username}", 401)
        jwt_secret = user_info.get('jwt_secret', None)
        if jwt_secret is None:
            raise self.server.error(
                f"Invalid JWT, user {username} not logged in", 401)
        secret = bytes.fromhex(jwt_secret)
        # Decode and verify signature
        signature = base64url_decode(parts[2])
        calc_sig = hmac.digest(
            secret, parts[0] + b"." + parts[1], "sha256")
        if signature != calc_sig:
            raise self.server.error("Invalid JWT signature")
        return user_info

    def _prune_conn_handler(self):
        cur_time = time.time()
        for ip, user_info in list(self.trusted_users.items()):
            exp_time = user_info['expires_at']
            if cur_time >= exp_time:
                self.trusted_users.pop(ip, None)
                logging.info(
                    f"Trusted Connection Expired, IP: {ip}")

    def _oneshot_token_expire_handler(self, token):
        self.oneshot_tokens.pop(token, None)

    def get_oneshot_token(self, ip_addr, user):
        token = base64.b32encode(os.urandom(20)).decode()
        ioloop = IOLoop.current()
        hdl = ioloop.call_later(
            ONESHOT_TIMEOUT, self._oneshot_token_expire_handler, token)
        self.oneshot_tokens[token] = (ip_addr, user, hdl)
        return token

    def _check_json_web_token(self, request):
        auth_token = request.headers.get("Authorization")
        if auth_token is None:
            auth_token = request.headers.get("X-Access-Token")
        if auth_token and auth_token.startswith("Bearer "):
            auth_token = auth_token[7:]
            try:
                return self._decode_jwt(auth_token)
            except Exception as e:
                raise HTTPError(401, str(e))
        return None

    def _check_authorized_ip(self, ip):
        if ip in self.trusted_ips:
            return True
        for rng in self.trusted_ranges:
            if ip in rng:
                return True
        return False

    def _check_trusted_connection(self, ip):
        if ip is not None:
            curtime = time.time()
            exp_time = curtime + TRUSTED_CONNECTION_TIMEOUT
            if ip in self.trusted_users:
                self.trusted_users[ip]['expires_at'] = exp_time
                return self.trusted_users[ip]
            elif self._check_authorized_ip(ip):
                logging.info(
                    f"Trusted Connection Detected, IP: {ip}")
                self.trusted_users[ip] = {
                    'username': TRUSTED_USER,
                    'password': None,
                    'created_on': curtime,
                    'expires_at': exp_time
                }
                return self.trusted_users[ip]
        return None

    def _check_oneshot_token(self, token, cur_ip):
        if token in self.oneshot_tokens:
            ip_addr, user, hdl = self.oneshot_tokens.pop(token)
            IOLoop.current().remove_timeout(hdl)
            if cur_ip != ip_addr:
                logging.info(f"Oneshot Token IP Mismatch: expected{ip_addr}"
                             f", Recd: {cur_ip}")
                return None
            return user
        else:
            return None

    def check_authorized(self, request):
        if request.path in self.permitted_paths:
            return None

        # Check JSON Web Token
        jwt_user = self._check_json_web_token(request)
        if jwt_user is not None:
            return jwt_user

        try:
            ip = ipaddress.ip_address(request.remote_ip)
        except ValueError:
            logging.exception(
                f"Unable to Create IP Address {request.remote_ip}")
            ip = None

        # Check oneshot access token
        ost = request.arguments.get('token', None)
        if ost is not None:
            ost_user = self._check_oneshot_token(ost[-1].decode(), ip)
            if ost_user is not None:
                return ost_user

        # Check API Key Header
        key = request.headers.get("X-Api-Key")
        if key and key == self.api_key:
            return self.users[API_USER]

        # Check if IP is trusted
        trusted_user = self._check_trusted_connection(ip)
        if trusted_user is not None:
            return trusted_user

        raise HTTPError(401, "Unauthorized")

    def check_cors(self, origin, request=None):
        if origin is None or not self.cors_domains:
            return False
        for regex in self.cors_domains:
            match = re.match(regex, origin)
            if match is not None:
                if match.group() == origin:
                    logging.debug(f"CORS Pattern Matched, origin: {origin} "
                                  f" | pattern: {regex}")
                    self._set_cors_headers(origin, request)
                    return True
                else:
                    logging.debug(f"Partial Cors Match: {match.group()}")
        else:
            # Check to see if the origin contains an IP that matches a
            # current trusted connection
            match = re.search(r"^https?://([^/:]+)", origin)
            if match is not None:
                ip = match.group(1)
                try:
                    ipaddr = ipaddress.ip_address(ip)
                except ValueError:
                    pass
                else:
                    if self._check_authorized_ip(ipaddr):
                        logging.debug(
                            f"Cors request matched trusted IP: {ip}")
                        self._set_cors_headers(origin, request)
                        return True
            logging.debug(f"No CORS match for origin: {origin}\n"
                          f"Patterns: {self.cors_domains}")
        return False

    def _set_cors_headers(self, origin, request):
        if request is None:
            return
        request.set_header("Access-Control-Allow-Origin", origin)
        request.set_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS")
        request.set_header(
            "Access-Control-Allow-Headers",
            "Origin, Accept, Content-Type, X-Requested-With, "
            "X-CRSF-Token, Authorization, X-Access-Token, "
            "X-Api-Key")

    def close(self):
        self.prune_handler.stop()


def load_component(config):
    return Authorization(config)
