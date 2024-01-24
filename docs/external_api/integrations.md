# Third Party Integrations

## Apprise Notifier

Moonraker supports configurable push notifications using the
[apprise](https://github.com/caronc/apprise) library.  The
endpoints in this section may be used to manage/view registered
notifiers.

The following endpoints are available when at least one
`[notifier <name>]` section has been configured in `moonraker.conf`.

### List Notifiers

```{.http .apirequest title="HTTP Request"}
GET /server/notifiers/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.notifiers.list",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "notifiers": [
        {
            "name": "print_start",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "started"
            ],
            "body": "Your printer started printing '{event_args[1].filename}'",
            "title": null,
            "attach": null
        },
        {
            "name": "print_complete",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "complete"
            ],
            "body": "Your printer completed printing '{event_args[1].filename}",
            "title": null,
            "attach": "http://192.168.1.100/webcam/?action=snapshot"
        },
        {
            "name": "print_error",
            "url": "tgram://{bottoken}/{ChatID}",
            "events": [
                "error"
            ],
            "body": "{event_args[1].message}",
            "title": null,
            "attach": "http://192.168.1.100/webcam/?action=snapshot"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field       |   Type   | Description                                          |
| ----------- | :------: | ---------------------------------------------------- |
| `notifiers` | [object] | An array of [Notifier Status](#notifier-status-spec) |
|             |          | objects.                                             |^

| Field    |      Type      | Description                                          |
| -------- | :------------: | ---------------------------------------------------- |
| `name`   |     string     | The configured name of the notifier.                 |
| `url`    |     string     | The notifier's destination url.                      |
| `events` |    [string]    | An array that contains one or more                   |
|          |                | [events](#notifier-event-desc) which will trigger    |^
|          |                | the push notification.                               |^
| `body`   | string \| null | The content to send in the body of the notification. |
|          |                | Will be `null` if no body is configured.             |^
| `title`  | string \| null | The title of the notification. Will be `null` if no  |
|          |                | title is configured                                  |^
| `attach` | string \| null | One or more attachments added to the notification.   |
|          |                | Multiple attachments are separated by newlines. Will |^
|          |                | be `null` if no attachment is configured.            |^
{ #notifier-status-spec } Notifier Status

| Event       | Description                                |
| ----------- | ------------------------------------------ |
| `standby`   | The printer has entered its standby state. |
| `started`   | A print job has started.                   |
| `paused`    | A print job has paused.                    |
| `resumed`   | A print job has resumed.                   |
| `complete`  | A print job has successfully finished.     |
| `error`     | A print job exited with an error.          |
| `cancelled` | A print job was cancelled by the user.     |
{ #notifier-event-desc } Available Notifier Events

//// note
The `url`, `body`, `title`, and `attach` parameters may contain Jinja 2
templates.  All templates are evaluated before the notification is
pushed.
////

///

### Test a notifier (debug)

Forces a registered notifier to push a notification.

/// note
This endpoint is only available when Moonraker's debug
features are enabled and should not be implemented
in production code
///


```{.http .apirequest title="HTTP Request"}
POST /debug/notifiers/test
Content-Type: application/json

{
    "name": "notifier_name"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "debug.notifiers.test",
    "params": {
        "name": "notifier_name"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default      | Description                       |
| ------ | :----: | ------------ | --------------------------------- |
| `name` | string | **REQUIRED** | The name of the notifier to test. |

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "status": "success",
    "stats": {
        "print_duration": 0.0,
        "total_duration": 0.0,
        "filament_used": 0.0,
        "filename": "notifier_test.gcode",
        "state": "standby",
        "message": "",
        "info": {
            "total_layer": null,
            "current_layer": null
        }
    }
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                               |
| -------- | :----: | ----------------------------------------- |
| `status` | string | The status of the test result.  Currently |
|          |        | will always be `success`.                 |^
| `stats`  | object | A [Print Stats](#print-stats-spec) object.|                                          |

| Field            |  Type  | Description                                        |
| ---------------- | :----: | -------------------------------------------------- |
| `print_duration` | float  | Time spent printing the current job in seconds.    |
|                  |        | Does not include time paused.                      |^
| `total_duration` | float  | Total job duration in seconds.                     |
| `filament_used`  | float  | Amount of filament used for the current job in mm. |
| `filename`       | string | File path of the current job, relative to the      |
|                  |        | `gcodes` root.                                     |^
| `state`          | string | The current job [state](#print-stats-state-desc).  |
| `message`        | string | A status message set by Klipper.  Will be an empty |
|                  |        | string if no message is set.                       |^
| `info`           | object | A `Print Stats Supplemental Info` object.          |
|                  |        | #print-stats-supplemental-info-spec                |+
{#print-stats-spec} Print Stats

| Field           |    Type     | Description                                  |
| --------------- | :---------: | -------------------------------------------- |
| `total_layer`   | int \| null | The total layer count of the current         |
|                 |             | job.  Will be null if the total layer        |^
|                 |             | count is not set.                            |^
| `current_layer` | int \| null | The index of the layer the job is currently  |
|                 |             | printing.  Will be null of the current layer |^
|                 |             | is not set.                                  |^
{#print-stats-supplemental-info-spec} Print Stats Supplemental Info

| State       | Description                                    |
| ----------- | ---------------------------------------------- |
| `standby`   | The printer is standing by for a job to begin. |
| `printing`  | A job is currently printing.                   |
| `paused`    | A print job has paused.                        |
| `complete`  | A print job has successfully finished.         |
| `error`     | A print job exited with an error.              |
| `cancelled` | A print job was cancelled by the user.         |
{ #print-stats-state-desc } Print Stats State

///

## Spoolman

[Spoolman](https://github.com/Donkie/Spoolman) is a spool
tracking web service that can manage spool data across
multiple printers.  Moonraker has support for updating and retrieving
spool data through its `[spoolman]` integration.

The following endpoints are available when the `[spoolman]` component
has been configured.

### Get Spoolman Status

Returns the current status of the spoolman module.

```{.http .apirequest title="HTTP Request"}
GET /server/spoolman/status
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.status",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "spoolman_connected": false,
    "pending_reports": [
        {
            "spool_id": 1,
            "filament_used": 10
        }
    ],
    "spool_id": 2
}
```
///

/// api-response-spec
    open: True

| Field                |    Type     | Description                                    |
| -------------------- | :---------: | ---------------------------------------------- |
| `spoolman_connected` |    bool     | Indicates that Moonraker has an established    |
|                      |             | websocket connection to Spoolman.              |^
| `pending_reports`    |  [object]   | An array of `Pending Spoolman Report` objects. |
|                      |             | A pending report is a report that has not yet  |^
|                      |             | been sent to Spoolman.  This may be because    |^
|                      |             | Spoolman is not available or because the       |^
|                      |             | current batch of reports are waiting for the   |^
|                      |             | internal report timer to schedule them.        |^
|                      |             | #spoolman-report-spec                          |+
| `spool_id`           | int \| null | The ID of the currently tracked spool. A value |
|                      |             | of `null` indicates that no spool ID is set    |^
|                      |             | and tracking is disabled.                      |^

| Field           | Type  | Description                                   |
| --------------- | :---: | --------------------------------------------- |
| `spool_id`      |  int  | The ID of the spool with pending report data. |
| `filament_used` | float | The amount of used filament to report in mm.  |
{ #spoolman-report-spec } Pending Reports

///

### Set active spool

Set the active ID of the spool to track filament usage and report
to Spoolman.

```{.http .apirequest title="HTTP Request"}
POST /server/spoolman/spool_id
Content-Type: application/json

{
    "spool_id": 1
}
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.post_spool_id",
    "params": {
        "spool_id": 1
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name       |    Type     | Default | Description                        |
| ---------- | :---------: | ------- | ---------------------------------- |
| `spool_id` | int \| null | null    | The new active spool ID. A `null`  |
|            |             |         | value will unset the previous      |^
|            |             |         | active spool and disable tracking. |^

///

```{.json .apiresponse title="Example Response"}
{
    "spool_id": 1
}
```

/// api-response-spec
    open: True

| Field      |    Type     | Description                                    |
| ---------- | :---------: | ---------------------------------------------- |
| `spool_id` | int \| null | The ID of the currently tracked spool. A value |
|            |             | of `null` indicates that no spool ID is set    |^
|            |             | and tracking is disabled.                      |^

///

### Get active spool
Retrieve the ID of the spool to which Moonraker reports usage for Spoolman.

```{.http .apirequest title="HTTP Request"}
GET /server/spoolman/spool_id
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.get_spool_id",
    "id": 4654
}
```

```{.json .apiresponse title="Example Response"}
{
    "spool_id": 1
}
```

/// api-response-spec
    open: True

| Field      |    Type     | Description                                    |
| ---------- | :---------: | ---------------------------------------------- |
| `spool_id` | int \| null | The ID of the currently tracked spool. A value |
|            |             | of `null` indicates that no spool ID is set    |^
|            |             | and tracking is disabled.                      |^

///

### Proxy

Proxy an API request to the Spoolman Server.

See Spoolman's [OpenAPI Description](https://donkie.github.io/Spoolman/) for
detailed information about it's API.

/// Note
The version 2 response has been added to eliminate ambiguity between
Spoolman errors and Moonraker errors.  With version 1 a frontend
is not able to reliably to determine if the error is sourced from
Spoolman or Moonraker.  Version 2 responses will return success
unless Moonraker is the source of the error.

The version 2 response is currently opt-in to avoid breaking
existing implementations, however in the future it will be
required, at which point the version 1 response will be removed.
The version 1 response is now deprecated.
///

```{.http .apirequest title="HTTP Request"}
POST /server/spoolman/proxy
Content-Type: application/json

{
    "request_method": "POST",
    "path": "/v1/spool",
    "query": "a=1&b=4",
    "body": {
        "filament_id": 1
    }
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.spoolman.proxy",
    "params": {
        "use_v2_response": true,
        "request_method": "POST",
        "path": "/v1/spool",
        "query": "a=1&b=4",
        "body": {
            "filament_id": 1
        }
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name              |  Type  | Default      | Description                              |
| ----------------- | :----: | ------------ | ---------------------------------------- |
| `use_v2_response` |  bool  | false        | When set to `true` the request will      |
|                   |        |              | return a version 2 response.             |^
| `request_method`  | string | **REQUIRED** | The HTTP request method of the API       |
|                   |        |              | call to proxy.                           |^
| `path`            | string | **REQUIRED** | The path section of the API endpoint to  |
|                   |        |              | proxy.  It must include the version, ie: |^
|                   |        |              | `/v1/filament`                           |^
| `query`           | string | null         | An optional query string component of    |
|                   |        |              | the URL to proxy.  A `null` value        |^
|                   |        |              | will omit the query string.              |^
| `body`            | object | null         | An optional body containing request      |
|                   |        |              | parameters for the API call.  This       |^
|                   |        |              | should be a JSON encodable object.       |^
|                   |        |              | A `null` value will send an empty body.  |^

///

/// collapse-code
```{.json .apiresponse title="Example Success Response (Version 2)"}
{
    "response": {
        "id": 2,
        "registered": "2023-11-23T12:18:31Z",
        "first_used": "2023-11-22T12:17:56.123000Z",
        "last_used": "2023-11-23T10:17:59.900000Z",
        "filament": {
            "id": 2,
            "registered": "2023-11-23T12:17:44Z",
            "name": "Reactor Red",
            "vendor": {
                "id": 2,
                "registered": "2023-06-26T21:00:42Z",
                "name": "Fusion"
            },
            "material": "PLA",
            "price": 25,
            "density": 1.24,
            "diameter": 1.75,
            "weight": 1000,
            "color_hex": "BD0B0B"
        },
        "remaining_weight": 950,
        "used_weight": 50,
        "remaining_length": 318519.4384459262,
        "used_length": 16764.18097083822,
        "archived": false
    },
    "error": null
}
```
///

/// collapse-code
```{.json .apiresponse title="Example Error Response (Version 2)"}
{
    "response": null,
    "error": {
        "status_code": 404,
        "message": "No spool with ID 3 found."
    }
}
```
///

/// api-response-spec
    open: True

//// Note
Version 1 responses are proxied directly.  See Spoolman's API
documentation for response specifications.  Errors are also
proxied directly.
////

| Field      |      Type      | Description                                 |
| ---------- | :------------: | ------------------------------------------- |
| `response` | object \| null | On success will be an object containing the |
|            |                | response received from Spoolman.  Will be   |^
|            |                | `null` if an error is received.             |^
| `error`    | object \| null | On error will be a `Spoolman Error` object. |
|            |                | Will be `null` on successful requests.      |^
|            |                | #spoolman-error-spec                        |+
{ #version2-success-spec} Version 2 response

| Field         |  Type  | Description                                   |
| ------------- | :----: | --------------------------------------------- |
| `status_code` |  int   | The HTTP status code of the response.         |
| `message`     | string | The error message received with the response. |
{ #spoolman-error-spec} Spoolman Error

///


## OctoPrint API emulation

Supports the minimal API requirements necessary to add compatibility
with the `upload G-Code to OctoPrint` feature present on 3rd party
applications, such as slicers.  Developers of Moonraker applications
*should not* implement these APIs.

These endpoints are available when the `[octoprint_compat]` feature
has been configured in `moonraker.conf`

/// tip
Most slicers now support Moonraker's native upload interface,
reducing the need for these endpoints.
///

/// note
Unlike all other Moonraker responses, OctoPrint responses are
not wrapped in an object with a `result` field.  This section
will not include parameter and response specifications, they
can be found in OctoPrint's API documentation.

In addition, many values in the responses returned by Moonraker
are simply placeholders and have no real meaning with regard
to Moonraker's internal state.
///

### Version information

```{.http .apirequest title="HTTP Request"}
GET /api/version
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "server": "1.5.0",
    "api": "0.1",
    "text": "OctoPrint (Moonraker v0.3.1-12)"
}
```
///

### Server status

```{.http .apirequest title="HTTP Request"}
GET /api/server
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "server": "1.5.0",
    "safemode": "settings"
}
```
///

### Login verification & User information

```{.http .apirequest title="HTTP Request"}
GET /api/login
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "_is_external_client": false,
    "_login_mechanism": "apikey",
    "name": "_api",
    "active": true,
    "user": true,
    "admin": true,
    "apikey": null,
    "permissions": [],
    "groups": ["admins", "users"]
}
```
///

### Get settings

```{.http .apirequest title="HTTP Request"}
GET /api/settings
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "plugins": {
        "UltimakerFormatPackage": {
            "align_inline_thumbnail": false,
            "inline_thumbnail": false,
            "inline_thumbnail_align_value": "left",
            "inline_thumbnail_scale_value": "50",
            "installed": true,
            "installed_version": "0.2.2",
            "scale_inline_thumbnail": false,
            "state_panel_thumbnail": true
        }
    },
    "feature": {
        "sdSupport": false,
        "temperatureGraph": false
    },
    "webcam": {
        "flipH": false,
        "flipV": false,
        "rotate90": false,
        "streamUrl": "/webcam/?action=stream",
        "webcamEnabled": true
    }
}
```
///

/// note
The webcam route in the response is hardcoded to Fluidd/Mainsail
default path. The UFP plugin reports that it is installed so the
Cura-OctoPrint plugin will upload in the preferred UFP format.
///

### OctoPrint File Upload

```{.http .apirequest title="HTTP Request"}
POST /api/files/local
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

Alias for Moonraker's [file upload API](./file_manager.md#file-upload).

### Get Job status

```{.http .apirequest title="HTTP Request"}
GET /api/job
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "job": {
        "file": {"name": null},
        "estimatedPrintTime": null,
        "filament": {"length": null},
        "user": null
    },
    "progress": {
        "completion": null,
        "filepos": null,
        "printTime": null,
        "printTimeLeft": null,
        "printTimeOrigin": null
    },
    "state": "Offline"
}
```
///

### Get Printer status

```{.http .apirequest title="HTTP Request"}
GET /api/printer
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "temperature": {
        "tool0": {
            "actual": 22.25,
            "offset": 0,
            "target": 0
        },
        "bed": {
            "actual": 22.25,
            "offset": 0,
            "target": 0
        }, ...<additional heaters>
    },
    "state": {
        "text": "state",
        "flags": {
            "operational": true,
            "paused": false,
            "printing": false,
            "cancelling": false,
            "pausing": false,
            "error": false,
            "ready": false,
            "closedOrError": false
        }
    }
}
```
///

### Send GCode command

```{.http .apirequest title="HTTP Request"}
POST /api/printer/command
Content-Type: application/json

{
    "commands": ["G28"]
}
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

```{.json .apiresponse title="Example Response"}
{}
```

### List Printer profiles

```{.http .apirequest title="HTTP Request"}
GET /api/printerprofiles
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "profiles": {
        "_default": {
            "id": "_default",
            "name": "Default",
            "color": "default",
            "model": "Default",
            "default": true,
            "current": true,
            "heatedBed": true,
            "heatedChamber": false
        }
    }
}
```
///
