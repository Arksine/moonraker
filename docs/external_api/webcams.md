# Webcam Management

Moonraker maintains webcam configuration in its database so
various applications and front-ends can share this configuration
through a consistent interface.  The endpoints in this section
may be used to manage various webcam configurations.

/// note
Moonraker does not directly manipulate webcams.
External applications, such as
[crowsnest](https://github.com/mainsail-crew/crowsnest),
handle direct webcam functionality.
///

## List Webcams

```{.http .apirequest title="HTTP Request"}
GET /server/webcams/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.webcams.list",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "webcams": [
        {
            "name": "testcam3",
            "location": "door",
            "service": "mjpegstreamer",
            "enabled": true,
            "icon": "mdiWebcam",
            "target_fps": 20,
            "target_fps_idle": 5,
            "stream_url": "http://camera.lan/webcam?action=stream",
            "snapshot_url": "http://camera.lan/webcam?action=snapshot",
            "flip_horizontal": false,
            "flip_vertical": true,
            "rotation": 90,
            "aspect_ratio": "4:3",
            "extra_data": {},
            "source": "config",
            "uid": "55d3801e-fdc1-438d-8728-2fff8b83b909"
        },
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
            "source": "database",
            "uid": "65e51c8a-6763-41d4-8e76-345bb6e8e7c3"
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
            "source": "database",
            "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field     |   Type   | Description                                             |
| --------- | :------: | ------------------------------------------------------- |
| `webcams` | [object] | An array of [Webcam Entry](#webcam-entry-spec) objects. |

| Field             |  Type  | Description                                                   |
| ----------------- | :----: | ------------------------------------------------------------- |
| `name`            | string | Friendly name of the webcam.                                  |
| `location`        | string | A single word description of where the webcam                 |
|                   |        | is located or what it is viewing.                             |^
| `service`         | string | The name of the webcam streaming service used to              |
|                   |        | operate the webcam.                                           |^
| `enabled`         |  bool  | Set to `true` when the webcam is available, `false`           |
|                   |        | otherwise.                                                    |^
| `icon`            | string | Name of the icon associated with the webcam.                  |
| `target_fps`      |  int   | Target frames per second when the printer is active.          |
| `target_fps_idle` |  int   | Target frames per second when the printer is idle.            |
| `stream_url`      | string | The url for the webcam's stream request.  This may            |
|                   |        | be a complete url, or a url path relative to                  |^
|                   |        | Moonraker's host.                                             |^
| `snapshot_url`    | string | The url for the webcam's snapshot request. This may           |
|                   |        | be a complete url, or a url path relative to                  |^
|                   |        | Moonraker's host.  If the webcam does not support             |^
|                   |        | a snapshot url this will be an empty string.                  |^
| `flip_horizontal` |  bool  | A value of `true` indicates that the stream should            |
|                   |        | be flipped horizontally.                                      |^
| `flip_vertical`   |  bool  | A value of `true` indicates that the stream should            |
|                   |        | be flipped vertically.                                        |^
| `rotation`        |  int   | Indicates the amount of clockwise rotation, in                |
|                   |        | degrees, that should be applied to the stream. May            |^
|                   |        | be 0, 90, 180, or 270.                                        |^
| `aspect_ratio`    | string | Indicates the aspect ratio of the stream. The format          |
|                   |        | should be `W:H`, for example `4:3` or `16:9`.                 |^
| `extra_data`      | object | An object containing custom configuration added by            |
|                   |        | frontends.                                                    |^
| `source`          | string | The [configuration source](#webcam-configuration-source-desc) |
|                   |        | of the webcam entry.                                          |^
| `uid`             | string | A unique identifier assigned to the webcam entry.             |
{ #webcam-entry-spec } Webcam Entry

| Source     | Description                                                    |
| ---------- | -------------------------------------------------------------- |
| `database` | The webcam's configuration is stored in Moonraker's database.  |
|            | These entries are generally added by front-ends via the webcam |^
|            | API.  Front-ends may modify and remove these entries.          |^
| `config`   | The webcam's configuration is sourced from `moonraker.conf`.   |
|            | The webcam endpoints can not modify or remove these entries.   |^
{ #webcam-configuration-source-desc } Configuration Source

//// note
Moonraker does not provide a specification for the `location`, `service`,
and `icon` fields.  These fields can contain any string value, generally
front-ends set these values based on their needs.  Developers should
consider using the same values that existing front-ends (such as Mainsail
and Fluidd) currently use to maintain compatibility.
////

///

## Get Webcam Information

```{.http .apirequest title="HTTP Request"}
GET /server/webcams/item?uid=341778f9-387f-455b-8b69-ff68442d41d9
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.webcams.get_item",
    "params": {
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default        | Description                                     |
| ------ | :----: | -------------- | ----------------------------------------------- |
| `uid`  | string | **REQUIRED**   | The requested webcam's unique ID. While         |
|        |        |                | this parameter is considered required, if       |^
|        |        |                | omitted the request will fall back on looking   |^
|        |        |                | up the camera by `name`.                        |^
| `name` | string | **DEPRECATED** | The requested webcam's friendly name. This      |
|        |        |                | parameter is deprecated, all future             |^
|        |        |                | implementations should use the `uid` parameter. |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "webcam": {
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
        "source": "database",
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    }
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                     |
| -------- | :----: | ----------------------------------------------- |
| `webcam` | object | A [Webcam Entry](#webcam-entry-spec) object for |
|          |        | the requested webcam.                           |^

///

## Add or update a webcam

Adds a new webcam entry or updates an existing entry.  When updating
an entry only the fields provided will be modified.

/// Note
A webcam configured in `moonraker.conf` cannot be updated or
overwritten using this API.
///

```{.http .apirequest title="HTTP Request"}
POST /server/webcams/item
Content-Type: application/json

{
    "name": "cam_name",
    "snapshot_url": "http://printer.lan:8080/webcam?action=snapshot",
    "stream_url": "http://printer.lan:8080/webcam?action=stream"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.webcams.post_item",
    "params": {
        "name": "cam_name",
        "snapshot_url": "/webcam?action=snapshot",
        "stream_url": "/webcam?action=stream"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

//// note
The default values in the parameter specification below apply to *new* webcam
entries.  Existing entries to be updated only require the `uid` parameter,
all other parameters default to their existing value.
////

| Name              |  Type  | Default         | Description                                          |
| ----------------- | :----: | --------------- | ---------------------------------------------------- |
| `uid`             | string | null            | The unique ID of an existing Webcam Entry to         |
|                   |        |                 | modify. If omitted the request will attempt          |^
|                   |        |                 | to create a new Webcam Entry, otherwise the          |^
|                   |        |                 | existing entry will be updated.                      |^
| `name`            | string | **REQUIRED**    | The friendly name of the webcam.  Each webcam        |
|                   |        |                 | entry must have a *unique* name.                     |^
| `location`        | string | "printer"       | A single word description of where the webcam        |
|                   |        |                 | is located or what it is viewing.                    |^
| `icon`            | string | "mdiWebcam"     | Name of the icon associated with the webcam.         |
| `enabled`         |  bool  | true            | Set to `true` when the webcam is available, `false`  |
|                   |        |                 | otherwise.                                           |^
| `service`         | string | "mjpegstreamer" | The name of the webcam streaming service used to     |
|                   |        |                 | operate the webcam.                                  |^
| `target_fps`      |  int   | 15              | Target frames per second when the printer is active. |
| `target_fps_idle` |  int   | 5               | Target frames per second when the printer is idle.   |
| `stream_url`      | string | **REQUIRED**    | The url for the webcam's stream request.  This may   |
|                   |        |                 | be a complete url or a url path relative to          |^
|                   |        |                 | Moonraker's host.                                    |^
| `snapshot_url`    | string | ""              | The url for the webcam's snapshot request. This may  |
|                   |        |                 | be a complete url or a url path relative to          |^
|                   |        |                 | Moonraker's host.                                    |^
| `flip_horizontal` |  bool  | false           | A value of `true` indicates that the stream should   |
|                   |        |                 | be flipped horizontally.                             |^
| `flip_vertical`   |  bool  | false           | A value of `true` indicates that the stream should   |
|                   |        |                 | be flipped vertically.                               |^
| `rotation`        |  int   | 0               | Indicates the amount of clockwise rotation, in       |
|                   |        |                 | degrees, that should be applied to the stream. May   |^
|                   |        |                 | be 0, 90, 180, or 270.                               |^
| `aspect_ratio`    | string | "4:3"           | Indicates the aspect ratio of the stream. The format |
|                   |        |                 | should be `W:H`, for example `4:3` or `16:9`.        |^
| `extra_data`      | object | {}              | An object containing custom configuration added by   |
|                   |        |                 | frontends.                                           |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "webcam": {
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
        "source": "database",
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    }
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                     |
| -------- | :----: | ----------------------------------------------- |
| `webcam` | object | A [Webcam Entry](#webcam-entry-spec) object for |
|          |        | the new or updated webcam.                      |^

///

## Delete a webcam

/// Note
  A webcam configured via `moonraker.conf` cannot be deleted
  using this API.
///

```{.http .apirequest title="HTTP Request"}
DELETE /server/webcams/item?uid=341778f9-387f-455b-8b69-ff68442d41d9
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.webcams.delete_item",
    "params": {
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default        | Description                                     |
| ------ | :----: | -------------- | ----------------------------------------------- |
| `uid`  | string | **REQUIRED**   | The requested webcam's unique ID. While         |
|        |        |                | this parameter is considered required, if       |^
|        |        |                | omitted the request will fall back on looking   |^
|        |        |                | up the camera by `name`.                        |^
| `name` | string | **DEPRECATED** | The requested webcam's friendly name. This      |
|        |        |                | parameter is deprecated, all future             |^
|        |        |                | implementations should use the `uid` parameter. |^

///


Parameters:

- `uid`:  The webcam's assigned unique ID.  This parameter is optional, when
  not specified the request will fallback to the `name` parameter.
- `name`: The name of the webcam to delete.  If the named webcam is not
  available the request will return with an error.  This parameter must
  be provided when the `uid` is omitted.

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "webcam": {
        "name": "TestCam",
        "location": "printer",
        "service": "mjpegstreamer",
        "target_fps": 15,
        "stream_url": "/webcam/?action=stream",
        "snapshot_url": "/webcam/?action=snapshot",
        "flip_horizontal": false,
        "flip_vertical": false,
        "rotation": 0,
        "source": "database",
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    }
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                     |
| -------- | :----: | ----------------------------------------------- |
| `webcam` | object | A [Webcam Entry](#webcam-entry-spec) object for |
|          |        | the deleted webcam.                             |^

///


## Test a webcam

Resolves a webcam's stream and snapshot urls.  If the snapshot
is served over http, a test is performed to see if the url is
reachable.

```{.http .apirequest title="HTTP Request"}
POST /server/webcams/test?uid=341778f9-387f-455b-8b69-ff68442d41d9
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.webcams.test",
    "params": {
        "uid": "341778f9-387f-455b-8b69-ff68442d41d9"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default        | Description                                     |
| ------ | :----: | -------------- | ----------------------------------------------- |
| `uid`  | string | **REQUIRED**   | The requested webcam's unique ID. While         |
|        |        |                | this parameter is considered required, if       |^
|        |        |                | omitted the request will fall back on looking   |^
|        |        |                | up the camera by `name`.                        |^
| `name` | string | **DEPRECATED** | The requested webcam's friendly name. This      |
|        |        |                | parameter is deprecated, all future             |^
|        |        |                | implementations should use the `uid` parameter. |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "name": "TestCam",
    "snapshot_reachable": true,
    "snapshot_url": "http://127.0.0.1:80/webcam/?action=snapshot",
    "stream_url": "http://127.0.0.1:80/webcam/?action=stream"
}
```
///

/// api-response-spec
    open: True

| Field                |  Type  | Description                                |
| -------------------- | :----: | ------------------------------------------ |
| `name`               | string | The friendly name of the webcam tested.    |
| `snapshot_reachable` |  bool  | Value will be `true` if Moonraker is able  |
|                      |        | to successfully resolve and connect to the |^
|                      |        | snapshot url.                              |^
| `snapshot_url`       | string | The resolved snapshot url.                 |
| `stream_url`         | string | The resolved stream url.                   |

///
