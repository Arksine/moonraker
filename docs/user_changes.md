##
This file tracks configuration changes and deprecations.  Additionally
changest to Moonraker that require user intervention will be tracked
here.

### July 18th 2023
- The following changes have been made to `[update_manager <name>]`
  extensions of the `git_repo` type:
  - The `env` option has been deprecated.  New configurations should
    use the `virtualenv` option in its place.
  - The `install_script` option has been deprecated. New configurations
    should use the `system_dependencies` option to specify system package
    dependencies.
- Configuration options for `[spoolman]` have been added
- Configuration options for `[sensor]` have been added

### Februrary 8th 2023
- The `provider` option in the `[machine]` section no longer accepts
  `supervisord` as an option.  It has been renamed to `supervisord_cli`.

### January 2nd 2023
- The `bound_service` option for `[power]` devices has been deprecated in
  favor of `bound_services`.  Currently this change does not generate a
  warning as it can be reliably resolved internally.

### October 14th 2022
- The systemd service file is now versioned.  Moonraker can now detect when
  the file is out of date and automate corrections as necessary.
- Moonraker's command line options are now specified in an environment file,
  making it possible to change these options without modifying the service file
  and reloading the systemd daemon.  The default location of the environment
  file is `~/printer_data/systemd/moonraker.env`.
- Moonraker now manages files and folders in a primary data folder supplied
  by the `-d` (`--data-path`) command line option.  As a result, the following
  options have been deprecated:
    - `ssl_certificate_path` in `[server]`
    - `ssl_key_path` in `[server]`
    - `database_path` in `[database]`
    - `config_path` in `[file_manager]`
    - `log_path` in `[file_manager]`
    - `secrets_path` in `[secrets]`
- Debugging options are now supplied to Moonraker via the command line.
  The `-v` (`--verbose`) option enables verbose logging, while the `-g`
  (`--debug`) option enables debug features, including access to debug
  endpoints and the repo debug feature in `update_manager`.  As a result,
  the following options are deprecated:
    - `enable_debug_logging` in `[server]`
    - `enable_repo_debug` in `[update_manager]`

### July 27th 2022
- The behavior of `[include]` directives has changed.  Included files
  are now parsed as they are encountered.  If sections are duplicated
  options in the last section parsed take precendence.  If you are
  using include directives to override configuration in `moonraker.conf`
  the directives should be moved to the bottom of the file.
- Configuration files now support inline comments.

### April 6th 2022
- The ability to configure core components in the `[server]`section
  is now deprecated.  When legacy items are detected in `[server]` a
  warning will be generated.  It is crucially important to move configuration
  to the correct section as in the future it will be a hard requirement.

### Feburary 22nd 2022
- The `on_when_upload_queued` option for [power] devices has been
  deprecated in favor of `on_when_job_queued`.  As the new option
  name implies, this option will power on the device when any new
  job is queued, not only when its sourced from an upload.  The
  `on_when_upload_queued` option will be treated as an alias to
  `on_when_job_queued` until its removal.

### February 16th 2022
- Webcam settings can now be defined in the `moonraker.conf` file, under
  the `[octoprint_compat]` section. The default values are being used as
  default values.

  Default values:
  | Setting | Default value |
  |---------|---------------|
  | flip_h | False |
  | flip_v | False |
  | rotate_90 | False |
  | stream_url | /webcam/?action=stream |
  | webcam_enabled | True |

### January 22th 2022
- The `color_order` option in the `[wled]` section has been deprecated.
  This is configured in wled directly. This is not a breaking change,
  the setting will simply be ignored not affecting functionality.

### December 24th 2021
- The `password_file` option in the `[mqtt]` section has been deprecated.
  Use the `password` option instead.  This option may be a template, thus
  can resolve secrets stored in the `[secrets]` module.

### November 7th 2021
- Previously all core components received configuration through
  the `[server]` config section.  As Moonraker's core functionality
  has expanded this is becoming unsustainable, thus core components
  should now be configured in their own section. For example, the
  `config_path` and `log_path` should now be configured in the
  `[file_manager]` section of `moonraker.conf`.  See the
  [configuration documentation](https://moonraker.readthedocs.io/en/latest/configuration/)
  for details.  This is not a breaking change, core components
  will still fall back to checking the `[server]` section for
  configuration.

### April 19th 2021
- The `[authorization]` module is now a component, thus is only
  loaded if the user has it configured in `moonraker.conf`.  This
  deprecates the previous `enable` option, as it is enabled
  if configured and disabled otherwise.
- The API Key is now stored in the database.  This deprecates the
  `api_key_file` option in the `[authorization]` module.  Users can
  no longer read the contents of the API Key file to retrieve the
  API Key.  Instead, users can run `scripts/fetch-apikey.sh` to
  print the API Key.  Alternative a user can navigate to
  `http://{moonraker-host}/access/api_key` from a trusted client
  to retrieve the API Key.

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
- To enable OctoPrint compatibility with slicer uploads it is now
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
