This document keeps a record of all changes to Moonraker's remote
facing APIs.

### August 16 2020
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
