# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import base64
import uuid
import hashlib
import secrets
import os
import time
import datetime
import ipaddress
import re
import socket
import logging
from jose import jwt
from tornado.ioloop import IOLoop, PeriodicCallback
from tornado.web import HTTPError

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Set,
    Optional,
    Union,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from tornado.httputil import HTTPServerRequest
    from tornado.web import RequestHandler
    from . import database
    DBComp = database.MoonrakerDatabase
    IPAddr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
    OneshotToken = Tuple[IPAddr, Optional[Dict[str, Any]], object]

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

class Authorization:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.login_timeout = config.getint('login_timeout', 90)
        self.force_logins = config.getboolean('force_logins', False)
        database: DBComp = self.server.lookup_component('database')
        database.register_local_namespace('authorized_users', forbidden=True)
        self.users = database.wrap_namespace('authorized_users')
        api_user: Optional[Dict[str, Any]] = self.users.get(API_USER, None)
        if api_user is None:
            self.api_key = uuid.uuid4().hex
            self.users[API_USER] = {
                'username': API_USER,
                'api_key': self.api_key,
                'created_on': time.time()
            }
        else:
            self.api_key = api_user['api_key']
        self.trusted_users: Dict[IPAddr, Any] = {}
        self.oneshot_tokens: Dict[str, OneshotToken] = {}
        self.permitted_paths: Set[str] = set()
        host_name, port = self.server.get_host_info()
        self.issuer = f"http://{host_name}:{port}"

        # Get allowed cors domains
        self.cors_domains: List[str] = []
        cors_cfg = config.get('cors_domains', "").strip()
        cds = [d.strip() for d in cors_cfg.split('\n') if d.strip()]
        for domain in cds:
            bad_match = re.search(r"^.+\.[^:]*\*", domain)
            if bad_match is not None:
                raise config.error(
                    f"Unsafe CORS Domain '{domain}'.  Wildcards are not"
                    " permitted in the top level domain.")
            self.cors_domains.append(
                domain.replace(".", "\\.").replace("*", ".*"))

        # Get Trusted Clients
        self.trusted_ips: List[IPAddr] = []
        self.trusted_ranges: List[IPNetwork] = []
        self.trusted_domains: List[str] = []
        tcs = config.get('trusted_clients', "")
        trusted_clients = [c.strip() for c in tcs.split('\n') if c.strip()]
        for val in trusted_clients:
            # Check IP address
            try:
                tc = ipaddress.ip_address(val)
            except ValueError:
                pass
            else:
                self.trusted_ips.append(tc)
                continue
            # Check ip network
            try:
                tc = ipaddress.ip_network(val)
            except ValueError:
                pass
            else:
                self.trusted_ranges.append(tc)
                continue
            # Check hostname
            self.trusted_domains.append(val.lower())

        t_clients = "\n".join(
            [str(ip) for ip in self.trusted_ips] +
            [str(rng) for rng in self.trusted_ranges] +
            self.trusted_domains)
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
            "/access/login", ['POST'], self._handle_login,
            protocol=['http'])
        self.server.register_endpoint(
            "/access/logout", ['POST'], self._handle_logout,
            protocol=['http'])
        self.server.register_endpoint(
            "/access/refresh_jwt", ['POST'], self._handle_refresh_jwt,
            protocol=['http'])
        self.server.register_endpoint(
            "/access/user", ['GET', 'POST', 'DELETE'],
            self._handle_user_request, protocol=['http'])
        self.server.register_endpoint(
            "/access/users/list", ['GET'], self._handle_list_request,
            protocol=['http'])
        self.server.register_endpoint(
            "/access/user/password", ['POST'], self._handle_password_reset,
            protocol=['http'])
        self.server.register_endpoint(
            "/access/api_key", ['GET', 'POST'],
            self._handle_apikey_request, protocol=['http'])
        self.server.register_endpoint(
            "/access/oneshot_token", ['GET'],
            self._handle_token_request, protocol=['http'])
        self.server.register_notification("authorization:user_created")
        self.server.register_notification("authorization:user_deleted")

    async def _handle_apikey_request(self, web_request: WebRequest) -> str:
        action = web_request.get_action()
        if action.upper() == 'POST':
            self.api_key = uuid.uuid4().hex
            self.users[f'{API_USER}.api_key'] = self.api_key
        return self.api_key

    async def _handle_token_request(self, web_request: WebRequest) -> str:
        ip = web_request.get_ip_address()
        assert ip is not None
        user_info = web_request.get_current_user()
        return self.get_oneshot_token(ip, user_info)

    async def _handle_login(self, web_request: WebRequest) -> Dict[str, Any]:
        return self._login_jwt_user(web_request)

    async def _handle_logout(self, web_request: WebRequest) -> Dict[str, str]:
        user_info = web_request.get_current_user()
        if user_info is None:
            raise self.server.error("No user logged in")
        username: str = user_info['username']
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid log out request for user {username}")
        self.users.pop(f"{username}.jwt_secret", None)
        return {
            "username": username,
            "action": "user_logged_out"
        }

    async def _handle_refresh_jwt(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, str]:
        refresh_token: str = web_request.get_str('refresh_token')
        user_info = self._decode_jwt(refresh_token, token_type="refresh")
        username: str = user_info['username']
        secret = user_info['jwt_secret']
        token = self._generate_jwt(username, secret)
        return {
            'username': username,
            'token': token,
            'action': 'user_jwt_refresh'
        }

    async def _handle_user_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
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
        raise self.server.error("Invalid Request Method")

    async def _handle_list_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, List[Dict[str, Any]]]:
        user_list = []
        for user in self.users.values():
            if user['username'] == API_USER:
                continue
            user_list.append({
                'username': user['username'],
                'created_on': user['created_on']
            })
        return {
            'users': user_list
        }

    async def _handle_password_reset(self,
                                     web_request: WebRequest
                                     ) -> Dict[str, str]:
        password: str = web_request.get_str('password')
        new_pass: str = web_request.get_str('new_password')
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

    def _login_jwt_user(self,
                        web_request: WebRequest,
                        create: bool = False
                        ) -> Dict[str, Any]:
        username: str = web_request.get_str('username')
        password: str = web_request.get_str('password')
        user_info: Dict[str, Any]
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
        jwt_secret: Optional[str] = user_info.get('jwt_secret', None)
        if jwt_secret is None:
            jwt_secret = secrets.token_bytes(32).hex()
            user_info['jwt_secret'] = jwt_secret
            self.users[username] = user_info
        token = self._generate_jwt(username, jwt_secret)
        refresh_token = self._generate_jwt(
            username, jwt_secret, token_type="refresh",
            exp_time=datetime.timedelta(days=self.login_timeout))
        if create:
            IOLoop.current().call_later(
                .005, self.server.send_event,
                "authorization:user_created",
                {'username': username})
        return {
            'username': username,
            'token': token,
            'refresh_token': refresh_token,
            'action': action
        }

    def _delete_jwt_user(self, web_request: WebRequest) -> Dict[str, str]:
        username: str = web_request.get_str('username')
        current_user = web_request.get_current_user()
        if current_user is not None:
            curname = current_user.get('username', None)
            if curname is not None and curname == username:
                raise self.server.error(
                    f"Cannot delete logged in user {curname}")
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Request for reserved user {username}")
        user_info: Optional[Dict[str, Any]] = self.users.get(username)
        if user_info is None:
            raise self.server.error(f"No registered user: {username}")
        del self.users[username]
        IOLoop.current().call_later(
            .005, self.server.send_event,
            "authorization:user_deleted",
            {'username': username})
        return {
            "username": username,
            "action": "user_deleted"
        }

    def _generate_jwt(self,
                      username: str,
                      secret: str,
                      token_type: str = "access",
                      exp_time: datetime.timedelta = JWT_EXP_TIME
                      ) -> str:
        curtime = datetime.datetime.utcnow()
        payload = {
            'iss': self.issuer,
            'aud': "Moonraker",
            'iat': curtime,
            'exp': curtime + exp_time,
            'username': username,
            'token_type': token_type
        }
        return jwt.encode(payload, secret, headers=JWT_HEADER)

    def _decode_jwt(self,
                    token: str,
                    token_type: str = "access"
                    ) -> Dict[str, Any]:
        header: Dict[str, Any] = jwt.get_unverified_header(token)
        payload: Dict[str, Any] = jwt.get_unverified_claims(token)
        if header != JWT_HEADER:
            raise self.server.error("Invalid JWT header")
        recd_type: str = payload.get('token_type', "")
        if token_type != recd_type:
            raise self.server.error(
                f"JWT Token type mismatch: Expected {token_type}, "
                f"Recd: {recd_type}", 401)
        username: str = payload['username']
        user_info: Dict[str, Any] = self.users.get(username, None)
        if user_info is None:
            raise self.server.error(
                f"Invalid JWT, no registered user {username}", 401)
        jwt_secret: Optional[str] = user_info.get('jwt_secret', None)
        if jwt_secret is None:
            raise self.server.error(
                f"Invalid JWT, user {username} not logged in", 401)
        jwt.decode(token, jwt_secret, algorithms=['HS256'],
                   audience="Moonraker")
        return user_info

    def _prune_conn_handler(self) -> None:
        cur_time = time.time()
        for ip, user_info in list(self.trusted_users.items()):
            exp_time: float = user_info['expires_at']
            if cur_time >= exp_time:
                self.trusted_users.pop(ip, None)
                logging.info(
                    f"Trusted Connection Expired, IP: {ip}")

    def _oneshot_token_expire_handler(self, token):
        self.oneshot_tokens.pop(token, None)

    def get_oneshot_token(self,
                          ip_addr: IPAddr,
                          user: Optional[Dict[str, Any]]
                          ) -> str:
        token = base64.b32encode(os.urandom(20)).decode()
        ioloop = IOLoop.current()
        hdl = ioloop.call_later(
            ONESHOT_TIMEOUT, self._oneshot_token_expire_handler, token)
        self.oneshot_tokens[token] = (ip_addr, user, hdl)
        return token

    def _check_json_web_token(self,
                              request: HTTPServerRequest
                              ) -> Optional[Dict[str, Any]]:
        auth_token: Optional[str] = request.headers.get("Authorization")
        if auth_token is None:
            auth_token = request.headers.get("X-Access-Token")
        if auth_token and auth_token.startswith("Bearer "):
            auth_token = auth_token[7:]
        else:
            qtoken = request.query_arguments.get('access_token', None)
            if qtoken is not None:
                auth_token = qtoken[-1].decode()
        if auth_token:
            try:
                return self._decode_jwt(auth_token)
            except Exception as e:
                raise HTTPError(401, str(e))
        return None

    def _check_authorized_ip(self, ip: IPAddr) -> bool:
        if ip in self.trusted_ips:
            return True
        for rng in self.trusted_ranges:
            if ip in rng:
                return True
        fqdn = socket.getfqdn(str(ip)).lower()
        if fqdn in self.trusted_domains:
            return True
        return False

    def _check_trusted_connection(self,
                                  ip: Optional[IPAddr]
                                  ) -> Optional[Dict[str, Any]]:
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

    def _check_oneshot_token(self,
                             token: str,
                             cur_ip: IPAddr
                             ) -> Optional[Dict[str, Any]]:
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

    def check_authorized(self,
                         request: HTTPServerRequest
                         ) -> Optional[Dict[str, Any]]:
        if request.path in self.permitted_paths or \
                request.method == "OPTIONS":
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
        ost: Optional[List[bytes]] = request.arguments.get('token', None)
        if ost is not None:
            ost_user = self._check_oneshot_token(ost[-1].decode(), ip)
            if ost_user is not None:
                return ost_user

        # Check API Key Header
        key: Optional[str] = request.headers.get("X-Api-Key")
        if key and key == self.api_key:
            return self.users[API_USER]

        # If the force_logins option is enabled and at least one
        # user is created this is an unauthorized request
        if self.force_logins and len(self.users) > 1:
            raise HTTPError(401, "Unauthorized")

        # Check if IP is trusted
        trusted_user = self._check_trusted_connection(ip)
        if trusted_user is not None:
            return trusted_user

        raise HTTPError(401, "Unauthorized")

    def check_cors(self,
                   origin: Optional[str],
                   req_hdlr: Optional[RequestHandler] = None
                   ) -> bool:
        if origin is None or not self.cors_domains:
            return False
        for regex in self.cors_domains:
            match = re.match(regex, origin)
            if match is not None:
                if match.group() == origin:
                    logging.debug(f"CORS Pattern Matched, origin: {origin} "
                                  f" | pattern: {regex}")
                    self._set_cors_headers(origin, req_hdlr)
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
                        self._set_cors_headers(origin, req_hdlr)
                        return True
            logging.debug(f"No CORS match for origin: {origin}\n"
                          f"Patterns: {self.cors_domains}")
        return False

    def _set_cors_headers(self,
                          origin: str,
                          req_hdlr: Optional[RequestHandler]
                          ) -> None:
        if req_hdlr is None:
            return
        req_hdlr.set_header("Access-Control-Allow-Origin", origin)
        req_hdlr.set_header(
            "Access-Control-Allow-Methods",
            "GET, POST, PUT, DELETE, OPTIONS")
        req_hdlr.set_header(
            "Access-Control-Allow-Headers",
            "Origin, Accept, Content-Type, X-Requested-With, "
            "X-CRSF-Token, Authorization, X-Access-Token, "
            "X-Api-Key")

    def close(self) -> None:
        self.prune_handler.stop()


def load_component(config: ConfigHelper) -> Authorization:
    return Authorization(config)
