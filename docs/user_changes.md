This file will track changes that require user intervention,
such as a configuration change or a reinstallation.

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
  ```
  ~/moonraker/scripts/install-moonraker.sh -r
  ```
- The power plugin configuration has changed.  See the
  [install guide](installation.md#power-control-plugin) for
  details on the new configuration.
- Users transitioning from the previous version of the power plugin will need
  to unexport any curently used pins.  For example, the following command
  may be used to unexport pin 19:
  ```
  echo 19 > /sys/class/gpio/unexport
  ```
  Alternatively one may reboot the machine after upgrading:
  ```
  cd ~/moonraker/
  git pull
  ~/moonraker/scripts/install-moonraker.sh -r
  sudo reboot
  ```
  Make sure that the power plugin configuration has been updated prior
  to rebooting the machine.
