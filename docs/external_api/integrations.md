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

## Klipper Estimator time analysis

Moonraker's `analysis` component uses
[Klipper Estimator](https://github.com/Annex-Engineering/klipper_estimator)
to perform gcode file time analysis.  The endpoints in this section are available
when the `[analysis]` section has been configured in `moonraker.conf`.

### Get Analysis Status

```{.http .apirequest title="HTTP Request"}
GET /server/analysis/status
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.analysis.status",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "estimator_executable": "klipper_estimator_rpi",
    "estimator_ready": true,
    "estimator_version": "v3.7.3",
    "estimator_config_exists": true,
    "using_default_config": false
}
```
///

/// api-response-spec
    open: True

| Field                     |  Type  | Description                                  |
| ------------------------- | :----: | -------------------------------------------- |
| `estimator_executable`    | string | The name of the Klipper Estimator executable |
|                           |        | file.                                        |^
| `estimator_ready`         |  bool  | A value of `true` indicates that the Klipper |
|                           |        | Estimator binary is present and successfully |^
|                           |        | reports its version.                         |^
| `estimator_version`       | string | The version reported by Klipper Estimator.   |
| `estimator_config_exists` |  bool  | A value of `true` indicates that a valid     |
|                           |        | Klipper Estimator config file exists.        |^
| `using_default_config`    |  bool  | Reports `true` when Klipper Estimator is     |
|                           |        | configured to use the default config.        |^

//// note
When Klipper Estimator is first initialized Moonraker downloads the binary
and grants it executable permissions.  A default configuration will be
dumped when Klippy reports `ready`.  The default configuration will not
exist until Klippy is `ready` and available.
////

///

### Perform a time analysis

```{.http .apirequest title="HTTP Request"}
POST /server/analysis/estimate
Content-Type: application/json

{
    "filename": "my_file.gcode",
    "estimator_config": "custom_estimator_cfg.json"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.analysis.estimate",
    "params": {
        "filename": "my_file.gcode",
        "estimator_config": "custom_estimator_cfg.json"
    }
    "id": 4654
}
```

/// api-parameters
    open: True

| Name               |  Type  | Default            | Description                               |
| ------------------ | :----: | ------------------ | ----------------------------------------- |
| `filename`         | string | **REQUIRED**       | The path to the gcode file to perform     |
|                    |        |                    | a time estimate on.  This should be a     |^
|                    |        |                    | path relative to the `gcodes` root        |^
|                    |        |                    | folder.                                   |^
| `estimator_config` | string | **CONFIG_DEFAULT** | The path to a Klipper Estimator config    |
|                    |        |                    | file, relative to the `config` root       |^
|                    |        |                    | folder.  When omitted the file configured |^
|                    |        |                    | in the `[analysis]` section of            |^
|                    |        |                    | `moonraker.conf` or the default dumped    |^
|                    |        |                    | config will be used.                      |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "total_time": 3086.8131575260686,
    "total_distance": 63403.85014049082,
    "total_extrude_distance": 2999.883480000007,
    "max_flow": 7.593973828405062,
    "max_speed": 180,
    "num_moves": 19068,
    "total_z_time": 122.51358592092325,
    "total_output_time": 2789.122405609832,
    "total_travel_time": 257.9946362351847,
    "total_extrude_only_time": 39.446115681036936,
    "phase_times": {
        "acceleration": 360.966527738102,
        "cruise": 2365.24977575805,
        "deceleration": 360.3468540299323
    },
    "kind_times": {
        "Bridge infill": 86.61878955677516,
        "Custom": 1.1925285421682654,
        "External perimeter": 727.5477605614387,
        "Gap fill": 11.412536370727818,
        "Internal infill": 1035.7116673043204,
        "Overhang perimeter": 0.966786878164481,
        "Perimeter": 562.2619470028691,
        "Skirt/Brim": 22.540480459569807,
        "Solid infill": 573.0419719937473,
        "Top solid infill": 65.26868885629558
    },
    "layer_times": [
        [
            0,
            0.05059644256269407
        ],
        [
            0.2,
            58.177320509927746
        ],
        [
            0.5,
            31.954084022391182
        ],
        [
            0.6,
            0.9089208501630478
        ],
        [
            0.8,
            33.99706357305071
        ],
        [
            1.1,
            25.96804446085199
        ],
        [
            1.4,
            26.479048320454805
        ],
        [
            1.7,
            26.582581091690333
        ],
        [
            2,
            27.072868276853875
        ],
        [
            2.3,
            23.266380178000148
        ],
        [
            2.6,
            23.32793916103499
        ],
        [
            2.9,
            22.68151682077201
        ],
        [
            3.2,
            39.49402504999236
        ],
        [
            3.5,
            27.195385252006332
        ],
        [
            3.8,
            28.109438088654816
        ],
        [
            4.1,
            24.08349852277251
        ],
        [
            4.4,
            23.917674876902552
        ],
        [
            4.7,
            23.202091559017017
        ],
        [
            5,
            23.198343943562456
        ],
        [
            5.3,
            22.153783595351126
        ],
        [
            5.6,
            21.808169596228392
        ],
        [
            5.9,
            21.904068197159418
        ],
        [
            6.2,
            21.726213600349016
        ],
        [
            6.5,
            21.555689782559813
        ],
        [
            6.8,
            21.56001088763045
        ],
        [
            7.1,
            21.616583527557026
        ],
        [
            7.4,
            21.587509695967398
        ],
        [
            7.7,
            21.582874923811257
        ],
        [
            8,
            21.57728927279966
        ],
        [
            8.3,
            21.76624101342738
        ],
        [
            8.6,
            21.450965502680578
        ],
        [
            8.9,
            21.461465610564524
        ],
        [
            9.2,
            21.366725852519636
        ],
        [
            9.5,
            21.362167027170038
        ],
        [
            9.8,
            25.600580479722474
        ],
        [
            10.1,
            26.282946643536636
        ],
        [
            10.4,
            26.693162061300253
        ],
        [
            10.7,
            25.87730466751283
        ],
        [
            11,
            25.837521272340645
        ],
        [
            11.3,
            25.220649143903664
        ],
        [
            11.6,
            24.91627368335564
        ],
        [
            11.9,
            24.565979527961527
        ],
        [
            12.2,
            21.901257609622963
        ],
        [
            12.5,
            21.26785043389243
        ],
        [
            12.8,
            21.099317506268335
        ],
        [
            13.1,
            21.524648538390988
        ],
        [
            13.4,
            24.108699996006557
        ],
        [
            13.7,
            24.373866962973825
        ],
        [
            14,
            25.230795272831255
        ],
        [
            14.3,
            25.47226683972438
        ],
        [
            14.6,
            26.051098821629687
        ],
        [
            14.9,
            26.2540071554197
        ],
        [
            15.2,
            26.54261709911606
        ],
        [
            15.5,
            22.769433528123376
        ],
        [
            15.8,
            22.57337903594234
        ],
        [
            16.1,
            22.120135631848644
        ],
        [
            16.4,
            22.302142435605443
        ],
        [
            16.7,
            22.490758568112852
        ],
        [
            17,
            22.216297455855806
        ],
        [
            17.3,
            22.241988841558136
        ],
        [
            17.6,
            22.030502249189826
        ],
        [
            17.9,
            21.442566629762368
        ],
        [
            18.2,
            21.537227968334165
        ],
        [
            18.5,
            21.187671992912446
        ],
        [
            18.8,
            21.176477375060422
        ],
        [
            19.1,
            21.176107665494644
        ],
        [
            19.4,
            21.164450306340775
        ],
        [
            19.7,
            21.211793185762044
        ],
        [
            20,
            21.049079879215107
        ],
        [
            20.3,
            21.018544238429598
        ],
        [
            20.6,
            20.833976711167224
        ],
        [
            20.9,
            20.833976711167224
        ],
        [
            21.2,
            20.833976711167224
        ],
        [
            21.5,
            20.833976711167224
        ],
        [
            21.8,
            20.833976711167224
        ],
        [
            22.1,
            20.833976711167224
        ],
        [
            22.4,
            21.258875428281975
        ],
        [
            22.7,
            21.303045487271195
        ],
        [
            23,
            21.54997891912768
        ],
        [
            23.3,
            21.4000724519804
        ],
        [
            23.6,
            21.172838007877022
        ],
        [
            23.9,
            21.89326824952405
        ],
        [
            24.2,
            22.260210513833638
        ],
        [
            24.5,
            22.34815676766725
        ],
        [
            24.8,
            23.018360476759195
        ],
        [
            25.1,
            22.83742910264808
        ],
        [
            25.4,
            21.884928399224517
        ],
        [
            25.7,
            21.16791844379882
        ],
        [
            26,
            21.062339082163817
        ],
        [
            26.3,
            20.497926920922225
        ],
        [
            26.6,
            20.441458670088437
        ],
        [
            26.9,
            20.497926920922225
        ],
        [
            27.2,
            21.411213211524064
        ],
        [
            27.5,
            21.205564835097203
        ],
        [
            27.8,
            21.403735651236662
        ],
        [
            28.1,
            21.72317504502876
        ],
        [
            28.4,
            20.83804429637327
        ],
        [
            28.7,
            20.992445860036398
        ],
        [
            29,
            20.96056166031732
        ],
        [
            29.3,
            20.96056166031732
        ],
        [
            29.6,
            20.96056166031732
        ],
        [
            29.9,
            20.96056166031732
        ],
        [
            30.2,
            21.163385361246583
        ],
        [
            30.5,
            21.375398470771565
        ],
        [
            30.8,
            21.845443716854845
        ],
        [
            31.1,
            21.003381151310677
        ],
        [
            31.4,
            20.660669538703793
        ],
        [
            31.7,
            20.497926920922225
        ],
        [
            32,
            20.441458670088437
        ],
        [
            32.3,
            20.497926920922225
        ],
        [
            32.6,
            20.441458670088437
        ],
        [
            32.9,
            20.497926920922225
        ],
        [
            33.2,
            20.441458670088437
        ],
        [
            33.5,
            20.497926920922225
        ],
        [
            33.8,
            20.441458670088437
        ],
        [
            34.1,
            36.85516926371657
        ],
        [
            34.4,
            23.906291084020573
        ],
        [
            34.7,
            24.10243730191063
        ],
        [
            35,
            29.058094876089566
        ],
        [
            35.3,
            21.585307365265763
        ],
        [
            35.6,
            21.977729818546266
        ],
        [
            35.9,
            21.982243563755652
        ],
        [
            36.2,
            21.84660060776076
        ],
        [
            36.5,
            21.852866392888306
        ],
        [
            36.8,
            21.809194828486756
        ],
        [
            37.1,
            20.510222555418448
        ],
        [
            37.4,
            19.19335211292996
        ],
        [
            37.7,
            17.170142031218244
        ],
        [
            38,
            15.027435648219916
        ],
        [
            38.3,
            12.070425871333898
        ],
        [
            38.6,
            9.187916276700111
        ],
        [
            38.9,
            8.965728773112703
        ],
        [
            39.2,
            6.353229978247989
        ],
        [
            39.5,
            6.225660195566472
        ],
        [
            39.8,
            0.5801244322591914
        ],
        [
            40.1,
            0.2925785856185972
        ]
    ]
}
```
///

/// api-response-spec
    open: True

//// Note
This specification applies to the values returned by
Klipper Estimator version `v3.7.3`.

All time estimates are reported in seconds.
////

| Field                     |   Type    | Description                                            |
| ------------------------- | :-------: | ------------------------------------------------------ |
| `total_time`              |   float   | The total estimated time spent on the job.             |
| `total_distance`          |   float   | The total estimated travel distance of the tool in mm. |
| `total_extrude_distance`  |   float   | The total estimated extrude distance in mm.            |
| `max_flow`                |   float   | The maximum flow rate detected in mm^3^/s.             |
| `max_speed`               |   float   | The maximum tool movement speed detected in mm/s.      |
| `num_moves`               |    int    | The total number of moves detected.                    |
| `total_z_time`            |   float   | The estimated amount of time spent moving on the       |
|                           |           | Z axis.                                                |^
| `total_output_time`       |   float   | The estimated amount of time moving while extruding.   |
| `total_travel_time`       |   float   | The estimated amount of time the tool spent traveling. |
| `total_extrude_only_time` |   float   | The estimated amount of time the tool spent extruding  |
|                           |           | without other movement.                                |^
| `phase_times`             |  object   | A `Phase Times` object.                                |
|                           |           | #phase-times-object-spec                               |+
| `kind_times`              |  object   | A `Kind Times` object.                                 |
|                           |           | #kind-times-object-spec                                |+
| `layer_times`             | [[float]] | An array of 2-element arrays.  The first element       |
|                           |           | is the layer height, the second is the estimated       |^
|                           |           | time spent printing the layer.                         |^

| Field          | Type  | Description                                           |
| -------------- | :---: | ----------------------------------------------------- |
| `acceleration` | float | The amount of time the tool spent accelerating during |
|                |       | the print job.                                        |^
| `cruise`       | float | The amount of time the tool spent at cruise velocity  |
|                |       | during the print job.                                 |^
| `deceleration` | float | The amount of time the tool spent decelerating during |
|                |       | the print job.                                        |^
{ #phase-times-object-spec } Phase Times

| Field       | Type  | Description                                                       |
| ----------- | :---: | ----------------------------------------------------------------- |
| *kind_desc* | float | An entry where the key is a description of the "kind" of item     |
|             |       | being printed and its value is the total time spent printing      |^
|             |       | this "kind".  The "kind" is determined by comments in the slicer. |^
|             |       | For example `Perimeter` and `Bridge infill` are "kinds" reported  |^
|             |       | by PrusaSlicer.  If the "kind" is not available Klipper Estimator |^
|             |       | will report it under `Other`.  The `Kind Times` object may have   |^
|             |       | multiple *kind_desc* entries.                                     |^
{ #kind-times-object-spec } Kind Times

///

### Post process a file

Klipper Estimator will perform a time analysis and use the results to
modify the time estimates in the file.  If M73 (progress) commands are
present they will also be modified.

```{.http .apirequest title="HTTP Request"}
POST /server/analysis/process
Content-Type: application/json

{
    "filename": "my_file.gcode",
    "estimator_config": "custom_estimator_cfg.json",
    "force": false
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.analysis.process",
    "params": {
        "filename": "my_file.gcode",
        "estimator_config": "custom_estimator_cfg.json",
        "force": false
    }
    "id": 4654
}
```

/// api-parameters
    open: True

| Name               |  Type  | Default            | Description                                    |
| ------------------ | :----: | ------------------ | ---------------------------------------------- |
| `filename`         | string | **REQUIRED**       | The path to the gcode file to post-process.    |
|                    |        |                    | This should be a path relative to the `gcodes` |^
|                    |        |                    | root folder.                                   |^
| `estimator_config` | string | **CONFIG_DEFAULT** | The path to a Klipper Estimator config         |
|                    |        |                    | file, relative to the `config` root            |^
|                    |        |                    | folder.  When omitted the file configured      |^
|                    |        |                    | in the `[analysis]` section of                 |^
|                    |        |                    | `moonraker.conf` or the default dumped         |^
|                    |        |                    | config will be used.                           |^
| `force`            |  bool  | false              | By default the request will not perform a new  |
|                    |        |                    | post-process if the file was already processed |^
|                    |        |                    | by Klipper Estimator. When `force` is `true`   |^
|                    |        |                    | the file will be post-processed regardless.    |^

///

```{.json .apiresponse title="Example Response"}
{
    "prev_processed": false,
    "version": "v3.7.3",
    "bypassed": false
}
```

/// api-response-spec
    open: True

| Field            | Type | Description                                                |
| ---------------- | :--: | ---------------------------------------------------------- |
| `prev_processed` | bool | Will be `true` if the requested file was previously        |
|                  |      | processed by Klipper Estimator.                            |^
| `version`        | str  | The version of Klipper Estimator used to process the file. |
| `bypassed`       | bool | Will be `true` if the post-processing was bypassed.  This  |
|                  |      | occurs if the file was previously processed by Klipper     |^
|                  |      | Estimator and the `force` argument is `false`.             |^

///

/// note
If the `file_manager` has `inotify` enabled the post-process will trigger a
`create_file` event, which will in turn trigger metadata extraction.
///

### Dump the current configuration

Create a Klipper Estimator configuration file using Klippy's
current settings.

/// note
Klippy must be connected and in the `ready` state to run
this request.
///

```{.http .apirequest title="HTTP Request"}
POST /server/analysis/dump_config
Content-Type: application/json

{
    "dest_config": "custom_estimator_cfg.json"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.analysis.dump_config",
    "params": {
        "dest_config": "custom_estimator_cfg.json"
    }
    "id": 4654
}
```

/// api-parameters
    open: True

| Name          |  Type  | Default | Description                               |
| ------------- | :----: | ------- | ----------------------------------------- |
| `dest_config` | string | null    | The name of the destination config file   |
|               |        |         | for the dump. This should be a path       |^
|               |        |         | relative to the `config` root folder.     |^
|               |        |         | If omitted the result of the dump will    |^
|               |        |         | be saved to the default Klipper Estimator |^
|               |        |         | configuration file.                       |^

//// Note
The default configuration for Klipper Estimator is stored in the same
folder as the binary.

```
<data_path>/tools/klipper_estimator/default_estimator_cfg.json
```
////

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "dest_root": "config",
    "dest_config_path": "est_cfg_test.json",
    "klipper_estimator_config": {
        "max_velocity": 300,
        "max_acceleration": 1500,
        "minimum_cruise_ratio": 0.5,
        "square_corner_velocity": 5,
        "instant_corner_velocity": 1,
        "move_checkers": [
            {
                "axis_limiter": {
                    "axis": [
                        0,
                        0,
                        1
                    ],
                    "max_velocity": 15,
                    "max_accel": 200
                }
            },
            {
                "extruder_limiter": {
                    "max_velocity": 120,
                    "max_accel": 1250
                }
            }
        ]
    }
}
```
///

/// api-response-spec
    open: True

| Field                      |      Type      | Description                               |
| -------------------------- | :------------: | ----------------------------------------- |
| `dest_root`                | string \| null | The destination root folder of the dumped |
|                            |                | configuration file.  Will be `null` if    |^
|                            |                | the dumped file is the default config.    |^
| `dest_config`              |     sting      | The path of the dumped configuration file |
|                            |                | relative to the `dest_root`.  If the      |^
|                            |                | `dest_root` is null then this will be     |^
|                            |                | the default configuration's file name.    |^
| `klipper_estimator_config` |     object     | An object containing the output of the    |
|                            |                | dump command.                             |^

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
