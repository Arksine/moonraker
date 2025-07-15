# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog].

## [Unreleased]

### Changed
- **data_store**: Store multi-line gcode commands in a single entry.
- **dbus_manager**: Replace unmaintained `dbus-next` requirement with
  `dbus-fast`.
- **pip_utils**:  Use the "upgrade" option when installing python packages.
  This will force upgrades to the latest version available as resolved by
  the requirement specifier.
- **python_deploy**: Use the "eager" dependency update strategy.
- **wled**: Use the `async_serial` utility for serial comms.
- **paneldue**: Use the `async_serial` utility for serial comms.
- **scripts**: Update `fetch-apikey.sh` to query the SQL database
- **update_manager**: The following endpoints have been deprecated
  as of API version 1.5.0:
  - `/machine/update/full`
  - `/machine/update/client`
  - `/machine/update/moonraker`
  - `/machine/update/klipper`
  - `/machine/update/system`

  The new `/machine/update/upgrade` endpoint replaces the functionality
  of all of the above.  The deprecated endpoints will NOT be removed,
  so existing software does not need to be changed.  New software
  should use the new endpoint, however it may be desirable to also
  support the deprecated `full` and `client` endpoints for compatibility
  with older API versions.
- **simplyprint**: Improve job progress calculation.
- **build**: Bump PDM-Backend to 2.4.3.
- **build**: Bump Apprise to 1.9.2
- **build**: Bump Tornado to 6.5.1
- **build**: Bump Streaming-form-data to 1.19.1
- **build**: Bump Jinja2 to 3.1.5
- **build**: Bump dbus-fast to 2.44.1

### Fixed
- **python_deploy**: fix "dev" channel updates for GitHub sources.
- **python_deploy**: fix release rollbacks.
- **mqtt**: Publish the result of the Klipper status subscription request.
  This fixes issues with MQTT clients missing the initial status updates
  after Klippy restarts.
- **eventloop**:  Fixed a condition where the garbage collector may
  prematurely cancel background tasks.
- **spoolman**: Use the default websocket ping timeout.  Disable pinging for
  versions of Tornado prior to 6.5.0.
- **application**: Disable pinging for versions of Tornado prior to 6.5.0.


### Added
- **application**: Verify that a filename is present when parsing the
  multipart/form-data for uploads.
- **application**: Log all failed HTTP API requests when verbose logging
  is enabled.
- **install**: Support "requirement specifiers" for system packages.
  Initially this is limited to restricting packages to a specific
  distro version.
- **async_serial**: Basic asyncio wrapper around pyserial.
- **wled**: Add initial support for receiving responses.
- **scripts**: Add a `-g` option to `fetch-apikey.sh`.  When specified
  a new API Key will be generated and stored in the database.  After
  running this script it is necessary to restart Moonraker.
- **update_manager**:  Report `name` and `configured_type` for all status
  response types.  This adds consistency and allows front-end devs to
  simply iterate over the values of the `version_info` object.
- **python_deploy**: Add support for updating python packages with
  "extras" installed.
- **update_manager**:  Add support for updating `executable` binaries.
- **update_manager**:  Added a `report_anomalies` option for git, web, and zip
  types.
- **analysis**: Initial support for gcode file time analysis using
  [Klipper Estimator](https://github.com/Annex-Engineering/klipper_estimator).
- **power**: Added the ability to discard unwanted responses for MQTT
  power devices.
- **power**: Added `poll_interval` option for HTTP (and all derivatives),
  TPLink Smartplug, and uhubctl devices.  When set Moonraker will poll device
  status.
- **power**: Added `restrict_action_processing` option.  When set to `False`,
  post toggle actions such as restarting Klippy and controlling bound services
  are run when an external power event is detected.

## [0.9.3] - 2024-09-05

### Changed
- **server**: Use `asyncio.run` to launch the server as recommended by the
  official Python documentation.
- **announcements**: Look for xml files at `<data_path>/development/announcements`
  when `dev_mode` is set to True.
- **build**: Move scripts from the "data" directory into a folder inside the
  moonraker package.

### Fixed
- **confighelper**: Don't resolve symbolic links to the main configuration file.
- **power**: Allow special characters in the user/pass options for backends that
  support Basic Authentication.

## [0.9.2] - 2024-07-30

### Added
- **install**: Add support for installing Moonraker's python package via pip.
- **scripts**: Add script to sync python and system dependencies from
  `pyproject.toml` and `system-dependencies.json` respectively.
- **dev**: Add pre-commit hook to call `sync_dependencies.py`.

### Fixed
- **build**: Build from sdist now correctly includes share data.
- **build**: Remove stray `.gitignore` from Python Wheel.

### Changed
- **install**: The `MOONRAKER_FORCE_DEFAULTS` environment variable has changed
  to `MOONRAKER_FORCE_SYSTEM_INSTALL`.

## [0.9.1] - 2024-07-25

### Fixed
- **source_info**: Fixed `importlib.metadata` compatibility issues with python
  versions 3.9 or older.

## [0.9.0] - 2024-07-25

### Added
- **notifier**: The `attach` option now supports Jinja2 templates.
- **notifier**: The `attach` option may now contain multiple attachments,
  each separated by a newline.
- **notifier**: Added support for a configurable `body_format`
- **power**: Added support for generic `http` type switches.
- **metadata**: Added support for OrcaSlicer
- **zeroconf**: Added support for a configurable mDNS hostname.
- **zeroconf**: Added support for UPnP/SSDP Discovery.
- **spoolman**: Added integration to the
  [Spoolman](https://github.com/Donkie/Spoolman) filament manager.
- **update_manager**: Added support for update rollbacks
- **update_manager**: Added support for stable `git_repo` updates
- **server**: Added a `--unixsocket` command line option
- **server**: Command line options may also be specified as env variables
- **server**: Added a `route_prefix` option
- **webcam**: Webcam APIs can now specify cameras by `uid` or `name`
- **deps**:  Added support for optional `msgspec` and `uvloop` packages
- **extensions**: Agents may now register remote methods with Klipper
- **file_manager**: Add `check_klipper_config_path` option
- **button**: Added `debounce_period` option
- **history**:  Added a check for previous jobs not finished (ie: when power is
  lost during a print).  These jobs will report their status as `interrupted`.
- **build**: Added support for optional speedup dependencies `uvloop` and `msgspec`
- **update_manager**: Added support for "zipped" application updates
- **file_manager**: Added `enable_config_write_access` option
- **machine**: Add support for system peripheral queries
- **mqtt**:  Added the `status_interval` option to support rate limiting
- **mqtt**:  Added the `enable_tls` option to support ssl/tls connections
- **mqtt**:  Added support for a configurable `client_id`
- **history**: Added `user` field to job history data
- **history**: Added support for auxiliary history fields
- **spoolman**:  Report spool ids set during a print in history auxiliary data
- **sensor**: Added support for history fields reported in auxiliary data
- **power**:  Added support for `uhubctl` devices
- **update_manager**: Add support for pinned git commits
- **update_manager**: Added support for updating pip managed python apps

### Fixed

- **simplyprint**:  Fixed import error preventing the component from loading.
- **update_manager**: Moonraker will now restart the correct "moonraker" and
  "klipper" services if they are not the default values.
- **job_queue**: Fixed transition when auto is disabled
- **history**: Added modification time to file existence checks.
- **dbus_manager**: Fixed PolKit warning when PolKit features are not used.
- **job_queue**: Fixed a bug where the `job_transition_gcode` runs when the
  queue is started.  It will now only run between jobs during automatic
  transition.
- **klippy_connection**:  Fixed a race condition that can result in
  skipped subscription updates.
- **configheler**: Fixed inline comment parsing.
- **authorization**: Fixed blocking call to `socket.getfqdn()`
- **power**: Fixed "on_when_job_queued" behavior when the internal device
  state is stale.

### Changed

- **build**: Bumped apprise to version `1.8.0`.
- **build**: Bumped lmdb to version `1.4.1`
- **build**: Bumped tornado to version `6.4.0`
- **build**: Bumped jinja2 to version `3.1.4`
- **build**: Bumped zeroconf to version `0.131.0`
- **build**: Bumped libnacl to version `2.1.0`
- **build**: Bumped distro to version `1.9.0`
- **build**: Bumped pillow to version `10.3.0`
- **build**: Bumped streaming-form-data to version `1.15.0`
- **machine**: Added `ratos-configurator` to list of default allowed services
- **update_manager**:  It is now required that an application be "allowed"
  for Moonraker to restart it after an update.
- **update_manager**:  Git repo validation no longer requires a match for the
  remote URL and/or branch.
- **update_manager**: Fixed potential security vulnerabilities in `web` type updates.
  This change adds a validation step to the install, front-end developers may refer to
  the [configuration documentation](./configuration.md#web-type-front-end-configuration)
  for details.
- **update_manager**: The `env` option for the `git_repo` type has been deprecated, new
  configurations should use the `virtualenv` option.
- **update_manager**: The `install_script` option for the `git_repo` has been
  deprecated, new configurations should use the `system_dependencies` option.
- **update_manager**: APIs that return status report additional fields.
  See the [API Documentation](./external_api/update_manager.md#get-update-status)
  for details.
- **proc_stats**: Improved performance of Raspberry Pi CPU throttle detection.
- **power**:  Bound services are now processed during initialization when
  `initial_state` is configured.
- **gpio**:  Migrate from libgpiod to python-periphery
- **authorization**:  The authorization module is now loaded as part of Moonraker's
  core.
- **database**: Migrated the underlying database from LMDB to Sqlite.
- **history**: Use dedicated SQL tables to store job history and job totals.
- **authorization**: Use a dedicated SQL table to store user data.

### REMOVED

- **simplyprint**: Removed defunct "AI" functionality

## [0.8.0] - 2023-02-23

!!! Note
    This is the first tagged release since a changelog was introduced.  The list
    below contains notable changes introduced beginning in February 2023. Prior
    notable changes were kept in [user_changes.md] and [api_changes.md].

### Added

- Added this changelog!
- Added pyproject.toml with support for builds through [pdm](https://pdm.fming.dev/latest/).
- **sensor**: New component for generic sensor configuration.
    - [Configuration Docs](configuration.md#sensor)
    - [API Docs](./external_api/devices.md#sensor-endpoints)
    - [Websocket Notification Docs](./external_api/jsonrpc_notifications.md#sensor-events)
- **file_manager**: Added new [scan metadata](./external_api/file_manager.md#scan-gcode-metadata) endpoint.
- **file_manager**: Added new [thumbnails](./external_api/file_manager.md#get-gcode-thumbnail-details) endpoint.
- **file_manager**: Added [file_system_observer](configuration.md#file_manager)
  configuration option.
- **file_manager**: Added [enable_observer_warnings](configuration.md#file_manager)
  configuration option.
- **file_manager**: Added ability to upload to symbolic links.
- **metadata**: Added support for Simplify3D V5 metadata parsing
- **machine**: Added [shutdown_action](configuration.md#machine) configuration
  option.
- **machine**: Added service detection to the `supervisord_cli` provider.
- **machine**: Added `octoeverywhere` to the list of default allowed service.
- **power**: Added support for "Hue" device groups.
- **websockets**: Added support for [direct bridge](./external_api/introduction.md#bridge-websocket)
  connections.
- **update_manager**: Added new [refresh](./external_api/update_manager.md#refresh-update-status) endpoint.
- **update_manager**: Added support for pinned pip upgrades.
- **websockets**:  Added support for post connection authentication over the websocket.
- **scripts**:  Added database backup and restore scripts.

### Changed

- Converted Moonraker source into a Python package.
- The source from `moonraker.py` has been moved to `server.py`.  The remaining code in
  `moonraker.py` serves as a legacy entry point for launching Moonraker.
- **file_manager**: Improved inotify synchronization with API requests.
- **file_manager**: Endpoint return values are now consistent with their
  respective websocket notifications.
- **machine**: The [provider](configuration.md#machine) configuration option
  now expects `supervisord_cli` instead of `supervisord`.
- **update_manager**: Relaxed requirement for git repo tag detection.  Now only two
  parts are required (ie: v1.5 and v1.5.0 are acceptable).

### Deprecated

- **file_manager**: The `enable_inotify_warnings` configuration option has been
  deprecated in favor of `enable_observer_warnings`.

### Fixed

- **file_manager**: Fix edge condition where `create_file` notifications
  may be sent before a `create_dir` notification.
- **power** - Fixed URL encoding issues for http devices.
- **template**: A ConfigError is now raised when a template fails to
  render during configuration.
- **machine**: Fixed support for Supervisord Version 4 and above.
- **update_manager**: Added package resolution step to the APT backend.
- **update_manger**: Fixed PackageKit resolution step for 64-bit systems.
- **update_manager**: Fixed Python requirements file parsing.  Comments are now ignored.

### Removed

- Pycurl dependency.  Moonraker no longer uses Tornado's curl based http client.

## [0.7.1] - 2021-07-08

- Experimental pre-release

<!-- Links -->
[keep a changelog]: https://keepachangelog.com/en/1.0.0/
[semantic versioning]: https://semver.org/spec/v2.0.0.html
[user_changes.md]: user_changes.md
[api_changes.md]: api_changes.md

<!-- Versions -->
[unreleased]: https://github.com/Arksine/moonraker/compare/v0.9.3...HEAD
[0.9.3]: https://github.com/Arksine/moonraker/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/Arksine/moonraker/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/Arksine/moonraker/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/Arksine/moonraker/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Arksine/moonraker/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Arksine/moonraker/releases/tag/v0.7.1