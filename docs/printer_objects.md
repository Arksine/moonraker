# Printer Objects

/// Note
For the most complete and up to date list of Klipper printer objects
available for query please see
[Klipper's Status Reference](https://www.klipper3d.org/Status_Reference.html).
The objects outlined in this document are a subset of all objects available.  In
addition it is possible that the object specifications here are out of date
relative to the latest commit pushed to Klipper's GitHub repo.

The Printer Object Specifications in this document are current as of
Klipper Version `v0.12.0-430-g329fbd01d`.
///

As mentioned in the API documentation, it is possible to
[query](./external_api/printer.md#query-printer-object-status) or
[subscribe](./external_api/printer.md#subscribe-to-printer-object-status-updates)
to "Klipper Printer Objects."  There are numerous printer objects in
Klipper, many of which are optional and only report status if they are
enabled by Klipper's configuration.  Clients may retrieve a list of
available printer objects via the
[list objects endpoint](./external_api/printer.md#list-loaded-printer-objects).
This should be done after Klipper reports its state as "ready".

/// Tip
There may be printer objects not documented here or in Klipper's
Status Reference.  Developers interested in the state for such
objects will need to view Klippy's Python source to determine the nature
of the values reported.

Look for a `get_status()` class method.  The returned `dict` will indicate
the fields reported by the object.
///

/// Tip
Positional coordinates are expressed as 4 element float arrays.
The format is [X, Y, Z, E].
///

## webhooks

/// warning
Websocket and Unix Socket subscribers to the `webhooks` object
should not rely on it for asynchronous `startup`, `ready`, or
`error` state updates. By the time Moonraker has established
a connection to Klipper it is possible that the `webhooks`
state is already beyond the startup phase.

MQTT subscriptions will publish the first `state` detected
after Klippy exits the `startup` phase.
///

```{.json title="Printer Object Example"}
{
  "state": "startup",
  "state_message": "message"
}
```

| Field           |  Type  | Description                                                      |
| --------------- | :----: | ---------------------------------------------------------------- |
| `state`         | string | The current [state](./external_api/printer.md#klippy-state-desc) |
|                 |        | reported by Klipper.                                             |^
| `state_message` | string | A message describing current state.                              |
{ #webhooks-object-spec } Webhooks Object

## motion_report

```{.json title="Printer Object Example"}
{
    "live_position": [0, 0, 0, 0],
    "live_velocity": 0,
    "live_extruder_velocity": 0,
    "steppers": [
        "extruder",
        "stepper_x",
        "stepper_y",
        "stepper_z"
    ],
    "trapq": [
        "extruder",
        "toolhead"
    ]
}
```

| Field                    |   Type   | Description                                   |
| ------------------------ | :------: | --------------------------------------------- |
| `live_position`          | [float]  | The estimated real world position of the tool |
|                          |          | at the time of the query.                     |^
| `live_velocity`          |  float   | The estimated real world velocity of the tool |
|                          |          | at the time of the query.                     |^
| `live_extruder_velocity` |  float   | The estimated real world velocity of the      |
|                          |          | active extruder at the time of the query.     |^
| `steppers`               | [string] | An array of registered stepper names.         |
| `trapq`                  | [string] | An array of registered trapq objects.         |
{ #motion-report-object-spec } Motion Report Object

## gcode_move

```{.json title="Printer Object Example"}
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

| Field                  |  Type   | Description                                   |
| ---------------------- | :-----: | --------------------------------------------- |
| `speed_factor`         |  float  | A speed multiplier applied to the move. Also  |
|                        |         | known as "feedrate percentage".               |^
| `speed`                |  float  | Speed of the most recently processed gcode    |
|                        |         | move command in mm/s.                         |^
| `extruder_factor`      |  float  | An extrusion multiplier applied to the move.  |
| `absolute_coordinates` |  bool   | True if the move is in absolute coordinates,  |
|                        |         | false if the move is relative.                |^
| `absolute_extrude`     |  bool   | True if the extrusion move is in absolute     |
|                        |         | coordinates, false if relative.               |^
| `homing_origin`        | [float] | A coordinate representing the amount of gcode |
|                        |         | offset applied to each axis.                  |^
| `position`             | [float] | The current position after offsets are        |
|                        |         | applied.                                      |^
| `gcode_position`       | [float] | The current position without any offsets.     |
{ #gcode-move-object-spec } GCode Move Object

/// Note
The printer's actual movement will lag behind the reported positional
coordinates due to lookahead.
///

## toolhead
```{.json title="Printer Object Example"}
{
    "homed_axes": "xyz",
    "axis_minimum": [0, -4, -2, 0],
    "axis_maximum": [250, 210, 220, 0],
    "print_time": 0.25,
    "stalls": 0,
    "estimated_print_time": 0,
    "extruder": "extruder",
    "position": [0, 0, 0, 0],
    "max_velocity": 300,
    "max_accel": 1500,
    "minimum_cruise_ratio": 0.5,
    "square_corner_velocity": 5
}
```

| Field                    |  Type   | Description                                      |
| ------------------------ | :-----: | ------------------------------------------------ |
| `homed_axes`             | string  | The current homed axes.  Will be an empty string |
|                          |         | if no axes are homed.                            |^
| `axis_minimum`           | [float] | A coordinate indicating the minimum valid move   |
|                          |         | location.                                        |^
| `axis_maximum`           | [float] | A coordinate indicating the maximum valid move   |
|                          |         | location.                                        |^
| `cone_start_z`           |  float  | Available for Delta printers only.  The value is |
|                          |         | the maximum z height at the maximum radius.      |^
| `print_time`             |  float  | An internal value Klipper uses for scheduling    |
|                          |         | commands.                                        |^
| `stalls`                 |   int   | The total number of times since the last restart |
|                          |         | that the printer had to pause because it ran out |^
|                          |         | of buffered G-Code.                              |^
| `estimated_print_time`   |  float  | An internal value Klipper uses for scheduling    |
|                          |         | commands.                                        |^
| `extruder`               | string  | The name of the currently selected extruder.     |
| `position`               | [float] | A coordinate indicating the commanded position   |
|                          |         | of the toolhead.                                 |^
| `max_velocity`           |  float  | The current maximum velocity limit.              |
| `max_accel`              |  float  | The current maximum acceleration limit.          |
| `minimum_cruise_ratio`   |  float  | The current minimum cruise ratio.  This ratio    |
|                          |         | enforces the minimum portion of a move that      |^
|                          |         | must occur at cruising speed.                    |^
| `square_corner_velocity` |  float  | The current square corner velocity. This is the  |
|                          |         | maximum velocity the tool may travel a 90        |^
|                          |         | degree corner.                                   |^
{ #toolhead-object-spec } Toolhead Object

/// tip
The `max_velocity`, `max_accel`, `minimum_cruise_ratio`, and
`square_corner_velocity` can be changed by the `SET_VELOCITY_LIMIT`
gcode command. Their default values may be configured in the `[printer]`
section of Klipper's `printer.cfg`.
///

## configfile

/// collapse-code
```{.json title="Printer Object Example"}
{
    "config": {
        "mcu": {
            "serial": "/dev/serial/by-id/usb"
        },
        "exclude_object": {},
        "printer": {
            "kinematics": "cartesian",
            "max_velocity": "300",
            "max_accel": "1500",
            "max_z_velocity": "15",
            "max_z_accel": "200"
        },
        "stepper_x": {
            "microsteps": "16",
            "step_pin": "PC0",
            "dir_pin": "!PL0",
            "enable_pin": "!PA7",
            "rotation_distance": "32",
            "endstop_pin": "!PK2",
            "position_endstop": "0",
            "position_min": "0",
            "position_max": "250",
            "homing_speed": "50",
            "homing_retract_dist": "0"
        },
        "stepper_y": {
            "microsteps": "16",
            "step_pin": "PC1",
            "dir_pin": "PL1",
            "enable_pin": "!PA6",
            "rotation_distance": "32",
            "endstop_pin": "!PK7",
            "position_endstop": "-4",
            "position_max": "210",
            "position_min": "-4",
            "homing_speed": "50",
            "homing_retract_dist": "0"
        },
        "stepper_z": {
            "microsteps": "16",
            "step_pin": "PC2",
            "dir_pin": "!PL2",
            "enable_pin": "!PA5",
            "rotation_distance": "8",
            "endstop_pin": "probe:z_virtual_endstop",
            "position_max": "220",
            "position_min": "-2",
            "homing_speed": "13.333"
        },
        "extruder": {
            "microsteps": "8",
            "step_pin": "PC3",
            "dir_pin": "PL6",
            "enable_pin": "!PA4",
            "rotation_distance": "6.53061216",
            "full_steps_per_rotation": "400",
            "nozzle_diameter": "0.4",
            "filament_diameter": "1.750",
            "max_extrude_cross_section": "50.0",
            "max_extrude_only_distance": "500.0",
            "max_extrude_only_velocity": "120.0",
            "max_extrude_only_accel": "1250.0",
            "heater_pin": "PE5",
            "sensor_type": "ATC Semitec 104GT-2",
            "sensor_pin": "PF0",
            "control": "pid",
            "pid_kp": "16.13",
            "pid_ki": "1.1625",
            "pid_kd": "56.23",
            "min_temp": "0",
            "max_temp": "305"
        },
        "heater_bed": {
            "heater_pin": "PG5",
            "sensor_type": "EPCOS 100K B57560G104F",
            "sensor_pin": "PF2",
            "control": "pid",
            "pid_kp": "126.13",
            "pid_ki": "4.3",
            "pid_kd": "924.76",
            "min_temp": "0",
            "max_temp": "125"
        },
        "verify_heater heater_bed": {
            "max_error": "240",
            "check_gain_time": "120"
        },
        "heater_fan nozzle_cooling_fan": {
            "pin": "PH5",
            "heater": "extruder",
            "heater_temp": "50.0"
        },
        "fan": {
            "pin": "PH3"
        },
        "display": {
            "lcd_type": "hd44780",
            "rs_pin": "PD5",
            "e_pin": "PF7",
            "d4_pin": "PF5",
            "d5_pin": "PG4",
            "d6_pin": "PH7",
            "d7_pin": "PG3",
            "encoder_pins": "^PJ1,^PJ2",
            "click_pin": "^!PH6"
        },
        "pause_resume": {},
        "virtual_sdcard": {
            "path": "~/printer_data/gcodes"
        },
        "respond": {
            "default_type": "command"
        },
        "probe": {
            "pin": "PB4",
            "x_offset": "23",
            "y_offset": "5",
            "z_offset": "0.8",
            "speed": "12.0"
        },
        "bed_mesh": {
            "speed": "140",
            "horizontal_move_z": "2",
            "mesh_min": "24, 6",
            "mesh_max": "238, 210",
            "probe_count": "7",
            "mesh_pps": "2",
            "fade_start": "1",
            "fade_end": "10",
            "fade_target": "0",
            "move_check_distance": "15",
            "algorithm": "bicubic",
            "bicubic_tension": ".2",
            "zero_reference_position": "154.0, 113.0",
            "faulty_region_1_min": "116.75, 41.81",
            "faulty_region_1_max": "133.25, 78.81",
            "faulty_region_2_min": "156.5, 99.31",
            "faulty_region_2_max": "193.5, 115.81",
            "faulty_region_3_min": "116.75, 136.21",
            "faulty_region_3_max": "133.25, 173.31"
        },
        "homing_override": {
            "gcode": "\nG1 Z3 F600\nG28 X0 Y0\nG1 X131 Y108 F5000\nG28 Z0",
            "axes": "Z",
            "set_position_x": "0",
            "set_position_y": "0",
            "set_position_z": "0"
        },
        "output_pin BEEPER_pin": {
            "pin": "PH2",
            "pwm": "True",
            "value": "0",
            "shutdown_value": "0",
            "cycle_time": "0.001",
            "scale": "1000"
        },
        "force_move": {
            "enable_force_move": "True"
        },
        "idle_timeout": {
            "gcode": "\nM104 S0\nM84"
        },
        "gcode_macro PAUSE": {
            "rename_existing": "BASE_PAUSE",
            "gcode": "\n{% if not printer.pause_resume.is_paused %}\nM600\n{% endif %}"
        },
        "gcode_macro M600": {
            "variable_extr_temp": "0",
            "gcode": "\n{% set X = params.X|default(100) %}\n{% set Y = params.Y|default(100) %}\n{% set Z = params.Z|default(100) %}\nBASE_PAUSE\nSET_GCODE_VARIABLE MACRO=M600 VARIABLE=extr_temp VALUE={printer.extruder.target}\nG91\n{% if printer.extruder.temperature|float > 180 %}\nG1 E-.8 F2700\n{% endif %}\nG1 Z{Z}\nG90\nG1 X{X} Y{Y} F3000"
        },
        "gcode_macro RESUME": {
            "rename_existing": "BASE_RESUME",
            "gcode": "\n{% if printer.pause_resume.is_paused %}\n{% if printer[\"gcode_macro M600\"].extr_temp %}\nM109 S{printer[\"gcode_macro M600\"].extr_temp}\n{% endif %}\nBASE_RESUME\n{% endif %}"
        }
    },
    "warnings": [],
    "save_config_pending": false,
    "save_config_pending_items": {},
    "settings": {
        "mcu": {
            "serial": "/dev/serial/by-id/usb",
            "baud": 250000,
            "max_stepper_error": 0.000025
        },
        "heater_bed": {
            "sensor_type": "EPCOS 100K B57560G104F",
            "pullup_resistor": 4700,
            "inline_resistor": 0,
            "sensor_pin": "PF2",
            "min_temp": 0,
            "max_temp": 125,
            "min_extrude_temp": 170,
            "max_power": 1,
            "smooth_time": 1,
            "control": "pid",
            "pid_kp": 126.13,
            "pid_ki": 4.3,
            "pid_kd": 924.76,
            "heater_pin": "PG5",
            "pwm_cycle_time": 0.1
        },
        "verify_heater heater_bed": {
            "hysteresis": 5,
            "max_error": 240,
            "heating_gain": 2,
            "check_gain_time": 120
        },
        "heater_fan nozzle_cooling_fan": {
            "heater": [
                "extruder"
            ],
            "heater_temp": 50,
            "max_power": 1,
            "kick_start_time": 0.1,
            "off_below": 0,
            "cycle_time": 0.01,
            "hardware_pwm": false,
            "shutdown_speed": 1,
            "pin": "PH5",
            "fan_speed": 1
        },
        "fan": {
            "max_power": 1,
            "kick_start_time": 0.1,
            "off_below": 0,
            "cycle_time": 0.01,
            "hardware_pwm": false,
            "shutdown_speed": 0,
            "pin": "PH3"
        },
        "display": {
            "lcd_type": "hd44780",
            "rs_pin": "PD5",
            "e_pin": "PF7",
            "d4_pin": "PF5",
            "d5_pin": "PG4",
            "d6_pin": "PH7",
            "d7_pin": "PG3",
            "hd44780_protocol_init": true,
            "line_length": 20,
            "menu_root": "__main",
            "menu_timeout": 0,
            "menu_reverse_navigation": false,
            "encoder_pins": "^PJ1,^PJ2",
            "encoder_steps_per_detent": 4,
            "encoder_fast_rate": 0.03,
            "click_pin": "^!PH6",
            "display_group": "_default_20x4"
        },
        "pause_resume": {
            "recover_velocity": 50
        },
        "virtual_sdcard": {
            "path": "~/printer_data/gcodes",
            "on_error_gcode": "\n{% if 'heaters' in printer %}\n   TURN_OFF_HEATERS\n{% endif %}\n"
        },
        "respond": {
            "default_type": "command",
            "default_prefix": "//"
        },
        "probe": {
            "z_offset": 0.8,
            "deactivate_on_each_sample": true,
            "activate_gcode": "",
            "deactivate_gcode": "",
            "pin": "PB4",
            "x_offset": 23,
            "y_offset": 5,
            "speed": 12,
            "lift_speed": 12,
            "samples": 1,
            "sample_retract_dist": 2,
            "samples_result": "average",
            "samples_tolerance": 0.1,
            "samples_tolerance_retries": 0
        },
        "bed_mesh": {
            "adaptive_margin": 0,
            "probe_count": [
                7
            ],
            "mesh_min": [
                24,
                6
            ],
            "mesh_max": [
                238,
                210
            ],
            "mesh_pps": [
                2
            ],
            "algorithm": "bicubic",
            "bicubic_tension": 0.2,
            "scan_overshoot": 0,
            "zero_reference_position": [
                154,
                113
            ],
            "horizontal_move_z": 2,
            "speed": 140,
            "faulty_region_1_min": [
                116.75,
                41.81
            ],
            "faulty_region_1_max": [
                133.25,
                78.81
            ],
            "faulty_region_2_min": [
                156.5,
                99.31
            ],
            "faulty_region_2_max": [
                193.5,
                115.81
            ],
            "faulty_region_3_min": [
                116.75,
                136.21
            ],
            "faulty_region_3_max": [
                133.25,
                173.31
            ],
            "fade_start": 1,
            "fade_end": 10,
            "fade_target": 0,
            "split_delta_z": 0.025,
            "move_check_distance": 15
        },
        "homing_override": {
            "set_position_x": 0,
            "set_position_y": 0,
            "set_position_z": 0,
            "axes": "Z",
            "gcode": "\nG1 Z3 F600\nG28 X0 Y0\nG1 X131 Y108 F5000\nG28 Z0"
        },
        "output_pin beeper_pin": {
            "pwm": true,
            "pin": "PH2",
            "cycle_time": 0.001,
            "hardware_pwm": false,
            "scale": 1000,
            "value": 0,
            "shutdown_value": 0
        },
        "force_move": {
            "enable_force_move": true
        },
        "idle_timeout": {
            "timeout": 600,
            "gcode": "\nM104 S0\nM84"
        },
        "gcode_macro pause": {
            "gcode": "\n{% if not printer.pause_resume.is_paused %}\nM600\n{% endif %}",
            "rename_existing": "BASE_PAUSE",
            "description": "G-Code macro"
        },
        "gcode_macro m600": {
            "gcode": "\n{% set X = params.X|default(100) %}\n{% set Y = params.Y|default(100) %}\n{% set Z = params.Z|default(100) %}\nBASE_PAUSE\nSET_GCODE_VARIABLE MACRO=M600 VARIABLE=extr_temp VALUE={printer.extruder.target}\nG91\n{% if printer.extruder.temperature|float > 180 %}\nG1 E-.8 F2700\n{% endif %}\nG1 Z{Z}\nG90\nG1 X{X} Y{Y} F3000",
            "description": "G-Code macro",
            "variable_extr_temp": "0"
        },
        "gcode_macro resume": {
            "gcode": "\n{% if printer.pause_resume.is_paused %}\n{% if printer[\"gcode_macro M600\"].extr_temp %}\nM109 S{printer[\"gcode_macro M600\"].extr_temp}\n{% endif %}\nBASE_RESUME\n{% endif %}",
            "rename_existing": "BASE_RESUME",
            "description": "G-Code macro"
        },
        "printer": {
            "max_velocity": 300,
            "max_accel": 1500,
            "minimum_cruise_ratio": 0.5,
            "square_corner_velocity": 5,
            "kinematics": "cartesian",
            "max_z_velocity": 15,
            "max_z_accel": 200
        },
        "stepper_x": {
            "step_pin": "PC0",
            "dir_pin": "!PL0",
            "rotation_distance": 32,
            "microsteps": 16,
            "full_steps_per_rotation": 200,
            "gear_ratio": [],
            "enable_pin": "!PA7",
            "endstop_pin": "!PK2",
            "position_endstop": 0,
            "position_min": 0,
            "position_max": 250,
            "homing_speed": 50,
            "second_homing_speed": 25,
            "homing_retract_speed": 50,
            "homing_retract_dist": 0,
            "homing_positive_dir": false
        },
        "stepper_y": {
            "step_pin": "PC1",
            "dir_pin": "PL1",
            "rotation_distance": 32,
            "microsteps": 16,
            "full_steps_per_rotation": 200,
            "gear_ratio": [],
            "enable_pin": "!PA6",
            "endstop_pin": "!PK7",
            "position_endstop": -4,
            "position_min": -4,
            "position_max": 210,
            "homing_speed": 50,
            "second_homing_speed": 25,
            "homing_retract_speed": 50,
            "homing_retract_dist": 0,
            "homing_positive_dir": false
        },
        "stepper_z": {
            "step_pin": "PC2",
            "dir_pin": "!PL2",
            "rotation_distance": 8,
            "microsteps": 16,
            "full_steps_per_rotation": 200,
            "gear_ratio": [],
            "enable_pin": "!PA5",
            "endstop_pin": "probe:z_virtual_endstop",
            "position_min": -2,
            "position_max": 220,
            "homing_speed": 13.333,
            "second_homing_speed": 6.6665,
            "homing_retract_speed": 13.333,
            "homing_retract_dist": 5,
            "homing_positive_dir": false
        },
        "extruder": {
            "sensor_type": "ATC Semitec 104GT-2",
            "pullup_resistor": 4700,
            "inline_resistor": 0,
            "sensor_pin": "PF0",
            "min_temp": 0,
            "max_temp": 305,
            "min_extrude_temp": 170,
            "max_power": 1,
            "smooth_time": 1,
            "control": "pid",
            "pid_kp": 16.13,
            "pid_ki": 1.1625,
            "pid_kd": 56.23,
            "heater_pin": "PE5",
            "pwm_cycle_time": 0.1,
            "nozzle_diameter": 0.4,
            "filament_diameter": 1.75,
            "max_extrude_cross_section": 50,
            "max_extrude_only_velocity": 120,
            "max_extrude_only_accel": 1250,
            "max_extrude_only_distance": 500,
            "instantaneous_corner_velocity": 1,
            "step_pin": "PC3",
            "pressure_advance": 0,
            "pressure_advance_smooth_time": 0.04,
            "dir_pin": "PL6",
            "rotation_distance": 6.53061216,
            "microsteps": 8,
            "full_steps_per_rotation": 400,
            "gear_ratio": [],
            "enable_pin": "!PA4"
        },
        "verify_heater extruder": {
            "hysteresis": 5,
            "max_error": 120,
            "heating_gain": 2,
            "check_gain_time": 20
        }
    }
}
```
///

| Field                       |   Type   | Description                                        |
| --------------------------- | :------: | -------------------------------------------------- |
| `config`                    |  object  | An object containing the raw config as parsed      |
|                             |          | from Klipper's config file.  The keys are          |^
|                             |          | `section` names, the value for each section is     |^
|                             |          | an object containing `option: value` pairs.        |^
|                             |          | The values for each option will always be          |^
|                             |          | strings.                                           |^
| `settings`                  |  object  | An object containing the parsed configuration      |
|                             |          | for all loaded Klipper objects.  Each key          |^
|                             |          | is a `Klipper object` name, the values are         |^
|                             |          | objects containing `setting: value` pairs.         |^
|                             |          | The values will be converted to the type requested |^
|                             |          | during parsing.  Settings with default values      |^
|                             |          | may be present without a corresponding `option`    |^
|                             |          | in the `config`.  It is also possible              |^
|                             |          | for an entire `Klipper object` to exist without    |^
|                             |          | a corresponding `section` in the `config`.         |^
| `save_config_pending`       |   bool   | A value of `true` indicates that a `save_config`   |
|                             |          | action is pending a restart before writing the     |^
|                             |          | updated options to the config file.                |^
| `save_config_pending_items` |  object  | An object containing the items pending for write   |
|                             |          | when `save_config_pending` is `true.`              |^
| `warnings`                  | [string] | An array of strings describing issues encountered  |
|                             |          | when the configuration file was parsed.            |^
{ # configfile-object-spec} Configfile Object

/// warning
The `configfile` object has the potential to be very large.  Client software
running on devices with limited memory (such as embedded devices) may have issues
querying this object.
///

## extruder

*Enabled when `[extruder]` is included in `printer.cfg`*

/// note
If multiple extruders are configured, extruder 0 is available as
`extruder`, extruder 1 as `extruder1` and so on.
///

```{.json title="Printer Object Example"}
{
    "temperature": 0,
    "target": 0,
    "power": 0,
    "can_extrude": true,
    "pressure_advance": 0,
    "smooth_time": 0.04,
    "motion_queue": null
}
```

| Field              |      Type      | Description                                       |
| ------------------ | :------------: | ------------------------------------------------- |
| `temperature`      |     float      | The extruder's current temperature in C.          |
| `target`           |     float      | The extruder's requested target temperature in C. |
| `power`            |     float      | The current pwm value applied to the extruder's   |
|                    |                | heater.  This value should be in a range from 0.0 |^
|                    |                | to 1.0.                                           |^
| `can_extrude`      |      bool      | A value of `true` indicates that the current      |
|                    |                | temperature is above the minimum extrusion temp.  |^
| `pressure_advance` |     float      | The extruder's current pressure advance value.    |
| `smooth_time`      |     float      | The currently set time range to use when          |
|                    |                | calculating the average extruder velocity for     |^
|                    |                | pressure advance.                                 |^
| `motion_queue`     | string \| null | The name of the extruder the stepper is           |
|                    |                | synchronized to.  Will be null if the stepper is  |^
|                    |                | not synced with another extruder.                 |^
{ #extruder-object-spec } Extruder Object

## heater_bed

*Enabled when `[heater_bed]` is included in `printer.cfg`*

```{.json title="Printer Object Example"}
{
    "temperature": 0.0,
    "target": 0.0,
    "power": 0.0,
}
```

| Field         | Type  | Description                                      |
| ------------- | :---: | ------------------------------------------------ |
| `temperature` | float | The current temperature of the bed.              |
| `target`      | float | The target temperature of the bed.               |
| `power`       | float | The current pwm value applied to the heater. The |
|               |       | value should be in the range from 0.0 to 1.0.    |^
{ #heater-bed-object-spec } Heater Bed Object

## fan

*Enabled when `[fan]` is included in `printer.cfg`*

```{.json title="Printer Object Example"}
{
    "speed": 0.0,
    "rpm": 4000
}
```

| Field   |    Type     | Description                                              |
| ------- | :---------: | -------------------------------------------------------- |
| `speed` |    float    | The current fan speed.  This is reported as a percentage |
|         |             | with a range from 0.0 to 1.0                             |^
| `rpm`   | int \| null | The fan's revolutions per minute if the tachometer pin   |
|         |             | has been configured.  Will report `null` when the tach   |^
|         |             | pin is not configured.                                   |^
{ #fan-object-spec } Fan Object

## idle_timeout

```{.json title="Printer Object Example"}
{
   "state": "Idle",
   "printing_time": 0.0
}
```

| Field           |  Type  | Description                                               |
| --------------- | :----: | --------------------------------------------------------- |
| `state`         | string | The current [state](#idle-timeout-state-desc) as reported |
|                 |        | by the idle timeout module.                               |^
| `printing_time` | float  | The amount of time, in seconds, that idle timeout has     |
|                 |        | reported a `Printing` state.  Will be reset to 0 when     |^
|                 |        | the state transitions from `Printing` to `Ready`.         |^
{ #idle-timeout-object-spec } Idle Timeout Object

| State      | Description                                                   |
| ---------- | ------------------------------------------------------------- |
| `Printing` | The printer is busy.  This indicates that some action has     |
|            | been scheduled, such as a move command.                       |^
| `Ready`    | The printer is no longer active and is waiting for more       |
|            | activity or for the idle timeout to expire.                   |^
| `Idle`     | The printer has been inactive for a period of time longer     |
|            | than the configured idle timeout.                             |^
{ #idle-timeout-state-desc} Idle Timeout State

/// Tip
The `idle_timeout` `state` field should not be used to determine if Klipper
is "printing" a file, as the state will report `Printing` when executing
manual commands.
///

## virtual_sdcard

*Enabled when `[virtual_sdcard]` is included in `printer.cfg`*

```{.json title="Printer Object Example"}
{
    "file_path": null,
    "progress": 0,
    "is_active": false,
    "file_position": 0,
    "file_size": 0
}
```

| Field           |      Type      | Description                                    |
| --------------- | :------------: | ---------------------------------------------- |
| `file_path`     | string \| null | The full absolute path of the currently loaded |
|                 |                | file. Will be `null` if no file is loaded.     |^
| `progress`      |     float      | The current file progress reported as a        |
|                 |                | percentage.  Valid range is 0.0 to 1.0.        |^
| `is_active`     |      bool      | When `true` the virtual sdcard is actively     |
|                 |                | processing a file.                             |^
| `file_position` |      int       | The current file position in bytes.            |
| `file_size`     |      int       | The file size of the currently loaded          |
|                 |                | file in bytes.                                 |^
{ #virtual-sdcard-object-spec } Virtual SDCard Object


/// Note
The value for most fields will persist after a print has
paused, completed, or errored.  They are cleared when the user issues
an `SDCARD_RESET_FILE` gcode or when a new print has started.
///

## print_stats

*Enabled when `[virtual_sdcard]` is included in `printer.cfg`*

```{.json title="Printer Object Example"}
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

| Field            |  Type  | Description                                        |
| ---------------- | :----: | -------------------------------------------------- |
| `filename`       | string | The path to the currently loaded file relative to  |
|                  |        | the configured gcode folder.  Will be an empty     |^
|                  |        | string if no file is loaded.                       |^
| `total_duration` | float  | Total job duration in seconds.                     |
| `print_duration` | float  | Time spent printing the current job in seconds.    |
|                  |        | Does not include time paused.                      |^
| `filament_used`  | float  | Amount of filament used for the current job in mm. |
| `state`          | string | The current job [state](#print-stats-state-desc).  |
| `message`        | string | A status message set by Klipper.  Will be an empty |
|                  |        | string if no message is set.                       |^
| `info`           | object | A `Print Stats Supplemental Info` object.          |
|                  |        | #print-stats-supplemental-info-spec                |+
{ #print-stats-object-spec } Print Stats Object

| Field           |    Type     | Description                                  |
| --------------- | :---------: | -------------------------------------------- |
| `total_layer`   | int \| null | The total layer count of the current         |
|                 |             | job.  Will be null if the total layer        |^
|                 |             | count is not set.                            |^
| `current_layer` | int \| null | The index of the layer the job is currently  |
|                 |             | printing.  Will be null of the current layer |^
|                 |             | is not set.                                  |^
{#print-stats-supplemental-info-spec} Print Stats Supplemental Info

| State       | Description                                    |
| ----------- | ---------------------------------------------- |
| `standby`   | The printer is standing by for a job to begin. |
| `printing`  | A job is currently printing.                   |
| `paused`    | The current print job is paused.               |
| `complete`  | The last print job successfully finished.      |
| `error`     | The last print job exited with an error.       |
| `cancelled` | THe last print job was cancelled by the user.  |
{ #print-stats-state-desc } Print Stats State

  /// tip
  The `total_layer` and `current_layer` values in the `info` field are set by the
  [SET_PRINT_STATS_INFO](https://www.klipper3d.org/G-Codes.html#set_print_stats_info)
  gcode command.  It is necessary to configure the slicer to include this command
  in the print.  `SET_PRINT_STATS_INFO TOTAL_LAYER={total_layer_count}` should
  be called in the slicer's "start gcode" to initialize the total layer count.
  `SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}` should be called in the
  slicer's "on layer change" gcode.  The user must substitute the
  `total_layer_count` and `current_layer` with the appropriate
  "placeholder syntax" for the slicer.
  ///

/// note
After a print has started all of the values reported by `print_stats` will
persist until the user issues an `SDCARD_RESET_FILE` gcode command or a new print
has started.
///

## display_status

*Enabled when `[display]` or `[display_status]` is included in `printer.cfg`*

```{.json title="Printer Object Example"}
{
    "message": "",
    "progress": 0.0
}
```

| Field      |  Type  | Description                                         |
| ---------- | :----: | --------------------------------------------------- |
| `message`  | string | The message set by an M117 gcode.  If no message    |
|            |        | is set this will be an empty string.                |^
| `progress` | float  | Current print progress as reported by the M73       |
|            |        | gcode command.  If M73 has not been issued, this    |^
|            |        | value will fall back on `virtual_sdcard.progress`.  |^
|            |        | This value is expressed as a percentage of progress |^
|            |        | complete with a range from 0.0 to 1.0.              |^
{ #display-status-object-spec }  Display Status Object

/// note
Progress updates via M73 commands must be received at an interval of
no more than 5 seconds.  M73 progress tracking has a 5 second timeout
after which the `progress` field will fall back to the value reported
by `virtual_sdcard.progress`.
///

## temperature_sensor

*Enabled when `[temperature_sensor sensor_name]` is included in `printer.cfg`.*
*It is possible for multiple temperature sensors to be configured.*

```{.json title="Printer Object Example"}
{
    "temperature": 0.0,
    "measured_min_temp": 0.0,
    "measured_max_temp": 0.0
}
```

| Field               | Type  | Description                                        |
| ------------------- | :---: | -------------------------------------------------- |
| `temperature`       | float | The sensor's current temperature reading in C.     |
| `measured_min_temp` | float | The minimum sensor reading since the host started. |
| `measured_max_temp` | float | The maximum sensor reading since the host started. |
{ #temp-sensor-object-spec } Temperature Sensor Object

## temperature_fan

*Enabled when `[temperature_fan fan_name]` is included in `printer.cfg`.*
*It is possible for multiple temperature fans to be configured.*

```{.json title="Printer Object Example"}
{
    "speed": 0.0,
    "rpm": null,
    "temperature": 0.0,
    "target": 0.0
}
```

| Field         |    Type     | Description                                                |
| ------------- | :---------: | ---------------------------------------------------------- |
| `speed`       |    float    | The current fan speed.  This is reported as a percentage   |
|               |             | with a range from 0.0 to 1.0                               |^
| `rpm`         | int \| null | The fan's revolutions per minute if the tachometer pin     |
|               |             | has been configured.  Will report `null` when the tach     |^
|               |             | pin is not configured.                                     |^
| `temperature` |    float    | Current temperature of the sensor associated with the fan. |
| `target`      |    float    | Target temperature for the fan to enable.                  |
{ #temp-fan-object-spec } Temperature Fan Object

## filament_switch_sensor

*Enabled when `[filament_switch_sensor sensor_name]` is included in*
*`printer.cfg`.  It is possible for multiple filament switch sensors*
*to be configured.*

```{.json title="Printer Object Example"}
{
    "filament_detected": false,
    "enabled": true
}
```

| Field               | Type | Description                                             |
| ------------------- | :--: | ------------------------------------------------------- |
| `filament_detected` | bool | Reports `true` when filament is detected by the sensor. |
| `enabled`           | bool | Reports `true` when the filament sensor is enabled.     |
{ #filament-switch-sensor-object-spec }

## output_pin

*Enabled when `[output_pin pin_name]` is included in `printer.cfg`.*
*It is possible for multiple output pins to be configured.*

```{.json title="Printer Object Example"}
{
    "value": 0.0
}
```

| Field   |     Type     | Description                                 |
| ------- | :----------: | ------------------------------------------- |
| `value` | float \| int | The current value of the pin.  Digital pins |
|         |              | will be 0 (off) or 1 (on). PWM pins will    |^
|         |              | report a range from 0.0 to 1.0.             |^
{ #output-pin-object-spec } Output Pin Object

## bed_mesh

*Enabled when `[bed_mesh]` is included in `printer.cfg`.*

/// collapse-code
```{.json title="Printer Object Example"}
{
    "profile_name": "default",
    "mesh_min": [
        24,
        6
    ],
    "mesh_max": [
        237.96,
        210
    ],
    "probed_matrix": [
        [
            -0.128919,
            -0.136419,
            -0.111419,
            -0.131419,
            -0.166419,
            -0.211419,
            -0.268919
        ],
        [
            -0.006419,
            0.021081,
            0.003581,
            -0.056419,
            -0.068919,
            -0.086419,
            -0.173919
        ],
        [
            0.001081,
            0.048581,
            0.023581,
            -0.014544,
            -0.016419,
            -0.063919,
            -0.108919
        ],
        [
            0.013581,
            0.041081,
            0.033581,
            0.006081,
            -0.008919,
            -0.036419,
            -0.076419
        ],
        [
            0.031081,
            0.076081,
            0.071081,
            0.022331,
            0.021081,
            0.013581,
            -0.036419
        ],
        [
            0.006081,
            0.053581,
            0.068581,
            0.016081,
            0.041081,
            0.023581,
            -0.001419
        ],
        [
            -0.041419,
            -0.021419,
            0.061081,
            0.068581,
            0.021081,
            0.036081,
            0.033581
        ]
    ],
    "mesh_matrix": [
        [
            -0.128919,
            -0.132067,
            -0.136049,
            -0.136419,
            -0.128826,
            -0.117623,
            -0.111419,
            -0.114197,
            -0.121975,
            -0.131419,
            -0.141604,
            -0.153456,
            -0.166419,
            -0.180215,
            -0.195123,
            -0.211419,
            -0.231789,
            -0.253549,
            -0.268919
        ],
        [
            -0.0929,
            -0.092739,
            -0.092341,
            -0.090771,
            -0.085645,
            -0.079346,
            -0.078086,
            -0.08576,
            -0.098472,
            -0.110748,
            -0.120355,
            -0.129525,
            -0.139475,
            -0.149675,
            -0.160655,
            -0.175215,
            -0.198,
            -0.224365,
            -0.243178
        ],
        [
            -0.043271,
            -0.038473,
            -0.031937,
            -0.027623,
            -0.02591,
            -0.026419,
            -0.031975,
            -0.046304,
            -0.06568,
            -0.081743,
            -0.090474,
            -0.095893,
            -0.101697,
            -0.107088,
            -0.112864,
            -0.125123,
            -0.151083,
            -0.183525,
            -0.206882
        ],
        [
            -0.006419,
            0.002377,
            0.014229,
            0.021081,
            0.020155,
            0.014229,
            0.003581,
            -0.01503,
            -0.038363,
            -0.056419,
            -0.063919,
            -0.066141,
            -0.068919,
            -0.071789,
            -0.075215,
            -0.086419,
            -0.113641,
            -0.148641,
            -0.173919
        ],
        [
            0.004414,
            0.016177,
            0.032024,
            0.041174,
            0.039035,
            0.030198,
            0.017655,
            -0.000672,
            -0.022706,
            -0.03922,
            -0.044637,
            -0.044534,
            -0.046419,
            -0.051524,
            -0.058616,
            -0.071512,
            -0.096278,
            -0.126848,
            -0.148826
        ],
        [
            0.00247,
            0.016561,
            0.03558,
            0.046822,
            0.044244,
            0.033888,
            0.021174,
            0.005504,
            -0.012524,
            -0.025701,
            -0.02868,
            -0.026807,
            -0.028919,
            -0.038547,
            -0.052159,
            -0.067993,
            -0.088391,
            -0.111011,
            -0.127067
        ],
        [
            0.001081,
            0.016081,
            0.036359,
            0.048581,
            0.046104,
            0.035572,
            0.023581,
            0.010502,
            -0.004035,
            -0.014544,
            -0.016164,
            -0.013757,
            -0.016419,
            -0.028965,
            -0.046581,
            -0.063919,
            -0.080771,
            -0.097345,
            -0.108919
        ],
        [
            0.004692,
            0.018015,
            0.03605,
            0.0471,
            0.045384,
            0.036683,
            0.026637,
            0.015674,
            0.003366,
            -0.005933,
            -0.008321,
            -0.007701,
            -0.011419,
            -0.02354,
            -0.04,
            -0.055956,
            -0.071107,
            -0.085754,
            -0.095956
        ],
        [
            0.008859,
            0.019085,
            0.032964,
            0.041729,
            0.041315,
            0.035788,
            0.028581,
            0.019667,
            0.009074,
            0.000317,
            -0.00392,
            -0.00632,
            -0.011419,
            -0.02151,
            -0.034299,
            -0.047438,
            -0.06171,
            -0.076333,
            -0.086604
        ],
        [
            0.013581,
            0.022007,
            0.033488,
            0.041081,
            0.041914,
            0.038859,
            0.033581,
            0.025433,
            0.015062,
            0.006081,
            0.000618,
            -0.003456,
            -0.008919,
            -0.016697,
            -0.025863,
            -0.036419,
            -0.050308,
            -0.065586,
            -0.076419
        ],
        [
            0.020618,
            0.030066,
            0.042971,
            0.051729,
            0.053413,
            0.050951,
            0.045525,
            0.035803,
            0.023118,
            0.012655,
            0.007137,
            0.003842,
            -0.000215,
            -0.0054,
            -0.011347,
            -0.019938,
            -0.034293,
            -0.051292,
            -0.063456
        ],
        [
            0.028211,
            0.039977,
            0.056064,
            0.0671,
            0.06958,
            0.067009,
            0.060525,
            0.047946,
            0.031454,
            0.018743,
            0.013793,
            0.012623,
            0.010988,
            0.008679,
            0.005904,
            -0.000956,
            -0.016272,
            -0.035671,
            -0.04966
        ],
        [
            0.031081,
            0.0446,
            0.063118,
            0.076081,
            0.079738,
            0.07784,
            0.071081,
            0.056312,
            0.036683,
            0.022331,
            0.018627,
            0.020201,
            0.021081,
            0.020618,
            0.019461,
            0.013581,
            -0.001789,
            -0.021882,
            -0.036419
        ],
        [
            0.026729,
            0.041019,
            0.060649,
            0.074785,
            0.07992,
            0.079562,
            0.073396,
            0.057015,
            0.034828,
            0.019738,
            0.019173,
            0.025705,
            0.02997,
            0.029346,
            0.026453,
            0.019785,
            0.005944,
            -0.011673,
            -0.024382
        ],
        [
            0.017655,
            0.032151,
            0.052141,
            0.0671,
            0.074094,
            0.076057,
            0.071266,
            0.053943,
            0.029865,
            0.014646,
            0.017897,
            0.030006,
            0.037748,
            0.035934,
            0.029752,
            0.021544,
            0.010138,
            -0.003295,
            -0.0129
        ],
        [
            0.006081,
            0.0196,
            0.038396,
            0.053581,
            0.063488,
            0.069785,
            0.068581,
            0.053211,
            0.03034,
            0.016081,
            0.020248,
            0.033025,
            0.041081,
            0.038674,
            0.031544,
            0.023581,
            0.014877,
            0.00534,
            -0.001419
        ],
        [
            -0.009845,
            0.000519,
            0.015275,
            0.029692,
            0.044726,
            0.059421,
            0.066174,
            0.058303,
            0.04249,
            0.031174,
            0.030519,
            0.034359,
            0.036637,
            0.0351,
            0.032,
            0.028025,
            0.022504,
            0.016108,
            0.011544
        ],
        [
            -0.028271,
            -0.022245,
            -0.013082,
            -0.00003,
            0.021186,
            0.04629,
            0.063211,
            0.065736,
            0.060078,
            0.052794,
            0.044416,
            0.034413,
            0.027748,
            0.027679,
            0.030949,
            0.032748,
            0.030676,
            0.027134,
            0.024507
        ],
        [
            -0.041419,
            -0.038549,
            -0.033456,
            -0.021419,
            0.004229,
            0.036822,
            0.061081,
            0.071174,
            0.072933,
            0.068581,
            0.054507,
            0.034322,
            0.021081,
            0.0221,
            0.030062,
            0.036081,
            0.036451,
            0.034877,
            0.033581
        ]
    ],
    "profiles": {
        "default": {
            "points": [
                [
                    -0.128919,
                    -0.136419,
                    -0.111419,
                    -0.131419,
                    -0.166419,
                    -0.211419,
                    -0.268919
                ],
                [
                    -0.006419,
                    0.021081,
                    0.003581,
                    -0.056419,
                    -0.068919,
                    -0.086419,
                    -0.173919
                ],
                [
                    0.001081,
                    0.048581,
                    0.023581,
                    -0.014544,
                    -0.016419,
                    -0.063919,
                    -0.108919
                ],
                [
                    0.013581,
                    0.041081,
                    0.033581,
                    0.006081,
                    -0.008919,
                    -0.036419,
                    -0.076419
                ],
                [
                    0.031081,
                    0.076081,
                    0.071081,
                    0.022331,
                    0.021081,
                    0.013581,
                    -0.036419
                ],
                [
                    0.006081,
                    0.053581,
                    0.068581,
                    0.016081,
                    0.041081,
                    0.023581,
                    -0.001419
                ],
                [
                    -0.041419,
                    -0.021419,
                    0.061081,
                    0.068581,
                    0.021081,
                    0.036081,
                    0.033581
                ]
            ],
            "mesh_params": {
                "min_x": 24,
                "max_x": 237.96,
                "min_y": 6,
                "max_y": 210,
                "x_count": 7,
                "y_count": 7,
                "mesh_x_pps": 2,
                "mesh_y_pps": 2,
                "algo": "bicubic",
                "tension": 0.5
            }
        }
    }
}
```
///

| Field           |   Type    | Description                                           |
| --------------- | :-------: | ----------------------------------------------------- |
| `profile_name`  |  string   | The name of the currently loaded profile.  Will be an |
|                 |           | empty string if no profile is loaded.                 |^
| `mesh_min`      |  [float]  | A coordinate (X,Y) indicating the minimum location    |
|                 |           | of the mesh.                                          |^
| `mesh_max`      |  [float]  | A coordinate (X,Y) indicating the maximum location    |
|                 |           | of the mesh.                                          |^
| `probed_matrix` | [[float]] | A 2D array of Z values sampled by the probe.          |
| `mesh_matrix`   | [[float]] | A 2D array of Z values representing the interpolated  |
|                 |           | mesh.                                                 |^
| `profiles`      |  object   | An object, where the keys are profile names and the   |
|                 |           | values are `Bed Mesh Profile` objects.                |^
|                 |           | #bed-mesh-profile-spec                                |+
{ #bed-mesh-object-spec } Bed Mesh Object

| Field         |   Type    | Description                                  |
| ------------- | :-------: | -------------------------------------------- |
| `mesh_params` |  object   | A `Mesh Parameters` object.                  |
|               |           | #profile-mesh-params-spec                    |+
| `points`      | [[float]] | A 2D array of Z values sampled by the probe. |
{ #bed-mesh-profile-spec } Bed Mesh Profile

| Field        |  Type  | Description                                         |
| ------------ | :----: | --------------------------------------------------- |
| `min_x`      | float  | The minimum X coordinate probed.                    |
| `max_x`      | float  | The maximum X coordinate probed.                    |
| `min_y`      | float  | The minimum Y coordinate probed.                    |
| `max_y`      | float  | The maximum Y coordinate probed.                    |
| `x_count`    |  int   | The number of probe samples taken along the X axis. |
| `y_count`    |  int   | The number of probe samples taken along the Y axis. |
| `mesh_x_pps` |  int   | The number of values to interpolate between probe   |
|              |        | samples on the X axis.                              |^
| `mesh_y_pps` |  int   | The number of values to interpolate between probe   |
|              |        | samples on the Y axis.                              |^
| `algo`       | string | The interpolation algorithm.  Can be `lagrange` or  |
|              |        | `bicubic`.                                          |^
| `tension`    | float  | The `tension` parameter used for the `bicubic`      |
|              |        | interpolation algorithm.                            |^
{ #profile-mesh-params-spec } Mesh Parameters

/// tip
See [the tutorials](./external_api/introduction.md#bed-mesh-coordinates)
for an example of how to use this information to generate (X,Y,Z)
coordinates.
///

## exclude_object

*Available when `[exclude_object]` is configured in `printer.cfg`.*

/// collapse-code
```{.json title="Printer Object Example"}
{
    "objects": [
        {
            "name": "HELPER_DISK_STL_(INSTANCE_1)",
            "center": [
                136.426,
                116.347
            ],
            "polygon": [
                [
                    136.614,
                    108.849
                ],
                [
                    138.112,
                    109.039
                ],
                [
                    139.541,
                    109.524
                ],
                [
                    140.844,
                    110.286
                ],
                [
                    141.968,
                    111.294
                ],
                [
                    142.868,
                    112.506
                ],
                [
                    143.506,
                    113.874
                ],
                [
                    143.858,
                    115.342
                ],
                [
                    143.921,
                    116.599
                ],
                [
                    143.719,
                    118.094
                ],
                [
                    143.324,
                    119.289
                ],
                [
                    142.595,
                    120.611
                ],
                [
                    141.616,
                    121.76
                ],
                [
                    140.427,
                    122.69
                ],
                [
                    139.076,
                    123.363
                ],
                [
                    137.617,
                    123.751
                ],
                [
                    136.362,
                    123.846
                ],
                [
                    134.862,
                    123.682
                ],
                [
                    133.425,
                    123.22
                ],
                [
                    132.109,
                    122.48
                ],
                [
                    130.969,
                    121.492
                ],
                [
                    130.185,
                    120.507
                ],
                [
                    129.478,
                    119.173
                ],
                [
                    129.053,
                    117.725
                ],
                [
                    128.927,
                    116.221
                ],
                [
                    129.104,
                    114.722
                ],
                [
                    129.577,
                    113.289
                ],
                [
                    130.328,
                    111.979
                ],
                [
                    131.326,
                    110.847
                ],
                [
                    132.318,
                    110.072
                ],
                [
                    133.658,
                    109.376
                ],
                [
                    135.109,
                    108.963
                ]
            ]
        },
        {
            "name": "HELPER_DISK_STL_(INSTANCE_2)",
            "center": [
                115.426,
                116.347
            ],
            "polygon": [
                [
                    115.615,
                    108.849
                ],
                [
                    117.113,
                    109.039
                ],
                [
                    118.542,
                    109.524
                ],
                [
                    119.639,
                    110.141
                ],
                [
                    120.796,
                    111.111
                ],
                [
                    121.736,
                    112.292
                ],
                [
                    122.42,
                    113.637
                ],
                [
                    122.821,
                    115.093
                ],
                [
                    122.922,
                    116.599
                ],
                [
                    122.774,
                    117.848
                ],
                [
                    122.325,
                    119.289
                ],
                [
                    121.596,
                    120.611
                ],
                [
                    120.617,
                    121.76
                ],
                [
                    119.428,
                    122.69
                ],
                [
                    118.077,
                    123.363
                ],
                [
                    116.618,
                    123.751
                ],
                [
                    115.363,
                    123.846
                ],
                [
                    113.863,
                    123.682
                ],
                [
                    112.426,
                    123.22
                ],
                [
                    111.11,
                    122.48
                ],
                [
                    109.969,
                    121.492
                ],
                [
                    109.186,
                    120.507
                ],
                [
                    108.479,
                    119.173
                ],
                [
                    108.054,
                    117.725
                ],
                [
                    107.927,
                    116.221
                ],
                [
                    108.104,
                    114.722
                ],
                [
                    108.578,
                    113.289
                ],
                [
                    109.329,
                    111.979
                ],
                [
                    110.327,
                    110.847
                ],
                [
                    111.318,
                    110.072
                ],
                [
                    112.658,
                    109.376
                ],
                [
                    114.11,
                    108.963
                ]
            ]
        },
        {
            "name": "HELPER_DISK_STL_(INSTANCE_3)",
            "center": [
                136.426,
                95.347
            ],
            "polygon": [
                [
                    136.614,
                    87.849
                ],
                [
                    138.112,
                    88.039
                ],
                [
                    139.541,
                    88.524
                ],
                [
                    140.844,
                    89.286
                ],
                [
                    141.968,
                    90.294
                ],
                [
                    142.868,
                    91.506
                ],
                [
                    143.506,
                    92.874
                ],
                [
                    143.858,
                    94.342
                ],
                [
                    143.921,
                    95.599
                ],
                [
                    143.719,
                    97.094
                ],
                [
                    143.324,
                    98.289
                ],
                [
                    142.595,
                    99.611
                ],
                [
                    141.616,
                    100.76
                ],
                [
                    140.427,
                    101.69
                ],
                [
                    139.076,
                    102.363
                ],
                [
                    137.617,
                    102.751
                ],
                [
                    136.362,
                    102.846
                ],
                [
                    134.862,
                    102.682
                ],
                [
                    133.425,
                    102.22
                ],
                [
                    132.109,
                    101.48
                ],
                [
                    130.969,
                    100.492
                ],
                [
                    130.185,
                    99.507
                ],
                [
                    129.478,
                    98.173
                ],
                [
                    129.053,
                    96.725
                ],
                [
                    128.927,
                    95.221
                ],
                [
                    129.104,
                    93.722
                ],
                [
                    129.577,
                    92.288
                ],
                [
                    130.328,
                    90.979
                ],
                [
                    131.326,
                    89.847
                ],
                [
                    132.531,
                    88.937
                ],
                [
                    133.658,
                    88.376
                ],
                [
                    135.109,
                    87.963
                ]
            ]
        },
        {
            "name": "M3_HEX_NUT_STL_(INSTANCE_1)",
            "center": [
                120.324,
                100.421
            ],
            "polygon": [
                [
                    120.517,
                    97.357
                ],
                [
                    123.074,
                    98.833
                ],
                [
                    123.074,
                    102.008
                ],
                [
                    120.324,
                    103.596
                ],
                [
                    117.574,
                    102.008
                ],
                [
                    117.574,
                    98.833
                ],
                [
                    120.324,
                    97.245
                ]
            ]
        },
        {
            "name": "M3_HEX_NUT_STL_(INSTANCE_2)",
            "center": [
                108.824,
                100.421
            ],
            "polygon": [
                [
                    109.017,
                    97.357
                ],
                [
                    111.574,
                    98.833
                ],
                [
                    111.574,
                    102.008
                ],
                [
                    108.824,
                    103.596
                ],
                [
                    106.074,
                    102.008
                ],
                [
                    106.074,
                    98.833
                ],
                [
                    108.824,
                    97.245
                ]
            ]
        },
        {
            "name": "M3_HEX_NUT_STL_(INSTANCE_3)",
            "center": [
                115.732,
                89.793
            ],
            "polygon": [
                [
                    115.925,
                    86.729
                ],
                [
                    118.482,
                    88.205
                ],
                [
                    118.482,
                    91.38
                ],
                [
                    115.732,
                    92.968
                ],
                [
                    112.982,
                    91.38
                ],
                [
                    112.982,
                    88.205
                ],
                [
                    115.732,
                    86.617
                ]
            ]
        }
    ],
    "excluded_objects": [],
    "current_object": "M3_HEX_NUT_STL_(INSTANCE_3)"
}

```
///

| Field              |      Type      | Description                                   |
| ------------------ | :------------: | --------------------------------------------- |
| `objects`          |    [object]    | An array of `Object Definitions`.             |
|                    |                | #object-definition-spec                       |+
| `excluded_objects` |    [string]    | An array of object names currently excluded.  |
| `current_object`   | string \| null | The name of the object currently being        |
|                    |                | printed.  Will be `null` if no defined object |^
|                    |                | is printing.                                  |^
{ #exclude-object-spec } Exclude Object

| Field     |   Type    | Description                                         |
| --------- | :-------: | --------------------------------------------------- |
| `name`    |  string   | The name of the defined object.                     |
| `polygon` | [[float]] | A 2D array indicating the (X,Y) coordinates         |
|           |           | that form the boundary of the object's location.    |^
|           |           | This field is only available if the `polygon`       |^
|           |           | is included when the object is defined by the       |^
|           |           | `EXCLUDE_OBJECT_DEFINE` gcode command.              |^
| `center`  |  [float]  | An (X,Y) coordinate indicating the center point     |
|           |           | of the object.  This field is only available if     |^
|           |           | the `center` is included when the object is defined |^
|           |           | by the `EXCLUDE_OBJECT_DEFINE` gcode command.       |^
{ #object-definition-spec } Object Definition

## gcode_macro

*Available when `[gcode_macro macro_name]` is included in `printer.cfg`.*
*It is possible for multiple gcode macros to be configured.*

```{.json title="Printer Object Example"}
{
    "var_name": "value"
}
```

| Field      | Type | Description                              |
| ---------- | :--: | ---------------------------------------- |
| *var_name* | any  | Zero or more `variables` present in the  |
|            |      | gcode_macro's configuration.  The type   |^
|            |      | is coerced from the value set.  If no    |^
|            |      | variables are configured the object will |^
|            |      | be empty.                                |^
{ #gcode-macro-object-spec } GCode Macro Object

## mcu

*The primary `mcu` object should always be available.  It is possible*
*for additional MCU objects to be present when one or more*
*[mcu mcu_name] sections are included in `printer.cfg`.*

```{.json title="Printer Object Example"}
{
    "mcu_version": "v0.12.0-272-g13c75ea87",
    "mcu_build_versions": "gcc: (GCC) 5.4.0 binutils: (GNU Binutils) 2.26.20160125",
    "mcu_constants": {
        "ADC_MAX": 1023,
        "BUS_PINS_spi": "PB3,PB2,PB1",
        "BUS_PINS_twi": "PD0,PD1",
        "CLOCK_FREQ": 16000000,
        "MCU": "atmega2560",
        "PWM_MAX": 255,
        "RECEIVE_WINDOW": 192,
        "RESERVE_PINS_serial": "PE0,PE1",
        "SERIAL_BAUD": 250000,
        "STATS_SUMSQ_BASE": 256
    }
}
```

| Field                |  Type  | Description                                   |
| -------------------- | :----: | --------------------------------------------- |
| `mcu_version`        | string | The version of Klipper at the time the MCU    |
|                      |        | firmware was built.                           |^
| `mcu_build_versions` | string | Version information about the tools used      |
|                      |        | to build the MCU firmware.                    |^
| `mcu_constants`      | object | An object containing compile time constants   |
|                      |        | reported by the MCU.  The constants available |^
|                      |        | may differ between MCUs as they depend on the |^
|                      |        | micro-controller's  underlying architecture.  |^
{ #mcu-object-spec } MCU Object

## stepper_enable

*Available when one or more steppers are configured in `printer.cfg`.*

```{.json title="Printer Object Example"}
{
    "steppers": {
        "stepper_z": false,
        "stepper_y": false,
        "stepper_x": false,
        "extruder": false
    }
}
```

| Field      |  Type  | Description                                  |
| ---------- | :----: | -------------------------------------------- |
| `steppers` | object | An object containing the enabled state for   |
|            |        | all registered steppers.  The keys are       |^
|            |        | stepper names, the values are booleans       |^
|            |        | reflecting the stepper driver enabled state. |^
{ #stepper-enable-object-spec } Stepper Enable Object

## TMC Drivers

*Available when one or more `[tmcXXXX driver_name]` sections are included*
*in `printer.cfg`.*

```{.json title="Printer Object Example"}
{
    "phase_offset_position": 0.01,
    "temperature": null,
    "drv_status": {
        "sg_result": 211,
        "cs_actual": 8,
        "stallguard": 1,
        "stst": 1
    },
    "mcu_phase_offset": 1,
    "run_current": 0.28173785812901503,
    "hold_current": 0.28173785812901503
}
```

| Field                   |      Type      | Description                                  |
| ----------------------- | :------------: | -------------------------------------------- |
| `phase_offset_position` | float \| null  | The commanded position corresponding         |
|                         |                | with the driver's "zero phase".  Will be     |^
|                         |                | `null` if the phase offset is unknown.       |^
| `temperature`           | float \| null  | The temperature reported by the driver.      |
|                         |                | Will be `null` if temperature reporting      |^
|                         |                | is not available.                            |^
| `drv_status`            | object \| null | An object containing results from the most   |
|                         |                | recent driver status query.  Will be `null`  |^
|                         |                | if the driver is disabled.  See the driver's |^
|                         |                | datasheet for explanations on the fields     |^
|                         |                | present in this object.                      |^
| `mcu_phase_offset`      |  int \| null   | The MCU stepper position corresponding       |
|                         |                | with the driver's "zero phase".  Will be     |^
|                         |                | `null` if the phase offset is unknown.       |^
| `run_current`           |     float      | The presently set run current in amps.       |
| `hold_current`          |     float      | The presently set hold current in amps.      |
{ #tmc-driver-object-spec } TMC Driver Object

/// note
Klipper will omit fields from the `drv_status` object whose value
evaluates to false.  For example, the `stallguard` field is only
present when its value is non-zero.
///
