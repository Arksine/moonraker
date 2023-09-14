# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog].

## [Unreleased]

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

### Fixed

- **simplyprint**:  Fixed import error preventing the component from loading.
- **update_manager**: Moonraker will now restart the correct "moonraker" and
  "klipper" services if they are not the default values.
- **job_queue**: Fixed transition wihen auto is disabled
- **history**: Added modification time to file existance checks.
- **dbus_manager**: Fixed PolKit warning when PolKit features are not used.
- **job_queue**: Fixed a bug where the `job_transition_gcode` runs when the
  queue is started.  It will now only run between jobs during automatic
  transition.
- **klippy_connection**:  Fixed a race condition that can result in
  skipped subscription updates.

### Changed

- **build**: Bumped apprise to version `1.3.0`.
- **build**:  Bumped lmdb to version `1.4.1`
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
  See the [API Documentation](./web_api.md#get-update-status) for details.

## [0.8.0] - 2023-02-23

!!! Note
    This is the first tagged release since a changelog was introduced.  The list
    below contains notable changes introduced beginning in Feburary 2023. Prior
    notable changes were kept in [user_changes.md] and [api_changes.md].

### Added

- Added this changelog!
- Added pyproject.toml with support for builds through [pdm](https://pdm.fming.dev/latest/).
- **sensor**: New component for generic sensor configuration.
    - [Configuration Docs](configuration.md#sensor)
    - [API Docs](web_api.md#sensor-apis)
    - [Websocket Notification Docs](web_api.md#sensor-events)
- **file_manager**: Added new [scan metadata](web_api.md#scan-gcode-metadata) endpoint.
- **file_manager**: Added new [thumbnails](web_api.md#get-gcode-thumbnails) endpoint.
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
- **websockets**: Added support for [direct bridge](web_api.md#bridge-websocket)
  connections.
- **update_manager**: Added new [refresh](web_api.md#refresh-update-status) endpoint.
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
[unreleased]: https://github.com/Arksine/moonraker/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/Arksine/moonraker/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/Arksine/moonraker/releases/tag/v0.7.1