#
As mentioned in the API documentation, it is possible to
[query](web_api.md#query-printer-object-status) or
[subscribe](web_api.md#subscribe-to-printer-object-status)
to "Klipper Printer Objects."  There are numerous printer objects in
Klipper, many of which are optional and only report status if they are
enabled by Klipper's configuration.  Client's may retrieve a list of
available printer objects via the
[list objects endpoint](web_api.md#list-available-printer-objects).  This
should be done after Klipper reports its state as "ready".

This section will provide an overview of the most useful printer objects.
If a developer is interested in retrieving state for an object not listed here,
look in Klipper's source code for module you wish to query.  If the module
contains a "get_status()" method, its return value will contain a dictionary
that reports state which can be queried.

## webhooks
```json
{
  "state": "startup",
  "state_message": "message"
}
```
The `webhooks` object contains the current printer state and the current
state message.  These fields match those returned via the `/printer/info`
endpoint.  This is provided as a convience, clients may subscribe to `webhooks`
so they are asynchonously notified of a change to printer state.  The `state`
may be `startup`, `ready`, `shutdown`, or `error`.  The `state_message`
contains a message specific to the current printers state.

## gcode_move
```json
{
    "speed_factor": 1.0,
    "speed": 100.0,
    "extrude_factor": 1.0,
    "absolute_coordinates": true,
    "absolute_extrude": false,
    "homing_origin": [0.0, 0.0, 0.0, 0.0],
    "position": [0.0, 0.0, 0.0, 0.0],
    "gcode_position": [0.0, 0.0, 0.0, 0.0]
}
```
The `gcode_move` object reports the current gcode state:

- `speed_factor`: AKA "feedrate", this is the current speed multiplier
- `speed`: The current gcode speed in mm/s.
- `extrude_factor`: AKA "extrusion multiplier".
- `absolute_coordinates`: true if the machine axes are moved using
  absolute coordinates, false if using relative coordinates.
- `absolute_extrude`: true if the extruder is moved using absolute
  coordinates, false if using relative coordinates.
- `homing_origin`: [X, Y, Z, E] - returns the "gcode offset" applied to
  each axis.  For example, the "Z" axis can be checked to determine how
  much offset has been applied via "babystepping".
- `position`: [X, Y, Z, E] - The internal gcode position, including
   any offsets (gcode_offset, G92, etc) added to an axis.
- `gcode_position`: [X, Y, Z, E] - The current gcode position
  sans any offsets applied.  For X, Y, and Z, this should match
  the most recent "G1" or "G0" processed assuming the machine is
  using absolute coordinates.

!!! Note
    The printer's actual movement will lag behind the reported positional
    coordinates due to lookahead.

## toolhead
```json
{
    "homed_axes": "xyz",
    "print_time": 0.0,
    "estimated_print_time": 0.0,
    "extruder": "extruder",
    "position": [0.0, 0.0, 0.0, 0.0],
    "max_velocity": 500.0,
    "max_accel": 3000.0,
    "max_accel_to_decel": 1500.0,
    "square_corner_velocity": 5.0
}
```
The `toolhead` object reports state of the current tool:

- `homed_axes`: a string containing the axes that are homed. If no axes
  are homed, returns a null string.
- `print_time`: internal value, not generally useful for clients
- `estimated_print_time`: internal value, not generally useful for clients.
- `extruder`: the name of the currently selected extruder, ie "extruder"
  or "extruder1".
- `position`: [X, Y, Z, E] - This the the last position toward which the tool
  was commanded to move.  It includes any offsets applied via gcode as well
  as any transforms made by modules such as "bed_mesh", "bed_tilt", or
  "skew_correction".
- `max_velocity`: The currently set maximum velocity of the tool (mm/s^2).
- `max_accel`:  The currently set maximum acceleration of the tool (mm/s^2).
- `max_accel_to_decel`:  The currently set maximum accel to decel of the tool.
  This value is the maximum rate at which the tool can transition from
  acceleration to deceleration (mm/s^2).
- `square_corner_velocity`: The currently set square corner velocity.  This
  is the maximum velocity at which the tool may travel a 90 degree corner.

!!! tip
    `max_velocity`, `max_accel`, `max_accel_to_decel`, and
    `square_corner_velocity` can be changed by the `SET_VELOCITY_LIMIT` gcode.
    `M204` can also change `max_accel`.

## configfile
```json
{
    "config": {},
    "settings": {},
    "save_config_pending": false
}
```
The `configfile` object reports printer configuration state:

- `config`:  This is an object containing the configuration as read from
  printer.cfg.  Each config section will be an object containing the
  configured options.  Values will ALWAYS be reported as
  strings.  Note that default values are not reported, only options
  configured in printer.cfg are present.
- `settings`:  Similar to `config`, however this object includes default
  values that may not have been included in `printer.cfg`.  It is possible
  for a value to be a string, integer, boolean, or float.
- `save_config_pending`: True if the printer has taken an action which
  has updated the internal configuration (ie: PID calibration, probe
  calibration, bed mesh calibration).  This allows clients to present
  the user with the option to execute a SAVE_CONFIG gcode which will
  save the configuration to printer.cfg and restart the Klippy Host.

## extruder
*Enabled when `[extruder]` is included in printer.cfg*
!!! note
    If multiple extruders are configured, extruder 0 is available as
    `extruder`, extruder 1 as `extruder1` and so on.
```json
{
    "temperature": 0.0,
    "target": 0.0,
    "power": 0.0,
    "pressure_advance": 0.0,
    "smooth_time": 0.0
}
```
The `extruder` object reports state of an extruder:

- `temperature`:  The extruder's current temperature (in C).
- `target`:  The extruder's target temperature (in C).
- `power`: The current pwm value applied to the heater.  This value is
  expressed as a percentage from 0.0 to 1.0.
- `pressure_advance`:  The extruder's current pressure advance value.
- `smooth_time`:  The currently set time range to use when calculating the
  average extruder velocity for pressure advance.

## heater_bed
*Enabled when `[heater_bed]` is included in printer.cfg*
```json
{
    "temperature": 0.0,
    "target": 0.0,
    "power": 0.0,
}
```
The `heater_bed` object reports state of the heated bed:

- `temperature`:  The bed's current temperature
- `target`:  The bed's target temperature
- `power`: The current pwm value applied to the heater.  This value is
  expressed as a percentage from 0.0 to 1.0.

## fan
*Enabled when `[fan]` is included in printer.cfg*
```json
{
    "speed": 0.0,
    "rpm": 4000
}
```
The `fan` object returns state of the part cooling fan:

- `speed`:  The current fan speed.  This is reported as a
  percentage of maximum speed in the range of 0.0 - 1.0.
- `rpm`:  The fan's revolutions per minute if the tachometer
  pin has been configured.  Will report `null` if no tach
  has been configured.

## idle_timeout
```json
{
   "state": "Idle",
   "printing_time": 0.0
}
```

The `idle_timeout` object reports the idle state of the printer:

- `state`: Can be `Idle`, `Ready`, or `Printing`.  The printer will
  transition to the `Printing` state whenever a gcode is issued that
  commands the tool, this includes manual commands.  Thus this should
  not be used to determine if a gcode file print is in progress.  It can
  however be used to determine if the printer is busy.
- `printing_time`:  The amount of time the printer has been in the
  `Printing` state.  This is reset to 0 whenever the printer transitions
  from `Printing` to `Ready`.

## virtual_sdcard
*Enabled when `[virtual_sdcard]` is included in printer.cfg*
```json
{
    "progress": 0.0,
    "is_active": false,
    "file_position": 0
}
```
The `virtual_sdcard` object reports the state of the virtual sdcard:

- `progress`: The print progress reported as a percentage of the file
  read, in the range of 0.0 - 1.0.
- `is_active`: Returns true if the virtual sdcard is currently processing
  a file.  Note that this will return false if a virtual sdcard print is
  paused.
- `file_position`:  The current file position in bytes.  This will always
  be an integer value

!!! Note
    `progress` and `file_position` will persist after a print has
    paused, completed, or errored.  They are cleared when the user issues
    a SDCARD_RESET_FILE gcode or when a new print has started.

## print_stats
*Enabled when `[virtual_sdcard]` is included in printer.cfg*
```json
{
    "filename": "",
    "total_duration": 0.0,
    "print_duration": 0.0,
    "filament_used": 0.0,
    "state": "standby",
    "message": "",
    "info": {
        "total_layer": null,
        "current_layer": null
    }
}
```
The `print_stats` object reports `virtual_sdcard` print state:

- `filename`:  The name of the current file loaded.  This will be a null
  string if no file is loaded.  Note that name is a path relative to the
  gcode folder, thus if the file is located in a subdirectory it would
  be reported as "my_sub_dir/myprint.gcode".
- `total_duration`:  The total time (in seconds) elapsed since a print
  has started. This includes time spent paused.
- `print_duration`:  The total time spent printing (in seconds).  This is
  equivalent to `total_duration` - time paused.
- `filament_used`:  The amount of filament used during the current print
  (in mm).  Any extrusion during a pause is excluded.
- `state`: Current print state.  Can be one of the following values:
    - `"standby"`
    - `"printing"`
    - `"paused"`
    - `"complete"`
    - `"cancelled"`
    - `"error"` - Note that if an error is detected the print will abort
- `message`:  If an error is detected, this field contains the error
  message generated.  Otherwise it will be a null string.
- `info`: This is a dict containing information about the print provided by the
  slicer.  Currently this is limited to the `total_layer` and `current_layer` values.
  Note that these values are set by the
  [SET_PRINT_STATS_INFO](https://www.klipper3d.org/G-Codes.html#set_print_stats_info)
  gcode command.  It is necessary to configure the slicer to include this command
  in the print.  `SET_PRINT_STATS_INFO TOTAL_LAYER=total_layer_count` should
  be called in the slicer's "start gcode" to initalize the total layer count.
  `SET_PRINT_STATS_INFO CURRENT_LAYER=current_layer` should be called in the
  slicer's "on layer change" gcode.  The user must substitute the
  `total_layer_count` and `current_layer` with the appropriate
  "placeholder syntax" for the slicer.

!!! Note
    After a print has started all of the values above will persist until
    the user issues a SDCARD_RESET_FILE gcode or when a new print has started.

## display_status
*Enabled when `[display]` or `[display_status]` is included in printer.cfg*
```json
{
    "message": "",
    "progress": 0.0
}
```
The `display_status` object contains state typically used to update displays:

- `message`:  The message set by a M117 gcode.  If no message is set this will
  be a null string.
- `progress`:  The percentage of print progress, as reported by M73.  This
  will be in the range of 0.0 - 1.0.  If no M73 has been issued this value
  will fallback to the eqivalent of `virtual_sdcard.progress`.  Note that
  progress updated via M73 has a timeout.  If no M73 is received after 5
  seconds, `progress` will be set to the fallback value.

## temperature_sensor sensor_name
*Enabled when `[temperature_sensor sensor_name]` is included in printer.cfg.
It is possible for multiple temperature sensors to be configured.*
```json
{
    "temperature": 0.0,
    "measured_min_temp": 0.0,
    "measured_max_temp": 0.0
}
```
A `temperature_sensor` reports the following state:

- `temperature`:  Sensor's current reported temperature
- `measured_min_temp`: The mimimum temperature read from the sensor
- `measured_max_temp`: The maximum temperature read from the sensor

## temperature_fan fan_name
*Enabled when `[temperature_fan fan_name]` is included in printer.cfg.  It is
possible for multiple temperature fans to be configured.*
```json
{
    "speed": 0.0,
    "temperature": 0.0,
    "target": 0.0
}
```
A `temperature_fan` reports the following state:

- `speed`:  Current fan speed as a percentage of maximum speed, reported
  in the range of 0.0 - 1.0
- `temperature`:  Currently reported temperature of the sensor associated
  with the fan
- `target`:  The current target temperature for the `temperature_fan`.

## filament_switch_sensor sensor_name
*Enabled when `[filament_switch_sensor sensor_name]` is included in
printer.cfg.  It is possible for multiple filament sensors to be configured.*
```json
{
    "filament_detected": false,
    "enabled": true
}
```
A `filament_switch_sensor` reports the following state:

- `filament_detected`:  Set to true if the switch detects filament, otherwise
  false
- `enabled`: Set to true if the sensor is currently enabled, otherwise false

## output_pin pin_name
*Enabled when `[output_pin pin_name]` is included in printer.cfg.
It is possible for multiple output pins to be configured.*
```json
{
    "value": 0.0
}
```
An `output_pin` reports the following state:

- `value`: The currently set value of the pin, in the range of 0.0 - 1.0.
  A digital pin will always be 0 or 1, whereas a pwm pin may report a value
  across the entire range.

## bed_mesh
*Enabled when `[bed_mesh]` is included in printer.cfg.*
```json
{
    "profile_name": "",
    "mesh_min": [0.0, 0.0],
    "mesh_max": [0.0, 0.0],
    "probed_matrix": [[]],
    "mesh_matrix": [[]]
}
```
The `bed_mesh` printer object reports the following state:

- `profile_name`:  The name of the currently loaded profile.  If no profile is
  loaded then this will report a null string.  If the user is not using
  bed_mesh profile management then this will report `default` after mesh
  calibration completes.
- `mesh_min`: [X, Y] - The minimum x and y coordinates of the mesh.
- `mesh_max`: [X, Y] - The maximum x and y coordinates of the mesh.
- `probed_matrix`:  A 2 dimensional array representing the matrix of probed
  values. If the matrix has not been probed the the result is `[[]]`.
- `mesh_matrix`: A 2 dimension array representing the interpolated mesh.  If
  no matrix has been generated the result is `[[]]`.

!!! tip
    See [web_api.md](web_api.md##bed-mesh-coordinates) for an example
    of how to use this information to generate (X,Y,Z) coordinates.

## gcode_macro macro_name
*Enabled when `[gcode_macro macro_name]` is included in printer.cfg.
It is possible for multiple gcode macros to be configured.*

Gcode macros will report the state of configured `variables`.
While user defined macros likely won't report state that is useful
for a client, it is possible for client developers to recommend or
request a specific gcode_macro configuration, then have the client
take action based on the variables reported by the macro.
