## Installation

This document provides a guide on how to install Moonraker on a Raspberry
Pi running Raspian/Rasperry Pi OS.  Other SBCs and/or linux distributions
may work, however they may need a custom install script.

Klipper should be installed prior to installing Moonraker.  Please see
[Klipper's Documention](https://github.com/KevinOConnor/klipper/blob/master/docs/Installation.md)
for instructions on how to do this.

Moonraker is still in alpha development, and thus some of its dependencies
in Klipper have yet to be merged.  Until this has been done it will be
necessary to add a remote and work off a developmental branch of Klipper
to correctly run Moonraker.

```
  cd ~/klipper
  git remote add arksine https://github.com/Arksine/klipper.git
```

Now fetch and checkout:
```
git fetch arksine
git checkout arksine/dev-moonraker-testing
```
Note that you are now in a detached head state and you cannot pull. Any
time you want to update to the latest version of this branch you must
repeat the two commands above.


For reference, if you want to switch back to the clone of the official repo:
```
git checkout master
```
Note that the above command is NOT part of the Moonraker install procedure.

You can now install the Moonraker application:
```
cd ~
git clone https://github.com/Arksine/moonraker.git
```

If you have an older version of moonraker installed, it must be removed:
```
cd ~/moonraker/scripts
./uninstall-moonraker.sh
```

Finally, run moonraker's install script:
```
cd ~/moonraker/scripts
./install-moonraker.sh
```

When the script completes it should start both Moonraker and Klipper. In
`klippy.log` you should find the following entry:\
`Moonraker: server connection detected`

Currently Moonraker is responsible for creating the Unix Domain Socket,
so so it must be started first for Klippy to connect.  In any instance
where Klipper was started first simply restart the klipper service.
```
sudo service klipper restart
```
After the connection is established Klippy will register API endpoints and
send configuration to the server.  Once the initial configuration is sent
to Moonraker its configuration will be retained when Klippy disconnects
(either through a restart or by stopping the service), and updated when
Klippy reconnects.

# Configuration
The host, port, log file location, socket file location and api key file
are all specified via command arguments:
```
usage: moonraker.py [-h] [-a <address>] [-p <port>] [-s <socketfile>]
                    [-l <logfile>] [-k <apikeyfile>]

Moonraker - Klipper API Server

optional arguments:
  -h, --help            show this help message and exit
  -a <address>, --address <address>
                        host name or ip to bind to the Web Server
  -p <port>, --port <port>
                        port the Web Server will listen on
  -s <socketfile>, --socketfile <socketfile>
                        file name and location for the Unix Domain Socket
  -l <logfile>, --logfile <logfile>
                        log file name and location
  -k <apikeyfile>, --apikey <apikeyfile>
                        API Key file location
```

The default configuration is:
- address = 0.0.0.0 (Bind to all interfaces)
- port = 7125
- socketfile = /tmp/moonraker
- logfile = /tmp/moonraker.log
- apikeyfile = ~/.moonraker_api_key

It is recommended to use the defaults, however one may change these
arguments by editing `/etc/default/moonraker`.

All other configuration is sent to the server via Klippy, thus it is done in
printer.cfg.  A basic configuration that authorizes clients on a range from
192.168.1.1 - 192.168.1.254 is as follows:
```
[moonraker]
trusted_clients:
 192.168.1.0/24
```

Below is a detailed explanation of all options currently available:
```
#[moonraker]
#require_auth: True
#  Enables Authorization.  When set to true, only trusted clients and
#  requests with an API key are accepted.
#enable_cors: False
#  Enables CORS support.  If serving static files from a different http
#  server then CORS  will need to be enabled.
#trusted_clients:
#  A list of new line separated ip addresses, or ip ranges, that are trusted.
#  Trusted clients are given full access to the API.  Note that ranges must
#  be expressed in 24-bit CIDR notation, where the last segment is zero:
#  192.168.1.0/24
#  The above example will allow 192.168.1.1 - 192.168.1-254.  Note attempting
#  to use a non-zero value for the last IP segement or different bit value will
#  result in a configuration error.
#request_timeout: 5.
#  The amount of time (in seconds) a client request has to process before the
#  server returns an error.  This timeout does NOT apply to gcode requests.
#  Default is 5 seconds.
#long_running_gcodes:
# BED_MESH_CALIBRATE, 120.
# M104, 200.
#   A list of gcodes that will be assigned their own timeout.  The list should
#   be in the format presented above, where the first item is the gcode name
#   and the second item is the timeout (in seconds).  Each pair should be
#   separated by a newline.  The default is an empty list where no gcodes have
#   a unique timeout.
#long_running_requests:
# gcode/script, 60.
# pause_resume/pause, 60.
# pause_resume/resume, 60.
# pause_resume/cancel, 60.
#    A list of requests that will be assigned their own timeout.  The list
#    should be formatted in the same manner as long_running_gcodes.  The
#    default is matches the example shown above.
#status_tier_1:
# toolhead
# gcode
#status_tier_2:
# fan
#status_tier_3:
# extruder
# virtual_sdcard
#  Subscription Configuration.  By default items in tier 1 are polled every
#  250 ms, tier 2 every 500 ms, tier 3 every 1s, tier 4 every 2s, tier
#  5 every 4s, tier 6 every 8s.
#tick_time: .25
#  This is the base interval used for status tier 1.  All other status tiers
#  are calculated using the value defined by tick_time (See below for more
#  information).  Default is 250ms.
```

The "status tiers" are used to determine how fast each klippy object is allowed
to be polled.  Each tier is calculated using the `tick_time` option.  There are
6 tiers, `tier_1 = tick_time` (.25s), `tier_2 = tick_time*2` (.5s),
`tier_3 = tick_time*4` (1s), `tier_4 = tick_time*8` (2s),
`tier_5 = tick_time*16` (4s), and `tier_6 = tick_time*16` (8s).  This method
was chosen to provide some flexibility for slower hosts while making it easy to
batch subscription updates together.

## Plugin Configuration
The core plugins are configured via the primary configuration above.  Optional
plugins each need their own configuration as outlined below.

### PanelDue Plugin

```
[moonraker_plugin paneldue]
serial: /dev/ttyAMA0
baud: 57600
machine_name: Voron 2
macros:
  LOAD_FILAMENT
  UNLOAD_FILAMENT
  PREHEAT_CHAMBER
  TURN_OFF_MOTORS
  TURN_OFF_HEATERS
  PANELDUE_BEEP FREQUENCY=500 DURATION=1
```

Most options above are self explanatory.  The "macros" option can be used
to specify commands (either built in or gcode_macros) that will show up
in the PanelDue's "macro" menu.

Note that buzzing the piezo requires the following gcode_macro:
```
[gcode_macro PANELDUE_BEEP]
# Beep frequency
default_parameter_FREQUENCY: 300
# Beep duration in seconds
default_parameter_DURATION: 1.
gcode:
  { printer.moonraker.action_call_remote_method(
		"paneldue_beep", frequency=FREQUENCY|int,
		duration=DURATION|float) }
```

### Power Control Plugin
```
[moonraker_plugin power]
devices: printer, led           
#  A comma separated list of devices you wish to control. Do not use spaces in
#  the device's name here
#{dev}_name: Friendly Name
#  This is the friendly name for the device. {dev} must be swapped for the name
#  of the device used under devices, as an example:
#  printer_name: My Printer
{dev}_pin: 23
#  This option is required.
#  The GPIO Pin number you wish to control
#{dev}_active_low: False
#  If you have a device that needs a low or 0 signal to be turned on, set this
#  option to True.
```

Define the devices you wish to control under _devices_ with a comma separated
list. For device specific configrations, swap {dev} for the name of the device
that you listed under devices.

Each device can have a Friendly Name, pin, and activehigh set. Pin is the only
required option. For devices that should be active when the signal is 0 or low,
set {dev}_activehigh to False, otherwise don't put the option in the
configuration.
