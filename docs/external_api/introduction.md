# Introduction

Moonraker provides APIs over two protocols, HTTP and JSON-RPC. Most
endpoints have corresponding APIs over both protocols.  When requests
are exclusive to a protocol or depend on some other condition it will
be noted in the documentation for that specific API.

At a high level, file transfer requests (upload and download) are exclusive
to the HTTP API. The Websocket is required to receive events such as status
updates and gcode responses.  For information on how to set up the Websocket,
please see the [Miscellaneous Tutorials](#websocket-setup).

## HTTP API Overview

Moonraker's HTTP API could be described as "REST-ish". Attempts are made to
conform to REST standards, however the dynamic nature of Moonraker's endpoint
 registration along with the need to keep consistency between two API protocols
results in an HTTP API that deviates

Moonraker is capable of parsing request arguments from the both the body
(either JSON or form-data depending on the `Content-Type` header) and from
the query string.  All arguments are grouped together in one data structure,
with body arguments taking precedence over query arguments.  Thus
if the same argument is supplied both in the body and in the
query string the body argument would be used. It is left up to the front end
developer to decide exactly how they want to provide arguments.

Nearly all successful HTTP requests will return a json encoded object in
the form of:

```text
{
    "result": <response data>
}
```

The response data may be any valid JSON type.  In most cases it will be
a JSON object itself, but some requests may return a simple string.

If the response is not wrapped in an object with a `result` field it will
be noted in the documentation for that API (or API set).  Generally this
only applies to endpoints that attempt to emulate other backends.

Should a request result in an error, a standard error code along with
an error specific message is returned, wrapped in a JSON object.

### Query string type hints

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

## JSON-RPC API Overview

Websocket, Unix Socket, and MQTT connections exclusively use the API
available over [JSON-RPC 2.0](https://jsonrpc.org).  In addition, Moonraker
provides an [JSON-RPC HTTP endpoint](#json-rpc-over-http-endpoint) giving
developers who want to avoid persistent connections a choice to use JSON-RPC.

The Websocket transmits and receives JSON-RPC objects in text frames.  MQTT
transmits them in the payload of a topic defined when MQTT is configured.  By
default, Moonraker receives JSON-RPC requests from the
`{instance_name}/moonraker/api/request` topic, and publishes responses to the
`{instance_name}/moonraker/api/response` topic.  The `{instance_name}` must be
a unique identifier for each instance of Moonraker connected to the broker.
It defaults to the machine's host name.

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

/// details | Optional MQTT timestamp
    type: tip
MQTT requests may provide an optional `mqtt_timestamp` keyword
argument in the `params` field of the JSON-RPC request.  To avoid
potential collisions from time drift it is recommended to specify
the timestamp in microseconds since the Unix Epoch.  If provided
Moonraker will use the timestamp to discard duplicate requests.
It is recommended to either provide a timestamp or publish API
requests at a QoS level of 0 or 2.
///

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

## Websocket Connections

### Primary websocket

The primary websocket supports Moonraker's JSON-RPC API.  Most applications that
desire a websocket connection will make use of the primary websocket.

The primary websocket is available at:
```
 ws://host_or_ip:port/websocket`
```

The primary websocket will remain connected until the application disconnects
or Moonraker is shutdown.

### Bridge websocket

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

The availability of bridge connections depends on Klippy's availability.
If Klippy is not running or its API server is not enabled then a bridge
websocket connection cannot be established.  Established bridge connections
will close when Klippy is shutdown or restarted.  Such connections will also
be closed if Moonraker is restarted or shutdown.

!!! Note
    If JWT or API Key authentication is required the application must use a
    [oneshot token](./authorization.md#generate-a-oneshot-token) when connecting
    to a bridge socket.  Since Moonraker does not decode bridge requests it is
    not possible to perform JWT authentication post connection.

## Unix Socket Connection

All JSON-RPC APIs available over the Websocket transport are also available
over the Unix Domain Socket connection.  Moonraker creates the socket file at
`<datapath>/comms/moonraker.sock` (ie: `~/printer_data/comms/moonraker.sock`).
The Unix Socket expects UTF-8 encoded JSON-RPC byte strings. Each JSON-RPC
request must be terminated with an ETX character (`0x03`).

The Unix Socket is desirable for front ends and extensions running on the
local machine as authentication is not necessary.  There should be a small
performance improvement due to the simplified transport protocol, however
the impact of this is likely negligible.

The `moontest` repo contains a
[python script](https://github.com/Arksine/moontest/blob/master/scripts/unix_socket_test.py)
to test comms over the unix socket.

## JSON-RPC over HTTP Endpoint

Exposes the JSON-RPC interface over HTTP.  Most JSON-RPC methods with
corresponding HTTP APIs are available.  Methods exclusive to other
transports, such as [Identify Connection](./server.md#identify-connection), are
not available.

/// note
If authentication is required it must be part of the HTTP request,
either using the API Key Header (`X-Api-Key`) or JWT Bearer Token.
///

```{.http .apirequest title="HTTP Request"}
POST /server/jsonrpc
Content-Type: application/json
{
    "jsonrpc": "2.0",
    "method": "printer.info",
    "id": 5153
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "jsonrpc": "2.0",
    "id": 5153,
    "result": {
        "state": "ready",
        "state_message": "Printer is ready",
        "hostname": "my-pi-hostname",
        "software_version": "v0.9.1-302-g900c7396",
        "cpu_info": "4 core ARMv7 Processor rev 4 (v7l)",
        "klipper_path": "/home/pi/klipper",
        "python_path": "/home/pi/klippy-env/bin/python",
        "log_file": "/tmp/klippy.log",
        "config_file": "/home/pi/printer.cfg"
    }
}
```
////

/// note
If an error is encountered while processing a JSON-RPC request
over HTTP the request itself will still return success.  The
error will be returned in the response as a JSON-RPC encoded
object.
///

## Jinja2 Template API Calls

Some template options in Moonraker's configuration, such as those in the
[button](../configuration.md#button) component, may call Moonraker APIs through
the `call_method(method_name, kwargs)` context function. The `call_method`
function takes the API's JSON-RPC method name as its first parameter, followed
by a set of keyword arguments as per the method's requirements.

```ini
# moonraker.conf

# Query Printer Objects example
[button check_status]
pin: gpio26
on_press:
  {% set query = {"toolhead": ["position"], "print_stats": None} %}
  # JSON-RPC method is "printer.objects.query", which takes a single "objects"
  # argument
  {% set status = call_method("printer.objects.query", objects=query) %}
  # do something with the value returned from the object query, perhaps
  # send a websocket notification or publish a mqtt topic

# Publish button event to MQTT Topic
[button check_status]
pin: gpio26
on_release:
  # JSON-RPC method is "server.mqtt.publish"
  {% do call_method("server.mqtt.publish",
                    topic="moonraker/my-button",
                    payload="Button Released") %}
```

## Miscellaneous Tutorials

### Websocket setup

The websocket is located at `ws://host:port/websocket`, for example:
```javascript
var s = new WebSocket("ws://" + location.host + "/websocket");
```

/// Note
It may be necessary to authenticate the connection first.  This
tutorial assumes that the client is running on a trusted connection.
///

The following is a basic startup sequence that may be used to establish
a full connection to Moonraker and ensure that Klipper is running and
available:

1. Attempt to connect to `/websocket` until successful using a timer-like
   mechanism.
2. Once connected, query the [server info](./server.md#query-server-info)
   endpoint to check Klippy's state.
      - If the response returns an error then either the client
        is not authorized or Moonraker is not running.  Direct the user to
        SSH into the machine and check `<data_folder/logs/moonraker.log`.
      - If the response returns success, check the result's `klippy_state`
        field:
        - `klippy_state == "ready"`: you may proceed to request status of
          printer objects make subscriptions, get the file list, etc.
        - `klippy_state == "error"`:  Klippy has experienced an error
          starting up
        - `klippy_state == "shutdown"`: Klippy is in a shutdown state.
        - `klippy_state == "startup"`: re-request the `server info` endpoint in 2 seconds.
        - `klippy_state == "disconnected"`: The Klippy host either isn't running,
          has experienced a critical error during startup, or does not have its
          API Server enabled.
3. Repeat step 2 until Klipper reports ready.
4. Clients should watch for the `notify_klippy_disconnected` event.  If
   received then Klippy has either been stopped or restarted.  In this
   state the client should repeat the steps above to determine when
   klippy is ready.

/// note
If  Klippy reports an `error` or `shutdown` state it is advisable prompt
the user. You can get a description from the `state_message`
field of a [printer info](./printer.md#get-klippy-host-information) request.
///

### Basic Print Status

An advanced client will likely use subscriptions and notifications
to interact with Moonraker, however simple clients such as home automation
software and embedded devices (ie: ESP32) may only wish to monitor the
status of a print.  Below is a high level walkthrough for receiving print state
via polling.

- Set up a timer to poll at the desired interval.  Depending on your use
  case, 1 to 2 seconds is recommended.
- On each cycle, issue the following request:
    ```
    GET http://host/printer/objects/query?webhooks&virtual_sdcard&print_stats
    ```
    Or via JSON-RPC 2.0:
    ```json
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
    ```

- If the request returns an error or the returned `result.status` is an empty
  object, then this is an indication that Klippy either experienced an error or
  it is not properly configured.  Each queried object should be available in
  `result.status`.  The client should check to make sure that all objects are
  received before proceeding.
- Inspect `webhooks.state`.  If the value is not `ready` the printer
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
  ```
  GET http://host/server/files/metadata?filename=<filename>
  ```
  Or via JSON-RPC 2.0:
  ```json
  {
      "jsonrpc": "2.0",
      "method": "server.files.metadata",
      "params": {
          "filename": "{filename}"
      },
      "id": 5643
  }
  ```
  If metadata extraction failed then this request will return an error.
  Some metadata fields are only populated for specific slicers, and
  unsupported slicers will only return the size and modified date.

- There are multiple ways to calculate the ETA, this example will use
  file progress, as it is possible calculate the ETA with or without
  metadata.
    - If `metadata.estimated_time` is available, the eta calculation can
      be done as:
      ```javascript
      // assume "result" is the response from the status query
      let vsd = result.status.virtual_sdcard;
      let prog_time = vsd.progress * metadata.estimated_time;
      let eta = metadata.estimated_time - prog_time
      ```
      Alternatively, one can simply subtract the print duration from
      the estimated time:
      ```javascript
      // assume "result" is the response from the status query
      let pstats = result.status.print_status;
      let eta = metadata.estimated_time - pstats.print_duration;
      if (eta < 0) eta = 0;
      ```
    - If no metadata is available, print duration and progress can be used to
      calculate the ETA:
      ```javascript
      // assume "result" is the response from the status query
      let vsd = result.status.virtual_sdcard;
      let pstats = result.status.print_stats;
      let total_time = pstats.print_duration / vsd.progress;
      let eta = total_time - pstats.print_duration;
      ```

- It is possible to query additional objects if a client wishes to display
  more information (ie: temperatures).  See Klipper's
  [Status Reference](https://www.klipper3d.org/Status_Reference.html)
  documentation for details on objects available for query.

### Bed Mesh Coordinates

The [Bed Mesh](../printer_objects.md#bed_mesh) printer object may be used
to generate three dimensional coordinates of a probed area (or mesh).
Below is an example (in javascript) of how to transform the data received
from a bed_mesh object query into an array of 3D coordinates.

```javascript
// assume that we have executed an object query for bed_mesh and have the
// result.  This example generates 3D coordinates for the probed matrix,
// however it would work with the mesh matrix as well
function process_mesh(result) {
  let bed_mesh = result.status.bed_mesh;
  let matrix = bed_mesh.probed_matrix;
  if (!(matrix instanceof Array) ||  matrix.length < 3 ||
      !(matrix[0] instanceof Array) || matrix[0].length < 3)
      // make sure that the matrix is valid
      return;
  let coordinates = [];
  // calculate the distance between each sample on both the X an Y
  // axes
  let x_distance = (bed_mesh.mesh_max[0] - bed_mesh.mesh_min[0]) /
    (matrix[0].length - 1);
  let y_distance = (bed_mesh.mesh_max[1] - bed_mesh.mesh_min[1]) /
    (matrix.length - 1);
  let x_idx = 0;
  let y_idx = 0;
  // transform the matrix of z values into (x, y, z) coordinates
  for (const x_axis of matrix) {
    x_idx = 0;
    // mesh_min is the (x, y) coordinate of the first z sample
    let y_coord = bed_mesh.mesh_min[1] + (y_idx * y_distance);
    for (const z_coord of x_axis) {
      let x_coord = bed_mesh.mesh_min[0] + (x_idx * x_distance);
      x_idx++;
      coordinates.push([x_coord, y_coord, z_coord]);
    }
    y_idx++;
  }
}
// Use the array of coordinates to visualize the "probed area"
// or mesh..
```

### Converting to Unix Time

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
