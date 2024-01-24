# Update Management

The endpoints in the section are available when the `[update_manager]`
component has been configured in `moonraker.conf`.  They may be used
to manage updates for Moonraker, Klipper, OS Packages, and additional
software added through the configuration.

## Get update status

```{.http .apirequest title="HTTP Request"}
GET /machine/update/status
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.status",
    "id": 4644
}
```

/// api-parameters
    open: True

| Name      | Type | Default | Description                                     |
| --------- | :--: | ------- | ----------------------------------------------- |
| `refresh` | bool | false   | *DEPRECATED*.  When `true` an attempt will      |
|           |      |         | be made to refresh all updaters. The refresh    |^
|           |      |         | will abort under the following conditions:<BR/> |^
|           |      |         | - an update is in progress<BR/>                 |^
|           |      |         | - a print is in progress<BR/>                   |^
|           |      |         | - the update manager hasn't completed           |^
|           |      |         | initialization<BR/>                             |^
|           |      |         | - a refresh has been performed within the last  |^
|           |      |         | 60 seconds<BR/>                                 |^

//// Note
The `refresh` parameter is deprecated.  Front end developers should use the
[refresh endpoint](#refresh-update-status) to request a refresh.
////
///

/// collapse-code
```{.json #status-example-response .apiresponse title="Example Response"}
{
    "busy": false,
    "github_rate_limit": 60,
    "github_requests_remaining": 57,
    "github_limit_reset_time": 1615836932,
    "version_info": {
        "system": {
            "name": "system",
            "configured_type": "system",
            "package_count": 4,
            "package_list": [
                "libtiff5",
                "raspberrypi-sys-mods",
                "rpi-eeprom-images",
                "rpi-eeprom"
            ]
        },
        "moonraker": {
            "channel": "dev",
            "debug_enabled": true,
            "is_valid": true,
            "configured_type": "git_repo",
            "corrupt": false,
            "info_tags": [],
            "detected_type": "git_repo",
            "name": "moonraker",
            "remote_alias": "arksine",
            "branch": "master",
            "owner": "arksine",
            "repo_name": "moonraker",
            "version": "v0.7.1-364",
            "remote_version": "v0.7.1-364",
            "rollback_version": "v0.7.1-360",
            "current_hash": "ecfad5cff15fff1d82cb9bdc64d6b548ed53dfaf",
            "remote_hash": "ecfad5cff15fff1d82cb9bdc64d6b548ed53dfaf",
            "is_dirty": false,
            "detached": true,
            "commits_behind": [],
            "git_messages": [],
            "full_version_string": "v0.7.1-364-gecfad5c",
            "pristine": true,
            "recovery_url": "https://github.com/Arksine/moonraker.git",
            "remote_url": "https://github.com/Arksine/moonraker.git",
            "warnings": [],
            "anomalies": [
                "Unofficial remote url: https://github.com/Arksine/moonraker-fork.git",
                "Repo not on official remote/branch, expected: origin/master, detected: altremote/altbranch",
                "Detached HEAD detected"
            ]
        },
        "mainsail": {
            "name": "mainsail",
            "owner": "mainsail-crew",
            "version": "v2.1.1",
            "remote_version": "v2.1.1",
            "rollback_version": "v2.0.0",
            "configured_type": "web",
            "channel": "stable",
            "info_tags": [
                "desc=Mainsail Web Client",
                "action=some_action"
            ],
            "warnings": [],
            "anomalies": [],
            "is_valid": true
        },
        "fluidd": {
            "name": "fluidd",
            "owner": "fluidd-core",
            "version": "v1.16.2",
            "remote_version": "v1.16.2",
            "rollback_version": "v1.15.0",
            "configured_type": "web",
            "channel": "beta",
            "info_tags": [],
            "warnings": [],
            "anomalies": [],
            "is_valid": true
        },
        "klipper": {
            "channel": "dev",
            "debug_enabled": true,
            "is_valid": true,
            "configured_type": "git_repo",
            "corrupt": false,
            "info_tags": [],
            "detected_type": "git_repo",
            "name": "klipper",
            "remote_alias": "origin",
            "branch": "master",
            "owner": "Klipper3d",
            "repo_name": "klipper",
            "version": "v0.10.0-1",
            "remote_version": "v0.10.0-41",
            "rollback_version": "v0.9.1-340",
            "current_hash": "4c8d24ae03eadf3fc5a28efb1209ce810251d02d",
            "remote_hash": "e3cbe7ea3663a8cd10207a9aecc4e5458aeb1f1f",
            "is_dirty": false,
            "detached": false,
            "commits_behind": [
                {
                    "sha": "e3cbe7ea3663a8cd10207a9aecc4e5458aeb1f1f",
                    "author": "Kevin O'Connor",
                    "date": "1644534721",
                    "subject": "stm32: Clear SPE flag on a change to SPI CR1 register",
                    "message": "The stm32 specs indicate that the SPE bit must be cleared before\nchanging the CPHA or CPOL bits.\n\nReported by @cbc02009 and @bigtreetech.\n\nSigned-off-by: Kevin O'Connor <kevin@koconnor.net>",
                    "tag": null
                },
                {
                    "sha": "99d55185a21703611b862f6ce4b80bba70a9c4b5",
                    "author": "Kevin O'Connor",
                    "date": "1644532075",
                    "subject": "stm32: Wait for transmission to complete before returning from spi_transfer()",
                    "message": "It's possible for the SCLK pin to still be updating even after the\nlast byte of data has been read from the receive pin.  (In particular\nin spi mode 0 and 1.)  Exiting early from spi_transfer() in this case\ncould result in the CS pin being raised before the final updates to\nSCLK pin.\n\nAdd an additional wait at the end of spi_transfer() to avoid this\nissue.\n\nSigned-off-by: Kevin O'Connor <kevin@koconnor.net>",
                    "tag": null
                }
            ],
            "git_messages": [],
            "full_version_string": "v0.10.0-1-g4c8d24ae-shallow",
            "pristine": true,
            "recovery_url": "https://github.com/Klipper3d/klipper.git",
            "remote_url": "https://github.com/Klipper3d/klipper.git",
            "warnings": [],
            "anomalies": []
        }
    }
}
```
///

/// api-response-spec
    open: True

| Field                       |  Type  | Description                                           |
| --------------------------- | :----: | ----------------------------------------------------- |
| `busy`                      |  bool  | Set to `true` if an update is currently in progress.  |
| `github_rate_limit`         |  int   | The maximum number of GitHub API requests allowed.    |
|                             |        | An unauthenticated user is typically allowed 60       |^
|                             |        | requests per hour.                                    |^
| `github_requests_remaining` |  int   | The number of GitHub API requests remaining until the |
|                             |        | reset time is reached.                                |^
| `github_limit_reset_time`   |  int   | The time when the rate limit will reset, reported in  |
|                             |        | unix time.                                            |^
| `version_info`              | object | A `Version Info` object containing the update status  |
|                             |        | for each configured software updater.                 |^
|                             |        | #version-info-desc                                    |+
{ #update-status-spec }

| Field     |  Type  | Description                                                |
| --------- | :----: | ---------------------------------------------------------- |
| `system`  | object | A [System Status](#system-status-spec) object.             |
|           |        | This field is only available  when the `update_manager` is |^
|           |        | configured to update system packages.                      |^
| _updater_ | object | A `Status Update` object.  There are multiple sub-types of |
|           |        | status objects, the specific type can be determined by the |^
|           |        | object's [configured_type](#configured-type-desc) field.   |^
|           |        | The `version_info` object may have multiple _updater_      |^
|           |        | items. The field name for each updater will match the      |^
|           |        | object's `name`.                                           |^
{ #version-info-desc }  Version Info

| Type       | Description                                                |
| ---------- | ---------------------------------------------------------- |
| `git_repo` | A [Git Repo Status](#git-repo-status-spec) object.         |
|            | The software is distributed through a git repo on GitHub.  |^
| `web`      | A [Net Hosted Status](#net-app-status-spec ) object.       |
|            | The software is a web application.                         |^
|            | Updates are GitHub hosted releases packaged in a zip file. |^
| `zip`      | A [Net Hosted Status](#net-app-status-spec ) object.       |
|            | The software is a local application, optionally installed  |^
|            | as a system service.  Updates are GitHub hosted  releases  |^
|            | packaged in a zip file.                                    |^
| `python`   | A [Python Package Status](#python-status-spec) object.     |
|            | The software is a Python Application installed in its own  |^
|            | virtualenv.  Updates may be hosted on PyPI or GitHub.      |^
|            | Pip is used to deploy updates.                             |^
| `system`   | A [System Status](#system-status-spec) object.  This type  |
|            | is internally managed and only applicable to the system    |^
|            | package manager.                                           |^
{ #configured-type-desc } Configured Types

| Field                  |   Type   | Description                                              |
| ---------------------- | :------: | -------------------------------------------------------- |
| `name`                 |  string  | The name of the software to manage updates for.          |
| `configured_type`      |  string  | The [type](#configured-type-desc) of updater configured. |
| `detected_type`        |  string  | **DEPRECATED.** Will always report `git_repo`.           |
| `channel`              |  string  | The configured update `channel`.                         |
|                        |          | #git-repo-channel-desc                                   |+
| `channel_invalid`      |   bool   | A value of `true` indicates that the current `channel`   |
|                        |          | configuration is not supported by the type.  Will        |^
|                        |          | always be `false` for `git_repo` types as all channels   |^
|                        |          | are supported.                                           |^
| `debug_enabled`        |   bool   | Set to `true` when Moonraker's debug features are        |
|                        |          | enabled.  In this condition updates may proceed when the |^
|                        |          | repo's HEAD is detached.                                 |^
| `is_valid`             |   bool   | Set to `true` when repo detection completes and passes   |
|                        |          | all validity checks.                                     |^
| `version`              |  string  | The current detected version.                            |
| `remote_version`       |  string  | The latest version available on the remote.              |
| `rollback_version`     |  string  | The version prior to the last update.  This version is   |
|                        |          | used during a `rollback` request.                        |^
| `full_version_string`  |  string  | The complete version string reported by `git describe`.  |
|                        |          | Generally includes an abbreviated hash of the current    |^
|                        |          | commit and tags such as "dirty" when appropriate.        |^
| `remote_hash`          |  string  | The latest available commit hash on the remote.          |
| `current_hash`         |  string  | The commit hash the local repo is currently on.          |
| `remote_alias`         |  string  | The git alias of the remote.  The git default for the    |
|                        |          | primary alias is `origin`.                               |^
| `remote_url`           |  string  | Full URL of the git remote matching the current          |
|                        |          | `remote_alias`.                                          |^
| `recovery_url`         |  string  | The `origin` git remote URL for this repo. This URL is   |
|                        |          | used to perform a `hard recovery` when requested.        |^
| `owner`                |  string  | The owner of the remote repo as detected from the remote |
|                        |          | URL.                                                     |^
| `branch`               |  string  | The name of the current git branch.                      |
| `repo_name`            |  string  | The name of the remote repo as detected from the remote  |
|                        |          | URL.                                                     |^
| `is_dirty`             |   bool   | Set to `true` if the repo is "dirty", ie: if one or      |
|                        |          | more files in the repo have been modified.               |^
| `corrupt`              |   bool   | Set to `true` if the repo is corrupt.  This indicates    |
|                        |          | that the local repo is broken and needs to be recovered. |^
| `pristine`             |   bool   | Set to `true` when the repo is clean and no untracked    |
|                        |          | files exist in the repo.                                 |^
| `detached`             |   bool   | Set to `true` when the git repo's HEAD is detached.      |
| `git_messages`         | [string] | An array of strings containing the output from a failed  |
|                        |          | `git` command during initialization or an update.  This  |^
|                        |          | array will be empty if all `git` commands succeed.       |^
| `anomalies`            | [string] | An array of strings that describe anomalies found during |
|                        |          | initialization.  An anomaly can be defined as an         |^
|                        |          | unexpected condition that does not result in an          |^
|                        |          | `invalid` repo state.  Updates may proceed when          |^
|                        |          | anomalies are detected.  An example of an anomaly is the |^
|                        |          | presence of "untracked files" in the repo.               |^
| `warnings`             | [string] | An array of strings that describe warnings detected      |
|                        |          | during repo  initialization. When a warning is present   |^
|                        |          | the repo is marked invalid and updates are disabled.     |^
| `commits_behind`       | [object] | An array of `Commit Info` objects providing commit data  |
|                        |          | on upstream commits available for update.  This array is |^
|                        |          | limited to a size of 30 untagged commits.  Any tagged    |^
|                        |          | commits within 100 commits behind are included.          |^
|                        |          | #git-commit-info-spec                                    |+
| `commits_behind_count` |   int    | The total number of commits the current repo is behind   |
|                        |          | the next update.  This number may be greater than the    |^
|                        |          | length of the `commits_behind` array.                    |^
| `info_tags`            |  object  | An object containing custom tags added to the updater's  |
|                        |          | configuration in `moonraker.conf`.  The values will      |^
|                        |          | always be strings.  Client developers may define what    |^
|                        |          | tags, if any, users will configure.  The software can    |^
|                        |          | then choose to display information or perform a          |^
|                        |          | specific action pre/post update if necessary.            |^
{ #git-repo-status-spec } Git Repo Status

| Channel  | Description                                               |
| -------- | --------------------------------------------------------- |
| `stable` | The repo will update to the latest tagged stable release. |
| `beta`   | The repo will update to the latest tag.  This may include |
|          | tags with `beta` and `release candidate` identifiers.     |^
| `dev`    | The repo will update to the latest available commit.      |
{ #git-repo-channel-desc }

| Field     |      Type      | Description                                           |
| --------- | :------------: | ----------------------------------------------------- |
| `author`  |     string     | The author of the commit.                             |
| `date`    |     string     | The date of the commit in unix time.  Note that the   |
|           |                | date is extracted from the git log as a string value. |^
|           |                | It should be converted to an integer prior to         |^
|           |                | processing from unix time.                            |^
| `sha`     |     string     | The commit hash.                                      |
| `subject` |     string     | The title of the commit.                              |
| `message` |     string     | The content in the body of the commit.                |
| `tag`     | string \| null | The name of the associated tag if present.  Will be   |
|           |                | null if the commit has no tag.                        |^
{ #git-commit-info-spec } Commit Info


| Field              |   Type   | Description                                              |
| ------------------ | :------: | -------------------------------------------------------- |
| `name`             |  string  | The name of the software to manage updates for.          |
| `configured_type`  |  string  | The [type](#configured-type-desc) of updater configured. |
| `channel`          |  string  | The configured update `channel`.                         |
|                    |          | #net-hosted-channel-desc                                 |+
| `channel_invalid`  |   bool   | A value of `true` indicates that the current `channel`   |
|                    |          | configuration is not supported by the type.              |^
|                    |          | are supported.                                           |^
| `debug_enabled`    |   bool   | Set to `true` when Moonraker's debug features are        |
|                    |          | enabled.                                                 |^
| `owner`            |  string  | The owner of the GitHub repo hosting the software.       |
| `repo_name`        |  string  | The name of the GitHub repo hosting the software.        |
| `last_error`       |  string  | A message associated with the last error encountered     |
|                    |          | after initialization or an update.  Will be an empty     |^
|                    |          | string if no errors were detected.                       |^
| `version`          |  string  | The current detected version.                            |
| `remote_version`   |  string  | The version of the latest available release on GitHub.   |
| `rollback_version` |  string  | The version prior to the last update.  This version is   |
|                    |          | used during a `rollback` request.                        |^
| `is_valid`         |   bool   | Set to `true` when the updater has completed             |
|                    |          | initialization and all validity checks passed.           |^
| `anomalies`        | [string] | An array of strings that describe anomalies found during |
|                    |          | initialization.  An anomaly can be defined as an         |^
|                    |          | unexpected condition that does not result in an          |^
|                    |          | `invalid` updater state.  Updates may proceed when       |^
|                    |          | anomalies are detected.                                  |^
| `warnings`         | [string] | An array of strings that describe warnings detected      |
|                    |          | during initialization. When a warning is present         |^
|                    |          | the updater is marked invalid and updates are disabled.  |^
| `info_tags`        |  object  | An object containing custom tags added to the updater's  |
|                    |          | configuration in `moonraker.conf`.  The values will      |^
|                    |          | always be strings.  Client developers may define what    |^
|                    |          | tags, if any, users will configure.  The software can    |^
|                    |          | then choose to display information or perform a          |^
|                    |          | specific action pre/post update if necessary.            |^
{ #net-app-status-spec } Net Hosted Status

| Channel  | Description                                               |
| -------- | --------------------------------------------------------- |
| `stable` | The software will update to the stable release on GitHub. |
| `beta`   | The software will update to the latest release on GitHub, |
|          | including those marked as "pre-release".                  |^
{ #net-hosted-channel-desc }

| Field                 |      Type      | Description                                               |
| --------------------- | :------------: | --------------------------------------------------------- |
| `name`                |     string     | The name of the software to manage updates for.           |
| `configured_type`     |     string     | The [type](#configured-type-desc) of updater configured.  |
| `channel`             |     string     | The configured update `channel`.                          |
|                       |                | #python-channel-desc                                      |+
| `channel_invalid`     |      bool      | A value of `true` indicates that the current `channel`    |
|                       |                | configuration is not supported by the type.               |^
|                       |                | are supported.                                            |^
| `debug_enabled`       |      bool      | Set to `true` when Moonraker's debug features are         |
|                       |                | enabled.                                                  |^
| `owner`               |     string     | The owner of the GitHub repo hosting the software.        |
|                       |                | Will be a `?` when no repo owner is detected.             |^
| `repo_name`           |     string     | The name of the GitHub repo hosting the software.         |
|                       |                | Will be a `?` when no repo name is detected.              |^
| `branch`              | string \| null | The name of the branch on the GitHub remote to build      |
|                       |                | `dev` updates from.  Will be `null` if no primary branch  |^
|                       |                | is configured.                                            |^
| `version`             |     string     | The current detected version.                             |
| `remote_version`      |     string     | The version of the latest available release on GitHub.    |
| `rollback_version`    |     string     | The version prior to the last update.  This version is    |
|                       |                | used during a `rollback` request.                         |^
| `full_version_string` |     string     | The complete version string extracted from the python     |
|                       |                | package's metadata.                                       |^
| `current_hash`        |     string     | The hash of the commit used to build the current version  |
|                       |                | of the package.  A placeholder of `not-specified` is used |^
|                       |                | when the the current hash is not provided in the package  |^
|                       |                | metadata.                                                 |^
| `remote_hash`         |     string     | The hash of the latest update available. A placeholder of |
|                       |                | `update-available` is used when the remote hash is not    |^
|                       |                | provided by the remote host and updates are available.    |^
| `is_valid`            |      bool      | Set to `true` when the updater has completed              |
|                       |                | initialization and all validity checks passed.            |^
| `is_dirty`            |      bool      | Set to `true` if the repo was modified at the time the    |
|                       |                | package was built.                                        |^
| `changelog_url`       |     string     | A URL to the software's changelog.  Will be an empty      |
|                       |                | string if no changelog URL is detected.                   |^
| `anomalies`           |    [string]    | An array of strings that describe anomalies found during  |
|                       |                | initialization.  An anomaly can be defined as an          |^
|                       |                | unexpected condition that does not result in an           |^
|                       |                | `invalid` updater state.  Updates may proceed when        |^
|                       |                | anomalies are detected.                                   |^
| `warnings`            |    [string]    | An array of strings that describe warnings detected       |
|                       |                | during initialization. When a warning is present          |^
|                       |                | the updater is marked invalid and updates are disabled.   |^
| `info_tags`           |     object     | An object containing custom tags added to the updater's   |
|                       |                | configuration in `moonraker.conf`.  The values will       |^
|                       |                | always be strings.  Client developers may define what     |^
|                       |                | tags, if any, users will configure.  The software can     |^
|                       |                | then choose to display information or perform a           |^
|                       |                | specific action pre/post update if necessary.             |^
{ #python-status-spec } Python Package Status

| Channel  | Description                                              |
| -------- | -------------------------------------------------------- |
| `stable` | The software will update to the stable release on GitHub |
|          | or PyPI.                                                 |^
| `beta`   | Only applies to packages installed via GitHub.  The      |
|          | software will update to the latest release, including    |^
|          | hose marked as "pre-release".                            |^
| `dev`    | Only applies to packages installed via GitHub.  The      |
|          | software will update to the latest commit available.     |^
{ #python-channel-desc }


| Field             |   Type   | Description                                              |
| ----------------- | :------: | -------------------------------------------------------- |
| `name`            |  string  | The name of the software to manage updates for. Will     |
|                   |          | always be `system`.                                      |^
| `configured_type` |  string  | The [type](#configured-type-desc) of updater configured. |
|                   |          | Will always be `system`.                                 |^
| `package_count`   |   int    | The number of system packages that require updating.     |
| `package_list`    | [string] | An array of package names that require updating.         |
{ #system-status-spec } System Update Status

///

## Refresh update status

Refreshes the internal update state for the requested software.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/refresh
Content-Type: application/json

{
    "name": "klipper"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.refresh",
    "params": {
        "name": "klipper"
    },
    "id": 4644
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default | Description                                           |
| ------ | :----: | ------- | ----------------------------------------------------- |
| `name` | string | `null`  | The name of the software to refresh.                  |
|        |        |         | If omitted all registered software will be refreshed. |^

///

/// Note
This endpoint will raise 503 error under the following conditions:

  - An update is in progress
  - A print is in progress
  - The update manager hasn't completed initialization
///

For an example response refer to the
[Status Example Response](#status-example-response).

/// api-response-spec
    open: True
The response spec is identical to the
[Status Request Specification](#update-status-spec)
///

/// Tip
Applications should use care when calling this method as a refresh
is CPU intensive and may be time consuming.  Moonraker can be
configured to refresh state periodically, thus it is recommended
that applications avoid their own procedural implementations.
Instead it is best to call this API only when a user requests a
refresh.
///

## Perform an Upgrade

*Added in API Version 1.5.0*

Upgrade to the most recent release of the requested software.
If an update is requested while a print is in progress then this
request will return an error.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/upgrade
Content-Type: application/json

{
    "name": "app_name"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method":  "machine.update.upgrade",
    "params": {
        "name": "app_name"
    },
    "id": 8546
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default | Description                          |
| ------ | :----: | ------- | ------------------------------------ |
| `name` | string | null    | The name of the software to upgrade. |
|        |        |         | If omitted all registered            |^
|        |        |         | software updates will be upgraded.   |^

///

```{.text .apiresponse title="Response"}
"ok"
```

## Recover a corrupt repo

On occasion a git command may fail resulting in a repo in a
dirty or invalid state.  This endpoint may be used to attempt
to recover a git repo that is dirty, broken, or corrupt.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/recover
Content-Type: application/json

{
    "name": "moonraker",
    "hard": false
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.recover",
    "params": {
        "name": "moonraker",
        "hard": false
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default      | Description                                         |
| ------ | :----: | ------------ | --------------------------------------------------- |
| `name` | string | **REQUIRED** | The name of the software to recover.                |
| `hard` |  bool  | false        | Determines the [mode](#git-repo-recovery-mode-desc) |
|        |        |              | used to perform the recovery.                       |^

| Name            | Description                                             |
| --------------- | ------------------------------------------------------- |
| `hard == false` | Moonraker will attempt to recover the repo by running   |
|                 | `git reset`.  This will generally work for repos that   |^
|                 | are dirty, but will not correct repos that are corrupt. |^
| `hard == true`  | Moonraker will remove the current repo and re-clone it. |
{ #git-repo-recovery-mode-desc } Recovery Modes

///

```{.text .apiresponse title="Response"}
"ok"
```

## Rollback to the previous version

```{.http .apirequest title="HTTP Request"}
POST /machine/update/rollback
Content-Type: application/json

{
    "name": "moonraker"
}
```

JSON-RPC request:

```json
{
    "jsonrpc": "2.0",
    "method": "machine.update.rollback",
    "params": {
        "name": "moonraker"
    },
    "id": 4564
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Perform a full update

*Deprecated in API Version 1.5.0, superseded by the*
*[Upgrade](#perform-an-upgrade) endpoint.*

Attempts to update all registered software.  Updates are performed in the
following order:

- `system` if enabled
- All optional software configured in `moonraker.conf`.
- Klipper
- Moonraker

```{.http .apirequest title="HTTP Request"}
POST /machine/update/full
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.full",
    "id": 4645
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Update Moonraker

*Deprecated in API Version 1.5.0, superseded by the*
*[Upgrade](#perform-an-upgrade) endpoint.*

Upgrades to the latest version of Moonraker and restarts
the service. If an update is requested while a print is in progress then
this request will return an error.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/moonraker
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.moonraker",
    "id": 4645
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Update Klipper

*Deprecated in API Version 1.5.0, superseded by the*
*[Upgrade](#perform-an-upgrade) endpoint.*

Upgrades to the latest version of Klipper and restarts
the service. If an update is requested while a print is in progress
then this request will return an error.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/klipper
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.klipper",
    "id": 5745
}
```
```{.text .apiresponse title="Response"}
"ok"
```

## Update Client

*Deprecated in API Version 1.5.0, superseded by the*
*[Upgrade](#perform-an-upgrade) endpoint.*

Update to the most recent release of the requested software.
If an update is requested while a print is in progress then this
request will return an error.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/client
Content-Type: application/json

{
    "name": "app_name"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method":  "machine.update.client",
    "params": {
        "name": "app_name"
    },
    "id": 8546
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default | Description                          |
| ------ | :----: | ------- | ------------------------------------ |
| `name` | string | null    | The name of the software to upgrade. |
|        |        |         | If omitted all registered            |^
|        |        |         | software updates will be upgraded.   |^

///

```{.text .apiresponse title="Response"}
"ok"
```

## Update System Packages

*Deprecated in API Version 1.5.0, superseded by the*
*[Upgrade](#perform-an-upgrade) endpoint.*

Upgrades system packages.  If an update is requested while a print is
in progress then this request will return an error.

```{.http .apirequest title="HTTP Request"}
POST /machine/update/system
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.update.system",
    "id": 4564
}
```

```{.text .apiresponse title="Response"}
"ok"
```


