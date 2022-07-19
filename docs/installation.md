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
- `-z`:
  Disables `systemctl` commands during install (ie: daemon-reload, restart).
  This is useful for installations that occur outside of a standard environment
  where systemd is not running.

Additionally, installation may be customized with the following environment
variables:

- `MOONRAKER_VENV`
- `MOONRAKER_REBUILD_ENV`
- `MOONRAKER_FORCE_DEFAULTS`
- `MOONRAKER_DISABLE_SYSTEMCTL`
- `MOONRAKER_CONFIG_PATH`
- `MOONRAKER_LOG_PATH`

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

### PolicyKit Permissions

Some of Moonraker's components require elevated privileges to perform actions.
Previously these actions could only be run via commandline programs launched
with the `sudo` prefix.  This has significant downsides:

- The user must be granted `NOPASSWD` sudo access.  Raspberry Pi OS
  grants the Pi user this access by default, however most other distros
  require that this be enabled through editing `visudo` or adding files
  in `/etc/sudoers.d/`.
- Some linux distributions require additional steps such as those taken
  in `sudo_fix.sh`.
- Running CLI programs is relatively expensive.  This isn't an issue for
  programs that are run once at startup, but is undesirable if Moonraker
  wants to poll information about the system.

Moonraker now supports communicating with system services via D-Bus.
Operations that require elevated privileges are authrorized through
PolicyKit. On startup Moonraker will check for the necessary privileges
and warn users if they are not available.  Warnings are presented in
`moonraker.log` and directly to the user through some clients.

To resolve these warnings users have two options:

1) Install the PolicyKit permissions with the `set-policykit-rules.sh` script,
   for example:

```shell
cd ~/moonraker/scripts
./set-policykit-rules.sh
sudo service moonraker restart
```

!!! tip
    If you still get warnings after installing the PolKit rules, run the
    install script with no options to make sure that all new dependencies
    are installed.

    ```shell
    cd ~/moonraker/scripts
    ./install-moonraker.sh
    ```

2) Configure Moonraker to use the legacy backend implementations for
   the `machine` and/or `update_manager` components, ie:

```ini
# Use the systemd CLI provider rather than the DBus Provider
[machine]
provider: systemd_cli

# Edit your existing [update_manager] section to disable
# PackageKit.  This will fallback to the APT CLI Package Update
# implementation.
[update_manager]
#..other update manager options
enable_packagekit: False

# Alternatively system updates can be disabled
[update_manager]
#..other update manager options
enable_system_updates: False
```

!!! Note
    Previously installed PolicyKit rules can be removed by running
    `set-policykit-rules.sh -c`

### Retrieving the API Key

Some clients may require an API Key to connect to Moonraker.  After the
`[authorization]` component is first configured Moonraker will automatically
generate an API Key.  There are two ways in which the key may be retrieved
by the user:

Retrieve the API Key via the command line (SSH):
```
cd ~/moonraker/scripts
./fetch-apikey.sh
```

Retrieve the API Key via the browser from a trusted client:

- Navigate to `http://{moonraker-host}/access/api_key`, where
  `{moonraker-host}` is the host name or ip address of the desired
  moonraker instance.
- The result will appear in the browser window in JSON format. Copy
  The API Key without the quotes.

        {"result": "8ce6ae5d354a4365812b83140ed62e4b"}

### Recovering a broken repo

Currently Moonraker is deployed using `git`.  Without going into the gritty
details,`git` is effectively a file system, and as such is subject to
file system corruption in the event of a loss of power, bad sdcard, etc.
If this occurs, updates using the `[update_manager]` may fail.  In most
cases Moonraker provides an automated method to recover, however in some
edge cases this is not possible and the user will need to do so manually.
This requires that you `ssh` into your machine.  The example below assumes
the following:

- You are using a Raspberry Pi
- Moonraker and Klipper are installed at the default locations in the `home`
  directory
- Both Moonraker and Klipper have been corrupted and need to be restored

The following commands may be used to restore Moonraker:

```shell
cd ~
rm -rf moonraker
git clone https://github.com/Arksine/moonraker.git
cd moonraker/scripts
./install-moonraker.sh
./set-policykit-rules.sh
sudo systemctl restart moonraker
```

And for Klipper:

```shell
cd ~
rm -rf klipper
git clone https://github.com/Klipper3d/klipper.git
sudo systemctl restart klipper
```

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
