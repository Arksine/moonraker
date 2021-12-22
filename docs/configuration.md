#
This document describes Moonraker's full configuration.  As this file
references configuration for both Klipper (`printer.cfg`) and Moonraker
(`moonraker.conf`), each example contains a commment indicating which
configuration file is being refrenenced. A basic
[sample configuration](./moonraker.conf) in the `docs` directory.

## Core Components

Moonraker's core components are always loaded regardless of configuration.

### `[server]`

The `[server]` section provides essential configuration for Moonraker.
This section is requrired.

```ini

# moonraker.conf

[server]
host: 0.0.0.0
#  The host address in which to bind the HTTP server.  Default is to bind
#  to all interfaces
port: 7125
#   The port the HTTP server will listen on.  Default is 7125
ssl_port: 7130
#   The port to listen on for SSL (HTTPS) connections.  Note that the HTTPS
#   server will only be started of the certificate and key options outlined
#   below are provied.  The default is 7130.
ssl_certificate_path:
#   The path to a self signed ssl certificate.  The default is no path, which
#   disables HTTPS.
ssl_key_path:
#   The path to the private key used to signed the certificate.  The default
#   is no path, which disables HTTPS.
klippy_uds_address: /tmp/klippy_uds
#   The address of Unix Domain Socket used to communicate with Klippy. Default
#   is /tmp/klippy_uds
max_upload_size: 1024
#   The maximum size allowed for a file upload (in MiB).  Default is 1024 MiB.
enable_debug_logging: False
#   When set to True Moonraker will log in verbose mode.  During this stage
#   of development the default is False.
```

### `[file_manager]`

The `file_manager` section provides configuration for Moonraker's file
management functionality.  If omitted defaults will be used.

```ini
# moonraker.conf

config_path:
#   The path to a directory where configuration files are located. This
#   directory may contain Klipper config files (printer.cfg) or Moonraker
#   config files (moonraker.conf).  Clients may also write their own config
#   files to this directory.  Note that this may not be the system root
#   (ie: "/") and moonraker must have read and write access permissions
#   for this directory.
log_path:
#   An optional path to a directory where log files are located.  Users may
#   configure various applications to store logs here and Moonraker will serve
#   them at "/server/files/logs/*".  The default is no log paths.
queue_gcode_uploads: False
#   When set to True the file manager will add uploads to the job_queue when
#   the `start_print` flag has been set.  The default if False.
enable_object_processing: False
#   When set to True gcode files will be run through a "preprocessor"
#   during metdata extraction if object tags are detected.  This preprocessor
#   replaces object tags with G-Code commands compatible with Klipper's
#   "cancel object" functionality.  Note that this process is file I/O intensive,
#   it is not recommended for usage on low resource SBCs such as a Pi Zero.
#   The default is False.
```

!!! Note
    It is also possible to enable object processing directly in the slicer.
    See the [preprocess-cancellation](https://github.com/kageurufu/cancelobject-preprocessor)
    documentation for details.

### `[database]`

The `database` section provides configuration for Moonraker's lmdb database.
If omitted defaults will be used.

```ini
moonraker.conf

database_path: ~/.moonraker_database
#   The path to the folder that stores Moonraker's lmdb database files.
#   It is NOT recommended to place this file in a location that is served by
#   Moonraker (such as the "config_path" or the location where gcode
#   files are stored).  If the folder does not exist an attempt will be made
#   to create it.  The default is ~/.moonraker_database.
enable_database_debug: False
#   For developer use only.  End users should leave this option set to False.
```
### `[data_store]`

The `data_store` section provides configuration for Moonraker's volatile
data store.  Note that this is different from the `database`, as it stores
data in memory and does not persist between restarts.  If omitted defaults
will be used.

```ini
# moonraker.conf

temperature_store_size: 1200
#   The maximum number of temperature values to store for each sensor. Note
#   that this value also applies to the "target", "power", and "fan_speed"
#   if the sensor reports them.  The default is 1200, which is enough to
#   store approximately 20 minutes of data at one value per second.
gcode_store_size:  1000
#   The maximum number "gcode lines" to store.  The default is 1000.
```

### `[job_queue]`

The `job_queue` section provides configuration for Moonraker's gcode job
queuing.  If omitted defaults will be used.

```ini
# moonraker.conf

load_on_startup: False
#   When set to true the job queue will attempt to load the next
#   pending job when Klipper reports as "Ready".  If the queue has
#   been paused it will automatically resume.  Note that neither
#   the job_transition_delay nor the job_transition_gcode are
#   applied in this case.  The default is False.
automatic_transition: False
#   When set to True the queue will automatically transition to
#   the next job in the queue after the current job is complete.
#   This is useful for belt printers and other machines with the
#   ability to automate clearing of the build area.  When False
#   the queue will be paused after each job is loaded, requiring
#   that users manually resume to load the next print.  The default
#   is False.
job_transition_delay:
#   The amount of time to delay after completion of a job before
#   loading the next job on the queue.  The default is no delay.
job_transition_gcode:
#   A gcode to execute after the completion of a job before the next
#   job is loaded.  If a "job_transition_delay" has been configured
#   this gcode will run after the delay.  The default is no gcode.
```
## Optional Components

Optional Components are only loaded if present in `moonraker.conf`.  This
includes components that may not have any configuration.

### `[authorization]`

The `[authorization]` section provides configuration for Moonraker's
authorization module.

```ini
# moonraker.conf

[authorization]
login_timeout:
#   The time, in days, after which a user is forced to re-enter their
#   credentials to log in.  This period begins when a logged out user
#   first logs in.  Successive logins without logging out will not
#   renew the timeout.  The default is 90 days.
trusted_clients:
 192.168.1.30
 192.168.1.0/24
 my-printer.lan
#   A list of newline separated ip addresses, ip ranges, or fully qualified
#   domain names that are trusted. Trusted clients are given full access to
#   the API.  Both IPv4 and IPv6 addresses and ranges are supported. Ranges
#   must be expressed in CIDR notation (see http://ip.sb/cidr for more info).
#   For example, an entry of 192.168.1.0/24 will authorize IPs in the range of
#   192.168.1.1 - 192.168.1.254.  Note that when specifying IPv4 ranges the
#   last segment of the ip address must be 0. The default is no clients are
#   trusted.
cors_domains:
  http://klipper-printer.local
  http://second-printer.local:7125
#   Enables CORS for the specified domains.  One may specify * if they wish
#   to allow all domains, however this should be an option reserved for
#   client developers and not used in production.  A * can also be used
#   to specify a wildcard that matches several domains.  For example:
#     *.local
#     http://*.my-domain.com
#     *.my-domain.com:*
#   Are all valid entries.  However, a wildcard may not be specified in
#   the top level domain:
#      http://my-printer.*
#   The above example will be rejected.
#   When CORS is enabled by adding an entry to this option, all origins
#   matching the "trusted_clients" option will have CORS headers set as
#   well.  If this option is not specified then CORS is disabled.
force_logins: False
#   When set to True a user login is required for authorization if at least
#   one user has been created, overriding the "trusted_clients" configuration.
#   If no users have been created then trusted client checks will apply.
#   The default is False.
```

### `[octoprint_compat]`
Enables partial support of Octoprint API is implemented with the purpose of
allowing uploading of sliced prints to a moonraker instance.
Currently we support Slic3r derivatives and Cura with Cura-Octoprint.

```ini
# moonraker.conf

[octoprint_compat]
enable_ufp: True
#   When set to True the octoprint_compat module will report that the UFP
#   plugin is available.  If the installed version of Cura supports UFP
#   files will be uploaded in UFP format.  When set to False Cura will
#   upload files in .gcode format.  This setting has no impact on other
#   slicers.  The default is True.
```

!!! Tip
    It is possible to embed "Prusa" style thumbnails in .gcode files using
    the latest version of Cura.  Select `Extensions` -> `Post Processing` ->
    `Modify G-Code`.  In the dialog click the `Add a script` button and select
    `Create Thumbnail`.   Change the width and height (most Moonraker clients
    handle 300x300 well) then click close.  A single large thumbnail is all
    that is necessary, Moonraker will generate a smaller 32x32 thumbnail from
    it.  This is convenient for users who do not wish to upload in UFP format.

### `[history]`
Enables print history tracking.

```ini
# moonraker.conf

[history]
```

### `[paneldue]`
Enables PanelDue display support.  The PanelDue should be connected to the
host machine, either via the machine's UART GPIOs or through a USB-TTL
converter.  Currently PanelDue Firmware Version 1.24 is supported.  Other
releases may not behave correctly.

```ini
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
```ini
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

### `[power]`
Enables device power control.  Multiple "power" devices may be configured,
each with their own section, ie: `[power my_printer]`, `[power my_led]`.

#### Options common to all power devices

The following configuration options are available for all power device types:

```ini
# moonraker.conf

[power device_name]
type:
#   The type of device.  Can be either gpio, rf, tplink_smartplug, tasmota
#   shelly, homeseer, homeassistant, loxonev1, or mqtt.
#   This parameter must be provided.
off_when_shutdown: False
#   If set to True the device will be powered off when Klipper enters
#   the "shutdown" state.  This option applies to all device types.
#   The default is False.
on_when_upload_queued: False
#   If set to True the device will power on if the file manager
#   queues an upload while the device is off.  This allows for an automated
#   "upload, power on, and print" approach directly from the slicer, see
#   the configuration example below for details. The default is False.
locked_while_printing: False
#   If True, locks the device so that the power cannot be changed while the
#   printer is printing. This is useful to avert an accidental shutdown to
#   the printer's power.  The default is False.
restart_klipper_when_powered: False
#   If set to True, Moonraker will issue a "FIRMWARE_RESTART" to Klipper
#   after the device has been powered on.  Note: If it isn't possible to
#   schedule a firmware restart (ie: Klippy is disconnected), the restart
#   will be postponed until Klippy reconnects and reports that startup is
#   complete.  In this scenario, if Klippy reports that it is "ready", the
#   FIRMWARE_RESTART will be aborted as unnecessary.
#   The default is False.
restart_delay: 1.
#   If "restart_klipper_when_powered" is set, this option specifies the amount
#   of time (in seconds) to delay the restart.  Default is 1 second.
bound_service:
#   Can be set to any service Moonraker is authorized to manage with the
#   exception of the moonraker service itself. See the tip below this section
#   for details on what services are authorized.  When a bound service has
#   been set the service will be started when the device powers on and stopped
#   when the device powers off.  The default is no service is bound to the
#   device.
```

!!! Tip
    Moonraker is authorized to manage the `klipper`, `klipper_mcu`,
    `webcamd`, `MoonCord`, `KlipperScreen`, and `moonraker-telegram-bot`
    services.  It can also manage multiple instances of a service, ie:
    `klipper_1`, `klipper_2`.  Keep in mind that service names are case
    sensitive.

!!! Note
    If a device has been bound to the `klipper` service and the
    `restart_klipper_when_powered` option is set to `True`, the restart
    will be scheduled to execute after Klipper reports that its startup
    sequence is complete.

#### GPIO Device Configuration

The following options are available for `gpio` device types:

```ini
# moonraker.conf

pin: gpiochip0/gpio26
#   The pin to use for GPIO and RF devices.  The chip is optional, if left out
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
timer:
#    A time (in seconds) after which the device will power off after being.
#    switched on. This effectively turns the device into a  momentary switch.
#    This option is available for gpio, tplink_smartplug, shelly, and tasmota
#    devices.  The timer may be a floating point value for gpio types, it should
#    be an integer for all other types.  The default is no timer is set.
```

!!! Note
    Moonraker can only be used to toggle host device GPIOs (ie: GPIOs on your
    PC or SBC).  Moonraker cannot control GPIOs on an MCU, Klipper should be
    used for this purpose.

Examples:

```ini
# moonraker.conf

# Control a relay providing power to the printer
[power printer]
type: gpio
pin: gpio26  # uses pin 26 on gpiochip0
off_when_shutdown: True
initial_state: off

# Control a status led
[power printer_led]
type: gpio
pin: !gpiochip0/gpio16  # inverts pin
initial_state: off

# Control a printer illumination, powers on when
# Moonraker starts
[power light_strip]
type: gpio
pin: gpiochip0/gpio17
initial_state: on
```

#### RF Device Configuration

The following options are available for gpio controlled `rf` device types:

```ini
# moonraker.conf

pin: gpiochip0/gpio26
#   The pin to use for GPIO and RF devices.  The chip is optional, if left out
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
timer:
#    A time (in seconds) after which the device will power off after being.
#    switched on. This effectively turns the device into a  momentary switch.
#    This option is available for gpio, tplink_smartplug, shelly, and tasmota
#    devices.  The timer may be a floating point value for gpio types, it should
#    be an integer for all other types.  The default is no timer is set.
on_code:
off_code:
#   Valid binary codes that are sent via the RF transmitter.
#   For example: 1011.
```

#### TPLink Smartplug Configuration

The following options are availble for `tplink_smartplug` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname for the tplink device.  "Power Strips" can
#   be controlled by including the socket index  in the ip address.  For example,
#   to control socket index 1:
#     192.168.1.127/1
#   This parameter must be provided.
port:
#   The port to connect to.  Default is 9999.
#
```

Example:

```ini
# moonraker.conf

[power printer_plug]
type: tplink_smartplug
address: 192.168.1.123
```

#### Tasmota Configuration

The following options are available for `tasmota` device types:

```ini
#   Note:
#   If your single-relay Tasmota device switches on/off successfully,
#   but fails to report its state, ensure that 'SetOption26' is set in
#   Tasmota.
address:
#   A valid ip address or hostname for the tasmota device.  This parameter
#   must be provided.
password:
#   A password used to authenticate requests.  Default is no password.
output_id:
#   The output_id (or relay id) to use if the Tasmota device supports
#   more than one output.  Default is 1.
```

!!! Note
    This implmentation communicates with Tasmota firmware through its
    HTTP APIs.  It is also possible to use [MQTT](#mqtt-device-configuration)
    to control devices flashed with Tasmota.

Example:

```ini
# moonraker.conf

[power tasmota_plug]
type: tasmota
address: 192.168.1.124
password: mypassword
```

#### Shelly Configuration

The following options are available for `shelly` device types:

```ini
address:
#   A valid ip address or hostname for the shelly device.  This parameter
#   must be provided.
user:
#   A user name to use for request authentication.  If no password is set
#   the the default is no user, otherwise the default is "admin".
password:
#   The password to use for request authentication.  The default is no
#   password.
output_id:
#   The output_id (or relay id) to use if the Shelly device supports
#   more than one output.  Default is 1.
```

!!! Note
    This implmentation communicates with Shelly firmware through its
    HTTP APIs.  It is also possible to use [MQTT](#mqtt-device-configuration)
    to control Shelly devices.

Example:

```ini
# moonraker.conf

[power shelly_plug]
type: shelly
address: 192.168.1.125
user: user2
password: password2
```

#### Homeseer Configuration

The following options are available for `homeseer` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname for the homeseer device.  This parameter
#   must be provided.
device:
#   The ID of the device to control.
#   To find out the ID in the HomeSeer UI, click on the device you want to
#   control (Make sure to click the sub-device that actually has On/Off
#   buttons).  And then go to the "status/graphics" tab and it should list
#   "ID" in the "advanced information" section.  This parameter must be
#   provided.
user:
#   The user name for request authentication.  This default is "admin".
password:
#   The password for request authentication.  The default is no password.
#
```

####  Home Assistant Configuration (HTTP)

The following options are available for `homeassistant` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname for the Home Assistant server.  This
#   parameter must be provided.
protocol:
#   The protocol for the URL to the Home Assistant server. Default is http.
port:
#   The port the Home Assistant server is listening on.  Default is 8123.
device:
#   The device ID of the switch to control. This parameter must be provided.
token:
#   A token used for request authorization.  This paramter must be provided.
domain:
#   The class of device managed by Home Assistant. Default is "switch".
status_delay: 1.0
#   The time (in seconds) to delay between requesting a device to turn
#   on/off and requesting its current status.  This is a workaround used
#   to validate that Home Assistant has successfully toggled the device,
#   as the API is currently broken on their end.  Default is 1 second.
#
```

Example:
```ini
# moonraker.conf

[power homeassistant_switch]
type: homeassistant
address: 192.168.1.126
port: 8123
device: switch.1234567890abcdefghij
token: home-assistant-very-long-token
domain: switch
```

#### Loxone Device Configuration

The following options are available for `loxone` device types:

```ini
address:
#   A valid ip address or hostname for the Loxone server.  This
#   parameter must be provided.
user:
#  The user name used for request authorization.  The default is "admin".
password:
#  The password used for request authorization.  The default is "admin".
output_id:
#   The name of a programmed output, virtual input or virtual
#   output in the loxone configuration.  The default is no output id.
#
```

#### MQTT Device Configuration

The following options are available for `mqtt` device types:

```ini
qos:
#  The MQTT QOS level to use when publishing and subscribing to topics.
#  The default is to use the setting supplied in the [mqtt] section.
command_topic:
#  The mqtt topic used to publish commands to the device.  This parameter must
#  be provided.
command_payload:
#  The payload sent with the topic.  This can be a template, with a "command"
#  variable included in the template context, where "command" is either "on"
#  or "off".  For example:
#    {% if command == "on" %}
#      TURN_ON
#    {% else %}
#      TURN_OFF
#  The above example would resolve to "TURN_ON" if the request is turn the
#  the device on, and "TURN_OFF" if the request is to turn the device off.
#  This parameter must be provided.
retain_command_state:
#  If set to True the retain flag will be set when the command topic is
#  published.  Default is False.
state_topic:
#  The mqtt topic to subscribe to for state updates.  This parameter must be
#  provided.
state_response_template:
#  A template used to parse the payload received with the state topic.  A
#  "payload" variable is provided the template's context.  This template
#  must resolve to "on" or "off".  For example:
#    {% set resp = payload|fromjson %}
#    {resp["POWER"]}
#  The above example assumes a json response is received, with a "POWER" field
#  that set to either "ON" or "OFF".  The resolved response will always be
#  trimmed of whitespace and converted to lowercase. The default is the payload.
state_timeout:
#  The amount of time (in seconds) to wait for the state topic to receive an
#  update. If the timeout expires the device revert to an "error" state.  This
#  timeout is applied during initialization and after a command has been sent.
#  The default is 2 seconds.
query_topic:
#  The topic used to query command state.  It is expected that the device will
#  respond by publishing to the "state_topic".  This parameter is optional,
query_payload:
#  The payload to send with the query topic.  This may be a template or a string.
#  The default is no payload.
query_after_command:
#  If set to True Moonraker will publish the query topic after publishing the
#  command topic.  This should only be necessary if the device does not publish a
#  reponse to a command request to the state topic.  The default is False.
```
!!! Note
    Moonraker's MQTT client must be properly configured to add a MQTT device.
    See the [mqtt](#mqtt) section for details.

!!! Tip
    MQTT is the most robust way of managing networked devices through
    Moonraker.  A well implemented MQTT device will publish all
    changes in state to the `state_topic`.  Moonraker recieves these changes,
    updates its internal state, and notifies connected clients.  This allows
    for device control outside of Moonraker.  Note however that post command
    actions, such as bound services, will not be run if a device is toggled
    outside of Moonraker.

Example:

```ini
# moonraker.conf

# Example configuration for ing with Tasmota firmware over mqtt
[power mqtt_plug]
type: mqtt
command_topic: cmnd/tasmota_switch/POWER
# Tasmota uses "on" and "off" as the payload, so our template simply renders
# the command
command_payload:
  {command}
# There is no need to set the retain flag for Tasmota devices.  Moonraker
# will use the query topic to initalize the device.  Tasmota will publish
# all changes in state to the state topic.
retain_command_state: False
# To query a tasmota device we send the command topic without a payload.
# Otpionally we could send a "?" as the payload.
query_topic: cmnd/tasmota_switch/POWER
# query_payload: ?
state_topic: stat/tasmota_switch/POWER
# The response is either "ON" or "OFF".  Moonraker will handle converting to
# lower case.
state_response_template:
  {payload}
# Tasmota updates the state topic when the device state changes, so it is not
# not necessary to query after a command
query_after_command: False
```

#### Toggling device state from Klipper

It is possible to toggle device power from the Klippy host, this can be done
with a gcode_macro, such as:
```ini
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
```ini
# printer.cfg

[delayed_gcode delayed_printer_off]
initial_duration: 0.
gcode:
  {% if printer.idle_timeout.state == "Idle" %}
    POWER_OFF_PRINTER
  {% endif %}

[idle_timeout]
gcode:
  M84
  TURN_OFF_HEATERS
  UPDATE_DELAYED_GCODE ID=delayed_printer_off DURATION=60
```

#### Power on G-Code Uploads

The following is an example configuration that would fully automate
the process of powering on a printer and loading a print from a
Slicer upload with the "start" flag enabled.

```ini
# moonraker.conf

# Configure the file manager to queue uploaded files when the "start" flag
# is set and Klipper cannot immediately start the print.
[file_manager]
queue_gcode_uploads: True
# Set the config_path and log_path options to the correct locations
config_path:
log_path:

# Configure the Job Queue to start a queued print when Klipper reports as
# ready.
[job_queue]
load_on_startup: True
# Configure the job_transition_delay and job_transition_gcode options
# if desired.  Note that they do no apply to prints loaded on startup.

# Configure the "power" device to turn on when uploads are queued.
[power printer]
type: gpio
pin: gpio26
initial_state: off
# Power the printer on when the file manager queues an upload
on_when_upload_queued: True
bound_service: klipper
```

With the above configuration options set, an upload with the "start"
flag set to true will be queued.  This "printer" device will be
notified and powered on.  Finally, the job_queue will load and start
the queued print after Klipper reports itself as "ready".

!!! Note
    This procedure assumes that the printer is powered off when the
    gcode file is uploaded.  It also assumes that the `job_queue` is
    empty, if any jobs exist in the queue then the next job on the
    queue will be loaded.


### `[update_manager]`
This enables moonraker's update manager.  Note that updates can only be
performed on pristine git repos.  Repos that have been modified on
disk or cloned from unofficial sources are not supported.

```ini
# moonraker.conf

[update_manager]
enable_repo_debug: False
#   When set to True moonraker will bypass repo validation and allow
#   updates from unofficial remotes and/or branches.  Updates on
#   detached repos are also allowed.  This option is intended for
#   developers and should not be used on production machines.  The
#   default is False.
enable_auto_refresh: False
#   When set to True Moonraker will attempt to fetch status about
#   available updates roughly every 24 hours, between 12am-4am.
#   When set to False Moonraker will only fetch update state on startup
#   and clients will need to request that Moonraker updates state.  The
#   default is False.
refresh_interval: 672
#   The interval (in hours) after which the update manager will check
#   for new updates.  This interval is applies to updates for Moonraker,
#   Klipper, and System Packages, and is the default for all clients.
#   The default is 672 hours (28 days).
enable_system_updates: True
#   A boolean value that can be used to toggle system package updates.
#   Currently Moonraker only supports updating packages via APT, so
#   this option is useful for users that wish to experiment with linux
#   distros that use other package management applications, or users
#   that prefer to manage their packages directly.  Note that if this
#   is set to False users will be need to make sure that all system
#   dependencies are up to date.  The default is True.
channel: dev
#   The update channel applied to Klipper and Moonraker.  May be 'dev'
#   which will fetch updates using git, or 'beta' which will fetch
#   zipped beta releases.  Note that this channel does not apply to
#   client updates, a client's update channel is determined by its
#   'type' option.  When this option is changed the next "update" will
#   swap channels, any untracked files in the application's path will be
#   removed during this process.  The default is dev.
```

#### Client Configuration
This allows client programs such as Fluidd, KlipperScreen, and Mainsail to be
updated in addition to klipper, moonraker, and the system os. Repos that have
been modified or cloned from unofficial sources are not supported.

Moonraker supports updates for "application" based clients and "web" based
clients. Each are detailed separately below.

```ini
# moonraker.conf

[update_manager client_name]
type: web
#   The client type.  For web clients this should be 'web', or 'web_beta'.
#   The 'web_beta' type will enable updates for releases tagged with
#   "prerelease" on GitHub.  This parameter must be provided.
repo:
#   This is the GitHub repo of the client, in the format of user/client.
#   For example, this could be set to fluidd-core/fluidd to update Fluidd or
#   mainsail-crew/mainsail to update Mainsail.  This parameter must be provided.
path:
#   The path to the client's files on disk.  This parameter must be provided.
persistent_files:
#   A list of newline separated file names that should persist between
#   updates.  This is useful for static configuration files, or perhaps
#   themes.  The default is no persistent files.
refresh_interval:
#   This overrides the refresh_interval set in the primary [update_manager]
#   section.
```

This second example is for "applications".  These may be git repositories
or zipped distributions.

Note that git repos must have at least one tag for Moonraker
to identify its version.

```ini
# moonraker.conf

# service_name must be the name of the systemd service
[update_manager service_name]
type: git_repo
#   Can be git_repo, zip, or zip_beta.  See your the client's documentation
#   for recommendations on which value to use.  Generally a git_repo is
#   an applications "dev" channel, zip_beta is its "beta" channel, and zip
#   is its "stable" channel.  This parameter must be provided.
path:
#   The absolute path to the client's files on disk. This parameter must be
#   provided.
#   Example:
#     path: ~/service_name
origin:
#   The full git URL of the "origin" remote for the repository.  This can
#   be be viewed by navigating to your repository and running:
#     git remote -v
#   This parameter must be provided.
primary_branch:
#   The name of the primary branch used for release code on this repo.  This
#   option allows clients to specify 'main', or their own unique name, as
#   the branch used for repo validity checks.  The default is master.
env:
#   The path to the client's virtual environment executable on disk.  For
#   example, Moonraker's venv is located at ~/moonraker-env/bin/python.
#   The default is no env, which disables updating python packages.
requirements:
#  This is the location in the repository to the client's virtual environment
#  requirements file. This location is relative to the root of the repository.
#  This parameter must be provided if the "env" option is set, otherwise it
#  should be omitted.
install_script:
#  The file location, relative to the repository, for the installation script.
#  The update manager parses this file for "system" packages that need updating.
#  The default is no install script, which disables system package updates
enable_node_updates:
#   When set to True, Moonraker will assume that this repo relies upon node
#   and will attempt to execute "npm ci --only=prod" when it detects a change
#   to package-lock.json.  Note that if your project does not have a
#   package-lock.json in its root directory then the plugin will fail to load.
#   Default is False.
host_repo:
#   The GitHub repo in which zipped releases are hosted.  Note that this does
#   not need to match the repository in the "origin" option, as it is possible
#   to use a central GitHub repository to host multiple client builds.  As
#   an example, Moonraker's repo hosts builds for both Moonraker and Klipper.
#   This option defaults to the repo extracted from the "origin" option,
#   however if the origin is not hosted on GitHub then this parameter must
#   be provided.
is_system_service: True
#   If set to true the update manager will attempt to use systemctl to restart
#   the service after an update has completed.  This can be set to flase for
#   repos that are not installed as a service.  The default is True.
refresh_interval:
#   This overrides the refresh_interval set in the primary [update_manager]
#   section.
```

### `[mqtt]`

Enables an MQTT Client.  When configured most of Moonraker's APIs are availble
by publishing JSON-RPC requests to `{instance_name}/moonraker/api/request`.
Responses will be published to `{instance_name}/moonraker/api/response`. See
the [API Documentation](web_api.md#json-rpc-api-overview) for details on
on JSON-RPC.

It is also possible for other components within Moonraker to use MQTT to
publish and subscribe to topics.

```ini
[mqtt]
address:
#   Address of the Broker.  This may be a hostname or IP Address.  This
#   parameter must be provided.
port:
#   Port the Broker is listening on.  Default is 1883.
username:
#   An optional username used to log in to the Broker.  Default is no
#   username (an anonymous login will be attempted)
password_file:
#   An optional path to a text file containing a password used to log in
#   to the broker.  It is strongly recommended that this file be located
#   in a folder not served by Moonraker.  It is also recommended that the
#   password be unique and not used for other logins, as it is stored in
#   plain text.  To create a password file, one may ssh in to the device
#   and enter the following commands:
#      cd ~
#      echo mypassword > .mqttpass
#   Then set this option to:
#     ~/.mqttpass
#   If this option is omitted no password will be used to login.
mqtt_protocol: v3.1.1
#   The protocol to use when connecting to the Broker.  May be v3.1,
#   v3.1.1, and v5.  The default is v3.1.1
enable_moonraker_api: True
#   If set to true the MQTT client will subscribe to API topic, ie:
#     {instance_name}/moonraker/api/request
#   This can be set to False if the user does not wish to allow API
#   requests over MQTT.  The default is True.
instance_name:
#   An identifer used to create unique API topics for each instance of
#   Moonraker on network.  This name cannot contain wildcards (+ or #).
#   For example, if the instance name is set to my_printer, Moonraker
#   will subscribe to the following topic for API requests:
#     my_printer/moonraker/api/request
#   Responses will be published to the following topic:
#     my_printer/moonraker/api/response
#   The default is the machine's hostname.
status_objects:
#   A newline separated list of Klipper objects whose state will be
#   published in the payload of the following topic:
#      {instance_name}/klipper/status
#   For example, this option could be set as follows:
#
#     status_objects:
#       webhooks
#       toolhead=position,print_time,homed_axes
#       extruder=temperature
#
#   In the example above, all fields of the "webhooks" object will be tracked
#   and changes will be published.  Only the "position", "print_time", and
#   "homed_axes" fields of the "toolhead" will be tracked.  Likewise, only the
#   "temperature" field of the extruder will be tracked. See the
#   "Printer Objects" section of the documentation for an overview of the most
#   common objects available.
#
#   Note that Klipper will only push an update to an object/field if the field
#   has changed.  An object with no fields that have changed will not be part
#   of the payload.  Object state is checked and published roughly every 250 ms.
#
#   If not configured then no objects will be tracked and published to
#   the klipper/status topic.
default_qos: 0
#   The default QOS level used when publishing or subscribing to topics.
#   Must be an integer value from 0 to 2.  The default is 0.
api_qos:
#   The QOS level to use for the API topics. If not provided, the
#   value specified by "default_qos" will be used.
```

### `[wled]`
Enables control of an WLED strip.

```ini
# moonraker.conf

[wled strip_name]
address:
#   The address should be a valid ip or hostname for the wled webserver and
#   must be specified
initial_preset:
#   Initial preset ID (favourite) to use. If not specified initial_colors
#   will be used instead.
initial_red:
initial_green:
initial_blue:
initial_white:
#   Initial colors to use for all neopixels should initial_preset not be set,
#   initial_white will only be used for RGBW wled strips (defaults: 0.5)
chain_count:
#   Number of addressable neopixels for use (default: 1)
color_order:
#   Color order for WLED strip, RGB or RGBW (default: RGB)

```
Below are some potential examples:
```ini
# moonraker.conf

[wled case]
address: led1.lan
initial_preset: 45
chain_count: 76

[wled lounge]
address: 192.168.0.45
initial_red: 0.5
initial_green: 0.4
initial_blue: 0.3
chain_count: 42
```

It is possible to control wled from the klippy host, this can be done using
one or more macros, such as:

```ini
# printer.cfg

[gcode_macro WLED_ON]
description: Turn WLED strip on using optional preset
gcode:
  {% set strip = params.STRIP|string %}
  {% set preset = params.PRESET|default(-1)|int %}

  {action_call_remote_method("set_wled_state",
                             strip=strip,
                             state=True,
                             preset=preset)}

[gcode_macro WLED_OFF]
description: Turn WLED strip off
gcode:
  {% set strip = params.STRIP|string %}

  {action_call_remote_method("set_wled_state",
                             strip=strip,
                             state=False)}

[gcode_macro SET_WLED]
description: SET_LED like functionlity for WLED
gcode:
    {% set strip = params.STRIP|string %}
    {% set red = params.RED|default(0)|float %}
    {% set green = params.GREEN|default(0)|float %}
    {% set blue = params.BLUE|default(0)|float %}
    {% set white = params.WHITE|default(0)|float %}
    {% set index = params.INDEX|default(-1)|int %}
    {% set transmit = params.TRANSMIT|default(1)|int %}

    {action_call_remote_method("set_wled",
                               strip=strip,
                               red=red, green=green, blue=blue, white=white,
                               index=index, transmit=transmit)}
```

### `[zeroconf]`
Enable Zeroconf service registration allowing external services to more
easily detect and use Moonraker instances.

```ini
# moonraker.conf

[zeroconf]
```


## Jinja2 Templates

Some Moonraker configuration options make use of Jinja2 Templates.  For
consistency, Moonraker uses the same Jinja2 syntax as Klipper. Statements
should be enclosed in `{% %}`, and expressions in `{ }`.  There are some
key differences, as outlined below:

- Moonraker templates do not currently include globals like
 `printer` and `params` in the context.  Variables included in the
 context will be specified in the option's documentation.
- Moonraker's template environment adds the `ext.do` extension.  The
  `{% do expression %}` statement can be used to modify variables without
  printing any text.  See the example below for details.
- Klipper uses Jinja2 exclusively for evaluating gcode statements.  Moonraker
  uses it to provide configuration options that may need to change based on
  runtime parameters.

For an example of how to use the `do` statement, lets assume we need to
send a specific json payload with an MQTT power device command.  Rather
than attempt to type out the json ourselves, it may be easier to create
a `dictionary` object and convert it to json:
```ini
# moonraker.conf

[power my_mqtt_device]
type: mqtt
command_topic: my/mqtt/command
# Lets assume this device requres a json payload with each command.
# We will use a dict to generate the payload
command_payload:
  {% set my_payload = {} %}
  {% do my_payload["SOME_FIELD"] = "some_string" %}
  {% do my_payload["ANOTHER_FIELD"] = True %}
  # Here we set the actual command, the "command" variable
  # is passed to the context of this template
  {% do my_payload["POWER_COMMAND"] = command %}
  # generate the json output
  { my_payload|tojson }
```
