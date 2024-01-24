
# Authorization and Authentication

The Authorization endpoints provide access to the various methods
used to authorize connections to Moonraker.  This includes user
authentication, API Key authentication, Temporary access via
"oneshot tokens", and IP and/or domain based authentication
("ie: trusted clients).

Untrusted clients must use either a JSON Web Token or an API key to access
Moonraker's HTTP APIs.  JWTs should be included in the `Authorization`
header as a `Bearer` type for each HTTP request.  If using an API Key it
should be included in the `X-Api-Key` header for each HTTP Request.

Websocket authentication can be achieved via the request itself or
post connection.  Unlike HTTP requests it is not necessary to pass a
token and/or API Key to each request.  The
[identify connection](./server.md#identify-connection) endpoint takes optional
`access_token` and `api_key` parameters that may be used to authenticate
a user already logged in, otherwise the `login` API may be used for
authentication.  Websocket connections will stay authenticated until
the connection is closed or the user logs out.

User authentication can be performed using a choice sources.  Moonraker
currently supports the following authentication sources:

| Source Name | Description                                                    |
| ----------- | -------------------------------------------------------------- |
| `moonraker` | Authentication is performed using credentials stored in        |
|             | Moonraker's database.                                          |^
| `ldap`      | Authentication is performed through a connected LDAP provider. |
|             | Requires a valid `[LDAP]` configuration.                       |^
{ #auth-source-desc } Authentication Source

/// note
ECMAScript imposes limitations on certain requests that prohibit the
developer from modifying the HTTP headers (ie: Requests to open a
websocket, "download" requests that open a user dialog).  In these cases
it is recommended for the developer to request a `oneshot_token`, then
send the result via the `token` query string argument in the desired
request.
///

/// warning
It is strongly recommended that arguments for the below APIs are
passed in the request's body.
///

## Login User
```{.http .apirequest title="HTTP Request"}
POST /access/login
Content-Type: application/json

{
    "username": "my_user",
    "password": "my_password",
    "source": "moonraker"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.login",
    "params": {
        "username": "my_user",
        "password": "my_password",
        "source": "moonraker"
    },
    "id": 1323
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default              | Description                                         |
| ---------- | :----: | -------------------- | --------------------------------------------------- |
| `username` | string | **REQUIRED**         | The user login name.                                |
| `password` | string | **REQUIRED**         | The user password.                                  |
| `source`   | string | Set by configuration | A valid [authentication source](#auth-source-desc). |

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNjU1LCAiZXhwIjogMTYxODg4MDQwMC40ODE2NTUsICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.QdieeEskrU0FrH7rXKuPDSZxscM54kV_vH60uJqdU9g",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNzUxNCwgImV4cCI6IDE2MjY2NTI4MDAuNDgxNzUxNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.btJF0LJfymInhGJQ2xvPwkp2dFUqwgcw4OA_wE-EcCM",
    "action": "user_logged_in",
    "source": "moonraker"
}
```
///

/// api-response-spec
    open: True

| Field           |  Type  | Description                                                          |
| --------------- | :----: | -------------------------------------------------------------------- |
| `username`      | string | The name of the logged in user.                                      |
| `token`         | string | A JSON Web Token (JWT) used to authenticate requests, also commonly  |
|                 |        | referred to as an `access token`.  HTTP requests should include this |^
|                 |        | token in the `Authorization` header as a `Bearer` type.  This token  |^
|                 |        | expires after 1 hour.                                                |^
| `refresh_token` | string | A JWT that should be used to generate new access tokens after they   |
|                 |        | expire.  See the [refresh token section](#refresh-json-web-token)    |^
|                 |        | for details.                                                         |^
| `action`        | string | The action taken by the auth manager.  Will always be                |
|                 |        | "user_logged_in".                                                    |^
| `source`        | string | The [authentication source](#auth-source-desc) used.                 |

///

/// note
This endpoint may be accessed without prior authentication.  A 401 will
only be returned if the authentication fails.
///

## Logout Current User

```{.http .apirequest title="HTTP Request"}
POST /access/logout
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.logout",
    "id": 1323
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "action": "user_logged_out"
}
```
///

/// api-response-spec
    open: True

| Field      |  Type  | Description                                           |
| ---------- | :----: | ----------------------------------------------------- |
| `username` | string | The name of the logged out user.                      |
| `action`   | string | The action taken by the auth manager.  Will always be |
|            |        | "user_logged_out".                                    |^

///

## Get Current User

```{.http .apirequest title="HTTP Request"}
GET /access/user
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.get_user",
    "id": 1323
}
```

Returns: An object containing the currently logged in user name, the source and
the date on which the user was created (in unix time).
/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "source": "moonraker",
    "created_on": 1618876783.8896716
}
```
///

/// api-response-spec
    open: True

| Field        |  Type  | Description                                          |
| ------------ | :----: | ---------------------------------------------------- |
| `username`   | string | The name of the logged in user.                      |
| `source`     | string | The [source](#auth-source-desc) used to authenticate |
|              |        | the user.                                            |^
| `created_on` | float  | The date, in unix time, the user entry was created.  |

///

## Create User

Creates a new local user and logs them in.

```{.http .apirequest title="HTTP Request"}
POST /access/user
Content-Type: application/json

{
    "username": "my_user",
    "password": "my_password"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.post_user",
    "params": {
        "username": "my_user",
        "password": "my_password"
    },
    "id": 1323
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default      | Description          |
| ---------- | :----: | ------------ | -------------------- |
| `username` | string | **REQUIRED** | The user login name. |
| `password` | string | **REQUIRED** | The user password.   |

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNjE5LCAiZXhwIjogMTYxODg4MDM4My44OTE2MTksICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.oH0IShTL7mdlVs4kcx3BIs_-1j0Oe-qXezJKjo-9Xgo",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNzAyNCwgImV4cCI6IDE2MjY2NTI3ODMuODkxNzAyNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.a6ZeRjk8RQQJDDH0JV-qGY_d_HIgfI3XpsqUlUaFT7c",
    "source": "moonraker",
    "action": "user_created"
}
```
///

/// api-response-spec
    open: True

| Field           |  Type  | Description                                                           |
| --------------- | :----: | --------------------------------------------------------------------- |
| `username`      | string | The name of the created user.                                         |
| `token`         | string | A JSON Web Token (JWT) used to authenticate requests, also commonly   |
|                 |        | referred to as an `access token`.  HTTP requests should include this  |^
|                 |        | token in the `Authorization` header as a `Bearer` type.  This token   |^
|                 |        | expires after 1 hour.                                                 |^
| `refresh_token` | string | A JWT that should be used to generate new access tokens after they    |
|                 |        | expire.  See the [refresh token section](#refresh-json-web-token)     |^
|                 |        | for details.                                                          |^
| `action`        | string | The action taken by the auth manager.  Will always be "user_created". |
| `source`        | string | The [authentication source](#auth-source-desc) used.                  |

///

/// note
Unlike `/access/login`, `/access/user` is a protected endpoint.  To
create a new user a client must either be trusted, use the API Key,
or be logged in as another user.
///

## Delete User

/// note
A request to delete a user MUST come from an authorized login
other than the account to be deleted.  This can be a "trusted user",
the "api key user", or any other user account.
///

```{.http .apirequest title="HTTP Request"}
DELETE /access/user
Content-Type: application/json

{
    "username": "my_username"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.delete_user",
    "params": {
        "username": "my_username"
    },
    "id": 1323
}
```

/// api-parameters
    open: True

| Name       | Type | Default      | Description                          |
| ---------- | :--: | ------------ | ------------------------------------ |
| `username` | str  | **REQUIRED** | The username of the entry to delete. |

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "action": "user_deleted"
}
```
///

/// api-response-spec
    open: True

| Field      |  Type  | Description                                       |
| ---------- | :----: | ------------------------------------------------- |
| `username` | string | The username of the deleted entry.                |
| `action`   | string | The action taken by the auth manager. Will always |
|            |        | be "user_deleted".                                |^

///

## List Available Users

```{.http .apirequest title="HTTP Request"}
GET /access/users/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.users.list",
    "id": 1323
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "users": [
        {
            "username": "testuser",
            "source": "moonraker",
            "created_on": 1618771331.1685035
        },
        {
            "username": "testuser2",
            "source": "ldap",
            "created_on": 1620943153.0191233
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field   |   Type   | Description                      |
| ------- | :------: | -------------------------------- |
| `users` | [object] | An array of `User Info` objects. |
|         |          | #user-info-spec                  |+

| Field        |  Type  | Description                                          |
| ------------ | :----: | ---------------------------------------------------- |
| `username`   | string | The username of the entry.                           |
| `source`     | string | The [source](#auth-source-desc) that must be used to |
|              |        | authenticate the user.                               |^
| `created_on` | float  | The date, in unix time, the user entry was created.  |
{ #user-info-spec } User Info

///

## Reset User Password

```{.http .apirequest title="HTTP Request"}
POST /access/user/password
Content-Type: application/json

{
    "password": "my_current_password",
    "new_password": "my_new_pass"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.user.password",
    "params": {
        "password": "my_current_password",
        "new_password": "my_new_pass"
    },
    "id": 1323
}
```

/// api-parameters
    open: True

| Name           |  Type  | Default      | Description                  |
| -------------- | :----: | ------------ | ---------------------------- |
| `password`     | string | **REQUIRED** | The user's current password. |
| `new_password` | string | **REQUIRED** | The user's new password.     |

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "action": "user_password_reset"
}
```
///

/// api-response-spec
    open: True

| Field      |  Type  | Description                                         |
| ---------- | :----: | --------------------------------------------------- |
| `username` | string | The username of the entry whose password was reset. |
| `action`   | string | Action taken by the Auth manager.  Will always be   |
|            |        | "user_password_reset".                              |^

///

## Refresh JSON Web Token
This endpoint can be used to refresh an expired access token.  If this
request returns an error then the refresh token is no longer valid and
the user must login with their credentials.

```{.http .apirequest title="HTTP Request"}
POST /access/refresh_jwt
Content-Type: application/json

{
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4Nzc0ODUuNzcyMjg5OCwgImV4cCI6IDE2MjY2NTM0ODUuNzcyMjg5OCwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAicmVmcmVzaCJ9.Y5YxGuYSzwJN2WlunxlR7XNa2Y3GWK-2kt-MzHvLbP8"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.refresh_jwt",
    "params": {
        "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4Nzc0ODUuNzcyMjg5OCwgImV4cCI6IDE2MjY2NTM0ODUuNzcyMjg5OCwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAicmVmcmVzaCJ9.Y5YxGuYSzwJN2WlunxlR7XNa2Y3GWK-2kt-MzHvLbP8"
    },
    "id": 1323
}
```

/// api-parameters
    open: True

| Name            |  Type  | Default      | Description                           |
| --------------- | :----: | ------------ | ------------------------------------- |
| `refresh_token` | string | **REQUIRED** | A valid `refresh_token` for the user. |

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzgyNDMuNTE2Nzc5MiwgImV4cCI6IDE2MTg4ODE4NDMuNTE2Nzc5MiwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.Ia_X_pf20RR4RAEXcxalZIOzOBOs2OwearWHfRnTSGU",
    "source": "moonraker",
    "action": "user_jwt_refresh"
}
```
///

/// api-response-spec
    open: True

| Field      |  Type  | Description                                                          |
| ---------- | :----: | -------------------------------------------------------------------- |
| `username` | string | The username of the entry whose access token ws refreshed.           |
| `token`    | string | A JSON Web Token (JWT) used to authenticate requests, also commonly  |
|            |        | referred to as an `access token`.  HTTP requests should include this |^
|            |        | token in the `Authorization` header as a `Bearer` type.  This token  |^
|            |        | expires after 1 hour.                                                |^
| `source`   | string | The [authentication source](#auth-source-desc) of the user entry.    |
| `action`   | string | The action taken by the Auth Manager.  Will always be                |
|            |        | "user_jwt_refresh".                                                  |^

///

/// note
This endpoint may be accessed by unauthorized clients.  A 401 will
only be returned if the refresh token is invalid.
///

## Generate a Oneshot Token

Javascript is not capable of modifying the headers for some HTTP requests
(for example, the `websocket`), which is a requirement to apply JWT or API Key
authorization.  To work around this clients may request a Oneshot Token and
pass it via the query string for these requests.  Tokens expire in 5 seconds
and may only be used once, making them relatively safe for inclusion in the
query string.

```{.http .apirequest title="HTTP Request"}
GET /access/oneshot_token
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.oneshot_token",
    "id": 1323
}
```

```{.json .apiresponse title="Example Response"}
"APDBEGHUTBUD6SOAYBPF3KE5BRMO7YSL"
```

/// api-response-spec
    open: True

The response is a string value containing the oneshot token. It may
added to a request's query string for access to any API endpoint.  The query
string should be added in the form of:

```
?token={base32_random_token}
```

///

## Get authorization module info

```{.http .apirequest title="HTTP Request"}
GET /access/info
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.info",
    "id": 1323
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "default_source": "moonraker",
    "available_sources": [
        "moonraker",
        "ldap"
    ],
    "login_required": false,
    "trusted": true
}
```
///

/// api-response-spec
    open: True

| Field               |   Type   | Description                                          |
| ------------------- | :------: | ---------------------------------------------------- |
| `default_source`    |  string  | The configured default                               |
|                     |          | [authentication source](#auth-source-desc).          |^
| `available_sources` | [string] | An array of available authentication sources.        |
| `login_required`    |   bool   | Set to `true` when `force_logins` is enabled via the |
|                     |          | configuration at least one user has been created.    |^
| `trusted`           |   bool   | Set to `true` when the connection making the info    |
|                     |          | request is a trusted connection.                     |^

///

/// note
This endpoint may be accessed by unauthorized clients.
///

## Get the Current API Key

```{.http .apirequest title="HTTP Request"}
GET /access/api_key
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.get_api_key",
    "id": 1323
}
```

```{.json .apiresponse title="Example Response"}
e514851f37b94c779d955212b6906f95
```

/// api-response-spec
    open: True

The response string value containing the current API key.

///

## Generate a New API Key
```{.http .apirequest title="HTTP Request"}
POST /access/api_key
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "access.post_api_key",
    "id": 1323
}
```

```{.json .apiresponse title="Example Response"}
e514851f37b94c779d955212b6906f95
```

/// api-response-spec
    open: True

The response string value containing the new API key.

///

/// note
After this request executes the API key change is applied immediately.
All subsequent HTTP requests from untrusted clients must use the new key.
Changing the API Key will not affect open websockets authenticated using
the previous API Key.
///
