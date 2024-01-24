# Job History

Moonraker's `history` component tracks print job completion data.
The following endpoints are available to manage Moonraker's job
history data.

## Get job list

```{.http .apirequest title="HTTP Request"}
GET /server/history/list?limit=50&start=50&since=1&before=5&order=asc
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.history.list",
    "params":{
        "limit": 50,
        "start": 10,
        "since": 464.54,
        "before": 1322.54,
        "order": "asc"
    },
    "id": 5656
}
```

/// api-parameters
    open: True

| Name     |  Type  | Default   | Description                                     |
| -------- | :----: | --------- | ----------------------------------------------- |
| `limit`  |  int   | 50        | Maximum number of job entries to return.        |
| `start`  |  int   | 0         | The record number indicating the first entry    |
|          |        |           | of the returned list.                           |^
| `before` | float  | undefined | A timestamp in unix time. When specified, the   |
|          |        |           | returned list will only contain entries created |^
|          |        |           | before this date.                               |^
| `since`  | float  | undefined | A timestamp in unix time. When specified, the   |
|          |        |           | returned list will only contain entries created |^
|          |        |           | after this date.                                |^
| `order`  | string | "desc"    | The order of the list returned.  May be `asc`   |
|          |        |           | (ascending) or `desc` (descending).             |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "count": 1,
    "jobs": [
        {
            "job_id": "000001",
            "exists": true,
            "end_time": 1615764265.6493807,
            "filament_used": 7.83,
            "filename": "test/history_test.gcode",
            "metadata": {
                // Object containing metadata at time of job
            },
            "print_duration": 18.37201827496756,
            "status": "completed",
            "start_time": 1615764496.622146,
            "total_duration": 18.37201827496756,
            "user": "testuser",
            "auxiliary_data": [
                {
                    "provider": "sensor hist_test",
                    "name": "power_consumption",
                    "value": 4.119977,
                    "description": "Printer Power Consumption",
                    "units": "kWh"
                },
                {
                    "provider": "sensor hist_test",
                    "name": "max_current",
                    "value": 2.768851,
                    "description": "Maximum current draw",
                    "units": "A"
                },
                {
                    "provider": "sensor hist_test",
                    "name": "min_current",
                    "value": 0.426725,
                    "description": "Minimum current draw",
                    "units": "A"
                },
                {
                    "provider": "sensor hist_test",
                    "name": "avg_current",
                    "value": 1.706872,
                    "description": "Average current draw",
                    "units": "A"
                },
                {
                    "provider": "sensor hist_test",
                    "name": "status",
                    "value": 2,
                    "description": "Power Switch Status",
                    "units": null
                },
                {
                    "provider": "sensor hist_test",
                    "name": "filament",
                    "value": 19.08058495194607,
                    "description": "filament usage tracker",
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
            ]
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field   |   Type   | Description                                        |
| ------- | :------: | -------------------------------------------------- |
| `count` |   int    | The number of entries returned by the query.       |
| `jobs`  | [object] | An array of [Job History](#job-history-entry-spec) |
|         |          | objects.                                           |^

| Field            |      Type      | Description                                                 |
| ---------------- | :------------: | ----------------------------------------------------------- |
| `job_id`         |     string     | A unique ID for the entry.                                  |
| `user`           | string \| null | The user that started the job.  Will be `null`              |
|                  |                | if Moonraker cannot identify a user (ie: job)               |^
|                  |                | was started via Klipper's display.                          |^
| `filename`       |     string     | The path, relative to `gcodes` root, of the file            |
|                  |                | associated with the job.                                    |^
| `exists`         |      bool      | A value of `true` indicates that the file                   |
|                  |                | associated with the job exists on disk and has              |^
|                  |                | not been modified.                                          |^
| `status`         |     string     | The [job status](#job-history-status-desc)                  |
|                  |                | at the time of query.                                       |^
| `start_time`     |     float      | A timestamp, in unix time, indicating when                  |
|                  |                | the job started.                                            |^
| `end_time`       | float \| null  | A timestamp, in unix time, indicating when                  |
|                  |                | the job finished.  Will be `null` if the                    |^
|                  |                | job is in progress or if Moonraker is                       |^
|                  |                | interrupted prior to the job completion.                    |^
| `print_duration` |     float      | The amount of time, in seconds, the job                     |
|                  |                | spent printing (ie: printer not idle).                      |^
| `total_duration` |     float      | The total amount of time, in seconds, the                   |
|                  |                | job took to print.  This includes time paused.              |^
| `filament_used`  |     float      | The amount of filament (in mm) used during the job.         |
| `metadata`       |     object     | The [gcode metadata](./file_manager.md#gcode-metadata-spec) |
|                  |                | object associated with the job.  The `job_id` and           |^
|                  |                | `print_start_time` fields are removed from the metadata as  |^
|                  |                | they are redundant.                                         |^
| `auxiliary_data` |    [object]    | An array of [auxiliary field](#job-history-aux-field-spec)  |
|                  |                | objects containing supplemental history data related to     |^
|                  |                | the job.                                                    |^
{ #job-history-entry-spec } Job History

| Field         |      Type      | Description                                       |
| ------------- | :------------: | ------------------------------------------------- |
| `provider`    |     string     | The component or extension that generated the     |
|               |                | auxiliary field.                                  |^
| `name`        |     string     | A name identifying the field.                     |
| `description` |     string     | A brief description of the data in this entry.    |
| `value`       |      any       | The value associated with the field.  Can be any  |
|               |                | valid JSON type.                                  |^
| `units`       | string \| null | The unit type associated with the value.  For     |
|               |                | example this would be `mm` for millimeters.  Can  |^
|               |                | be `null` if no unit abbreviation is appropriate. |^
{ #job-history-aux-field-spec } Auxiliary Field

| Status              | Description                                           |
| ------------------- | ----------------------------------------------------- |
| `in_progress`       | The job is currently active.                          |
| `completed`         | The job successfully completed.                       |
| `cancelled`         | The job was cancelled by the user.                    |
| `error`             | The job was aborted due to an error during execution. |
| `klippy_shutdown`   | The job was aborted due to Klippy Shutdown.           |
| `klippy_disconnect` | Moonraker's connection to Klippy was lost while the   |
|                     | job was in progress.                                  |^
| `interrupted`       | Moonraker was abruptly terminated while the job was   |
|                     | in progress.                                          |^
{ #job-history-status-desc } Job Status

///
## Get job totals

```{.http .apirequest title="HTTP Request"}
GET /server/history/totals
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.history.totals",
    "id": 5656
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "job_totals": {
        "total_jobs": 3,
        "total_time": 11748.077333278954,
        "total_print_time": 11348.794790096988,
        "total_filament_used": 11615.718840001999,
        "longest_job": 11665.191012736992,
        "longest_print": 11348.794790096988
    },
    "auxiliary_totals": [
        {
            "provider": "sensor hist_test",
            "field": "power_consumption",
            "maximum": 4.119977,
            "total": 4.119977
        },
        {
            "provider": "sensor hist_test",
            "field": "avg_current",
            "maximum": 1.706872,
            "total": null
        },
        {
            "provider": "sensor hist_test",
            "field": "filament",
            "maximum": 19.08058495194607,
            "total": 19.08058495194607
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field              |   Type   | Description                                     |
| ------------------ | :------: | ----------------------------------------------- |
| `job_totals`       |  object  | A [Job Totals](#job-history-totals-spec) object |
|                    |          | reporting all current totals.                   |^
| `auxiliary_totals` | [object] | An array of                                     |
|                    |          | [Auxiliary Total](#job-auxiliary-totals-spec)   |^
|                    |          | objects.                                        |^

| Field                 |   Type   | Description                                            |
| --------------------- | :------: | ------------------------------------------------------ |
| `total_jobs`          |   int    | The total number of jobs tracked.                      |
| `total_time`          |  float   | The total amount of job work time (in seconds)         |
|                       |          | across all jobs, including time paused.                |^
| `total_print_time`    |  float   | The total amount of time printing (in seconds)         |
|                       |          | across all jobs.                                       |^
| `total_filament_used` |  float   | The total amount of filament used (in mm) across       |
|                       |          | all jobs.                                              |^
| `longest_job`         |  float   | The maximum time spent working on a single job,        |
|                       |          | including time paused.                                 |^
| `longest_print`       |  float   | The maximum time spent printing a single job.          |
| `auxiliary_totals`    | [object] | An array of                                            |
|                       |          | [Auxiliary Total](#job-auxiliary-totals-spec) objects. |^
{ #job-history-totals-spec } Job Totals

| Field      |     Type      | Description                                     |
| ---------- | :-----------: | ----------------------------------------------- |
| `provider` |    string     | The component or extension that generated the   |
|            |               | auxiliary totals.                               |^
| `field`    |    string     | The corresponding `name` of the auxiliary field |
|            |               | used to generate totals.                        |^
| `maximum`  | float \| null | The maximum value observed across all prints.   |
|            |               | Will be `null` if the maximum is not available. |^
| `total`    | float \| null | The accumulated total value across all prints.  |
|            |               | Will be `null` if the total is not available.   |^
{ #job-auxiliary-totals-spec } Auxiliary Total

///

## Reset totals
Resets the persistent "job totals" to zero.

```{.http .apirequest title="HTTP Request"}
POST /server/history/reset_totals
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.history.reset_totals",
    "id": 5534
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "last_totals": {
        "total_jobs": 3,
        "total_time": 11748.077333278954,
        "total_print_time": 11348.794790096988,
        "total_filament_used": 11615.718840001999,
        "longest_job": 11665.191012736992,
        "longest_print": 11348.794790096988
    },
    "last_auxiliary_totals": [
        {
            "provider": "sensor hist_test",
            "field": "power_consumption",
            "maximum": 4.119977,
            "total": 4.119977
        },
        {
            "provider": "sensor hist_test",
            "field": "avg_current",
            "maximum": 1.706872,
            "total": null
        },
        {
            "provider": "sensor hist_test",
            "field": "filament",
            "maximum": 19.08058495194607,
            "total": 19.08058495194607
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field                   |   Type   | Description                                     |
| ----------------------- | :------: | ----------------------------------------------- |
| `last_totals`           |  object  | A [Job Totals](#job-history-totals-spec) object |
|                         |          | reporting all totals prior to the reset.        |^
| `last_auxiliary_totals` | [object] | An array of                                     |
|                         |          | [Auxiliary Total](#job-auxiliary-totals-spec)   |^
|                         |          | objects reporting totals prior to the reset.    |^

///

## Get a single job

```{.http .apirequest title="HTTP Request"}
GET /server/history/job?uid=<id>
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.history.get_job",
    "params":{"uid": "{uid}"},
    "id": 4564
}
```

/// api-parameters
    open: True

| Name  |  Type  | Default      | Description                             |
| ----- | :----: | ------------ | --------------------------------------- |
| `uid` | string | **REQUIRED** | The unique identifier for the requested |
|       |        |              | job history.                            |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "job": {
        "job_id": "000001",
        "exists": true,
        "end_time": 1615764265.6493807,
        "filament_used": 7.83,
        "filename": "test/history_test.gcode",
        "metadata": {
            // Object containing metadata at time of job
        },
        "print_duration": 18.37201827496756,
        "status": "completed",
        "start_time": 1615764496.622146,
        "total_duration": 18.37201827496756
    }
}
```
///

/// api-response-spec
    open: True

| Field |  Type  | Description                                                   |
| ----- | :----: | ------------------------------------------------------------- |
| `job` | object | The requested [Job History](#job-history-entry-spec ) object. |

///

## Delete a job

```{.http .apirequest title="HTTP Request"}
DELETE /server/history/job?uid=<id>
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.history.delete_job",
    "params":{
        "uid": "{uid}"
    },
    "id": 5534
}
```

/// api-parameters
    open: True

| Name  |  Type  | Default         | Description                                |
| ----- | :----: | --------------- | ------------------------------------------ |
| `uid` | string | **REQUIRED**    | The unique identifier for the job entry    |
|       |        | if `all==false` | to delete.                                 |^
| `all` |  bool  | false           | When set to `true` all job history entries |
|       |        |                 | will be removed.                           |^

//// tip
If `all = true` is specified the `uid` parameter should be omitted.
////

///

```{.json .apiresponse title="Example Response"}
{
    "deleted_jobs": [
        "000000",
        "000001"
    ]
}
```

/// api-response-spec
    open: True

| Field          |   Type   | Description                           |
| -------------- | :------: | ------------------------------------- |
| `deleted_jobs` | [string] | An array of unique IDs indicating the |
|                |          | job entries that were deleted.        |^

///
