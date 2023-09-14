#
This document describes Moonraker's full configuration. By default Moonraker
loads the configuration file from `~/moonraker.conf`, however prebuilt
images such as MainsailOS and FluiddPi configure Moonraker to load the
configuration from `~/klipper_config/moonraker.conf`.

As this document references configuration for both Klipper (`printer.cfg`)
and Moonraker (`moonraker.conf`), each example contains a comment indicating
which configuration file is being referenced A basic
[sample configuration](./moonraker.conf) in the `docs` directory.

Moonraker uses an ini style configuration very close to that of Klipper.
Inline comments are supported, prefixed by either a `#` or `;`.  If it
is necessary to use one of those characters in an option, they may be
escaped using backslash, ie `\#`.  For example:

```ini
# This is a comment
[section_name] # This is a comment
opt: \# This is not a comment
```

Moonraker uses strict parsing rules.  A configuration file may not
contain multiple sections of the same name.  A section may not contain
multiple options of the same name.   However, configuration files included
using [include directives](#include-directives) may contain sections
specified in other files, and those sections may contain options
specified in other files.

## Core Components

Moonraker's core components are always loaded regardless of configuration.

### `[server]`

The `[server]` section provides essential configuration for Moonraker.
This section is required.

```ini

# moonraker.conf

[server]
host: 0.0.0.0
#  The host address in which to bind the HTTP server.  Default is to bind
#  to all ipv4 interfaces.  If set to "all" the server will bind to all
#  ipv4 an ipv6 interfaces.
port: 7125
#   The port the HTTP server will listen on.  Default is 7125
ssl_port: 7130
#   The port to listen on for SSL (HTTPS) connections.  Note that the HTTPS
#   server will only be started of the certificate and key options outlined
#   below are provided.  The default is 7130.
klippy_uds_address: /tmp/klippy_uds
#   The address of Unix Domain Socket used to communicate with Klippy. This
#   option accepts Jinja2 Templates, where the configured data path is
#   passed to the template context, for example:
#     klippy_uds_address: {data_path}/comms/klippy.sock
#
#   Default is /tmp/klippy_uds.
route_prefix:
#   A prefix prepended to the path for each HTTP endpoint.  For example
#   if the route_prefix is set to moonraker/printer1, then the server info
#   endpoint is available at:
#     http://myprinter.local/moonraker/printer1/server/info
#
#   This is primarily useful for installations that feature multiple instances
#   of Moonraker, as it allows a reverse proxy identify the correct instance based
#   on the path and redirect requests without a rewrite.  Note that frontends must feature
#   support for HTTP endpoints with a route prefix to communicate with Moonraker when
#   this option is set. The default is no route prefix.
max_upload_size: 1024
#   The maximum size allowed for a file upload (in MiB).  Default is 1024 MiB.
max_websocket_connections:
#   The maximum number of concurrently open websocket connections.
#   The default is 50.
enable_debug_logging: False
#   ***DEPRECATED***
#   Verbose logging is enabled by the '-v' command line option.
```

!!! Note
    Previously the `[server]` section contained `ssl_certificate_path` and
    `ssl_key_path` options. These options are now deprecated, as both locations
    are determined by the `data path` and `alias` configured on the command
    line, ie `<data_file_path>/certs/<alias>.cert`.  By default the certificate
    path resolves to `$HOME/moonraker_data/certs/moonraker.cert` and the key
    path resolves to `$HOME/moonraker_data/certs/moonraker.key`.  Both files
    may be symbolic links.

### `[file_manager]`

The `file_manager` section provides configuration for Moonraker's file
management functionality.  If omitted defaults will be used.

```ini
# moonraker.conf
queue_gcode_uploads: False
#   When set to True the file manager will add uploads to the job_queue when
#   the `start_print` flag has been set.  The default if False.
enable_object_processing: False
#   When set to True gcode files will be run through a "preprocessor"
#   during metadata extraction if object tags are detected.  This preprocessor
#   replaces object tags with G-Code commands compatible with Klipper's
#   "cancel object" functionality.  Note that this process is file I/O intensive,
#   it is not recommended for usage on low resource SBCs such as a Pi Zero.
#   The default is False.
file_system_observer: inotify
#   The observer used to monitor file system changes.  May be inotify or none.
#   When set to none file system observation is disabled.  The default is
#   inotify.
enable_observer_warnings: True
#   When set to True Moonraker will generate warnings when an observer
#   encounters an error. This may be useful to determine if the observer
#   malfunctioning. The default is True.
enable_inotify_warnings: True
#   *** DEPRECATED - SEE "enable_observer_warnings" ***
#   When set to True Moonraker will generate warnings when inotify attempts
#   to add a duplicate watch or when inotify encounters an error.  On some
#   file systems inotify may not work as expected, this gives users the
#   option to suppress warnings when necessary.  The default is True.
```

!!! Note:
    Previously the `[file_manager]` section contained `config_path` and
    `log_path` options. These options are now deprecated, as both locations
    are determined by the `data path` configured on the command line.

!!! Tip
    It is also possible to enable object processing directly in the slicer.
    See the [preprocess-cancellation](https://github.com/kageurufu/cancelobject-preprocessor)
    documentation for details.

### `[machine]`

The `machine` section provides configuration for Moonraker's machine component, which
is responsible for for collecting "machine" (ie: PC, SBC, etc) data and communicating
with system services such as systemd.

```ini
# moonraker.conf
[machine]
provider: systemd_dbus
#   The provider implementation used to collect system service information
#   and run service actions (ie: start, restart, stop).  This can be "none",
#   "supervisord_cli", "systemd_dbus", or "systemd_cli".  If the provider is
#   set to "none" service action APIs will be disabled.
#   The default is systemd_dbus.
shutdown_action: poweroff
#   Determines the action Moonraker will take when a shutdown is requested.
#   This option may be set to "halt" or "poweroff. Not all linux distributions
#   support poweroff, in such scenarios it is necessary to specify 'halt'.
#   The default is "poweroff".
sudo_password:
#   The password for the linux user.  When set Moonraker can run linux commands
#   that require elevated permissions.  This option accepts Jinja2 Templates,
#   see the [secrets] section for details.  It is strongly recommended to only
#   set this option when required and to use the aforementioned secrets module
#   when doing so.  The default is no sudo password is set.
validate_service:
#   Enables validation of Moonraker's systemd service unit.  If Moonraker
#   detects that a change is necessary it will attempt to do so.  Custom
#   installations and installations that do systemd should set this to False.
#   The default is True.
validate_config:
#   Enables validation of Moonraker's configuration.  If Moonraker detects
#   deprecated options it will attempt to correct them.  The default is True.
force_validation:
#   By default Moonraker will not attempt to revalidate if a previous attempt
#   at validation successfully completed. Setting this value to True will force
#   Moonraker to perform validation.  The default is False.
supervisord_config_path:
#   Path to the supervisord config file. This is required when for multiple
#   supervisord are instances running on single machine and the default
#  '/var/run/supervisord.sock' is occupied by other services.
#   The default is no path.
```

!!! Note
    See the [install documentation](installation.md#policykit-permissions) for
    details on PolicyKit permissions when using the DBus provider.

!!! Warning
    Some distributions (ie: DietPi) disable and mask the `systemd-logind`
    service.  This service is necessary for the DBus provider to issue
    `reboot` and `shutdown` commands.  In this scenario, Moonraker will fall
    back to CLI based `reboot` and `shutdown` commands.  These commands require
    that Moonraker be able to run `sudo` commands without a password or that the
    `sudo_password` option is set.

    Alternatively it may be possible to enable the `systemd-logind` service,
    consult with your distributions's documentation.

#### Allowed Services

The `machine` component uses the configured provider to manage services
on the system (ie: restart a service).  Moonraker is authorized to manage
the `moonraker` and `klipper` services, including those that match common
multi-instance patterns, such as `moonraker-1`, `klipper_2`, and `moonraker1`.

Moonraker may be authorized to manage additional services by modifying
`<data_folder>/moonraker.asvc`.  By default this file includes the
following services:

- `klipper_mcu`
- `webcamd`
- `MoonCord`
- `KlipperScreen`
- `moonraker-telegam-bot`
- `sonar`
- `crowsnest`

#### Reboot / Shutdown from Klipper

It is possible to call the `shutdown_machine` and `reboot_machine`
remote methods from a gcode macro in Klipper.  For example:

```ini
# printer.cfg

[gcode_macro SHUTDOWN]
gcode:
  {action_call_remote_method("shutdown_machine")}

[gcode_macro REBOOT]
gcode:
  {action_call_remote_method("reboot_machine")}
```

### `[database]`

!!! Note:
    This section no long has configuration options.  Previously the
    `database_path` option was used to determine the locatation of
    the database folder, it is now determined by the `data path`
    configured on the command line.

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

### `[announcements]`

The `announcements` section provides supplemental configuration for
Moonraker announcements.  If omitted defaults will be used.

```ini
# moonraker.conf

[announcements]
subscriptions:
#   A newline separated list announcement "subscriptions".  Generally
#   this would refer to specific clients that are registered to provide
#   announcements.  All items specified here are added in addition to
#   "moonraker" and "klipper", which are always subscribed to.  The default
#   is no additional subscriptions.
dev_mode: False
#   A developer option that fetches RSS announcements from a local folder when
#   set to True. The default behavior is for Moonraker to retrieve announcements
#   from RSS feeds generated by the "moonlight" repo on GitHub.
```

### `[webcam]`

The `webcam` module provides unified webcam configuration management.  Webcams
may be configured directly through front-ends and added to the database,
however it is also possible for users to configure one or more webcams in
`moonraker.conf`.  If a webcam is configured in `moonraker.conf` it takes
precedent over a webcam in the database by the same name.  The options
available may not apply to all front ends, refer to your front end's
documentation for details on camera configuration.

```ini
[webcam my_camera_name]
location: printer
#   A description of the webcam location, ie: what the webcam is observing.
#   The default is "printer".
icon:
#   A name of the icon to use for the camera.  The default is mdiWebcam.
enabled: True
#   An optional boolean value to indicate if this webcam should be enabled.
#   Default is True.
service: mjpegstreamer
#   The name of the application or service hosting the webcam stream.  Front-
#   ends may use this configuration to determine how to launch or start the
#   program.  The default is "mjpegstreamer".
target_fps: 15
#   An integer value specifying the target framerate.  The default is 15 fps.
target_fps_idle: 5
#   An integer value specifying the target framerate when the printer is idle.
#   The default is 5 fps.
stream_url:
#   The url for the camera stream request.  This may be a full url or a
#   relative path (ie: /webcam?action=stream) if the stream is served on the
#   same host as Moonraker at port 80.  This parameter must be provided.
snapshot_url:
#   The url for the camera snapshot request.  This may be a full url or a
#   relative path (ie: /webcam?action=stream) if the stream is served on the
#   same host as Moonraker at port 80.  The default is an empty url.
flip_horizontal: False
#   A boolean value indicating whether the stream should be flipped
#   horizontally.  The default is false.
flip_vertical: False
#   A boolean value indicating whether the stream should be flipped
#   vertically.  The default is false.
rotation: 0
#   An integer value indicating the amount of clockwise rotation to apply
#   to the stream.  May be 0, 90, 180, or 270.  The default is 0.
aspect_ratio: 4:3
#   The aspect ratio to display for the camera.  Note that this option
#   is specific to certain services, otherwise it is ignored.
#   The default is 4:3.
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
enable_api_key: True
#   Enables API Key authentication.  The default is True.
login_timeout:
#   The time, in days, after which a user is forced to re-enter their
#   credentials to log in.  This period begins when a logged out user
#   first logs in.  Successive logins without logging out will not
#   renew the timeout.  The default is 90 days.
max_login_attempts:
#   Maximum number of consecutive failed login attempts before an IP address
#   is locked out.  Failed logins are tracked per IP and are reset upon a
#   successful login.  Locked out IPs are reset when Moonraker restarts.
#   By default there is no maximum number of logins.
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
default_source: moonraker
#   The default source used to authenticate user logins. Can be "ldap" or
#   "moonraker"  The default is "moonraker".
```

### `[ldap]`

The `ldap` module may be used by `[authorization]` to perform user
authentication though an ldap server.

```ini
# moonraker.conf

[ldap]
ldap_host: ldap.local
#   The host address of the LDAP server.  This parameter must be provided
ldap_port:
#   The LDAP server's port.  The default is 389 for standard connections
#   and 636 for SSL/TLS connections.
ldap_secure: True
#   Enables LDAP over SSL/TLS. The default is False.
base_dn: DC=ldap,DC=local
#   The base distinguished name used to search for users on the server.
#   This option accepts Jinja2 Templates, see the [secrets] section for details.
#   This parameter must be provided.
bind_dn: {secrets.ldap_credentials.bind_dn}
#   The distinguished name for bind authentication.  For example:
#       CN=moonraker,OU=Users,DC=ldap,DC=local
#   This option accepts Jinja2 Templates, see the [secrets] section for
#   details.  By default the ldap client will attempt to bind anonymously.
bind_password: {secrets.ldap_credentials.bind_password}
#   The password for bind authentication. This option accepts Jinja2 Templates,
#   see the [secrets] section for details.  This parameter must be provided
#   if a "bind_dn" is specified, otherwise it must be omitted.
group_dn: CN=moonraker,OU=Groups,DC=ldap,DC=local
#   A group distinguished name in which the user must be a member of to pass
#   authentication.  This option accepts Jinja2 Templates, see the [secrets]
#   section for details. The default is no group requirement.
is_active_directory: True
#   Enables support for Microsoft Active Directory. This option changes the
#   field used to lookup a user by username to sAMAccountName.
#   The default is False.
user_filter: (&(objectClass=user)(cn=USERNAME))
#   Allows filter of users by custom LDAP query. Must contain the USERNAME
#   token, it will be replaced by the user's username during lookup. Will
#   override the change done by is_active_directory. This option accepts
#   Jinja2 Templates, see the [secrets] section for details.
#   The default is empty, which will change the lookup query depending on
#   is_active_directory.
```

### `[octoprint_compat]`
Enables partial support of OctoPrint API is implemented with the purpose of
allowing uploading of sliced prints to a moonraker instance.
Currently we support Slic3r derivatives and Cura with Cura-OctoPrint.

```ini
# moonraker.conf

[octoprint_compat]
enable_ufp: True
#   When set to True the octoprint_compat module will report that the UFP
#   plugin is available.  If the installed version of Cura supports UFP
#   files will be uploaded in UFP format.  When set to False Cura will
#   upload files in .gcode format.  This setting has no impact on other
#   slicers.  The default is True.

flip_h: False
#   Set the webcam horizontal flip.  The default is False.
flip_v: False
#   Set the webcam vertical flip.  The default is False.
rotate_90: False
#   Set the webcam rotation by 90 degrees.  The default is False.
stream_url: /webcam/?action=stream
#   The URL to use for streaming the webcam.  It can be set to an absolute
#   URL if needed. In order to get the webcam to work in Cura through
#   an OctoPrint connection, you can set this value to
#   http://<octoprint ip>/webcam/?action=stream.  The default value is
#   /webcam/?action=stream.
webcam_enabled: True
#   Enables the webcam.  The default is True.
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
#  When one of these macros is executed the PanelDue will prompt
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
#   The type of device.  Can be either gpio, klipper_device, rf,
#   tplink_smartplug, tasmota, shelly, homeseer, homeassistant, loxonev1,
#   smartthings, mqtt or hue.
#   This parameter must be provided.
off_when_shutdown: False
#   If set to True the device will be powered off when Klipper enters
#   the "shutdown" state.  This option applies to all device types.
#   The default is False.
off_when_shutdown_delay: 0
#   If "off_when_shutdown" is set, this option specifies the amount of time
#   (in seconds) to wait before turning the device off. Default is 0 seconds.
on_when_job_queued: False
#   If set to True the device will power on if a job is queued while the
#   device is off.  This allows for an automated "upload, power on, and
#   print" approach directly from the slicer, see the configuration example
#   below for details. The default is False.
locked_while_printing: False
#   If True, locks the device so that the power cannot be changed while the
#   printer is printing. This is useful to avert an accidental shutdown to
#   the printer's power.  The default is False.
restart_klipper_when_powered: False
#   If set to True, Moonraker will schedule a "FIRMWARE_RESTART" to command
#   after the device has been powered on. If it isn't possible to immediately
#   schedule a firmware restart (ie: Klippy is disconnected), the restart
#   will be postponed until Klippy reconnects and reports that startup is
#   complete.  Prior to scheduling the restart command the power device will
#   always check Klippy's state.  If Klippy reports that it is "ready", the
#   FIRMWARE_RESTART will be aborted as unnecessary.
#   The default is False.
restart_delay: 1.
#   If "restart_klipper_when_powered" is set, this option specifies the amount
#   of time (in seconds) to delay the restart.  Default is 1 second.
bound_services:
#   A newline separated list of services that are "bound" to the state of this
#   device.  When the device is powered on all bound services will be started.
#   When the device is powered off all bound services are stopped.
#
#   The items in this list are limited to those specified in the allow list,
#   see the [machine] configuration documentation for details.  Additionally,
#   the Moonraker service can not be bound to a power device.  Note that
#   service names are case sensitive.
#
#   The default is no services are bound to the device.
```

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
#    This option is available for gpio, klipper_device, tplink_smartplug,
#    shelly, and tasmota devices.  The timer may be a floating point value
#    for gpio types, it should be an integer for all other types.  The
#    default is no timer is set.
```

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

#### Klipper Device Configuration

The following options are available for `klipper_device` device types:

```ini
# moonraker.conf

object_name: output_pin my_pin
#    The Klipper object_name (as defined in your Klipper config).  Valid examples:
#      output_pin my_pin
#      gcode_macro MY_MACRO
#    Currently, only `output_pin` and `gcode_macro` Klipper devices are
#    supported.  See the note below for macro restrictions. Keep in mind that
#    the object name is case sensitive.  This parameter must be provided.
timer:
#    A time (in seconds) after which the device will power off after being.
#    switched on. This effectively turns the device into a  momentary switch.
#    This option is available for gpio, klipper_device, tplink_smartplug,
#    shelly, and tasmota devices.  The timer may be a floating point value
#    for gpio types, it should be an integer for all other types.  The
#    default is no timer is set.
```

!!! Warning
    Klipper devices cannot be used to toggle the printer's power supply as they
    require that Klipper be running and in the "Ready" state.

!!! Note
    Some of the options common to all `[power]` devices are not available for
    the `klipper_device` type.  Specifically `off_when_shutdown` and
    `restart_klipper_when_powered` may not be configured.  The `bound_service`
    option is restricted, it may not be set to an instance of `klipper` or
    `klipper_mcu`.

##### Gcode Macro Restrictions

To control "gcode_macro" klipper devices, macros must be configured to accept a
VALUE parameter, and they must report and update a `value` variable.  The value
should be 1 when the device is on, and 0 when the device is off.  For example,
a macro could be configured like the following in `printer.cfg`:

```ini
# printer.cfg

# Assume we have a neopixel we want to control
[neopixel extruder_flare]
pin: PA13

[gcode_macro SET_FLARE]
# The variable below should be initialized to the startup value.  If your
# device is configured to be on at startup use "variable_value: 1"
variable_value: 0
gcode:
  {% if 'VALUE' not in params %}
    {action_raise_error("Parameter 'VALUE' missing from 'SET_FLARE'")}
  {% endif %}
  {% set state = params.VALUE|int %}
  {% if state %}
    # turn the neopixel on
    SET_LED LED=extruder_flare RED=0.75 BLUE=0.2 GREEN=0.2 SYNC=0
  {% else %}
    # turn the neopixel off
    SET_LED LED=extruder_flare RED=0 BLUE=0 GREEN=0 SYNC=0
  {% endif %}
  # Update the state of our variable.  This will inform Moonraker that
  # the device has changed its state.
  SET_GCODE_VARIABLE MACRO=SET_FLARE VARIABLE=value value={state}
```

This can be controlled via Moonraker with the following in `moonraker.conf`:

```ini
# moonraker.conf

[power flare]
type: klipper_device
object_name: gcode_macro SET_FLARE
# The option below locks out requests to toggle the flare
# when Klipper is printing, however it cannot prevent a
# direct call to the SET_FLARE gcode macro.
locked_while_printing: True
```

Output Pin Example:

```ini
# moonraker.conf

# Control a relay providing power to the printer
[power my_pin]
type: klipper_device
object_name: output_pin my_pin
```

!!! Tip
    If you need to use pwm you can wrap the call to `SET_PIN` in a
    gcode_macro and configure Moonraker to toggle the Macro rather than
    the pin directly.

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
#    This option is available for gpio, klipper_device, tplink_smartplug,
#    shelly, and tasmota devices.  The timer may be a floating point value
#    for gpio types, it should be an integer for all other types.  The
#    default is no timer is set.
on_code:
off_code:
#   Valid binary codes that are sent via the RF transmitter.
#   For example: 1011.
```

#### TPLink Smartplug Configuration

!!! Warning
    TPLink has removed access to the local API for some of its Kasa devices
    in recent firmware releases.  As such, it is possible that Moonraker
    will be unable to communicate with your device.  While TPLink claims that
    they will provide a new local API, they have have not done so as of
    December 22nd, 2021.
    See [this TPLink forum post](https://community.tp-link.com/en/smart-home/forum/topic/239364)
    and [this Home Assistant Alert](https://alerts.home-assistant.io/#tplink.markdown)
    for details.

The following options are available for `tplink_smartplug` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname for the tplink device.  For example:
#     192.168.1.127
#   This parameter must be provided.
port:
#   The port to connect to.  Default is 9999.
#
output_id:
#   For power strips, the socket index to use. Default is 0 which indicates the
#   device is not a power strip.
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
# moonraker.conf
#
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
    This implementation communicates with Tasmota firmware through its
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
# moonraker.conf

address:
#   A valid ip address or hostname for the shelly device.  This parameter
#   must be provided.
user:
#   A user name to use for request authentication.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details.  If no password
#   is set the the default is no user, otherwise the default is "admin".
password:
#   The password to use for request authentication.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. The default is no
#   password.
output_id:
#   The output_id (or relay id) to use if the Shelly device supports
#   more than one output.  Default is 1.
```

!!! Note
    This implementation communicates with Shelly firmware through its
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
#   The user name for request authentication.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details.  This
#   default is "admin".
password:
#   The password for request authentication.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. The
#   default is no password.
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
#   The entity ID of the switch to control. This parameter must be provided.
token:
#   A token used for request authorization.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. This parameter
#   must be provided.
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
protocol: http
address: 192.168.1.126
port: 8123
device: switch.1234567890abcdefghij
token: home-assistant-very-long-token
domain: switch
```

#### Loxone Device Configuration

The following options are available for `loxone` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname for the Loxone server.  This
#   parameter must be provided.
user:
#   The user name used for request authorization.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. The default is
#   "admin".
password:
#   The password used for request authorization.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. The default
#   is "admin".
output_id:
#   The name of a programmed output, virtual input or virtual
#   output in the loxone configuration.  The default is no output id.
#
```

#### MQTT Device Configuration

The following options are available for `mqtt` device types:

```ini
# moonraker.conf

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
#    {% endif %}
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
#  response to a command request to the state topic.  The default is False.
```
!!! Note
    Moonraker's MQTT client must be properly configured to add a MQTT device.
    See the [mqtt](#mqtt) section for details.

!!! Tip
    MQTT is the most robust way of managing networked devices through
    Moonraker.  A well implemented MQTT device will publish all
    changes in state to the `state_topic`.  Moonraker receives these changes,
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
# will use the query topic to initialize the device.  Tasmota will publish
# all changes in state to the state topic.
retain_command_state: False
# To query a tasmota device we send the command topic without a payload.
# Optionally we could send a "?" as the payload.
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
####  SmartThings (HTTP)

!!! Important
    SmartThings Developer API Topics:

    * See [Getting a Bearer Token](https://developer-preview.smartthings.com/docs/advanced/authorization-and-permissions/)
    * See [Getting a list of devices](https://developer-preview.smartthings.com/api/public#operation/getDevices)

The following options are available for `smartthings` device types:

```ini
# moonraker.conf

address: api.smartthings.com
protocol: https
port: 443
token:
#   A token used for request authorization.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. This parameter
#   must be provided.
device:
#   The Device guid of the switch to control. This parameter must be provided.
```

Example:
```ini
# moonraker.conf

[power smartthings_switch]
type: smartthings
address: api.smartthings.com
protocol: https
port: 443
token: smartthings-bearer-token
device: smartthings-device-id
```

#### Hue Device Configuration

The following options are available for `hue` device types:

```ini
# moonraker.conf

address:
#   A valid ip address or hostname of the Philips Hue Bridge. This
#   parameter must be provided.
user:
#   The api key used for request authorization.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details.
#   An explanation how to get the api key can be found here:
#   https://developers.meethue.com/develop/get-started-2/#so-lets-get-started
device_id:
#   The device id of the light/socket you want to control.
#   An explanation on how you could get the device id, can be found here:
#   https://developers.meethue.com/develop/get-started-2/#turning-a-light-on-and-off
device_type: light
#   Set to light to control a single hue light, or group to control a hue light group.
#   If device_type is set to light, the device_id should be the light id,
#   and if the device_type is group, the device_id should be the group id.
#   The default is "light".

```

#### Generic HTTP Devices

Support for configurable HTTP switches.  This device type may be used when
no specific implementation is available for a switch.

```ini
on_url:
off_url:
status_url:
#   The urls used to control a device and report its status.  These options
#   accept Jinja2 templates with access to "secrets", see the [secrets]
#   documentation for details.  It is required that any special characters
#   be escaped per RFC 3986 section 2.  These options must be provided.
request_template:
#   An optional Jinja2 template used to customize the http request.  This
#   template can set the request method, additional headers, and the body.
#   When this option is not specified all commands will use a "GET" method
#   with no body and no additional headers.
response_template:
#   A Jinja2 template used to process the http response for each command.  This
#   template should always render to "on" or "off" based on the response.  See
#   the following section for details on the fields provided to the Jinja2
#   context.  This parameter must be provided.

```

###### The template context

The `request_template` and `response_template` options are each provided
a Jinja2 context with the following fields:

- `command`: The command associated with this call.  Will be one of "on"
  "off", or "status".
- `async_sleep`:  An alias for the `asyncio.sleep` method.  This may be used
  to add delays if necessary.
- `log_debug`: An alias for `logging.debug`.  This can be used to log messages
  and data to `moonraker.log` to aid in debugging an implmentation.  Note that
  verbose logging must be
  [enabled](installation.md#debug-options-for-developers) for these messages
  to appear in the log.
- `http_request`: A request object used to build and send http requests.
  This object exposes several methods detailed in the following section.
  When a `request_template` is configured it will share the same http
  request object with the `response_template`.
- `urls`: A `Dict` object containing the configured urls for each command.
  Specifically this object contains "on", "off", and "status" fields, where
  each field points to the url specified in the configuration.

###### The HTTP Request object

The HTTP Request Object is a wrapper around Moonraker's internal HTTP Client
that facilitates building HTTP requests. By default the request object will be
initialized as a "GET" request with the URL configured for the specified command
(ie: if the command is `on` then the request is initialized with the `on_url`).
The request provides the following methods that may be called from a Jinja2
script:

__`http_request.set_method(method)`__

> Sets the request method (ie: `GET`, `POST`, `PUT`).


__`http_request.set_url(url)`__

> Sets the request URL.  Reserved characters in the url must be encoded
per [RFC3986](https://www.rfc-editor.org/rfc/rfc3986#section-2).

__`http_request.set_body(body)`__


> Sets the request body.  This may be a `string`, `List`, or `Dict` object.
`List` and `Dict` objects will be encoded to json and the `Content-Type`
header will be set to `application/json`.

__`http_request.add_header(name, value)`__

> Adds a request header.

__`http_request.set_headers(headers)`__

> Sets the request headers to supplied `Dict` object.  This will overwrite any
headers previously added or set.

__`http_request.reset()`__

> Resets the request object to the default values.  The request method will be
set to `GET`, the body will be empty, and the headers will be cleared.  The
url will be reset to the configured URL for the current command.

__`http_request.last_response()`__

> Returns the most recent [HTTP response](#the-http-response-object).  If no
request has been sent this will return `None`.

__`http_request.send(**kwargs)`__

> Sends the request and returns an [HTTP response](#the-http-response-object).


###### The HTTP Response object

A response object provides access to http response data.  The methods and
properties available will look familiar for those who have experience with
the Python `requests` module.

__`http_response.json()`__

> Decodes the body and returns a resulting `Dict`.

__`http_response.has_error()`__

> Returns if the response is an error.  This is typically true if
the response returns a status code outside of the 200-299 range.

__`http_response.raise_for_status(message=None)`__

> Raises an exception if the response is an error.  The optional "message"
may be specified to replace the error message received from the response.

__`http_response.text`__

> A property that returns the body as a UTF-8 encoded string.

__`http_response.content`__

> A property that returns the body as a python `bytes` object.

__`http_response.url`__

> A property that returns the url of the request associated with this response.

__`http_response.final_url`__

> A property that returns "effective" url of the request after all redirects.

__`http_reponse.headers`__

> A property that returns the response headers as a python `Dict`.

__`http_response.status_code`__

> A property that returns the HTTP status code received with the response.

###### Examples

The following examples re-implement some of the `[power]` modules existing
types using generic http.  The first example shows how a [tasmota](#tasmota-configuration)
switch may be implemented.  Tasmota depends on `GET` http requests for all actions,
making it the most simple type of generic implementation:

```ini
# moonraker.conf

[power generic_tasmota]
type: http
on_url:
  # Build the query string so we can encode it.  This example assumes a password is
  # supplied in a "secrets" file.  If no password is required the "password" field can
  # be omitted or set to an empty string
  {% set qs = {"user": "admin", "password": secrets.tasmota.password, "cmnd": "Power1 on"} %}
  http://tasmota-switch.lan/cm?{qs|urlencode}
off_url:
  {% set qs = {"user": "admin", "password": secrets.tasmota.password, "cmnd": "Power1 off"} %}
  http://tasmota-switch.lan/cm?{qs|urlencode}
status_url:
  {% set qs = {"user": "admin", "password": secrets.tasmota.password, "cmnd": "Power1"} %}
  http://tasmota-switch.lan/cm?{qs|urlencode}
response_template:
  # The module will perform the "GET" request using the appropriate url.
  # We use the `last_response` method to fetch the result and decode the
  # json response.  Tasmota devices return a similar response for all
  # commands, so the response does not require special processing.
  {% set resp = http_request.last_response().json() %}
  # The expression below will render "on" or "off".
  {resp["POWER1"].lower()}
```

The next example implements a [Home Assistant](#home-assistant-configuration-http)
device.  Home Assistant requires `POST` requests for the on and off commands,
and a `GET` request for the status command.  The Home Assistant API uses Token
based authentication, requiring that the request add an `Authorization` header.
Finally, the on and off HTTP requests do not consistently return device state,
making necessary to send a status request after an on or off request.

```ini
# moonraker.conf

[power generic_homeassistant]
type: http
on_url: http://homeassistant.lan:8123/api/services/switch/turn_on
off_url: http://homeassistant.lan:8123/api/services/switch/turn_off
status_url: http://homeassistant.lan:8123/api/states/switch.test_switch
request_template:
  # Home Assistant uses token authorization, add the correct authorization header
  {% do http_request.add_header("Authorization", "Bearer %s" % secrets.homeassistant.token) %}
  {% if command in ["on", "off"] %}
    # On and Off commands are POST requests.  Additionally they require that we add
    # a json body.  The content type header will be automatically set for us in this
    # instance.
    {% do http_request.set_method("POST") %}
    {% do http_request.set_body({"entity_id": "switch.test_switch"}) %}
  {% endif %}
  {% do http_request.send() %}
response_template:
  # Home Assistant does not return device state in the response to on and off
  # commands making it necessary to request device status.
  {% if command in ["on", "off"] %}
    # Some delay is necessary to ensure that Home Assistant has finished processing
    # the command.  This example sleeps for 1 second, more or less may be required
    # depending on the type of switch, speed of the Home Assistant host, etc.
    {% do async_sleep(1.0) %}
    # Set the request method, clear the body, set the url
    {% do http_request.set_method("GET") %}
    {% do http_request.set_body(None) %}
    {% do http_request.set_url(urls.status) %}
    # Note: The Authorization header was set in the "request_template".  Since the
    # http request object is shared between both templates it is not necessary to
    # add it again unless we perform a "reset()" on the request.
    {% set response = http_request.send() %}
    # Raise an exception if we don't get a successful response.  This is handled
    # for us after executing the response template, however sending a request here
    # requires that
    {% do response.raise_for_status() %}
  {% endif %}
  {% set resp = http_request.last_response().json() %}
  {resp["state"]}
```

#### Toggling device state from Klipper

It is possible to toggle device power from the Klippy host, this can be done
with a gcode_macro, such as:
```ini
# printer.cfg

[gcode_macro POWER_OFF_PRINTER]
gcode:
  {action_call_remote_method(
    "set_device_power", device="printer", state="off"
  )}
```

The `device` parameter must be the name of a configured power device.
The `state` parameter must be `on`, `off`, or `toggle`.  In the example above
a device configured as `[power printer]` will be powered off.


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

##### Power on a device when a print starts

Some users have their logic wired to a separate power supply from heaters,
fans, etc.  This keeps Klipper in the "ready" state when power is removed
from such devices.  It is possible to configure Klipper to power up such
devices just before a print is started by overriding the `SDCARD_PRINT_FILE`
gcode command.

The following example presumes that the user a `[power heaters]`
device configured in `moonraker.conf`:

```ini
# printer.cfg

# Create a Macro to Power on the Heaters.  This is necessary to be
# sure that the template evaluates the call in the correct order.
[gcode_macro POWER_ON_HEATERS]
gcode:
  {action_call_remote_method(
    "set_device_power", device="heaters", state="on"
  )}

# Override SDCARD_PRINT_FILE
[gcode_macro SDCARD_PRINT_FILE]
rename_existing: SDCPF
gcode:
   # Step 1: Call the remote method to turn on the power device
   POWER_ON_HEATERS
   # Step 2: Pause while the device powers up.  The following example
   # pauses for 4 seconds.  It may be necessary to tweak this value.
   G4 P4000
   # Step 3: Call the renamed command to start the print
   SDCPF {rawparams}

```

!!! Warning
    The `SDCARD_PRINT_FILE` G-Code command will be executed when a Moonraker
    forwards a request to start a print.  Do not put this command in a G-Code
    file or in a macro that is run from a G-Code file.  This will result in an
    `SD Busy` error and abort the print.


##### Force a power device to change state during a print

Another exotic use case is the addition of a "conditional" peripheral,
such as an MMU device.  The user may not wish to power on this device
for every print, and instead power it on from within the "Start G-GCode"
conditionally.  Additionaly we do not want this device to be turned on/off
unintentionally during a print.  The `set_device_power` remote method takes
an optional `force` argument that can be used to accommodate this scenario.

The following example presumes that the user has a `[power mmu]` device
configured in `moonraker.conf` with the `locked_when_printing` option
set to `True`.  The slicer would be configured to set `USE_MMU=1` for
the print start macro when the MMU is in use.

```ini
# printer.cfg

[gcode_macro POWER_ON_MMU]
gcode:
  {action_call_remote_method(
    "set_device_power", device="mmu", state="on", force=True
  )}

[gcode_macro PRINT_START]
gcode:
  {% set use_mmu = params.USE_MMU|default(0)|int %}
  {% if use_mmu $}
    # Turn on power supply for extruders/bed
    POWER_ON_MMU
    # Add a bit of delay to give the switch time
    G4 P2000
  {% endif %}
  # Add the rest of your "Start G-Code"...
```


#### Power on G-Code Uploads

To power on a device after an upload, `queue_gcode_uploads: True` must
be set in the `[file_manager]`, `load_on_startup: True` must be set in
`[job_queue]` and `one_when_job_queued: True` must be set in `[power dev_name]`,
where "dev_name" the the name of your power device.  For example:

```ini
# moonraker.conf

# Configure the file manager to queue uploaded files when the "start" flag
# is set and Klipper cannot immediately start the print.
[file_manager]
queue_gcode_uploads: True


# Configure the Job Queue to start a queued print when Klipper reports as
# ready.
[job_queue]
load_on_startup: True
# Configure the job_transition_delay and job_transition_gcode options
# if desired.  Note that they do no apply to prints loaded on startup.

# Configure the "power" device to turn on when jobs are queued.
[power printer]
on_when_job_queued: True
# configure the type and any type specific options.  This example
# uses a gpio
#type: gpio
#pin: gpio26
#initial_state: off
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
#  ***DEPRECATED***
#   Debug features are now enabled by the '-g' command line option
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
enable_packagekit: True
#   This option is available when system updates are enabled via the
#   "enable_system_updates" option.  When set to True, system package
#   updates will be processed via PackageKit over D-Bus.  When set to False
#   the "apt cli" fallback will be used.  The default is True.
channel: dev
#   The update channel applied to Klipper and Moonraker.  May dev or
#   beta.  The dev channel will update to the latest commit pushed
#   to the repo, whereas the beta channel will update to the latest
#   commit tagged by Moonraker.  The beta channel will see less frequent
#   updates and should be more stable.  Users on the beta channel will have
#   more opportunity to review breaking changes before choosing to update.
#   The default is dev.
```

#### Extension Configuration
The update manager may be configured manage additional software, henceforth
referred to as "extensions".  In general terms, an extension may be defined
as a piece of software hosted on GitHub.  The update manager breaks this
down into 3 basic types:

- `web`: A front-end such as Mainsail or Fluidd.  Updates are deployed via
  zip files created for GitHub releases.
- `git_repo`:  Used to manage extensions that do not fall into the "web" type.
  Updates are deployed directly via git.  Typical usage scenarios are to
  manage extensions installed a service such as KlipperScreen, repos containing
  configuration, and unofficial 3rd party extensions for Klipper and Moonraker.
  See the note below in reference to unofficial extensions.
- `zip`:  This can be used to managed various extensions like the `git_repo`
  type, however its updates are deployed via zipped GitHub releases.

!!! Note
    To benefit the community Moonraker facilitates updates for 3rd party
    "Klippy Extras" and "Moonraker Components".  While many of these
    extensions are well developed and tested, users should always be
    careful when using such extensions.  Moonraker and Klipper provide
    no official support for such extensions, thus users experiencing an
    issue should not create bug reports on the Klipper or Moonraker issue
    trackers without first reproducing the issue with all unofficial
    extensions disabled.

####  Web type (front-end) configuration

!!! Note
    Front-end developers that wish to deploy updates via Moonraker
    should host releases on their GitHub repo.  In the root of each
    release a `release_info.json` file should be present.  This
    file must contain a JSON object with the following fields:

    - `project_name`:  The name of the GitHub project
    - `project_owner`: The User or Organization that owns the project
    - `version`: The current release version

    For example, a `release_info.json` for Mainsail might contain the
    following:
    ```json
    {
      "project_name": "mainsail",
      "project_owner": "mainsail-crew",
      "version": "v2.5.1"
    }
    ```

```ini
# moonraker.conf

[update_manager extension_name]
type: web
#   The management type.  This should always be "web" for browser based
#   front-ends. This parameter must be provided.
channel: stable
#   May be stable or beta.  When beta is specified "pre-release"
#   updates are available.
repo:
#   This is the GitHub repo of the front-end, in the format of owner/repo_name.
#   For example, this could be set to fluidd-core/fluidd to update Fluidd or
#   mainsail-crew/mainsail to update Mainsail.  This parameter must be provided.
path:
#   The path to the front-end's files on disk.  This folder must contain a
#   a previously installed client.   The folder must not be located within a
#   git repo and it must not be located within a path that Moonraker has
#   reserved, ie: it cannot share a path with another extension. This parameter
#   must be provided.
persistent_files:
#   A list of newline separated file names that should persist between
#   updates.  This is useful for static configuration files, or perhaps
#   themes.  The default is no persistent files.
refresh_interval:
#   This overrides the refresh_interval set in the primary [update_manager]
#   section.
info_tags:
#   Optional information tags about this extensions that are reported via
#   Moonraker's API as a list of strings. Each tag should be separated by
#   a new line. For example:
#       info_tags:
#           desc=My Client App
#           action=webcam_restart
#   Front-ends may use these tags to perform additional actions or display
#   information, see your extension documentation for details on configuration.
#   The default is an empty list.
```

#### Git Repo Configuration

!!! Note
    Git repos must have at least one tag for Moonraker to identify its
    version.  The tag may be lightweight or annotated.  The tag must be in
    semantic version format, `vX.Y.Z`, where X, Y, and Z are all unsigned
    integer values.  For example, a repos first tag might be `v0.0.1`.

    Moonraker can update repos without tags, however front ends may disable
    update controls when version information is not reported by Moonraker.

```ini
# moonraker.conf

# When defining a service, the "extension_name" must be the name of the
# systemd service
[update_manager extension_name]
type: git_repo
#   Currently must be git_repo.  This value is set depending on how an
#   extension chooses to deploy updates, see its documentation for details.
#   This parameter must be provided.
channel: dev
#   The update channel.  The available value differs depending on the
#   "type" option.
#      type: git_repo - May be dev or beta.  The dev channel will update to
#                       the latest pushed commit, whereas the beta channel
#                       will update to the latest tagged commit.
#   The default is dev.
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
virtualenv:
#   An optional path to the virtualenv folder for Python Applications. For
#   example, Moonraker's default virtualenv is located at ~/moonraker-env.
#   When a virtualenv is specified Moonraker can update its Python
#   dependencies when it detects a change to the requirements file.  The
#   default is no virtualenv.
env:
#   *** DEPRICATED FOR NEW CONFIGURATIONS - USE the 'virtualenv' OPTION ***
#
#   The path to the extension's virtual environment executable on disk.  For
#   example, Moonraker's venv is located at ~/moonraker-env/bin/python.
#   The default is no env, which disables updating python packages.
requirements:
#  This is the location in the repository to the extension's python
#  requirements file. This location is relative to the root of the repository.
#  This parameter must be provided if the 'virtualenv' or 'env' option is set,
#  otherwise it must be omitted.
system_dependencies:
#  A path, relative to the repository, to a json file containing operating
#  system package dependencies.  Application developers should refer to the
#  "System Dependencies File Format" section of this document for details on how
#  this file should be formatted. The default is no system dependencies.
install_script:
#  *** DEPRICATED FOR NEW CONFIGURATIONS - USE the 'system_dependencies' OPTION ***
#
#  The file location, relative to the repository, for the installation script
#  associated with this application.  Moonraker will not run this script, instead
#  it will parse the script searching for new "system" package dependencies that
#  require installation.  Packages in the script must be defined as follows for
#  Moonraker to successfully parse them:
#      PKGLIST="packagename1 packagename2 packagename3"
#      PKGLIST="${PKGLIST} packagename4 packagename5"
#
#  Note that the "packagenameX" items in the example above should be the names
#  of valid system packages.  The second line in the example is optional and
#  additional lines in the same format may be added.
#
#  The default is no install script.
enable_node_updates:
#   When set to True, Moonraker will assume that this repo relies upon node
#   and will attempt to execute "npm ci --only=prod" when it detects a change
#   to package-lock.json.  Note that if your project does not have a
#   package-lock.json in its root directory then the plugin will fail to load.
#   Default is False.
is_system_service: True
#   This should be set to False for repos that are not installed as a service
#   or do not need to restart a service after updates. This option sets the
#   default value for the "managed_services" option.  When set to False,
#   "managed_services" defaults to an empty list.  When set to True,
#   "managed_services" defaults to a list containing a single item, a service
#   matching the "extension_name" provided in the section header. The default
#   is True.
#   NOTE: In the future this option will be deprecated.  In preparation
#   for this, extensions that are installed as service, such as "KlipperScreen"
#   should ignore this option and set the "managed_services" option.
managed_services:
#   A list of one or more systemd services that must be restarted after an
#   update is complete.  Multiple services must be separated by whitespace.
#   Currently this option is restricted to the following values:
#       <name>    - The name configured in the extension's section header.
#                   If the section header is [update_manager KlipperScreen]
#                   then KlipperScreen would be a valid value.
#       klipper   - The Klipper service associated with this instance of
#                   Moonraker will be restarted after an update.
#       moonraker - The Moonraker service will be restarted after an update.
#
#   NOTE: Moonraker will resolve the service names for the "klipper" and
#   "moonraker" services if they are not the default values.  Specific names
#   such as "klipper-1" or "moonraker_2" should not be entered in this option.
#
#   When this option is specified it overrides the "is_system_service" option.
#   Thus it is not required to specify both, only one or the other.  The
#   default is no managed services if "is_system_service" is set to False,
#   otherwise the default is the service named in the section header.
refresh_interval:
#   This overrides the refresh_interval set in the primary [update_manager]
#   section.
info_tags:
#   Optional information tags about this application that will be reported
#   front-ends as a list of strings. Each tag should be separated by a new line.
#   For example:
#       info_tags:
#           desc=Special Application
#   Front-ends my use these tags to perform additional actions or display
#   information, see your extension documentation for details on configuration.
#   The default is an empty list.
```

!!! Note
    If this application requires a restart after an update it may be necessary
    to grant Moonraker permission to manage its service. See the
    [allowed services](#allowed-services) section for details on which
    services Moonraker is allowed to manage and how to add additional services.

#### The System Dependencies File Format

When an application depends on OS packages it is possible to specify them
in a file that Moonraker can refer to.  During an update Moonraker will
use this file to install new dependencies if they are detected.

Below is an example of Moonraker's system dependcies file, located at
in the repository at
[scripts/system-dependencies.json](https://github.com/Arksine/moonraker/blob/master/scripts/system-dependencies.json):

```json
{
    "debian": [
        "python3-virtualenv",
        "python3-dev",
        "python3-libgpiod",
        "liblmdb-dev",
        "libopenjp2-7",
        "libsodium-dev",
        "zlib1g-dev",
        "libjpeg-dev",
        "packagekit",
        "wireless-tools",
        "curl"
    ]
}
```

The general format is an object, where each key is the name of a linux
distribution, and the value is an array of strings each naming a dependency.
Moonraker uses Python's [distro](https://distro.readthedocs.io/en/latest/)
package to match the detected operating system against keys in the system
dependencies file.  It will first attempt to match against the return value
of `distro.id()`, the fall back on the values reported by `distro.like()`.
Following this logic, the `debian` key will be applied to Debian, Raspberry
Pi OS, Ubuntu, and likely other Debian derived distributions.

### `[mqtt]`

Enables an MQTT Client.  When configured most of Moonraker's APIs are available
by publishing JSON-RPC requests to `{instance_name}/moonraker/api/request`.
Responses will be published to `{instance_name}/moonraker/api/response`. See
the [API Documentation](web_api.md#json-rpc-api-overview) for details on
on JSON-RPC.

It is also possible for other components within Moonraker to use MQTT to
publish and subscribe to topics.

```ini
# moonraker.conf

[mqtt]
address:
#   Address of the Broker.  This may be a hostname or IP Address.  This
#   parameter must be provided.
port:
#   Port the Broker is listening on.  Default is 1883.
username:
#   An optional username used to log in to the Broker.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details. The default is
#   no username (an anonymous login will be attempted).
password:
#   An optional password used to log in to the Broker.  This option accepts
#   Jinja2 Templates, see the [secrets] section for details.  The default is
#   no password.
password_file:
#   *** DEPRECATED - Use the "password" option ***
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
#   An identifier used to create unique API topics for each instance of
#   Moonraker on network.  This name cannot contain wildcards (+ or #).
#   For example, if the instance name is set to my_printer, Moonraker
#   will subscribe to the following topic for API requests:
#     my_printer/moonraker/api/request
#   Responses will be published to the following topic:
#     my_printer/moonraker/api/response
#   The default is the machine's hostname.
status_objects:
#   A newline separated list of Klipper objects whose state will be
#   published.  There are two different ways to publish the states - you
#   can use either or both depending on your need.  See the
#   "publish_split_status" options for details.
#
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
publish_split_status: False
#   Configures how to publish status updates to MQTT.
#
#   When set to False (default), all Klipper object state updates will be
#   published to a single mqtt state with the following topic:
#     {instance_name}/klipper/status
#
#   When set to True, all Klipper object state updates will be published to
#   separate mqtt topics derived from the object and item in the following
#   format:
#     {instance_name}/klipper/state/{objectname}/{statename}
#
#   The actual value of the state is published as "value" to the topic above.
#   For example, if the heater_bed temperature was 24.0, this is the payload:
#     {"eventtime": {timestamp}, "value": 24.0}
#   It would be published to this topic:
#     {instance_name}/klipper/state/heater_bed/temperature
default_qos: 0
#   The default QOS level used when publishing or subscribing to topics.
#   Must be an integer value from 0 to 2.  The default is 0.
api_qos:
#   The QOS level to use for the API topics. If not provided, the
#   value specified by "default_qos" will be used.
```

#### Publishing topics from Klipper

It is possible to publish a topic from a Klipper gcode macro with the
`publish_mqtt_topic` remote method.  For example:

```ini
# printer.cfg

[gcode_macro PUBLISH_ALERT]
gcode:
  {% set data = params.PAYLOAD|default(None) %}
  {action_call_remote_method("publish_mqtt_topic",
                             topic="klipper/alert",
                             payload=data,
                             qos=0,
                             retain=False,
                             use_prefix=True)}

```

The `topic` is required, all other parameters are optional.  Below is a brief
explanation of each parameter:

- `topic`: a valid mqtt topic
- `payload`: Defaults to an empty payload.  This can be set to string, integer,
  float, boolean, any json object (dict or list) or None. The default
  value is None, in which no payload will be sent with the topic
- `qos`: an integer value in the range from 0 to 2.  The default is the qos
  set in the configuration.
- `retain`: When set to True the retain flag will be set with the published topic.
  Defaults to False.
- `use_prefix`: When set to True the configured `instance_name` will be prefixed
  to the topic.  For example, if the instance_name is `my_printer` and the topic
  is `klipper/alert` the published topic will be `my_printer/klipper/alert`.  The
  default is False.

### `[wled]`
Enables control of a [WLED](https://kno.wled.ge/) strip. Moonraker always
supports 4 color channel strips - the color order is defined within WLED
itself.

```ini
# moonraker.conf

[wled strip_name]
type:
#   The type of device. Can be either http, or serial.
#   This parameter must be provided.
address:
#   The address should be a valid ip or hostname for the wled webserver.
#   Required when type: http
serial:
#   The serial port to be used to communicate directly to wled. Requires wled
#   0.13 Build 2108250 or later.
#   Required when type: serial
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
#   *** DEPRECATED - Color order is defined per GPIO in WLED directly ***
#   Color order for WLED strip, RGB or RGBW (default: RGB)
```
Below are some examples:
```ini
# moonraker.conf

[wled case]
type: http
address: led1.lan
initial_preset: 45
chain_count: 76

[wled lounge]
type: http
address: 192.168.0.45
initial_red: 0.5
initial_green: 0.4
initial_blue: 0.3
chain_count: 42

[wled stealthburner]
type: serial
serial: /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0
initial_white: 0.6
chain_count: 3
```

It is possible to control wled from the klippy host, this can be done using
one or more macros, such as:

```ini
# printer.cfg

[gcode_macro WLED_ON]
description: Turn WLED strip on using optional preset and resets led colors
gcode:
  {% set strip = params.STRIP|string %}
  {% set preset = params.PRESET|default(-1)|int %}

  {action_call_remote_method("set_wled_state",
                             strip=strip,
                             state=True,
                             preset=preset)}

[gcode_macro WLED_CONTROL]
description: Control effect values and brightness
gcode:
  {% set strip = params.STRIP|default('lights')|string %}
  {% set brightness = params.BRIGHTNESS|default(-1)|int %}
  {% set intensity = params.INTENSITY|default(-1)|int %}
  {% set speed = params.SPEED|default(-1)|int %}

  {action_call_remote_method("set_wled_state",
                             strip=strip,
                             brightness=brightness,
                             intensity=intensity,
                             speed=speed)}

[gcode_macro WLED_OFF]
description: Turn WLED strip off
gcode:
  {% set strip = params.STRIP|string %}

  {action_call_remote_method("set_wled_state",
                             strip=strip,
                             state=False)}

[gcode_macro SET_WLED]
description: SET_LED like functionality for WLED, applies to all active segments
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
Enables support for Zeroconf (Apple Bonjour) discovery, allowing external services
detect and use Moonraker instances.

```ini
# moonraker.conf

[zeroconf]
mdns_hostname:
#   The hostname used when registering the multicast DNS serivce.
#   The instance will be available at:
#       http://{mdns_hostname}.local:{port}/
#   The default is the operating system's configured hostname.
enable_ssdp:
#   Enables discovery over UPnP/SSDP in ad.  The default is False
```

### `[button]`
Enables support for handling `button` events.

```ini
# moonraker.conf

[button my_button]
type: gpio
#   Reserved for future use.  Currently the only button type available is
#   'gpio', which is the default.
pin: gpiochip0/gpio26
#   The gpio pin to watch for button events.  The chip is optional, if
#   omitted then the module will default to gpiochip0.  The pin may be
#   inverted by specifying a "!" may be prefix.  Valid examples:
#      gpiochip0/gpio26
#      gpio26
#      !gpiochip0/gpio26
#      !gpio26
#   Systems with libgpiod 1.5 or greater installed also support pullup and
#   pulldown modes.  Prefix a "^" to enable the internal pullup and a "~" to
#   enable the internal pulldown:
#      ^gpiochip0/gpio26
#      ^gpio26
#      ~gpiochip0/gpio26
#      ~gpio26
#      # Its also possible to invert a pin with the pullup/pulldown enabled
#      ^!gpiochip0/gpio26
#      ~!gpiochip0/gpio26
#   This parameter must be provided
minimum_event_time: .05
#   The minimum time (in seconds) between events to trigger a response.  This is
#   is used to debounce buttons.  This value must be at least .01 seconds.
#   The default is .05 seconds (50 milliseconds).
on_press:
on_release:
#   Jinja2 templates to be executed when a button event is detected.  At least one
#   must be provided.

```

#### Button Templates

Both the `on_press` and `on_release` templates are provided a context with the
with two methods that may be called in addition to Jinja2's default filters
adn methods:

- `call_method`:  Calls an internal API method.  See the
  [API documentation](web_api.md#jinja2-template-api-calls) for  details.
- `send_notification`:  Emits a websocket notification.  This is useful if you
   wish to use buttons to notify attached clients of some action.  This
   method takes an optional argument that may contain any JSON object.
   If provided, this value will be sent as part of the payload with
   the notification.

Additionally, the following context variables are available:

- `event`:  This is a dictionary with details about the event:
    - `elapsed_time`:  The time elapsed (in seconds) since the last detected
      button event
    - `received_time`: The time the event was detected according to asyncio's
      monotonic clock.  Note that this is not in "unix time".
    - `render_time`: The time the template was rendered (began execution)
      according to asyncio's monotonic clock.  It is possible execution of
      an event may be delayed well beyond the `received_time`.
    - `pressed`: A boolean value to indicate if the button is currently pressed.
- `user_data`:  This is a dictionary in which templates can store information
  that will persist across events.  This may be useful to track the number of
  events, specific timing of events, return values from previous API calls,
  etc.  Note that the data in this field does not persist across Moonraker
  restarts.

!!! Warning
    It is recommended to avoid API calls that may block (ie: the `update` APIs).
    Only one event may be rendered at a time, subsequent events received will be
    delayed. Calling a blocking API would effectively make the button
    non-responsive until the API call returns.

Button Template Examples:

```ini
# moonraker.conf

# Emergency Stop Example
[button estop]
type: gpio
pin: gpio26
on_press:
  # Executes immediately after a press is detected
  {% do call_method("printer.emergency_stop") %}

# Reboot Long Press Example
[button reboot]
type: gpio
pin: gpio26
on_release:
  # Only call reboot if the button was held for more than 1 second.
  # Note that this won't execute until the button has been released.
  {% if event.elapsed_time > 1.0 %}
    {% do call_method("machine.reboot") %}
  {% endif %}

# Double Click Notification Example
[button notify_btn]
type: gpio
pin: gpio26
on_press:
  # Use the "user_data" context variable to track a single click
  {% set clicked = user_data.clicked|default(false) %}
  # It isn't possible to assign a value to a context variable in Jinja2,
  # however since user_data is a dict we can call its methods.  The
  # call to __setitem__ below is equivalent to:
  #   user_data["clicked"] = true
  {% do user_data.__setitem__("clicked", true) %}
  {% if event.elapsed_time < 0.5 and clicked %}
    # We will consider this a double click if the second click occurs
    # within .5 seconds of releasing the first
    {% do user_data.__setitem__("clicked", false) %}
    {% do user_data.__setitem__("double_clicked", true) %}
  {% endif %}
on_release:
  {% set double_clicked = user_data.double_clicked|default(false) %}
  {% if double_clicked %}
    {% do user_data.__setitem__("double_clicked", false) %}
    {% do send_notification("Double Clicked!") %}
  {% endif %}
```

### `[secrets]`

Retrieves credentials and other information from a "secrets" file
separate from `moonraker.conf`.  This allows users to safely distribute
their configuration and log files without revealing credentials and
other sensitive information.

!!! Note
    This section no longer has configuration options.  Previously the
    `secrets_path` option was used to specify the location of the file.
    The secrets file name and location is now determined by the `data path`
    and `alias` command line options, ie: `<data_base_path>/moonraker.secrets`.
    For a typical single instance installation this resolves to
    `$HOME/printer_data/moonraker.secrets`. This may be a symbolic link.

Example ini secrets file:

```ini
# /home/pi/printer_data/moonraker.secrets

[mqtt_credentials]
username: mqtt_user
password: my_mqtt_password

[home_assistant]
token: long_token_string

```

Example json secrets file:

```json
{
    "mqtt_credentials": {
        "username": "mqtt_user",
        "password": "my_mqtt_password"
    },
    "home_assistant": {
      "token": "long_token_string"
    }
}
```

!!! Tip
    Generally speaking `ini` files are easier to manually edit.  However,
    options are limited to string values without parsing and converting.
    The strength of `json` is that a field may be an integer, string,
    float, boolean, array, or object.

#### Accessing secret credentials

The `secrets` object is added to Moonraker's Jinja2 environment as a
global, thus it is available in all templates. All options in
Moonraker's configuration that accept credentials support templates.

MQTT configuration example with secret credentials:

```ini
[mqtt]
address: mqtt-broker.local
port: 1883
# The username and password options below may be templates that
# we can use to resolve stored secrets
username: {secrets.mqtt_credentials.username}
password: {secrets.mqtt_credentials.password}
enable_moonraker_api: True
```

!!! warning
    The purpose of the `[secrets]` module is to keep credentials and
    other sensitive information out of configuration files and Moonraker's
    log.  These items are stored in plain text, it is wise to use
    unique credentials. Never leave a Moonraker client application open
    unattended in an untrusted location, as it would be possible for a
    malicious actor to reconfigure moonraker to send items stored in the
    secrets file to themselves via `mqtt`, `notifier`, etc.

Home Assistant Switch Example:

```ini
# moonraker.conf

[power homeassistant_switch]
type: homeassistant
address: home-assistant-host.local
port: 8123
device: switch.1234567890abcdefghij
# The token option may be a template
token: {secrets.home_assistant.token}
domain: switch
```


### `[notifier]`

Enables the notification service. Multiple "notifiers" may be configured,
each with their own section, ie: `[notifier my_discord_server]`,
`[notifier my_phone]`.

All notifiers require an url for a service to be set up. Moonraker depends on
[Apprise](https://github.com/caronc/apprise) to emit notifications.
Available services and their corresponding at urls may be found on the
[Apprise Wiki](https://github.com/caronc/apprise/wiki).

```ini
# moonraker.conf

[notifier telegram]
url: tgram://{bottoken}/{ChatID}
#   The url for your notifier. This URL accepts Jinja2 templates,
#   so you can use [secrets] if you want.  This parameter must be
#   provided.
events: *
#   The events this notifier should trigger to. '*' means all events.
#   You can use multiple events, comma separated.
#   Valid events:
#      started
#      complete
#      error
#      cancelled
#      paused
#      resumed
#   This parameter must be provided.
body: "Your printer status has changed to {event_name}"
#   The body of the notification. This option accepts Jinja2 templates, where
#   the template is passed a context containing the following fields:
#      event_name: The name of the event that triggered the notification
#                  (ie: started, complete, error, etc)
#      event_args: A list containing the arguments passed to the event.
#                  See the "Tip" below for additional details on this field.
#      event_message: An additional message passed to the notification when
#                     triggered.  This is commonly used when the notification
#                     is received from Klippy using a gcode_macro.
#   The default is a body containining the "name" of the notification as entered
#   in the section header.
body_format:
#   The formatting to use for the body, can be `text`, `html` and `markdown`.
#   The default is `text`.
title:
#   The optional title of the notification. This option accepts Jinja2 templates,
#   the template will receive a context with the same fields as the body.  The
#   default is an empty string as the title.
attach:
#   One or more items to attach to the notification. This may be a path to a
#   local file or a url (such as a webcam snapshot).  Multiple attachments must be
#   separated by a newline.  This option accepts Jinja2 templates, the tempalte
#   will recieve the same context as the "body" and "title" options.  The default
#   is no attachment will be sent with the notification.
#
#   Note: Attachments are not available for all notification services, you can
#   check if it's supported on the Apprise Wiki.  Be aware that links to items
#   hosted on your local network can only be viewed within that network.
```

!!! Tip
    The `event_args` field of the Jinja2 context passed to templates in
    this section receives a list of "arguments" passed to the event.  For
    those familiar with Python this list is known as "variable arguments".
    Currently the notifier only supports two kinds of events: those
    triggered by a change in the job state and those triggered from a remote
    method call frm a `gcode_macro`.

    For `remote method` events the `event_args` field will always be
    an empty list.  For `job state` events the `event_args` field will
    contain two items. The first item (`event_args[0]`) contains the
    job state recorded prior to the event, the second item (`event_args[1]`)
    contains the current job state.  In most cases users will be interested
    in the current job state (`event_args[1]`).

    The `job state` is a dict that contains the values reported by
    Klipper's [print_stats](printer_objects.md#print_stats) object.

#### An example:
```ini
# moonraker.conf

[notifier print_start]
url: tgram://{bottoken}/{ChatID}
events: started
body: Your printer started printing '{event_args[1].filename}'

[notifier print_complete]
url: tgram://{bottoken}/{ChatID}
events: complete
body: Your printer completed printing '{event_args[1].filename}'
attach: http://192.168.1.100/webcam/?action=snapshot

[notifier print_error]
url: tgram://{bottoken}/{ChatID}
events: error
body: {event_args[1].message}
attach: http://192.168.1.100/webcam/?action=snapshot

[notifier my_telegram_notifier]
url: tgram://{bottoken}/{ChatID}
events: gcode
body: {event_message}
attach: http://192.168.1.100/webcam/?action=snapshot
```

#### Notifying from Klipper
It is possible to invoke your notifiers from the Klippy host, this can be done
with a gcode_macro, such as:
```ini
# printer.cfg

[gcode_macro NOTIFY_FILAMENT_CHANGE]
gcode:
  {action_call_remote_method("notify",
                             name="my_telegram_notifier",
                             message="Filament change needed!")}
```

### `[simplyprint]`

Enables support for print monitoring through
[SimplyPrint](https://simplyprint.io),
publicly launched Moonraker integration Nov 21st 2022.

```ini
# moonraker.conf
[simplyprint]
webcam_name:
#   Optional name of a configured webcam for use by the SimplyPrint service.
#   This can either be a webcam configured through the `[webcam]` module or
#   a webcam added via a front-end like Mainsail.  The default is to attempt
#   to autodetect a webcam.
power_device:
#   The name of a configured [power] device available to toggle over using
#   the SimplyPrint service.  For example, to toggle a device specified
#   as [power printer] may be configured as:
#       power_device: printer
#   By default no power device is configured.
filament_sensor:
#   The name of a configured filament sensor to be monitored by SimplyPrint.
#   The filament sensor must be configured in Klipper and the full name,
#   including the prefix, must be specified.  For example, to monitor a sensor
#   specified as [filament_switch_sensor fsensor] may be configured as:
#       filament_sensor:  filament_switch_sensor fsensor
#   By default no filament sensor is monitored.
ambient_sensor:
#   The name of a configured temperature sensor used to report the ambient
#   temperature.  The sensor must be configured in Klipper and the full name,
#   including the prefix, must be specified.  For example, an ambient sensor
#   specified in Klipper as [temperature_sensor chamber] may be configured as:
#       ambient_sensor: temperature_sensor chamber
#   If no ambient_sensor is configured then SimplyPrint will use the extruder
#   to estimate ambient temperature when the heater is idle and cool.
```

!!! Note
    This module collects and uploads the following data to SimplyPrint:

    - Klipper's version, connection state, and date pulled
    - Moonraker's version
    - Currenly connected front-end and version
    - Current python version
    - Linux distribution and version
    - Network connection type (wifi or ethernet)
    - wifi SSID (if connected)
    - LAN IP address
    - LAN hostname
    - CPU model
    - CPU core count
    - Total system memory
    - CPU usage
    - Memory usage
    - Current extruder selected
    - Extruder and bed temperatures
    - Mesh data (if Klipper has `bed_mesh` configured)
    - Current print state
    - Loaded file metadata, including estimated filament usage and print time
    - Current print filament usage
    - Current print time elapse
    - Estimated ambient temperature
    - Webcam configuration (if available)
    - Webcam images.
    - Power device state (if configured)
    - Filament sensor state (if configured)

More on how your data is used in the SimplyPrint privacy policy here;
[https://simplyprint.io/legal/privacy](https://simplyprint.io/legal/privacy)

### `[sensor]`

Enables data collection from additional sensor sources.  Multiple "sensor"
sources may be configured, each with their own section, ie: `[sensor current]`,
`[sensor voltage]`.

#### Options common to all sensor devices

The following configuration options are available for all sensor types:

```ini
# moonraker.conf

[sensor my_sensor]
type:
#   The type of device.  Supported types: mqtt
#   This parameter must be provided.
name:
#   The friendly display name of the sensor.
#   The default is the sensor source name.
```

#### MQTT Sensor Configuration

The following options are available for `mqtt` sensor types:

```ini
# moonraker.conf

qos:
#  The MQTT QOS level to use when publishing and subscribing to topics.
#  The default is to use the setting supplied in the [mqtt] section.
state_topic:
#  The mqtt topic to subscribe to for sensor state updates.  This parameter
#  must be provided.
state_response_template:
#  A template used to parse the payload received with the state topic.  A
#  "payload" variable is provided the template's context. This template must
#  call the provided set_result() method to pass sensor values to Moonraker.
#  `set_result()` expects two parameters, the name of the measurement (as
#  string) and the value of the measurement (either integer or float number).
#
#  This allows for sensor that can return multiple readings (e.g. temperature/
#  humidity sensors or powermeters).
#  For example:
#    {% set notification = payload|fromjson %}
#    {set_result("temperature", notification["temperature"]|float)}
#    {set_result("humidity", notification["humidity"]|float)}
#    {set_result("pressure", notification["pressure"]|float)}
#
#  The above example assumes a json response with multiple fields in a struct
#  is received. Individual measurements are extracted from that struct, coerced
#  to a numeric format and passed to Moonraker. The default is the payload.
```

!!! Note
    Moonraker's MQTT client must be properly configured to add a MQTT sensor.
    See the [mqtt](#mqtt) section for details.

!!! Tip
    MQTT is the most robust way of collecting sensor data from networked
    devices through Moonraker.  A well implemented MQTT sensor will publish all
    changes in state to the `state_topic`.  Moonraker receives these changes,
    updates its internal state, and notifies connected clients.

Example:

```ini
# moonraker.conf

# Example configuration for a Shelly Pro 1PM (Gen2) switch with
# integrated power meter running the Shelly firmware over MQTT.
[sensor mqtt_powermeter]
type: mqtt
name: Powermeter
# Use a different display name
state_topic: shellypro1pm-8cb113caba09/status/switch:0
# The response is a JSON object with a multiple fields that we convert to
# float values before passing them to Moonraker.
state_response_template:
  {% set notification = payload|fromjson %}
  {set_result("power", notification["apower"]|float)}
  {set_result("voltage", notification["voltage"]|float)}
  {set_result("current", notification["current"]|float)}
  {set_result("energy", notification["aenergy"]["by_minute"][0]|float * 0.000001)}
```

### `[spoolman]`

Enables integration with the [Spoolman](https://github.com/Donkie/Spoolman)
filament manager. Moonraker will automatically send filament usage updates to
the Spoolman database.

Front ends can also utilize this config to provide a built-in management tool.

```ini
# moonraker.conf

[spoolman]
server: http://192.168.0.123:7912
#   URL to the Spoolman instance. This parameter must be provided.
sync_rate: 5
#   The interval, in seconds, between sync requests with the
#   Spoolman server.  The default is 5.
```

#### Setting the active spool from Klipper

The `spoolman` module registers the `spoolman_set_active_spool` remote method
with Klipper.  This method may be used to set the active spool ID, or clear it,
using gcode macros.  For example, the following could be added to Klipper's
`printer.cfg`:

```ini
# printer.cfg

[gcode_macro SET_ACTIVE_SPOOL]
gcode:
  {% if params.ID %}
    {% set id = params.ID|int %}
    {action_call_remote_method(
       "spoolman_set_active_spool",
       spool_id=id
    )}
  {% else %}
    {action_respond_info("Parameter 'ID' is required")}
  {% endif %}

[gcode_macro CLEAR_ACTIVE_SPOOL]
gcode:
  {action_call_remote_method(
    "spoolman_set_active_spool",
    spool_id=None
  )}
```

With the above configuration it is possible to run the `SET_ACTIVE_SPOOL ID=1`
command to set the currently tracked spool ID to `1`, and the `CLEAR_ACTIVE_SPOOL`
to clear spool tracking (useful when unloading filament for example).

## Include directives

It is possible to include configuration from other files via include
directives.  Include directives in Moonraker are specified identically
to those in Klipper, ie: `[include relative_path]`.  The `relative_path`
is a path relative to the configuration file's parent folder, and may
include wildcards.  For example:

```ini
# moonraker.conf

[include my_extra_config.conf]

[include subfolder/*.conf]

```

If a section is duplicated in an included file the options from both
sections will be merged, with the latest section parsed taking precedence.
The order in which a section is parsed depends on the location of the
include directive.  When wildcards are specified all matches are parsed in
alphabetical order.


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
# Lets assume this device requires a json payload with each command.
# We will use a dict to generate the payload
command_payload:
  {% set my_payload = {"SOME_FIELD": ""} %}
  # example of calling the dict.update method
  {% do my_payload.update({"SOME_FIELD": "a string value"}) %}
  # Here we set the actual command, the "command" variable
  # is passed to the context of this template
  {% do my_payload.__setitem__("POWER_COMMAND", command) %}
  # generate the json output
  { my_payload|tojson }
```
## Option Moved Deprecations

On November 7th 2021 a change to Moonraker was made regarding configuration of
`core components`.  Moonraker defines a `core component` as a component that
is required for Moonraker to function and is always loaded.  Prior to November
7th all core components were configured in the `[server]` section.  As
Moonraker's functionality expanded, this became untenable as the `[server]`
section began to bloat.  Thus a change was made to move the configuration
for `core components` out of `[server]` into their own sections.  This was
not a breaking change, Moonraker would fall back to the `[server]` section
for core component configuration if no section was present.

On April 6th 2022 the fallback was deprecated.  Moonraker will still function
normally if `core components` are configured in the `[server]` section,
however Moonraker now generates warnings when it detects this condition,
such as:

```
[server]: Option 'temperature_store_size' has been moved to section [data_store]. Please correct your configuration, see https://moonraker.readthedocs.io/en/latest/configuration for detailed documentation.
[server]: Option 'gcode_store_size' has been moved to section [data_store]. Please correct your configuration, see https://moonraker.readthedocs.io/en/latest/configuration for detailed documentation
```

To correct these warnings, the user must modify `moonraker.conf`.  For example,
your current configuration may look like the following:

```ini
# moonraker.conf

[server]
host: 0.0.0.0
port: 7125
temperature_store_size: 600
gcode_store_size: 1000

```

You will need to change it to the following;

```ini
# moonraker.conf

[server]
host: 0.0.0.0
port: 7125

[data_store]
temperature_store_size: 600
gcode_store_size: 1000
```

The common front-ends provide a UI for modifying `moonraker.conf`, otherwise
it will be necessary to ssh into the host and use a tool such as `nano` to
make the changes.

!!! Warning
    Make sure `moonraker.conf` does not have duplicate sections, and double
    check to make sure that the formatting is correct.

Once the changes are complete you may use the UI to restart Moonraker and
the warnings should clear.
