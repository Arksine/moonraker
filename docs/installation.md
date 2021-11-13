## Installation

This document provides a guide on how to install Moonraker on a Raspberry
Pi running Raspian/Rasperry Pi OS.  Other SBCs and/or linux distributions
may work, however they may need a custom install script.  Moonraker
requires Python 3.7 or greater, verify that your distribution's
Python 3 packages meet this requirement.

### Installing Klipper

Klipper should be installed prior to installing Moonraker.  Please see
[Klipper's Documention](https://klipper3d.com/Overview.html) for details.
After installing Klipper you should make sure to add Moonraker's
[configuration requirements](#klipper-configuration-requirements).

### Klipper Configuration Requirements

Moonraker depends on the following Klippy extras for full functionality:

- `[virtual_sdcard]`
- `[pause_resume]`
- `[display_status]`

If you have a `[filament_switch_sensor]` configured then `[pause_resume]` will
automatically be loaded.  Likewise, if you have a `[display]` configured then
`[display_status]` will be automatically loaded.  If your configuration is
missing one or both, you can simply add the bare sections to `printer.cfg`:
```ini
[pause_resume]

[display_status]

[virtual_sdcard]
path: ~/gcode_files
```

### Enabling the Unix Socket

After Klipper is installed it may be necessary to modify its `defaults` file in
order to enable the Unix Domain Socket.  Begin by opening the file in your
editor of choice, for example:
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

Add `-a /tmp/klippy_uds` to KLIPPY_ARGS:
```
# Configuration for /etc/init.d/klipper

KLIPPY_USER=pi

KLIPPY_EXEC=/home/pi/klippy-env/bin/python

KLIPPY_ARGS="/home/pi/klipper/klippy/klippy.py /home/pi/printer.cfg -l /tmp/klippy.log -a /tmp/klippy_uds"
```

!!! note
    Your installation of Klipper may use systemd instead of
    the default LSB script.  In this case, you need to modify the
    klipper.service file.

You may also want to take this opportunity to change the location of
printer.cfg to match Moonraker's `config_path` option (see the
[configuration document](configuration.md#primary-configuration)
for more information on the config_path). For example, if the `config_path`
is set to  `~/printer_config`, your klipper defaults file might look
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

### Installing Moonraker

Begin by cloning the git respository:

```
cd ~
git clone https://github.com/Arksine/moonraker.git
```

Now is a good time to create [moonraker.conf](configuration.md).  If you are
using the `config_path`, create it in the specified directory otherwise create
it in the HOME directory.  The [sample moonraker.conf](./moonraker.conf) in
the `docs` directory may be used as a starting point.

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

- `-r`:
  Rebuilds the virtual environment for existing installations.
  Sometimes this is necessary when a dependency has been added.
- `-f`:
  Force an overwrite of Moonraker's systemd script. By default the
  the systemd script will not be modified if it exists.
- `-c /home/pi/moonraker.conf`:
  Specifies the path to Moonraker's config file. The default location
  is `/home/<user>/moonraker.conf`.  When using this option to modify
  an existing installation it is necessary to add `-f` as well.

When the script completes it should start both Moonraker and Klipper. In
`/tmp/klippy.log` you should find the following entry:

`webhooks client <uid>: Client info {'program': 'Moonraker', 'version': '<version>'}`

Now you may install a client, such as
[Mainsail](https://github.com/mainsail-crew/mainsail) or
[Fluidd](https://github.com/cadriel/fluidd).

!!! Note
    Moonraker's install script no longer includes the nginx dependency.
    If you want to install one of the above clients on the local machine,
    you may want to first install nginx (`sudo apt install nginx` on
    debian/ubuntu distros).


### Command line usage

This section is intended for users that need to write their own
installation script.  Detailed are the command line arguments
available to Moonraker:
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
`-n` option may be used, for example:
```
~/moonraker-env/bin/python ~/moonraker/moonraker/moonraker.py -n -c /path/to/moonraker.conf
```
In general it is not recommended to install moonraker with this option.
While moonraker will still log to stdout, all requests for support must
be accompanied by moonraker.log.

These options may be changed by editing
`/etc/systemd/system/moonraker.service`.  The `install-moonraker.sh` script
may also be used to modify the config file location.

### Retreiving the API Key

Some clients may require an API Key to connect to Moonraker.  After the
`[authorization]` component is first configured Moonraker will automatically
generate an API Key.  There are two ways in which the key may be retreived
by the user:

Retreive the API Key via the command line (SSH):
```
cd ~/moonraker/scripts
./fetch-apikey.sh
```

Retreive the API Key via the browser from a trusted client:

- Navigate to `http://{moonraker-host}/access/api_key`, where
  `{moonraker-host}` is the host name or ip address of the desired
  moonraker instance.
- The result will appear in the browser window in JSON format. Copy
  The API Key without the quotes.

        {"result": "8ce6ae5d354a4365812b83140ed62e4b"}

### Additional Notes

- Make sure that Moonraker and Klipper both have read and write access to the
  directory set in the `path` option for the `[virtual_sdcard]` in
  `printer.cfg`.
- Upon first starting Moonraker is not aware of the gcode file path, thus
  it cannot serve gcode files, add directories, etc.  After Klippy enters
  the "ready" state it sends Moonraker the gcode file path.
  Once Moonraker receives the path it will retain it regardless of Klippy's
  state, and update it if the path is changed in printer.cfg.

Please see [configuration.md](configuration.md) for details on how to
configure moonraker.conf.
