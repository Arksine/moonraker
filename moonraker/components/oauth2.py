# OAuth2 Device Authorization Flow (RFC8628) for Moonraker
#
# Copyright (C) 2025 Pedro Lamas <pedrolamas@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

from __future__ import annotations
import base64
import logging
import time
import jwt
from urllib.parse import urlencode
from ..common import OAuth2Record

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Optional,
    Tuple,
    cast
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .http_client import HttpClient

# OAuth2 Device Flow constants
DEVICE_CODE_TIMEOUT = 600  # 10 minutes
TOKEN_TIMEOUT = 300  # 5 minutes
DEFAULT_POLL_INTERVAL = 5  # 5 seconds

class OAuth2Provider:
    def __init__(self, **kwargs) -> None:
        self.name: str = kwargs["name"]
        self.client_id: str = kwargs["client_id"]
        self.client_secret: str = kwargs.get("client_secret", "")
        self.device_authorization_endpoint: str = kwargs[
            "device_authorization_endpoint"
        ]
        self.token_endpoint: str = kwargs["token_endpoint"]
        self.userinfo_endpoint: str = kwargs.get("userinfo_endpoint", "")
        self.jwks_uri: str = kwargs.get("jwks_uri", "")
        self.scope: str = kwargs.get("scope", "openid profile")
        self.jwks_client: Optional[jwt.PyJWKClient] = None
        if self.jwks_uri:
            self.jwks_client = jwt.PyJWKClient(self.jwks_uri)

    def as_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k[0] != "_"}

    @classmethod
    def from_config(cls, config: ConfigHelper) -> OAuth2Provider:
        server = config.get_server()
        name = config.get_name().split(maxsplit=1)[-1]

        try:
            # Required fields
            client_id = config.get('client_id')
            device_authorization_endpoint = config.get(
                'device_authorization_endpoint'
            )
            token_endpoint = config.get('token_endpoint')

            # Optional fields
            client_secret = config.get('client_secret', '')
            userinfo_endpoint = config.get('userinfo_endpoint', '')
            jwks_uri = config.get('jwks_uri', '')
            scope = config.get('scope', 'openid profile')

            # Validate required fields
            if (not client_id or not device_authorization_endpoint or
                    not token_endpoint):
                raise config.error(
                    "Missing required OAuth2 device flow configuration "
                    "fields: client_id, device_authorization_endpoint, "
                    "token_endpoint"
                )

            return cls(
                name=name,
                client_id=client_id,
                client_secret=client_secret,
                device_authorization_endpoint=device_authorization_endpoint,
                token_endpoint=token_endpoint,
                userinfo_endpoint=userinfo_endpoint,
                jwks_uri=jwks_uri,
                scope=scope
            )
        except server.error as err:
            raise config.error(str(err)) from err

class MoonrakerOAuth2:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()

        # In-memory OAuth2 device flow storage
        self.device_records: Dict[str, OAuth2Record] = {}

        # Load OAuth2 device flow providers
        self.providers: Dict[str, OAuth2Provider] = {}
        prefix_sections = config.get_prefix_sections('oauth2 ')
        for section_name in prefix_sections:
            try:
                provider_config = config[section_name]
                provider = OAuth2Provider.from_config(provider_config)
                self.providers[provider.name] = provider
                logging.info(f"Loaded OAuth2 device provider: {provider.name}")
            except Exception as e:
                self.server.add_warning(
                    f"[{section_name}]: "
                    f"Failed to load OAuth2 device provider: {e}"
                )

    async def component_init(self) -> None:
        pass

    def get_provider_names(self) -> list[str]:
        return list(self.providers.keys())

    async def initiate_device_flow(
        self, provider_name: str
    ) -> OAuth2Record:
        if provider_name not in self.providers:
            raise self.server.error(
                f"Unknown OAuth2 provider: {provider_name}", 404
            )

        provider = self.providers[provider_name]
        http_client: HttpClient = self.server.lookup_component(
            'http_client'
        )

        # Prepare device authorization request
        data = {
            'client_id': provider.client_id,
            'scope': provider.scope
        }
        response = await http_client.post(
            provider.device_authorization_endpoint,
            body=urlencode(data),
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
        )

        response.raise_for_status()
        response_data = cast(Dict[str, Any], response.json())

        # Create device record
        device_record = OAuth2Record(
            device_code=response_data['device_code'],
            user_code=response_data['user_code'],
            verification_uri=response_data.get(
                'verification_uri',
                response_data.get('verification_url', '')
            ),
            verification_uri_complete=response_data.get(
                'verification_uri_complete',
                response_data.get('verification_url_complete', '')
            ),
            expires_in=response_data.get('expires_in', DEVICE_CODE_TIMEOUT),
            interval=response_data.get('interval', DEFAULT_POLL_INTERVAL),
            provider=provider_name
        )

        # Store device record
        self.device_records[device_record.device_code] = device_record

        # Schedule cleanup
        loop = self.server.get_event_loop()
        loop.delay_callback(
            device_record.expires_in,
            self._cleanup_device_record,
            device_record.device_code
        )

        logging.info(
            f"Initiated device flow for provider {provider_name}, "
            f"user code: {device_record.user_code}"
        )

        return device_record

    async def poll_token(self, device_code: str) -> Tuple[str, str, str]:
        if device_code not in self.device_records:
            raise self.server.error("Invalid or expired device code", 400)

        device_record = self.device_records[device_code]
        provider = self.providers[device_record.provider]

        # Check if device code expired
        if ((time.time() - device_record.created_time) >
                device_record.expires_in):
            self._cleanup_device_record(device_code)
            raise self.server.error("Device code expired", 400)

        http_client: HttpClient = self.server.lookup_component(
            'http_client'
        )

        # Prepare token request
        data = {
            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
            'device_code': device_code,
            'client_id': provider.client_id
        }

        if provider.client_secret:
            data['client_secret'] = provider.client_secret

        response = await http_client.post(
            provider.token_endpoint,
            body=urlencode(data),
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
        )

        response_data: Dict[str, Any] = {}
        if response.headers.get('Content-Type', '').startswith('application/json'):
            response_data = cast(Dict[str, Any], response.json())

        error: Optional[str] = response_data.get('error', None)
        if error:
            if error != 'authorization_pending' and error != 'slow_down':
                self._cleanup_device_record(device_code)
            return error, '', ''

        response.raise_for_status()

        # Clean up device record
        self._cleanup_device_record(device_code)

        # Extract tokens
        access_token = cast(str, response_data['access_token'])
        id_token = cast(str, response_data.get('id_token', ''))
        user_info_data = await self._validate_id_token(
            provider, id_token, access_token
        )

        if not user_info_data.get('email') and not user_info_data.get('sub'):
            userinfo = await self._get_user_info(
                provider, access_token
            )
            user_info_data.update(userinfo)

        username = user_info_data.get(
            'email',
            user_info_data.get(
                'preferred_username',
                user_info_data.get('sub', 'unknown')
            )
        )

        logging.info(
            f"Token obtained for provider {device_record.provider}"
        )

        return '', provider.name, username

    async def _get_user_info(
        self, provider: OAuth2Provider, access_token: str
    ) -> Dict[str, Any]:
        if not provider.userinfo_endpoint:
            return {}

        http_client: HttpClient = self.server.lookup_component(
            'http_client'
        )

        response = await http_client.get(
            provider.userinfo_endpoint,
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
        )

        response.raise_for_status()
        return cast(Dict[str, Any], response.json())

    async def _validate_id_token(
        self, provider: OAuth2Provider, id_token: str, access_token: str
    ) -> Dict[str, Any]:
        if not id_token:
            return {}

        try:
            # Get signing key and validate token
            signing_key = provider.jwks_client.get_signing_key_from_jwt(
                id_token
            ) if provider.jwks_client else ''
            data = jwt.decode_complete(
                id_token,
                key=signing_key,
                algorithms=['RS256', 'RS384', 'RS512', 'ES256'],
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

    def _cleanup_device_record(self, device_code: str) -> None:
        self.device_records.pop(device_code, None)

def load_component(config: ConfigHelper) -> MoonrakerOAuth2:
    return MoonrakerOAuth2(config)
