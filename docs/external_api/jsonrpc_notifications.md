# JSON-RPC notifications

Persistent connections to Moonraker (websocket, unix socket) will
receive asynchronous via JSON-RPC notifications. A "notification"
in JSON-RPC is a method call without an `id` parameter, for example:

```json
{
    "jsonrpc": "2.0",
    "method": "{notification method name}"
}
```

OR with parameters:

```json
{
    "jsonrpc": "2.0",
    "method": "{notification method name}",
    "params": [true, "pos_param_2", {"obj": "stuff"}]
}
```

To keep consistency Moonraker always sends parameters as positional
arguments.  Specifically, the `params` field will always contain
an array.  This can often lead to a somewhat strange format where
the `params` field contains a single element array, where the element
is an object.

All of the notifications sent by Moonraker are outlined in this document.

## Gcode Responses

Gcode Responses received from Klipper are broadcast to all persistent connections.
All of Klippy's gcode responses are forwarded over the websocket.

```{.text title="Notification Method Name"}
notify_gcode_response
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_gcode_response",
    "params": ["response message"]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                 |
| --- | :----: | --------------------------- |
| 0   | string | The gcode response message. |

///

## Subscription Updates

Klipper object subscription data received as a result of invoking the
[subscribe endpoint](./printer.md#subscribe-to-printer-object-status-updates).

```{.text title="Notification Method Name"}
notify_status_update
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_status_update",
    "params": [
        {
            "gcode_move": {
                "speed": 1500,
            },
            "toolhead": {
                "status": "Ready"
            }
        },
        578243.57824499
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                      |
| --- | :----: | ------------------------------------------------ |
| 0   | object | An object containing changes to subscribed       |
|     |        | Klipper objects.  Each key is the name of a      |^
|     |        | printer object, each value will be an object     |^
|     |        | containing fields that have changed since        |^
|     |        | the last update.                                 |^
| 1   | float  | A timestamp indicating the time the subscription |
|     |        | data was sent.  This time is relative to the     |^
|     |        | monotonic clock used by Klipper.                 |^

///

/// Tip
See Klipper's [status reference](https://www.klipper3d.org/Status_Reference.html)
for details on printer objects and the fields they report.
///

## Klippy Ready

Indicates that the Klippy Host has entered the `ready` state.

```{.text title="Notification Method Name"}
notify_klippy_ready
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_ready"
}
```

## Klippy Shutdown

Indicates that the Klippy Host has entered the `shutdown` state.

```{.text title="Notification Method Name"}
notify_klippy_shutdown
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_shutdown"
}
```

## Klippy Disconnected

Indicates that Moonraker's connection to Klippy has terminated.

```{.text title="Notification Method Name"}
notify_klippy_disconnected
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_klippy_disconnected"
}
```

## File List Changed

Moonraker's `file_manager` will emit notifications when a change
to one of its watched `root` directories is detected.  This includes
changes to files and subdirectories within the root.


```{.text title="Notification Method Name"}
notify_filelist_changed
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_filelist_changed",
    "params": [
        {
            "item": {
                "root": "gcodes",
                "path": "subdir/my_file.gcode",
                "modified": 1676940082.8595376,
                "size": 384096,
                "permissions": "rw"
            },
            "source_item": {
                "path": "testdir/my_file.gcode",
                "root": "gcodes"
            },
            "action": "move_file"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                           |
| --- | :----: | ----------------------------------------------------- |
| 0   | object | A [Changed Item Info](#fileinfo-changed-spec) object. |

| Field         |  Type  | Description                                     |
| ------------- | :----: | ----------------------------------------------- |
| `action`      | string | The [action](#filelist-changed-action-desc)     |
|               |        | that caused the notification.                   |^
| `item`        | object | The `destination item` affected by the change.  |
|               |        | #dest-item-info-spec                            |+
| `source_item` | object | The `source item` affected by the change.  Only |
|               |        | present for `move_file` and `move_dir` actions. |^
|               |        | #source-item-info-spec                          |+
{ #fileinfo-changed-spec } Changed Item Info

| Field         |  Type  | Description                                    |
| ------------- | :----: | ---------------------------------------------- |
| `path`        | string | The path of the destination item relative to   |
|               |        | the root directory.                            |^
| `root`        | string | The root node of the destination item.         |
| `modified`    | float  | The last modified date in Unix Time (seconds). |
| `size`        |  int   | The size of the destination item.              |
| `permissions` | string | Permissions available on the changed item      |
|               |        | (if applicable).                               |^
{ #dest-item-info-spec } Destination Item Info

| Field  |  Type  | Description                             |
| ------ | :----: | --------------------------------------- |
| `path` | string | The path of the source item relative to |
|        |        | the root directory.                     |^
| `root` | string | The root node of the source item.       |
{ #source-item-info-spec } Source Item Info

| Action        | Description                                              |
| ------------- | -------------------------------------------------------- |
| `create_file` | A file has been created within the watched root.         |
| `create_dir`  | A subdirectory has been created within the watched root. |
| `delete_file` | A file has been deleted within the watched root.         |
| `delete_dir`  | A subdirectory has been deleted within the watched root. |
| `move_file`   | A file in a watched root has been moved.                 |
| `move_dir`    | A subdirectory in a watched root has been moved.         |
| `modify_file` | A file in a watched root has been modified.              |
| `root_update` | A root folder's location on disk has changed.            |
{ #filelist-changed-action-desc } Filelist Changed Action

///

/// tip
Notifications are bundled where applicable.  For example, when a
directory containing children is deleted a single `delete_dir`
notification is pushed.  Likewise, when a directory is moved or copied,
a single `move_dir` or `create_dir` notification is pushed.  Children
that are moved, copied, or deleted as a result of a parent's action will
not receive individual notifications.
///

## Update Manager Response

While the `update_manager` is in the process of updating one or more
registered software items, it will emit notifications containing information
about the current status of the update.

```{.text title="Notification Method Name"}
notify_update_response
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_update_response",
    "params": [
        {
            "application": "{app_name}",
            "proc_id": 446461,
            "message": "Update Response Message",
            "complete": false
        }
    ]
}
```
/// api-notification-spec
    open: True

| Pos |  Type  | Description                                   |
| --- | :----: | --------------------------------------------- |
| 0   | object | An update manager notification status object. |

| Field         |  Type   | Description                                     |
| ------------- | :-----: | ----------------------------------------------- |
| `application` | string  | The name of the software currently updating.    |
| `proc_id`     |   int   | A unique ID associated with the current update. |
| `message`     | message | A message containing status and/or information  |
|               |         | about the current update.                       |^
| `complete`    |  bool   | When set to `true` it indicates that the update |
|               |         | has finished and this will be the last status   |^
|               |         | response notification sent for this update.     |^

///

## Update Manager Refreshed

After the update manager has performed a refresh of the
registered software update state it will send a notification
to all connections containing the complete current status.

```{.text title="Notification Method Name"}
notify_update_refreshed
```

/// collapse-code
```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_update_refreshed",
    "params": [
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
    ]
}
```
///

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                     |
| --- | :----: | --------------------------------------------------------------- |
| 0   | object | An [Update Status Info](./update_manager.md#update-status-spec) |
|     |        | object.                                                         |^

///

## CPU Throttled

If the system supports CPU monitoring via `vcgencmd` Moonraker will emit
notifications when the CPU's throttled state changes.

```{.text title="Notification Method Name"}
notify_cpu_throttled
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_cpu_throttled",
    "params": [
        {
            "bits": 0,
            "flags": []
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                            |
| --- | :----: | ------------------------------------------------------ |
| 0   | object | A [CPU Throttled State](#throttled-state-spec) object. |

| Field   |   Type   | Description                                                 |
| ------- | :------: | ----------------------------------------------------------- |
| `bits`  |   int    | The current throttled state as an integer.  A bitwise AND   |
|         |          | can be performed against this value to generate custom flag |^
|         |          | descriptions.                                               |^
| `flags` | [string] | A list of `Throttled Flags` describing the current state.   |
|         |          | #throttled-flags-desc                                       |+
{ #throttled-state-spec } Throttled State

| Flag                           | Bit Offset |
| ------------------------------ | :--------: |
| Under-Voltage Detected         |  `1 << 0`  |
| Frequency Capped               |  `1 << 1`  |
| Currently Throttled            |  `1 << 2`  |
| Temperature Limit Active       |  `1 << 3`  |
| Previously Under-Volted        | `1 << 16`  |
| Previously Frequency Capped    | `1 << 17`  |
| Previously Throttled           | `1 << 18`  |
| Previously Temperature Limited | `1 << 16`  |
{ #throttled-flags-desc } Throttled Flags
///

/// note
It is possible for clients to receive this notification multiple times
if the system repeatedly transitions between an active and inactive throttled
condition.
///

## Moonraker Process Statistic Update

Moonraker performs live monitoring of host machine data and periodically
emits a notification with the most recent statistics.

```{.text title="Notification Method Name"}
notify_proc_stat_update
```

/// collapse-code
```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_proc_stat_update",
    "params": [
        {
            "moonraker_stats": {
                "time": 1615837812.0894408,
                "cpu_usage": 1.99,
                "memory": 23636,
                "mem_units": "kB"
            },
            "cpu_temp": 44.008,
            "network": {
                "lo": {
                    "rx_bytes": 114555457,
                    "tx_bytes": 114555457,
                    "bandwidth": 2911.49
                },
                "wlan0": {
                    "rx_bytes": 48773134,
                    "tx_bytes": 115035939,
                    "bandwidth": 3458.77
                }
            },
            "system_cpu_usage": {
                "cpu": 2.53,
                "cpu0": 3.03,
                "cpu1": 5.1,
                "cpu2": 1.02,
                "cpu3": 1
            },
            "websocket_connections": 2
        }
    ]
}
```
///

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                    |
| --- | :----: | -------------------------------------------------------------- |
| 0   | object | A [proc stats response](./machine.md#proc-stats-response-spec) |
|     |        | object.  The `throttled_state` and `system_uptime` fields are  |^
|     |        | omitted from the notification.                                 |^

///

## History Changed

When Moonraker's `[history]` component detects a new or completed
job a notification will be emitted.

```{.text title="Notification Method Name"}
notify_history_changed
```

/// collapse-code
```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_history_changed",
    "params": [
        {
            "action": "added",
            "job": {
                "end_time": null,
                "filament_used": 20.09796999999998,
                "filename": "calicat_0.3mm_PLA_MK3S_33m.gcode",
                "metadata": {
                    "size": 538254,
                    "modified": 1646770808,
                    "uuid": "4022d6bd-e5f5-45d7-84af-f38bcc57a5d3",
                    "file processors": [],
                    "slicer": "PrusaSlicer",
                    "slicer_version": "2.4.0+linux-x64-GTK3",
                    "gcode_start_byte": 51238,
                    "gcode_end_byte": 528796,
                    "object_height": 34.4,
                    "estimated_time": 1954,
                    "nozzle_diameter": 0.4,
                    "layer_height": 0.3,
                    "first_layer_height": 0.2,
                    "first_layer_extr_temp": 225,
                    "first_layer_bed_temp": 60,
                    "filament_name": "Fusion PLA Carbon Rod Black",
                    "filament_type": "PLA",
                    "filament_total": 1754.96,
                    "filament_weight_total": 5.23,
                    "thumbnails": [
                        {
                            "width": 32,
                            "height": 24,
                            "size": 1829,
                            "relative_path": ".thumbs/calicat_0.3mm_PLA_MK3S_33m-32x32.png"
                        },
                        {
                            "width": 400,
                            "height": 300,
                            "size": 36586,
                            "relative_path": ".thumbs/calicat_0.3mm_PLA_MK3S_33m-400x300.png"
                        }
                    ]
                },
                "print_duration": 0.911540990229696,
                "status": "in_progress",
                "start_time": 1738671939.433274,
                "total_duration": 0.911540990229696,
                "auxiliary_data": [
                    {
                        "provider": "sensor hist_test",
                        "name": "power_consumption",
                        "value": 0,
                        "description": "Printer Power Consumption",
                        "units": "kWh"
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "max_current",
                        "value": 0,
                        "description": "Maximum current draw",
                        "units": "A"
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "min_current",
                        "value": 0,
                        "description": "Maximum current draw",
                        "units": "A"
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "avg_current",
                        "value": 0,
                        "description": "Maximum current draw",
                        "units": "A"
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "status",
                        "value": null,
                        "description": "Power Switch State",
                        "units": null
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "id",
                        "value": [],
                        "description": "Test ID",
                        "units": null
                    },
                    {
                        "provider": "sensor hist_test",
                        "name": "filament",
                        "value": 0,
                        "description": "filament tracker",
                        "units": "mm"
                    },
                    {
                        "provider": "spoolman",
                        "name": "spool_ids",
                        "value": [
                            1
                        ],
                        "description": "Spool IDs used",
                        "units": null
                    }
                ],
                "user": "testuser",
                "exists": true,
                "job_id": "000027"
            }
        }
    ]
}
```
///

/// api-notification-spec
    open: True

| Pos |  Type  | Description |
| --- | :----: | ----------- |
| 0   | object | A [Job History Notification](#job-hist-notify-spec) object. |

| Field    |  Type  | Description                                                |
| -------- | :----: | ---------------------------------------------------------- |
| `action` | string | The `action` that triggered the notification.              |
|          |        | #job-hist-notify-action                                    |+
| `job`    | string | A [Job History Entry](./history.md#job-history-entry-spec) |
|          |        | object.                                                    |^
{ #job-hist-notify-spec } Job History Notification

| Action     | Description                                  |
| ---------- | -------------------------------------------- |
| `added`    | A new job was added to the job history.      |
| `finished` | A running job was completed.  This includes  |
|            | jobs successfully completed, cancelled jobs, |^
|            | and jobs that encountered an error.          |^
{ #job-hist-notify-action } Job History Notify Action

///

## Authorized User Created

Moonraker's `[authorization]` component will emit a notification when
a new user entry has been created.

```{.text title="Notification Method Name"}
notify_user_created
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_user_created",
    "params": [
        {
            "username": "Eric"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                             |
| --- | :----: | ------------------------------------------------------- |
| 0   | object | An [Auth Notification](#auth-notification-spec) object. |

| Field      |  Type  | Description                                    |
| ---------- | :----: | ---------------------------------------------- |
| `username` | string | The username of the user entry associated with |
|            |        | the notification.                              |^
{ #auth-notification-spec } Auth Notification

///

## Authorized User Deleted

Moonraker's `[authorization]` component will emit a notification when
an existing user entry has been deleted.

```{.text title="Notification Method Name"}
notify_user_deleted
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_user_deleted",
    "params": [
        {
            "username": "Eric"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                             |
| --- | :----: | ------------------------------------------------------- |
| 0   | object | An [Auth Notification](#auth-notification-spec) object. |

///

## Authorized User Logged Out

Moonraker's `[authorization]` component will emit a notification when
a user has logged out.

```{.text title="Notification Method Name"}
notify_user_logged_out
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_user_logged_out",
    "params": [
        {
            "username": "Eric"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                             |
| --- | :----: | ------------------------------------------------------- |
| 0   | object | An [Auth Notification](#auth-notification-spec) object. |

///


## Service State Changed

If Moonraker's `[machine]` component is configured with its `systemd`
integration enabled it will monitor the state of various systemd services.
When a change is detected in service state Moonraker will emit a
notification.

```{.text title="Notification Method Name"}
notify_service_state_changed
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_service_state_changed",
    "params": [
        {
            "klipper": {
                "active_state": "inactive",
                "sub_state": "dead"
            }
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                |
| --- | :----: | ---------------------------------------------------------- |
| 0   | object | A [Service State Notification](#service-state-notify-spec) |
|     |        | object.                                                    |^

| Field          |  Type  | Description                                         |
| -------------- | :----: | --------------------------------------------------- |
| *service_name* | object | A [Unit Status](#unit-status-spec) object.  The key |
|                |        | for this field is the service name.                 |^
|                |        | #unit-status-spec                                   |+
{ #service-state-notify-spec } Service State Notification

| Field          |  Type  | Description                                                      |
| -------------- | :----: | ---------------------------------------------------------------- |
| `active_state` | string | The new `ACTIVE` state reported by the provider for the service. |
| `sub_state`    | string | The new `SUB` state reported by the provider for the service.    |
{ #unit-status-spec } Unit Status

///

## Job Queue Changed

Moonraker's `[job_queue]` component emits a notification when the job
queue state changes and when the queue is modified.

```{.text title="Notification Method Name"}
notify_job_queue_changed
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_job_queue_changed",
    "params": [
        {
            "action": "state_changed",
            "updated_queue": null,
            "queue_state": "paused"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                              |
| --- | :----: | -------------------------------------------------------- |
| 0   | object | A [Job Queue Notification](#job-queue-notification-spec) |
|     |        | object.                                                  |^

| Field           |       Type       | Description                                 |
| --------------- | :--------------: | ------------------------------------------- |
| `action`        |      string      | The [action](#job-queue-notify-action) that |
|                 |                  | triggered the notification.                 |^
| `queue_state`   |      string      | The current queue                           |
|                 |                  | [state](./job_queue.md#queue-state-desc).   |^
| `updated_queue` | [object] \| null | An array of `Queued Job` objects reflecting |
|                 |                  | the updated queue.  Will be `null` if the   |^
|                 |                  | queue has not changed.                      |^
|                 |                  | #queued-job-spec                            |+
{ #job-queue-notification-spec }

| Field           |  Type  | Description                                             |
| --------------- | :----: | ------------------------------------------------------- |
| `filename`      | string | The name of the gcode file queued.                      |
| `job_id`        | string | A unique ID assigned to the queued job.                 |
| `time_added`    | float  | The time (in Unix Time) the job was added to the queue. |
| `time_in_queue` | float  | The cumulative amount of time, in seconds, the job has  |
|                 |        | been pending in the queue.                              |^
{ #queued-job-spec } Queued Job

| Action          | Description                                           |
| --------------- | ----------------------------------------------------- |
| `state_changed` | The internal job queue state has changed.             |
| `jobs_added`    | One or more jobs have been added to the queue.        |
| `jobs_removed`  | One or more jobs have been removed from the queue.    |
| `job_loaded`    | A job has been popped from the queue and successfully |
|                 | started.                                              |^
{ #job-queue-notify-action } Job Queue Notification Action

///


## Button Event

Moonraker `[button]` component supports optional notifications
sent when a button is pressed and/or released.

```{.text title="Notification Method Name"}
notify_button_event
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_button_event",
    "params": [
        {
            "name": "my_button",
            "type": "gpio",
            "event": {
                "elapsed_time": 0.09323832602240145,
                "received_time": 698614.214597004,
                "render_time": 698614.214728513,
                "pressed": false
            },
            "aux": null
        }
    ]
}
```
/// api-notification-spec
    open: True

| Pos |  Type  | Description                             |
| --- | :----: | --------------------------------------- |
| 0   | object | A [Button Notification](#button-notify-spec) object. |

| Field   |  Type  | Description                                   |
| ------- | :----: | --------------------------------------------- |
| `name`  | string | The name of the button sending the event.     |
| `type`  | string | The configured type of the button.  Currently |
|         |        | only the `gpio` type is supported.            |^
| `event` | object | A `Button Event` object.                      |
|         |        | #button-event-spec                            |+
| `aux`   |  any   | Auxiliary data attached to the event.  Can be |
|         |        | any JSON encodable type.  If no aux data is   |^
|         |        | sent with the event the value will be `null`  |^
{#button-notify-spec} Button Notification

| Field           | Type  | Description                                 |
| --------------- | :---: | ------------------------------------------- |
| `elapsed_time`  | float | The time elapsed (in seconds) since the     |
|                 |       | last detected button event.                 |^
| `received_time` | float | The time the event was detected relative    |
|                 |       | to Moonraker's monotonic clock.             |^
| `render_time`   | float | The time the button's template started      |
|                 |       | rendering relative to Moonraker's monotonic |^
|                 |       | clock.                                      |^
| `pressed`       | bool  | Set to `true` if the button is pressed.     |
{ #button-event-spec } Button Event
///

## Announcement update event

Moonraker's `[announcements]` component will emit a notification
when announcement entries are added or removed.

```{.text title="Notification Method Name"}
notify_announcement_update
```

/// collapse-code
```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_update",
    "params": [
        {
            "entries": [
                {
                    "entry_id": "arksine/moonlight/issue/3",
                    "url": "https://github.com/Arksine/moonlight/issues/3",
                    "title": "Test announcement 3",
                    "description": "Test Description [with a link](https://moonraker.readthedocs.io).",
                    "priority": "normal",
                    "date": 1647459219,
                    "dismissed": false,
                    "date_dismissed": null,
                    "dismiss_wake": null,
                    "source": "moonlight",
                    "feed": "moonlight"
                },
                {
                    "entry_id": "arksine/moonlight/issue/2",
                    "url": "https://github.com/Arksine/moonlight/issues/2",
                    "title": "Announcement Test Two",
                    "description": "This is a high priority announcement. This line is included in the description.",
                    "priority": "high",
                    "date": 1646855579,
                    "dismissed": false,
                    "date_dismissed": null,
                    "dismiss_wake": null,
                    "source": "moonlight",
                    "feed": "moonlight"
                }
                {
                    "entry_id": "arksine/moonraker/issue/349",
                    "url": "https://github.com/Arksine/moonraker/issues/349",
                    "title": "PolicyKit warnings; unable to manage services, restart system, or update packages",
                    "description": "This announcement is an effort to get ahead of a coming change that will certainly result in issues.  PR #346  has been merged, and with it are some changes to Moonraker's default behavior.",
                    "priority": "normal",
                    "date": 1643392406,
                    "dismissed": false,
                    "source": "moonlight",
                    "feed": "Moonraker"
                }
            ]
        }
    ]
}
```
///

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                               |
| --- | :----: | --------------------------------------------------------- |
| 0   | object | An [Announcement Notification](#announcement-notify-spec) |
|     |        | object.                                                   |^

| Field     |   Type   | Description                                                       |
| --------- | :------: | ----------------------------------------------------------------- |
| `entries` | [object] | An array of                                                       |
|           |          | [Announcement Entry](./announcements.md#announcement-entry-spec ) |^
|           |          | objects.                                                          |^
{ #announcement-notify-spec } Announcement Notification

///

## Announcement dismissed event

Moonraker's `[announcements]` component will emit a notification
when an announcement is dismissed.

```{.text title="Notification Method Name"}
notify_announcement_dismissed
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_dismissed",
    "params": [
        {
            "entry_id": "arksine/moonlight/issue/3"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                                   |
| --- | :----: | ----------------------------------------------------------------------------- |
| 0   | object | An [Announcement Dismissed Notification](#announcement-dismissed-notify-desc) |
|     |        | object.                                                                       |^

| Field      |  Type  | Description                                        |
| ---------- | :----: | -------------------------------------------------- |
| `entry_id` | string | The unique entry ID of the dismissed announcement. |
{ #announcement-dismissed-notify-desc} Announcement Dismissed Notification

///

## Announcement wake event

Moonraker's `[announcements]` component will emit a notification
when an announcement "wakes" from a dismissed state.

```{.text title="Notification Method Name"}
notify_announcement_wake
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_announcement_wake",
    "params": [
        {
            "entry_id": "arksine/moonlight/issue/1"
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                                   |
| --- | :----: | ----------------------------------------------------------------------------- |
| 0   | object | An [Announcement Wake Notification](#announcement-wake-notify-desc) |
|     |        | object.                                                                       |^

| Field      |  Type  | Description                                     |
| ---------- | :----: | ----------------------------------------------- |
| `entry_id` | string | The unique entry ID of the awoken announcement. |
|            |        | The announcement's `dismissed` field will       |^
|            |        | be set to `false`.                              |^
{ #announcement-wake-notify-desc} Announcement Wake Notification

///

## Sudo alert event

At times Moonraker may require sudo permission to perform a specific task.
This is rare and generally involves an upgrade (ie: Moonraker's systemd
service file needs to be modified).  When Moonraker runs a command that
requires sudo permission an alert will be sent via notification.

Pending sudo requests that cannot be executed until the user
[sets their sudo password](./machine.md#set-sudo-password) will
also emit a notification.

```{.text title="Notification Method Name"}
notify_sudo_alert
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_sudo_alert",
    "params": [
        {
            "sudo_requested": true,
            "sudo_messages": [
                "Sudo password required to update Moonraker's systemd service."
            ]
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                 |
| --- | :----: | ----------------------------------------------------------- |
| 0   | object | A [Sudo Alert Notification](#sudo-alert-notify-spec) object |

| Pos              |   Type   | Description                                |
| ---------------- | :------: | ------------------------------------------ |
| `sudo_requested` |   bool   | When `true` there are pending tasks that   |
|                  |          | require super user permission.             |^
| `sudo_messages`  | [string] | An array of messages describing the action |
|                  |          | or actions requiring sudo permission.      |^
{ #sudo-alert-notify-spec} Sudo Alert Notification

//// note
Each `sudo message` can fall into one of the following categories:

- An explanation of a pending sudo request.
- A response from a task that successfully ran with sudo permissions.
- A response from a task that returned an error.
////

///

## Webcams changed event

Moonraker's `[webcam]` component will send a notification when
a webcam is added, removed, or updated.

```{.text title="Notification Method Name"}
notify_webcams_changed
```

/// collapse-code
```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_webcams_changed",
    "params": [
        {
            "webcams": [
                {
                    "name": "tc2",
                    "location": "printer",
                    "service": "mjpegstreamer",
                    "enabled": true,
                    "icon": "mdiWebcam",
                    "target_fps": 15,
                    "target_fps_idle": 5,
                    "stream_url": "http://printer.lan/webcam?action=stream",
                    "snapshot_url": "http://printer.lan/webcam?action=snapshot",
                    "flip_horizontal": false,
                    "flip_vertical": false,
                    "rotation": 0,
                    "aspect_ratio": "4:3",
                    "extra_data": {},
                    "source": "database"
                },
                {
                    "name": "TestCam",
                    "location": "printer",
                    "service": "mjpegstreamer",
                    "enabled": true,
                    "icon": "mdiWebcam",
                    "target_fps": 15,
                    "target_fps_idle": 5,
                    "stream_url": "/webcam/?action=stream",
                    "snapshot_url": "/webcam/?action=snapshot",
                    "flip_horizontal": false,
                    "flip_vertical": false,
                    "rotation": 0,
                    "aspect_ratio": "4:3",
                    "extra_data": {},
                    "source": "database"
                }
            ]
        }
    ]
}
```
///

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                          |
| --- | :----: | ---------------------------------------------------- |
| 0   | object | A [Webcam Notification](#webcam-notify-spec) object. |

| Field     |   Type   | Description                                                |
| --------- | :------: | ---------------------------------------------------------- |
| `webcams` | [object] | An array of [Webcam Entry](./webcams.md#webcam-entry-spec) |
|           |          | objects.                                                   |^
{ #webcam-notify-spec } Webcam Notification

///

## Spoolman active spool ID changed

Moonraker's `[spoolman]` component will emit a notification
when the current active spool ID has changed.

```{.text title="Notification Method Name"}
notify_active_spool_set
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_active_spool_set",
    "params": [
        {
            "spool_id": 1
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                            |
| --- | :----: | ---------------------------------------------------------------------- |
| 0   | object | An [Active Spool Set Notification](#spoolman-active-spool-notify-spec) |
|     |        | object.                                                                |^

| Field      |    Type     | Description                              |
| ---------- | :---------: | ---------------------------------------- |
| `spool_id` | int \| null | The spool ID of the new active spool.  A |
|            |             | value of `null` indicates that no active |^
|            |             | spool is set and tracking is disabled.   |^
{ #spoolman-active-spool-notify-spec } Active Spool Set Notification

///

## Spoolman Status Changed

Moonraker's `[spoolman]` component holds a persistent websocket
connection to the server.  This allow Moonraker to remain aware
of the server's status.  Moonraker will emit a notification when
the connection status to Spoolman changes.

```{.text title="Notification Method Name"}
notify_spoolman_status_changed
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_spoolman_status_changed",
    "params": [
        {
            "spoolman_connected": false
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                     |
| --- | :----: | --------------------------------------------------------------- |
| 0   | object | A [Spoolman Status Notification](#spoolman-status-notify-spec ) |
|     |        | object.                                                         |^

| Field                | Type | Description                                   |
| -------------------- | :--: | --------------------------------------------- |
| `spoolman_connected` | bool | A value of `true` indicates that Moonraker is |
|                      |      | currently connected to the Spoolman Server.   |^
{ #spoolman-status-notify-spec } Spoolman Status Notification

///

## Agent Events

Moonraker has limited support for third party extensions through
client connections that identify themselves as
[agents](./extensions.md#agent-specific-endpoints).
Agents are granted access to additional endpoints that allow
them to extend Moonraker's functionality.  One such method
allows agents to send events that are broadcast to all of Moonraker's
connected clients.  Moonraker proxies agent events through a
notification.

```{.text title="Notification Method Name"}
notify_agent_event
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_agent_event",
    "params": [
        {
            "agent": "moonagent",
            "event": "connected",
            "data": {
                "name": "moonagent",
                "version": "0.0.1",
                "type": "agent",
                "url": "https://github.com/arksine/moontest"
            }
        }
    ]
}
```

/// api-notification-spec
    open: True

| Pos |  Type  | Description                                             |
| --- | :----: | ------------------------------------------------------- |
| 0   | object | An [Agent Event Notification](#agent-event-notify-spec) |
|     |        | object.                                                 |^

| Field   |   Type   | Description                                              |
| ------- | :------: | -------------------------------------------------------- |
| `agent` | `string` | The name of the agent sending the event.                 |
| `event` | `string` | The name of the event.  Can be any name                  |
|         |          | other than those [reserved](#reserved-agent-event-desc)  |^
|         |          | by Moonraker.                                            |^
| `data`  |   any    | Additional data sent with the event.  Can be any JSON    |
|         |          | encodable value.  If the event does not attach data this |^
|         |          | field will be omitted.                                   |^
{ #agent-event-notify-spec } Agent Event Notification

| Event          | Description                                           |
| -------------- | ----------------------------------------------------- |
| `connected`    | An agent has connected to Moonraker.  The `data`      |
|                | field will contain an                                 |^
|                | [Agent Info](./extensions.md#agent-info-spec) object. |^
| `disconnected` | An agent has disconnected from Moonraker.  The `data` |
|                | field is omitted for this event.                      |^
{ #reserved-agent-event-desc} Reserved Agent Events

///

/// note
The agent that sends an event will not receive a notification for that
event.  It will however receive events from other agents.
///

## Sensor Events

Moonraker's `[sensor]` component will take periodic measurements
of configured sensors.  When one or more new measurement is received
a notification will be emitted containing the new measurement data.

```{.text title="Notification Method Name"}
notify_sensor_update
```

```{.json .apiresponse title="Example Notification"}
{
    "jsonrpc": "2.0",
    "method": "notify_sensor_update",
    "params": [
        {
            "sensor1": {
                "humidity": 28.9,
                "temperature": 22.4
            }
        }
    ]
}
```
/// api-notification-spec
    open: True

| Pos |  Type  | Description                                                |
| --- | :----: | ---------------------------------------------------------- |
| 0   | object | A [Sensor Notification](#sensor-notification-spec) object. |

| Field         |  Type  | Description                                      |
| ------------- | :----: | ------------------------------------------------ |
| *sensor_name* | object | The object may contain multiple `sensors`, where |
|               |        | each key is the name of a sensor and the value   |^
|               |        | is a `Sensor Values` object.                     |^
|               |        | #sensor-values-spec                              |+
{ #sensor-notification-spec } Sensor Notification

| Field        | Type | Description                                     |
| ------------ | :--: | ----------------------------------------------- |
| *value_name* | any  | The object may contain multiple `values`, where |
|              |      | each key is the name of a parameter tracked     |^
|              |      | by the sensor, and the value is the most        |^
|              |      | recent reported measurement.                    |^
{ #sensor-values-spec } Sensor Values

///
