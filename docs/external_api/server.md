# Server Administration

These endpoints provide access to server status, data tracking, and
administrative requests.

## Query Server Info

```{.http .apirequest title="HTTP Request"}
GET /server/info
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.info",
    "id": 9546
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "klippy_connected": true,
    "klippy_state": "ready",
    "components": [
        "database",
        "file_manager",
        "klippy_apis",
        "machine",
        "data_store",
        "shell_command",
        "proc_stats",
        "history",
        "octoprint_compat",
        "update_manager",
        "power"
    ],
    "failed_components": [],
    "registered_directories": ["config", "gcodes", "config_examples", "docs"],
    "warnings": [
        "Invalid config option 'api_key_path' detected in section [authorization]. Remove the option to resolve this issue. In the future this will result in a startup error.",
        "Unparsed config section [fake_section] detected.  This may be the result of a component that failed to load.  In the future this will result in a startup error."
    ],
    "websocket_count": 2,
    "moonraker_version": "v0.7.1-105-ge4f103c",
    "api_version": [1, 4, 0],
    "api_version_string": "1.4.0"
}
```
////

/// api-response-spec
    open: True
| Field                    |   Type   | Description                                              |
| ------------------------ | :------: | -------------------------------------------------------- |
| `klippy_connected`       |   bool   | Moonraker's connection status to the Klippy Host.        |
| `klippy_state`           |  string  | Klippy's current state. Expand for available values.     |
| #klippy-state-desc       |          |                                                          |+
| `components`             | [string] | A list of Moonraker components that are currently        |
|                          |          | loaded.                                                  |^
| `failed_components`      | [string] | A list of Moonraker components that failed to load.      |
| `registered_directories` | [string] | A list "roots" registered with Moonraker's file manager. |
| `warnings`               | [string] | A list of warning messages describing errors encountered |
|                          |          | during initialization or regular operation.              |^
| `websocket_count`        |   int    | The number of currently active websocket connections.    |
| `moonraker_version`      |  string  | The version of the Moonraker Application.                |
| `api_version`            |  [int]   | The version of the API in tuple format.                  |
| `api_version_string`     |  string  | The version of the API in string format.                 |

| State          | Description                                                        |
| -------------- | ------------------------------------------------------------------ |
| `disconnected` | Moonraker is currently disconnect from Klippy.                     |
| `startup`      | Klippy is currently initializing.                                  |
| `ready`        | Klippy is active and ready to receive commands.                    |
| `error`        | Klippy experienced an error during startup.                        |
| `shutdown`     | Klippy has been emergency stopped.  This can occur at user request |
|                | or if a critical error is encountered while running.               |^
{ #klippy-state-desc }
///

## Get Server Configuration

```{.http .apirequest title="HTTP Request"}
GET /server/config
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.config",
    "id": 5616
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "config": {
        "server": {
            "host": "0.0.0.0",
            "port": 7125,
            "ssl_port": 7130,
            "enable_debug_logging": true,
            "enable_asyncio_debug": false,
            "klippy_uds_address": "/tmp/klippy_uds",
            "max_upload_size": 210,
            "ssl_certificate_path": null,
            "ssl_key_path": null
        },
        "dbus_manager": {},
        "database": {
            "database_path": "~/.moonraker_database",
            "enable_database_debug": false
        },
        "file_manager": {
            "enable_object_processing": true,
            "queue_gcode_uploads": true,
            "config_path": "~/printer_config",
            "log_path": "~/logs"
        },
        "klippy_apis": {},
        "machine": {
            "provider": "systemd_dbus"
        },
        "shell_command": {},
        "data_store": {
            "temperature_store_size": 1200,
            "gcode_store_size": 1000
        },
        "proc_stats": {},
        "job_state": {},
        "job_queue": {
            "load_on_startup": true,
            "automatic_transition": false,
            "job_transition_delay": 2,
            "job_transition_gcode": "\nM118 Transitioning to next job..."
        },
        "http_client": {},
        "announcements": {
            "dev_mode": false,
            "subscriptions": []
        },
        "authorization": {
            "login_timeout": 90,
            "force_logins": false,
            "cors_domains": [
                "*.home",
                "http://my.mainsail.xyz",
                "http://app.fluidd.xyz",
                "*://localhost:*"
            ],
            "trusted_clients": [
                "192.168.1.0/24"
            ]
        },
        "zeroconf": {},
        "octoprint_compat": {
            "enable_ufp": true,
            "flip_h": false,
            "flip_v": false,
            "rotate_90": false,
            "stream_url": "/webcam/?action=stream",
            "webcam_enabled": true
        },
        "history": {},
        "secrets": {
            "secrets_path": "~/moonraker_secrets.ini"
        },
        "mqtt": {
            "address": "eric-work.home",
            "port": 1883,
            "username": "{secrets.mqtt_credentials.username}",
            "password_file": null,
            "password": "{secrets.mqtt_credentials.password}",
            "mqtt_protocol": "v3.1.1",
            "instance_name": "pi-debugger",
            "default_qos": 0,
            "status_objects": {
                "webhooks": null,
                "toolhead": "position,print_time",
                "idle_timeout": "state",
                "gcode_macro M118": null
            },
            "api_qos": 0,
            "enable_moonraker_api": true
        },
        "template": {}
    },
    "orig": {
        "DEFAULT": {},
        "server": {
            "enable_debug_logging": "True",
            "max_upload_size": "210"
        },
        "file_manager": {
            "config_path": "~/printer_config",
            "log_path": "~/logs",
            "queue_gcode_uploads": "True",
            "enable_object_processing": "True"
        },
        "machine": {
            "provider": "systemd_dbus"
        },
        "announcements": {},
        "job_queue": {
            "job_transition_delay": "2.",
            "job_transition_gcode": "\nM118 Transitioning to next job...",
            "load_on_startup": "True"
        },
        "authorization": {
            "trusted_clients": "\n192.168.1.0/24",
            "cors_domains": "\n*.home\nhttp://my.mainsail.xyz\nhttp://app.fluidd.xyz\n*://localhost:*"
        },
        "zeroconf": {},
        "octoprint_compat": {},
        "history": {},
        "secrets": {
            "secrets_path": "~/moonraker_secrets.ini"
        },
        "mqtt": {
            "address": "eric-work.home",
            "port": "1883",
            "username": "{secrets.mqtt_credentials.username}",
            "password": "{secrets.mqtt_credentials.password}",
            "enable_moonraker_api": "True",
            "status_objects": "\nwebhooks\ntoolhead=position,print_time\nidle_timeout=state\ngcode_macro M118"
        }
    },
    "files": [
        {
            "filename": "moonraker.conf",
            "sections": [
                "server",
                "file_manager",
                "machine",
                "announcements",
                "job_queue",
                "authorization",
                "zeroconf",
                "octoprint_compat",
                "history",
                "secrets"
            ]
        },
        {
            "filename": "include/extras.conf",
            "sections": [
                "mqtt"
            ]
        }
    ]
}
```
////

/// api-response-spec
    open: True
| Field    |   Type   | Description                                                            |
| -------- | :------: | ---------------------------------------------------------------------- |
| `config` |  object  | An object containing the full Moonraker configuration.  Each field of  |
|          |          | this object is a section name.  The value for each section is an       |^
|          |          | object mapping option names to values.  Values are cast to their       |^
|          |          | internal type.  Default values not specified in the configuration      |^
|          |          | files are included.                                                    |^
| `orig`   |  object  | An object containing the original configuration as read from the       |
|          |          | configuration file(s).  Like `config`, each field is a section name    |^
|          |          | and each value is a mapping of options to values.  Only values present |^
|          |          | in the configuration files are reported, and all values are strings.   |^
| `files`  | [object] | An array of [File Objects](#file-object-spec) describing the config  |
|          |          | files parsed.                                                          |^


| Field      |   Type   | Description                                                       |
| ---------- | :------: | ----------------------------------------------------------------- |
| `filename` |  string  | The name of the configuration file.  This name is a path relative |
|            |          | to the main configuration file's parent folder.                   |^
| `sections` | [string] | The config sections parsed from this file.                        |
{ #file-object-spec } File Object

///

## Request Cached Temperature Data

```{.http .apirequest title="HTTP Request"}
GET /server/temperature_store?include_monitors=false
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.temperature_store",
    "params": {
        "include_monitors": false
    },
    "id": 2313
}
```

/// api-parameters
    open: True
| Name               | Type | Default | Description                                  |
| ------------------ | :--: | ------- | -------------------------------------------- |
| `include_monitors` | bool | `false` | When set to `true` the response will include |
|                    |      |         | sensors reported as `temperature monitors.`  |^
|                    |      |         | A temperature monitor is a specific type of  |^
|                    |      |         | sensor that may include `null` values in     |^
|                    |      |         | the `temperatures` field of the response.    |^
///

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "extruder": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1],
        "targets": [0, 0, 0, 0, 0],
        "powers": [0, 0, 0, 0, 0]
    },
    "temperature_fan my_fan": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1],
        "targets": [0, 0, 0, 0, 0],
        "speeds": [0, 0, 0, 0, 0]
    },
    "temperature_sensor my_sensor": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1]
    }
}
```
////

/// api-response-spec
    open: True
| Field      |  Type  | Description                                                                   |
| ---------- | :----: | ----------------------------------------------------------------------------- |
| *variable* | object | A primary object including zero or more [Sensor Objects](#sensor-obj-spec). |
|            |        | The `fields` in this object will be sensor names as reported by Klippy.       |^
|            |        | If Klippy has not been initialized or reports no sensors this object will     |^
|            |        | be empty.                                                                     |^

| Field          |   Type   | Description                                                        |
| -------------- | :------: | ------------------------------------------------------------------ |
| `temperatures` | [float?] | Contains the history of temperature measurements of this sensor.   |
|                |          | If the sensor is a `temperature monitor` values may be `null`.  A  |^
|                |          | `null` value indicates that the sensor recorded no measurement at  |^
|                |          | that time.                                                         |^
| `targets`      | [float]  | Contains the history of temperature targets for heaters.           |
| `speeds`       | [float]  | Contains a history of `speeds` for fans.  This value should be     |
|                |          | between 0 and 1 indicating the pwm duty cycle.                     |^
| `powers`       | [float]  | Contains a history fof `powers` for heaters.  This value should be |
|                |          | between 0 and 1 indicating the pwm duty cycle.                     |^
{ #sensor-obj-spec } Sensor Object

//// Note
Fields not reported by a sensor will be omitted in the `Sensor Object`.

Each array in the `Sensor Object` is a FIFO queue, where the measurement at index 0
is the oldest value.  The time period between each measurement is 1 second.  The
maximum length of the array is set in Moonraker's configuration, where the default is
1200 values.
////

///

## Request Cached GCode Responses

```{.http .apirequest title="HTTP Request"}
GET /server/gcode_store?count=100
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.gcode_store",
    "params": {
        "count": 100
    },
    "id": 7643
}
```

/// api-parameters
    open: True
| Name    | Type | Default      | Description                                         |
| ------- | :--: | ------------ | --------------------------------------------------- |
| `count` | int  | *Store Size* | The number of cached gcode responses to return. The |
|         |      |              | default is to return all cached items.              |^
///

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "gcode_store": [
        {
            "message": "FIRMWARE_RESTART",
            "time": 1615832299.1167388,
            "type": "command"
        },
        {
            "message": "// Klipper state: Ready",
            "time": 1615832309.9977088,
            "type": "response"
        },
        {
            "message": "M117 This is a test",
            "time": 1615834094.8662775,
            "type": "command"
        },
        {
            "message": "G4 P1000",
            "time": 1615834098.761729,
            "type": "command"
        },
        {
            "message": "STATUS",
            "time": 1615834104.2860553,
            "type": "command"
        },
        {
            "message": "// Klipper state: Ready",
            "time": 1615834104.3299904,
            "type": "response"
        }
    ]
}
```
////

/// api-response-spec
    open: True

| Field         |   Type   | Description                                                      |
| ------------- | :------: | ---------------------------------------------------------------- |
| `gcode_store` | [object] | An array of [GCode Tracking Objects](#gc-tracking-obj-spec).   |
|               |          | The array is a FIFO queue with the oldest item being at index 0. |^

| Field     |  Type  | Description                                                        |
| --------- | :----: | ------------------------------------------------------------------ |
| `message` | string | The GCode Message associated with ths object.                      |
| `time`    | float  | The time at which the message was received reported in Unix Time.  |
| `type`    | string | The message type.  Can be `command` or `response`.  Commands are   |
|           |        | only tracked when received through Moonraker's gcode API endpoint. |^
{ #gc-tracking-obj-spec } GCode Tracking Object

///

## Rollover Logs

Requests a manual rollover for log files registered with Moonraker's
log management facility.  Currently these are limited to `moonraker.log`
and `klippy.log`.

/// Warning
Moonraker must be able to manage Klipper's systemd service to
perform a manual rollover.  The rollover will fail under the following
conditions:

- Moonraker cannot detect Klipper's systemd unit
- Moonraker cannot detect the location of Klipper's files
- A print is in progress
///

```{.http .apirequest title="HTTP Request"}
POST /server/logs/rollover
Content-Type: application/json

{
    "application": "moonraker"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.logs.rollover",
    "params": {
        "application": "moonraker"
    },
    "id": 4656
}
```

/// api-parameters
    open: True
| Name          |  Type  | Default | Description                                             |
| ------------- | :----: | ------- | ------------------------------------------------------- |
| `application` | string | *all*   | The name of the application for which the log should be |
|               |        |         | rolled over.  Can be `moonraker` or `klipper`.  When no |^
|               |        |         | value is specified all logs are rolled over.            |^
///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "rolled_over": [
        "moonraker",
        "klipper"
    ],
    "failed": {}
}
```
///

/// api-response-spec
    open: True
| Field         |   Type   | Description                                              |
| ------------- | :------: | -------------------------------------------------------- |
| `rolled_over` | [string] | A list of application names successfully rolled over.    |
| `failed`      |  object  | An object where the fields consist of applications names |
|               |          | that failed the rollover procedure.  The value assigned  |^
|               |          | to each field is an error message.                       |^
///


## Restart Server
```{.http .apirequest title="HTTP Request"}
POST /server/restart
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.restart",
    "id": 4656
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Identify Connection
This method provides a way for applications with persistent connections
to identify themselves to Moonraker.  This information may be used by
Moonraker perform an action or present information based on if a specific
type of frontend is connected.  Currently this method is only available
to websocket and unix socket connections.  Once this endpoint returns
success it cannot be called again, repeated calls will result in an error.

```{.text .apirequest title="HTTP request"}
Not Available
```

```{.json .apirequest title="JSON-RPC request (Websocket/Unix Socket Only)"}
{
    "jsonrpc": "2.0",
    "method": "server.connection.identify",
    "params": {
        "client_name": "moontest",
        "version": "0.0.1",
        "type": "web",
        "url": "http://github.com/arksine/moontest",
        "access_token": "<base64 encoded token>",
        "api_key": "<system API key>"
    },
    "id": 4656
}
```

/// api-parameters
    open: True
| Name           |  Type  | Default      | Description                                        |
| -------------- | :----: | ------------ | -------------------------------------------------- |
| `client_name`  | string | **REQUIRED** | The name of the application identifying itself,    |
|                |        |              | ie: `Mainsail`, `Fluidd`, `KlipperScreen`, etc.    |^
| `version`      | string | **REQUIRED** | The version of the application identifying itself. |
| `type`         | string | **REQUIRED** | The type of the application.  Expand for available |
|                |        |              | values.                                            |^
|                |        |              | #valid-id-type-desc                                |+
| `url`          | string | **REQUIRED** | The project URL or homepage for the application.   |
| `access_token` | string | `null`       | An optional JSON Web Token used to authenticate    |
|                |        |              | the websocket connection.  Only needed when the    |^
|                |        |              | app uses JWT authentication and did not            |^
|                |        |              | authenticate through the original Websocket        |^
|                |        |              | Request.                                           |^
| `api_key`      | string | `null`       | An optional API Key used to authenticate the       |
|                |        |              | connection.  Only needed when the APP uses API     |^
|                |        |              | Key authentication and did not authenticate        |^
|                |        |              | through the original Websocket Request.            |^

| Name      | Description                                                     |
| --------- | --------------------------------------------------------------- |
| `web`     | A web application like `Mainsail` and `Fluidd`.                 |
| `mobile`  | A mobile application like `Mobileraker`.                        |
| `desktop` | A desktop application like `OrcaSlicer`.                        |
| `display` | An application intended to drive displays like `KlipperScreen`. |
| `bot`     | An interactive bot like `MoonCord`.                             |
| `agent`   | An external extension like `Obico`.                             |
| `other`   | Anything that doesn't fit in to the above categories.           |
{: #valid-id-type-desc }

//// Note
When identifying as an `agent`, only one instance should be connected
to Moonraker at a time.  If multiple agents of the same `client_name`
attempt to identify themselves this endpoint will return an error.
See the [extensions](./extensions.md) document for more information about
`agents`.
////

//// Tip
See the authorization API documentation for details on JWT and API Key authentication.
////
///


```{.json .apiresponse title="Example Response"}
{
    "connection_id": 1730367696
}
```

/// api-response-spec
    open: True
| Field           | Type | Description                              |
| --------------- | :--: | ---------------------------------------- |
| `connection_id` | int  | A unique identifier for this connection. |
///

## Get Websocket ID

!!! Warning
    This method is deprecated.  Please use the
    [identify endpoint](#identify-connection) to retrieve the
    Websocket's UID

```{.text .apirequest title="HTTP request"}
Not Available
```

```{json .apirequest title="JSON-RPC request (Websocket/Unix Socket Only)"}
{
    "jsonrpc": "2.0",
    "method": "server.websocket.id",
    "id": 4656
}
```

```{.json .apiresponse title="Example Response"}
{
    "websocket_id": 1730367696
}
```

/// api-response-spec
    open: True

| Field          | Type | Description                              |
| -------------- | :--: | ---------------------------------------- |
| `websocket_id` | int  | A unique identifier for this connection. |
///
