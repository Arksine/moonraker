### Moonraker Version 0.1 - August 11 2020
- It is no longer possible to configure the subscription timer.  All subscribed
  objects will update at an interval of 250ms.
- Request timeout configuration has been removed.  The server will no longer
  apply a timeout to requests.  Any requests pending when Klippy disconnects
  will be aborted with an error.  All pending requests are logged each minute.
- The RESET_SD gcode is now SDCARD_RESET_FILE
- The "virtual_sdcard" object has removed the following items:
  - "filename"
  - "total_duration"
  - "print_duration"
  - "filament_used"
- A new object, "print_stats", has been added.  It reports the following items:
  - "filename"
  - "total_duration"
  - "print_duration"
  - "filament_used"
  - "state" - can be one of the following:
    - "standby" - sd print not in progress
    - "printing" - print in progress
    - "paused" - print paused
    - "error" - print experienced an error
    - "complete" - print complete
  - "message" - contains error message when state is "error"
- The behavior of print_stats is slightly different.  When a print is finished the stats are
  not cleared.  They will remain populated with the final data until the user issues a
  SDCARD_RESET_FILE gcode.
- Moonraker Configuration has moved to moonraker.conf
- Klippy now hosts the Unix Domain Socket.  As a result, the order in which the
  Klipper and Moonraker services are started no longer matters.
- The  `notify_filelist_changed` event has been refactored for clarity.  It now
  returns a result in the following format:
  ```json
  {
    action: "<action>",
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
  Note that the `source_item` is only present for `move_item` and `copy_item`
  actions.  Below is a list of all available actions:
  - `upload_file`
  - `delete_file`
  - `create_dir`
  - `delete_dir`
  - `move_item`
  - `copy_item`

### Moonraker Version .08-alpha - 7/2/2020
- Moonraker has moved to its own repo.
- Python 3 support has been added.
- API Key management has moved from Klippy to Moonraker
- File Management has moved from Klippy to Moonraker. All static files are now
  located in the the `/server/files` root path:
  - klippy.log - `/server/files/klippy.log`
  - moonraker.log - `/server/files/moonraker.log`
  - gcode files - `/server/files/gcodes/(.*)`
  Note that the new file manager will be capable of serving and listing files
  in directories aside from "gcodes".
- Added basic plugin support
- Added metadata support for SuperSlicer
- Added thumbnail extraction from SuperSlicer and PrusaSlicer gcode files
- For status requests, `virtual_sdcard.current_file` has been renamed to
  `virtual_sdcard.filename`
- Clients should not send `M112` via gcode to execute an emegency shutdown.
  They should instead use the new API which exposes this functionality.
- New APIs:
  - `POST /printer/emergency_stop` - `post_printer_emergency_stop`
  - `GET /server/files/metadata` - `get_file_metadata`
  - `GET /server/files/directory`
  - `POST /server/files/directory`
  - `DELETE /server/files/directory`
- The following API changes have been made:
  | Previous URI | New URI | Previous JSON_RPC method | New JSON_RPC method |
  |--------------|---------|--------------------------| --------------------|
  | GET /printer/objects | GET /printer/objects/list | get_printer_objects | get_printer_objects_list |
  | GET /printer/subscriptions | GET /printer/objects/subscription | get_printer_subscriptions | get_printer_objects_subscription |
  | POST /printer/subscriptions | POST /printer/objects/subscription | post_printer_subscriptions | post_printer_objects_subscription |
  | GET /printer/status | GET /printer/objects/status | get_printer_status | get_printer_objects_status |
  | POST /printer/gcode | POST /printer/gcode/script | post_printer_gcode | post_printer_gcode_script |
  | GET /printer/klippy.log | GET /server/files/klippy.log | | |
  | GET /server/moonraker.log | GET /server/files/moonraker.log | | |
  | GET /printer/files | GET /server/files/list | get_printer_files | get_file_list |
  | POST /printer/files/upload | POST /server/files/upload | | |
  | GET /printer/files/<filename> | GET /server/files/gcodes/<filename> | | |
  | DELETE /printer/files/<filename> | DELETE /server/files/<filename> | | |
  | GET /printer/endstops | GET /printer/query_endstops/status | get_printer_endstops | get_printer_query_endstops_status |

### Moonraker Version .07-alpha - 5/7/2020
- The server process is no longer managed directly by Klippy.  It has moved
  into its own process dubbed Moonraker.  Please see README.md for
  installation instructions.
- API Changes:
  - `/printer/temperature_store` is now `/server/temperature_store`, or
    `get_server_temperature_store` via the websocket
  - `/printer/log` is now `/printer/klippy.log`
  - `/server/moonraker.log` has been added to fetch the server's log file
- Klippy Changes:
  - The remote_api directory has been removed.  There is now a single
    remote_api.py module that handles server configuration.
  - webhooks.py has been changed to handle communications with the server
  - klippy.py has been changed to pass itself to webhooks
  - file_manager.py has been changed to specifiy the correct status code
    when an error is generated attempting to upload or delete a file
- The nginx configuration will need the following additional section:
  ```
  location /server {
      proxy_pass http://apiserver/server;
      proxy_set_header Host $http_host;
      proxy_set_header X-Real-IP $remote_addr;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Scheme $scheme;
  }
  ```

### Version .06-alpha - 5/4/2020
- Add `/machine/reboot` and `/machine/shutdown` endpoints.  These may be used
  to reboot or shutdown the host machine
- Fix issue where websocket was blocked on long transactions, resulting in the
  connection being closed
- Log all client requests over the websocket
- Add `/printer/temperature_store` endpoint.  Clients may use this to fetch
  stored temperature data.  By default the store for each temperature sensor
  is updated every 1s, with the store holding 20 minutes of data.

### Version .05-alpha - 04/23/2020
- The `[web_server]` module has been renamed to `[remote_api]`.  Please update
  printer.cfg accordingly
- Static files no longer served by the API server.  As a result, there is
  no `web_path` option in `[remote_api]`.
- The server process now now forwards logging requests back to the Klippy
  Host, thus all logging is done in klippy.log.  The temporary endpoint serving
  klippy_server.log has been removed.
- `/printer/info` now includes two additional keys:
  - `error_detected` - Boolean value set to true if a host error has been
    detected
  - `message` - The current Klippy State message.  If an error is detected this
    message may be presented to the user.  This is the same message returned
    when by the STATUS gcode.
- The server process is now launched immediately after the config file is read.
  This allows the client limited access to Klippy in the event of a startup
  error, assuming the config file was successfully parsed and the
  `remote_api` configuration section is valid. Note that when the server is
  initally launched not all endpoints will be available.  The following
  endponts are guaranteed when the server is launched:
  - `/websocket`
  - `/printer/info`
  - `/printer/restart`
  - `/printer/firmware_restart`
  - `/printer/log`
  - `/printer/gcode`
  - `/access/api_key`
  - `/access/oneshot_token`
  The following startup sequence is recommened for clients which make use of
  the websocket:
  - Attempt to connect to `/websocket` until successful
  - Once connected, query `/printer/info` for the ready status.  If not ready
    check `error_detected`.  If not ready and no error, continue querying on
    a timer until the printer is either ready or an error is detected.
  - After the printer has identified itself as ready make subscription requests,
    get the current file list, etc
  - If the websocket disconnects the client can assume that the server is shutdown.
    It should consider the printer's state to be NOT ready and try reconnecting to
    the websocket until successful.

### Version .04-alpha - 04/20/2020
- Add `/printer/gcode/help` endpoint to gcode.py
- Allow the clients to fetch .json files in the root web directory
- Add support for detailed print tracking to virtual_sdcard.py.  This
  includes filament usage and print time tracking
- Add new file_manager.py module for advanced gcode file management. Gcode
  files may exist in subdirectories.  This module also supports extracting
  metadata from gcode files.
- Clean up API registration.  All endpoints are now registered by Klippy
  host modules outside of static files and `/api/version`, which is used for
  compatibility with OctoPrint's file upload API.
- The server now runs in its own process.  Communication between the Host and
  the server is done over a duplex pipe.  Currently this results in a second
  log file being generated specifically for the server at
  `/tmp/klippy_server.log`.  This is likely a temporary solution, and as such
  a temporary endpoint has been added at `/printer/klippy_server.log`.  Users
  can use the browser to download the log by navigating to
  `http://<host>/printer/klippy_server.log`.

### Version .03-alpha - 03/09/2020
- Require that the configured port be above 1024.
- Fix hard crash if the webserver fails to start.
- Fix file uploads with names containing whitespace
- Serve static files based on their relative directory, ie a request
  for "/js/main.js" will now look for the files in "<web_path>/js/main.js".
- Fix bug in CORS where DELETE requests raised an exception
- Disable the server when running Klippy in batch mode
- The the `/printer/cancel`, `/printer/pause` and `/printer/resume` gcodes
  are now registed by the pause_resume module.  This results in the following
  changes:
  - The `cancel_gcode`, `pause_gcode`, and `resume_gcode` options have
    been removed from the [web_server] section.
  - The `/printer/pause` and `/printer/resume` endpoints will run the "PAUSE"
    and "RESUME" gcodes respectively.  These gcodes can be overridden by a
    gcode_macro to run custom PAUSE and RESUME commands.  For example:
    ```
    [gcode_macro PAUSE]
    rename_existing: BASE_PAUSE
    gcode:
      {% if not printer.pause_resume.is_paused %}
        M600
      {% endif %}

    [gcode_macro M600]
    default_parameter_X: 50
    default_parameter_Y: 0
    default_parameter_Z: 10
    gcode:
      SET_IDLE_TIMEOUT TIMEOUT=18000
      {% if not printer.pause_resume.is_paused %}
        BASE_PAUSE
      {% endif %}
      G1 E-.8 F2700
      G91
      G1 Z{Z}
      G90
      G1 X{X} Y{Y} F3000
    ```
    If you are calling "PAUSE" in any other macro of config section, please
    remember that it will execute the macro.  If that is not your intention,
    change "PAUSE" in those sections to the renamed version, in the example
    above it is BASE_PAUSE.
  - The cancel endpoint runs a "CANCEL_PRINT" gcode.  Users will need to
    define their own gcode macro for this
  - Remove "notify_paused_state_changed" and "notify_printer_state_changed"
    events.  The data from these events can be fetched via status
    subscriptions.
  - "idle_timeout" and "pause_resume" now default to tier 1 status updates,
    which sets their default refresh time is 250ms.
  - Some additional status attributes have been added to virtual_sdcard.py.  At
    the moment they are experimental and subject to change:
    - 'is_active' - returns true when the virtual_sdcard is processing.  Note
      that this will return false when the printer is paused
    - 'current_file' - The name of the currently loaded file.  If no file is
      loaded returns an empty string.
    - 'print_duration' - The approximate duration (in seconds) of the current
      print.  This value does not include time spent paused.  Returns 0 when
      no file is loaded.
    - 'total_duration' - The total duration of the current print, including time
      spent paused.  This can be useful for approximating the local time the
      print started  Returns 0 when no file is loaded.
    - 'filament_used' - The approximate amount of filament used.  This does not
      include changes to flow rate.  Returns 0 when no file is loaded.
    - 'file_position' - The current position (in bytes) of the loaded file
       Returns 0 when no file is loaded.
    - 'progress' - This attribute already exists, however it has been changed
      to retain its value while the print is paused.  Previously it would reset
      to 0 when paused.  Returns 0 when no file is loaded.

### Version .02-alpha - 02/27/2020
- Migrated Framework and Server from Bottle/Eventlet to Tornado.  This
  resolves an issue where the server hangs for a period of time if the
  network connection abruptly drops.
- A `webhooks` host module has been created.  Other modules can use this
  the webhooks to register endpoints, even if the web_server is not
  configured.
- Two modules have been renamed, subscription_handler.py is now
  status_handler.py and ws_handler.py is now ws_manager.py.  These names
  more accurately reflect their current functionality.
- Tornado Websockets support string encoded frames.  Thus it is no longer
  necessary for clients to use a FileReader object to convert incoming
  websocket data from a Blob into a String.
- The endpoint for querying endstops has changed from `GET
  /printer/extras/endstops` to `GET /printer/endstops`
- Serveral API changes have been made to accomodate the addition of webhooks:
  - `GET /printer/klippy_info` is now `GET /printer/info`.  This endpoint no
    longer  returns host information, as that can be retrieved direct via the
    `location` object in javascript.  Instead it returns CPU information.
  - `GET /printer/objects` is no longer used to accomodate multiple request
    types by modifying the "Accept" headers.  Each request has been broken
    down in their their own endpoints:
    - `GET /printer/objects` returns all available printer objects that may
      be queried
    - `GET /printer/status?gcode=gcode_position,speed&toolhead` returns the
      status of the printer objects and attribtues
    - `GET /printer/subscriptions` returns all printer objects that are current
      being subscribed to along with their poll times
    - `POST /printer/subscriptions?gcode&toolhead` requests that the printer
      add the specified objects and attributes to the list of subscribed objects
  - Requests that query the Klippy host with additional parameters can no
    longer use variable paths. For example, `POST /printer/gcode/<gcode>` is no
    longer valid.  Parameters must be added to the query string.  This currently
    affects two endpoints:
    - `POST /printer/gcode/<gcode>` is now `POST /printer/gcode?script=<gcode>`
    - `POST printer/print/start/<filename>` is now
      `POST /printer/print/start?filename=<filename>`
  - The websocket API also required changes to accomodate dynamically registered
    endpoints.  Each method name is now generated from its comparable HTTP
    request.  The new method names are listed below:
    | new method | old method |
    |------------|------------|
    | get_printer_files | get_file_list |
    | get_printer_info | get_klippy_info |
    | get_printer_objects | get_object_info |
    | get_printer_subscriptions | get_subscribed |
    | get_printer_status | get_status |
    | post_printer_subscriptions | add_subscription |
    | post_printer_gcode | run_gcode |
    | post_printer_print_start | start_print |
    | post_printer_print_pause | pause_print |
    | post_printer_print_resume | resume_print |
    | post_printer_print_cancel | cancel_print |
    | post_printer_restart | restart |
    | post_printer_firmware_restart | firmware_restart |
    | get_printer_endstops | get_endstops |
  - As with the http API, a change was necessary to the way arguments are send
    along with the request.  Webocket requests should now send "keyword
    arguments" rather than "variable arguments".  The test client has been
    updated to reflect these changes, see main.js and json-rpc.js, specifically
    the new method `call_method_with_kwargs`.  For status requests this simply
    means that it is no longer necessary to wrap the Object in an Array.  The
    gcode and start print requests now look for named parameters, ie:
    - gcode requests - `{jsonrpc: "2.0", method: "post_printer_gcode",
        params: {script: "M117 FooBar"}, id: <request id>}`
    - start print - `{jsonrpc: "2.0", method: "post_printer_print_start",
        params: {filename: "my_file.gcode"}, id:<request id>}`


### Version .01-alpha - 02/14/2020
- The api.py module has been refactored to contain the bottle application and
  all routes within a class.  Bottle is now imported and patched dynamically
  within this class's constructor.  This resolves an issue where the "request"
  context was lost when the Klippy host restarts.
- Change the Websocket API to use the JSON-RPC 2.0 protocol.  See the test
  client (main.js and json-rpc.js) for an example client side implementation.
- Remove file transfer support from the websocket.  Use the HTTP for all file
  transfer requests.
- Add support for Klippy Host modules to register their own urls.
  Query_endstops.py has been updated with an example.  As a result of this
  change, the endpoint for endstop query has been changed to
  `/printer/extras/endstops`.
- Add support for "paused", "resumed", and "cleared" pause events.
- Add routes for downloading klippy.log, restart, and firmware_restart.
- Remove support for trailing slashes in HTTP API routes.
- Support "start print after upload" requests
- Add support for user configured request timeouts
- The test client has been updated to work with the new changes
