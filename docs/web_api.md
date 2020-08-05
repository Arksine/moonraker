# API

Most API methods are supported over both the Websocket and HTTP transports.
File Transfer and "/access" requests are only available over HTTP. The
Websocket is required to receive printer generated events such as gcode
responses.  For information on how to set up the Websocket, please see the
Appendix at the end of this document.

Note that all HTTP responses are returned as a json encoded object in the form
of:

`{result: <response data>}`

The command matches the original command request, the result is the return
value generated from the request.

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
  `{jsonrpc: "2.0", method: "get_printer_info", id: <request id>}`

- Returns:\
  An object containing the build version, cpu info, and if the Klippy
  process is ready for operation.  The latter is useful when a client connects
  after the klippy state event has been broadcast.

  `{version: "<version>", cpu: "<cpu_info>", is_ready: <boolean>,
    hostname: "<hostname>", error_detected: <boolean>,
    message: "<current state message>"}`

### Emergency Stop
- HTTP command:\
  `POST /printer/emergency_stop`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_emergency_stop", id: <request id>}`

- Returns:\
  `ok`

### Restart the host
- HTTP command:\
  `POST /printer/restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_restart", id: <request id>}`

- Returns:\
  `ok`

### Restart the firmware (restarts the host and all connected MCUs)
- HTTP command:\
  `POST /printer/firmware_restart`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_firmware_restart", id: <request id>}`

- Returns:\
  `ok`

## Printer Status

### Request available printer objects and their attributes:
- HTTP command:\
  `GET /printer/objects/list`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_objects_list", id: <request id>}`

- Returns:\
  An object containing key, value pairs, where the key is the name of the
  Klippy module available for status query, and the value is an array of
  strings containing that module's available attributes.

  ```json
  { gcode: ["busy", "gcode_position", ...],
    toolhead: ["position", "status"...], ...}
  ```

### Request currently subscribed objects:
- HTTP command:
  `GET /printer/objects/subscription`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_objects_subscription", id: <request id>}`

- Returns:\
  An object of the similar that above, however the format of the `result`
  value is changed to include poll times:

   ```json
  { objects: {
      gcode: ["busy", "gcode_position", ...],
      toolhead: ["position", "status"...],
      ...},
    poll_times: {
      gcode: .25,
      toolhead: .25,
      ...}
    }
  ```

### Request the a status update for an object, or group of objects:
- HTTP command:\
  `GET /printer/objects/status?gcode`

  The above will fetch a status update for all gcode attributes.  The query
  string can contain multiple items, and specify individual attributes:

  `?gcode=gcode_position,busy&toolhead&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_objects_status", params:
    {gcode: [], toolhead: ["position", "status"]}, id: <request id>}`

  Note that an empty array will fetch all available attributes for its key.

- Returns:\
  An object where the top level keys are the requested Klippy objects, as shown
  below:

  ```json
  { gcode: {
      busy: true,
      gcode_position: [0, 0, 0 ,0],
      ...},
    toolhead: {
      position: [0, 0, 0, 0],
      status: "Ready",
      ...},
    ...}
  ```
### Subscribe to a status request or a batch of status requests:
- HTTP command:\
  `POST /printer/objects/subscription?gcode=gcode_position,bus&extruder=target`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_objects_subscription", params:
    {gcode: [], toolhead: ["position", "status"]}, id: <request id>}`

- Returns:\
  An acknowledgement that the request has been received:

  `ok`

  The actual status updates will be sent asynchronously over the websocket.

### Query Endstops
- HTTP command:\
  `GET /printer/query_endstops/status`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_query_endstops_status", id: <request id>}`

- Returns:\
  An object containing the current endstop state, with each attribute in the
  format of `endstop:<state>`, where "state" can be "open" or "TRIGGERED", for
  example:

```json
  {x: "TRIGGERED",
   y: "open",
   z: "open"}
```

### Fetch stored temperature data
- HTTP command:\
  `GET /server/temperature_store`

- Websocket command:
  `{jsonrpc: "2.0", method: "get_temperature_store", id: <request id>}`

- Returns:\
  An object where the keys are the available temperature sensor names, and with
  the value being an array of stored temperatures.  The array is updated every
  1 second by default, containing a total of 1200 values (20 minutes).  The
  array is organized from oldest temperature to most recent (left to right).
  Note that when the host starts each array is initialized to 0s.

## Gcode Controls

### Run a gcode:
- HTTP command:\
  `POST /printer/gcode/script?script=<gc>`

  For example,\
  `POST /printer/gcode/script?script=RESPOND MSG=Hello`\
  Will echo "Hello" to the terminal.

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_gcode_script",
    params: {script: <gc>}, id: <request id>}`

- Returns:\
  An acknowledgement that the gcode has completed execution:

  `ok`

### Get GCode Help
- HTTP command:\
  `GET /printer/gcode/help`

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_printer_gcode_help",
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
  `{jsonrpc: "2.0", method: "post_printer_print_start",
    params: {filename: <file name>, id:<request id>}`

- Returns:\
  `ok` on success

### Pause a print
- HTTP command:\
  `POST /printer/print/pause`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_pause", id: <request id>}`

- Returns:\
  `ok`

### Resume a print
- HTTP command:\
  `POST /printer/print/resume`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_resume", id: <request id>}`

- Returns:\
  `ok`

### Cancel a print
- HTTP command:\
  `POST /printer/print/cancel`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_printer_print_cancel", id: <request id>}`

- Returns:\
  `ok`

## Machine Commands

### Shutdown the Operating System
- HTTP command:\
  `POST /machine/shutdown`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_machine_shutdown", id: <request id>}`

- Returns:\
  No return value as the server will shut down upon execution

### Reboot the Operating System
- HTTP command:\
  `POST /machine/reboot`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_machine_reboot", id: <request id>}`

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
  `{jsonrpc: "2.0", method: "get_file_list", params: {root: "gcodes"}
  , id: <request id>}`

  If `params` are are omitted then the command will return the "gcodes"
  file list.

- Returns:\
  A list of objects containing file data in the following format:

```json
[
  {filename: "file name",
   size: <file size>,
   modified: "last modified date",
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
  `{jsonrpc: "2.0", method: "get_file_metadata", params: {filename: "filename"}
  , id: <request id>}`

- Returns:\
  Metadata for the requested file if it exists.  If any fields failed
  parsing they will be omitted.  The metadata will always include the file name,
  modified time, and size.

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

### Get directory information
Returns a list of files and subdirectories given a supplied path.
Unlike `/server/files/list`, this command does not walk through
subdirectories.

- HTTP command:\
  `GET /server/files/directory?path=gcodes/my_subdir`

  If the query string is omitted then the command will return
  the "gcodes" file list by default.

- Websocket command:\
  `{jsonrpc: "2.0", method: "get_directory", params: {path: "gcodes/my_subdir"}
  , id: <request id>}`

  If the "params" are omitted then the command will return
  the "gcodes" file list by default.

- Returns:\
  An object containing file and subdirectory information in the
  following format:

```json
  {
    files: [
      {
        filename: "file name",
        size: <file size>,
        modified: "last modified date"
      }, ...
    ],
    dirs: [
      {
        dirname: "directory name",
        modified: "last modified date"
      }
    ]
  }
```

### Make new directory
Creates a new directory at the specified path.

- HTTP command:\
  `POST /server/files/directory?path=gcodes/my_new_dir`

- Websocket command:\
  `{jsonrpc: "2.0", method: "post_directory", params:
   {path: "gcodes/my_new_dir"}, id: <request id>}`

Returns:\
`ok` if successful

### Delete directory
Deletes a directory at the specified path.

- HTTP command:\
  `DELETE /server/files/directory?path=gcodes/my_subdir`

- Websocket command:\
  `{jsonrpc: "2.0", method: "delete_directory", params:
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
  `{jsonrpc: "2.0", method: "post_file_move", params:
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
  `{jsonrpc: "2.0", method: "post_file_copy", params:
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
  Not Available

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
  Not Available

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

`{jsonrpc: "2.0", method: <event method name>, params: [<event state>]}`

It is important to keep in mind that the `params` value will always be
wrapped in an array as directed by the JSON-RPC standard.  Currently
all notifications available are broadcast with a single parameter.

### Gcode response:
All calls to gcode.respond() are forwarded over the websocket.  They arrive
as a "gcode_response" notification:

`{jsonrpc: "2.0", method: "notify_gcode_response", params: ["response"]}`

### Status subscriptions:
Status Subscriptions arrive as a "notify_status_update" notification:

`{jsonrpc: "2.0", method: "notify_status_update", params: [<status_data>]}`

The structure of the status data is identical to the structure that is
returned from a status request.

### Klippy Process State Changed:
The following Klippy state changes are broadcast over the websocket:
- ready
- disconnect
- shutdown

Note that Klippy's "ready" is different from the Printer's "ready".  The
Klippy "ready" state is broadcast upon startup after initialization is
complete.  It should also be noted that the websocket will be disconnected
after the "disconnect" state, as that notification is broadcast prior to a
restart. Klippy State notifications are broadcast in the following format:

`{jsonrpc: "2.0", method: "notify_klippy_state_changed", params: [<state>]}`

### File List Changed
When a client makes a change to the virtual sdcard file list
(via upload or delete) a notification is broadcast to alert all connected
clients of the change:

`{jsonrpc: "2.0", method: "notify_filelist_changed",
 params: [<file changed info>]}`

The <file changed info> param is an object in the following format:

```json
{action: "<action>", filename: "<file_name>", root: "<root_name>"}
```
Note that file move/copy actions also include the name and root of the
previous/source file:
```json
{action: "<action>", filename: "<file_name>", root: "<root_name>",
 prev_file: "<previous file name>", prev_root: "<previous_root_name>"}
```

The `action` is the operation that resulted in a file list change, the `filename`
is the name of the file the action was performed on, and the `filelist` is the current
file list, returned in the same format as `get_file_list`.

# Appendix

### Websocket setup
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
websocket's request header.

The following startup sequence is recommened for clients which make use of
the websocket:
1) Attempt to connect to `/websocket` until successful using a timer-like
   mechanism
2) Once connected, query `/printer/info` (or `get_printer_info`) for the ready
   status.
   - If the response returns an error (such as 404), set a timeout for
     2 seconds and try again.
   - If the response returns success, check the result's `is_ready` attribute
     to determine if Klipper is ready.
     - If Klipper is ready you may proceed to request status of printer objects
       make subscriptions, get the file list, etc.
     - If not ready check `error_detected` to see if Klippy has experienced an
       error.
       - If an error is detected it might be wise to prompt the user.  You can
         get a description of the error from the `message` attribute
       - If no error then re-request printer info in 2s.
- Repeat step 2s until Klipper reports ready.  T
- Client's should watch for the `notify_klippy_state_changed` event.  If it reports
  disconnected then Klippy has either been stopped or restarted.  In this
  instance the client should repeat the steps above to determine when
  klippy is ready.