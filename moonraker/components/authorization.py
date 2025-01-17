# API Key Based Authorization
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import asyncio
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
from tornado.web import HTTPError
from libnacl.sign import Signer, Verifier
from ..utils import json_wrapper as jsonw
from ..common import RequestType, TransportType, SqlTableDefinition, UserInfo

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Tuple,
    Optional,
    Union,
    Dict,
    List,
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .websockets import WebsocketManager
    from tornado.httputil import HTTPServerRequest
    from .database import MoonrakerDatabase as DBComp
    from .database import DBProviderWrapper
    from .ldap import MoonrakerLDAP
    IPAddr = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
    IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
    OneshotToken = Tuple[IPAddr, Optional[UserInfo], asyncio.Handle]

# Helpers for base64url encoding and decoding
def base64url_encode(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")

def base64url_decode(data: str) -> bytes:
    pad_cnt = len(data) % 4
    if pad_cnt:
        data += "=" * (4 - pad_cnt)
    return base64.urlsafe_b64decode(data)


ONESHOT_TIMEOUT = 5
TRUSTED_CONNECTION_TIMEOUT = 3600
FQDN_CACHE_TIMEOUT = 84000
PRUNE_CHECK_TIME = 300.

USER_TABLE = "authorized_users"
AUTH_SOURCES = ["moonraker", "ldap"]
HASH_ITER = 100000
API_USER = "_API_KEY_USER_"
TRUSTED_USER = "_TRUSTED_USER_"
RESERVED_USERS = [API_USER, TRUSTED_USER]
JWT_EXP_TIME = datetime.timedelta(hours=1)
JWT_HEADER = {
    'alg': "EdDSA",
    'typ': "JWT"
}

class UserSqlDefinition(SqlTableDefinition):
    name = USER_TABLE
    prototype = (
        f"""
        {USER_TABLE} (
            username TEXT PRIMARY KEY NOT NULL,
            password TEXT NOT NULL,
            created_on REAL NOT NULL,
            salt TEXT NOT NULL,
            source TEXT NOT NULL,
            jwt_secret TEXT,
            jwk_id TEXT,
            groups pyjson
        )
        """
    )
    version = 1

    def migrate(self, last_version: int, db_provider: DBProviderWrapper) -> None:
        if last_version == 0:
            users: Dict[str, Dict[str, Any]]
            users = db_provider.get_namespace("authorized_users")
            api_user = users.pop(API_USER, {})
            if not isinstance(api_user, dict):
                api_user = {}
            user_vals: List[Tuple[Any, ...]] = [
                UserInfo(
                    username=API_USER,
                    password=api_user.get("api_key", uuid.uuid4().hex),
                    created_on=api_user.get("created_on", time.time())
                ).as_tuple()
            ]
            for key, user in users.items():
                if not isinstance(user, dict):
                    logging.info(
                        f"Auth migration, skipping invalid value: {key} {user}"
                    )
                    continue
                user_vals.append(UserInfo(**user).as_tuple())
            placeholders = ",".join("?" * len(user_vals[0]))
            conn = db_provider.connection
            with conn:
                conn.executemany(
                    f"INSERT OR IGNORE INTO {USER_TABLE} VALUES({placeholders})",
                    user_vals
                )
            db_provider.wipe_local_namespace("authorized_users")

class Authorization:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.login_timeout = config.getint('login_timeout', 90)
        self.force_logins = config.getboolean('force_logins', False)
        self.default_source = config.get('default_source', "moonraker").lower()
        self.enable_api_key = config.getboolean('enable_api_key', True)
        self.max_logins = config.getint("max_login_attempts", None, above=0)
        self.failed_logins: Dict[IPAddr, int] = {}
        self.fqdn_cache: Dict[IPAddr, Dict[str, Any]] = {}
        if self.default_source not in AUTH_SOURCES:
            self.server.add_warning(
                "[authorization]: option 'default_source' - Invalid "
                f"value '{self.default_source}', falling back to "
                "'moonraker'."
            )
            self.default_source = "moonraker"
        self.ldap: Optional[MoonrakerLDAP] = None
        if config.has_section("ldap"):
            self.ldap = self.server.load_component(config, "ldap", None)
        if self.default_source == "ldap" and self.ldap is None:
            self.server.add_warning(
                "[authorization]: Option 'default_source' set to 'ldap',"
                " however [ldap] section failed to load or not configured"
            )
        database: DBComp = self.server.lookup_component('database')
        self.user_table = database.register_table(UserSqlDefinition())
        self.users: Dict[str, UserInfo] = {}
        self.api_key = uuid.uuid4().hex
        hi = self.server.get_host_info()
        self.issuer = f"http://{hi['hostname']}:{hi['port']}"
        self.public_jwks: Dict[str, Dict[str, Any]] = {}
        self.trusted_users: Dict[IPAddr, Dict[str, Any]] = {}
        self.oneshot_tokens: Dict[str, OneshotToken] = {}

        # Get allowed cors domains
        self.cors_domains: List[str] = []
        for domain in config.getlist('cors_domains', []):
            bad_match = re.search(r"^.+\.[^:]*\*", domain)
            if bad_match is not None:
                self.server.add_warning(
                    f"[authorization]: Unsafe domain '{domain}' in option "
                    f"'cors_domains'. Wildcards are not permitted in the"
                    " top level domain."
                )
                continue
            if domain.endswith("/"):
                self.server.add_warning(
                    f"[authorization]: Invalid domain '{domain}' in option "
                    "'cors_domains'.  Domain's cannot contain a trailing "
                    "slash."
                )
            else:
                self.cors_domains.append(
                    domain.replace(".", "\\.").replace("*", ".*"))

        # Get Trusted Clients
        self.trusted_ips: List[IPAddr] = []
        self.trusted_ranges: List[IPNetwork] = []
        self.trusted_domains: List[str] = []
        for val in config.getlist('trusted_clients', []):
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
                tn = ipaddress.ip_network(val)
            except ValueError as e:
                if "has host bits set" in str(e):
                    self.server.add_warning(
                        f"[authorization]: Invalid CIDR expression '{val}' "
                        "in option 'trusted_clients'")
                    continue
                pass
            else:
                self.trusted_ranges.append(tn)
                continue
            # Check hostname
            match = re.match(r"([a-z0-9]+(-[a-z0-9]+)*\.?)+[a-z]{2,}$", val)
            if match is not None:
                self.trusted_domains.append(val.lower())
            else:
                self.server.add_warning(
                    f"[authorization]: Invalid domain name '{val}' "
                    "in option 'trusted_clients'")

        t_clients = "\n".join(
            [str(ip) for ip in self.trusted_ips] +
            [str(rng) for rng in self.trusted_ranges] +
            self.trusted_domains)
        c_domains = "\n".join(self.cors_domains)

        logging.info(
            f"Authorization Configuration Loaded\n"
            f"Trusted Clients:\n{t_clients}\n"
            f"CORS Domains:\n{c_domains}")

        eventloop = self.server.get_event_loop()
        self.prune_timer = eventloop.register_timer(
            self._prune_conn_handler)

        # Register Authorization Endpoints
        self.server.register_endpoint(
            "/access/login", RequestType.POST, self._handle_login,
            transports=TransportType.HTTP | TransportType.WEBSOCKET,
            auth_required=False
        )
        self.server.register_endpoint(
            "/access/logout", RequestType.POST, self._handle_logout,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/refresh_jwt", RequestType.POST, self._handle_refresh_jwt,
            transports=TransportType.HTTP | TransportType.WEBSOCKET,
            auth_required=False
        )
        self.server.register_endpoint(
            "/access/user", RequestType.all(), self._handle_user_request,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/users/list", RequestType.GET, self._handle_list_request,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/user/password", RequestType.POST, self._handle_password_reset,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/api_key", RequestType.GET | RequestType.POST,
            self._handle_apikey_request,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/oneshot_token", RequestType.GET, self._handle_oneshot_request,
            transports=TransportType.HTTP | TransportType.WEBSOCKET
        )
        self.server.register_endpoint(
            "/access/info", RequestType.GET, self._handle_info_request,
            transports=TransportType.HTTP | TransportType.WEBSOCKET,
            auth_required=False
        )
        wsm: WebsocketManager = self.server.lookup_component("websockets")
        wsm.register_notification("authorization:user_created")
        wsm.register_notification(
            "authorization:user_deleted", event_type="logout"
        )
        wsm.register_notification(
            "authorization:user_logged_out", event_type="logout"
        )

    async def component_init(self) -> None:
        # Populate users from database
        cursor = await self.user_table.execute(f"SELECT * FROM {USER_TABLE}")
        self.users = {row[0]: UserInfo(**dict(row)) for row in await cursor.fetchall()}
        need_sync = self._initialize_users()
        if need_sync:
            await self._sync_user_table()
        self.prune_timer.start(delay=PRUNE_CHECK_TIME)

    async def _sync_user(self, username: str) -> None:
        user = self.users[username]
        vals = user.as_tuple()
        placeholders = ",".join("?" * len(vals))
        async with self.user_table as tx:
            await tx.execute(
                f"REPLACE INTO {USER_TABLE} VALUES({placeholders})", vals
            )

    async def _sync_user_table(self) -> None:
        async with self.user_table as tx:
            await tx.execute(f"DELETE FROM {USER_TABLE}")
            user_vals: List[Tuple[Any, ...]]
            user_vals = [user.as_tuple() for user in self.users.values()]
            if not user_vals:
                return
            placeholders = ",".join("?" * len(user_vals[0]))
            await tx.executemany(
                f"INSERT INTO {USER_TABLE} VALUES({placeholders})", user_vals
            )

    def _initialize_users(self) -> bool:
        need_sync = False
        api_user: Optional[UserInfo] = self.users.get(API_USER, None)
        if api_user is None:
            need_sync = True
            self.users[API_USER] = UserInfo(username=API_USER, password=self.api_key)
        else:
            self.api_key = api_user.password
        for username, user_info in list(self.users.items()):
            if username == API_USER:
                continue
            # generate jwks for valid users
            if user_info.jwt_secret is not None:
                try:
                    priv_key = self._load_private_key(user_info.jwt_secret)
                    jwk_id = user_info.jwk_id
                    assert jwk_id is not None
                except (self.server.error, KeyError, AssertionError):
                    logging.info("Invalid jwk found for user, removing")
                    user_info.jwt_secret = None
                    user_info.jwk_id = None
                    self.users[username] = user_info
                    need_sync = True
                    continue
                self.public_jwks[jwk_id] = self._generate_public_jwk(priv_key)
        return need_sync

    async def _handle_apikey_request(self, web_request: WebRequest) -> str:
        if web_request.get_request_type() == RequestType.POST:
            self.api_key = uuid.uuid4().hex
            self.users[API_USER].password = self.api_key
            await self._sync_user(API_USER)
        return self.api_key

    async def _handle_oneshot_request(self, web_request: WebRequest) -> str:
        ip = web_request.get_ip_address()
        assert ip is not None
        user_info = web_request.get_current_user()
        return self.get_oneshot_token(ip, user_info)

    async def _handle_login(self, web_request: WebRequest) -> Dict[str, Any]:
        ip = web_request.get_ip_address()
        if ip is not None and self.check_logins_maxed(ip):
            raise HTTPError(
                401, "Unauthorized, Maximum Login Attempts Reached"
            )
        try:
            ret = await self._login_jwt_user(web_request)
        except asyncio.CancelledError:
            raise
        except Exception:
            if ip is not None:
                failed = self.failed_logins.get(ip, 0)
                self.failed_logins[ip] = failed + 1
            raise
        if ip is not None:
            self.failed_logins.pop(ip, None)
        return ret

    async def _handle_logout(self, web_request: WebRequest) -> Dict[str, str]:
        user_info = web_request.get_current_user()
        if user_info is None:
            raise self.server.error("No user logged in")
        username: str = user_info.username
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid log out request for user {username}")
        jwk_id: Optional[str] = self.users[username].jwk_id
        self.users[username].jwt_secret = None
        self.users[username].jwk_id = None
        if jwk_id is not None:
            self.public_jwks.pop(jwk_id, None)
        await self._sync_user(username)
        eventloop = self.server.get_event_loop()
        eventloop.delay_callback(
            .005, self.server.send_event, "authorization:user_logged_out",
            {'username': username}
        )
        return {
            "username": username,
            "action": "user_logged_out"
        }

    async def _handle_info_request(self, web_request: WebRequest) -> Dict[str, Any]:
        sources = ["moonraker"]
        if self.ldap is not None:
            sources.append("ldap")
        login_req = self.force_logins and len(self.users) > 1
        request_trusted: Optional[bool] = None
        user = web_request.get_current_user()
        req_ip = web_request.ip_addr
        if user is not None and user.username == TRUSTED_USER:
            request_trusted = True
        elif req_ip is not None:
            request_trusted = await self._check_authorized_ip(req_ip)
        return {
            "default_source": self.default_source,
            "available_sources": sources,
            "login_required": login_req,
            "trusted": request_trusted
        }

    async def _handle_refresh_jwt(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, str]:
        refresh_token: str = web_request.get_str('refresh_token')
        try:
            user_info = self.decode_jwt(refresh_token, token_type="refresh")
        except Exception:
            raise self.server.error("Invalid Refresh Token", 401)
        username: str = user_info.username
        if user_info.jwt_secret is None or user_info.jwk_id is None:
            raise self.server.error("User not logged in", 401)
        private_key = self._load_private_key(user_info.jwt_secret)
        jwk_id: str = user_info.jwk_id
        token = self._generate_jwt(username, jwk_id, private_key)
        return {
            'username': username,
            'token': token,
            'source': user_info.source,
            'action': 'user_jwt_refresh'
        }

    async def _handle_user_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        req_type = web_request.get_request_type()
        if req_type == RequestType.GET:
            user = web_request.get_current_user()
            if user is None:
                return {
                    "username": None,
                    "source": None,
                    "created_on": None,
                }
            else:
                return {
                    "username": user.username,
                    "source": user.source,
                    "created_on": user.created_on
                }
        elif req_type == RequestType.POST:
            # Create User
            return await self._login_jwt_user(web_request, create=True)
        elif req_type == RequestType.DELETE:
            # Delete User
            return await self._delete_jwt_user(web_request)
        raise self.server.error("Invalid Request Method")

    async def _handle_list_request(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, List[Dict[str, Any]]]:
        user_list = []
        for user in self.users.values():
            if user.username == API_USER:
                continue
            user_list.append({
                'username': user.username,
                'source': user.source,
                'created_on': user.created_on
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
        username = user_info.username
        if user_info.source == "ldap":
            raise self.server.error(
                f"Can´t Reset password for ldap user {username}")
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Reset Request for user {username}")
        salt = bytes.fromhex(user_info.salt)
        hashed_pass = hashlib.pbkdf2_hmac(
            'sha256', password.encode(), salt, HASH_ITER).hex()
        if hashed_pass != user_info.password:
            raise self.server.error("Invalid Password")
        new_hashed_pass = hashlib.pbkdf2_hmac(
            'sha256', new_pass.encode(), salt, HASH_ITER).hex()
        self.users[username].password = new_hashed_pass
        await self._sync_user(username)
        return {
            'username': username,
            'action': "user_password_reset"
        }

    async def _login_jwt_user(
        self, web_request: WebRequest, create: bool = False
    ) -> Dict[str, Any]:
        username: str = web_request.get_str('username')
        password: str = web_request.get_str('password')
        source: str = web_request.get_str(
            'source', self.default_source
        ).lower()
        if source not in AUTH_SOURCES:
            raise self.server.error(f"Invalid 'source': {source}")
        user_info: UserInfo
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Request for user {username}")
        if source == "ldap":
            if create:
                raise self.server.error("Cannot Create LDAP User")
            if self.ldap is None:
                raise self.server.error(
                    "LDAP authentication not available", 401
                )
            await self.ldap.authenticate_ldap_user(username, password)
            if username not in self.users:
                create = True
        if create:
            if username in self.users:
                raise self.server.error(f"User {username} already exists")
            salt = secrets.token_bytes(32)
            hashed_pass = hashlib.pbkdf2_hmac(
                'sha256', password.encode(), salt, HASH_ITER).hex()
            user_info = UserInfo(
                username=username,
                password=hashed_pass,
                salt=salt.hex(),
                source=source,
            )
            self.users[username] = user_info
            await self._sync_user(username)
            action = "user_created"
            if source == "ldap":
                # Dont notify user created
                action = "user_logged_in"
                create = False
        else:
            if username not in self.users:
                raise self.server.error(f"Unregistered User: {username}")
            user_info = self.users[username]
            auth_src = user_info.source
            if auth_src != source:
                raise self.server.error(
                    f"Moonraker cannot authenticate user '{username}', must "
                    f"specify source '{auth_src}'", 401
                )
            salt = bytes.fromhex(user_info.salt)
            hashed_pass = hashlib.pbkdf2_hmac(
                'sha256', password.encode(), salt, HASH_ITER).hex()
            action = "user_logged_in"
            if hashed_pass != user_info.password:
                raise self.server.error("Invalid Password")
        jwt_secret_hex: Optional[str] = user_info.jwt_secret
        if jwt_secret_hex is None:
            private_key = Signer()
            jwk_id = base64url_encode(secrets.token_bytes()).decode()
            user_info.jwt_secret = private_key.hex_seed().decode()  # type: ignore
            user_info.jwk_id = jwk_id
            self.users[username] = user_info
            await self._sync_user(username)
            self.public_jwks[jwk_id] = self._generate_public_jwk(private_key)
        else:
            private_key = self._load_private_key(jwt_secret_hex)
            if user_info.jwk_id is None:
                user_info.jwk_id = base64url_encode(secrets.token_bytes()).decode()
            jwk_id = user_info.jwk_id
        token = self._generate_jwt(username, jwk_id, private_key)
        refresh_token = self._generate_jwt(
            username, jwk_id, private_key, token_type="refresh",
            exp_time=datetime.timedelta(days=self.login_timeout))
        conn = web_request.get_client_connection()
        if create:
            event_loop = self.server.get_event_loop()
            event_loop.delay_callback(
                .005, self.server.send_event,
                "authorization:user_created",
                {'username': username})
        elif conn is not None:
            conn.user_info = user_info
        return {
            'username': username,
            'token': token,
            'source': user_info.source,
            'refresh_token': refresh_token,
            'action': action
        }

    async def _delete_jwt_user(self, web_request: WebRequest) -> Dict[str, str]:
        username: str = web_request.get_str('username')
        current_user = web_request.get_current_user()
        if current_user is not None:
            curname = current_user.username
            if curname == username:
                raise self.server.error(f"Cannot delete logged in user {curname}")
        if username in RESERVED_USERS:
            raise self.server.error(
                f"Invalid Request for reserved user {username}")
        user_info: Optional[UserInfo] = self.users.get(username)
        if user_info is None:
            raise self.server.error(f"No registered user: {username}")
        if user_info.jwk_id is not None:
            self.public_jwks.pop(user_info.jwk_id, None)
        del self.users[username]
        async with self.user_table as tx:
            await tx.execute(
                f"DELETE FROM {USER_TABLE} WHERE username = ?", (username,)
            )
        event_loop = self.server.get_event_loop()
        event_loop.delay_callback(
            .005, self.server.send_event,
            "authorization:user_deleted",
            {'username': username})
        return {
            "username": username,
            "action": "user_deleted"
        }

    def _generate_jwt(self,
                      username: str,
                      jwk_id: str,
                      private_key: Signer,
                      token_type: str = "access",
                      exp_time: datetime.timedelta = JWT_EXP_TIME
                      ) -> str:
        curtime = int(time.time())
        payload = {
            'iss': self.issuer,
            'aud': "Moonraker",
            'iat': curtime,
            'exp': curtime + int(exp_time.total_seconds()),
            'username': username,
            'token_type': token_type
        }
        header = {'kid': jwk_id}
        header.update(JWT_HEADER)
        jwt_header = base64url_encode(jsonw.dumps(header))
        jwt_payload = base64url_encode(jsonw.dumps(payload))
        jwt_msg = b".".join([jwt_header, jwt_payload])
        sig = private_key.signature(jwt_msg)
        jwt_sig = base64url_encode(sig)
        return b".".join([jwt_msg, jwt_sig]).decode()

    def decode_jwt(
        self, token: str, token_type: str = "access", check_exp: bool = True
    ) -> UserInfo:
        message, sig = token.rsplit('.', maxsplit=1)
        enc_header, enc_payload = message.split('.')
        header: Dict[str, Any] = jsonw.loads(base64url_decode(enc_header))
        sig_bytes = base64url_decode(sig)

        # verify header
        if header.get('typ') != "JWT" or header.get('alg') != "EdDSA":
            raise self.server.error("Invalid JWT header")
        jwk_id: Optional[str] = header.get('kid')
        if jwk_id not in self.public_jwks:
            raise self.server.error("Invalid key ID")

        # validate signature
        public_key = self._public_key_from_jwk(self.public_jwks[jwk_id])
        public_key.verify(sig_bytes + message.encode())

        # validate claims
        payload: Dict[str, Any] = jsonw.loads(base64url_decode(enc_payload))
        if payload['token_type'] != token_type:
            raise self.server.error(
                f"JWT Token type mismatch: Expected {token_type}, "
                f"Recd: {payload['token_type']}", 401)
        if payload['iss'] != self.issuer:
            raise self.server.error("Invalid JWT Issuer", 401)
        if payload['aud'] != "Moonraker":
            raise self.server.error("Invalid JWT Audience", 401)
        if check_exp and payload['exp'] < int(time.time()):
            raise self.server.error("JWT Expired", 401)

        # get user
        user_info: Optional[UserInfo] = self.users.get(
            payload.get('username', ""), None)
        if user_info is None:
            raise self.server.error("Unknown user", 401)
        return user_info

    def validate_jwt(self, token: str) -> UserInfo:
        try:
            user_info = self.decode_jwt(token)
        except Exception as e:
            if isinstance(e, self.server.error):
                raise
            raise self.server.error(
                f"Failed to decode JWT: {e}", 401
            ) from e
        return user_info

    def validate_api_key(self, api_key: str) -> UserInfo:
        if not self.enable_api_key:
            raise self.server.error("API Key authentication is disabled", 401)
        if api_key and api_key == self.api_key:
            return self.users[API_USER]
        raise self.server.error("Invalid API Key", 401)

    def _load_private_key(self, secret: str) -> Signer:
        try:
            key = Signer(bytes.fromhex(secret))
        except Exception:
            raise self.server.error(
                "Error decoding private key, user data may"
                " be corrupt", 500) from None
        return key

    def _generate_public_jwk(self, private_key: Signer) -> Dict[str, Any]:
        public_key = private_key.vk
        return {
            'x': base64url_encode(public_key).decode(),
            'kty': "OKP",
            'crv': "Ed25519",
            'use': "sig"
        }

    def _public_key_from_jwk(self, jwk: Dict[str, Any]) -> Verifier:
        if jwk.get('kty') != "OKP":
            raise self.server.error("Not an Octet Key Pair")
        if jwk.get('crv') != "Ed25519":
            raise self.server.error("Invalid Curve")
        if 'x' not in jwk:
            raise self.server.error("No 'x' argument in jwk")
        key = base64url_decode(jwk['x'])
        return Verifier(key.hex().encode())

    def _prune_conn_handler(self, eventtime: float) -> float:
        cur_time = time.time()
        for ip, user_info in list(self.trusted_users.items()):
            exp_time: float = user_info['expires_at']
            if cur_time >= exp_time:
                self.trusted_users.pop(ip, None)
                logging.info(f"Trusted Connection Expired, IP: {ip}")
        for ip, fqdn_info in list(self.fqdn_cache.items()):
            exp_time = fqdn_info["expires_at"]
            if cur_time >= exp_time:
                domain: str = fqdn_info["domain"]
                self.fqdn_cache.pop(ip, None)
                logging.info(f"Cached FQDN Expired, IP: {ip}, domain: {domain}")
        return eventtime + PRUNE_CHECK_TIME

    def _oneshot_token_expire_handler(self, token):
        self.oneshot_tokens.pop(token, None)

    def get_oneshot_token(self, ip_addr: IPAddr, user: Optional[UserInfo]) -> str:
        token = base64.b32encode(os.urandom(20)).decode()
        event_loop = self.server.get_event_loop()
        hdl = event_loop.delay_callback(
            ONESHOT_TIMEOUT, self._oneshot_token_expire_handler, token)
        self.oneshot_tokens[token] = (ip_addr, user, hdl)
        return token

    def _check_json_web_token(
        self, request: HTTPServerRequest, required: bool = True
    ) -> Optional[UserInfo]:
        auth_token: Optional[str] = request.headers.get("Authorization")
        if auth_token is None:
            auth_token = request.headers.get("X-Access-Token")
            if auth_token is None:
                qtoken = request.query_arguments.get('access_token', None)
                if qtoken is not None:
                    auth_token = qtoken[-1].decode(errors="ignore")
        elif auth_token.startswith("Bearer "):
            auth_token = auth_token[7:]
        else:
            return None
        if auth_token:
            try:
                return self.decode_jwt(auth_token, check_exp=required)
            except Exception:
                logging.exception(f"JWT Decode Error {auth_token}")
                raise HTTPError(401, "JWT Decode Error")
        return None

    async def _check_authorized_ip(self, ip: IPAddr) -> bool:
        if ip in self.trusted_ips:
            return True
        for rng in self.trusted_ranges:
            if ip in rng:
                return True
        if self.trusted_domains:
            if ip in self.fqdn_cache:
                fqdn: str = self.fqdn_cache[ip]["domain"]
            else:
                eventloop = self.server.get_event_loop()
                try:
                    fut = eventloop.run_in_thread(socket.getfqdn, str(ip))
                    fqdn = await asyncio.wait_for(fut, 5.0)
                except asyncio.TimeoutError:
                    logging.info("Call to socket.getfqdn() timed out")
                    return False
                else:
                    fqdn = fqdn.lower()
                    self.fqdn_cache[ip] = {
                        "expires_at": time.time() + FQDN_CACHE_TIMEOUT,
                        "domain": fqdn
                    }
            return fqdn in self.trusted_domains
        return False

    async def _check_trusted_connection(
        self, ip: Optional[IPAddr]
    ) -> Optional[UserInfo]:
        if ip is not None:
            curtime = time.time()
            exp_time = curtime + TRUSTED_CONNECTION_TIMEOUT
            if ip in self.trusted_users:
                self.trusted_users[ip]["expires_at"] = exp_time
                return self.trusted_users[ip]["user"]
            elif await self._check_authorized_ip(ip):
                logging.info(
                    f"Trusted Connection Detected, IP: {ip}")
                self.trusted_users[ip] = {
                    "user": UserInfo(TRUSTED_USER, "", curtime),
                    "expires_at": exp_time
                }
                return self.trusted_users[ip]["user"]
        return None

    def _check_oneshot_token(
        self, token: str, cur_ip: Optional[IPAddr]
    ) -> Optional[UserInfo]:
        if token in self.oneshot_tokens:
            ip_addr, user, hdl = self.oneshot_tokens.pop(token)
            hdl.cancel()
            if cur_ip != ip_addr:
                logging.info(f"Oneshot Token IP Mismatch: expected{ip_addr}"
                             f", Recd: {cur_ip}")
                return None
            return user
        else:
            return None

    def check_logins_maxed(self, ip_addr: IPAddr) -> bool:
        if self.max_logins is None:
            return False
        return self.failed_logins.get(ip_addr, 0) >= self.max_logins

    async def authenticate_request(
        self, request: HTTPServerRequest, auth_required: bool = True
    ) -> Optional[UserInfo]:
        if request.method == "OPTIONS":
            return None

        # Check JSON Web Token
        jwt_user = self._check_json_web_token(request, auth_required)
        if jwt_user is not None:
            return jwt_user

        try:
            ip = ipaddress.ip_address(request.remote_ip)  # type: ignore
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
        if self.enable_api_key:
            key: Optional[str] = request.headers.get("X-Api-Key")
            if key and key == self.api_key:
                return self.users[API_USER]

        # If the force_logins option is enabled and at least one user is created
        # then trusted user authentication is disabled
        if self.force_logins and len(self.users) > 1:
            if not auth_required:
                return None
            raise HTTPError(401, "Unauthorized, Force Logins Enabled")

        # Check if IP is trusted.  If this endpoint doesn't require authentication
        # then it is acceptable to return None
        trusted_user = await self._check_trusted_connection(ip)
        if trusted_user is not None:
            return trusted_user
        if not auth_required:
            return None

        raise HTTPError(401, "Unauthorized")

    async def check_cors(self, origin: Optional[str]) -> bool:
        if origin is None or not self.cors_domains:
            return False
        for regex in self.cors_domains:
            match = re.match(regex, origin)
            if match is not None:
                if match.group() == origin:
                    logging.debug(f"CORS Pattern Matched, origin: {origin} "
                                  f" | pattern: {regex}")
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
                    if await self._check_authorized_ip(ipaddr):
                        logging.debug(f"Cors request matched trusted IP: {ip}")
                        return True
            logging.debug(f"No CORS match for origin: {origin}\n"
                          f"Patterns: {self.cors_domains}")
        return False

    def cors_enabled(self) -> bool:
        return self.cors_domains is not None

    def get_api_key(self) -> Optional[str]:
        if not self.enable_api_key:
            return None
        return self.api_key

    def close(self) -> None:
        self.prune_timer.stop()


def load_component(config: ConfigHelper) -> Authorization:
    return Authorization(config)
