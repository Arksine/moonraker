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
between mulitple API protocols results in an HTTP API that does not
completely adhere to the standard.

Moonraker is capable of parsing request arguments from the both the body
(either JSON or form-data depending on the `Content-Type` header) and from
the query string.  All arguments are grouped together in one data structure,
with body arguments taking precedence over query arguments.  Thus
if the same argument is supplied both in the body and in the
query string the body argument would be used. It is left up to the client
developer to decide exactly how they want to provide arguments, however
future API documention will make recommendations.  As of March 1st 2021
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
assing an object, `{foo: 21.5, bar: "hello"}` might look like:
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

The `test/client` folder includes a basic test interface with example usage for
most of the requests below.  It also includes a basic JSON-RPC implementation
that uses promises to return responses and errors (see json-rpc.js).

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
See [printer_objects.md](printer_objects.md) for details on the printer objects
available for query.

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

See [printer_objects.md](printer_objects.md) for details on the printer objects
available for subscription.

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
    "websocket_count": 2
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
    "id": 5616,
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
    "config": {
        "server": {
            "host": "0.0.0.0",
            "port": 7125,
            "klippy_uds_address": "/tmp/klippy_uds",
            "max_upload_size": 210,
            "enable_debug_logging": true,
            "database_path": "~/.moonraker_database",
            "config_path": "~/printer_config",
            "temperature_store_size": 100,
            "gcode_store_size": 50
        },
        "authorization": {
            "api_key_file": "~/.moonraker_api_key",
            "enabled": true,
            "cors_domains": "\nhttp://my.mainsail.xyz\nhttp://app.fluidd.xyz",
            "trusted_clients": "\n192.168.1.0/24"
        },
        "system_args": {},
        "history": {},
        "octoprint_compat": {},
        "update_manager": {
            "enable_auto_refresh": true,
            "distro": "debian",
            "enable_repo_debug": true,
            "client_repo": null
        },
        "update_manager static debian moonraker": {},
        "update_manager client mainsail": {
            "type": "web",
            "repo": "meteyou/mainsail",
            "path": "~/mainsail",
            "persistent_files": null
        },
        "update_manager client fluidd": {
            "type": "web",
            "repo": "cadriel/fluidd",
            "path": "~/fluidd",
            "persistent_files": null
        },
        "power green_led": {
            "type": "gpio",
            "locked_while_printing": false,
            "off_when_shutdown": false,
            "restart_klipper_when_powered": false,
            "pin": "gpiochip0/gpio26",
            "initial_state": false
        },
        "update_manager static debian klipper": {}
    }
}
```
#### Request Cached Temperature Data
HTTP request:
```http
GET /server/temperature_store
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.temperature_store",
    "id": 2313
}
```
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

#### Get Websocket ID
HTTP request: `Not Available`

JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.websocket.id",
    "id": 4656
}
```
Returns:

The connected websocket's unique identifer.
```json
{
    "websocket_id": 1730367696
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
Restarts a system service via `sudo systemctl restart {name}`. Currently
the `moonraker`, `klipper`, `MoonCord`, `KlipperScreen` and `webcamd`
services are supported.

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

`ok` when complete.  Note that if `moonraker` is chosen, the return
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
- `mem_units`: A string indentifying the units of the value in the
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

The `websocket_connections` field reports the number of active websockets
currently connected to moonraker.

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

#### Get gcode metadata
Get metadata for a specified gcode file.  If the file is located in
a subdirectory, then the file name should include the path relative to
the "gcodes" root.  For example, if the file is located at:
```
http://host.local/server/files/gcodes/my_sub_dir/my_print.gcode
```
Then the `{filename}` argument should be `my_sub_dir/my_print.gcode`.

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
!!! note
    The `print_start_time` and `job_id` fields are initialized to
    `null`.  They will be updated for each print job if the user has the
    `[history]` component configured

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
        "path": "gcodes/testdir",
        "root": "gcodes"
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
        "path": "gcodes/my_new_dir",
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
        "path": "gcodes/testdir",
        "root": "gcodes"
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
in *moving* the source directory into the destination directory.

HTTP request:
```http
POST /server/files/move?source=gcodes/my_file.gcode&dest=gcodes/subdir/my_file.gcode
```
JSON-RPC request:
```json
{
    "jsonrpc": "2.0",
    "method": "server.files.move",
    "params": {
        "source": "gcodes/my_file.gcode",
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
            "path": "test4/test3"
        },
        "source_item": {
            "path": "gcodes/test4/test3",
            "root": "gcodes"
        },
        "action": "move_dir"
    }
}
```

#### Copy a file or directory
Copies a file or directory from one location to another.  A successful copy has
the pre-requesites as a move with one exception, a copy may complete if the
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
        "path": "test4/Voron_v2_350_aferburner_Filament Cover_0.2mm_ABS.gcode"
    },
    "action": "create_file"
}
```

#### File download
Retreives file `filename` at root `root`.  The `filename` must include
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
  provides compatibility with Octoprint's legacy upload API.

JSON-RPC request: Not Available

Returns:  Information about the uploaded file.  Note that `print_started`
is only included when the supplied root is set to `gcodes`.
```json
{
    "item": {
        "path": "Lock Body Shim 1mm_0.2mm_FLEX_MK3S_2h30m.gcode",
        "root": "gcodes"
    },
    "print_started": false,
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
        "root": "gcodes"
    },
    "action": "delete_file"
}
```

#### Download klippy.log
HTTP request:
```http
GET /server/files/klippy.log
```
JSON-RPC request: Not Available

Returns:

The requested file

#### Download moonraker.log
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

!!! note
    For requests in which clients cannot modify headers it is acceptable
    to pass the JWT via the query string's `access_token` argument.
    Alternatively client developers may request a `oneshot_token` and
    send the result via the `token` query string argument.

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
    "password": "my_password"
}
```
JSON-RPC request: Not Available

Returns: An object the logged in username, auth token, refresh token,
and action summary:
```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNjU1LCAiZXhwIjogMTYxODg4MDQwMC40ODE2NTUsICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.QdieeEskrU0FrH7rXKuPDSZxscM54kV_vH60uJqdU9g",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY4MDAuNDgxNzUxNCwgImV4cCI6IDE2MjY2NTI4MDAuNDgxNzUxNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.btJF0LJfymInhGJQ2xvPwkp2dFUqwgcw4OA_wE-EcCM",
    "action": "user_logged_in"
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
    only be returned if the username and/or password is invalid.

#### Logout Current User
HTTP Request:
```http
POST /access/logout
```
JSON-RPC request: Not Available

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
JSON-RPC request: Not Available

Returns: An object containing the currently logged in user name and
the date on which the user was created (in unix time).
```json
{
    "username": "my_user",
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
JSON-RPC request: Not Available

Returns: An object containing the created user name, an auth token,
a refresh token, and an action summary.  Creating a user also effectively
logs the user in.
```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNjE5LCAiZXhwIjogMTYxODg4MDM4My44OTE2MTksICJ1c2VybmFtZSI6ICJteV91c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.oH0IShTL7mdlVs4kcx3BIs_-1j0Oe-qXezJKjo-9Xgo",
    "refresh_token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzY3ODMuODkxNzAyNCwgImV4cCI6IDE2MjY2NTI3ODMuODkxNzAyNCwgInVzZXJuYW1lIjogIm15X3VzZXIiLCAidG9rZW5fdHlwZSI6ICJyZWZyZXNoIn0.a6ZeRjk8RQQJDDH0JV-qGY_d_HIgfI3XpsqUlUaFT7c",
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
JSON-RPC request: Not Available

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
JSON-RPC request: Not Available

Returns: A list of created users on the system
```json
{
    "users": [
        {
            "username": "testuser",
            "created_on": 1618771331.1685035
        },
        {
            "username": "testuser2",
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
JSON-RPC request: Not Available

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

JSON-RPC request: Not Available

Returns:  The username, new auth token, and action summary.
```json
{
    "username": "my_user",
    "token": "eyJhbGciOiAiSFMyNTYiLCAidHlwIjogIkpXVCJ9.eyJpc3MiOiAiTW9vbnJha2VyIiwgImlhdCI6IDE2MTg4NzgyNDMuNTE2Nzc5MiwgImV4cCI6IDE2MTg4ODE4NDMuNTE2Nzc5MiwgInVzZXJuYW1lIjogInRlc3R1c2VyIiwgInRva2VuX3R5cGUiOiAiYXV0aCJ9.Ia_X_pf20RR4RAEXcxalZIOzOBOs2OwearWHfRnTSGU",
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
JSON-RPC request: Not Available

Returns:

A temporary token that may be added to a request's query string for access
to any API endpoint.  The query string should be added in the form of:
```
?token={base32_ramdom_token}
```

#### Get the Current API Key
HTTP request:
```http
GET /access/api_key
```
JSON-RPC request: Not Available

Returns:

The current API key

#### Generate a New API Key
HTTP request:
```http
POST /access/api_key
```
JSON-RPC request: Not Available

Returns:

The newly generated API key.  This overwrites the previous key.  Note that
the API key change is applied immediately, all subsequent HTTP requests
from untrusted clients must use the new key.

### Database APIs
The following endpoints provide access to Moonraker's ldbm database.  The
database is divided into `namespaces`.  Each client may define its own
namespace to store information.  From the client's point of view, a
namespace is an `object`.  Items in the database are accessed by providing
a namespace and a key.  A key may be specifed as string, where a "." is a
delimeter, to access nested fields. Alternatively the key may be specified
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
Retreives an item from a specified namespace. The `key` argument may be
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

### Update Manager APIs
The following endpoints are available when the `[update_manager]` component has
been configured:

#### Get update status
Retreives the current state of each "package" available for update.  Typically
this will consist of information regarding `moonraker`, `klipper`, `system`
packages, along with configured clients.  If moonraker has not yet received
information from Klipper then its status will be omitted.  One may request that
the update info be refreshed by setting the `refresh` argument to `true`.  Note
that the `refresh` argument is ignored if an update is in progress or if a print
is in progress. In these cases the current status will be returned immediately
and no refresh will take place.  If the `refresh` argument is omitted its value
defaults to `false`.

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
Returns:

Status information for each update package.  Note that `mainsail`
and `fluidd` are present as clients configured in `moonraker.conf`
```json
{
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
            "type": "zip_beta",
            "channel": "beta",
            "need_channel_update": false,
            "pristine": true,
            "remote_alias": "origin",
            "branch": "master",
            "owner": "Arksine",
            "version": "v0.4.1-45",
            "remote_version": "v0.4.1-45",
            "full_version_string": "v0.4.1-45-g7e230c1c",
            "current_hash": "7e230c1c77fa406741ab99fb9156951c4e5c9cb4",
            "remote_hash": "7e230c1c77fa406741ab99fb9156951c4e5c9cb4",
            "is_dirty": false,
            "detached": false,
            "commits_behind": [],
            "is_valid": true,
            "debug_enabled": true
        },
        "mainsail": {
            "name": "mainsail",
            "owner": "meteyou",
            "version": "v1.3.0",
            "remote_version": "v1.4.0"
        },
        "fluidd": {
            "name": "fluidd",
            "owner": "cadriel",
            "version": "v1.6.1",
            "remote_version": "v1.10.0"
        },
        "klipper": {
            "type": "zip_beta",
            "channel": "beta",
            "need_channel_update": false,
            "pristine": true,
            "remote_alias": "origin",
            "branch": "master",
            "owner": "Klipper3d",
            "version": "v0.9.1-317",
            "remote_version": "v0.9.1-324",
            "full_version_string": "v0.9.1-324-gd77928b1",
            "current_hash": "d77928b17ba6b32189033b3d6decdb5bcc7c342c",
            "remote_hash": "22753f3b389e3f21a6047bac70abc42b6cf4a7dc",
            "is_dirty": false,
            "detached": false,
            "commits_behind": [
                {
                    "sha": "22753f3b389e3f21a6047bac70abc42b6cf4a7dc",
                    "author": "Kevin O'Connor",
                    "date": "1615830538",
                    "subject": "tmc: Only check for tmc2130 reset via CS_ACTUAL if IHOLD > 0",
                    "message": "Signed-off-by: Kevin O'Connor <kevin@koconnor.net>",
                    "tag": null
                },
                {
                    "sha": "b4437f8eeeaddf60f893ceaeaf4d9ed06d57eeae",
                    "author": "Michael Kurz",
                    "date": "1615823429",
                    "subject": "bme280: Add support for BMP280 and BME680 sensors (#4040)",
                    "message": "This adds support for BMP280 and BME680 sensor ICs,\r\nalong with fixing calibration data readout for BME280.\r\n\r\nGas sensor readout for the BME680 is just the raw compensated value.\r\nTo get actual meaningful values, more research is needed.\r\n\r\nSigned-off-by: Michael Kurz <michi.kurz@gmail.com>",
                    "tag": null
                }
            ],
            "git_messages": [],
            "is_valid": true,
            "debug_enabled": true
        }
    },
    "busy": false
}
```
Below is an explanation for each field:

- `busy`: set to true if an update is in progress.  Moonraker will not
  allow concurrent updates.
- `github_rate_limit`: the maximum number of github API requests
  the user currently is allowed.  An unathenticated user typically has 60
  requests per hour.
- `github_requests_remaining`: the number of API request the user
  currently has remaining.
- `github_limit_reset_time`:  the time when the rate limit will reset,
  reported as seconds since the epoch (aka Unix Time).

The `moonraker`, `klipper` packages, along with and clients configured
as applications have the following fields:

- `configured_type`: the application type configured by the user
- `detected_type`:  the applicaiton type as detected by Moonraker.
- `channel`:  the currently configured update channel.  For Moonraker
  and Klipper this is set in the `[update_manager]` configuration.
  For clients the channel is determined by the configured type
- `need_channel_update`: This will be set to `true` if Moonraker has
  detected that a channel swap is necessary (ie: the configured type does
  not match the detected type). The channel swap will be performed on the
  next update.
- `pristine`: For `zip` and `zip_beta` types this is set to `true` if an
  applications source checksum matches the one generated  when the app was
  built.  This value will be set to the opposite of "dirty" for git repos.
  Note that a zip application can still be updated if the repo is not
  pristine.
- `owner`: the owner of the repo / application
- `branch`: the name of the current git branch.  This should typically
    be "master".
- `remote_alias`: the alias for the remote.  This should typically be
    "origin".
- `version`:  abbreviated version of the current repo on disk
- `remote_version`: abbreviated version of the latest available update
- `full_version_string`:  The complete version string of the current repo.
- `current_hash`: hash of the most recent commit on disk
- `remote_hash`: hash of the most recent commit pushed to the remote
- `is_valid`: true if installation is a valid git repo on the master branch
    and an "origin" set to the official remote.  For `zip` and `zip_beta`
    types this will report false if Moonraker is unable to fetch the
    current repo state from GitHub.
- `is_dirty`: true if the repo has been modified.  This will always be false
  for `zip` and `zip_beta` types.
- `detached`: true if the repo is currently in a detached state.  For `zip`
  and `zip_beta` types it is considered detached if the local release info
  does not match what is present on the remote.
- `debug_enabled`: True when `enable_repo_debug` has been configured.  This
    will bypass repo validation allowing detached updates, and updates from
    a remote/branch other than than the primary (typically origin/master).
- `commits_behind`: A list of commits behind.  Up to 30 "untagged" commits
  will be reported.  Moonraker checks the last 100 commits for tags, any
  commits beyond the last 30 with a tag will also be reported.
- `git_messages`:  If a repo is in the "invalid" state this field will hold
  a list of string messages containing the output of the last failed git
  command.  Note that it is possible for a git command to fail without
  providing output (for example, it may become non-responsive and time out),
  so it is possible for this field to be an empty list when the repo is
  invalid.

Web clients have the following fields:

- `name`: name of the configured client
- `owner`: the owner of the client
- `version`:  version of the installed client.
- `remote_version`:  version of the latest release published to GitHub

The `system` package has the following fields:

- `package_count`: the number of system packages available for update
- `package_list`: an array containing the names of packages available
  for update

### Perform a full update
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
`name` argument is requred, it's value should match the `client_name`
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
On ocassion a git command may fail resulting in a repo in a
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
        "dev_one":null,
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

### Octoprint API emulation
Partial support of Octoprint API is implemented with the purpose of
allowing uploading of sliced prints to a moonraker instance.
Currently we support Slic3r derivatives and Cura with Cura-Octoprint.

#### Version information
HTTP request:
```http
GET /api/version
```
JSON-RPC request: Not Available

Returns:

An object containing simulated Octoprint version information
```json
{
    "server": "1.5.0",
    "api": "0.1",
    "text": "Octoprint (Moonraker v0.3.1-12)"
}
```

#### Server status
HTTP request:
```http
GET /api/server
```
JSON-RPC request: Not Available

Returns:

An object containing simulated Octoprint server status
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

An object containing stubbed Octoprint login/user verification
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

An object containing stubbed Octoprint settings.
The webcam route is hardcoded to Fluidd/Mainsail default path.
We say we have the UFP plugin installed so that Cura-Octoprint will
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

#### Octoprint File Upload
HTTP request:
```http
POST /api/files/local
```
JSON-RPC request: Not Available

Alias for Moonrakers [file upload API](#file-upload).

#### Get Job status
HTTP request:
```http
GET /api/job
```
JSON-RPC request: Not Available

Returns:

An object containing stubbed Octoprint Job status
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

An object containing Octoprint Printer status
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
Content-Type: applicaton/json

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

An object containing simulates Octoprint Printer profile
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
The APIs below are avilable when the `[history]` component has been configured.

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

An array of requsted historical jobs:
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
each paramater:

- `topic`: The topic to publish.
- `payload`: Payload to send with the topic.  May be a boolean, float,
  integer, string, object, or array. All values are converted to strings prior
  to publishing.  Objects and Arrays are JSON encoded.  If omitted an empty
  payload is sent.
- `qos`: QOS level to use when publishing the topic.  Must be an integer value
  from 0 to 2.  If omitted the system configured default is used.
- `retain`: If set to `true` the MQTT broker will retain the payload of this
  request.  Note that only the mostly recently tagged payload is retained.
  When other clients first subscribe to the topic they immediately recieve the
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
                "path": "{file or directory path}",
                "root": "{root}",
                "size": 46458,
                "modified": 545465
            },
            "source_item": {
                "path": "{file or directory path}",
                "root": "{root_name}"
            }
        }
    ]
}
```
The `source_item` field is only present for `move_item` and
`copy_item` actions.  The `action` field will be set
to one of the following values:

- `create_file`
- `create_dir`
- `delete_file`
- `delete_dir`
- `move_file`
- `move_dir`
- `modify_file`
- `root_update`

Most of the above actions are self explanatory.  The `root_update`
notification is sent when a `root` folder has changed its location,
for example when a user configures a different gcode file path
in Klipper.

#### Update Manager Response
The update manager will send asyncronous messages to the client during an
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
- The `message` field contains an asyncronous message sent during the update
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
        "websocket_connections": 2
    }]
}
```

As with the [proc_stats request](#get-moonraker-process-stats) the `cpu_temp`
field will be set to `null` if the host machine does not support retreiving CPU
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

The following startup sequence is recommened for clients which make use of
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
    unsupported slicers will only return the size and modifed date.

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
Most languanges have functionality built in to convert Unix
time to a workable object or string.  For example, in JavaScript
one might do something like the following:
```javascript
for (let resp of result.gcode_store) {
  let date = new Date(resp.time * 1000);
  // Do something with date and resp.message ...
}
```
