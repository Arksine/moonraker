##
This file will track changes that require user intervention,
such as a configuration change or a reinstallation.

### March 10th 2021
- The `cors_domain` option in the `[authoriztion]` section is now
  checked for dangerous entries.  If a domain entry contains a
  wildcard in the top level domain (ie: `http://www.*`) then it
  will be rejected, as malicious website can easily reproduce
  this match.

### March 6th 2021
- The `enable_debug_logging` in the `[server]` section now defaults
  to `False`.  This dramatically reduces the amount of logging produced
  by Moonraker for the typical user.

### March 4th 2021
- To enable Octoprint compatibility with slicer uploads it is now
  required to add `[octoprint_compat]` to `moonraker.conf`.  After
  making this change it is necessary to restart the Moonraker service
  so the module is loaded.

### December 31st 2020
- The file manager no longer restricts the `config_path` to a folder
  within the HOME directory.  The path may not be the system root,
  however it can reside anywhere else on the file system as long as
  Moonraker has read and write access to the directory.  This applies
  to gcode path received from Klipper via the `virtual_sdcard` section
  as well.

### December 6th 2020
- Moonraker is now installed as a systemd service.  This allows logging
  to stdout which can be viewed with the `journalctl -u moonraker` command.
  This changes requires the user to rerun the install script.  If
  `moonraker.conf` is not located in the home directory, the command
  will looks something like the following:

        cd ~/moonraker
        ./scripts/install-moonraker.sh -f -c /home/pi/klipper_config/moonraker.conf

  Otherwise you can run the install script with no arguments.

### November 19th 2020
- The install script (`install-moonraker.sh`) now has command-line
  options:\
  `-r`   Rebuild the python virtual env\
  `-f`   Force an overwrite of `/etc/default/moonraker` during installation\
  `-c /path/to/moonraker.conf`    Allows user to specify the path to
  moonraker.conf during configuration.  Using this in conjunction with `-f`
  will update the defaults file wih the new path.
- New dependencies have been added to Moonraker which require reinstallation.
  Run the following command to reinstall and rebuild the virtualenv:

        ~/moonraker/scripts/install-moonraker.sh -r

- The power plugin configuration has changed.  See the
  [install guide](installation.md#power-control-plugin) for
  details on the new configuration.
- Users transitioning from the previous version of the power plugin will need
  to unexport any curently used pins.  For example, the following command
  may be used to unexport pin 19:

        echo 19 > /sys/class/gpio/unexport

  Alternatively one may reboot the machine after upgrading:

    cd ~/moonraker/
    git pull
    ~/moonraker/scripts/install-moonraker.sh -r
    sudo reboot

  Make sure that the power plugin configuration has been updated prior
  to rebooting the machine.
