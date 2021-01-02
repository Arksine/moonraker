## Installation

This document provides a guide on how to install Moonraker on a Raspberry
Pi running Raspian/Rasperry Pi OS.  Other SBCs and/or linux distributions
may work, however they may need a custom install script.  Moonraker
requires Python 3.7 or greater, verify that your distribution's
Python 3 packages meet this requirement.

Klipper should be installed prior to installing Moonraker.  Please see
[Klipper's Documention](https://github.com/KevinOConnor/klipper/blob/master/docs/Installation.md)
for instructions on how to do this.  After installation you should make
sure that the [prerequistes](#prerequisites-klipper-configuration) are configured.

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
printer.cfg to match Moonraker's "config_path" option (see the
[configuration document](configuration.md#primary-configuration)
for more information on the config_path). For example, if the `config_path`
option is set to  `~/printer_config`, your klipper defaults file might look
like the following:
```
# Configuration for /etc/init.d/klipper

KLIPPY_USER=pi

KLIPPY_EXEC=/home/pi/klippy-env/bin/python

KLIPPY_ARGS="/home/pi/klipper/klippy/klippy.py /home/pi/printer_config/printer.cfg -l /tmp/klippy.log -a /tmp/klippy_uds"
```

If necessary, create the config directory and move printer.cfg to it:
```
cd ~
mkdir printer_config
mv printer.cfg printer_config
```

You can now install the Moonraker application:
```
cd ~
git clone https://github.com/Arksine/moonraker.git
```

Prior to installation it is a good idea to create
[moonraker.conf](configuration.md).  If you are using the `config_path`,
create it in the specified directory otherwise create it in the HOME
directory.  A sample `moonraker.conf` may be found in the `docs` folder
of this repo.

For a default installation run the following commands:
```
cd ~/moonraker/scripts
./install-moonraker.sh
```

Or to install with `moonraker.conf` in the `config_path`:
```
cd ~/moonraker/scripts
./install-moonraker.sh -f -c /home/pi/printer_config/moonraker.conf
```

The install script has a few command line options that may be useful,
particularly for those upgrading:
- -r\
  This will rebuild the virtual environment for existing installations.
  Sometimes this is necessary when a dependency has been added.
- -f\
  This will tell the script to overwrite Moonraker's "defaults" file.
  By default the script will not modify the "defaults" file if it is
  detected as present.
- -c /path/to/moonraker.conf\
  This allows the user to specify the path to Moonraker's config file.
  The default location is `/home/<user>/moonraker.conf`.

When the script completes it should start both Moonraker and Klipper. In
`klippy.log` you should find the following entry:\
`webhooks client <uid>: Client info {'program': 'Moonraker', 'version': '<version>'}`\

Now you may install a client, such as
[Mainsail](https://github.com/meteyou/mainsail) or
[Fluidd](https://github.com/cadriel/fluidd)

## Command line Usage
The configuration and log file paths may be specified via the command
line.
```
usage: moonraker.py [-h] [-c <configfile>] [-l <logfile>] [-n]

Moonraker - Klipper API Server

optional arguments:
  -h, --help            show this help message and exit
  -c <configfile>, --configfile <configfile>
                        Location of moonraker configuration file
  -l <logfile>, --logfile <logfile>
                        log file name and location
  -n, --nologfile       disable logging to a file
```

The default configuration is:
- config file path- `~/moonraker.conf`
- log file path - `/tmp/moonraker.log`
- logging to a file is enabled

If one needs to start moonraker without generating a log file, the
'-n' option may be used, for example:
```
~/moonraker-env/bin/python ~/moonraker/moonraker/moonraker.py -n -c /path/to/moonraker.conf
```
In general it is not recommended to install moonraker with this option.
While moonraker will still log to stdout, all requests for support must
be accompanied by moonraker.log.

These options may be changed by editing
`/etc/systemd/system/moonraker.service`.  The `install-moonraker.sh` script
may also be used to modify the config file location.

## Prerequisites (Klipper Configuration)

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
NOTES:
- Make sure that Moonraker (and Klipper) has read and write access to the
  directory set in the `path` option for the `virtual_sdcard`.
- Upon first starting Moonraker is not aware of the gcode file path, thus
  it cannot serve gcode files, add directories, etc.  After Klippy enters
  the "ready" state it sends Moonraker the gcode file path.
  Once Moonraker receives the path it will retain it regardless of Klippy's
  state, and update it if the path is changed in printer.cfg.

Please see [configuration.md](configuration.md) for details on how to
configure moonraker.conf.
