# Job Queue Management

The following endpoints may be used to manage Moonraker's job queue.
Note that Moonraker's Job Queue is implemented as a FIFO queue and it may
contain multiple references to the same job.

The queue maintains an internal state attribute, which will always be one
of the following:

| State      | Description                                                      |
| ---------- | ---------------------------------------------------------------- |
| `ready`    | The queue is active and will load the next queued job upon       |
|            | completion of the current job.                                   |^
| `loading`  | The queue is currently loading the next job.  If the             |
|            | `job_queue` configuration specifies a `job_transition_delay`     |^
|            | and/or a `job_transition_gcode` the queue will remain in the     |^
|            | `loading` state until both are completed.                        |^
| `starting` | The state reported while the Job Queue is requesting Klipper     |
|            | to start the print.                                              |^
| `paused`   | When the queue is `paused` it will not load the next queued      |
|            | job after a working job has completed.  The queue will enter     |^
|            | this state if a pause is requested through the "pause" endpoint, |^
|            | an error is encountered during the startup or loading phases, or |^
|            | after completion of a job when the `job_queue` configuration     |^
|            | specifies that `automatic_transition` is set to false.           |^
{ #queue-state-desc } Queue State

/// note
All filenames provided to and returned by these endpoints are relative to
the `gcodes` root.
///

## Get job queue status

Retrieves the current state of the job queue.

```{.http .apirequest title="HTTP Request"}
GET /server/job_queue/status
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.status",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "ready"
}
```
///

/// api-response-spec
    open: True

| Field         |   Type   | Description                                              |
| ------------- | :------: | -------------------------------------------------------- |
| `queued_jobs` | [object] | An array of `Queued Job` objects.                        |
|               |          | #queued-job-spec                                         |+
| `queue_state` |  string  | The current [state](#queue-state-desc) of the job queue. |
{ #job-queue-status-response-spec }

| Field           |  Type  | Description                                             |
| --------------- | :----: | ------------------------------------------------------- |
| `filename`      | string | The name of the gcode file queued.                      |
| `job_id`        | string | A unique ID assigned to the queued job.                 |
| `time_added`    | float  | The time (in Unix Time) the job was added to the queue. |
| `time_in_queue` | float  | The cumulative amount of time, in seconds, the job has  |
|                 |        | been pending in the queue.                              |^
{ #queued-job-spec } Queued Job

///

## Enqueue a job

Adds a job, or an array of jobs, to the end of the job queue.  The same
filename may be specified multiple times to queue a job that repeats.
When multiple jobs are specified they will be enqueued in the order they
are received.

/// note
The request will be aborted and return an error if any of the supplied
files do not exist.
///

```{.http .apirequest title="HTTP Request"}
POST /server/job_queue/job
Content-Type: application/json

{
    "filenames": [
        "job1.gcode",
        "job2.gcode",
        "subdir/job3.gcode"
    ],
    "reset": false
}

```
/// tip
If it isn't possible for your client to pass parameters in the body
of the request as a json object, they can be added to the query string
as shown below:

```{.http .apirequest title="HTTP Request"}
POST /server/job_queue/job?filenames=job1.gcode,job2.gcode,subdir/job3.gcode
```

Multiple jobs should be comma separated as shown above.
///

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.post_job",
    "params": {
        "filenames": [
            "job1.gcode",
            "job2.gcode",
            "subdir/job3.gcode"
        ],
        "reset": false
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name        |   Type   | Default      | Description                                             |
| ----------- | :------: | ------------ | ------------------------------------------------------- |
| `filenames` | [string] | **REQUIRED** | An array of filenames of jobs to add to the queue.      |
|             |          |              | The file names should be paths relative to the `gcodes` |^
|             |          |              | root.  All of the specified files must exist, otherwise |^
|             |          |              | the request will return with an error.                  |^
| `reset`     |   bool   | false        | When set to `true` the job queue will be                |
|             |          |              | cleared prior to adding the requested jobs.             |^

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 0.01680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 0.01480004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 0.010680004119873
        }
    ],
    "queue_state": "ready"
}
```
///

/// api-response-spec
    open: True

See the [Job Queue Status](#job-queue-status-response-spec)
Response Specification.

///

## Remove a Job

Removes one or more jobs from the queue.

/// Note
Unlike the POST version of this method, it is not necessary that
all job ids exist.  If any supplied job id does not exist in the
queue it will be silently ignored.  Clients can verify the contents
of the queue via the return value.
///

```{.http .apirequest title="HTTP Request"}
DELETE /server/job_queue/job?job_ids=0000000066D991F0,0000000066D99D80
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.delete_job",
    "params": {
        "job_ids": [
            "0000000066D991F0",
            "0000000066D99D80"
        ]
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name      |   Type   | Default         | Description                                      |
| --------- | :------: | --------------- | ------------------------------------------------ |
| `job_ids` | [string] | **REQUIRED**    | An array of `job_ids` to remove from the queue.  |
|           |          | if `all==false` | Any job ids that do not exist will be ignored.   |^
| `all`     |   bool   | false           | When set to `true` all jobs will be removed      |
|           |          |                 | from the queue. In this case it is not necessary |^
|           |          |                 | to set the `job_ids` parameter.                  |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        }
    ],
    "queue_state": "ready"
}
```
///

/// api-response-spec
    open: True

See the [Job Queue Status](#job-queue-status-response-spec)
Response Specification.

///

## Pause the job queue

Sets the job queue state to "pause", which prevents the next job
in the queue from loading after an job in progress is complete.
If the queue is paused while the queue is in the `loading` state
the load will be aborted.

```{.http .apirequest title="HTTP Request"}
POST /server/job_queue/pause
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.pause",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "paused"
}
```
///

/// api-response-spec
    open: True

See the [Job Queue Status](#job-queue-status-response-spec)
Response Specification.

///

## Start the job queue

Starts the job queue.  If Klipper is ready to start a print the next
job in the queue will be loaded.  Otherwise the queue will be put
into the "ready" state, where the job will be loaded after the current
job completes.

```{.http .apirequest title="HTTP Request"}
POST /server/job_queue/start
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.start",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "loading"
}
```
///

/// api-response-spec
    open: True

See the [Job Queue Status](#job-queue-status-response-spec)
Response Specification.

///

## Perform a Queue Jump

Jumps a job to the front of the queue.

```{.http .apirequest title="HTTP Request"}
POST /server/job_queue/jump?job_id=0000000066D991F0
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.job_queue.jump",
    "params": {
        "job_id": "0000000066D991F0"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name     |  Type  | Default      | Description                             |
| -------- | :----: | ------------ | --------------------------------------- |
| `job_id` | string | **REQUIRED** | The `job_id` of the job to jump to the. |
|          |        |              | front of the queue.                     |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "queued_jobs": [
        {
            "filename": "job2.gcode",
            "job_id": "0000000066D991F0",
            "time_added": 1636151050.7766452,
            "time_in_queue": 21.88680004119873
        },
        {
            "filename": "job1.gcode",
            "job_id": "0000000066D99C90",
            "time_added": 1636151050.7666452,
            "time_in_queue": 21.89680004119873
        },
        {
            "filename": "subdir/job3.gcode",
            "job_id": "0000000066D99D80",
            "time_added": 1636151050.7866452,
            "time_in_queue": 21.90680004119873
        }
    ],
    "queue_state": "loading"
}
```
///

/// api-response-spec
    open: True

See the [Job Queue Status](#job-queue-status-response-spec)
Response Specification.

///
