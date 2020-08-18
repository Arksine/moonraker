## Installation

This document provides a guide on how to install Moonraker on a Raspberry
Pi running Raspian/Rasperry Pi OS.  Other SBCs and/or linux distributions
may work, however they may need a custom install script.  Moonraker
requires Python 3.7 or greater, verify that your distribution's
Python 3 packages meet this requirement.

Klipper should be installed prior to installing Moonraker.  Please see
[Klipper's Documention](https://github.com/KevinOConnor/klipper/blob/master/docs/Installation.md)
for instructions on how to do this.

After Klipper is installed, you need to modify its "default" file.  This file
contains klipper's command line arguments, and you must add an argument that
instructs Klippy to create a Unix Domain socket:
```
sudo nano /etc/default/klipper
```
You should see a file that looks something like the following:
```
# Configuration for /etc/init.d/klipper

KLIPPY_USER=pi

KLIPPY_EXEC=/home/pi/klippy-env/bin/python

KLIPPY_ARGS="/home/pi/klipper/klippy/klippy.py /home/pi/printer.cfg -l /tmp/klippy.log"
```

You need to add `-a /tmp/klippy_uds` to KLIPPY_ARGS:
```
# Configuration for /etc/init.d/klipper

KLIPPY_USER=pi

KLIPPY_EXEC=/home/pi/klippy-env/bin/python

KLIPPY_ARGS="/home/pi/klipper/klippy/klippy.py /home/pi/printer.cfg -l /tmp/klippy.log -a /tmp/klippy_uds"
```
You may also want to take this opportunity to change the location of
printer.cfg if you enable Moonraker's "config_path" option (see the
[configuration section](#moonraker-configuration-moonrakerconf) for more information).

You can now install the Moonraker application:
```
cd ~
git clone https://github.com/Arksine/moonraker.git
```

If you have an experimental verison of moonraker that pre-dates this repo,
it must be uninstalled:
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
`webhooks: New connection established`

Now you may install a client, such as [Mainsail](
https://github.com/meteyou/mainsail).
- Note that as of the time of this writing (August 11 2020) the current version
  of Mainsail (0.1.2) is not compatible with this repo.  Please give the
  developer some time to bring up Mainsail in line with the latest release
  of Moonraker.

# Configuration

## Command line
The configuration and log file paths may be specified via the command
line.
```
usage: moonraker.py [-h] [-c <configfile>] [-l <logfile>]

Moonraker - Klipper API Server

optional arguments:
  -h, --help            show this help message and exit
  -c <configfile>, --configfile <configfile>
                        Location of moonraker configuration file
  -l <logfile>, --logfile <logfile>
                        log file name and location
```

The default configuration is:
- config file - `~/moonraker.conf`
- log file - `/tmp/moonraker.log`

It is recommended to use the defaults, however one may change these
arguments by editing `/etc/default/moonraker`.

## Klipper configuration (printer.cfg)

Moonraker depends on the following Klippy extras for full functionality:
- [virtual_sdcard]
- [pause_resume]
- [display_status]

If you have a `filament_switch_sensor` configured then `pause_resume` will
automatically be loaded.  Likewise, if you have a `display` configured then
`display_status` will be automatically loaded.  If your configuration is
missing one or both, you can simply add the bare sections to printer.cfg:
```
[pause_resume]

[display_status]

[virtual_sdcard]
path: ~/gcode_files
```
NOTE: While Klipper does not set any hard limits on the location of the
`path` option for the `virtual_sdcard`, Moonraker requires that the path
be located within the HOME directory, it cannot however be the HOME
directory.  If you wish to host your files elsewhere, use a symlink.

## Moonraker configuration (moonraker.conf)

All other configuration is done via `moonraker.conf`.  If you are
familiar with Klipper, the configuration is similar.  A basic
configuration might look like the following:
```
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

Note that while all items in the `[server]` and `[authorization]`
sections have default values, the sections must be present for
moonraker to start. Aside from the `config_path` and `trusted_clients`
options it is recommended to use default values.

Below is a detailed explanation of all options currently available:
```
[server]
host: 0.0.0.0
#  The host address in which to bind the HTTP server.  Default is to bind
#  to all interfaces
port: 7125
#   The port the HTTP server will listen on.  Default is 7125
klippy_address: /tmp/klippy_uds
#   The address of Unix Domain Socket used to communicate with Klippy. Default
#   is /tmp/klippy_uds
enable_debug_logging: True
#   When set to True Moonraker will log in verbose mode.  During this stage
#   of development the default is True.  In the future this will change.
config_path:
#   An optional path where configuration files are located. If specified,
#   Moonraker will serve this path allowing file and directory manipulation
#   within it. This path must be located within the user's HOME directory,
#   by may not be the home directory itself. The default is no path, which
#   results in no configuration files being served.

[authorization]
enabled: True
#   Enables authorization.  When set to true, requests must either contain
#   a valid API key or originate from a trusted client. Default is True.
api_key_path: ~/.moonraker_api_key
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
```

For the moment, you need to restart the moonraker service to load a new
configuration:
```
sudo service moonraker restart
```

### Plugin Configuration
The core plugins are configured via the primary configuration above.  Optional
plugins each need their own configuration as outlined below.

#### PanelDue Plugin
```
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
[gcode_macro PANELDUE_BEEP]
variable_sequence: 0
variable_frequency: 0
variable_duration: 0
# Beep frequency
default_parameter_FREQUENCY: 300
# Beep duration in seconds
default_parameter_DURATION: 1.
gcode:
  SET_GCODE_VARIABLE MACRO=PANELDUE_BEEP VARIABLE=frequency VALUE={FREQUENCY|int}
  SET_GCODE_VARIABLE MACRO=PANELDUE_BEEP VARIABLE=duration VALUE={DURATION|float}
  SET_GCODE_VARIABLE MACRO=PANELDUE_BEEP VARIABLE=sequence VALUE={printer["gcode_macro PANELDUE_BEEP"].sequence|int + 1}
```

#### Power Control Plugin
Power Plugin Configuration.  One may use this module to toggle the
state of a relay using a linux GPIO, enabling the ability to power
a printer on/off regardless of Klippy's state.  GPIOs are toggled
using linux sysfs.
```
[power]
devices: printer, led
#   A comma separated list of devices you wish to control. Device names may not
#   contain whitespace.  This parameter must be provided.
#
# Each device specified in "devices" should define its own set of the below
# options:
{dev}_name: Friendly Name
#   An optional alias for the device. The default is the name specifed in
#   "devices".
{dev}_pin: 23
#   The sysfs GPIO pin number you wish to control.  This parameter must be
#   provided.
{dev}_active_low: False
#   When set to true the pin signal is inverted.  Default is False.
```

Define the devices you wish to control under _devices_ with a comma separated
list. For device specific configrations, swap {dev} for the name of the device
that you listed under devices.

Each device can have a Friendly Name, pin, and activehigh set. Pin is the only
required option. For devices that should be active when the signal is 0 or low,
set {dev}_activehigh to False, otherwise don't put the option in the
configuration.
