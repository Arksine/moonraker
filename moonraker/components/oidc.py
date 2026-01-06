# OIDC (OpenID Connect) authentication for Moonraker
#
# Copyright (C) 2024 Pedro Lamas <pedrolamas@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import base64
import logging
import secrets
import time
import urllib.parse
import jwt
from ..common import OIDCLoginRecord, OIDCTokenRecord

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    Optional,
    cast
)

if TYPE_CHECKING:
    from ..server import Server
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient

# OIDC constants
OIDC_LOGIN_TIMEOUT = 600  # 10 minutes
OIDC_TOKEN_TIMEOUT = 300  # 5 minutes
JWKS_CACHE_TIMEOUT = 3600  # 1 hour

class OIDCProvider:
    def __init__(self, server: Server, **kwargs) -> None:
        self._server = server
        self.name: str = kwargs["name"]
        self.client_id: str = kwargs["client_id"]
        self.client_secret: str = kwargs["client_secret"]
        self.issuer_url: str = kwargs.get("issuer_url", "")
        self.authorization_url: str = kwargs["authorization_url"]
        self.token_url: str = kwargs["token_url"]
        self.userinfo_url: str = kwargs.get("userinfo_url", "")
        self.signing_algos: Optional[List[str]] = kwargs.get(
            "signing_algos", None
        )
        jwks_uri: str = kwargs.get("jwks_uri", "")
        if (jwks_uri):
            self.jwks_client = jwt.PyJWKClient(jwks_uri)
        # OIDC requires 'openid' scope
        scope = kwargs.get("scope", "openid")
        if "openid" not in scope:
            scope = f"openid {scope}".strip()
        self.scope: str = scope
        self.redirect_uri: str = kwargs.get("redirect_uri", "")

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}

    @classmethod
    def from_config(cls, config: ConfigHelper) -> OIDCProvider:
        server = config.get_server()
        name = config.get_name().split(maxsplit=1)[-1]

        try:
            # Required fields
            client_id = config.get('client_id')
            client_secret = config.get('client_secret')

            # OIDC Discovery support
            issuer_url = config.get('issuer_url', '')
            if issuer_url:
                # Use OIDC Discovery - endpoints will be discovered
                authorization_url = config.get('authorization_url', '')
                token_url = config.get('token_url', '')
            else:
                # Manual configuration
                authorization_url = config.get('authorization_url')
                token_url = config.get('token_url')

            # Validate required fields
            if not client_id or not client_secret:
                raise config.error(
                    "Missing required OIDC configuration fields: "
                    "client_id, client_secret"
                )

            if not issuer_url and (not authorization_url or not token_url):
                raise config.error(
                    "Must provide either 'issuer_url' for OIDC Discovery or "
                    "both 'authorization_url' and 'token_url' for manual "
                    "configuration"
                )

            # OIDC-specific fields
            scope = config.get('scope', 'openid')
            userinfo_url = config.get('userinfo_url', '')
            jwks_uri = config.get('jwks_uri', '')
            signing_algos = config.getlist('signing_algos', None)
            redirect_uri = config.get('redirect_uri', '')

            return cls(
                server,
                name=name,
                client_id=client_id,
                client_secret=client_secret,
                issuer_url=issuer_url,
                authorization_url=authorization_url,
                token_url=token_url,
                userinfo_url=userinfo_url,
                jwks_uri=jwks_uri,
                signing_algos=signing_algos,
                scope=scope,
                redirect_uri=redirect_uri
            )
        except server.error as err:
            raise config.error(str(err)) from err

class MoonrakerOIDC:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()

        # In-memory OIDC storage
        self.oidc_login_records: Dict[str, OIDCLoginRecord] = {}
        self.oidc_tokens: Dict[str, OIDCTokenRecord] = {}

        # Load OIDC providers
        self.oidc_providers: Dict[str, OIDCProvider] = {}
        oidc_sections = config.get_prefix_sections('oidc ')
        for section_name in oidc_sections:
            provider_name = section_name[5:].strip()
            if not provider_name:
                self.server.add_warning(
                    f"[{section_name}]: Invalid OIDC provider section name"
                )
                continue
            try:
                provider_config = config[section_name]
                provider = OIDCProvider.from_config(provider_config)
                # Perform OIDC Discovery if issuer_url is provided
                if provider.issuer_url:
                    self.server.get_event_loop().create_task(
                        self._discover_oidc_endpoints(provider)
                    )
                self.oidc_providers[provider.name] = provider
                logging.info(f"Loaded OIDC provider: {provider.name}")
            except Exception as e:
                self.server.add_warning(
                    f"[oidc {provider_name}]: "
                    f"Failed to load OIDC provider: {e}"
                )

    async def component_init(self) -> None:
        pass

    async def _discover_oidc_endpoints(self, provider: OIDCProvider) -> None:
        try:
            discovery_url = (
                f"{provider.issuer_url.rstrip('/')}/"
                f".well-known/openid-configuration"
            )
            http_client: HttpClient = self.server.lookup_component(
                'http_client'
            )
            response = await http_client.get(
                discovery_url,
                headers={'Accept': 'application/json'}
            )
            response.raise_for_status()
            discovery_data = cast(dict, response.json())

            # Update provider endpoints from discovery
            if not provider.authorization_url:
                provider.authorization_url = discovery_data.get(
                    'authorization_endpoint', ''
                )
            if not provider.token_url:
                provider.token_url = discovery_data.get(
                    'token_endpoint', ''
                )
            if not provider.userinfo_url:
                provider.userinfo_url = discovery_data.get(
                    'userinfo_endpoint', ''
                )
            if not provider.signing_algos:
                provider.signing_algos = discovery_data.get(
                    "id_token_signing_alg_values_supported", []
                )
            if not provider.jwks_client:
                jwks_uri = discovery_data.get('jwks_uri', '')
                if jwks_uri:
                    provider.jwks_client = jwt.PyJWKClient(jwks_uri)

            logging.info(
                f"OIDC Discovery completed for provider: {provider.name}"
            )
        except Exception as e:
            logging.error(f"OIDC Discovery failed for {provider.name}: {e}")

    def get_provider_names(self) -> list[str]:
        return list(self.oidc_providers.keys())

    def has_providers(self) -> bool:
        return bool(self.oidc_providers)

    async def initiate_oidc_login(
        self, provider_name: str, next_url: Optional[str] = None
    ) -> Dict[str, str]:
        # Validate provider
        if provider_name not in self.oidc_providers:
            raise self.server.error(
                f"Unknown OIDC provider: {provider_name}", 400
            )

        provider = self.oidc_providers[provider_name]

        # Generate login record
        login_id = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)  # OIDC nonce for ID token validation
        expires_at = time.time() + OIDC_LOGIN_TIMEOUT

        # Store login record in memory
        self.oidc_login_records[state] = OIDCLoginRecord(
            login_id=login_id,
            state=state,
            provider=provider_name,
            next_url=next_url,
            expires_at=expires_at
        )

        # Build authorization URL with redirect_uri override support
        if provider.redirect_uri:
            redirect_uri = provider.redirect_uri
        else:
            host_info = self.server.get_host_info()
            redirect_uri = (
                f"http://{host_info['hostname']}:{host_info['port']}"
                f"/access/oidc/callback"
            )

        auth_params = {
            'client_id': provider.client_id,
            'redirect_uri': redirect_uri,
            'state': state,
            'nonce': nonce,
            'response_type': 'code',
            'scope': provider.scope  # Already includes 'openid'
        }

        auth_url = (
            provider.authorization_url + '?' +
            urllib.parse.urlencode(auth_params)
        )

        return {
            'authorization_url': auth_url,
            'state': state,
            'login_id': login_id
        }

    async def handle_oidc_callback(
        self,
        code: Optional[str],
        state: Optional[str],
    ) -> tuple[str, str, str, Optional[str], Dict[str, Any]]:
        if not code or not state:
            raise self.server.error("Missing code or state parameter", 400)

        login_record = self.oidc_login_records.get(state)
        if not login_record:
            raise self.server.error("Invalid state parameter", 400)

        login_id = login_record.login_id
        provider_name = login_record.provider
        next_url = login_record.next_url

        # Check expiration
        if time.time() > login_record.expires_at:
            raise self.server.error("Login session expired", 400)

        # Exchange code for tokens
        provider = self.oidc_providers[provider_name]
        tokens = await self._exchange_oidc_code(provider, code)

        # Validate and extract ID token claims
        id_token = tokens.get('id_token', '')
        access_token = tokens.get("access_token", '')
        user_info_data = await self._validate_and_extract_id_token(
            provider, id_token, access_token
        )

        # If ID token doesn't have enough info, call userinfo endpoint
        if not user_info_data.get('email') and not user_info_data.get('sub'):
            userinfo = await self._get_oidc_user_info(
                provider, access_token
            )
            user_info_data.update(userinfo)

        # Create username from OIDC claims (prefer email, fallback to sub)
        username = user_info_data.get(
            'email',
            user_info_data.get(
                'preferred_username',
                user_info_data.get('sub', 'unknown')
            )
        )

        # Clean up login record
        self.oidc_login_records.pop(state, None)

        return login_id, username, provider_name, next_url, user_info_data

    def store_oidc_tokens(
        self,
        login_id: str,
        username: str,
        access_token: str,
        refresh_token: str,
        source: str
    ) -> None:
        # Store tokens temporarily in memory
        token_expires_at = time.time() + OIDC_TOKEN_TIMEOUT
        self.oidc_tokens[login_id] = OIDCTokenRecord(
            login_id=login_id,
            username=username,
            access_token=access_token,
            refresh_token=refresh_token,
            source=source,
            expires_at=token_expires_at
        )

    def get_stored_token_record(
        self, login_id: str
    ) -> Optional[OIDCTokenRecord]:
        token_record = self.oidc_tokens.pop(login_id, None)

        if token_record and time.time() <= token_record.expires_at:
            return token_record

        return None

    async def _exchange_oidc_code(
        self, provider: OIDCProvider, code: str
    ) -> Dict[str, str]:
        # Use configured redirect_uri or build default
        if provider.redirect_uri:
            redirect_uri = provider.redirect_uri
        else:
            host_info = self.server.get_host_info()
            redirect_uri = (
                f"http://{host_info['hostname']}:{host_info['port']}"
                f"/access/oidc/callback"
            )

        data = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': provider.client_id,
            'client_secret': provider.client_secret
        }

        http_client: HttpClient = self.server.lookup_component(
            'http_client'
        )
        response = await http_client.post(
            provider.token_url,
            body=urllib.parse.urlencode(data),
            headers={
                'Accept': 'application/json',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
        )
        response.raise_for_status()

        return cast(dict, response.json())

    async def _validate_and_extract_id_token(
        self, provider: OIDCProvider, id_token: str, access_token: str
    ) -> Dict[str, Any]:
        if not id_token:
            return {}

        try:
            signing_key = provider.jwks_client.get_signing_key_from_jwt(
                id_token
            ) if provider.jwks_client else ''
            data = jwt.decode_complete(
                id_token,
                key=signing_key,
                algorithms=provider.signing_algos,
                audience=provider.client_id
            )
            payload, header = data["payload"], data["header"]

            alg_obj = jwt.get_algorithm_by_name(header["alg"])

            # compute at_hash, then validate / assert
            digest = alg_obj.compute_hash_digest(
                access_token.encode('utf-8')
            )
            at_hash = base64.urlsafe_b64encode(
                digest[: (len(digest) // 2)]
            ).rstrip(b"=").decode('utf-8')

            assert at_hash == payload["at_hash"]

            logging.info(
                f"JWT validation successful for {provider.name}"
            )

            return payload

        except jwt.InvalidTokenError as e:
            logging.error(
                f"JWT validation failed for {provider.name}: {e}"
            )
            return {}
        except Exception as e:
            logging.error(
                f"JWT validation error for {provider.name}: {e}"
            )
            return {}

    async def _get_oidc_user_info(
        self, provider: OIDCProvider, access_token: str
    ) -> Dict[str, Any]:
        if not provider.userinfo_url:
            return {}

        http_client: HttpClient = self.server.lookup_component(
            'http_client'
        )
        response = await http_client.get(
            provider.userinfo_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
        )
        response.raise_for_status()

        return cast(dict, response.json())

    def cleanup_expired_records(self, current_time: float) -> None:
        try:
            # Clean up expired login records
            expired_states: List[str] = []
            for state, record in self.oidc_login_records.items():
                if record.expires_at < current_time:
                    expired_states.append(state)

            for state in expired_states:
                self.oidc_login_records.pop(state, None)

            if expired_states:
                logging.debug(
                    f"Cleaned up {len(expired_states)} expired OIDC login "
                    f"records"
                )

            # Clean up expired token records
            expired_tokens: List[str] = []
            for login_id, oidc_token in self.oidc_tokens.items():
                if oidc_token.expires_at < current_time:
                    expired_tokens.append(login_id)

            for login_id in expired_tokens:
                self.oidc_tokens.pop(login_id, None)

            if expired_tokens:
                logging.debug(
                    f"Cleaned up {len(expired_tokens)} expired OIDC token "
                    f"records"
                )
        except Exception:
            logging.exception("Error cleaning up OIDC records")


def load_component(config: ConfigHelper) -> MoonrakerOIDC:
    return MoonrakerOIDC(config)
