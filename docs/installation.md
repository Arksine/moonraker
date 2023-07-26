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
path: ~/printer_data/gcodes
```

### Enabling Klipper's Unix Domain Socket Server

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

You may also want to take this opportunity to configure `printer.cfg` and
`klippy.log` so they are located in Moonraker's `data_path`, for example:

```
# Configuration for /etc/init.d/klipper

KLIPPY_USER=pi

KLIPPY_EXEC=/home/pi/klippy-env/bin/python

KLIPPY_ARGS="/home/pi/klipper/klippy/klippy.py /home/pi/printer_data/config/printer.cfg -l /home/pi/printer_data/logs/klippy.log -a /tmp/klippy_uds"
```

Moonraker's install script will create the data folder, however you
may wish to create it now and move `printer.cfg` to the correct
location, ie:
```
mkdir ~/printer_data
mkdir ~/printer_data/logs
mkdir ~/printer_data/config
mv printer.cfg ~/printer_data/config
```

### Installing Moonraker

Begin by cloning the git respository:

```
cd ~
git clone https://github.com/Arksine/moonraker.git
```

The install script will attempt to create a basic configuration if
`moonraker.conf` does not exist at the expected location, however if you
prefer to have Moonraker start with a robust configuration you may create
it now.  By default the configuration file should be located at
`$HOME/printer_data/config/moonraker.conf`, however the location of the
data path may be configured using the script's command line options.
The [sample moonraker.conf](./moonraker.conf) may be used as a starting
point, full details can be found in the
[confguration documentation](./configuration.md).

For a default installation run the following commands:
```
cd ~/moonraker/scripts
./install-moonraker.sh
```

The install script has a few command line options that may be useful,
particularly for those upgrading:

- `-f`:
  Force an overwrite of Moonraker's systemd script. By default the
  the systemd script will not be modified if it exists.
- `-a <alias>`:
  The installer uses this option to determine the name of the service
  to install.  If `-d` is not provided then this options will also be
  used to determine the name of the data path folder. If omitted this
  defaults to `moonraker`.
- `-d <path to data folder>`:
  Specifies the path to Moonraker's data folder.  This folder organizes
  files and directories used by moonraker.  See the `Data Folder Structure`
  section for details.  If omitted this defaults to `$HOME/printer_data`.
- `-c <path to configuration file>`
  Specifies the path to Moonraker's configuation file.  By default the
  configuration is expected at `<data_folder>/config/moonraker.conf`. ie:
  `/home/pi/printer_data/config/moonraker.conf`.
- `-l <path to log file>`
   Specifies the path to Moonraker's log file.  By default Moonraker logs
   to `<data_folder>/logs/moonraker.log`. ie:
  `/home/pi/printer_data/logs/moonraker.log`.
- `-z`:
  Disables `systemctl` commands during install (ie: daemon-reload, restart).
  This is useful for installations that occur outside of a standard environment
  where systemd is not running.
- `-x`:
  Skips installation of [polkit rules](#policykit-permissions).  This may be
  necessary to install Moonraker on systems that do not have policykit
  installed.

Additionally, installation may be customized with the following environment
variables:

- `MOONRAKER_VENV`
- `MOONRAKER_REBUILD_ENV`
- `MOONRAKER_FORCE_DEFAULTS`
- `MOONRAKER_DISABLE_SYSTEMCTL`
- `MOONRAKER_SKIP_POLKIT`
- `MOONRAKER_CONFIG_PATH`
- `MOONAKER_LOG_PATH`
- `MOONRAKER_DATA_PATH`

When the script completes it should start both Moonraker and Klipper. In
`klippy.log` you should find the following entry:

`webhooks client <uid>: Client info {'program': 'Moonraker', 'version': '<version>'}`

Now you may install a client, such as
[Mainsail](https://github.com/mainsail-crew/mainsail) or
[Fluidd](https://github.com/cadriel/fluidd).

!!! Note
    Moonraker's install script no longer includes the nginx dependency.
    If you want to install one of the above clients on the local machine,
    you may want to first install nginx (`sudo apt install nginx` on
    debian/ubuntu distros).


### Data Folder Structure

As mentioned previously, files and folders used by Moonraker are organized
in a primary data folder.  The example below illustrates the folder
structure using the default data path of `$HOME/printer_data`.

```
/home/pi/printer_data
├── backup
│   └── 20220822T202419Z
│       ├── config
│       │   └── moonraker.conf
│       └── service
│           └── moonraker.service
├── certs
│   ├── moonraker.cert (optional)
│   └── moonraker.key (optional)
├── config
│   ├── moonraker.conf
│   └── printer.cfg
├── database
│   ├── data.mdb
│   └── lock.mdb
├── gcodes
│   ├── test_gcode_one.gcode
│   └── test_gcode_two.gcode
├── logs
│   ├── klippy.log
│   └── moonraker.log
├── systemd
│   └── moonraker.env
├── moonraker.secrets (optional)
└── moonraker.asvc
```

If it is not desirible for the files and folders to exist in these specific
locations it is acceptable to use symbolic links.  For example, it is common
for the gcode folder to be located at `$HOME/gcode_files`.  Rather than
reconfigure Klipper's `virtual_sdcard` it may be desirable to create a
`gcodes` symbolic link in the data path pointing to this location.

!!! Note
    It is still possible to directly configure the paths to the configuration
    and log files if you do not wish to use the default file names of
    `moonraker.conf` and `moonraker.log`

When Moonraker attempts to update legacy installations symbolic links
are used to avoid an unrecoverable error.  Additionally a `backup`
folder is created which contains the prior configuration and/or
systemd service unit, ie:

```
/home/pi/printer_data
├── backup
│   └── 20220822T202419Z
│       ├── config
│       │   ├── include
│       │   │   ├── extras.conf
│       │   │   ├── power.conf
│       │   │   └── updates.conf
│       │   └── moonraker.conf
│       └── service
│           └── moonraker.service
├── certs
│   ├── moonraker.cert -> /home/pi/certs/certificate.pem
│   └── moonraker.key -> /home/pi/certs/key.pem
├── config -> /home/pi/klipper_config
├── database -> /home/pi/.moonraker_database
├── gcodes -> /home/pi/gcode_files
├── logs -> /home/pi/logs
├── systemd
│   └── moonraker.env
└── moonraker.secrets -> /home/pi/moonraker_secrets.ini
```

!!! Warning
    The gcode and config paths should not contain symbolic links
    that result in an "overlap" of on another.  Moonraker uses
    inotify to watch files in each of these folders and takes action
    when a file change is detected.  The action taken depends on the
    "root" folder, thus it is important that they be distinct.

### The systemd service file

The default installation will create `/etc/systemd/system/moonraker.service`.
Below is a common example of service file, installed on a Raspberry Pi:

```ini
# systemd service file for moonraker
[Unit]
Description=API Server for Klipper SV1
Requires=network-online.target
After=network-online.target

[Install]
WantedBy=multi-user.target

[Service]
Type=simple
User=pi
SupplementaryGroups=moonraker-admin
RemainAfterExit=yes
WorkingDirectory=/home/pi/moonraker
EnvironmentFile=/home/pi/printer_data/systemd/moonraker.env
ExecStart=/home/pi/moonraker-env/bin/python $MOONRAKER_ARGS
Restart=always
RestartSec=10
```

Following are some items to take note of:

- The `Description` contains a string that Moonraker uses to validate
  the version of the service file, (notice `SV1` at the end, ie: Service
  Version 1).
- The `moonraker-admin` supplementary group is used to grant policykit
  permissions.
- The `EnvironmentFile` field contains Moonraker's arguments.  See the
  [environment file section](#the-environment-file) for details.
- The `ExecStart` field begins with the python executable, followed by
  by the enviroment variable `MOONRAKER_ARGS`.  This variable is set in
  the environment file.


### Command line usage

This section is intended for users that need to write their own
installation script.  Detailed are the command line arguments
available to Moonraker:
```
usage: moonraker.py [-h] [-d <data path>] [-c <configfile>] [-l <logfile>] [-u <unixsocket>] [-n] [-v] [-g] [-o]

Moonraker - Klipper API Server

options:
  -h, --help            show this help message and exit
  -d <data path>, --datapath <data path>
                        Location of Moonraker Data File Path
  -c <configfile>, --configfile <configfile>
                        Path to Moonraker's configuration file
  -l <logfile>, --logfile <logfile>
                        Path to Moonraker's log file
  -u <unixsocket>, --unixsocket <unixsocket>
                        Path to Moonraker's unix domain socket
  -n, --nologfile       disable logging to a file
  -v, --verbose         Enable verbose logging
  -g, --debug           Enable Moonraker debug features
  -o, --asyncio-debug   Enable asyncio debug flag
```

The default configuration is:

- `data path`: `$HOME/printer_data`
- `config file`: `$HOME/printer_data/config/moonraker.conf`
- `log file`: `$HOME/printer_data/logs/moonraker.log`
- `unix socket`: `$HOME/printer_data/comms/moonraker.sock`
- logging to a file is enabled
- Verbose logging is disabled
- Moonraker's debug features are disabled
- The asyncio debug flag is set to false

!!! Tip
    While the `data path` option may be omitted it is recommended that it
    always be included for new installations.  This allows Moonraker
    to differentiate between new and legacy installations.

!!! Warning
    Moonraker's `--unixsocket` option should not be confused with Klipper's
    `--api-server` option.  The `unixsocket` option for Moonraker specifies
    the path where Moonraker will create a unix domain socket that serves its
    JSON-RPC API.

If is necessary to run Moonraker without logging to a file the
`-n` option may be used, for example:
```
~/moonraker-env/bin/python ~/moonraker/moonraker/moonraker.py -d ~/printer_data -n
```

!!! Tip
    It is not recommended to install Moonraker with file logging disabled
    While moonraker will still log to stdout, all requests for support
    must be accompanied by `moonraker.log`.

Each command line argument has an associated enviroment variable that may
be used to specify options in place of the command line.

- `MOONRAKER_DATA_PATH="<data path>"`: equivalent to `-d <data path>`
- `MOONRAKER_CONFIG_PATH="<configfile>"`: equivalent to `-c <configfile>`
- `MOONRAKER_LOG_PATH="<logfile>"`: equivalent to `-l <logfile>`
- `MOONRAKER_UDS_PATH="<unixsocket>"`: equivalent to `-u <unixsocket>`
- `MOONRAKER_DISABLE_FILE_LOG="y"`: equivalent to `-n`
- `MOONRAKER_VERBOSE_LOGGING="y"`: equivalent to `-v`
- `MOONRAKER_ENABLE_DEBUG="y"`: equivalent to `-g`.
- `MOONRAKER_ASYNCIO_DEBUG="y"`: equivalent to `-o`

!!! Note
    Command line arguments take priority over environment variables when
    both are specified.

[The environment file](#the-environment-file) may be used to set Moonraker's
command line arguments and/or environment variables.

### The environment file

The environment file, `moonraker.env`. is created in the data path during
installation. A default installation's environment file will contain the path
to `moonraker.py` and the data path option, ie:

```
MOONRAKER_DATA_PATH="/home/pi/printer_data"
MOONRAKER_ARGS="-m moonraker"
PYTHONPATH="/home/pi/moonraker"
```

A legacy installation converted to the updated flexible service unit
might contain the following.  Note that this example uses command line
arguments instead of environment variables, either would be acceptable:

```
MOONRAKER_ARGS="/home/pi/moonraker/moonraker/moonraker.py -d /home/pi/printer_data -c /home/pi/klipper_config/moonraker.conf -l /home/pi/klipper_logs/moonraker.log"
```

Post installation it is simple to customize
[arguments and/or environment variables](#command-line-usage)
supplied to Moonraker by editing this file and restarting the service.
The following example sets a custom config file path, log file path,
enables verbose logging, and enables debug features:

```
MOONRAKER_DATA_PATH="/home/pi/printer_data"
MOONRAKER_CONFIG_PATH="/home/pi/printer_data/config/moonraker-1.conf"
MOONRAKER_LOG_PATH="/home/pi/printer_data/logs/moonraker-1.log"
MOONRAKER_VERBOSE_LOGGING="y"
MOONRAKER_ENABLE_DEBUG="y"
MOONRAKER_ARGS="-m moonraker"
PYTHONPATH="/home/pi/moonraker"
```

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

### Completing Privileged Upgrades

At times an update to Moonraker may require a change to the systemd service
file, which requires sudo permission to complete.  Moonraker will present
an announcement when it need's the user's password and the process can
be completed by entering the password through Moonraker's landing page.

Some users prefer not to provide these credentials via the web browser and
instead would like to do so over ssh.  These users may run
 `scripts/finish-upgrade.sh` to provide Moonraker the necessary credentials
 via ssh:

```
Utility to complete privileged upgrades for Moonraker

usage: finish-upgrade.sh [-h] [-a <address>] [-p <port>] [-k <api_key>]

optional arguments:
  -h                show this message
  -a <address>      address for Moonraker instance
  -p <port>         port for Moonraker instance
  -k <api_key>      API Key for authorization
```

By default the script will connect to a Moonraker instances on the local
machine at port 7125.  If the instance is not bound to localhost or is
bound to another port the user may specify a custom address and port.

The API Key (`-k`) option is only necessary if the localhost is not authorized
to access Moonraker's API.

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

### LMDB Database Backup and Restore

Moonraker uses a [LMDB Database](http://www.lmdb.tech/doc/) for persistent
storage of procedurally generated data.  LMDB database files are platform
dependent, and thus cannot be easily transferred between different machines.
A file generated on a Raspberry Pi cannot be directly transferred to an x86
machine.  Likewise, a file generated on a 32-bit version of Linux cannot
be transferred to a 64-bit machine.

Moonraker includes two scripts, `backup-database.sh` and `restore-database.sh`
to help facilitate database backups and transfers.

```shell
~/moonraker/scripts/backup-database.sh -h
Moonraker Database Backup Utility

usage: backup-database.sh [-h] [-e <python env path>] [-d <database path>] [-o <output file>]

optional arguments:
  -h                  show this message
  -e <env path>       Moonraker Python Environment
  -d <database path>  Moonraker LMDB database to backup
  -o <output file>    backup file to save to
```

```shell
~/moonraker/scripts/restore-database.sh -h
Moonraker Database Restore Utility

usage: restore-database.sh [-h] [-e <python env path>] [-d <database path>] [-i <input file>]

optional arguments:
  -h                  show this message
  -e <env path>       Moonraker Python Environment
  -d <database path>  Moonraker LMDB database path to restore to
  -i <input file>     backup file to restore from
```

Both scripts include default values for the Moonraker Environment and Database
Path.  These are `$HOME/moonraker-env` and `$HOME/printer_data/database`
respectively.  The `backup` script defaults the output value to
`$HOME/database.backup`.  The `restore` script requires that the user specify
the input file using the `-i` option.

To backup a database for a default Moonraker installation the user may ssh into
the machine and run the following command:

```shell
~/moonraker/scripts/backup-database.sh -o ~/moonraker-database.backup
```

And to restore the database:
```shell
sudo service moonraker stop
~/moonraker/scripts/restore-database.sh -i ~/moonraker-database.backup
sudo service moonraker start
```

The backup file contains [cdb like](https://manpages.org/cdb/5) entries
for each key/value pair in the database.  All keys and values are base64
encoded, however the data is not encrypted.  Moonraker's database may
contain credentials and other sensitive information, so users should treat
this file accordingly.  It is not recommended to keep backups in any folder
served by Moonraker.

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

### Debug options for developers

Moonraker accepts several command line arguments that can be used to
assist both front end developers and developers interested in extending
Moonraker.

- The `-v` (`--verbose`) argument enables verbose logging.  This includes
  logging that reports information on all requests received and responses.
- The `-g` (`--debug`) argument enables Moonraker's debug features,
  including:
    - Debug endpoints
    - The `update_manager` will bypass strict git repo validation, allowing
      updates from unofficial remotes and repos in a `detached HEAD` state.
- The `-o` (`--asyncio-debug`) argument enables the asyncio debug flag.  This
  will substantially increase logging and is intended for low level debugging
  of the asyncio event loop.

!!! Warning
    The debug option should not be enabled in production environments.  The
    database debug endpoints grant read/write access to all namespaces,
    including those typically exclusive to Moonraker.  Items such as user
    credentials are exposed.

Installations using systemd can enable debug options by editing `moonraker.env`
via ssh:

```
nano ~/printer_data/systemd/moonraker.env
```

Once the file is open, append the debug option(s) (`-v` and `-g` in this example) to the
value of `MOONRAKER_ARGS`:
```
MOONRAKER_ARGS="/home/pi/moonraker/moonraker/moonraker.py -d /home/pi/printer_data -c /home/pi/klipper_config/moonraker.conf -l /home/pi/klipper_logs/moonraker.log -v -g"
```

Save the file, exit the text editor, and restart the Moonraker service:

```
sudo systemctl restart moonraker
```
