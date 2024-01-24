# Printer Administration

These endpoints provide access to printer state and printer control.
Klippy must be connected to Moonraker to receive a successful response,
and in many cases Klippy must be in the `ready` state.

Most of these endpoints are registered with Moonraker by Klipper.
Requests pass from the frontend through Moonraker directly to Klipper.
Moonraker does not intervene.

## Get Klippy host information

```{.http .apirequest title="HTTP Request"}
GET /printer/info
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.info",
    "id": 5445
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "state": "ready",
    "state_message": "Printer is ready",
    "hostname": "pi-debugger",
    "klipper_path": "/home/pi/klipper",
    "python_path": "/home/pi/klipper/venv/bin/python",
    "process_id": 275124,
    "user_id": 1000,
    "group_id": 1000,
    "log_file": "/home/pi/printer_data/logs/klippy.log",
    "config_file": "/home/pi/printer_data/config/printer.cfg",
    "software_version": "v0.12.0-85-gd785b396",
    "cpu_info": "4 core ?"
}
```
////

/// api-response-spec
    open: True
| Field              |  Type  | Description                                           |
| ------------------ | :----: | ----------------------------------------------------- |
| `state`            | string | Klippy's current [state](#klippy-state-desc).         |
| `state_message`    | string | A message describing Klippy's current state.          |
| `hostname`         | string | Hostname of the machine running Klippy.               |
| `klipper_path`     | string | Path on disk to the Klipper application.              |
| `python_path`      | string | Path on disk to the Python executable running Klippy. |
| `process_id`       |  int   | The PID of the current Klippy process.                |
| `user_id`          |  int   | The UID of the user the Klippy process belongs to.    |
| `group_id`         |  int   | The GID of the group the Klippy process belongs to.   |
| `log_file`         | string | Path on disk to Klipper's log file.                   |
| `configfile`       | string | Path on disk to Klipper's configuration file.         |
| `software_version` | string | Version of the currently running instance of Klipper. |
| `cpu_info`         | string | A brief description of the host machine's CPU.        |

| State      | Description                                             |
| ---------- | ------------------------------------------------------- |
| `ready`    | Klippy has initialized and is ready for commands.       |
| `startup`  | Klippy is currently in its startup phase.               |
| `error`    | Klippy encountered an error during startup.             |
| `shutdown` | Klippy is in the shutdown state.  This can be initiated |
|            | by the user via an emergency stop, or by the software   |^
|            | if it encounters a critical error during operation.     |^
{ #klippy-state-desc } Klippy State
///

## Emergency Stop

```{.http .apirequest title="HTTP Request"}
POST /printer/emergency_stop
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.emergency_stop",
    "id": 4564
}
```

/// Note
This endpoint will immediately halt the printer and put it in a "shutdown"
state.  It should be used to implement an "emergency stop" button and
also used if a user enters `M112`(emergency stop) via a console.
///


```{.text .apiresponse title="Response"}
"ok"
```

## Host Restart

Requests a Klipper "soft" restart.  This will reload the Klippy application
and configuration.  Connected MCUs will not be reset.

```{.http .apirequest title="HTTP Request"}
POST /printer/restart
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.restart",
    "id": 4894
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Firmware Restart

Requests a complete Klipper restart.  Both the Klippy Application and connected
MCUs will be reset.

```{.http .apirequest title="HTTP Request"}
POST /printer/firmware_restart
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.firmware_restart",
    "id": 8463
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Printer Status Requests

### List loaded printer objects

Returns a list of Klipper `printer objects` that are currently loaded.
This can be used to determine if a specific object is available for query
and/or subscription.

```{.http .apirequest title="HTTP Request"}
GET /printer/objects/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.objects.list",
    "id": 1454
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "objects": [
        "gcode",
        "webhooks",
        "configfile",
        "mcu",
        "mcu linux",
        "heaters",
        "bme280 chamber",
        "temperature_sensor chamber",
        "filament_switch_sensor extruder_sensor",
        "output_pin sensor_toggle",
        "gcode_move",
        "bed_mesh",
        "exclude_object",
        "temperature_host RPi",
        "temperature_sensor RPi",
        "gcode_macro TURN_OFF_MOTORS",
        "gcode_macro SET_HOMING_CURRENT",
        "temperature_sensor ambient",
        "gcode_macro query_bme280",
        "pause_resume",
        "print_stats",
        "virtual_sdcard",
        "probe",
        "stepper_enable",
        "tmc2130 stepper_x",
        "tmc2130 stepper_y",
        "tmc2130 stepper_z",
        "tmc2130 extruder",
        "heater_bed",
        "heater_fan nozzle_cooling_fan",
        "fan",
        "menu",
        "display_status",
        "output_pin BEEPER_pin",
        "idle_timeout",
        "motion_report",
        "query_endstops",
        "system_stats",
        "manual_probe",
        "toolhead",
        "extruder"
    ]
}
```
////

/// api-response-spec
    open: True
| Field     |   Type   | Description                                      |
| --------- | :------: | ------------------------------------------------ |
| `objects` | [string] | A list Klipper printer objects currently loaded. |
///

### Query printer object status

Requests the status of a provided set of printer objects.

/// Tip
See the [Printer Objects](../printer_objects.md) document
for details on the objects available for query.
///

```{.http .apirequest title="HTTP Request"}
POST /printer/objects/query
Content-Type: application/json

{
    "objects": {
        "gcode_move": null,
        "toolhead": ["position", "status"]
    }
}
```

/// details | Using the Query String
The HTTP Request may also be performed using the query string.  It is
recommended to send the request in the body unless otherwise not possible.

```{.http .apirequest title="HTTP Request"}
GET /printer/objects/query?gcode_move&toolhead&extruder=target,temperature
```

The above will request a status update for all `gcode_move` and `toolhead`
attributes.  Only the `temperature` and `target` attributes are requested
for the `extruder`.
///


```{.json .apirequest title="JSON-RPC Request"}
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

/// api-parameters
    open: True
| Name      |  Type  | Default      | Description                                            |
| --------- | :----: | ------------ | ------------------------------------------------------ |
| `objects` | object | **REQUIRED** | An object whose key, value pairs represent one or      |
|           |        |              | more [Printer Object Requests](#printer-obj-req-desc). |^

| Key Description                 | Value Description                                           |
| ------------------------------- | ----------------------------------------------------------- |
| The `key`should be an available | The `value` specifies the attributes of the object that     |
| Klipper printer object.         | should be returned.  If the value is `null`  all attributes |^
| { width=40% }                   | will be returned.  Alternatively a list of strings          |^
|                                 | specifying the desired attributes can be provided.          |^
{ #printer-obj-req-desc } Printer Object Request

//// Note
If a requested printer object or attribute does not exist then the result
will be omitted from the response.  No error is returned.
////

///

//// collapse-code
```{.json .apiresponse title="Example Response"}
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
            "speed_factor": 1
        },
        "toolhead": {
            "position": [0, 0, 0, 0],
            "status": "Ready"
        }
    }
}
```
////

/// api-response-spec
    open: True
| Field       |  Type  | Description                                                    |
| ----------- | :----: | -------------------------------------------------------------- |
| `eventtime` | float  | The time at which the status was received, according Klipper's |
|             |        | monotonic clock.                                               |^
| `status`    | object | An object containing the current state of the requested        |
|             |        | printer objects.                                               |^
{ #object-query-response-spec }
///

### Subscribe to printer object status updates

Requests status updates for a set of printer objects.  A persistent
connection (Websocket or Unix Socket) is required to fulfill this
request.

Status updates for subscribed objects are sent asynchronously over the
connection.  See the
[notify_status_update](./jsonrpc_notifications.md#subscription-updates)
notification for details.

/// Tip
See the [Printer Objects](../printer_objects.md) document
for details on the objects available for subscription.
///

```{.text .apirequest title="HTTP Request"}
Not available
```

```{.json .apirequest title="JSON-RPC Request (Websocket and Unix Socket Only)"}
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

/// api-parameters
    open: True
Parameters are identical to the [query](#query-printer-object-status)
status parameters.  A new request will override a previous request.
If `objects` is set to an empty object then the subscription will be
cancelled.
///

//// collapse-code
```{.json .apiresponse title="Example Response"}
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
////

/// api-response-spec
    open: True
The response spec is identical to the [query response specification](#object-query-response-spec)
The response may be used to initialize local state without performing a
separate query.
///

### Query Endstops

```{.http .apirequest title="HTTP Request"}
GET /printer/query_endstops/status
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.query_endstops.status",
    "id": 3456
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "x": "TRIGGERED",
    "y": "open",
    "z": "open"
}
```
////

/// api-response-spec
    open: True
| Field      |  Type  | Description                                                 |
| ---------- | :----: | ----------------------------------------------------------- |
| *variable* | string | The field is the name of the registered endstop.  The value |
|            |        | will be `open` or `TRIGGERED`.                              |^
///

## GCode APIs

### Run a gcode command

Executes a gcode command.  Multiple commands may be executed by separating
them with a newline (`\n`).  The request returns when the command or series
of commands have completed, or when the command results in an error.

/// warning
When `M112`(emergency stop) is requested via this endpoint it will not
immediately stop the printer. `M112` will be placed on the gcode queue and
executed after all previous gcodes are complete.  If a frontend detects
`M112` via user input (such as a console) it should request the
`/printer/emergency_stop` endpoint to immediately halt the printer.  This
may be done in addition to sending the `M112` gcode if desired.
///

```{.http .apirequest title="HTTP Request"}
POST /printer/gcode/script
Content-Type: application/json

{
    "script": "G28"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.gcode.script",
    "params": {
        "script": "G28"
    },
    "id": 7466}
```

/// api-parameters
    open: True
| Name     |  Type  | Default      | Description                                       |
| -------- | :----: | ------------ | ------------------------------------------------- |
| `script` | string | **REQUIRED** | A GCode Command to run.  Multiple commands may be |
|          |        |              | specified, separated by a newline (`\n`).         |^
///

```{.text .apiresponse title="Response"}
"ok"
```

### Get GCode Help

Retrieves a list of registered GCode Command Descriptions.  Not all registered
GCode commands have a description, so this list should not be treated as an
exhaustive list of all supported commands.

```{.http .apirequest title="HTTP Request"}
GET /printer/gcode/help
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.gcode.help",
    "id": 4645
}
```

//// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "RESTART": "Reload config file and restart host software",
    "FIRMWARE_RESTART": "Restart firmware, host, and reload config",
    "STATUS": "Report the printer status",
    "HELP": "Report the list of available extended G-Code commands",
    "SAVE_CONFIG": "Overwrite config file and restart",
    "SHUTDOWN_MACHINE": "G-Code macro",
    "SET_GCODE_VARIABLE": "Set the value of a G-Code macro variable",
    "REBOOT_MACHINE": "G-Code macro",
    "UPDATE_DELAYED_GCODE": "Update the duration of a delayed_gcode",
    "TURN_OFF_HEATERS": "Turn off all heaters",
    "TEMPERATURE_WAIT": "Wait for a temperature on a sensor",
    "QUERY_ADC": "Report the last value of an analog pin",
    "QUERY_FILAMENT_SENSOR": "Query the status of the Filament Sensor",
    "SET_FILAMENT_SENSOR": "Sets the filament sensor on/off",
    "SET_PIN": "Set the value of an output pin",
    "BED_MESH_CALIBRATE": "Perform Mesh Bed Leveling",
    "BED_MESH_PROFILE": "Bed Mesh Persistent Storage management",
    "BED_MESH_OUTPUT": "Retrieve interpolated grid of probed z-points",
    "BED_MESH_MAP": "Serialize mesh and output to terminal",
    "BED_MESH_CLEAR": "Clear the Mesh so no z-adjustment is made",
    "BED_MESH_OFFSET": "Add X/Y offsets to the mesh lookup",
    "SET_GCODE_OFFSET": "Set a virtual offset to g-code positions",
    "SAVE_GCODE_STATE": "Save G-Code coordinate state",
    "RESTORE_GCODE_STATE": "Restore a previously saved G-Code state",
    "GET_POSITION": "Return information on the current location of the toolhead",
    "EXCLUDE_OBJECT_START": "Marks the beginning the current object as labeled",
    "EXCLUDE_OBJECT_END": "Marks the end the current object",
    "EXCLUDE_OBJECT": "Cancel moves inside a specified objects",
    "EXCLUDE_OBJECT_DEFINE": "Provides a summary of an object",
    "TURN_OFF_MOTORS": "G-Code macro",
    "CLEAR_PAUSE": "Clears the current paused state without resuming the print",
    "SET_PRINT_STATS_INFO": "Pass slicer info like layer act and total to klipper",
    "SDCARD_RESET_FILE": "Clears a loaded SD File. Stops the print if necessary",
    "SDCARD_PRINT_FILE": "Loads a SD file and starts the print.  May include files in subdirectories.",
    "RESPOND": "Echo the message prepended with a prefix",
    "PROBE": "Probe Z-height at current XY position",
    "QUERY_PROBE": "Return the status of the z-probe",
    "PROBE_CALIBRATE": "Calibrate the probe's z_offset",
    "PROBE_ACCURACY": "Probe Z-height accuracy at current XY position",
    "Z_OFFSET_APPLY_PROBE": "Adjust the probe's z_offset",
    "GET_CURRENT_SKEW": "Report current printer skew",
    "CALC_MEASURED_SKEW": "Calculate skew from measured print",
    "SET_SKEW": "Set skew based on lengths of measured object",
    "SKEW_PROFILE": "Profile management for skew_correction",
    "SET_STEPPER_ENABLE": "Enable/disable individual stepper by name",
    "SET_TMC_FIELD": "Set a register field of a TMC driver",
    "INIT_TMC": "Initialize TMC stepper driver registers",
    "SET_TMC_CURRENT": "Set the current of a TMC driver",
    "DUMP_TMC": "Read and display TMC stepper driver registers",
    "PID_CALIBRATE": "Run PID calibration test",
    "SET_HEATER_TEMPERATURE": "Sets a heater temperature",
    "SET_DISPLAY_TEXT": "Set or clear the display message",
    "SET_DISPLAY_GROUP": "Set the active display group",
    "STEPPER_BUZZ": "Oscillate a given stepper to help id it",
    "FORCE_MOVE": "Manually move a stepper; invalidates kinematics",
    "SET_KINEMATIC_POSITION": "Force a low-level kinematic position",
    "SET_IDLE_TIMEOUT": "Set the idle timeout in seconds",
    "QUERY_ENDSTOPS": "Report on the status of each endstop",
    "SET_VELOCITY_LIMIT": "Set printer velocity limits",
    "MANUAL_PROBE": "Start manual probe helper script",
    "TUNING_TOWER": "Tool to adjust a parameter at each Z height",
    "SET_PRESSURE_ADVANCE": "Set pressure advance parameters",
    "SET_EXTRUDER_ROTATION_DISTANCE": "Set extruder rotation distance",
    "SYNC_EXTRUDER_MOTION": "Set extruder stepper motion queue",
    "SET_EXTRUDER_STEP_DISTANCE": "Set extruder step distance",
    "SYNC_STEPPER_TO_EXTRUDER": "Set extruder stepper",
    "ACTIVATE_EXTRUDER": "Change the active extruder",
    "BASE_PAUSE": "Renamed builtin of 'PAUSE'",
    "BASE_RESUME": "Renamed builtin of 'RESUME'",
    "BASE_CANCEL_PRINT": "Renamed builtin of 'CANCEL_PRINT'",
    "ACCEPT": "Accept the current Z position",
    "ABORT": "Abort manual Z probing tool",
    "TESTZ": "Move to new Z height"
}
```
////

/// api-response-spec
    open: True
| Field      |  Type  | Description                                                       |
| ---------- | :----: | ----------------------------------------------------------------- |
| *variable* | string | The field is the name of the registered gcode command.  The value |
|            |        | is a string containing the associated help descriptions.          |^

//// Note
As mentioned previously, this list is not exhaustive.  Help strings are not
available for default gcode handlers such as G1, G28, etc, nor are they
available for extended handlers that failed to register a description in
Klipper's python source.
////

///

## Print Job Management

### Start a print job
```{.http .apirequest title="HTTP Request"}
POST /printer/print/start?filename=test_print.gcode
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.print.start",
    "params": {
        "filename": "test_print.gcode"
    },
    "id": 4654
}
```

/// api-parameters
    open: true
| Name       |  Type  | Default      | Description                                         |
| ---------- | :----: | ------------ | --------------------------------------------------- |
| `filename` | string | **REQUIRED** | The name of the gcode file to print.  May be a path |
|            |        |              | relative to the gcode folder.                       |^
///

```{.text .apiresponse title="Response"}
"ok"
```

### Pause a print job
```{.http .apirequest title="HTTP Request"}
POST /printer/print/pause
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.print.pause",
    "id": 4564
}
```

```{.text .apiresponse title="Response"}
"ok"
```

### Resume a print job

```{.http .apirequest title="HTTP Request"}
POST /printer/print/resume
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.print.resume",
    "id": 1465
}
```

```{.text .apiresponse title="Response"}
"ok"
```

### Cancel a print job

```{.http .apirequest title="HTTP Request"}
POST /printer/print/cancel
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "printer.print.cancel",
    "id": 2578
}
```

```{.text .apiresponse title="Response"}
"ok"
```
