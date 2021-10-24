#
This document describes Moonraker's full configuration.  As this file
references configuration for both Klipper (`printer.cfg`) and Moonraker
(`moonraker.conf`), each example contains a commment indicating which
configuration file is being refrenenced. A basic
[sample configuration](./moonraker.conf) in the `docs` directory.

## `[server]`

The `[server]` section provides essential configuration for Moonraker
and its core components.  This section is requrired.

```ini

# moonraker.conf

[server]
host: 0.0.0.0
#  The host address in which to bind the HTTP server.  Default is to bind
#  to all interfaces
port: 7125
#   The port the HTTP server will listen on.  Default is 7125
ssl_port: 7130
#   The port to listen on for SSS (HTTPS) connections.  Note that the HTTPS
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
config_path:
#   The path to a directory where configuration files are located. This
#   directory may contain Klipper config files (printer.cfg) or Moonraker
#   config files (moonraker.conf).  Clients may also write their own config
#   files to this directory.  Note that this may not be the system root
#   (ie: "/") and moonraker must have read and write access permissions
#   for this directory.
database_path: ~/.moonraker_database
#   The path to the folder that stores Moonraker's lmdb database files.
#   It is NOT recommended to place this file in a location that is served by
#   Moonraker (such as the "config_path" or the location where gcode
#   files are stored).  If the folder does not exist an attempt will be made
#   to create it.  The default is ~/.moonraker_database.
log_path:
#   An optional path to a directory where log files are located.  Users may
#   configure various applications to store logs here and Moonraker will serve
#   them at "/server/files/logs/*".  The default is no log paths.
enable_database_debug: False
#   For developer use only.  End users should leave this option set to False.
temperature_store_size: 1200
#   The maximum number of temperature values to store for each sensor. Note
#   that this value also applies to the "target", "power", and "fan_speed"
#   if the sensor reports them.  The default is 1200, which is enough to
#   store approximately 20 minutes of data at one value per second.
gcode_store_size:  1000
#   The maximum number "gcode lines" to store.  The default is 1000.
```
## `[authorization]`

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

## `[octoprint_compat]`
Enables partial support of Octoprint API is implemented with the purpose of
allowing uploading of sliced prints to a moonraker instance.
Currently we support Slic3r derivatives and Cura with Cura-Octoprint.

```ini
# moonraker.conf

[octoprint_compat]
```

## `[history]`
Enables print history tracking.

```ini
# moonraker.conf

[history]
```

## `[paneldue]`
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

## `[power]`
Enables device power control.  Currently GPIO (relays), RF transmitter, TPLink Smartplug,
and Tasmota (via http) devices, HomeAssistant switch are supported.

```ini
# moonraker.conf

[power device_name]
type: gpio
#   The type of device.  Can be either gpio, rf, tplink_smartplug, tasmota
#   shelly, homeseer, homeassistant, or loxonev1.
#   This parameter must be provided.
off_when_shutdown: False
#   If set to True the device will be powered off when Klipper enters
#   the "shutdown" state.  This option applies to all device types.
#   The default is False.
locked_while_printing: False
#   If True, locks the device so that the power cannot be changed while the
#   printer is printing. This is useful to avert an accidental shutdown to
#   the printer's power.  The default is False.
restart_klipper_when_powered: False
#   If set to True, Moonraker will issue a "FIRMWARE_RESTART" to Klipper
#   after the device has been powered on.  The default is False, thus no
#   attempt to made to restart Klipper after power on.
bound_service:
#   Can be set to any service Moonraker is authorized to manage with the
#   exception of the moonraker service itself. See the tip below this section
#   for details on what services are authorized.  When a bound service has
#   been set the service will be started when the device powers on and stopped
#   when the device powers off.  The default is no service is bound to the
#   device.
restart_delay: 1.
#   If "restart_klipper_when_powered" is set, this option specifies the amount
#   of time (in seconds) to delay the restart.  Default is 1 second.
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
#
address:
port:
#   The above options are used for "tplink_smartplug" devices.  The
#   address should be a valid ip or hostname for the tplink device.
#   The port should be the port the device is configured to use.
#   "Power Strips" can be controlled by including the socket index
#   in the ip address.  For example, to control socket index 1:
#     192.168.1.127/1
#    The address must be provided. The port defaults to 9999.
#
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
#
address:
user:
password:
output_id:
#   The above options are used for "shelly" devices.  The
#   address should be a valid ip or hostname for the Shelly device.
#   Provide a user and password if configured in Shelly (default is empty).
#   If password is set but user is empty the default user "admin" will be used
#   Provided an output_id (relay id) if the Shelly device supports
#   more than one (default is 0).
#
address:
device:
user:
password:
#   The above options are used for "homeseer" devices.  The
#   address should be a valid ip or hostname for the homeseer controller.
#   "device" should be the ID of the device to control.
#   To find out the ID, in the HomeSeer UI, click on the device you want to
#   control (Make sure to click the sub-device that actually has On/Off
#   buttons).  And then go to the "status/graphics" tab and it should list
#   "ID" in the "advanced information" section.
#   Provide a user and password with access to "device control"
#   and at least the specific device you want to control
#
address:
port:
device:
token:
domain:
#   The above options are used for "homeassistant" devices.  The
#   address should be a valid ip or hostname for the homeassistant controller.
#   "device" should be the ID of the switch to control.
#   "domain" is the class of device set managed by homeassistant, defaults to "switch".
#
address:
user:
password:
output_id:
#   The above options are used for "loxone smart home miniserver v1 " devices.
#   The address should be a valid ip or hostname for the loxone miniserver v1
#   device. All entries must be configured in advance in the loxone config.
#   Provide a user and password configured in loxone config.
#   The output_id is the name of a programmed output, virtual input or virtual
#   output in the loxone config his output_id (name) may only be used once in
#   the loxone config
#
on_code:
off_code:
#   The above options are used for "rf" devices.  The
#   codes should be valid binary codes that are send via the RF transmitter.
#   For example: 1011.
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

Below are some potential examples:
```ini
# moonraker.conf

[power printer]
type: gpio
pin: gpio26
off_when_shutdown: True
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

[power shelly_plug]
type: shelly
address: 192.168.1.125
user: user2
password: password2

[power homeassistant_switch]
type: homeassistant
address: 192.168.1.126
port: 8123
device: switch.1234567890abcdefghij
token: home-assistant-very-long-token
domain: switch
```

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

## `[update_manager]`
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

### Client Configuration
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
#   For example, this could be set to cadriel/fluidd to update Fluidd or
#   meteyou/mainsail to update Mainsail.  This parameter must be provided.
path:
#   The path to the client's files on disk.  This parameter must be provided.
persistent_files:
#   A list of newline separated file names that should persist between
#   updates.  This is useful for static configuration files, or perhaps
#   themes.  The default is no persistent files.
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
#   When set to True, Moonraker will asssume that this repo relies upon node
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
```

## `[mqtt]`

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
