This document keeps a record of all changes to Moonraker's web APIs.

### November 28th 2020
- The following new endpoints are available when the `[update_manager]`
  section has been configured:
  - `GET /machine/update/status`
  - `POST /machine/update/moonraker`
  - `POST /machine/update/klipper`
  - `POST /machine/update/client`
  - `POST /machine/update/system`
- The following endpoint has been added and is available as part of the
  core API:
  - `POST /machine/services/restart`

See [web_api.md](web_api.md) for details on these new endpoints.

### November 23rd 2020
- Moonraker now serves Klipper's "docs" directory.  This can be access
  at `GET /server/files/docs/<filename>`.

### November 19th 2020
- The path for the power APIs has changed from `gpio_power` to `device_power`:
  - `GET /machine/device_power/devices`\
    `{"jsonrpc":"2.0","method":"machine.device_power.devices","id":"1"}`\
    Returns an array of objects listing all detected devices.
    Each object in the array is guaranteed to have the following
    fields:
    - `device`:  The device name
    - `status`:  May be "init", "on", "off", or "error"
    - `type`: May be "gpio" or "tplink_smartplug"
  - `GET /machine/device_power/status?dev_name`\
    `{"jsonrpc":"2.0","method":"machine.device_power.status","id":"1",
    "params":{"dev_name":null}}`\
    It is no longer possible to call this method with no arguments.
    Status will only be returned for the requested device, to get
    status of all devices use `/machine/device_power/devices`.  As
    before, this returns an object in the format of
    `{device_name: status}`, where device_name is the name of the device
    and `status` is the devices current status.
  - `POST /machine/device_power/on?dev_name`\
    `{"jsonrpc":"2.0","method":"machine.device_power.on","id":"1",
    "params":{"dev_name":null}}`\
    Toggles device on.  Returns the current status of the device.
  - `POST /machine/device_power/off?dev_name`\
    `{"jsonrpc":"2.0","method":"machine.device_power.off","id":"1",
    "params":{"dev_name":null}}`\
    Toggles device off.  Returns the current status of the device.
  - The `notify_power_changed` notification now includes an object
    containing device info, matching that which would be recieved
    from a single item in `/machine/power/devices`.

### November 12th 2020
- Two new fields have been added to the gcode metadata:
  - `gcode_start_byte`:  Indicates the byte position in the
    file where the first "Gxx" or "Mxx" command is detected.
  - `gcode_end_byte`:  Indicates the byte position in the
    file where the last "Gxx" or "Mxx" command is detected.
  These fields may be used to more accurately predict print
  progress based on the file size.

### November 11th 2020
- The `server.websocket.id` API has been added.  This returns a
  unique ID that Moonraker uses to track each client connection.
  As such, this API is only available over the websocket, there
  is no complementary HTTP request.
- All HTTP API request may now include arguments in either the
  query string or in the request's body.
- Subscriptions are now managed on a per connection basis.  Each
  connection will only recieve updates for objects in which they
  are currently subscribed.  If an "empty" request is sent, the
  subscription will be cancelled.
- The `POST /printer/object/subscribe` now requires a
  `connection_id` argument.  This is used to identify which
  connection's associated subscription should be updated.
  Currenlty subscriptions are only supported over the a
  websocket connection, one may use the id received from
  `server.websocket.id`.
- The `notify_klippy_ready` websocket notification has been
  added.

### November 2nd 2020
- The `GET /server/files/directory` endpoint now accepts a new
  optional argument, `extended`.  If `extended=true`, then
  the data returned for gcode files will also include extracted
  metadata if it exists.

### October 25th 2020
- The `modified` field reported for files and directories is no
  longer represented as a string.  It is now a floating point
  value representing unix time (in seconds).  This can be used
  to display the "last modified date" based on the client's
  timezone.

### October 21st 2020
- The `/server/gcode_store` endpoint no longer returns a string
  in the result's `gcode_store` field.  It now returns an
  Array of objects, each object containing `message` and `time`
  fields.  The time refers to a timestamp in unix time (seconds),
  and may be used to determine when the gcode store received the
  accompanying `message`.

### September 30th 2020
- Two new endpoints have been added:
  - `GET /server/info` (`server.info`)
  - `GET /server/gcode_store` (`server.gcode_store`)
  See web_api.md for details on their usage.

### September 7th 2020
- A new websocket API has been added, `server.files.delete_file`:
  ```
  {jsonrpc: "2.0", method: "server.files.delete_file", params:
  {path: "<root>/<file_name>"}, id: <request id>}
  ```
  Where <root> is either "gcodes" or "config", and <file_name> is
  the relative path to the file for deletion.  For example:
  `path: "gcodes/my_sub_dir/my_gcode_file.gcode"`


### September 3rd 2020
- The Websocket APIs have changed for clarity.  The APIs methods now
  use namespaces similar to those found in common programming languages.
  This change affects all websocket APIs, however websocket events have
  not changed.  Below is a chart mapping the Previous API to the New API:
  | Previous Websocket Method | New Websocket Method |
  |---------------------------|----------------------|
  | get_printer_info | printer.info |
  | post_printer_emergency_stop | printer.emergency_stop |
  | post_printer_restart | printer.restart |
  | post_printer_firmware_restart | printer.firmware_restart |
  | get_printer_objects_list | printer.objects.list |
  | get_printer_objects_query | printer.objects.query |
  | post_printer_objects_subscribe | printer.objects.subscribe |
  | get_printer_query_endstops_status | printer.query_endstops.status |
  | post_printer_gcode_script | printer.gcode.script |
  | get_printer_gcode_help | printer.gcode.help |
  | post_printer_print_start | printer.print.start |
  | post_printer_print_pause | printer.print.pause |
  | post_printer_print_resume | printer.print.resume |
  | post_printer_print_cancel | printer.print.cancel |
  | post_machine_reboot | machine.reboot |
  | post_machine_shutdown | machine.shutdown |
  | get_server_temperature_store | server.temperature_store |
  | get_file_list | server.files.list |
  | get_file_metadata | server.files.metadata |
  | get_directory | server.files.get_directory |
  | post_directory | server.files.post_directory |
  | delete_directory | server.files.delete_directory |
  | post_file_move | server.files.move |
  | post_file_copy | server.files.copy |
- The "power" plugin APIs have changed.  This affects both HTTP and
  Websocket APIs.  They were originally added to the "/printer" path,
  however this adds the possibility of a naming conflict.  The new
  APIs are as follows:
  - `GET /machine/gpio_power/devices` : `machine.gpio_power.devices`
  - `GET /machine/gpio_power/status` : `machine.gpio_power.status`
  - `POST /machine/gpio_power/on` : `machine.gpio_power.on`
  - `POST /machine/gpio_power/off` : `machine.gpio_power.off`

### September 1st 2020
- A new notification has been added: `notify_metdata_update`.  This
  notification is sent when Moonraker parses metdata from a new upload.
  Note that the upload must be made via the API, files manually (using
  SAMBA, SCP, etc) do not trigger a notification.  The notification is
  sent in the following format:
  ```
  {jsonrpc: "2.0", method: "notify_metadata_update", params: [metadata]}
  ```
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

### August 16th 2020
- The structure of data returned from `/printer/info` (`get_printer_info`)
  has changed to the following format:
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
  The "state" item can be one of the following:
  - "startup" - Klippy is in the process of starting up
  - "ready" - Klippy is ready
  - "shutdown" - Klippy has shutdown
  - "error" - Klippy has experienced an error during startup

  The message from each state can be found in the `state_message`.
- A `webhooks` printer object has been added, available for subscription or
  query. It includes the following items:
  - `state` - Printer state identical to that returned from `/printer/info`
  - `state_message` - identical to that returned from  `/printer/info`
- `/printer/objects/status` (`get_printer_objects_status`) has been renamed to
  `/printer/objects/query` (`get_printer_objects_query`).  The format of the
  websocket request has changed, it should now look like the following:
  ```json
  {
      jsonrpc: "2.0",
      method: "get_printer_objects_query",
      params: {
          objects: {
            gcode: null,
            toolhead: ["position", "status"]
          }
      },
      id: <request id>
  }
  ```
  As shown above, printer objects are now wrapped in an "objects" parameter.
  When a client wishes to subscribe to all items of a printer object, they
  should now be set to `null` rather than an empty array.
  The return value has also changed:
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
  The `status` item now contains the requested status.
- `/printer/objects/subscription` (`post_printer_objects_subscription`) is now
  `printer/objects/subscribe` (`post_printer_objects_subscribe`).  This
  request takes parameters in the same format as the `query`.  It now returns
  state for all currently subscribed objects (in the same format as a `query`).
  This data can be used to initialize all local state after the request
  completes.
- Subscriptions are now pushed as "diffs".  Clients will only recieve updates
  for subscribed items when that data changes.  This requires that clients
  initialize their local state with the data returned from the subscription
  request.
- The structure of data returned from `/printer/objects/list` has changed.  It
  now returns an array of available printer objects:
  ```json
  { objects: ["gcode", "toolhead", "bed_mesh", "configfile",....]}
  ```
- The `notify_klippy_state_changed` notification has been removed.  Clients
  can subscribe to `webhooks` and use `webhooks.state` to be notified of
  transitions to the "ready" and "shutdown" states
- A `notify_klippy_disconnected` event has been added to notify clients
  when the connection between Klippy and Moonraker has been terminated.
  This event is sent with no parameters:
  ```json
  {jsonrpc: "2.0", method: "notify_klippy_disconnected"}
  ```
