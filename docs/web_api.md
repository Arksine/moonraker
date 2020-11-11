# API

Most API methods are supported over both the Websocket and HTTP transports.
File Transfer and "/access" requests are only available over HTTP. The
Websocket is required to receive printer generated events such as gcode
responses.  For information on how to set up the Websocket, please see the
Appendix at the end of this document.

Note that all HTTP responses are returned as a json encoded object in the form
of:

`{result: <response data>}`

Arguments sent via the HTTP APIs may either be included in the query string
or as part of the request's body.  All of the examples in this document
use the query string for arguments.

Websocket requests are returned in JSON-RPC format:
`{jsonrpc: "2.0", "result": <response data>, id: <request id>}`

HTML requests will recieve a 500 status code on error, accompanied by
the specific error message.

Websocket requests that result in an error will receive a properly formatted
JSON-RPC response:
`{jsonrpc: "2.0", "error": {code: <code>, message: <msg>}, id: <request_id>}`
Note that under some circumstances it may not be possible for the server to
return a request ID, such as an improperly formatted json request.

The `test\client` folder includes a basic test interface with example usage for
most of the requests below.  It also includes a basic JSON-RPC implementation
that uses promises to return responses and errors (see json-rcp.js).

## Printer Administration

### Get Klippy host information:
- HTTP command:\
  `GET /printer/info`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.info", id: <request id>}`

- Returns:\
  An object containing the build version, cpu info, Klippy's current state.

    ```json
    {
      state: "<klippy state>",
      state_message: "<current state message>",
      hostname: "<hostname>",
      software_version: "<version>",
      cpu_info: "<cpu_info>",
      klipper_path: "<moonraker use only>",
      python_path: "<moonraker use only>",
      log_file: "<moonraker use only>",
      config_file: "<moonraker use only>",
    }
    ```

### Emergency Stop
- HTTP command:\
  `POST /printer/emergency_stop`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.emergency_stop", id: <request id>}`

- Returns:\
  `ok`

### Restart the host
- HTTP command:\
  `POST /printer/restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.restart", id: <request id>}`

- Returns:\
  `ok`

### Restart the firmware (restarts the host and all connected MCUs)
- HTTP command:\
  `POST /printer/firmware_restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.firmware_restart", id: <request id>}`

- Returns:\
  `ok`

## Printer Status

### List available printer objects:
- HTTP command:\
  `GET /printer/objects/list`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.objects.list", id: <request id>}`

- Returns:\
  An a list of "printer objects" that are currently available for query
  or subscription.  This list will be passed in an "objects" parameter.

  ```json
  { objects: ["gcode", "toolhead", "bed_mesh", "configfile",....]}
  ```

### Query printer object status:
- HTTP command:\
  `GET /printer/objects/query?gcode`

  The above will fetch a status update for all gcode attributes.  The query
  string can contain multiple items, and specify individual attributes:

  `?gcode=gcode_position,busy&toolhead&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.objects.query", params:
    {objects: {gcode: null, toolhead: ["position", "status"]}},
     id: <request id>}`

  Note that an empty array will fetch all available attributes for its key.

- Returns:\
  An object where the top level items are "eventtime" and "status".  The
  "status" item contains data about the requested update.

  ```json
  {
    eventtime: <klippy time of update>,
    status: {
      gcode: {
        busy: true,
        gcode_position: [0, 0, 0 ,0],
        ...},
      toolhead: {
        position: [0, 0, 0, 0],
        status: "Ready",
        ...},
      ...}
    }
  ```
See [printer_objects.md](printer_objects.md) for details on the printer objects
available for query.

### Subscribe to printer object status:
- HTTP command:\
  `POST /printer/objects/subscribe?connection_id=123456789&
   gcode=gcode_position,bus&extruder=target`

   Note:  The HTTP API requires that a `connection_id` is passed via the query
   string or as part of the form.   This should be the
   [ID reported](#get-websocket-id) from a currently connected websocket. A
   request that includes only the `connection_id` argument will cancel the
   subscription on the specified websocket.

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.objects.subscribe", params:
    {objects: {gcode: null, toolhead: ["position", "status"]}},
    id: <request id>}`

    Note that if `objects` is an empty object then the subscription will
    be cancelled.

- Returns:\
  Status data for objects in the request, with the format matching that of
  the `/printer/objects/query`:

  ```json
  {
    eventtime: <klippy time of update>,
    status: {
      gcode: {
        busy: true,
        gcode_position: [0, 0, 0 ,0],
        ...},
      toolhead: {
        position: [0, 0, 0, 0],
        status: "Ready",
        ...},
      ...}
    }
  ```
See [printer_objects.md](printer_objects.md) for details on the printer objects
available for subscription.

Status updates for subscribed objects are sent asynchronously over the
websocket.  See the `notify_status_update` notification for details.

### Query Endstops
- HTTP command:\
  `GET /printer/query_endstops/status`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.query_endstops.status", id: <request id>}`

- Returns:\
  An object containing the current endstop state, with each attribute in the
  format of `endstop:<state>`, where "state" can be "open" or "TRIGGERED", for
  example:

```json
  {x: "TRIGGERED",
   y: "open",
   z: "open"}
```

### Query Server Info
- HTTP command:\
  `GET /server/info`

- Websocket command:
  `{jsonrpc: "2.0", method: "server.info", id: <request id>}`

- Returns:\
  An object containing the server's state, structured as follows:

```json
  {
    klippy_connected: <bool>,
    klippy_state: <string>,
    plugins: [<strings>]
  }
```
  Note that `klippy_state` will match the `state` value received from
  `/printer/info`. The `klippy_connected` item tracks the state of the
  connection to Klippy. The `plugins` key will return a list of all
  enabled plugins.  This can be used by clients to check if an optional
  plugin is available.

### Fetch stored temperature data
- HTTP command:\
  `GET /server/temperature_store`

- Websocket command:
  `{jsonrpc: "2.0", method: "server.temperature_store", id: <request id>}`

- Returns:\
  An object where the keys are the available temperature sensor names, and with
  the value being an array of stored temperatures.  The array is updated every
  1 second by default, containing a total of 1200 values (20 minutes).  The
  array is organized from oldest temperature to most recent (left to right).
  Note that when the host starts each array is initialized to 0s.

### Fetch stored gcode info
- HTTP command:\
  `GET /server/gcode_store`

  Optionally, a `count` argument may be added to specify the number of
  responses to fetch. If omitted, the entire gcode store will be sent
  (up to 1000 responses).

  `GET /server/gcode_store?count=100`

- Websocket command:
  `{jsonrpc: "2.0", method: "server.gcode_store", id: <request id>}`

  OR
  `{jsonrpc: "2.0", method: "server.gcode_store",
   params: {count: <integer>} id: <request id>}`

- Returns:\
  An object with the field `gcode_store` that contains an array
  of objects.  Each object will contain a `message` field and a
  `time` field:
```json
  {
    gcode_store: [
      {
        message: <string>,
        time: unix_time_stamp
      }, ...
    ]
  }
```
Each `message` field contains a gcode response received at the time
indicated in the `time` field. Note that the time stamp refers to
unix time (in seconds).  This can be used to create a JavaScript
`Date` object:
```javascript
for (let resp of result.gcode_store) {
  let date = new Date(resp.time * 1000);
  // Do something with date and resp.message ...
}
```

### Restart Server
- HTTP command:\
  `POST /server/restart`

- Websocket command:
  `{jsonrpc: "2.0", method: "server.restart", id: <request id>}`

- Returns:\
  `"ok"` upon receipt of the restart request.  After the request
  is returns, the server will restart.  Any existing connection
  will be disconnected.  A restart will result in the creation
  of a new server instance where the configuration is reloaded.

## Get Websocket ID
- HTTP command:\
  Not Available

- Websocket command:
  `{jsonrpc: "2.0", method: "server.websocket.id", id: <request id>}`

- Returns:\
  This connected websocket's unique identifer in the format shown below.
  Note that this API call is only available over the websocket.

```json
  {
    websocket_id: <int>
  }
```

## Gcode Controls

### Run a gcode:
- HTTP command:\
  `POST /printer/gcode/script?script=<gc>`

  For example,\
  `POST /printer/gcode/script?script=RESPOND MSG=Hello`\
  Will echo "Hello" to the terminal.

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.gcode.script",
    params: {script: <gc>}, id: <request id>}`

- Returns:\
  An acknowledgement that the gcode has completed execution:

  `ok`

### Get GCode Help
- HTTP command:\
  `GET /printer/gcode/help`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.gcode.help",
    params: {script: <gc>}, id: <request id>}`

- Returns:\
  An object where they keys are gcode handlers and values are the associated
  help strings.  Note that help strings are not available for basic gcode
  handlers such as G1, G28, etc.

## Print Management

### Print a file
- HTTP command:\
  `POST /printer/print/start?filename=<file name>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.print.start",
    params: {filename: <file name>, id:<request id>}`

- Returns:\
  `ok` on success

### Pause a print
- HTTP command:\
  `POST /printer/print/pause`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.print.pause", id: <request id>}`

- Returns:\
  `ok`

### Resume a print
- HTTP command:\
  `POST /printer/print/resume`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.print.resume", id: <request id>}`

- Returns:\
  `ok`

### Cancel a print
- HTTP command:\
  `POST /printer/print/cancel`

- Websocket command:\
  `{jsonrpc: "2.0", method: "printer.print.cancel", id: <request id>}`

- Returns:\
  `ok`

## Machine Commands

### Shutdown the Operating System
- HTTP command:\
  `POST /machine/shutdown`

- Websocket command:\
  `{jsonrpc: "2.0", method: "machine.shutdown", id: <request id>}`

- Returns:\
  No return value as the server will shut down upon execution

### Reboot the Operating System
- HTTP command:\
  `POST /machine/reboot`

- Websocket command:\
  `{jsonrpc: "2.0", method: "machine.reboot", id: <request id>}`

- Returns:\
  No return value as the server will shut down upon execution


## File Operations

Most file operations are available over both APIs, however file upload,
file download, and file delete are currently only available via HTTP APIs.

Moonraker organizes different local directories into "roots".  For example,
gcodes are located at `http:\\host\server\files\gcodes\*`, otherwise known
as the "gcodes" root.  The following roots are available:
- gcodes
- config
- config_examples (read-only)

Write operations (upload, delete, make directory, remove directory) are
only available on the `gcodes` and config roots.  Note that the `config` root
is only available if the "config_path" option has been set in Moonraker's
configuration.

### List Available Files
Walks through a directory and fetches all files.  All file names include a
path relative to the specified "root".  Note that if the query st

- HTTP command:\
  `GET /server/files/list?root=gcodes`

  If the query string is omitted then the command will return
  the "gcodes" file list by default.

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.list", params: {root: "gcodes"}
  , id: <request id>}`

  If `params` are are omitted then the command will return the "gcodes"
  file list.

- Returns:\
  A list of objects containing file data in the following format:

```json
[
  {filename: "file name",
   size: <file_size>,
   modified: <unix_time>,
   ...]
```

### Get GCode Metadata
  Get file metadata for a specified gcode file.  If the file is located in
  a subdirectory, then the file name should include the path relative to
  the "gcodes" root.  For example, if the file is located at:\
  `http://host/server/files/gcodes/my_sub_dir/my_print.gcode`
  Then the filename should be `my_sub_dir/my_print.gcode`.

- HTTP command:\
  `GET /server/files/metadata?filename=<filename>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.metadata", params: {filename: "filename"}
  , id: <request id>}`

- Returns:\
  Metadata for the requested file if it exists.  If any fields failed
  parsing they will be omitted.  The metadata will always include the file name,
  modified time, and size.

```json
  {
    filename: "file name",
    size: <file_size>,
    modified: <unix_time>,
    slicer: "Slicer Name",
    slicer_version: "<version>",
    first_layer_height: <mm>,
    first_layer_bed_temp: <C>,
    first_layer_extr_temp: <C>,
    layer_height: <mm>,
    object_height: <mm>,
    estimated_time: <time_in_seconds>,
    filament_total: <mm>,
    thumbnails: [
      {
        width: <in_pixels>,
        height: <in_pixels>,
        size: <length_of_string>,
        data: <base64_string>
      }, ...
    ]
  }
```

### Get directory information
Returns a list of files and subdirectories given a supplied path.
Unlike `/server/files/list`, this command does not walk through
subdirectories.

- HTTP command:\
  `GET /server/files/directory?path=gcodes/my_subdir&extended=true`

  If the query string is omitted then the command will return
  the "gcodes" file list by default.

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.get_directory",
   params: {path: "gcodes/my_subdir", extended: true} ,
   id: <request id>}`

  If the "params" are omitted then the command will return
  the "gcodes" file list by default.

The `extended` argument is optional, and defaults to false. If
specified and set to true, then data returned for gcode files
will also include metadata if it is available.

- Returns:\
  An object containing file and subdirectory information in the
  following format:

```json
  {
    files: [
      {
        filename: "file name",
        size: <file_size>,
        modified: <unix_time>
      }, ...
    ],
    dirs: [
      {
        dirname: "directory name",
        modified: <unix_time>
      }
    ]
  }
```

### Make new directory
Creates a new directory at the specified path.

- HTTP command:\
  `POST /server/files/directory?path=gcodes/my_new_dir`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.post_directory", params:
   {path: "gcodes/my_new_dir"}, id: <request id>}`

Returns:\
`ok` if successful

### Delete directory
Deletes a directory at the specified path.

- HTTP command:\
  `DELETE /server/files/directory?path=gcodes/my_subdir`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.delete_directory", params:
   {path: "gcodes/my_subdir"} , id: <request id>}`

  If the specified directory contains files then the delete request
  will fail, however it is possible to "force" deletion of the directory
  and all files in it with and additional argument in the query string:\
  `DELETE /server/files/directory?path=gcodes/my_subdir&force=true`

  OR to the JSON-RPC params:\
  `{jsonrpc: "2.0", method: "get_directory", params:
   {path: "gcodes/my_subdir", force: True}, id: <request id>}`

  Note that a forced deletion will still check in with Klippy to be sure
  that a file in the requested directory is not loaded by the virtual_sdcard.

- Returns:\
`ok` if successful

### Move a file or directory
Moves a file or directory from one location to another. Note that the following
conditions must be met for a move successful move:
- The source must exist
- The source and destinations must have the same "root" directory
- The user (typically "Pi") must have the appropriate file permissions
- Neither the source nor destination can be loaded by the virtual_sdcard.
  If the source or destination is a directory, it cannot contain a file
  loaded by the virtual_sdcard.

When specifying the `source` and `dest`, the "root" directory should be
prefixed. Currently the only supported roots are "gcodes/" and "config/".

This API may also be used to rename a file or directory.   Be aware that an
attempt to rename a directory to a directory that already exists will result
in *moving* the source directory to the destination directory.

- HTTP command:\
  `POST /server/files/move?source=gcodes/my_file.gcode
  &dest=gcodes/subdir/my_file.gcode`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.move", params:
   {source: "gcodes/my_file.gcode",
   dest: "gcodes/subdir/my_file.gcode"}, id: <request id>}`

### Copy a file or directory
Copies a file or directory from one location to another.  A successful copy has
the pre-requesites as a move with one exception, a copy may complete if the
source file/directory is loaded by the virtual_sdcard.  As with the move API,
the source and destination should have the root prefixed.

- HTTP command:\
  `POST /server/files/copy?source=gcodes/my_file.gcode
   &dest=gcodes/subdir/my_file.gcode`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.copy", params:
   {source: "gcodes/my_file.gcode", dest: "gcodes/subdir/my_file.gcode"},
   id: <request id>}`

### Gcode File Download
- HTTP command:\
  `GET /server/files/gcodes/<file_name>`

- Websocket command:\
  Not Available

- Returns:\
  The requested file

### File Upload
Upload a file.  Currently files may be uploaded to the "gcodes" or "config"
root, with "gcodes" being the default location.  If one wishes to upload
to a subdirectory, the path may be added to the upload's file name
(relative to the root). If the directory does not exist an error will be
returned.  Alternatively, the "path" argument may be set, as explained
below.

- HTTP command:\
  `POST /server/files/upload`

  The file to be uploaded should be added to the FormData per the XHR spec.
  The following arguments may be added to the form:
  - root: The root location in which to upload the file.  Currently this may
    be "gcodes" or "config".  If not specified the default is "gcodes".
  - path: This argument may contain a path (relative to the root) indicating
    a subdirectory to which the file is written. If a "path" is present, the
    server will attempt to create any subdirectories that do not exist.
  Arguments available only for the "gcodes" root:
  - print: If set to "true", Klippy will attempt to start the print after
    uploading.  Note that this value should be a string type, not boolean. This
    provides compatibility with Octoprint's legacy upload API.

- Websocket command:\
  Not Available

- Returns:\
  The file name along with a successful response.
  ```json
  {'result': "file_name"}
  ```
  If the supplied root is "gcodes", a "print_started" attribute is also
   returned.
  ```json
  {'result': "file_name", 'print_started': <boolean>}
  ```

### Gcode File Delete
Delete a file in the "gcodes" root.  A relative path may be added to the file
to delete a file in a subdirectory.
- HTTP command:\
  `DELETE /server/files/gcodes/<file_name>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.delete_file", params:
   {path: "gcodes/<file_name>"}, id: <request id>}`

   If the gcode file exists within a subdirectory, the relative
   path should be included in the file name.

- Returns:\
  The HTTP request returns the name of the deleted file.

### Download included config file
- HTTP command:\
  `GET /server/files/config/<file_name>`

- Websocket command:\
  Not Available

- Returns:\
  The requested file

### Delete included config file
Delete a file in the "config" root.  A relative path may be added to the file
to delete a file in a subdirectory.
- HTTP command:\
  `DELETE /server/files/config/<file_name>`

- Websocket command:\
  `{jsonrpc: "2.0", method: "server.files.delete_file", params:
   {path: "config/<file_name>}, id: <request id>}`

- Returns:\
  The HTTP request returns the name of the deleted file.

### Download a config example
- HTTP command:\
  `GET /server/files/config_examples/<file_name>`

- Websocket command:\
  Not Available

- Returns:\
  The requested file

### Download klippy.log
- HTTP command:\
  `GET /server/files/klippy.log`

- Websocket command:\
  Not Available

- Returns:\
  klippy.log

### Download moonraker.log
- HTTP command:\
  `GET /server/files/moonraker.log`

- Websocket command:\
  Not Available

- Returns:\
  moonraker.log

## Authorization

Untrusted Clients must use a key to access the API by including it in the
`X-Api-Key` header for each HTTP Request.  The API below allows authorized
clients to receive and change the current API Key.

### Get the Current API Key
- HTTP command:\
  `GET /access/api_key`

- Websocket command:\
  Not Available

- Returns:\
  The current API key

### Generate a New API Key
- HTTP command:\
  `POST /access/api_key`

- Websocket command:\
  Not available

- Returns:\
  The newly generated API key.  This overwrites the previous key.  Note that
  the API key change is applied immediately, all subsequent HTTP requests
  from untrusted clients must use the new key.

### Generate a Oneshot Token

Some HTTP Requests do not expose the ability the change the headers, which is
required to apply the `X-Api-Key`.  To accomodiate these requests it a client
may ask the server for a Oneshot Token.  Tokens expire in 5 seconds and may
only be used once, making them relatively for inclusion in the query string.

- HTTP command:\
  `GET /access/oneshot_token`

- Websocket command:
  Not available

- Returns:\
  A temporary token that may be added to a requests query string for access
  to any API endpoint.  The query string should be added in the form of:
  `?token=randomly_generated_token`

## Websocket notifications
Printer generated events are sent over the websocket as JSON-RPC 2.0
notifications.  These notifications are sent to all connected clients
in the following format:

`{jsonrpc: "2.0", method: <event method name>}`

OR

`{jsonrpc: "2.0", method: <event method name>, params: [<event parameter>]}`

If a notification has parameters,  the `params` value will always be
wrapped in an array as directed by the JSON-RPC standard.  Currently
all notifications available are broadcast with either no parameters
or a single parameter.

### Gcode response:
All calls to gcode.respond() are forwarded over the websocket.  They arrive
as a "gcode_response" notification:

`{jsonrpc: "2.0", method: "notify_gcode_response", params: ["response"]}`

### Status subscriptions:
Status Subscriptions arrive as a "notify_status_update" notification:

`{jsonrpc: "2.0", method: "notify_status_update", params: [<status_data>]}`

The structure of the status data is identical to the structure that is
returned from an object query's "status" attribute.

### Klippy Disconnected:
Notify clients when Moonraker's connection to Klippy has terminated

`{jsonrpc: "2.0", method: "notify_klippy_disconnected"}`

### File List Changed
When a client makes a change to the virtual sdcard file list
(via upload or delete) a notification is broadcast to alert all connected
clients of the change:

`{jsonrpc: "2.0", method: "notify_filelist_changed",
 params: [<file changed info>]}`

The <file changed info> param is an object in the following format, where
the "action" is the operation that prompted the change, and the "item"
contains information about the item that has changed:

```json
{action: "<action>",
  item: {
    path: "<file or directory path>",
    root: "<root_name>",
    size: <file size>,
    modified: "<date modified>"
 }
```
Note that file move and copy actions also include a "source item" that
contains the path and root of the source file or directory.
```json
{action: "<action>",
  item: {
    path: "<file or directory path>",
    root: "<root_name>",
    size: <file size>,
    modified: "<date modified>"
 },
  source_item: {
    path: "<file or directory path>",
    root: "<root_name>"
  }
}
```

The following `actions` are currently available:
- `upload_file`
- `delete_file`
- `create_dir`
- `delete_dir`
- `move_item`
- `copy_item`

### Metadata Update
When a new file is uploaded via the API a websocket notification is broadcast
to all connected clients after parsing is complete:

`{jsonrpc: "2.0", method: "notify_metadata_update", params: [metadata]}`

Where `metadata` is an object in the following format:

```json
{
  filename: "file name",
  size: <file size>,
  modified: "last modified date",
  slicer: "Slicer Name",
  first_layer_height: <in mm>,
  layer_height: <in mm>,
  object_height: <in mm>,
  estimated_time: <time in seconds>,
  filament_total: <in mm>,
  thumbnails: [
    {
      width: <in pixels>,
      height: <in pixels>,
      size: <length of string>,
      data: <base64 string>
    }, ...
  ]
}
```

# Appendix

## Websocket setup
All transmissions over the websocket are done via json using the JSON-RPC 2.0
protocol.  While the websever expects a json encoded string, one limitation
of Eventlet's websocket is that it can not send string encoded frames.  Thus
the client will receive data om the server in the form of a binary Blob that
must be read using a FileReader object then decoded.

The websocket is located at `ws://host:port/websocket`, for example:
```javascript
var s = new WebSocket("ws://" + location.host + "/websocket");
```

It also should be noted that if authorization is enabled, an untrusted client
must request a "oneshot token" and add that token's value to the websocket's
query string:

```
ws://host:port/websocket?token=<32 character base32 string>
```

This is necessary as it isn't currently possible to add `X-Api-Key` to a
Websocket object's request header.

The following startup sequence is recommened for clients which make use of
the websocket:
1) Attempt to connect to `/websocket` until successful using a timer-like
   mechanism
2) Once connected, query `/printer/info` (or `printer.info`) for the ready
   status.
   - If the response returns an error (such as 404), set a timeout for
     2 seconds and try again.
   - If the response returns success, check the result's `state` attribute
     - If `state == "ready"` you may proceed to request status of printer objects
       make subscriptions, get the file list, etc.
     - If `state == "error"` then Klippy has experienced an error
       - If an error is detected it might be wise to prompt the user.  You can
         get a description of the error from the `state_message` attribute
     - If `state == "shutdown"` then Klippy is in a shutdown state.
     - If `state == "startup"` then re-request printer info in 2s.
- Repeat step 2 until Klipper reports ready.
- Client's should watch for the `notify_klippy_disconnected` event.  If it reports
  disconnected then Klippy has either been stopped or restarted.  In this
  instance the client should repeat the steps above to determine when
  klippy is ready.

## Basic Print Status
An advanced client will likely use subscriptions and notifications
to interact with Moonraker, however simple clients such as home automation
software and embedded devices (ie: ESP32) may only wish to monitor the
status of a print.  Below is a high level walkthrough for receiving print state
via polling.

- Set up a timer to poll at the desired interval.  Depending on your use
  case, 1 to 2 seconds is recommended.
- On each cycle, issue the following request:
  - `GET http://host/printer/objects/query?webhooks&virtual_sdcard&print_stats`\
    Or via json-rpc:\
    `{'jsonrpc': "2.0", 'method': "printer.objects.query", 'params':
    {'objects': {'webhooks': null, 'virtual_sdcard': null,
    'print_stats': null}}, id: <request id>}`
- If the request returns an error or the returned `result.status` is an empty
  object printer objects are not available for query.  Each queried object
  should be available in `result.status`.  The client should check to make
  sure that all objects are received before proceeding.
- Inspect `webhooks.ready`.  If the value is not "ready" the printer
  is not available.  `webhooks.message` contains a message pertaining
  to the current state.
- If the printer is ready, inspect `print_stats.state`.  It may be one
  of the following values:
  - "standby": No print in progress
  - "printing":  The printer is currently printing
  - "paused":  A print in progress has been paused
  - "error":  The print exited with an error.  `print_stats.message`
    contains a related error message
  - "complete":  The last print has completed
- If `print_stats.state` is not "standby" then `print_stats.filename`
  will report the name of the currently loaded file.
- `print_stats.filename` can be used to fetch file metadata.  It
  is only necessary to fetch metadata once per print.\
  `GET http://host/server/files/metadata?filename=<filename>`\
  Or via json-rpc:\
  `{jsonrpc: "2.0", method: "server.files.metadata",
  params: {filename: "filename"}
  , id: <request id>}`\
  If metadata extraction failed then this request will return an error.
  Some metadata fields are only populated for specific slicers, and
  unsupported slicers will only return the size and modifed date.
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
    if (eta < 0)
      eta = 0;
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
- It is possible to query additional object if a client wishes to display
  more information (ie: temperatures).  See
  [printer_objects.md](printer_objects.md) for more information.

## Bed Mesh Coordinates
The [bed_mesh](printer_objects.md#bed_mesh) printer object may be used
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
