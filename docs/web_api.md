#

Most API methods are supported over the Websocket, HTTP, and MQTT
(if configured) transports. File Transfer and `/access` requests are only
available over HTTP. The Websocket is required to receive server generated
events such as gcode responses.  For information on how to set up the
Websocket, please see the Appendix at the end of this document.

### HTTP API Overview

Moonraker's HTTP API could best be described as "RESTish".  Attempts are
made to conform to REST standards, however the dynamic nature of
Moonraker's API registration along with the desire to keep consistency
between multiple API protocols results in an HTTP API that does not
completely adhere to the standard.

Moonraker is capable of parsing request arguments from the both the body
(either JSON or form-data depending on the `Content-Type` header) and from
the query string.  All arguments are grouped together in one data structure,
with body arguments taking precedence over query arguments.  Thus
if the same argument is supplied both in the body and in the
query string the body argument would be used. It is left up to the client
developer to decide exactly how they want to provide arguments, however
future API documentation will make recommendations.  As of March 1st 2021
this document exclusively illustrates arguments via the query string.

All successful HTTP requests will return a json encoded object in the form of:

```
{result: <response data>}
```

Response data is generally an object itself, however for some requests this
may simply be an "ok" string.

Should a request result in an error, a standard error code along with
an error specific message is returned.

#### Query string type hints

By default all arguments passed via the query string are represented as
strings.  Most endpoint handlers know the data type for each of their
arguments, thus they can perform conversion from a string type if necessary.
However some endpoints accept arguments of a "generic" type, thus the
client is responsible for specifying the type if "string" is not desirable.
This is not a problem for websocket requests as the JSON parser can extract
the appropriate type.  HTTP requests must provide "type hints" in these
scenarios.  Moonraker supplies support for the following query string type hints:
- int
- bool
- float
- json
The `json` type hint can be specified to pass an array or an object via
the query string.  Remember to percent encode the json string so that
the query string is correctly parsed.

Type hints may be specified by post-fixing them to a key, with a ":"
separating the key and the hint.  For example, lets assume that we
have a request that takes `seconds` (integer) and `enabled` (boolean)
arguments.  The query string with type hints might look like:
```
?seconds:int=120&enabled:bool=true
```
A query string that takes a `value` argument with which we want to
pass an object, `{foo: 21.5, bar: "hello"}` might look like:
```
?value:json=%7B%22foo%22%3A21.5%2C%22bar%22%3A%22hello%22%7D
```
As you can see, a percent encoded json string is not human readable,
thus using this functionality should be seen as a "last resort."  If at
all possible clients should attempt to put these arguments in the body
of a request.

### JSON-RPC API Overview

The Websocket and MQTT transports use the [JSON-RPC 2.0](https://jsonrpc.org)
protocol.  The Websocket transmits objects in a text frame,  whereas MQTT
transmits them in the payload of a topic.  When MQTT is configured Moonraker
subscribes to an api request topic. After an api request is processed Moonraker
publishes the return value to a response topic. By default these topics are
`{instance_name}/moonraker/api/request` and
`{instance_name}/moonraker/api/response`.  The `{instance_name}` should be a
unique identifier for each instance of Moonraker and defaults to the machine's
host name.

An encoded request should look something like:
```json
{
    "jsonrpc": "2.0",
    "method": "API method",
    "params": {"arg_one": 1, "arg_two": true},
    "id": 354
}
```

The `params` field may be left out if the API request takes no arguments.
The `id` should be a unique value that has no chance of colliding
with other JSON-RPC requests.  The `method` is the API method, as defined
for each API in this document.

!!! tip
    MQTT requests may provide an optional `mqtt_timestamp` keyword
    argument in the `params` field of the JSON-RPC request.  To avoid
    potential collisions from time drift it is recommended to specify
    the timestamp in microseconds since the Unix Epoch.  If provided
    Moonraker will use the timestamp to discard duplicate requests.
    It is recommended to either provide a timestamp or publish API
    requests at a QoS level of 0 or 2.

A successful request will return a response like the following:
```json
{
    "jsonrpc": "2.0",
    "result": {"res_data": "success"},
    "id": 354
}
```
The `result` will generally contain an object, but as with the HTTP API in some
cases it may simply return a string.  The `id` field will return an id that
matches the one provided by the request.

Requests that result in an error will receive a properly formatted
JSON-RPC response:
```json
{
    "jsonrpc": "2.0",
    "error": {"code": 36000, "message": "Error Message"},
    "id": 354
}
```
Some errors may not return a request ID, such as an improperly formatted request.

The [moontest](https://www.github.com/arksine/moontest) repo includes a basic
test interface with example usage for most of the requests below.  It also
includes a basic JSON-RPC implementation that uses promises to return responses
and errors (see json-rpc.js).

### Websocket Connections

#### Primary websocket

The primary websocket supports Moonraker's JSON-RPC API.  Most applications that
desire a websocket connection will make use of the primary websocket.

The primary websocket is available at:
```
 ws://host_or_ip:port/websocket`
```

The primary websocket will remain connected until the application disconnects
or Moonraker is shutdown.

#### Bridge websocket

The "bridge" websocket provides a near direct passthrough to Klipper's API
Server.  Klipper uses its own RPC protocol, which is effectively a simplified
version of the JSON-RPC specification. Developers should refer to
[Klipper's API documentation](https://www.klipper3d.org/API_Server.html)
for details on the protocol and available APIs.

!!! Note
    The bridge websocket is described as "near direct passthrough" because
    Moonraker handles the ETX (`0x03`) terminator internally.  Applications
    can expect to receive complete JSON encoded messages in a text frame
    without the ETX terminator.  Likewise applications should send JSON encoded
    messages without the ETX terminator.  Messages may be sent using either
    text frames or binary frames.

The bridge websocket provides access to diagnostic APIs that are not generally
suitable for Moonraker's primary connection.  These requests stream a
substantial amount of data; bridge connections allow Moonraker to avoid
decoding and re-encoding this data, reducing CPU load on the host. The "dump"
requests, such as `motion_report/dump_stepper` and `adxl345/dump_adxl345`, are
examples of APIs that should make use of the bridge websocket.

The bridge websocket is available at:
```
ws://host_or_ip:port/klippysocket
```

The availability of bridge connections depends on Klippy's availablility.
If Klippy is not running or its API server is not enabled then a bridge
websocket connection cannot be established.  Established bridge connections
will close when Klippy is shutdown or restarted.  Such connections will also
be closed if Moonraker is restarted or shutdown.

!!! Note
    If JWT or API Key authentication is required the application must use a
    [oneshot token](#generate-a-oneshot-token) when connecting to a bridge
    socket.  Since Moonraker does not decode bridge requests it is not possible
    to authenticate post connection.

### Unix Socket Connection

All JSON-RPC APIs available over the websocket are also made available over a
Unix Domain Socket.  Moonraker creates the socket file at
`<datapath>/comms/moonraker.sock` (ie: `~/printer_data/comms/moonraker.sock`).
The Unix Socket does not use the websocket transport protocol, instead
it expects UTF-8 encoded JSON-RPC strings. Each JSON-RPC request must be
terminated with an ETX character (`0x03`).

The Unix Socket is desirable for front ends and extensions running on the
local machine as authentication is not necessary.  There should be a small
performance improvement due to the simplified transport protocol, however
the impact of this is likely negligible.

The `moontest` repo contains a
[python script](https://github.com/Arksine/moontest/blob/master/scripts/unix_socket_test.py)
to test comms over the unix socket.

### Jinja2 Template API Calls

Some template options in Moonraker's configuration, such as those in the
[button](configuration.md#button) component, may call Moonraker APIs through
the `call_method(method_name, kwargs)` context function. The `call_method`
function takes the API's JSON-RPC method name as its first parameter, followed
by a set of keyword arguments as per the method's requirements.

```ini
# moonraker.conf

# Query Printer Objects example
[button check_status]
pin: gpio26
on_press:
  {% set query_objs = {"toolhead": ["position"], "print_stats": None} %}
  # JSON-RPC method is "printer.objects.query", which takes a single "objects"
  # argument
  {% set status = call_method("printer.objects.query", objects=query_objs) %}
  # do something with the value returned from the object query, perhaps
  # send a websocket notification or publish a mqtt topic

# Publish button event to MQTT Topic
[button check_status]
pin: gpio26
on_release:
  # JSON-RPC method is "server.mqtt.publish"
  {% do call_method("server.mqtt.publish",
                    topic="moonraker/mybutton",
                    payload="Button Released") %}
```

### Server Administration

#### Query Server Info
HTTP request:
```http
GET /server/info
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.info",
    "id": 9546
}
```
Returns:

An object containing various fields that report server state.

```json
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
    "api_version": [1, 0, 0],
    "api_version_string": "1.0.0"
  }
```
!!! warning
    This object also includes `plugins` and `failed_plugins` fields that
    are now deprecated.  They duplicate the information in
    `components` and `failed_components`, and will be removed in the future.

Note that `klippy_state` will match the `state` value received from
`/printer/info`. The `klippy_connected` item tracks the state of the
unix domain socket connect to Klippy. The `components` key will return a list
of enabled components.  This can be used by clients to check if an optional
component is available.  Optional components that do not load correctly will
not prevent the server from starting, thus any components that failed to load
will be reported in the `failed_components` field.

The `websocket_count` field reports the total number of connected websockets.

#### Get Server Configuration
HTTP request:
```http
GET /server/config
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.config",
    "id": 5616
}
```
Returns:

An object containing the server's full configuration.  Note that
this includes auxiliary configuration sections not part of `moonraker.conf`,
for example the `update_manager static debian moonraker` section.
Options not specified in `moonraker.conf` with default values are also
included.

```json
{
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
}
```
#### Request Cached Temperature Data
HTTP request:
```http
GET /server/temperature_store?include_monitors=false
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.temperature_store",
    "params": {
        "include_monitors": false
    },
    "id": 2313
}
```

Parameters:

- `include_monitors`: _Optional, defaults to `false`._  When set to `true`
  the response will include sensors reported as `temperature monitors` by
  Klipper.  A temperature monitor may report `null` values in the `temperatures`
  field, applications should be sure that they are modified to handle this
  condition before setting `inlcude_monitors` to `true`.

Returns:

An object where the keys are the available temperature sensor names, and with
the value being an array of stored temperatures.  The array is updated every
1 second by default, containing a total of 1200 values (20 minutes).  The
array is organized from oldest temperature to most recent (left to right).
Note that when the host starts each array is initialized to 0s.
```json
{
    "extruder": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1],
        "targets": [0, 0, 0, 0, 0],
        "powers": [0, 0, 0, 0, 0]
    },
    "temperature_fan my_fan": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1],
        "targets": [0, 0, 0, 0, 0],
        "speeds": [0, 0, 0, 0, 0],
    },
    "temperature_sensor my_sensor": {
        "temperatures": [21.05, 21.12, 21.1, 21.1, 21.1]
    }
}
```

#### Request Cached GCode Responses
HTTP request:
```http
GET /server/gcode_store?count=100
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.gcode_store",
    "params": {
        "count": 100
    },
    "id": 7643}
```

The `count` argument is optional, limiting number of returned items
in the response to the value specified. If omitted, the entire gcode
store will be returned (up to 1000 responses).

Returns:

An object with the field `gcode_store` that contains an array
of objects.  Each object will contain `message`, `time`, and
`type` fields.  The `time` field is reported in Unix Time.
The `type` field will either be `command` or `response`.
```json
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

#### Rollover Logs

Requests a manual rollover for log files registered with Moonraker's
log management facility.  Currently these are limited to `moonraker.log`
and `klippy.log`.

HTTP request:
```http
POST /server/logs/rollover
Content-Type: application/json

{
    "application": "moonraker"
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.logs.rollover",
    "params": {
        "application": "moonraker"
    },
    "id": 4656
}
```

Parameters:

- `application` - (Optional) The name of the application to rollover.
  Currently can be `moonraker` or `klipper`.  The default is to rollover
  all logs.

!!! Note
    Moonraker must be able to manage Klipper's systemd service to
    perform a manual rollover.  The rollover will fail under the following
    conditions:

    - Moonraker cannot detect Klipper's systemd unit
    - Moonraker cannot detect the location of Klipper's files
    - A print is in progress

Returns:  An object in the following format:

```json
{
    "rolled_over": [
        "moonraker",
        "klipper"
    ],
    "failed": {}
}
```

- `rolled_over` - An array of application names successfully rolled over.
- `failed` - An object containing information about failed applications.  The
  key will match an application name and its value will be an error message.

#### Restart Server
HTTP request:
```http
POST /server/restart
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.restart",
    "id": 4656
}
```
Returns:

`ok` upon receipt of the restart request.  After the request
is returns, the server will restart.  Any existing connection
will be disconnected.  A restart will result in the creation
of a new server instance where the configuration is reloaded.

#### Identify Connection
This method provides a way for persistent clients to identify
themselves to Moonraker.  This information may be used by Moonraker
perform an action or present information based on if a specific
client is connected.  Currently this method is only available
to websocket and unix socket connections.  Once this endpoint returns
success it cannot be called again, repeated calls will result in an error.

HTTP request: `Not Available`

JSON-RPC request (Websocket/Unix Socket Only):
```json
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

Parameters:

- `client_name`: (required) The name of your client, such as `Mainsail`,
  `Fluidd`, `KlipperScreen`, `MoonCord`, etc.
- `version`: (required) The current version of the connected client
- `type`: (required)  Application type. May be one of `web`, `mobile`,
  `desktop`, `display`, `bot`, `agent` or `other`.  These should be self
  explanatory, use `other` if your client does not fit any of the prescribed
  options.
- `url`: (required) The url for your client's homepage
- `access_token`: (optional) A JSON Web Token that may be used to assign a
  logged in user to the connection. See the [authorization](#authorization)
  section for APIs used to create and refresh the access token.
- `api_key`:. (optional) The system API Key.  This key may be used to grant
  access to clients that do not wish to implement user authentication.  Note
  that if the `access_token` is also supplied then this parameter will be
  ignored.

!!! Note
    When identifying as an `agent`, only one instance should be connected
    to Moonraker at a time.  If multiple agents of the same `client_name`
    attempt to identify themselves this endpoint will return an error.
    See the [extension APIs](#extension-apis) for more information about
    `agents`.

Returns:

The connection's unique identifier.
```json
{
    "connection_id": 1730367696
}
```

#### Get Websocket ID

!!! Warning
    This method is deprecated.  Please use the
    [identify endpoint](#identify-connection) to retrieve the
    Websocket's UID

HTTP request: `Not Available`

JSON-RPC request (Websocket/Unix Socket Only):
```json
{
    "jsonrpc": "2.0",
    "method": "server.websocket.id",
    "id": 4656
}
```
Returns:

The connected websocket's unique identifier.
```json
{
    "websocket_id": 1730367696
}
```

### Printer Administration

#### Get Klippy host information

HTTP Request:
```http
GET /printer/info
```
JSON-RPC Request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.info",
    "id": 5445
}
```
Returns:

An object containing the build version, cpu info, Klippy's current state.

```json
{
    "state": "ready",
    "state_message": "Printer is ready",
    "hostname": "my-pi-hostname",
    "software_version": "v0.9.1-302-g900c7396",
    "cpu_info": "4 core ARMv7 Processor rev 4 (v7l)",
    "klipper_path": "/home/pi/klipper",
    "python_path": "/home/pi/klippy-env/bin/python",
    "log_file": "/tmp/klippy.log",
    "config_file": "/home/pi/printer.cfg",
}
```

#### Emergency Stop
HTTP request:
```http
POST /printer/emergency_stop
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.emergency_stop",
    "id": 4564
}
```

!!! note
    This endpoint will immediately halt the printer and put it in a "shutdown"
    state.  It should be used to implement an "emergency stop" button and
    also used if a user enters `M112`(emergency stop) via a console.

Returns:

`ok`

#### Host Restart
HTTP request:
```http
POST /printer/restart
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.restart",
    "id": 4894
}
```
Returns:

`ok`

#### Firmware Restart
HTTP request:
```http
POST /printer/firmware_restart
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.firmware_restart",
    "id": 8463
}
```
Returns:

`ok`

### Printer Status

#### List available printer objects
HTTP request:
```http
GET /printer/objects/list
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.objects.list",
    "id": 1454
}
```

Returns:

An array of "printer objects" that are currently available for query
or subscription.  This list will be passed in an `objects` parameter.

```json
{
    "objects": ["gcode", "toolhead", "bed_mesh", "configfile",...]
}
```

#### Query printer object status
HTTP request:
```http
GET /printer/objects/query?gcode_move&toolhead&extruder=target,temperature
```
The above will request a status update for all `gcode_move` and `toolhead`
attributes.  Only the `temperature` and `target` attributes are requested
for the `extruder`.

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.objects.query",
    "params": {
        "objects": {
            "gcode_move": null,
            "toolhead": ["position", "status"]
        }
    },
    "id": 4654
}
```
!!! note
    A `null` value will fetch all available attributes for its key.

Returns:

An object where the top level items are "eventtime" and "status".  The
"status" item contains data about the requested update.

```json
{
    "eventtime": 578243.57824499,
    "status": {
        "gcode_move": {
            "absolute_coordinates": true,
            "absolute_extrude": true,
            "extrude_factor": 1,
            "gcode_position": [0, 0, 0, 0],
            "homing_origin": [0, 0, 0, 0],
            "position": [0, 0, 0, 0],
            "speed": 1500,
            "speed_factor": 1,
        },
        "toolhead": {
            "position": [0, 0, 0, 0],
            "status": "Ready"
        }
    }
}
```
See [Klipper's status reference](https://www.klipper3d.org/Status_Reference.html) for
details on the printer objects available for query.

#### Subscribe to printer object status
HTTP request:
```http
POST /printer/objects/subscribe?connection_id=123456789&gcode_move&extruder`
```
!!! note
    The HTTP API requires that a `connection_id` is passed via the query
    string or as part of the form.   This should be the
    [ID reported](#get-websocket-id) from a currently connected websocket. A
    request that includes only the `connection_id` argument will cancel the
    subscription on the specified websocket.

    This request is not available over MQTT as it can not be set per client.
    Instead MQTT can publish printer status by setting the `status_objects`
    option in the `[mqtt]` section.

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.objects.subscribe",
    "params": {
        "objects": {
            "gcode_move": null,
            "toolhead": ["position", "status"]
        }
    },
    "id": 5434
}
```
!!! note
    If `objects` is set to an empty object then the subscription will
    be cancelled.

Returns:

Status data for objects in the request, with the format matching that of
the `/printer/objects/query`:

```json
{
    "eventtime": 578243.57824499,
    "status": {
        "gcode_move": {
            "absolute_coordinates": true,
            "absolute_extrude": true,
            "extrude_factor": 1,
            "gcode_position": [0, 0, 0, 0],
            "homing_origin": [0, 0, 0, 0],
            "position": [0, 0, 0, 0],
            "speed": 1500,
            "speed_factor": 1,
        },
        "toolhead": {
            "position": [0, 0, 0, 0],
            "status": "Ready"
        }
    }
}
```

See [Klipper's status reference](https://www.klipper3d.org/Status_Reference.html) for
details on the printer objects available for subscription.

Status updates for subscribed objects are sent asynchronously over the
websocket.  See the [notify_status_update](#subscriptions)
notification for details.

#### Query Endstops
HTTP request:
```http
GET /printer/query_endstops/status
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.query_endstops.status",
    "id": 3456
}
```
Returns:

An object containing the current endstop state, where each field is an
endstop identifier, with a string value of "open" or "TRIGGERED".
```json
{
    "x": "TRIGGERED",
    "y": "open",
    "z": "open"
}
```

### GCode APIs

#### Run a gcode:
HTTP request:
```http
POST /printer/gcode/script?script=G28
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.gcode.script",
    "params": {
        "script": "G28"
    },
    "id": 7466}
```

!!! warning
    When `M112`(emergency stop) is requested via this endpoint it will not
    immediately stop the printer. `M112` will be placed on the gcode queue and
    executed after all previous gcodes are complete.  If a client detects
    `M112` via user input (such as a console) it should request the
    `/printer/emergency_stop` endpoint to immediately halt the printer.  This
    may be done in addition to sending the `M112` gcode if desired.

Returns:

`ok` when the gcode has completed execution.
#### Get GCode Help
HTTP request:
```http
GET /printer/gcode/help
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.gcode.help",
    "id": 4645
}
```

Returns:

An object where they keys are gcode handlers and values are the associated
help strings.  Note that help strings are not available for default gcode
handlers such as G1, G28, etc, nor are they available for extended handlers
that failed to register a description in Klippy.
```json
{
    "RESTORE_GCODE_STATE": "Restore a previously saved G-Code state",
    "PID_CALIBRATE": "Run PID calibration test",
    "QUERY_ADC": "Report the last value of an analog pin",
    "TUNING_TOWER": "Tool to adjust a parameter at each Z height",
    "SAVE_CONFIG": "Overwrite config file and restart",
    "SET_DISPLAY_GROUP": "Set the active display group",
    "SAVE_GCODE_STATE": "Save G-Code coordinate state",
    "SET_PRESSURE_ADVANCE": "Set pressure advance parameters",
    "SET_GCODE_OFFSET": "Set a virtual offset to g-code positions",
    "BED_TILT_CALIBRATE": "Bed tilt calibration script",
    ...
}
```

### Print Management

#### Print a file
HTTP request:
```http
POST /printer/print/start?filename=test_print.gcode
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.print.start",
    "params": {
        "filename": "test_pring.gcode"
    },
    "id": 4654
}
```
Returns:

`ok`

#### Pause a print
HTTP request:
```http
POST /printer/print/pause
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.print.pause",
    "id": 4564
}
```
Returns:

`ok`

#### Resume a print
HTTP request:
```http
POST /printer/print/resume
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.print.resume",
    "id": 1465
}
```
Returns:

`ok`

#### Cancel a print
HTTP request:
```http
POST /printer/print/cancel
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "printer.print.cancel",
    "id": 2578
}
```
Returns:

`ok`

### Machine Commands

#### Get System Info
HTTP request:
```http
GET /machine/system_info
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.system_info",
    "id": 4665
}
```
Returns: Information about the host system in the following format:
```json
{
    "system_info": {
        "cpu_info": {
            "cpu_count": 4,
            "bits": "32bit",
            "processor": "armv7l",
            "cpu_desc": "ARMv7 Processor rev 4 (v7l)",
            "serial_number": "b898bdb4",
            "hardware_desc": "BCM2835",
            "model": "Raspberry Pi 3 Model B Rev 1.2",
            "total_memory": 945364,
            "memory_units": "kB"
        },
        "sd_info": {
            "manufacturer_id": "03",
            "manufacturer": "Sandisk",
            "oem_id": "5344",
            "product_name": "SU32G",
            "product_revision": "8.0",
            "serial_number": "46ba46",
            "manufacturer_date": "4/2018",
            "capacity": "29.7 GiB",
            "total_bytes": 31914983424
        },
        "distribution": {
            "name": "Raspbian GNU/Linux 10 (buster)",
            "id": "raspbian",
            "version": "10",
            "version_parts": {
                "major": "10",
                "minor": "",
                "build_number": ""
            },
            "like": "debian",
            "codename": "buster"
        },
        "available_services": [
            "klipper",
            "klipper_mcu",
            "moonraker"
        ],
        "instance_ids": {
            "moonraker": "moonraker",
            "klipper": "klipper"
        },
        "service_state": {
            "klipper": {
                "active_state": "active",
                "sub_state": "running"
            },
            "klipper_mcu": {
                "active_state": "active",
                "sub_state": "running"
            },
            "moonraker": {
                "active_state": "active",
                "sub_state": "running"
            }
        },
        "virtualization": {
            "virt_type": "none",
            "virt_identifier": "none"
        },
        "python": {
            "version": [
                3,
                7,
                3,
                "final",
                0
            ],
            "version_string": "3.7.3 (default, Jan 22 2021, 20:04:44)  [GCC 8.3.0]"
        },
        "network": {
            "wlan0": {
                "mac_address": "<redacted_mac>",
                "ip_addresses": [
                    {
                        "family": "ipv4",
                        "address": "192.168.1.127",
                        "is_link_local": false
                    },
                    {
                        "family": "ipv6",
                        "address": "<redacted_ipv6>",
                        "is_link_local": false
                    },
                    {
                        "family": "ipv6",
                        "address": "fe80::<redacted>",
                        "is_link_local": true
                    }
                ]
            }
        },
        "canbus": {
            "can0": {
                "tx_queue_len": 128,
                "bitrate": 500000,
                "driver": "mcp251x"
            },
            "can1": {
                "tx_queue_len": 128,
                "bitrate": 500000,
                "driver": "gs_usb"
            }
        }
    }
}
```

!!! note
    If no SD Card is detected the `sd_info` field will contain an empty object.

#### Shutdown the Operating System
HTTP request:
```http
POST /machine/shutdown
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.shutdown",
    "id": 4665
}
```
Returns:

This request will not return.  The machine will shutdown
and the socket connection will drop.

#### Reboot the Operating System
HTTP request:
```http
POST /machine/reboot
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.reboot",
    "id": 4665
}
```
Returns:

This request will not return.  The machine will reboot
and the socket connection will drop.

#### Restart a system service
Uses: `sudo systemctl restart {name}`

Services allowed:

* `crowsnest`
* `MoonCord`
* `moonraker`
* `moonraker-telegram-bot`
* `klipper`
* `KlipperScreen`
* `sonar`
* `webcamd`

HTTP request:
```http
POST /machine/services/restart?service={name}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.services.restart",
    "params": {
        "service": "{name}"
    },
    "id": 4656}
```

Returns:

`ok` when complete.
!!! note
    If `moonraker` is chosen, the return
    value will be sent prior to the service restart.

#### Stop a system service
Stops a system service via `sudo systemctl stop <name>`. Currently
only `webcamd` and `klipper` are supported.

HTTP request:
```http
POST /machine/services/stop?service={name}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.services.stop",
    "params": {
        "service": "{name}"
    },
    "id": 4645
}
```

Returns:

`ok`

#### Start a system service
Starts a system service via `sudo systemctl start <name>`. Currently
only `webcamd` and `klipper` are supported.

HTTP request:
```http
POST /machine/services/start?service={name}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.services.start",
    "params": {
        "service": "{name}"
    },
    "id": 4645
}
```

Returns:

`ok`

#### Get Moonraker Process Stats
Returns system usage information about the moonraker process.

HTTP request:
```http
GET /machine/proc_stats
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.proc_stats",
    "id": 7896
}
```
Returns:

An object in the following format:
```json
{
    "moonraker_stats": [
        {
            "time": 1626612666.850755,
            "cpu_usage": 2.66,
            "memory": 24732,
            "mem_units": "kB"
        },
        {
            "time": 1626612667.8521338,
            "cpu_usage": 2.62,
            "memory": 24732,
            "mem_units": "kB"
        }
    ],
    "throttled_state": {
        "bits": 0,
        "flags": []
    },
    "cpu_temp": 45.622,
    "network": {
        "lo": {
            "rx_bytes": 113516429,
            "tx_bytes": 113516429,
            "bandwidth": 3342.68
        },
        "wlan0": {
            "rx_bytes": 48471767,
            "tx_bytes": 113430843,
            "bandwidth": 4455.91
        }
    },
    "system_cpu_usage": {
        "cpu": 2.53,
        "cpu0": 3.03,
        "cpu1": 5.1,
        "cpu2": 1.02,
        "cpu3": 1
    },
    "system_uptime": 2876970.38089603,
    "websocket_connections": 4
}
```
Process information is sampled every second.  The `moonraker_stats` field
will return up to 30 samples, each sample with the following fields:

- `time`: Time of the sample (in seconds since the Epoch)
- `cpu_usage`: A floating point value between 0-100, representing the
CPU usage of the Moonraker process.
- `memory`: Integer value representing the current amount of memory
allocated in RAM (resident set size).
- `mem_units`: A string identifying the units of the value in the
`memory` field.  This is typically "kB", but not guaranteed.

If the system running Moonraker supports `vcgencmd` then Moonraker
will check the current throttled flags via `vcgencmd get_throttled`
and report them in the `throttled_state` field:

- `bits`: An integer value that represents the bits reported by
`vcgencmd get_throttled`
- `flags`: Descriptive flags parsed out of the bits.  One or more
of the following flags may be reported:
- "Under-Voltage Detected"
- "Frequency Capped"
- "Currently Throttled"
- "Temperature Limit Active"
- "Previously Under-Volted"
- "Previously Frequency Capped"
- "Previously Throttled"
- "Previously Temperature Limited"

The first four flags indicate an active throttling condition,
whereas the last four indicate a previous condition (may or
may not still be active).  If `vcgencmd` is not available
`throttled_state` will report `null`.

If the system reports CPU temp at `/sys/class/thermal/thermal_zone0`
then temperature will be supplied in the `cpu_temp` field.  Otherwise
the field will be set to `null`.

If the system reports network statistics at `/proc/net/dev` then the
`network` field will contain network statistics.  All available interfaces
will be tracked.  Each interface reports the following fields:

- `rx_bytes`: total number of bytes received over the interface
- `tx_bytes`: total number of bytes transferred over the interface
- `bandwidth`: estimated current bandwidth used (both rx and tx) in
  bytes/second

If network information is not available then the `network` field will
contain an empty object.

If the system reports cpu usage at `/proc/stat` then the `system_cpu_usage`
field will contain an object with cpu usage data.  The `cpu` field of this
object reports total cpu usage, while each `cpuX` field is usage per core.

The `websocket_connections` field reports the number of active websockets
currently connected to moonraker.

#### Get Sudo Info
Retrieve sudo information status.  Optionally checks if Moonraker has
permission to run commands as root.

HTTP request:
```http
GET /machine/sudo/info?check_access=false
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.sudo.info",
    "params": {
        "check_access": false
    },
    "id": 7896
}
```

Parameters:

- `check_access`: A boolean value, when set to `true` Moonraker will
  attempt to run a command with elevated permissions.  The result will
  be returned in the `sudo_access` field of the response.  Defaults to
  `false`.

Returns:

An object in the following format:
```json
{
    "sudo_access": null,
    "linux_user": "pi",
    "sudo_requested": false,
    "request_messages": []
}
```

- `sudo_access`:  The result of a request to check access.  Returns
  `true` if Moonraker has sudo permission, `false` if it does not,
  and `null` if `check_access` is `false`.
- `linux_user`:  The current linux user running Moonraker.
- `sudo_requested`:  Returns true if Moonraker is currently requesting
  sudo access.
- `request_messages`:  An array of strings, each string describing
  a pending sudo request.  The array will be empty if no sudo
  requests are pending.

#### Set sudo password
Sets/updates the sudo password currently used by Moonraker.  When
the password is set using this endpoint the change is not persistent
across restarts.  If Moonraker has one or more pending sudo requests
they will be processed.

HTTP request:
```http
POST /machine/sudo/password
Content-Type: application/json

{
    "password": "linux_user_password"
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.sudo.password",
    "params": {
        "password": "linux_user_password"
    },
    "id": 7896
}
```

Parameters:

- `password`:  The linux user password used to grant elevated
  permission.  This parameter must be provided.

Returns:

An object in the following format:
```json
{
    "sudo_responses": [
        "Sudo password successfully set."
    ],
    "is_restarting": false
}
```

- `sudo_responses`: An array of one or more sudo responses.
  If there are pending sudo requests each request will provide
  a response.
- `is_restarting`: A boolean value indicating that a sudo request
  prompted Moonraker to restart its service.

This request will return an error if the supplied password is
incorrect or if any pending sudo requests fail.

### File Operations

Most file operations are available over both APIs, however file upload and
file download are currently only available via HTTP APIs.

Moonraker organizes local directories into "roots".  For example,
gcodes are located at `http:\\host\server\files\gcodes\*`, otherwise known
as the "gcodes" root.  The following roots are available:

- gcodes
- config
- config_examples (read-only)
- docs (read-only)

Write operations (upload, delete, make directory, remove directory) are
only available on the `gcodes` and `config` roots.  Note that the `config` root
is only available if the `config_path` option has been set in Moonraker's
configuration.

#### List available files
Walks through a directory and fetches all files.  All file names include a
path relative to the specified `root`.

HTTP request:
```http
GET /server/files/list?root={root_folder}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.list",
    "params": {
        "root": "{root_folder}"
    },
    "id": 4644
}
```
!!! tip
    If the `root` argument is omitted the request will default to
    the `gcodes` root.

!!! note
    The `gcodes` root will only return files with valid gcode
    extensions.

Returns:
A list of objects, where each object contains file data.
```json
[
    {
        "path": "3DBenchy_0.15mm_PLA_MK3S_2h6m.gcode",
        "modified": 1615077020.2025201,
        "size": 4926481,
        "permissions": "rw"
    },
    {
        "path": "Shape-Box_0.2mm_PLA_Ender2_20m.gcode",
        "modified": 1614910966.946807,
        "size": 324236,
        "permissions": "rw"
    },
    {
        "path": "test_dir/A-Wing.gcode",
        "modified": 1605202259,
        "size": 1687387,
        "permissions": "rw"
    },
    {
        "path": "test_dir/CE2_CubeTest.gcode",
        "modified": 1614644445.4025,
        "size": 1467339,
        "permissions": "rw"
    },
    {
        "path": "test_dir/V350_Engine_Block_-_2_-_Scaled.gcode",
        "modified": 1615768477.5133543,
        "size": 189713016,
        "permissions": "rw"
    },
]
```

#### List registered roots
Reports all "root" directories registered with Moonraker.  Information
such as location on disk and permissions are included.

HTTP request:
```http
GET /server/files/roots
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.roots",
    "id": 4644
}
```

Returns:
A list of objects, where each object contains file data:

```json
[
    {
        "name": "config",
        "path": "/home/pi/printer_data/config",
        "permissions": "rw"
    },
    {
        "name": "logs",
        "path": "/home/pi/printer_data/logs",
        "permissions": "r"
    },
    {
        "name": "gcodes",
        "path": "/home/pi/printer_data/gcodes",
        "permissions": "rw"
    },
    {
        "name": "config_examples",
        "path": "/home/pi/klipper/config",
        "permissions": "r"
    },
    {
        "name": "docs",
        "path": "/home/pi/klipper/docs",
        "permissions": "r"
    }
]
```

#### Get GCode Metadata

Get metadata for a specified gcode file.

HTTP request:
```http
GET /server/files/metadata?filename={filename}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.metadata",
    "params": {
        "filename": "{filename}"
    },
    "id": 3545
}
```

Parameters:

- `filename`: Path to the gcode file, relative to the `gcodes` root.
  For example, if the file is located at
  `http://host/server/files/gcodes/tools/drill_head.gcode`,
  the `filename` should be specified as `tools/drill_head.gcode`

Returns:

Metadata for the requested file if it exists.  If any fields failed
parsing they will be omitted.  The metadata will always include the file name,
modified time, and size.

```json
{
    "print_start_time": null,
    "job_id": null,
    "size": 4926481,
    "modified": 1615077020.2025201,
    "slicer": "SuperSlicer",
    "slicer_version": "2.2.52",
    "layer_height": 0.15,
    "first_layer_height": 0.2,
    "object_height": 48.05,
    "filament_total": 4056.4,
    "estimated_time": 7569,
    "thumbnails": [
        {
            "width": 32,
            "height": 32,
            "size": 2596,
            "relative_path": ".thumbs/3DBenchy_0.15mm_PLA_MK3S_2h6m-32x32.png"
        },
        {
            "width": 400,
            "height": 300,
            "size": 73308,
            "relative_path": ".thumbs/3DBenchy_0.15mm_PLA_MK3S_2h6m-400x300.png"
        }
    ],
    "first_layer_bed_temp": 60,
    "first_layer_extr_temp": 215,
    "gcode_start_byte": 79451,
    "gcode_end_byte": 4915668,
    "filename": "3DBenchy_0.15mm_PLA_MK3S_2h6m.gcode"
}
```
!!! Note
    The `print_start_time` and `job_id` fields are initialized to
    `null`.  They will be updated for each print job if the user has the
    `[history]` component configured

#### Scan GCode Metadata

Initiate a metadata scan for a selected file.  If the file has already
been scanned the endpoint will force a rescan

HTTP request:
```http
GET /server/files/metascan?filename={filename}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.metascan",
    "params": {
        "filename": "{filename}"
    },
    "id": 3545
}
```

Parameters:

- `filename`: Path to the gcode file, relative to the `gcodes` root.
  For example, if the file is located at
  `http://host/server/files/gcodes/tools/drill_head.gcode`,
  the `filename` should be specified as `tools/drill_head.gcode`

Returns:

- An object containing the metadata resulting from the scan, matching
  the return value of the [Get Metdata Endpoint](#get-gcode-metadata).

#### Get GCode Thumbnails

Returns thumbnail information for a supplied gcode file. If no thumbnail
information is available

HTTP request:
```http
GET /server/files/thumbnails?filename={filename}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.thumbnails",
    "params": {
        "filename": "{filename}"
    },
    "id": 3545
}
```

Parameters:

- `filename`: Path to the gcode file, relative to the `gcodes` root.
  For example, if the file is located at
  `http://host/server/files/gcodes/tools/drill_head.gcode`,
  the `filename` should be specified as `tools/drill_head.gcode`

Returns:

An array of objects containing thumbnail information.  If no
thumbnail information exists for the specified file then the
returned array wil be empty.

```json
[
    {
        "width": 32,
        "height": 32,
        "size": 1551,
        "thumbnail_path": "test/.thumbs/CE2_FanCover-120mm-Mesh-32x32.png"
    },
    {
        "width": 300,
        "height": 300,
        "size": 31819,
        "thumbnail_path": "test/.thumbs/CE2_FanCover-120mm-Mesh.png"
    }
]
```

!!! Note
    This information is the same as reported in the `thumbnails` field
    of a [metadata](#get-gcode-metadata) object, with one exception.
    The `thumbnail_path` field in the result above contains a
    path relative to the `gcodes` root, whereas the `relative_path`
    field reported in the metadata is relative to the gcode file's
    parent folder.

#### Get directory information
Returns a list of files and subdirectories given a supplied path.
Unlike `/server/files/list`, this command does not walk through
subdirectories.  This request will return all files in a directory,
including files in the `gcodes` root that do not have a valid gcode
extension.

HTTP request:
```http
GET /server/files/directory?path=gcodes/my_subdir&extended=true
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.get_directory",
    "params": {
        "path": "gcodes/my_subdir",
        "extended": true
    },
    "id": 5644
}
```
!!! tip
    If the `path` argument is omitted then the command will return
    directory information from the `gcodes` root.

The `extended` argument is optional and defaults to false. If
supplied and set to true then data returned for gcode files
will also include metadata (if available).

Returns:

An object containing file and subdirectory information in the
following format:

```json
{
    "dirs": [
        {
            "modified": 1615768162.0412788,
            "size": 4096,
            "permissions": "rw",
            "dirname": "test"
        },
        {
            "modified": 1613569827.489749,
            "size": 4096,
            "permissions": "rw",
            "dirname": "Cura"
        },
        {
            "modified": 1615767459.6265886,
            "size": 4096,
            "permissions": "rw",
            "dirname": "thumbs"
        }
    ],
    "files": [
        {
            "modified": 1615578004.9639666,
            "size": 7300692,
            "permissions": "rw",
            "filename": "Funnel_0.2mm_PLA_Ender2_2h4m.gcode"
        },
        {
            "modified": 1589156863.9726968,
            "size": 4214831,
            "permissions": "rw",
            "filename": "CE2_Pi3_A+_CaseLID.gcode"
        },
        {
            "modified": 1615030592.7722695,
            "size": 2388774,
            "permissions": "rw",
            "filename": "CE2_calicat.gcode"
        },
    ],
    "disk_usage": {
        "total": 7522213888,
        "used": 4280369152,
        "free": 2903625728
    },
    "root_info": {
        "name": "gcodes",
        "permissions": "rw"
    }
}
```

#### Create directory
Creates a directory at the specified path.

HTTP request:
```http
POST /server/files/directory?path=gcodes/my_new_dir
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.post_directory",
    "params": {
        "path": "gcodes/my_new_dir"
    },
    "id": 6548
}
```

Returns: Information about the created directory
```json
{
    "item": {
        "path": "my_new_dir",
        "root": "gcodes",
        "modified": 1676983427.3732708,
        "size": 4096,
        "permissions": "rw"

    },
    "action": "create_dir"
}
```

#### Delete directory
Deletes a directory at the specified path.

HTTP request:
```http
DELETE /server/files/directory?path=gcodes/my_subdir&force=false
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.delete_directory",
    "params": {
        "path": "gcodes/my_subdir",
        "force": false
    },
    "id": 6545
}
```
!!! warning
    If the specified directory contains files then the delete request
    will fail unless the `force` argument is set to `true`.

Returns:  Information about the deleted directory
```json
{
    "item": {
        "path": "my_subdir",
        "root": "gcodes",
        "modified": 0,
        "size": 0,
        "permissions": ""

    },
    "action": "delete_dir"
}
```

#### Move a file or directory
Moves a file or directory from one location to another. The following
conditions must be met for a move successful move:

- The source must exist
- The user (typically "pi") must have the appropriate file permissions
- Neither the source nor destination can be loaded by the `virtual_sdcard`.
  If the source is a directory, it must not contain a file loaded by the
  `virtual_sdcard`.

When specifying the `source` and `dest`, the `root` directory should be
prefixed. Currently the only supported roots for `dest` are `gcodes`"
and `config`".

This API may also be used to rename a file or directory.   Be aware that an
attempt to rename a directory to a directory that already exists will result
in *moving* the source directory into the destination directory.  Also be aware
that renaming a file to a file that already exists will result in overwriting
the existing file.

HTTP request:
```http
POST /server/files/move?source=gcodes/testdir/my_file.gcode&dest=gcodes/subdir/my_file.gcode
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.move",
    "params": {
        "source": "gcodes/testdir/my_file.gcode",
        "dest": "gcodes/subdir/my_file.gcode"
    },
    "id": 5664
}
```

Returns:  Information about the moved file or directory
```json
{
    "result": {
        "item": {
            "root": "gcodes",
            "path": "subdir/my_file.gcode",
            "modified": 1676940082.8595376,
            "size": 384096,
            "permissions": "rw"
        },
        "source_item": {
            "path": "testdir/my_file.gcode",
            "root": "gcodes"
        },
        "action": "move_file"
    }
}
```

!!! Note
    The `item` field contains file info for the destination.  The `source_item`
    contains the `path` and `root` the item was moved from.  The `action` field
    will be `move_file` if the source is a file or `move_dir` if the source is
    a directory.

#### Copy a file or directory
Copies a file or directory from one location to another.  A successful copy has
the prerequisites as a move with one exception, a copy may complete if the
source file or directory is loaded by the `virtual_sdcard`.  As with the move
API, the `source` and `dest` should have the root prefixed to the path.

HTTP request:
```http
POST /server/files/copy?source=gcodes/my_file.gcode&dest=gcodes/subdir/my_file.gcode
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.copy",
    "params": {
        "source": "gcodes/my_file.gcode",
        "dest": "gcodes/subdir/my_file.gcode"
    },
    "id": 5623
}
```

Returns: Information about the copied file or directory
```json
{
    "item": {
        "root": "gcodes",
        "path": "subdir/my_file.gcode",
        "modified": 1676940082.8595376,
        "size": 384096,
        "permissions": "rw"
    },
    "action": "create_file"
}
```

!!! Note
    The `item` field contains file info for the destination.  The `action` field
    will be `create_file` if a new file was created, `modify_file` if an exiting
    file was overwitten, or `create_dir` if an entire directory was copied.

#### Create a ZIP archive

Creates a `zip` file consisting of one or more files.

HTTP request:
```http
POST /server/files/zip
Content-Type: application/json

{
    "dest": "config/errorlogs.zip",
    "items": [
        "config/printer.cfg",
        "logs",
        "gcodes/subfolder"
    ],
    "store_only": false
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.zip",
    "params": {
        "dest": "config/errorlogs.zip",
        "items": [
            "config/printer.cfg",
            "logs",
            "gcodes/subfolder"
        ],
        "store_only": false
    },
    "id": 5623
}
```

Parameters:

- `dest` - (Optional) - Relative path to the destination zip.  The first element
  of the path must be valid `root` with write access.  If the path contains subfolders
  the parent folder must exist.  The default is `config/collection-{timestamp}.zip`,
  where `{timestamp}` is generated based on the localtime.
- `items` - (Required) - An array of relative paths containing files and or folders
  to include in the archive.  Each item must meet the following requirements:
    - The first element of the item must be a registered `root` with read access.
    - Each item must point to a valid file or folder.
    - Moonraker must have permission to read the specified files and/or directories.
    - If the path is to a directory then all files with read permissions are included.
      Subfolders are not included recursively.
- `store_only` - (Optional) - If set to `true` then the archive will not compress its
  contents.  Otherwise the traditional `deflation` algorithm is used to compress the
  archives contents.  The default is `false`.

Returns:  An object in the following format:

```json
{
    "destination": {
        "root": "config",
        "path": "errorlogs.zip",
        "modified": 1676984423.8892415,
        "size": 420,
        "permissions": "rw"
    },
    "action": "zip_files"
}
```

- `destination` - an object containing the destination `root` and a path to the file
  relative to the root.
- `action` - The file action, will be `zip_files`

#### File download
Retrieves file `filename` at root `root`.  The `filename` must include
the relative path if it is not in the root folder.

HTTP request:
```http
GET /server/files/{root}/{filename}
```
JSON-RPC request: Not Available

Returns:

The requested file

#### File upload
Upload a file.  Currently files may be uploaded to the `gcodes` or `config`
roots, with `gcodes` being the default.  If one wishes to upload
to a subdirectory, the path may be added to the upload's file name
(relative to the root). If the directory does not exist an error will be
returned.  Alternatively, the `path` form argument may be set, as explained
below.

HTTP request:
```http
POST /server/files/upload`
Content-Type: multipart/form-data

------FormBoundaryemap3PkuvKX0B3HH
Content-Disposition: form-data; name="file"; filename="myfile.gcode"
Content-Type: application/octet-stream

<binary data>
------FormBoundaryemap3PkuvKX0B3HH--
```

The file must be uploaded in the request's body `multipart/form-data` (ie:
`<input type="file">`).  The following arguments may also be added to the
form-data:

- `root`: The root location in which to upload the file.  Currently this may
  be `gcodes` or `config`.  If not specified the default is `gcodes`.
- `path`: This argument may contain a path (relative to the root) indicating
  a subdirectory to which the file is written. If a `path` is present the
  server will attempt to create any subdirectories that do not exist.
- `checksum`: A SHA256 hex digest calculated by the client for the uploaded
  file.  If this argument is supplied the server will compare it to its own
  checksum calculation after the upload has completed.  A checksum mismatch
  will result in a 422 error.

Arguments available only for the `gcodes` root:

- `print`: If set to "true", Klippy will attempt to start the print after
  uploading.  Note that this value should be a string type, not boolean. This
  provides compatibility with OctoPrint's upload API.

JSON-RPC request: Not Available

Returns:  Information about the uploaded file.  Note that `print_started`
is only included when the supplied root is set to `gcodes`.
```json
{
    "item": {
        "path": "Lock Body Shim 1mm_0.2mm_FLEX_MK3S_2h30m.gcode",
        "root": "gcodes",
        "modified": 1676984527.636818,
        "size": 71973,
        "permissions": "rw"
    },
    "print_started": false,
    "print_queued": false,
    "action": "create_file"
}
```

#### File delete
Delete a file in the requested root.  If the file exists in a subdirectory,
its relative path must be part of the `{filename}` argument.

HTTP request:
```http
DELETE /server/files/{root}/{filename}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.delete_file",
    "params": {
        "path": "{root}/{filename}"
    },
    "id": 1323
}
```
Returns:  Information about the deleted file
```json
{
    "item": {
        "path": "Lock Body Shim 1mm_0.2mm_FLEX_MK3S_2h30m.gcode",
        "root": "gcodes",
        "size": 0,
        "modified": 0,
        "permissions": ""
    },
    "action": "delete_file"
}
```

#### Download klippy.log
!!! Note
    Logs are now available in the `logs` root.  Front ends should consider
    presenting all available logs using "file manager" type of UI.  That said,
    If Klipper has not been configured to write logs in the `logs` root then
    this endpoint is available as a fallback.

HTTP request:
```http
GET /server/files/klippy.log
```
JSON-RPC request: Not Available

Returns:

The requested file

#### Download moonraker.log
!!! Note
    Logs are now available in the `logs` root.  Front ends should consider
    presenting all available logs using "file manager" type of UI.  That said,
    If Moonraker has not been configured to write logs in the `logs` root then
    this endpoint is available as a fallback.

HTTP request:
```http
GET /server/files/moonraker.log
```
JSON-RPC request: Not Available

Returns:

The requested file

### Authorization

The Authorization endpoints are enabled when the user has the
`[authorization]` component configured in `moonraker.conf`.

Untrusted clients must use either a JSON Web Token or an API key to access
Moonraker's HTTP APIs.  JWTs should be included in the `Authorization`
header as a `Bearer` type for each HTTP request.  If using an API Key it
should be included in the `X-Api-Key` header for each HTTP Request.

Websocket authentication can be achieved via the request itself or
post connection.  Unlike HTTP requests it is not necessasry to pass a
token and/or API Key to each request.  The
[identify connection](#identify-connection) endpoint takes optional
`access_token` and `api_key` parameters that may be used to authentiate
a user already logged in, otherwise the `login` API may be used for
authentication.  Websocket connections will stay authenticated until
the connection is closed or the user logs out.

!!! note
    ECMAScript imposes limitations on certain requests that prohibit the
    developer from modifying the HTTP headers (ie: The request to open a
    websocket, "download" requests that open a dialog).  In these cases
    it is recommended for the developer to request a `oneshot_token`, then
    send the result via the `token` query string argument in the desired
    request.

!!! warning
    It is strongly recommended that arguments for the below APIs are
    passed in the request's body.

#### Login User
HTTP Request:
```http
POST /access/login
Content-Type: application/json

{
    "username": "my_user",
    "password": "my_password",
    "source": "moonraker"
}
```

JSON-RPC request:
```json
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

Arguments:
- `username`: The user login name.  This argument is required.
- `password`: The user password. This arugment is required.
- `source`:  The authentication source.  Can be `moonraker` or `ldap`. The
  default is `moonraker`.

Returns: An object the logged in username, auth token, refresh token,
and action summary:
```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNjU1LCAiZXhwIjogMTYxODg4MDQwMC40ODE2NTUsICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.QdieeEskrU0FrH7rXKuPDSZxscM54kV_vH60uJqdU9g",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNzUxNCwgImV4cCI6IDE2MjY2NTI4MDAuNDgxNzUxNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.btJF0LJfymInhGJQ2xvPwkp2dFUqwgcw4OA_wE-EcCM",
    "action": "user_logged_in",
    "source": "moonraker"
}
```
- The `token` field is a JSON Web Token used to authorize the user.  It should
  be included in the `Authorization` header as a `Bearer` type for all HTTP
  requests.  The `token` expires after 1 hour.
- The `refresh_token` field contains a JWT that can be used to generate new
  tokens after they are expire. See the
  [refresh token section](#refresh-json-web-token) for details.

!!! Note
    This endpoint may be accessed by unauthorized clients.  A 401 would
    only be returned if the authentication failed.

#### Logout Current User
HTTP Request:
```http
POST /access/logout
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.logout",
    "id": 1323
}
```

Returns: An object containing the logged out username and action summary.
```json
{
    "username": "my_user",
    "action": "user_logged_out"
}

```

#### Get Current User
HTTP Request:
```http
GET /access/user
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.get_user",
    "id": 1323
}
```

Returns: An object containing the currently logged in user name, the source and
the date on which the user was created (in unix time).
```json
{
    "username": "my_user",
    "source": "moonraker",
    "created_on": 1618876783.8896716
}
```

#### Create User
HTTP Request:
```http
POST /access/user
Content-Type: application/json

{
    "username": "my_user",
    "password": "my_password"
}
```

JSON-RPC request:
```json
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

Returns: An object containing the created user name, an auth token,
a refresh token, the source, and an action summary.  Creating a user also
effectively logs the user in.

```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNjE5LCAiZXhwIjogMTYxODg4MDM4My44OTE2MTksICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.oH0IShTL7mdlVs4kcx3BIs_-1j0Oe-qXezJKjo-9Xgo",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNzAyNCwgImV4cCI6IDE2MjY2NTI3ODMuODkxNzAyNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.a6ZeRjk8RQQJDDH0JV-qGY_d_HIgfI3XpsqUlUaFT7c",
    "source": "moonraker",
    "action": "user_created"
}
```
!!! note
    Unlike `/access/login`, `/access/user` is a protected endpoint.  To
    create a new user a client must either be trusted, use the API Key,
    or be logged in as another user.

#### Delete User
Deletes a registered user.

!!! note
    A request to delete a user MUST come from an authorized source
    other than the account to be deleted.  This can be a "trusted user",
    the "api key user", or any other user account.

HTTP Request:
```http
DELETE /access/user
Content-Type: application/json

{
    "username": "my_username"
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.delete_user",
    "params": {
        "username": "my_username"
    },
    "id": 1323
}
```

Returns: The username of the deleted user and an action summary.  This
effectively logs the user out, as all outstanding tokens will be invalid.
```json
{
    "username": "my_user",
    "action": "user_deleted"
}
```

#### List Available Users
HTTP Request:
```http
GET /access/users/list
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.users.list",
    "id": 1323
}
```

Returns: A list of created users on the system
```json
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

#### Reset User Password
HTTP Request:
```http
POST /access/user/password
Content-Type: application/json

{
    "password": "my_current_password",
    "new_password": "my_new_pass"
}
```

JSON-RPC request:
```json
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

Returns:  The username and action summary.
```json
{
    "username": "my_user",
    "action": "user_password_reset"
}
```

#### Refresh JSON Web Token
This endpoint can be used to refresh an expired auth token.  If this
request returns an error then the refresh token is no longer valid and
the user must login with their credentials.

HTTP Request:
```http
POST /access/refresh_jwt
Content-Type: application/json

{
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4Nzc0ODUuNzcyMjg5OCwgImV4cCI6IDE2MjY2NTM0ODUuNzcyMjg5OCwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAicmVmcmVzaCJ9.Y5YxGuYSzwJN2WlunxlR7XNa2Y3GWK-2kt-MzHvLbP8"
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.refresh_jwt",
    "params": {
        "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4Nzc0ODUuNzcyMjg5OCwgImV4cCI6IDE2MjY2NTM0ODUuNzcyMjg5OCwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAicmVmcmVzaCJ9.Y5YxGuYSzwJN2WlunxlR7XNa2Y3GWK-2kt-MzHvLbP8"
    },
    "id": 1323
}
```

Returns:  The username, new auth token, the source and action summary.
```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzgyNDMuNTE2Nzc5MiwgImV4cCI6IDE2MTg4ODE4NDMuNTE2Nzc5MiwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.Ia_X_pf20RR4RAEXcxalZIOzOBOs2OwearWHfRnTSGU",
    "source": "moonraker",
    "action": "user_jwt_refresh"
}
```
!!! Note
    This endpoint may be accessed by unauthorized clients.  A 401 would
    only be returned if the refresh token is invalid.

#### Generate a Oneshot Token

Javascript is not capable of modifying the headers for some HTTP requests
(for example, the `websocket`), which is a requirement to apply JWT or API Key
authorization.  To work around this clients may request a Oneshot Token and
pass it via the query string for these requests.  Tokens expire in 5 seconds
and may only be used once, making them relatively safe for inclusion in the
query string.

HTTP request:
```http
GET /access/oneshot_token
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.oneshot_token",
    "id": 1323
}
```

Returns:

A temporary token that may be added to a request's query string for access
to any API endpoint.  The query string should be added in the form of:
```
?token={base32_random_token}
```

#### Retrieve information about authorization endpoints
HTTP Request:
```http
GET /access/info
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.info",
    "id": 1323
}
```

Returns: An object containing information about authorization endpoints, such as
default_source and available_sources.
```json
{
    "default_source": "moonraker",
    "available_sources": [
        "moonraker",
        "ldap"
    ]
}
```

#### Get the Current API Key
HTTP request:
```http
GET /access/api_key
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.get_api_key",
    "id": 1323
}
```

Returns:

The current API key

#### Generate a New API Key
HTTP request:
```http
POST /access/api_key
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "access.post_api_key",
    "id": 1323
}
```

Returns:

The newly generated API key.  This overwrites the previous key.  Note that
the API key change is applied immediately, all subsequent HTTP requests
from untrusted clients must use the new key.  Changing the API Key will
not affect open websockets authenticated using the previous API Key.

### Database APIs
The following endpoints provide access to Moonraker's lmdb database.  The
database is divided into `namespaces`.  Each client may define its own
namespace to store information.  From the client's point of view, a
namespace is an `object`.  Items in the database are accessed by providing
a namespace and a key.  A key may be specified as string, where a "." is a
delimiter, to access nested fields. Alternatively the key may be specified
as an array of strings, where each string references a nested field.
This is useful for scenarios where your namespace contains keys that include
a "." character.

!!! note
    Moonraker reserves the `moonraker`, `gcode_metadata`, and `history`
    namespaces. Clients may read from these namespaces but they may not
    modify them.

For example, assume the following object is stored in the "superclient"
namespace:

```json
{
    "settings": {
        "console": {
            "enable_autocomplete": true
        }
    },
    "theme": {
        "background_color": "black"
    }
}
```
One may access the `enable_autocomplete` field by supplying `superclient` as
the `namespace` argument and `settings.console.enable_autocomplete` or
`["settings", "console", "enable_autocomplete"]` as the `key` argument for
the request.  The entire settings object could be accessed by providing
`settings` or `["settings"]` as the `key` argument.  The entire namespace
may be read by omitting the `key` argument, however as explained below it
is not possible to modify a namespace without specifying a key.

#### List namespaces
Lists all available namespaces.

HTTP request:
```http
GET /server/database/list
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.database.list",
    "id": 8694
}
```

Returns:

An object containing an array of namespaces in the following format:
```json
{
    "namespaces": [
        "gcode_metadata",
        "history",
        "moonraker",
        "test_namespace"
    ]
}
```

#### Get Database Item
Retrieves an item from a specified namespace. The `key` argument may be
omitted, in which case an object representing the entire namespace will
be returned in the `value` field.  If the `key` is provided and does not
exist in the database an error will be returned.

HTTP request:
```http
GET /server/database/item?namespace={namespace}&key={key}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.database.get_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 5644
}
```
Returns:

An object containing the requested `namespace`, `key`, and `value`.
```json
{
    "namespace": "moonraker",
    "key": "file_manager.metadata_version",
    "value": 2
}
```

#### Add Database Item
Inserts an item into the database.  If the `namespace` does not exist
it will be created.  If the `key` specifies a nested field, all parents
will be created if they do not exist.  If the key exists it will be
overwritten with the provided `value`.  The `key` parameter must be provided,
as it is not possible to assign a value directly to a namespace.

HTTP request:
```http
POST /server/database/item?namespace={namespace}&key={key}value={value}`
```
!!! note
    If the `value` is not a string type, the `value` argument must
    provide a [type hint](#query-string-type-hints).  Alternatively,
    arguments may be passed via the request body in JSON format. For
    example:
```http
POST /server/database/item
Content-Type: application/json

{
    "namespace": "my_client",
    "key": "settings.some_count",
    "value": 100
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.database.post_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}",
        "value": 100
    },
    "id": 4654
}
```
Returns:

An object containing the inserted `namespace`, `key`, and `value`.
```json
{
    "namespace": "test",
    "key": "settings.some_count",
    "value": 9001
}
```

#### Delete Database Item
Deletes an item from a `namespace` at the specified `key`. If the key does not
exist in the namespace an error will be returned.  If the deleted item results
in an empty namespace, the namespace will be removed from the database.

HTTP request:
```http
DELETE /server/database/item?namespace={namespace}&key={key}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.database.delete_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 4654
}
```
Returns:
An object containing the `namespace`, `key`, and `value` of the
deleted item.
```json
{
    "namespace": "test",
    "key": "settings.some_count",
    "value": 9001
}
```

### Job Queue APIs

The following endpoints may be used to manage Moonraker's job queue.
Note that Moonraker's Job Queue is implemented as a FIFO queue and it may
contain multiple references to the same job.

!!! Note
    All filenames provided to and returned by these endpoints are relative to
    the `gcodes` root.

#### Retrieve the job queue status

Retrieves the current state of the job queue

HTTP request:
```http
GET /server/job_queue/status
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.status",
    "id": 4654
}
```

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "ready"
}
```

Below is a description of the returned fields:

- `queued_jobs`: an array of objects representing each queued job.  Each
  object contains the `filename` of the enqueued job and a unique `job_id`
  generated for each job.  The `job_id` is a 64-bit Hexadecimal string value.
  On 32-bit systems the most significant bits will always contain zeros.  Items
  are ordered by the time they were queued, the first item will be the next job
  loaded.
- `queue_state`: The current state of the queue.  Can be one of the following:
    - `ready`: The queue is active and will load the next job upon completion
      of the current job
    - `loading`: The queue is currently loading the next job. If the user
      specified a `job_transition_delay` and/or `job_transition_gcode`, the
      queue will remain in the `loading` state until both are completed or
      an error is encountered.
    - `starting`: The queue enters this state after the `loading` phase is
      complete before attempting to start the job.
    - `paused`:  The queue is currently paused and will not load the next job
      upon completion of the current job.  The queue will enter the `paused`
      state if an error is encountered during the `loading` or `starting` phases,
      or if the user pauses the queue through the provided endpoint.
- `time_added`: The time (in Unix Time) the job was added to the queue
- `time_in_queue`: The cumulative amount of time (in seconds) the job has been
  pending in the queue

#### Enqueue a job

Adds a job, or an array of jobs, to the end of the job queue.  The same
filename may be specified multiple times to queue a job that repeats.
When multiple jobs are specified they will be enqueued in the order they
are received.

!!! Note
    The request will be aborted and return an error if any of the supplied
    files do not exist.

HTTP request:
```http
POST /server/job_queue/job?filenames=job1.gcode,job2.gcode,subdir/job3.gcode
```

!!! Note
    Multiple jobs should be comma separated as shown above.
    Alternatively `filenames` maybe be specified as a json object
    in the body of the request.

```http
POST /server/job_queue/job
Content-Type: application/json

{
    "filenames": [
        "job1.gcode",
        "job2.gcode",
        "subdir/job3.gcode"
    ],
    "reset": false
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.post_job",
    "params": {
        "filenames": [
            "job1.gcode",
            "job2.gcode",
            "subdir/job3.gcode"
        ],
        "reset": false
    },
    "id": 4654
}
```

Parameters:

- `reset`: A boolean value indicating whether Moonraker should clear the
  existing queued jobs before adding the new jobs. Defaults to `false`.

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "ready"
}
```

#### Remove a Job

Removes one or more jobs from the queue.

!!! Note
    Unlike the POST version of this method, it is not necessary that
    all job ids exist.  If any supplied job id does not exist in the
    queue it will be silently ignored.  Clients can verify the contents
    of the queue via the return value.

HTTP request:
```http
DELETE /server/job_queue/job?job_ids=0000000066D991F0,0000000066D99D80
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.delete_job",
    "params": {
        "job_ids": [
            "0000000066D991F0".
            "0000000066D99D80"
        ]
    },
    "id": 4654
}
```
!!! Tip
    Alternatively `all=true` (`"all": true` for JSON-RPC) may specified
    to clear the job queue.

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        }
    ],
    "queue_state": "ready"
}
```
#### Pause the job queue

Sets the job queue state to "pause", which prevents the next job
in the queue from loading after an job in progress is complete.

!!! Note
    If the queue is paused while the queue is in the `loading` state
    the load will be aborted.

HTTP request:
```http
POST /server/job_queue/pause
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.pause",
    "id": 4654
}
```

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "paused"
}
```

#### Start the job queue

Starts the job queue.  If Klipper is ready to start a print the next
job in the queue will be loaded.  Otherwise the queue will be put
into the "ready" state, enabling automatic transition after the next
completed print.

HTTP request:
```http
POST /server/job_queue/start
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.start",
    "id": 4654
}
```

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "loading"
}
```

#### Perform a Queue Jump

Jumps a job to the front of the queue.

HTTP request:
```http
POST /server/job_queue/jump?job_id=0000000066D991F0
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.jump",
    "params" {
        "job_id": "0000000066D991F0"
    },
    "id": 4654
}
```

Returns:

The current state of the job queue:

```json
{
    "queued_jobs": [
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "loading"
}
```

### Announcement APIs
The following endpoints are available to manage announcements.  See
[the appendix](#announcements) for details on how
announcements work and recommendations for your implementation.

#### List announcements
Retrieves a list of current announcements. The `include_dismissed`
argument is optional and defaults to `true`.  If set to `false`
dismissed entries will be omitted from the return value.

HTTP request:
```http
GET /server/announcements/list?include_dismissed=false
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.list",
    "params": {
        "include_dismissed": false
    },
    "id": 4654
}
```

Returns:

The current list of announcements, in descending order (newest to oldest)
sorted by `date` and a list of feeds Moonraker is currently subscribed to:

```json
{
    {
    "entries": [
        {
            "entry_id": "arksine/moonlight/issue/3",
            "url": "https://github.com/Arksine/moonlight/issues/3",
            "title": "Test announcement 3",
            "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
            "priority": "normal",
            "date": 1647459219,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/2",
            "url": "https://github.com/Arksine/moonlight/issues/2",
            "title": "Announcement Test Two",
            "description": "This is a high priority announcement. This line is included in the description.",
            "priority": "high",
            "date": 1646855579,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/1",
            "url": "https://github.com/Arksine/moonlight/issues/1",
            "title": "Announcement Test One",
            "description": "This is the description.  Anything here should appear in the announcement, up to 512 characters.",
            "priority": "normal",
            "date": 1646854678,
            "dismissed": false,
            "date_dismissed": null,
            "dismiss_wake": null,
            "source": "moonlight",
            "feed": "moonlight"
        },
        {
            "entry_id": "arksine/moonraker/issue/349",
            "url": "https://github.com/Arksine/moonraker/issues/349",
            "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
            "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
            "priority": "normal",
            "date": 1643392406,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonraker"
        }
    ],
    "feeds": [
        "moonraker",
        "klipper",
        "moonlight"
    ]
}
}
```

#### Update announcements
Requests that Moonraker check for announcement updates.  This is generally
not required in production, as Moonraker will automatically check for
updates every 30 minutes.  However, during development this endpoint is
useful to force an update when it is necessary to perform integration
tests.

HTTP request:
```http
POST /server/announcements/update
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.update",
    "id": 4654
}
```

Returns:

The current list of announcements, in descending order (newest to oldest)
sorted by `date`, and a `modified` field that contains a boolean value
indicating if the update resulted in a change:

```json
{
    "entries": [
        {
            "entry_id": "arksine/moonraker/issue/349",
            "url": "https://github.com/Arksine/moonraker/issues/349",
            "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
            "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
            "priority": "normal",
            "date": 1643392406,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonraker"
        },
        {
            "entry_id": "arksine/moonlight/issue/1",
            "url": "https://github.com/Arksine/moonlight/issues/1",
            "title": "Announcement Test One",
            "description": "This is the description.  Anything here should appear in the announcement, up to 512 characters.",
            "priority": "normal",
            "date": 1646854678,
            "dismissed": true,
            "source": "moonlight",
            "feed": "Moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/2",
            "url": "https://github.com/Arksine/moonlight/issues/2",
            "title": "Announcement Test Two",
            "description": "This is a high priority announcement. This line is included in the description.",
            "priority": "high",
            "date": 1646855579,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonlight"
        },
        {
            "entry_id": "arksine/moonlight/issue/3",
            "url": "https://github.com/Arksine/moonlight/issues/3",
            "title": "Test announcement 3",
            "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
            "priority": "normal",
            "date": 1647459219,
            "dismissed": false,
            "source": "moonlight",
            "feed": "Moonlight"
        }
    ],
    "modified": false
}
```

#### Dismiss an announcement
Sets the dismiss flag of an announcement to `true`.  The `entry_id`
field is required.  The `entry_id` contains forward slashes so remember
to escape the ID if including it in the query string of an HTTP request.

HTTP request:
```http
POST /server/announcements/dismiss?entry_id=arksine%2Fmoonlight%2Fissue%2F1&wake_time=600
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.dismiss",
    "params": {
        "entry_id": "arksine/moonlight/issue/1",
        "wake_time": 600
    },
    "id": 4654
}
```

Parameters:

- `entry_id`:  The entry identifier.  This field may contain forward slashes so
  it should be url escaped when placed in the query string of an http request.
  This parameter is required.
- `wake_time`:  The time, in seconds, in which the entry's `dismissed` state
  will revert to false.  This parameter is optional, if omitted the entry will
  be dismissed indefinitely.

Returns:

The `entry_id` of the dismissed entry:

```json
{
    "entry_id": "arksine/moonlight/issue/1"
}
```

#### List announcement feeds

HTTP request:
```http
GET /server/announcements/feeds
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.feeds",
    "id": 4654
}
```

Returns:

A list of feeds the instance of Moonraker is subscribed to.

```json
{
    "feeds": [
        "moonraker",
        "klipper"
    ]
}
```

#### Add an announcement feed
Specifies a new feed for Moonraker's `announcements` component to query
in addition to `moonraker`, `klipper`, and feeds configured in
`moonraker.conf`.

HTTP request:
```http
POST /server/announcements/feed?name=my_feed
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.post_feed",
    "params": {
        "name": "my_feed"
    },
    "id": 4654
}
```

Parameters:

- `name`:  The name of the new feed.  This parameter is required.

Returns:

The name of the new feed and the action taken.  The `action` will be `added`
if a new feed added, or `skipped` if the feed already exists.

```json
{
    "feed": "my_feed",
    "action": "added"
}
```

#### Remove an announcement feed
Removes a subscribed feed.  Only feeds previously subscribed to using
the [add feed](#add-an-announcement-feed) API may be removed. Feeds
configured in `moonraker.conf` may not be removed.

HTTP request:
```http
DELETE /server/announcements/feed?name=my_feed
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.announcements.delete_feed",
    "params": {
        "name": "my_feed"
    },
    "id": 4654
}
```

Parameters:

- `name`:  The name of the new feed to remove.  This parameter is required.

Returns:

The name of the new feed and the action taken.  The `action` will be
`removed` if the operation was successful.

```json
{
    "feed": "my_feed",
    "action": "removed"
}
```

### Webcam APIs
The following APIs are available to manage webcam configuration:

#### List Webcams

HTTP request:
```http
GET /server/webcams/list
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.webcams.list",
    "id": 4654
}
```

Returns:

A list of configured webcams:

```json
{
    "webcams": [
        {
            "name": "testcam3",
            "location": "door",
            "service": "mjpegstreamer",
            "enabled": true,
            "icon": "mdiWebcam",
            "target_fps": 20,
            "target_fps_idle": 5,
            "stream_url": "http://camera.lan/webcam?action=stream",
            "snapshot_url": "http://camera.lan/webcam?action=snapshot",
            "flip_horizontal": false,
            "flip_vertical": true,
            "rotation": 90,
            "aspect_ratio": "4:3",
            "extra_data": {},
            "source": "config"
        },
        {
            "name": "tc2",
            "location": "printer",
            "service": "mjpegstreamer",
            "enabled": true,
            "icon": "mdiWebcam",
            "target_fps": 15,
            "target_fps_idle": 5,
            "stream_url": "http://printer.lan/webcam?action=stream",
            "snapshot_url": "http://printer.lan/webcam?action=snapshot",
            "flip_horizontal": false,
            "flip_vertical": false,
            "rotation": 0,
            "aspect_ratio": "4:3",
            "extra_data": {},
            "source": "database"
        },
        {
            "name": "TestCam",
            "location": "printer",
            "service": "mjpegstreamer",
            "enabled": true,
            "icon": "mdiWebcam",
            "target_fps": 15,
            "target_fps_idle": 5,
            "stream_url": "/webcam/?action=stream",
            "snapshot_url": "/webcam/?action=snapshot",
            "flip_horizontal": false,
            "flip_vertical": false,
            "rotation": 0,
            "aspect_ratio": "4:3",
            "extra_data": {},
            "source": "database"
        }
    ]
}
```

#### Get Webcam Information

HTTP request:
```http
GET /server/webcams/item?name=cam_name
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.webcams.get_item",
    "params": {
        "name": "cam_name"
    },
    "id": 4654
}
```

Parameters:

- `name`: The name of the camera to request information for.  If the named
  camera is not available the request will return with an error.  This
  parameter must be provided.

Returns:

The full configuration for the requested webcam:

```json
{
    "webcam": {
        "name": "TestCam",
        "location": "printer",
        "service": "mjpegstreamer",
        "enabled": true,
        "icon": "mdiWebcam",
        "target_fps": 15,
        "target_fps_idle": 5,
        "stream_url": "/webcam/?action=stream",
        "snapshot_url": "/webcam/?action=snapshot",
        "flip_horizontal": false,
        "flip_vertical": false,
        "rotation": 0,
        "aspect_ratio": "4:3",
        "extra_data": {},
        "source": "database"
    }
}
```
#### Add or update a webcam

Adds a new webcam entry or updates an existing entry.  When updating
an entry only the fields provided will be modified.

!!! Note
    A webcam configured via `moonraker.conf` cannot be updated or
    overwritten using this API.

HTTP request:
```http
POST /server/webcams/item
Content-Type: application/json

{
    "name": "cam_name",
    "snapshot_url": "http://printer.lan:8080/webcam?action=snapshot",
    "stream_url": "http://printer.lan:8080/webcam?action=stream"
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.webcams.post_item",
    "params": {
        "name": "cam_name",
        "snapshot_url": "/webcam?action=snapshot",
        "stream_url": "/webcam?action=stream"
    },
    "id": 4654
}
```

Parameters:

- `name`: The name of the camera to add or update.  This parameter must
  be provided for new entries.
- `location`: A description of the webcam location, ie: what the webcam is
  observing.  The default is `printer` for new entries.
- `icon`:  The name of the icon to use for the camera. The default is `mdiWebcam`
  for new entries.
- `enabled`:  A boolean value to indicate if this webcam should be enabled.
   Default is True for new entries.
- `service`: The name of the webcam application streaming service.  The default
  is "mjpegstreamer" for new entries.
- `target_fps`:  The target framerate.  The default is 15 for new entries.
- `target_fps_idle`: The target framerate when the printer is idle.
   The default is 5 for new entries.
- `stream_url`:  The url for the camera stream request.  This may be a full url
  or a url relative to Moonraker's host machine.  If the url is relative it is
  assumed that the stream is available over http on port 80. This parameter
  must be provided for new entries.
- `snapshot_url`: The url for the camera snapshot request. This may be a full
  url or a url relative to Moonraker's host machine.  If the url is relative
  it is assumed that the snapshot is available over http on port 80. The
  default is an empty string for new entries.
- `flip_horizontal`:  A boolean value indicating whether the stream should be
  flipped horizontally.  The default is false for new entries.
- `flip_vertical`: A boolean value indicating whether the stream should be
  flipped vertically.  The default is false for new entries.
- `rotation`: An integer value indicating the amount of clockwise rotation to
   apply to the stream.  May be 0, 90, 180, or 270.  The default is 0 for new entries.
- `aspect_ratio`: The aspect ratio to display for the camera.  Note that this option
   is specific to certain services, otherwise it is ignored. The default is `4:3`
   for new entries.
- `extra_data`:  Additional webcam data set by the front end in the form of a json
  object.  This may be used to store any additional webcam options and/or data. The
  default is an empty object for new entries.

Returns:

The full configuration for the added/updated webcam:

```json
{
    "webcam": {
        "name": "TestCam",
        "location": "printer",
        "service": "mjpegstreamer",
        "enabled": true,
        "icon": "mdiWebcam",
        "target_fps": 15,
        "target_fps_idle": 5,
        "stream_url": "/webcam/?action=stream",
        "snapshot_url": "/webcam/?action=snapshot",
        "flip_horizontal": false,
        "flip_vertical": false,
        "rotation": 0,
        "aspect_ratio": "4:3",
        "extra_data": {},
        "source": "database"
    }
}
```

#### Delete a webcam

!!! Note
    A webcam configured via `moonraker.conf` cannot be deleted
    using this API.

HTTP request:
```http
DELETE /server/webcams/item?name=cam_name
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.webcams.delete_item",
    "params": {
        "name": "cam_name"
    },
    "id": 4654
}
```

Parameters:

- `name`: The name of the camera to delete.  If the named camera is not
  available the request will return with an error.  This parameter must
  be provided.

Returns:

The full configuration of the deleted webcam:

```json
{
    "webcam": {
        "name": "TestCam",
        "location": "printer",
        "service": "mjpegstreamer",
        "target_fps": 15,
        "stream_url": "/webcam/?action=stream",
        "snapshot_url": "/webcam/?action=snapshot",
        "flip_horizontal": false,
        "flip_vertical": false,
        "rotation": 0,
        "source": "database"
    }
}
```

#### Test a webcam

Resolves a webcam's stream and snapshot urls.  If the snapshot
is served over http, a test is performed to see if the url is
reachable.

HTTP request:
```http
POST /server/webcams/test?name=cam_name
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.webcams.test",
    "params": {
        "name": "cam_name"
    },
    "id": 4654
}
```

Parameters:

- `name`: The name of the camera to test.  If the named camera is not
  available the request will return with an error.  This parameter must
  be provided.

Returns: Test results in the following format

```json
{
    "name": "TestCam",
    "snapshot_reachable": true,
    "snapshot_url": "http://127.0.0.1:80/webcam/?action=snapshot",
    "stream_url": "http://127.0.0.1:80/webcam/?action=stream"
}
```

### Notifier APIs
The following APIs are available to view and tests notifiers.

#### List Notifiers

HTTP request:
```http
GET /server/notifiers/list
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.notifiers.list",
    "id": 4654
}
```

Returns:

A list of configured notifiers:

```json
{
    "notifiers": [
        {
            "name": "print_start",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "started"
            ],
            "body": "Your printer started printing '{event_args[1].filename}'",
            "title": null,
            "attach": null
        },
        {
            "name": "print_complete",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "complete"
            ],
            "body": "Your printer completed printing '{event_args[1].filename}",
            "title": null,
            "attach": "http://192.168.1.100/webcam/?action=snapshot"
        },
        {
            "name": "print_error",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "error"
            ],
            "body": "{event_args[1].message}",
            "title": null,
            "attach": "http://192.168.1.100/webcam/?action=snapshot"
        }
    ]
}
```

### Update Manager APIs
The following endpoints are available when the `[update_manager]` component has
been configured:

#### Get update status
Retrieves the current state of each item available for update.  Items may
include the linux package manager (`system`), applications such as `moonraker` and
`klipper`, web clients such as `mainsail` and `fluidd`, and other configured
applications/extensions.

HTTP request:
```http
GET /machine/update/status?refresh=false
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.status",
    "params": {
        "refresh": false
    },
    "id": 4644
}
```

Parameters:

- `refresh`: (Optional) When set to true state for all updaters will be refreshed.
  The default is `false`.  A request to refresh is aborted under the following
  conditions:
    - An update is in progress
    - A print is in progress
    - The update manager hasn't completed initialization
    - A previous refresh has occured within the last 60 seconds

!!! Note
    The `refresh` parameter is deprecated.  Client developers should use the
    [refresh endpoint](#refresh-application-state) to request a refresh.

Returns:

Status information for each update package.  Note that `mainsail`
and `fluidd` are present as clients configured in `moonraker.conf`
```json
{
    "busy": false,
    "github_rate_limit": 60,
    "github_requests_remaining": 57,
    "github_limit_reset_time": 1615836932,
    "version_info": {
        "system": {
            "package_count": 4,
            "package_list": [
                "libtiff5",
                "raspberrypi-sys-mods",
                "rpi-eeprom-images",
                "rpi-eeprom"
            ]
        },
        "moonraker": {
            "channel": "dev",
            "debug_enabled": true,
            "is_valid": true,
            "configured_type": "git_repo",
            "corrupt": false,
            "info_tags": [],
            "detected_type": "git_repo",
            "remote_alias": "arksine",
            "branch": "master",
            "owner": "?",
            "repo_name": "moonraker",
            "version": "v0.7.1-364",
            "remote_version": "v0.7.1-364",
            "rollback_version": "v0.7.1-360",
            "current_hash": "ecfad5cff15fff1d82cb9bdc64d6b548ed53dfaf",
            "remote_hash": "ecfad5cff15fff1d82cb9bdc64d6b548ed53dfaf",
            "is_dirty": false,
            "detached": true,
            "commits_behind": [],
            "git_messages": [],
            "full_version_string": "v0.7.1-364-gecfad5c",
            "pristine": true,
            "recovery_url": "https://github.com/Arksine/moonraker.git",
            "remote_url": "https://github.com/Arksine/moonraker.git",
            "warnings": [],
            "anomalies": [
                "Unofficial remote url: https://github.com/Arksine/moonraker-fork.git",
                "Repo not on offical remote/branch, expected: origin/master, detected: altremote/altbranch",
                "Detached HEAD detected"
            ]
        },
        "mainsail": {
            "name": "mainsail",
            "owner": "mainsail-crew",
            "version": "v2.1.1",
            "remote_version": "v2.1.1",
            "rollback_version": "v2.0.0",
            "configured_type": "web",
            "channel": "stable",
            "info_tags": [
                "desc=Mainsail Web Client",
                "action=some_action"
            ],
            "warnings": [],
            "anomalies": [],
            "is_valid": true
        },
        "fluidd": {
            "name": "fluidd",
            "owner": "fluidd-core",
            "version": "v1.16.2",
            "remote_version": "v1.16.2",
            "rollback_version": "v1.15.0",
            "configured_type": "web",
            "channel": "beta",
            "info_tags": [],
            "warnings": [],
            "anomalies": [],
            "is_valid": true
        },
        "klipper": {
            "channel": "dev",
            "debug_enabled": true,
            "is_valid": true,
            "configured_type": "git_repo",
            "corrupt": false,
            "info_tags": [],
            "detected_type": "git_repo",
            "remote_alias": "origin",
            "branch": "master",
            "owner": "Klipper3d",
            "repo_name": "klipper",
            "version": "v0.10.0-1",
            "remote_version": "v0.10.0-41",
            "rollback_version": "v0.9.1-340",
            "current_hash": "4c8d24ae03eadf3fc5a28efb1209ce810251d02d",
            "remote_hash": "e3cbe7ea3663a8cd10207a9aecc4e5458aeb1f1f",
            "is_dirty": false,
            "detached": false,
            "commits_behind": [
                {
                    "sha": "e3cbe7ea3663a8cd10207a9aecc4e5458aeb1f1f",
                    "author": "Kevin O'Connor",
                    "date": "1644534721",
                    "subject": "stm32: Clear SPE flag on a change to SPI CR1 register",
                    "message": "The stm32 specs indicate that the SPE bit must be cleared before\nchanging the CPHA or CPOL bits.\n\nReported by @cbc02009 and @bigtreetech.\n\nSigned-off-by: Kevin O'Connor <kevin@koconnor.net>",
                    "tag": null
                },
                {
                    "sha": "99d55185a21703611b862f6ce4b80bba70a9c4b5",
                    "author": "Kevin O'Connor",
                    "date": "1644532075",
                    "subject": "stm32: Wait for transmission to complete before returning from spi_transfer()",
                    "message": "It's possible for the SCLK pin to still be updating even after the\nlast byte of data has been read from the receive pin.  (In particular\nin spi mode 0 and 1.)  Exiting early from spi_transfer() in this case\ncould result in the CS pin being raised before the final updates to\nSCLK pin.\n\nAdd an additional wait at the end of spi_transfer() to avoid this\nissue.\n\nSigned-off-by: Kevin O'Connor <kevin@koconnor.net>",
                    "tag": null
                },
            ],
            "git_messages": [],
            "full_version_string": "v0.10.0-1-g4c8d24ae-shallow",
            "pristine": true,
            "recovery_url": "https://github.com/Klipper3d/klipper.git",
            "remote_url": "https://github.com/Klipper3d/klipper.git",
            "warnings": [],
            "anomalies": [],
        }
    }
}
```
Below is an explanation for each field:

- `busy`: set to true if an update is in progress.  Moonraker will not
  allow concurrent updates.
- `github_rate_limit`: the maximum number of github API requests
  the user currently is allowed.  An unauthenticated user typically has 60
  requests per hour.
- `github_requests_remaining`: the number of API request the user
  currently has remaining.
- `github_limit_reset_time`:  the time when the rate limit will reset,
  reported as seconds since the epoch (aka Unix Time).

Extensions configured with the `git_repo` type will contain the following
fields:

- `configured_type`: the application type configured by the user
- `detected_type`:  the application type as detected by Moonraker.
- `channel`:  the currently configured update channel.  For Moonraker
  and Klipper this is set in the `[update_manager]` configuration.
  For clients the channel is determined by the configured type
- `pristine`: Indicates that there are no modified files or untracked
  source files in a `git_repo`.  A repo with untracked files can still
  be updated, however a repo with modified files (ie: `dirty`) cannot
  be updated.
- `owner`: the owner of the repo / application
- `branch`: the name of the current git branch.  This should typically
    be "master".
- `remote_alias`: the alias for the remote.  This should typically be
    "origin".
- `version`:  abbreviated version of the current repo on disk
- `remote_version`: abbreviated version of the latest available update
- `rollback_version`: version the repo will revert to when a rollback is
   requested
- `full_version_string`:  The complete version string of the current repo.
- `current_hash`: hash of the most recent commit on disk
- `remote_hash`: hash of the most recent commit pushed to the remote
- `is_valid`: true if the `git_repo` is valid and can be updated.
- `corrupt`: Indicates that the git repo has been corrupted.  When a repo
  is in this state it a hard recovery (ie: re-cloning the repo) is necessary.
  Note that the most common cause of repo corruption is removing power from
  the host machine without safely shutting down.  Damaged storage can also
  lead to repo corruption.
- `is_dirty`: true if a `git_repo` has modified files.  A dirty repo cannot
  be updated.
- `detached`: true if the `git_repo` is currently in a detached state.
- `debug_enabled`: True when debug flag has been set via the command line.
  When debug is enabled Moonraker will allow detached updates.
- `commits_behind`: A list of commits behind.  Up to 30 "untagged" commits
  will be reported.  Moonraker checks the last 100 commits for tags, any
  commits beyond the last 30 with a tag will also be reported.
- `git_messages`:  If a repo is in the "invalid" state this field will hold
  a list of string messages containing the output of the last failed git
  command.  Note that it is possible for a git command to fail without
  providing output (for example, it may become non-responsive and time out),
  so it is possible for this field to be an empty list when the repo is
  invalid.
- `info_tags`: These are tags defined in the `[update_manager client_name]`
  configuration for each client. Client developers my define what tags,
  if any, users will configure.  They can choose to use those tags to display
  information or perform an additional action after an update if necessary.
- `recovery_url`:  The url Moonraker will use to re-clone the repo when a
  hard recovery is requested.  If this reports a "?" then a hard recovery is
  not possible.
- `remote_url`:  The url for the currently configured remote.
- `warnings`:  An array of strings that describe warnings detected during
  repo init.  These warnings provide additional context when the `is_valid`
  field reports `true`.
- `anomalies`:  An array of strings that describe anomalies found during
  initialization.  An anomaly can be defined as an unexpected condition, they
  will not result in an invalid state, nor will they prevent an update.  For
  example, when the detected remote url does not match the configured/expected
  url Moonraker will fall back to the detected url and report this condition
  as an anomaly.

Extensions configured with the `web` type will contain the following fields:

- `channel`: channel to fetch updates from
- `configured_type`: will be `web`
- `name`: name of the configured client
- `owner`: the owner of the client
- `version`:  version of the installed client.
- `remote_version`:  version of the latest release published to GitHub
- `rollback_version`: version the client will revert to when a rollback is
   requested
- `info_tags`: These are tags defined in the `[update_manager client_name]`
  configuration for each client. Client developers my define what tags,
  if any, users will configure.  They can choose to use those tags to display
  information or perform an additional action after an update if necessary.
- `is_valid`: A boolean that reports true if an update is possible, false
  if an update cannot be performed.
- `warnings`:  An array of strings that describe warnings detected during
  updater init.  These warnings add context when the `is_valid` field reports
  `true`.
- `anomalies`:  An array of strings that describe anomalies found during
  initialization.  An anomaly can be defined as an unexpected condition, they
  will not result in an invalid state, nor will they prevent an update.
  For example, when the configured repo to check for updates does not match
  the detected repo Moonraker will fall back to the detected repo and report
  this condition as an anomaly.


The `system` object contains the following fields:

- `package_count`: the number of system packages available for update
- `package_list`: an array containing the names of packages available
  for update

#### Refresh update status

Refreshes the internal update state for the requested item(s).

HTTP request:
```http
POST /machine/update/refresh?name=klipper
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.refresh",
    "params": {
        "name": "klipper"
    },
    "id": 4644
}
```

Parameters:

- `name`: (Optional) The name of the specified application.  If omitted
  all registered applications will be refreshed.

Returns:

An object containing full update status matching the response in the
[status endpoint](#get-update-status).

!!! Note
    This endpoint will raise 503 error under the following conditions:

      - An update is in progress
      - A print is in progress
      - The update manager hasn't completed initialization

!!! Warning
    Applications should use care when calling this method as a refresh
    is CPU intensive and may be time consuming.  Moonraker can be
    configured to refresh state periodically, thus it is recommended
    that applications avoid their own procedural implementations.
    Instead it is best to call this API only when a user requests a
    refresh.

#### Perform a full update
Attempts to update all configured items in Moonraker.  Updates are
performed in the following order:

- `system` if enabled
- All configured clients
- Klipper
- Moonraker

HTTP request:
```http
POST /machine/update/full
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.full",
    "id": 4645
}
```
Returns:

`ok` when complete


#### Update Moonraker
Pulls the most recent version of Moonraker from GitHub and restarts
the service. If an update is requested while a print is in progress then
this request will return an error.

HTTP request:
```http
POST /machine/update/moonraker
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.moonraker",
    "id": 4645
}
```
Returns:

`ok` when complete

#### Update Klipper
Pulls the most recent version of Klipper from GitHub and restarts
the service. If an update is requested while a print is in progress
then this request will return an error.

HTTP request:
```http
POST /machine/update/klipper
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.klipper",
    "id": 5745
}
```
Returns:

`ok` when complete

#### Update Client
If one more more `[update_manager client client_name]` sections have
been configured this endpoint can be used to install the most recently
published release of the client.  If an update is requested while a
print is in progress then this request will return an error.  The
`name` argument is required, it's value should match the `client_name`
of the configured section.

HTTP request:
```http
POST /machine/update/client?name={client_name}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":  "machine.update.client",
    "params": {
        "name": "client_name"
    },
    "id": 8546
}
```
Returns:

`ok` when complete

#### Update System Packages
Upgrades system packages.  Currently only `apt-get` is supported.
If an update is requested while a print is in progress then this request
will return an error.

HTTP request:
```http
POST /machine/update/system
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.system",
    "id": 4564
}
```
Returns:

`ok` when complete

#### Recover a corrupt repo
On occasion a git command may fail resulting in a repo in a
dirty or invalid state.  When this happens it is possible
to recover.  The `name` argument must specify the name of
the repo to recover, it must be of a git repo type. There are two
methods of recovery, the `hard` argument determines which method
is used:

- `hard == true`: Moonraker will remove the old directory
  entirely.  It will then attempt to recover with `rsync`
  by restoring a backup of a recent valid repo.
- `hard == false`:  Will run `git clean -f -d` followed
  by `git reset --hard {remote}/{branch}`.  This is useful
  for recovering dirty repos that are valid.  It is possible
  that this will work on an invalid repo, however it will
  not work on a corrupt repo.

The `hard` argument defaults to `false`.

HTTP request:
```http
POST /machine/update/recover?name=moonraker&hard=false
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.recover",
    "params": {
        "name": "moonraker",
        "hard": false
    },
    "id": 4564
}
```
Returns:

`ok` when complete

### Rollback to the previous version

HTTP request:

```http
POST /machine/update/rollback?name=moonraker
```

JSON-RPC request:

```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.rollback",
    "params": {
        "name": "moonraker"
    },
    "id": 4564
}
```

Returns:

`ok` when complete

### Power APIs
The APIs below are available when the `[power]` component has been configured.

#### Get Device List
HTTP request:
```http
GET /machine/device_power/devices
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.device_power.devices",
    "id": 5646
}
```
Returns:

An array of objects containing info for each configured device.
```json
{
    "devices": [
        {
            "device": "green_led",
            "status": "off",
            "locked_while_printing": true,
            "type": "gpio"
        },
        {
            "device": "printer",
            "status": "off",
            "locked_while_printing": false,
            "type": "tplink_smartplug"
        }
    ]
}
```

#### Get Device Status
Returns the status for a single configured device.

HTTP request:
```http
GET /machine/device_power/device?device=green_led
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.get_device",
    "params": {
        "device": "green_led"
    },
    "id": 4564
}
```
Returns:

An object containing power state for the requested device:
```json
{
    "green_led": "off"
}
```

#### Set Device State
Toggle, turn on, or turn off a specified device.

HTTP request:
```http
POST /machine/device_power/device?device=green_led&action=on
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.post_device",
    "params": {
        "device": "green_led",
        "action": "on"
    },
    "id": 4564
}
```

!!! note
    The `action` argument may be `on`, `off`, or `toggle`.  Any
    other value will result in an error.

Returns:

An object containing new power state for the requested device:
```json
{
    "green_led": "off"
}
```

#### Get Batch Device Status
Get power status for the requested devices.  At least one device must be
specified.

HTTP request:
```http
GET /machine/device_power/status?dev_one&dev_two
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.status",
    "params": {
        "dev_one":null,
        "dev_two": null
    },
    "id": 4564
}
```
Returns:

An object containing power state for each requested device:
```json
{
    "green_led": "off",
    "printer": "off"
}
```

#### Batch Power On Devices
Power on the requested devices.  At least one device must be
specified.

HTTP request:
```http
POST /machine/device_power/on?dev_one&dev_two
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.on",
    "params": {
        "dev_one":null,
        "dev_two": null
    },
    "id": 4564
}
```
An object containing power state for each requested device:
```json
{
    "green_led": "on",
    "printer": "on"
}
```

#### Batch Power Off Devices
Power off the requested devices.  At least one device must be
specified.

HTTP request:
```http
POST /machine/device_power/off?dev_one&dev_two
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.off",
    "params": {
        "dev_one": null,
        "dev_two": null
    },
    "id": 4564
}
```
An object containing power state for each requested device:
```json
{
    "green_led": "off",
    "printer": "off"
}
```
### WLED APIs
The APIs for WLED are available when the `[wled]` component has been configured. For lower-level control of wled consider using the WLED [JOSN API](https://kno.wled.ge/interfaces/json-api/) directly.

#### Get strips
HTTP request:
```http
GET /machine/wled/strips
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.strips",
    "id": 7123
}
```
Returns:

Strip information for all wled strips.
```json
{
    "result": {
        "strips": {
            "lights": {
                "strip": "lights",
                "status": "on",
                "chain_count": 79,
                "preset": -1,
                "brightness": 255,
                "intensity": -1,
                "speed": -1,
                "error": null
            },
            "desk": {
                "strip": "desk",
                "status": "on",
                "chain_count": 60,
                "preset": 8,
                "brightness": -1,
                "intensity": -1,
                "speed": -1,
                "error": null
            }
        }
    }
}
```

#### Get strip status
HTTP request:
```http
GET /machine/wled/status?strip1&strip2
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.status",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7124
}
```
Returns:

Strip information for requested strips.
```json
{
    "result": {
        "lights": {
            "strip": "lights",
            "status": "on",
            "chain_count": 79,
            "preset": -1,
            "brightness": 255,
            "intensity": -1,
            "speed": -1,
            "error": null
        },
        "desk": {
            "strip": "desk",
            "status": "on",
            "chain_count": 60,
            "preset": 8,
            "brightness": -1,
            "intensity": -1,
            "speed": -1,
            "error": null
        }
    }
}
```

#### Turn strip on
Turns the specified strips on to the initial colors or intial preset.

HTTP request:
```http
POST /machine/wled/on?strip1&strip2
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.on",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7125
}
```
Returns:

Strip information for requested strips.
```json
{
    "result": {
        "lights": {
            "strip": "lights",
            "status": "on",
            "chain_count": 79,
            "preset": -1,
            "brightness": 255,
            "intensity": -1,
            "speed": -1,
            "error": null
        },
        "desk": {
            "strip": "desk",
            "status": "on",
            "chain_count": 60,
            "preset": 8,
            "brightness": -1,
            "intensity": -1,
            "speed": -1,
            "error": null
        }
    }
}
```

#### Turn strip off
Turns off all specified strips.

HTTP request:
```http
POST /machine/wled/off?strip1&strip2
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.off",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7126
}
```
Returns:

The new state of the specified strips.
```json
{
    "result": {
        "lights": {
            "strip": "lights",
            "status": "off",
            "chain_count": 79,
            "preset": -1,
            "brightness": 255,
            "intensity": -1,
            "speed": -1,
            "error": null
        },
        "desk": {
            "strip": "desk",
            "status": "off",
            "chain_count": 60,
            "preset": 8,
            "brightness": -1,
            "intensity": -1,
            "speed": -1,
            "error": null
        }
    }
}
```

#### Toggle strip on/off state
Turns each strip off if it is on and on if it is off.

HTTP request:
```http
POST /machine/wled/off?strip1&strip2
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.toggle",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7127
}
```
Returns:

The new state of the specified strips.
```json
{
    "result": {
        "lights": {
            "strip": "lights",
            "status": "on",
            "chain_count": 79,
            "preset": -1,
            "brightness": 255,
            "intensity": -1,
            "speed": -1,
            "error": null
        },
        "desk": {
            "strip": "desk",
            "status": "off",
            "chain_count": 60,
            "preset": 8,
            "brightness": -1,
            "intensity": -1,
            "speed": -1,
            "error": null
        }
    }
}
```

#### Control individual strip state
Toggle, turn on, turn off, turn on with preset, turn on with brightness, or
turn on preset will some of brightness, intensity, and speed. Or simply set
some of brightness, intensity, and speed.

HTTP requests:

Turn strip `lights` off
```http
POST /machine/wled/strip?strip=lights&action=off
```

Turn strip `lights` on to the initial colors or intial preset.
```http
POST /machine/wled/strip?strip=lights&action=on
```

Turn strip `lights` on activating preset 3.
```http
POST /machine/wled/strip?strip=lights&action=on&preset=3
```

Turn strip `lights` on activating preset 3 while specifying speed and
intensity.
```http
POST /machine/wled/strip?strip=lights&action=on&preset=3&intensity=50&speed=255
```

Change strip `lights` brightness (if on) and speed (if a preset is active).
```http
POST /machine/wled/strip?strip=lights&action=control&brightness=99&speed=50
```

JSON-RPC request:

Returns information for the specified strip.
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.get_strip",
    "params": {
        "strip": "lights",
    },
    "id": 7128
}
```

Calls the action with the arguments for the specified strip.
```json
{
    "jsonrpc": "2.0",
    "method":"machine.wled.post_strip",
    "params": {
        "strip": "lights",
        "action": "on",
        "preset": 1,
        "brightness": 255,
        "intensity": 255,
        "speed": 255
    },
    "id": 7129
}
```
!!! note
    The `action` argument may be `on`, `off`, `toggle` or `control`. Any
    other value will result in an error.

The `intensity` and `speed` arguments are only used if a preset is active.
Permitted ranges are 1-255 for `brightness` and 0-255 for `intensity` and
`speed`. When action is `on` a `preset` with some or all of `brightness`,
`intensity` and `speed` may also be specified. If the action `control` is used
one or all of `brightness`, `intensity`, and `speed` must be specified.

Returns:

State of the strip.
```json
{
    "result": {
        "lights": {
            "strip": "lights",
            "status": "on",
            "chain_count": 79,
            "preset": 1,
            "brightness": 50,
            "intensity": 255,
            "speed": 255,
            "error": null
        }
    }
}
```

### Sensor APIs
The APIs below are available when the `[sensor]` component has been configured.

#### Get Sensor List
HTTP request:
```http
GET /server/sensors/list
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.sensors.list",
    "id": 5646
}
```
Returns:

An array of objects containing info for each configured sensor.
```json
{
    "sensors": {
        "sensor1": {
            "id": "sensor1",
            "friendly_name": "Sensor 1",
            "type": "mqtt",
            "values": {
                "value1": 0,
                "value2": 119.8
            }
        }
    }
}
```

#### Get Sensor Information
Returns the status for a single configured sensor.

HTTP request:
```http
GET /server/sensors/info?sensor=sensor1
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.sensors.info",
    "params": {
        "sensor": "sensor1"
    },
    "id": 4564
}
```
Returns:

An object containing sensor information for the requested sensor:
```json
{
    "id": "sensor1",
    "friendly_name": "Sensor 1",
    "type": "mqtt",
    "values": {
        "value1": 0.0,
        "value2": 120.0
    }
}
```

#### Get Sensor Measurements
Returns all recorded measurements for a configured sensor.

HTTP request:
```http
GET /server/sensors/measurements?sensor=sensor1
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.sensors.measurements",
    "params": {
        "sensor": "sensor1"
    },
    "id": 4564
}
```
Returns:

An object containing all recorded measurements for the requested sensor:
```json
{
    "sensor1": {
        "value1": [
            3.1,
            3.2,
            3.0
        ],
        "value2": [
            120.0,
            120.0,
            119.9
        ]
    }
}
```

#### Get Batch Sensor Measurements
Returns recorded measurements for all sensors.

HTTP request:
```http
GET /server/sensors/measurements
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.sensors.measurements",
    "id": 4564
}
```
Returns:

An object containing all measurements for every configured sensor:
```json
{
    "sensor1": {
        "value1": [
            3.1,
            3.2,
            3.0
        ],
        "value2": [
            120.0,
            120.0,
            119.9
        ]
    },
    "sensor2": {
        "value_a": [
            1,
            1,
            0
        ]
    }
}
```

### Spoolman APIs
The following APIs are available to interact with the Spoolman integration:

#### Set active spool
Set the ID of the spool that Moonraker should report usage to Spoolman of.

HTTP request:
```http
POST /server/spoolman/spool_id
Content-Type: application/json

{
    "spool_id": 1
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.post_spool_id",
    "params": {
        "spool_id": 1
    },
    "id": 4654
}
```

Returns:

The id of the now active spool:

```json
{
    "spool_id": 1
}
```

!!! note
    Send an empty object, `{}`, to un-set the spool ID and stop any reporting.
    The response `spool_id` will then be set to *null*

#### Get active spool
Retrieve the ID of the spool to which Moonraker reports usage for Spoolman.

HTTP request:
```http
GET /server/spoolman/spool_id
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.get_spool_id",
    "id": 4654
}
```

Returns:

The id of the active spool:

```json
{
    "spool_id": 1
}
```

!!! note
    The `spool_id` can be *null* if there is no active spool.

#### Proxy

Moonraker supplies a proxy endpoint where you have full access to the Spoolman
API without having to configure the endpoint yourself.

See Spoolman's [OpenAPI Description](https://donkie.github.io/Spoolman/) for
detailed information about it's API.

HTTP request:
```http
POST /server/spoolman/proxy
Content-Type: application/json

{
    "request_method": "POST",
    "path": "/v1/spool",
    "query": "a=1&b=4",
    "body": {
        "filament_id": 1
    }
}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.proxy",
    "params": {
        "request_method": "POST",
        "path": "/v1/spool",
        "query": "a=1&b=4",
        "body": {
            "filament_id": 1
        }
    },
    "id": 4654
}
```

The following parameters are available. `request_method` and `path` are required, the rest are optional.

- `request_method`: The HTTP request method, e.g. `GET`, `POST`, `DELETE`, etc.
- `path`: The endpoint, including API version, e.g. `/v1/filament`.
- `query`: The query part of the URL, e.g. `filament_material=PLA&vendor_name=Prima`.
- `body`: The request body for the request.

Returns:

The json response from the Spoolman server.

### OctoPrint API emulation
Partial support of OctoPrint API is implemented with the purpose of
allowing uploading of sliced prints to a moonraker instance.
Currently we support Slic3r derivatives and Cura with Cura-OctoPrint.

#### Version information
HTTP request:
```http
GET /api/version
```
JSON-RPC request: Not Available

Returns:

An object containing simulated OctoPrint version information
```json
{
    "server": "1.5.0",
    "api": "0.1",
    "text": "OctoPrint (Moonraker v0.3.1-12)"
}
```

#### Server status
HTTP request:
```http
GET /api/server
```
JSON-RPC request: Not Available

Returns:

An object containing simulated OctoPrint server status
```json
{
    "server": "1.5.0",
    "safemode": "settings"
}
```

#### Login verification & User information
HTTP request:
```http
GET /api/login
```
JSON-RPC request: Not Available

Returns:

An object containing stubbed OctoPrint login/user verification
```json
{
    "_is_external_client": false,
    "_login_mechanism": "apikey",
    "name": "_api",
    "active": true,
    "user": true,
    "admin": true,
    "apikey": null,
    "permissions": [],
    "groups": ["admins", "users"],
}
```

#### Get settings
HTTP request:
```http
GET /api/settings
```
JSON-RPC request: Not Available

Returns:

An object containing stubbed OctoPrint settings.
The webcam route is hardcoded to Fluidd/Mainsail default path.
We say we have the UFP plugin installed so that Cura-OctoPrint will
upload in the preferred UFP format.
```json
{
    "plugins": {
        "UltimakerFormatPackage": {
            "align_inline_thumbnail": false,
            "inline_thumbnail": false,
            "inline_thumbnail_align_value": "left",
            "inline_thumbnail_scale_value": "50",
            "installed": true,
            "installed_version": "0.2.2",
            "scale_inline_thumbnail": false,
            "state_panel_thumbnail": true
        }
    },
    "feature": {
        "sdSupport": false,
        "temperatureGraph": false
    },
    "webcam": {
        "flipH": false,
        "flipV": false,
        "rotate90": false,
        "streamUrl": "/webcam/?action=stream",
        "webcamEnabled": true
    }
}
```

#### OctoPrint File Upload
HTTP request:
```http
POST /api/files/local
```
JSON-RPC request: Not Available

Alias for Moonraker's [file upload API](#file-upload).

#### Get Job status
HTTP request:
```http
GET /api/job
```
JSON-RPC request: Not Available

Returns:

An object containing stubbed OctoPrint Job status
```json
{
    "job": {
        "file": {"name": null},
        "estimatedPrintTime": null,
        "filament": {"length": null},
        "user": null
    },
    "progress": {
        "completion": null,
        "filepos": null,
        "printTime": null,
        "printTimeLeft": null,
        "printTimeOrigin": null
    },
    "state": "Offline"
}
```

#### Get Printer status
HTTP request:
```http
GET /api/printer
```
JSON-RPC request: Not Available

Returns:

An object containing OctoPrint Printer status
```json
{
    "temperature": {
        "tool0": {
            "actual": 22.25,
            "offset": 0,
            "target": 0
        },
        "bed": {
            "actual": 22.25,
            "offset": 0,
            "target": 0
        }, ...<additional heaters>
    },
    "state": {
        "text": "state",
        "flags": {
            "operational": true,
            "paused": false,
            "printing": false,
            "cancelling": false,
            "pausing": false,
            "error": false,
            "ready": false,
            "closedOrError": false
        }
    }
}
```

#### Send GCode command
HTTP request:
```http
POST /api/printer/command
Content-Type: application/json

{
    "commands": ["G28"]
}
```
JSON-RPC request: Not Available

Returns:

An empty JSON object
```json
{}
```

#### List Printer profiles
HTTP request:
```http
GET /api/printerprofiles
```
JSON-RPC request: Not Available

Returns:

An object containing simulates OctoPrint Printer profile
```json
{
    "profiles": {
        "_default": {
            "id": "_default",
            "name": "Default",
            "color": "default",
            "model": "Default",
            "default": true,
            "current": true,
            "heatedBed": true,
            "heatedChamber": false
        }
    }
}
```

### History APIs
The APIs below are available when the `[history]` component has been configured.

#### Get job list
HTTP request:
```http
GET /server/history/list?limit=50&start=50&since=1&before=5&order=asc
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.history.list",
    "params":{
        "limit": 50,
        "start": 10,
        "since": 464.54,
        "before": 1322.54,
        "order": "asc"
    },
    "id": 5656
}
```

All arguments are optional. Arguments are as follows:

- `start` Record number to start from (i.e. 10 would start at the 10th print)
- `limit` Maximum Number of prints to return (default: 50)
- `before` All jobs before this UNIX timestamp
- `since` All jobs after this UNIX timestamp
- `order` Define return order `asc` or `desc` (default)

Returns:

An array of requested historical jobs:
```json
{
    "count": 1,
    "jobs": [
        {
            "job_id": "000001",
            "exists": true,
            "end_time": 1615764265.6493807,
            "filament_used": 7.83,
            "filename": "test/history_test.gcode",
            "metadata": {
                // Object containing metadata at time of job
            },
            "print_duration": 18.37201827496756,
            "status": "completed",
            "start_time": 1615764496.622146,
            "total_duration": 18.37201827496756
        },
    ]
}
```

#### Get job totals
HTTP request:
```http
GET /server/history/totals
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.history.totals",
    "id": 5656
}
```

Returns:

An object containing the following total job statistics:
```json
{
    "job_totals": {
        "total_jobs": 3,
        "total_time": 11748.077333278954,
        "total_print_time": 11348.794790096988,
        "total_filament_used": 11615.718840001999,
        "longest_job": 11665.191012736992,
        "longest_print": 11348.794790096988
    }
}
```

#### Reset totals
Resets the persistent "job totals" to zero.

HTTP request:
```http
POST /server/history/reset_totals
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.history.reset_totals",
    "id": 5534
}

Returns:

The totals prior to the reset:

```json
{
    "last_totals": {
        "total_jobs": 3,
        "total_time": 11748.077333278954,
        "total_print_time": 11348.794790096988,
        "total_filament_used": 11615.718840001999,
        "longest_job": 11665.191012736992,
        "longest_print": 11348.794790096988
    }
}
```

#### Get a single job
HTTP request:
```http
GET /server/history/job?uid=<id>
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.history.get_job",
    "params":{"uid": "{uid}"},
    "id": 4564
}
```
Returns:

Data associated with the job ID in the following format:
```json
{
    "job": {
        "job_id": "000001",
        "exists": true,
        "end_time": 1615764265.6493807,
        "filament_used": 7.83,
        "filename": "test/history_test.gcode",
        "metadata": {
            // Object containing metadata at time of job
        },
        "print_duration": 18.37201827496756,
        "status": "completed",
        "start_time": 1615764496.622146,
        "total_duration": 18.37201827496756
    }
}
```

#### Delete job
HTTP request:
```http
DELETE /server/history/job?uid=<id>
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.history.delete_job",
    "params":{
        "uid": "{uid}"
    },
    "id": 5534
}
```
!!! tip
    It is possible to replace the `uid` argument with `all=true`
    to delete all jobs in the history database.

Returns:

An array of deleted job ids
```json
[
    "000000",
    "000001",
]
```

### MQTT APIs

The following API is available when `[mqtt]` has been configured.

!!! Note
    These requests are not available over the `mqtt` transport as they
    are redundant.  MQTT clients can publish and subscribe to
    topics directly.

#### Publish a topic

HTTP request:
```http
POST /server/mqtt/publish
Content-Type: application/json

{
    "topic": "home/test/pub",
    "payload": "hello",
    "qos": 0,
    "retain": false,
    "timeout": 5
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.mqtt.publish",
    "params":{
        "topic": "home/test/pub",
        "payload": "hello",
        "qos": 0,
        "retain": false,
        "timeout": 5
    },
    "id": 4564
}
```
Only the `topic` parameter is required.  Below is an explanation for
each parameter:

- `topic`: The topic to publish.
- `payload`: Payload to send with the topic.  May be a boolean, float,
  integer, string, object, or array. All values are converted to strings prior
  to publishing.  Objects and Arrays are JSON encoded.  If omitted an empty
  payload is sent.
- `qos`: QOS level to use when publishing the topic.  Must be an integer value
  from 0 to 2.  If omitted the system configured default is used.
- `retain`: If set to `true` the MQTT broker will retain the payload of this
  request.  Note that only the mostly recently tagged payload is retained.
  When other clients first subscribe to the topic they immediately receive the
  retained message.  The default is `false`.
- `timeout`: A float value in seconds.  By default requests with QoS levels of
  1 or 2 will block until the Broker acknowledges confirmation.  This option
  applies a timeout to the request, returning a 504 error if the timeout is
  exceeded. Note that the topic will still be published if the QoS level is 1
  or 2.

!!! tip
    To clear a retained value of a topic, publish the topic with an empty
    payload and `retain` set to `true`.

Returns:

The published topic:
```json
{
    "topic": "home/test/pub"
}
```

#### Subscribe to a topic


HTTP request:
```http
POST /server/mqtt/subscribe
Content-Type: application/json

{
    "topic": "home/test/sub",
    "qos": 0,
    "timeout": 5
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.mqtt.subscribe",
    "params":{
        "topic": "home/test/sub",
        "qos": 0,
        "timeout": 5
    },
    "id": 4564
}
```

Only the `topic` parameter is required.  Below is an explanation for
each paramater:

- `topic`: The topic to subscribe.  Note that wildcards may not be used.
- `qos`: QOS level to use when subscribing to the topic.  Must be an integer
  value from 0 to 2.  If omitted the system configured default is used.
- `timeout`: A float value in seconds.  By default requests will block
  indefinitely until a response is received. This option applies a timeout to
  the request, returning a 504 error if the timeout is exceeded.  The
  subscription will be removed after a timeout.

!!! note
    If the topic was previously published with a retained payload this request
    will return with the retained value.

Returns:

The subscribed topic and its payload:
```json
{
    "topic": "home/test/pub",
    "payload": "test"
}
```
If the payload is json encodable it will be returned as an object or array.
Otherwise it will be a string.

### Extension APIs

Moonraker currently has limited support for 3rd party extensions.  These
extensions must create a websocket connect and [identify](#identify-connection)
themselves as an `agent`.  Agents may host their own JSON-RPC methods
that other clients may call.  Agents may also emit events that are
broadcast to all other websocket connections.

#### List Extensions

Returns a list of all available extensions.  Currently Moonraker can only
be officially extended through connected `agents`.

HTTP request:
```http
GET /server/extensions/list
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.extensions.list",
    "id": 4564
}
```

Returns:

A list of connected agents, where each item is an object containing the
agent's identity:

```json
{
    "agents": [
        {
            "name": "moonagent",
            "version": "0.0.1",
            "type": "agent",
            "url": "https://github.com/arksine/moontest"
        }
    ]
}
```

#### Call an extension method

This API may be used to call a method on a connected agent.  The
request effectively relays a JSON-RPC request from a client
to the agent.

HTTP request:
```http
POST /server/extensions/request
Content-Type: application/json

{
    "agent": "moonagent",
    "method": "moontest.hello_world",
    "arguments": {"argone": true, "argtwo": 9000}
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"server.extensions.request",
    "params":{
        "agent": "moonagent",
        "method": "moontest.hello_world",
        "arguments": {"argone": true, "argtwo": 9000}
    },
    "id": 4564
}
```

Parameters:

- `agent`: The name of the agent.  This parameter is required.
- `method`: The name of the method to call on the agent.  Agents determine
  the method names they expose.  This parameter is required.
- `arguments`:  This parameter is optional, depending on if the method
  being called takes parameters.  This should be either an array of positional
  arguments or an object of keyword arguments.

Returns:

The result returned by the JSON-RPC call to the agent.  This can be any JSON
value as determined by the agent.

#### Send an agent event

!!! Note
    This API is only available to websocket connections that have
    identified themselves as an `agent` type.

HTTP Request: Not Available

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method":"connection.send_event",
    "params":{
        "event": "my_event",
        "data": {"my_arg": "optional data"}
    }
}
```

Parameters:
- `event`:  The name of the event.  This may be any name as
  determined by the agent, with the exception of the reserved
  names noted below.
- `data`: Optional supplemental data sent with the event.  This
  can be any JSON value.


!!! Note
    The `connected` and `disconnected` events are reserved for use
    by Moonraker.

Returns:

`ok` if an `id` was present in the request, otherwise no response is
returned.  Once received, Moonraker will broadcast this event via
the [agent event notification](#agent-events) to all other connections.

### Debug APIs

The APIs in this section are available when Moonraker the debug argument
(`-g`) has been supplied via the command line.  Some API may also depend
on Moonraker's configuration, ie: an optional component may choose to
register a debug API.

!!! Warning
    Debug APIs may expose security vulnerabilities.  They should only be
    enabled by developers on secured machines.

#### List Database Namespaces (debug)

Debug version of [List Namespaces](#list-namespaces). Return value includes
namespaces exlusively reserved for Moonraker. Only availble when Moonraker's
debug features are enabled.


HTTP request:
```http
GET /debug/database/list
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "debug.database.list",
    "id": 8694
}
```

#### Get Database Item (debug)

Debug version of [Get Database Item](#get-database-item).  Keys within
protected and forbidden namespaces are accessible. Only availble when
Moonraker's debug features are enabled.

!!! Warning
    Moonraker's forbidden namespaces include items such as user credentials.
    This endpoint should NOT be implemented in front ends directly.

HTTP request:
```http
GET /debug/database/item?namespace={namespace}&key={key}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "debug.database.get_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 5644
}
```

#### Add Database Item (debug)

Debug version of [Add Database Item](#add-database-item).  Keys within
protected and forbidden namespaces may be added. Only availble when
Moonraker's debug features are enabled.

!!! Warning
    This endpoint should be used for testing/debugging purposes only.
    Modifying protected namespaces outside of Moonraker can result in
    broken functionality and is not supported for production environments.
    Issues opened with reports/queries related to this endpoint will be
    redirected to this documentation and closed.

```http
POST /debug/database/item
Content-Type: application/json

{
    "namespace": "my_client",
    "key": "settings.some_count",
    "value": 100
}
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "debug.database.post_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}",
        "value": 100
    },
    "id": 4654
}
```

#### Delete Database Item (debug)

Debug version of [Delete Database Item](#delete-database-item).  Keys within
protected and forbidden namespaces may be removed. Only availble when
Moonraker's debug features are enabled.

!!! Warning
    This endpoint should be used for testing/debugging purposes only.
    Modifying protected namespaces outside of Moonraker can result in
    broken functionality and is not supported for production environments.
    Issues opened with reports/queries related to this endpoint will be
    redirected to this documentation and closed.

HTTP request:
```http
DELETE /debug/database/item?namespace={namespace}&key={key}
```

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "debug.database.delete_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 4654
}
```

#### Test a notifier (debug)

You can trigger a notifier manually using this endpoint.

HTTP request:
```http
POST /debug/notifiers/test?name=notifier_name
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "debug.notifiers.test",
    "params": {
        "name": "notifier_name"
    },
    "id": 4654
}
```

Parameters:

- `name`: The name of the notifier to test.

Returns: Test results in the following format

```json
{
    "status": "success",
    "stats": {
        "print_duration": 0.0,
        "total_duration": 0.0,
        "filament_used": 0.0,
        "filename": "notifier_test.gcode",
        "state": "standby",
        "message": ""
    }
}
```

### Websocket notifications
Printer generated events are sent over the websocket as JSON-RPC 2.0
notifications.  These notifications are sent to all connected clients
in the following format:
```json
{
    "jsonrpc": "2.0",
    "method": "{event method name}"
}
```
OR
```json
{
    "jsonrpc": "2.0",
    "method": "{event method name}",
    "params": [<event parameter>]
}
```

If a notification has parameters,  the `params` value will always be
wrapped in an array as directed by the JSON-RPC standard.  Currently
all notifications available are broadcast with either no parameters
or a single parameter.

#### Gcode Response
All of Klippy's gcode responses are forwarded over the websocket.  They arrive
as a "gcode_response" notification:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_gcode_response",
    "params": ["response message"]
}
```

#### Subscriptions
Status Subscriptions arrive as a "notify_status_update" notification:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_status_update",
    "params": [{<status object>}, <eventtime>]
}
```
The structure of the `status object` is identical to the structure that is
returned from an [object query's](#query-printer-object-status)
`status` field.

The `eventtime` is a timestamp generated by Klipper when
the update was originally pushed.  This timestamp is a float value,
relative to Klipper's monotonic clock.

#### Klippy Ready
Notify clients when Klippy has reported a ready state
```json
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_ready"
}
```

#### Klippy Shutdown
Notify clients when Klippy has reported a shutdown state
```json
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_shutdown"
}
```

#### Klippy Disconnected
Notify clients when Moonraker's connection to Klippy has terminated
```json
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_disconnected"
}
```

#### File List Changed
When a client makes a change to a file or directory in a registered
`root` (via upload, delete, move, etc) a notification is broadcast
to alert all connected clients of the change:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_filelist_changed",
    "params": [
        {
            "action": "{action}",
            "item": {
                "path": "{file or directory path relative to root}",
                "root": "{root}",
                "size": 46458,
                "modified": 545465,
                "permissions": "rw"
            },
            "source_item": {
                "path": "{file or directory path relative to root}",
                "root": "{root_name}"
            }
        }
    ]
}
```

!!! Note
    The `source_item` field is only present for `move_file` and
    `move_dir` actions.

The `action` field will be set to one of the following values:

- `create_file`
- `create_dir`
- `delete_file`
- `delete_dir`
- `move_file`
- `move_dir`
- `modify_file`
- `root_update`

Most of the above actions are self explanatory.  The `root_update`
notification is sent when a `root` folder has changed its location.
This should be a rare event as folders are now managed in using the
data folder structure.

Notifications are bundled where applicable.  For example, when a
directory containing children is deleted a single `delete_dir` notification
is pushed.  Likewise, when a directory is moved or copied, a single
`move_dir` or `create_dir` notification is pushed.  Children that are
moved, copied, or deleted as a result of a parent's action will
not receive individual notifications.

#### Update Manager Response
The update manager will send asynchronous messages to the client during an
update:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_update_response",
    "params": [
        {
            "application": "{app_name}",
            "proc_id": 446461,
            "message": "Update Response Message",
            "complete": false
        }
    ]
}
```
The fields reported in the response are as follows:

- The `application` field contains the name of application currently being
  updated.  Generally this will be either "moonraker", "klipper", "system",
  or "client".
- The `proc_id` field contains a unique id associated with the current update
  process.  This id is generated for each update request.
- The `message` field contains an asynchronous message sent during the update
  process.
- The `complete` field is set to true on the final message sent during an
  update, indicating that the update completed successfully.  Otherwise it
  will be false.

#### Update Manager Refreshed
The update manager periodically auto refreshes the state of each application
it is tracking.  After an auto refresh has completed the following
notification is broadcast:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_update_refreshed",
    "params": [{update_info}]}
```
Where `update_info` is an object that matches the response from an
[update status](#get-update-status) request.

#### CPU Throttled
If the system supports throttled CPU monitoring Moonraker will send the
following notification when it detects a change to the current throttled
state:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_cpu_throttled",
    "params": [{throttled_state}]
}
```

Where `throttled_state` is an object that matches the `throttled_state` field
in the response from a [Moonraker process stats](#get-moonraker-process-stats)
request. It is possible for clients to receive this notification multiple times
if the system repeatedly transitions between an active and inactive throttled
condition.

#### Moonraker Process Statistic Update
Moonraker will emit the following notification each time it samples its
process statistics:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_proc_stat_update",
    "params": [{
        "moonraker_stats": {
            "time": 1615837812.0894408,
            "cpu_usage": 1.99,
            "memory": 23636,
            "mem_units": "kB"
        },
        "cpu_temp": 44.008,
        "network": {
            "lo": {
                "rx_bytes": 114555457,
                "tx_bytes": 114555457,
                "bandwidth": 2911.49
            },
            "wlan0": {
                "rx_bytes": 48773134,
                "tx_bytes": 115035939,
                "bandwidth": 3458.77
            }
        },
        "system_cpu_usage": {
            "cpu": 2.53,
            "cpu0": 3.03,
            "cpu1": 5.1,
            "cpu2": 1.02,
            "cpu3": 1
        },
        "websocket_connections": 2
    }]
}
```

As with the [proc_stats request](#get-moonraker-process-stats) the `cpu_temp`
field will be set to `null` if the host machine does not support retrieving CPU
temperatures at `/sys/class/thermal/thermal_zone0`.

#### History Changed
If the `[history]` module is enabled the following notification is sent when
a job is added or finished:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_history_changed",
    "params": [
        {
            "action": "added",
            "job": <job object>
        }
    ]
}
```
The `action` field may be `added` or `finished`. The `job` field contains
an object matches the one returned when requesting
[job data](#get-a-single-job).

#### Authorized User Created
If the `[authorization]` module is enabled the following notification is
sent when a new user is created:
```json
{
    "jsonrpc": "2.0",
    "method": "notify_user_created",
    "params": [
        {
            "username": "<username>"
        }
    ]
}
```

#### Authorized User Deleted
If the `[authorization]` module is enabled the following notification is
sent when an existing user is deleted.
```json
{
    "jsonrpc": "2.0",
    "method": "notify_user_deleted",
    "params": [
        {
            "username": "<username>"
        }
    ]
}
```

#### Authorized User Logged Out
If the `[authorization]` module is enabled the following notification is
sent when an existing user is logged out.
```json
{
    "jsonrpc": "2.0",
    "method": "notify_user_logged_out",
    "params": [
        {
            "username": "<username>"
        }
    ]
}
```

#### Service State Changed
Moonraker monitors the state of systemd services it is authorized to track.
When the state of a service changes the following notification is sent:

```json
{
    "jsonrpc": "2.0",
    "method": "notify_service_state_changed",
    "params": [
        {
            "klipper": {
                "active_state": "inactive",
                "sub_state": "dead"
            }
        }
    ]
}
```

The example above shows that the `klipper` service has changed to `inactive`.

#### Job Queue Changed
Moonraker will send a `job_queue_changed` notification when a change is
detected to the queue state or the queue itself:

```json
{
    "jsonrpc": "2.0",
    "method": "notify_job_queue_changed",
    "params": [
        {
            "action": "state_changed",
            "updated_queue": null,
            "queue_state": "paused"
        }
    ]
}
```

The object sent with the notification contains the following fields:

- `action`: The action taken to the queue which led to the notification.
  Will be a string set to one of the following values:
    - `state_changed`: The queue state has changed
    - `jobs_added`: One or more jobs were added to the queue
    - `jobs_removed`: One or more jobs were removed from the queue
    - `job_loaded`:  A job was popped off the queue and successfully started
- `updated_queue`:  If the queue itself is changed this will be a list
   containing each item in the queue.  If the queue has not changed this will
   be `null`.
- `queue_state`: The current queue state

#### Button Event
Moonraker `[button]` components may be configured to emit websocket
notifications.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_button_event",
    "params": [
        {
            "name": "my_button",
            "type": "gpio",
            "event": {
                "elapsed_time": 0.09323832602240145,
                "received_time": 698614.214597004,
                "render_time": 698614.214728513,
                "pressed": false
            },
            "aux": null
        }
    ]
}
```

The `params` array will always contain a single object with the following
fields:

- `name`: The name of the configured button
- `type`: The button type, currently this will always be `gpio`
- `event`: An object with details about the button event, containing the
  following fields:
    - `elapsed_time`:  The time elapsed (in seconds) since the last detected
      button event
    - `received_time`: The time the event was detected according to asyncio's
      monotonic clock.  Note that this is not in "unix time".
    - `render_time`: The time the template was rendered (began execution)
      according to asyncio's monotonic clock.  It is possible execution of
      an event may be delayed well beyond the `received_time`.
    - `pressed`: A boolean value to indicate if the button is currently pressed.
- `aux`: This is an optional field where the button may specify any json
  encodable value.  Clients may suggest a specific button configuration
  that includes details about the event.  If no aux parameter is specified
  in the configuration this will be a `null` value.

#### Announcement update event

Moonraker will emit the `notify_announcement_update` notification when
a announcement entries are added or removed:

```json
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_update",
    "params": [
        {
            "entries": [
                {
                    "entry_id": "arksine/moonlight/issue/3",
                    "url": "https://github.com/Arksine/moonlight/issues/3",
                    "title": "Test announcement 3",
                    "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
                    "priority": "normal",
                    "date": 1647459219,
                    "dismissed": false,
                    "date_dismissed": null,
                    "dismiss_wake": null,
                    "source": "moonlight",
                    "feed": "moonlight"
                },
                {
                    "entry_id": "arksine/moonlight/issue/2",
                    "url": "https://github.com/Arksine/moonlight/issues/2",
                    "title": "Announcement Test Two",
                    "description": "This is a high priority announcement. This line is included in the description.",
                    "priority": "high",
                    "date": 1646855579,
                    "dismissed": false,
                    "date_dismissed": null,
                    "dismiss_wake": null,
                    "source": "moonlight",
                    "feed": "moonlight"
                }
                {
                    "entry_id": "arksine/moonraker/issue/349",
                    "url": "https://github.com/Arksine/moonraker/issues/349",
                    "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
                    "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
                    "priority": "normal",
                    "date": 1643392406,
                    "dismissed": false,
                    "source": "moonlight",
                    "feed": "Moonraker"
                }
            ]
        }
    ]
}
```

The `params` array will contain an object with all current announcement entries.
This object is identical to that returned by the
[list announcements](#list-announcements) endpoint.

#### Announcement dismissed event
Moonraker will emit the `notify_announcement_dismissed` notification when
a dismissed announcement is detected:

```json
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_dismissed",
    "params": [
        {
            "entry_id": "arksine/moonlight/issue/3"
        }
    ]
}
```

The `params` array will contain an object with the `entry_id` of the dismissed
announcement.

#### Announcement wake event
Moonraker will emit the `notify_announcement_wake` notification when
a specified `wake_time` for a dismissed announcement has expired.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_wake",
    "params": [
        {
            "entry_id": "arksine/moonlight/issue/1"
        }
    ]
}
```

The `params` array will contain an object with the `entry_id` of the
announcement that is no longer dismissed.

#### Sudo alert event
Moonraker will emit the `notify_sudo_alert` notification when
a component has requested sudo access.  The event is also emitted
when a sudo request has been granted.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_sudo_alert",
    "params": [
        {
            "sudo_requested": true,
            "sudo_messages": [
                "Sudo password required to update Moonraker's systemd service."
            ]
        }
    ]
}
```

The `params` array contains an object with the following fields:

- `sudo_requested`:  Returns true if Moonraker is currently requesting
  sudo access.
- `request_messages`:  An array of strings, each string describing
  a pending sudo request.  The array will be empty if no sudo
  requests are pending.

#### Webcams changed event

Moonraker will emit the `notify_webcams_changed` event when a configured
webcam is added, removed, or updated.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_webcams_changed",
    "params": [
        {
            "webcams": [
                {
                    "name": "tc2",
                    "location": "printer",
                    "service": "mjpegstreamer",
                    "enabled": true,
                    "icon": "mdiWebcam",
                    "target_fps": 15,
                    "target_fps_idle": 5,
                    "stream_url": "http://printer.lan/webcam?action=stream",
                    "snapshot_url": "http://printer.lan/webcam?action=snapshot",
                    "flip_horizontal": false,
                    "flip_vertical": false,
                    "rotation": 0,
                    "aspect_ratio": "4:3",
                    "extra_data": {},
                    "source": "database"
                },
                {
                    "name": "TestCam",
                    "location": "printer",
                    "service": "mjpegstreamer",
                    "enabled": true,
                    "icon": "mdiWebcam",
                    "target_fps": 15,
                    "target_fps_idle": 5,
                    "stream_url": "/webcam/?action=stream",
                    "snapshot_url": "/webcam/?action=snapshot",
                    "flip_horizontal": false,
                    "flip_vertical": false,
                    "rotation": 0,
                    "aspect_ratio": "4:3",
                    "extra_data": {},
                    "source": "database"
                }
            ]
        }
    ]
}
```

The `webcams` field contans an array of objects like those returned by the
[list webcams](#list-webcams) API.

#### Spoolman active spool ID changed

Moonraker will emit the `notify_active_spool_set` event when the active spool
ID for the Spoolman integration has been changed.

See the [Spoolman API](#spoolman-apis) for more information.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_active_spool_set",
    "params": [
        {
            "spool_id": 1
        }
    ]
}
```

#### Agent Events
Moonraker will emit the `notify_agent_event` notification when it
an agent event is received.

```json
{
    "jsonrpc": "2.0",
    "method": "notify_agent_event",
    "params": [
        {
            "agent": "moonagent",
            "event": "connected",
            "data": {
                "name": "moonagent",
                "version": "0.0.1",
                "type": "agent",
                "url": "https://github.com/arksine/moontest"
            }
        }
    ]
}
```

When an agent connects, all connections will receive a `connected` event
for that agent, with its identity info in the `data` field.  When an agent
disconnects clients will receive a `disconnected` event with the data field
omitted.  All other events are determined by the agent, where each event may
or may not include optional `data`.

#### Sensor Events

Moonraker will emit a `sensors:sensor_update` notification when a measurement
from at least one monitored sensor changes.

```json
{
    "jsonrpc": "2.0",
    "method": "sensors:sensor_update",
    "params": [
        {
            "sensor1": {
                "humidity": 28.9,
                "temperature": 22.4
            }
        }
    ]
}
```

When a sensor reading changes, all connections will receive a
`sensors:sensor_update` event where the params contains a data struct
with the sensor id as the key and the sensors letest measurements as value
struct.

### Appendix

#### Websocket setup
The websocket is located at `ws://host:port/websocket`, for example:
```javascript
var s = new WebSocket("ws://" + location.host + "/websocket");
```

!!! tip
    A client using API Key authorization may request a
    [oneshot token](#generate-a-oneshot-token), applying the result to the
    websocket request's query string:

```http
ws://host:port/websocket?token={32 character base32 string}
```

The following startup sequence is recommended for clients which make use of
the websocket:

1. Attempt to connect to `/websocket` until successful using a timer-like
   mechanism
2. Once connected, query `/server/info` (or `server.info`) for the ready
   status.
      - If the response returns an error (such as 404) then either the client
        is not authorized or Moonraker is not running.  Direct the user to
        SSH into the machine and check `/tmp/moonraker.log`.
      - If the response returns success, check the result's `klippy_state`
        field:
        - `klippy_state == "ready"`: you may proceed to request status of
          printer objects make subscriptions, get the file list, etc.
        - `klippy_state == "error"`:  Klippy has experienced an error
          starting up
        - `klippy_state == "shutdown"`: Klippy is in a shutdown state.
        - `klippy_state == "startup"`: re-request `/server/info` in 2 seconds.
             - If  `error` or `shutdown` is detected it might be wise to prompt
               the user. You can get a description from the `state_message`
               field of a `/printer/info` request.
3. Repeat step 2 until Klipper reports ready.
4. Clients should watch for the `notify_klippy_disconnected` event.  If
   received then Klippy has either been stopped or restarted.  In this
   state the client should repeat the steps above to determine when
   klippy is ready.

#### Basic Print Status
An advanced client will likely use subscriptions and notifications
to interact with Moonraker, however simple clients such as home automation
software and embedded devices (ie: ESP32) may only wish to monitor the
status of a print.  Below is a high level walkthrough for receiving print state
via polling.

- Set up a timer to poll at the desired interval.  Depending on your use
  case, 1 to 2 seconds is recommended.
- On each cycle, issue the following request:

        GET http://host/printer/objects/query?webhooks&virtual_sdcard&print_stats

    Or via JSON-RPC 2.0:

        {
            "jsonrpc": "2.0",
            "method": "printer.objects.query",
            "params": {
                "objects": {
                    "webhooks": null,
                    "virtual_sdcard": null,
                    "print_stats": null
                }
            },
            "id": 5664
        }

- If the request returns an error or the returned `result.status` is an empty
  object, then this is an indication that Klippy either experienced an error or
  it is not properly configured.  Each queried object should be available in
  `result.status`.  The client should check to make sure that all objects are
  received before proceeding.
- Inspect `webhooks.ready`.  If the value is not `ready` the printer
  is not available.  `webhooks.message` contains a message pertaining
  to the current state.
- If the printer is ready, inspect `print_stats.state`.  It may be one
  of the following values:
      - `standby`: No print in progress
      - `printing`:  The printer is currently printing
      - `paused`:  A print in progress has been paused
      - `error`:  The print exited with an error.  `print_stats.message`
        contains a related error message
      - `complete`:  The last print has completed
- If `print_stats.state` is not `standby` then `print_stats.filename`
  will report the name of the currently loaded file.
- `print_stats.filename` can be used to fetch file metadata.  It
  is only necessary to fetch metadata once per print.

        GET http://host/server/files/metadata?filename=<filename>

    Or via JSON-RPC 2.0:

        {
            "jsonrpc": "2.0",
            "method": "server.files.metadata",
            "params": {
                "filename": "{filename}"
            },
            "id": 5643
        }

    If metadata extraction failed then this request will return an error.
    Some metadata fields are only populated for specific slicers, and
    unsupported slicers will only return the size and modified date.

- There are multiple ways to calculate the ETA, this example will use
  file progress, as it is possible calculate the ETA with or without
  metadata.
    - If `metadata.estimated_time` is available, the eta calculation can
      be done as:

            // assume "result" is the response from the status query
            let vsd = result.status.virtual_sdcard;
            let prog_time = vsd.progress * metadata.estimated_time;
            let eta = metadata.estimated_time - prog_time

        Alternatively, one can simply subtract the print duration from
        the estimated time:

            // assume "result" is the response from the status query
            let pstats = result.status.print_status;
            let eta = metadata.estimated_time - pstats.print_duration;
            if (eta < 0)
            eta = 0;

    - If no metadata is available, print duration and progress can be used to
      calculate the ETA:

            // assume "result" is the response from the status query
            let vsd = result.status.virtual_sdcard;
            let pstats = result.status.print_stats;
            let total_time = pstats.print_duration / vsd.progress;
            let eta = total_time - pstats.print_duration;

- It is possible to query additional objects if a client wishes to display
  more information (ie: temperatures).  See the
  [Printer Objects](printer_objects.md) documentation for details.

#### Bed Mesh Coordinates
The [Bed Mesh](printer_objects.md#bed_mesh) printer object may be used
to generate three dimensional coordinates of a probed area (or mesh).
Below is an example (in javascript) of how to transform the data received
from a bed_mesh object query into an array of 3D coordinates.
```javascript
// assume that we have executed an object query for bed_mesh and have the
// result.  This example generates 3D coordinates for the probed matrix,
// however it would work with the mesh matrix as well
function process_mesh(result) {
  let bed_mesh = result.status.bed_mesh
  let matrix = bed_mesh.probed_matrix;
  if (!(matrix instanceof Array) ||  matrix.length < 3 ||
      !(matrix[0] instanceof Array) || matrix[0].length < 3)
      // make sure that the matrix is valid
      return;
  let coordinates = [];
  let x_distance = (bed_mesh.mesh_max[0] - bed_mesh.mesh_min[0]) /
    (matrix[0].length - 1);
  let y_distance = (bed_mesh.mesh_max[1] - bed_mesh.mesh_min[1]) /
    (matrix.length - 1);
  let x_idx = 0;
  let y_idx = 0;
  for (const x_axis of matrix) {
    x_idx = 0;
    let y_coord = bed_mesh.mesh_min[1] + (y_idx * y_distance);
    for (const z_coord of x_axis) {
      let x_coord = bed_mesh.mesh_min[0] + (x_idx * x_distance);
      x_idx++;
      coordinates.push([x_coord, y_coord, z_coord]);
    }
    y_idx++;
  }
}
// Use the array of coordinates visualize the probed area
// or mesh..
```

#### Converting to Unix Time
Some of Moonraker's APIs return a date represented in Unix time.
Most languages have functionality built in to convert Unix
time to a workable object or string.  For example, in JavaScript
one might do something like the following:
```javascript
for (let resp of result.gcode_store) {
  let date = new Date(resp.time * 1000);
  // Do something with date and resp.message ...
}
```

#### Announcements

Moonraker announcements are effectively push notifications that
can be used to notify users of important information related the
development and status of software in the Klipper ecosystem.  This
section will provide an overview of how the announcement system
works, how to set up a dev environment, and provide recommendations
on client implementation.

##### How announcements work

Moonraker announcements are GitHub issues tagged with the `announcement`
label.  GitHub repos may registered with
[moonlight](https://github.com/arksine/moonlight), which is responsible
for generating RSS feeds from GitHub issues using GitHub's REST API. These
RSS feeds are hosted on GitHub Pages, for example Moonraker's feed may be found
[here](https://arksine.github.io/moonlight/assets/moonraker.xml). By
centralizing GitHub API queries in `moonlight` we are able to poll multiple
repos without running into API rate limit issues. Moonlight has has a workflow
that checks all registered repos for new announcements every 30 minutes.  In
theory it would be able to check for announcements in up to 500 repos before
exceeding GitHub's API rate limit.

Moonraker's `[announcements]` component will always check the `klipper` and
`moonraker` RSS feeds.  It is possible to configure additional RSS feeds by
adding them to the `subscriptions` option.  The component will poll configured
feeds every 30 minutes, resulting in maximum of 1 hour for new announcements
to reach all users.

When new issues are tagged with `announcement` these entries will be parsed
and added to the RSS feeds.  When the issue is closed they will be removed from
the corresponding feed.  Moonlight will fetch up to 20 announcements for each
feed, if a repo goes over this limit older announcements will be removed.

!!! Note
    It is also possible for Moonraker to generate announcements itself.  For
    example, if a Moonraker component needs user feedback it may generate an
    announcement and notify all connected clients.   From a client's
    perspective there is no need to treat announcements differently than
    any other announcement.

##### Setting up the dev environment

Moonraker provides configuration to parse announcements from a local folder
so that it is possible to manually add and remove entries, allowing client
developers to perform integration tests:

```ini
# moonraker.conf

[announcements]
dev_mode: True
```

With `dev_mode` enabled, Moonraker will look for`moonraker.xml` and
`klipper.xml` in the following folder:
```shell
~/moonraker/.devel/announcement_xml
```

If moonraker is not installed in the home folder then substitute `~`
for the parent folder location.  This folder is in a hardcoded location
to so as not to expose users to vulnerabilities associated with parsing XML.

It is possible to configure Moonraker to search for your own feeds:

```ini
# moonraker.conf

[announcements]
subscription:
  my_project
dev_mode: True
```

The above configuration would look for `my_project.xml` in addition to
`klipper.xml` and `moonraker.xml`.  The developer may manually create
the xml feeds or they may clone `moonlight` and leverage its script
to generate a feed from issues created on their test repo.  When local
feeds have been modified one may call the [update announcements API](#update-announcements) to have Moonraker fetch the updates and add/remove
entries.

##### RSS file structure

Moonlight generates RSS feeds in XML format.  Below is an example generated
from moonlight's own issue tracker:

```xml
<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0" xmlns:moonlight="https://arksine.github.io/moonlight">
    <channel>
        <title>arksine/moonlight</title>
        <link>https://github.com/Arksine/moonlight</link>
        <description>RSS Announcements for Moonraker</description>
        <pubDate>Tue, 22 Mar 2022 23:19:04 GMT</pubDate>
        <moonlight:configHash>f2912192bf0d09cf18d8b8af22b2d3501627043e5afa3ebff0e45e4794937901</moonlight:configHash>
        <item>
            <title>Test announcement 3</title>
            <link>https://github.com/Arksine/moonlight/issues/3</link>
            <description>Test Description [with a link](https://moonraker.readthedocs.io).</description>
            <pubDate>Wed, 16 Mar 2022 19:33:39 GMT</pubDate>
            <category>normal</category>
            <guid>arksine/moonlight/issue/3</guid>
        </item>
        <item>
            <title>Announcement Test Two</title>
            <link>https://github.com/Arksine/moonlight/issues/2</link>
            <description>This is a high priority announcement. This line is included in the description.</description>
            <pubDate>Wed, 09 Mar 2022 19:52:59 GMT</pubDate>
            <category>high</category>
            <guid>arksine/moonlight/issue/2</guid>
        </item>
        <item>
            <title>Announcement Test One</title>
            <link>https://github.com/Arksine/moonlight/issues/1</link>
            <description>This is the description.  Anything here should appear in the announcement, up to 512 characters.</description>
            <pubDate>Wed, 09 Mar 2022 19:37:58 GMT</pubDate>
            <category>normal</category>
            <guid>arksine/moonlight/issue/1</guid>
        </item>
    </channel>
</rss>
```

Each xml file may contain only one `<rss>` element, and each `<rss>` element
may contain only one channel.  All items must be present aside from
`moonlight:configHash`, which is used by the workflow to detect changes to
moonlight's configuration.  Most elements are self explanatory, developers will
be most interested in adding and removing `<item>` elements, as these are
the basis for entries in Moonraker's announcement database.

##### Generating announcements from your own repo

As mentioned previously, its possible to clone moonlight and use its rss
script to generate announcements from issues in your repo:

```shell
cd ~
git clone https://github.com/arksine/moonlight
cd moonlight
virtualenv -p /usr/bin/python3 .venv
source .venv/bin/activate
pip install httpx[http2]
deactivate
```

To add your repo edit `~/moonlight/src/config.json`:
```json
{
    "moonraker": {
        "repo_owner": "Arksine",
        "repo_name": "moonraker",
        "description": "API Host For Klipper",
        "authorized_creators": ["Arksine"]
    },
    "klipper": {
        "repo_owner": "Klipper3d",
        "repo_name": "klipper",
        "description": "A 3D Printer Firmware",
        "authorized_creators": ["KevinOConnor"]
    },
    // Add your test repo info here.  It should contain
    // fields matching those in "moonraker" and "klipper"
    // shown above.
}
```

Once your repo is added, create one or more issues on your GitHub
repo tagged with the `announcement` label.  Add the `critical` label to
one if you wish to test high priority announcements.  You may need to
create these labels in your repo before they can be added.

Now we can use moonlight to generate the xml files:
```shell
cd ~/moonlight
source .venv/bin/activate
src/update_rss.py
deactivate
```

After the script has run it will generate the configured RSS feeds
and store them in `~/moonlight/assets`.  If using this method it may
be useful to create a symbolic link to it in Moonraker's devel folder:

```shell
cd ~/moonraker
mkdir .devel
cd .devel
ln -s ~/moonlight/assets announcement_xml
```

If you haven't done so, configure Moonraker to subscribe to your feed
and restart the Moonraker service.  Otherwise you may call the
[announcement update](#update-announcements) API to have Moonraker
parse the announcements from your test feed.


##### Implementation details and recommendations

When Moonraker detects a change to one or more feeds it will fire the
[announcement update](#announcement-update-event) notification.  It is also
possible to [query the API for announcements](#list-announcements).  Both
the notification and the API return a list of announcement entries, where
each entry is an object containing the following fields:

- `entry_id`: A unique ID derived for each entry.  Typically this is in the
  form of `{owner}/{repo}/issue/{issue number}`.
- `url`: The url to the full announcement.  This is generally a link to
  an issue on GitHub.
- `title`: Announcement title, will match the title of the issue on GitHub.
- `description`: The first paragraph of the announcement.  Anything over
  512 characters will be truncated.
- `priority`: Can be `normal` or `high`.  It is recommended that clients
  immediately alert the user when one or more `high` priority announcments
  are present.  Issued tagged with the `critical` label will be assigned
  a `high` priority.
- `date`:  The announcement creation date in unix time.
- `dismissed`: If set to `true` this announcement has been previously
  dismissed
- `date_dismissed`: The date the announcement was dismissed in unix time.
  If the announcement has not been dismissed this value is `null`.
- `dismiss_wake`: If the announcement was dismissed with a `wake_time`
  specified this is the time (in unix time) at which the `dismissed`
  state will revert.  If the announcement is not dismissed or dismissed
  indefinitely this value will be `null`.
- `source`: The source from which the announcement was generated.  Can
  be `moonlight` or `internal`.
- `feed`: The RSS feed for moonlight announcements.  For example, this
  could be `Moonraker` or `Klipper`.  If the announcement was generated
  internally this should match the name of the component that generated
  the announcement.

When a client first connects to Moonraker it is recommended that the
[list announcements](#list-announcements) API is called to retrieve
the current list of entries.  A client may then watch for the
[announcement update](#announcement-update-event) and
[announcement dismissed](#announcement-dismissed-event) notifications
and update the UI accordingly.

Client devs should decide how they want to present announcements to users.  They could be treated as any other notification, for example a client
may have a notification icon that shows the current number of unread
announcements.  Clients can mark an announcement as `read` by calling
the [dismiss announcement](#dismiss-an-announcement) API.  Any announcement
entry with `dismissed = true` should be considered read.

When a `high priority` announcement is detected it is recommended that
clients present the announcement in a format that is immediately visible
to the user.  That said, it may be wise to allow users to opt out of
this behavior via configuration.

!!! Note
    If an announcement is dismissed, closed, then reopened the
    `dismissed` flag will reset to false.  This is expected behavior
    as announcements are pruned from the database when they are no
    longer present in feeds.  It isn't valid for repo maintaners
    to re-open a closed announcement.  That said, its fine to close
    and re-open issues during development and testing using repos
    that are not yet registered with moonlight.
