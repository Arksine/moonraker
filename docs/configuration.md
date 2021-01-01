This document describes Moonraker's full configuration.  As this file
references configuration for both Klipper (printer.cfg) and Moonraker
(moonraker.conf), each example contains a commment indicating which
configuration file is being refrenenced.

# Primary Configuration
The sections outlined here are required for Moonraker to function. A
minimal functional configuration might look like the following:
```
# moonraker.conf

[server]
host: 0.0.0.0
port: 7125
enable_debug_logging: True
config_path: ~/.klippy_config

[authorization]
enabled: True
trusted_clients:
 192.168.1.0/24
```

## server

```
# moonraker.conf

[server]
host: 0.0.0.0
#  The host address in which to bind the HTTP server.  Default is to bind
#  to all interfaces
port: 7125
#   The port the HTTP server will listen on.  Default is 7125
klippy_uds_address: /tmp/klippy_uds
#   The address of Unix Domain Socket used to communicate with Klippy. Default
#   is /tmp/klippy_uds
max_upload_size: 200
#   The maximum size allowed for a file upload.  Default is 200 MiB.
enable_debug_logging: True
#   When set to True Moonraker will log in verbose mode.  During this stage
#   of development the default is True.  In the future this will change.
config_path:
#   The path to a directory where configuration files are located. This
#   directory may contain Klipper config files (printer.cfg) or Moonraker
#   config files (moonraker.conf).  Clients may also write their own config
#   files to this directory.  Note that this may not be the system root
#   (ie: "/") and moonraker must have read and write access permissions
#   for this directory.
```
## authorization

```
# moonraker.conf

[authorization]
enabled: True
#   Enables authorization.  When set to true, requests must either contain
#   a valid API key or originate from a trusted client. Default is True.
api_key_file: ~/.moonraker_api_key
#   Path of the file that stores Moonraker's API key.  The default is
#   ~/.moonraker_api_key
trusted_clients:
 192.168.1.30
 192.168.1.0/24
#   A list of newline separated ip addresses and/or ip ranges that are
#   trusted. Trusted clients are given full access to the API.  Both IPv4
#   and IPv6 addresses and ranges are supported. Ranges must be expressed
#   in CIDR notation (see http://ip.sb/cidr for more info).  For example, an
#   entry of 192.168.1.0/24 will authorize IPs in the range of 192.168.1.1 -
#   192.168.1.254.  Note that when specifying IPv4 ranges the last segment
#   of the ip address must be 0. The default is no clients or ranges are
#   trusted.
cors_domains:
  http://klipper-printer.local
  http://second-printer.local:7125
#   Enables CORS for the specified domains.  One may specify * if they wish
#   to allow all domains.
```

# Plugin Configuration
The sections outlined here are optional and enable additional
functionality in moonraker.

## paneldue
Enables PanelDue display support.  The PanelDue should be connected to the
host machine, either via the machine's UART GPIOs or through a USB-TTL
converter.  Currently PanelDue Firmware Version 1.24 is supported.  Other
releases may not behave correctly.

```
# moonraker.conf

[paneldue]
serial:
#   The serial port in which the PanelDue is connected.  This parameter
#   must be provided.
baud: 57600
#   The baud rate to connect at.  The default is 57600 baud.
machine_name: Klipper
#   An optional unique machine name which displays on the PanelDue's
#   Header.  The default is "Klipper".
macros:
 LOAD_FILAMENT
 UNLOAD_FILAMENT
 PANELDUE_BEEP FREQUENCY=500 DURATION=1
#   A list of newline separated "macros" that are displayed in the
#   PanelDue's "macros" tab.  These can be gcode macros or simple
#   gcodes.  A macro may contain parameters.  The default is no
#   macros will be displayed by the PanelDue.
confirmed_macros:
  RESTART
  FIRMWARE_RESTART
#  Like the "macros" option, this list is added to the macros tab.
#  When one of these macros is excuted the PanelDue will prompt
#  the user with a confirmation dialog.  The default is to include
#  RESTART and FIRMWARE_RESTART.
```

Most options above are self explanatory.  The "macros" option can be used
to specify commands (either built in or gcode_macros) that will show up
in the PanelDue's "macro" menu.

Note that buzzing the piezo requires the following gcode_macro in `printer.cfg`:
```
# printer.cfg

[gcode_macro PANELDUE_BEEP]
# Beep frequency
default_parameter_FREQUENCY: 300
# Beep duration in seconds
default_parameter_DURATION: 1.
gcode:
  {action_call_remote_method("paneldue_beep",
                             frequency=FREQUENCY|int,
                             duration=DURATION|float)}
```

## power
Enables device power control.  Currently GPIO (relays), TPLink Smartplug,
and Tasmota (via http) devices are supported.

```
# moonraker.conf

[power device_name]
type: gpio
#   The type of device.  Can be either gpio, tplink_smartplug or tasmota.
#   This parameter must be provided.
pin: gpiochip0/gpio26
#   The pin to use for GPIO devices.  The chip is optional, if left out
#   then the module will default to gpiochip0.  If one wishes to invert
#   the signal, a "!" may be prefixed to the pin.  Valid examples:
#      gpiochip0/gpio26
#      gpio26
#      !gpiochip0/gpio26
#      !gpio26
#    This parameter must be provided for "gpio" type devices
initial_state: off
#    The initial state for GPIO type devices.  May be on or
#    off.  When moonraker starts the device will be set to this
#    state.  Default is off.
address:
port:
#   The above options are used for "tplink_smartplug" devices.  The
#   address should be a valid ip or hostname for the tplink device.
#   The port should be the port the device is configured to use.  The
#   address must be provided. The port defaults to 9999.
address:
password:
output_id:
#   The above options are used for "tasmota" devices.  The
#   address should be a valid ip or hostname for the tasmota device.
#   Provide a password if configured in Tasmota (default is empty).
#   Provided an output_id (relay id) if the Tasmota device supports
#   more than one (default is 1).
#   If your single-relay Tasmota device switches on/off successfully,
#   but fails to report its state, ensure that 'SetOption26' is set in
#   Tasmota.

```
Below are some potential examples:
```
# moonraker.conf

[power printer]
type: gpio
pin: gpio26
initial_state: off

[power printer_led]
type: gpio
pin: !gpiochip0/gpio16
initial_state: off

[power light_strip]
type: gpio
pin: gpiochip0/gpio17
initial_state: on

[power wifi_switch]
type: tplink_smartplug
address: 192.168.1.123

[power tasmota_plug]
type: tasmota
address: 192.168.1.124
password: password1
```

It is possible to toggle device power from the Klippy host, this can be done
with a gcode_macro, such as:
```
# printer.cfg

[gcode_macro POWER_OFF_PRINTER]
gcode:
  {action_call_remote_method("set_device_power",
                             device="printer",
                             state="off")}
```
The `POWER_OFF_PRINTER` gcode can be run to turn off the "printer" device.
This could be used in conjunction with Klipper's idle timeout to turn the
printer off when idle with a configuration similar to that of below:
```
# printer.cfg

[delayed_gcode delayed_printer_off]
initial_duration: 0.
gcode:
  {% if printer.idle_timeout.state == "Idle" %}
    POWER_OFF_PRINTER
  {% endif %}

[idle_timeout]
gcode:
  TURN_OFF_MOTORS
  TURN_OFF_HEATERS
  UPDATE_DELAYED_GCODE ID=delayed_printer_off DURATION=60
```

## update_manager
This enables moonraker's update manager.  Note that updates can only be
performed on pristine git repos.  Repos that have been modified on
disk or cloned from unofficial sources are not supported.

```
# moonraker.conf

[update_manager]
enable_repo_debug: False
#   When set to True moonraker will bypass repo validation and allow
#   updates from unofficial remotes and/or branches.  Updates on
#   detached repos are also allowed.  This option is intended for
#   developers and should not be used on production machines.  The
#   default is False.
distro: debian
#   The disto in which moonraker has been installed.  Currently the
#   update manager only supports "debian", which encompasses all of
#   its derivatives.  The default is debain.
client_repo:
#   This is the GitHub repo of the client, in the format of user/client.
#   For example, this could be set to cadriel/fluidd to update Fluidd or
#   meteyou/mainsail to update Mainsail.  If this option is not set then
#   the update manager will not attempt to update a client.
client_path:
#   The path to the client's files on disk.  This cannot be a symbolic link,
#   it must be the real directory in which the client's files are located.
#   If `client_repo` is set, this parameter must be provided

```
