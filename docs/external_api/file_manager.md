# File Management

Most file operations are available over both HTTP and JSON-RPC APIs,
however file transfers (upload and download) are exclusive to the HTTP
API.

Moonraker organizes local directories into "roots".  For example,
`gcodes` are located at `http://host/server/files/gcodes/*`, otherwise known
as the "gcodes" root.  The following default roots are generally available:

- gcodes
- config
- logs (read-only)
- config_examples (Klipper Configuration Examples, read-only)
- docs (Klipper Documentation, read-only)

Write operations (upload, delete, make directory, remove directory) are
only available on the `gcodes` and `config` roots, however it is possible
for users to configure the `config` root to be read-only.

Many endpoints return permission information on files and/or folders.
Permissions are represented as a string value in the following format:

| Value  | Description                          |
| ------ | ------------------------------------ |
| `"r"`  | Item is read-only.                   |
| `"rw"` | Item has read and write permissions. |
| `""`   | Item is not accessible.              |
{ #file-permissions-desc } Permissions

## List available files

Walks through a directory and fetches all detected files.
File names include a path relative to the specified `root`.

/// Tip
In most scenarios it will likely be preferable to request files
by [directory](#get-directory-information) as opposed to listing
the entire root.
///

```{.http .apirequest title="HTTP Request"}
GET /server/files/list?root=config
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.list",
    "params": {
        "root": "config"
    },
    "id": 4644
}
```

/// api-parameters
    open: True
| Name   |  Type  | Default  | Description                                   |
| ------ | :----: | -------- | --------------------------------------------- |
| `root` | string | `gcodes` | The name of the `root` from which a file list |
|        |        |          | should be returned.                           |^

//// Note
The `gcodes` root will only return files with valid gcode file extensions.
////
///


/// collapse-code
```{.json .apiresponse title="Example Response"}
[
    {
        "path": "3DBenchy_0.15mm_PLA_MK3S_2h6m.gcode",
        "modified": 1615077020.2025201,
        "size": 4926481,
        "permissions": "rw"
    },
    {
        "path": "Shape-Box_0.2mm_PLA_Ender2_20m.gcode",
        "modified": 1614910966.946807,
        "size": 324236,
        "permissions": "rw"
    },
    {
        "path": "test_dir/A-Wing.gcode",
        "modified": 1605202259,
        "size": 1687387,
        "permissions": "rw"
    },
    {
        "path": "test_dir/CE2_CubeTest.gcode",
        "modified": 1614644445.4025,
        "size": 1467339,
        "permissions": "rw"
    },
    {
        "path": "test_dir/V350_Engine_Block_-_2_-_Scaled.gcode",
        "modified": 1615768477.5133543,
        "size": 189713016,
        "permissions": "rw"
    }
]
```
///

/// api-response-spec
    open: True

The result is an array of `File Info` objects:

| Field         |  Type  | Description                                                      |
| ------------- | :----: | ---------------------------------------------------------------- |
| `path`        | string | The path of the file, relative to the requested root.            |
| `modified`    | float  | The last modified date in Unix Time (seconds).                   |
| `size`        |  int   | The size of the file in bytes.                                   |
| `permissions` | string | The available [permissions](#file-permissions-desc) of the file. |
{ #file-info-spec} File Info

///

## List registered roots

Reports information about "root" directories registered with Moonraker.

```{.http .apirequest title="HTTP Request"}
GET /server/files/roots
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.roots",
    "id": 4644
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
[
    {
        "name": "config",
        "path": "/home/pi/printer_data/config",
        "permissions": "rw"
    },
    {
        "name": "logs",
        "path": "/home/pi/printer_data/logs",
        "permissions": "r"
    },
    {
        "name": "gcodes",
        "path": "/home/pi/printer_data/gcodes",
        "permissions": "rw"
    },
    {
        "name": "config_examples",
        "path": "/home/pi/klipper/config",
        "permissions": "r"
    },
    {
        "name": "docs",
        "path": "/home/pi/klipper/docs",
        "permissions": "r"
    }
]
```
///

/// api-response-spec
    open: True

The result is an array of `Root Info` objects:

| Field         |  Type  | Description                                                  |
| ------------- | :----: | ------------------------------------------------------------ |
| `name`        | string | The name of the registered root.                             |
| `path`        | string | The absolute path on disk of the registered root.            |
| `permissions` | string | [Permissions](#file-permissions-desc) available on the root. |
{ #root-info-spec } Root Info

///

## Get GCode Metadata

Get metadata for a specified gcode file.

```{.http .apirequest title="HTTP Request"}
GET /server/files/metadata?filename=tools/drill.gcode
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.metadata",
    "params": {
        "filename": "tools/drill.gcode"
    },
    "id": 3545
}
```
/// api-parameters
    open: True

| Name       | Type | Default      | Description                                                |
| ---------- | :--: | ------------ | ---------------------------------------------------------- |
| `filename` | str  | **REQUIRED** | The path to the gcode file, relative to the `gcodes` root. |

///

/// collapse-code
```{.json #metadata-example-response .apiresponse title="Example Response"}
{
    "size": 1629418,
    "modified": 1706359465.4947228,
    "uuid": "473a41d2-15f4-434b-aeb4-ab96eb122bbf",
    "file_processors": [],
    "slicer": "PrusaSlicer",
    "slicer_version": "2.7.1+win64",
    "gcode_start_byte": 87410,
    "gcode_end_byte": 1618468,
    "object_height": 8,
    "estimated_time": 5947,
    "nozzle_diameter": 0.4,
    "layer_height": 0.2,
    "first_layer_height": 0.2,
    "first_layer_extr_temp": 215,
    "first_layer_bed_temp": 60,
    "chamber_temp": 50,
    "filament_name": "Generic PLA Brown",
    "filament_type": "PLA",
    "filament_total": 9159.55,
    "filament_weight_total": 27.32,
    "thumbnails": [
        {
            "width": 32,
            "height": 32,
            "size": 1078,
            "relative_path": ".thumbs/hook_x4_0.2mm_PLA_MK3S_1h39m-32x32.png"
        },
        {
            "width": 400,
            "height": 300,
            "size": 61576,
            "relative_path": ".thumbs/hook_x4_0.2mm_PLA_MK3S_1h39m-400x300.png"
        }
    ],
    "print_start_time": 1706359466.722097,
    "job_id": "0000BF",
    "filename": "hook_x4_0.2mm_PLA_MK3S_1h39m.gcode"
}
```
///

/// api-response-spec
    open: True
| Field                   |   Type   | Description                                                  |
| ----------------------- | :------: | ------------------------------------------------------------ |
| `size`                  |   int    | The gcode file size in bytes.                                |
| `modified`              |  float   | The last modified time in Unix Time (seconds).               |
| `uuid`                  |  string  | A unique identifier for the metadata object.                 |
| `file_processors`       | [string] | An array of `File Processors` that have processed            |
|                         |          | and modified the file.                                       |^
|                         |          | #file-processor-app-desc                                     |+
| `slicer`                |  string  | The name of the slicer software used to slice the file.      |
| `slicer_version`        |  string  | The version of the slicer software.                          |
| `gcode_start_byte`      |   int    | The byte offset in the file where the first gcode command    |
|                         |          | is detected.                                                 |^
| `gcode_int_byte`        |   int    | The byte offset in the file where the last gcode command     |
|                         |          | is detected.                                                 |^
| `object_height`         |  float   | The height (in mm) of the tallest object in the file.        |
| `estimated_time`        |  float   | The estimated time to complete the print, in seconds.        |
| `nozzle_diameter`       |  float   | The configured nozzle diameter, in mm.                       |
| `layer_height`          |  float   | The configured layer height, in mm.                          |
| `first_layer_height`    |  float   | The configured first layer height in mm.                     |
| `first_layer_extr_temp` |  float   | The configured first layer extruder temperature, in Celsius. |
| `first_layer_bed_temp`  |  float   | The configured first layer bed temperature, in Celsius.      |
| `chamber_temp`          |  float   | The configured chamber temperature, in Celsius.              |
| `filament_name`         |  string  | The name(s) of the filaments contained in print.             |
| `filament_colors`       | [string] | List of filament colors used in #RRGGBB format.              |
| `extruder_colors`       | [string] | List of  slicer defined extruder colors for the print.       |
| `filament_temps`        |  [int]   | List of base temperatures for filaments, in Celsius.         |
| `filament_type`         |  string  | The type(s) of filament used, ie: `PLA`.                     |
| `filament_total`        |  float   | The total length filament used in mm.                        |
| `filament_change_count` |   int    | The number of filament changes in the print.                 |
| `filament_weight_total` |  float   | The total weight of filament used in grams.                  |
| `filament_weights`      | [float]  | List of weights in grams used by each tool in the print.     |
| `mmu_print`             |   int    | Identifies a multimaterial print with single extruder.       |
| `referenced_tools`      |  [int]   | List of tool numbers used in the print.                      |
| `thumbnails`            | [object] | A list of `Thumbnail Info` objects.                          |
|                         |          | #thumbnail-info-spec                                         |+
| `job_id`                | string?  | The last `history` job ID associated with the gcode.         |
|                         |          | Will be `null` if no job has been associated with the file.  |^
| `print_start_time`      |  float   | The most recent start time the gcode file was printed. Will  |
|                         |          | be `null` if the file has yet to be printed.                 |^
| `filename`              |  string  | Path to the gcode file, relative to the `gcodes` root.       |
{ #gcode-metadata-spec }

| Field           |  Type  | Description                                                     |
| --------------- | :----: | --------------------------------------------------------------- |
| `width`         |  int   | The width of the thumbnail in pixels.                           |
| `height`        |  int   | The height of the thumbnail in pixels.                          |
| `size`          |  int   | The size of the thumbnail in bytes.                             |
| `relative_path` | string | The path of the thumbnail, relative to the gcode file's parent. |
{ #thumbnail-info-spec } Thumbnail Info

| Application               | Description                                                      |
| ------------------------- | ---------------------------------------------------------------- |
| `preprocess_cancellation` | Converts "object identifiers" generated by the slicer into       |
|                           | GCode commands for use with Klipper's `[exclude_object]` module. |^
| `klipper_estimator`       | Performs a time analysis on the gcode file, replacing the        |
|                           | time estimates in the file with the result.  Also updates M73    |^
|                           | commands.  The [analysis](../configuration.md#analysis)          |^
|                           | component must be loaded for the metadata processor to detect if |^
|                           | a file has been processed by `klipper_estimator`.                |^
{ #file-processor-app-desc } File Processors

//// Note
Metadata field availability depends on the Slicer application and its
configuration.  If a field cannot be parsed from the slicer it will
be omitted.
////

///


## Scan GCode Metadata

Initiate a metadata scan for a selected file.  If the file has already
been scanned the endpoint will force a re-scan.

```{.http .apirequest title="HTTP Request"}
POST /server/files/metascan?filename={filename}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.metascan",
    "params": {
        "filename": "{filename}"
    },
    "id": 3545
}
```

/// api-parameters
    open: True

| Name       | Type | Default      | Description                                                |
| ---------- | :--: | ------------ | ---------------------------------------------------------- |
| `filename` | str  | **REQUIRED** | The path to the gcode file, relative to the `gcodes` root. |

///

For an example response refer to the
[Metadata Example Response](#metadata-example-response).

/// api-response-spec
    open: True
The response spec is identical to the
[Metadata Request Specification](#gcode-metadata-spec)
///


## Get GCode Thumbnail Details

Returns thumbnail information for a supplied gcode file.

```{.http .apirequest title="HTTP Request"}
GET /server/files/thumbnails?filename=tools/drill.gcode
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.thumbnails",
    "params": {
        "filename": "{filename}"
    },
    "id": 3545
}
```

/// api-parameters
    open: True

| Name       | Type | Default      | Description                                                |
| ---------- | :--: | ------------ | ---------------------------------------------------------- |
| `filename` | str  | **REQUIRED** | The path to the gcode file, relative to the `gcodes` root. |

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
[
    {
        "width": 32,
        "height": 32,
        "size": 1551,
        "thumbnail_path": "test/.thumbs/CE2_FanCover-120mm-Mesh-32x32.png"
    },
    {
        "width": 300,
        "height": 300,
        "size": 31819,
        "thumbnail_path": "test/.thumbs/CE2_FanCover-120mm-Mesh.png"
    }
]
```
///

/// api-response-spec
    open: True

The result is an array of `Thumbnail Details` objects.

| Field            |  Type  | Description                                               |
| ---------------- | :----: | --------------------------------------------------------- |
| `width`          |  int   | The width of the thumbnail in pixels.                     |
| `height`         |  int   | The height of the thumbnail in pixels.                    |
| `size`           |  int   | The size of the thumbnail in bytes.                       |
| `thumbnail_path` | string | The path of the thumbnail, relative to the `gcodes` root. |
{ #thumbnail-details-spec } Thumbnail Details

//// Note
The `Thumbnails Details` spec is nearly identical to the
[Thumbnail Info](#thumbnail-info-spec) spec reported in
a [metadata request](#get-gcode-metadata), with one exception.
The `thumbnail_path` field in the result above contains a
path relative to the `gcodes` root, whereas the `relative_path`
field reported in the `Thumbnail Info` is relative to the gcode
file's parent folder.
////

///


## Get directory information

Returns a list of files and subdirectories given a supplied path.
Unlike `/server/files/list`, this command does not walk through
subdirectories.  This request will return all files in a directory,
including files in the `gcodes` root that do not have a valid gcode
extension.

```{.http .apirequest title="HTTP Request"}
GET /server/files/directory?path=gcodes/my_subdir&extended=true
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.get_directory",
    "params": {
        "path": "gcodes/my_subdir",
        "extended": true
    },
    "id": 5644
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default  | Description                                         |
| ---------- | :----: | -------- | --------------------------------------------------- |
| `path`     | string | `gcodes` | Path to the directory.  The first part must be a    |
|            |        |          | registered root.                                    |^
| `extended` |  bool  | `false`  | When set to `true` metadata will be included in the |
|            |        |          | response for gcode file.                            |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "dirs": [
        {
            "modified": 1615768162.0412788,
            "size": 4096,
            "permissions": "rw",
            "dirname": "test"
        },
        {
            "modified": 1613569827.489749,
            "size": 4096,
            "permissions": "rw",
            "dirname": "Cura"
        },
        {
            "modified": 1615767459.6265886,
            "size": 4096,
            "permissions": "rw",
            "dirname": "thumbs"
        }
    ],
    "files": [
        {
            "modified": 1615578004.9639666,
            "size": 7300692,
            "permissions": "rw",
            "filename": "Funnel_0.2mm_PLA_Ender2_2h4m.gcode"
        },
        {
            "modified": 1589156863.9726968,
            "size": 4214831,
            "permissions": "rw",
            "filename": "CE2_Pi3_A+_CaseLID.gcode"
        },
        {
            "modified": 1615030592.7722695,
            "size": 2388774,
            "permissions": "rw",
            "filename": "CE2_calicat.gcode"
        }
    ],
    "disk_usage": {
        "total": 7522213888,
        "used": 4280369152,
        "free": 2903625728
    },
    "root_info": {
        "name": "gcodes",
        "permissions": "rw"
    }
}
```
///

/// api-response-spec
    open: True

| Field        |   Type   | Description                                             |
| ------------ | :------: | ------------------------------------------------------- |
| `dirs`       | [object] | An array of `Directory Info` objects.  Will be empty if |
|              |          | no sub-directories are found.                           |^
|              |          | #directory-info-spec                                    |+
| `files`      | [object] | An array of `File Info` objects.  Will be empty if no   |
|              |          | files are found.                                        |^
|              |          | #dir-req-file-info-spec                                 |+
| `disk_usage` |  object  | A `Disk Usage` object. This provides disk usage details |
|              |          | about the underlying storage media containing the       |^
|              |          | requested directory.                                    |^
|              |          | #disk-usage-spec                                        |+
| `root_info`  |  object  | A `Root Info` object. Provides details about the        |
|              |          | directory's root parent.                                |^
|              |          | #root-info-spec                                         |+


| Field         |  Type  | Description                                                           |
| ------------- | :----: | --------------------------------------------------------------------- |
| `modified`    | float  | The last modified date in Unix Time (seconds).                        |
| `size`        |  int   | The size of the file in bytes.                                        |
| `permissions` | string | The available [permissions](#file-permissions-desc) of the directory. |
| `dirname`     | string | The name of the directory.                                            |
{ #directory-info-spec } Directory Info


| Field             |  Type  | Description                                                            |
| ----------------- | :----: | ---------------------------------------------------------------------- |
| `modified`        | float  | The last modified date in Unix Time (seconds).                         |
| `size`            |  int   | The size of the file in bytes.                                         |
| `permissions`     | string | The available [permissions](#file-permissions-desc) of the directory.  |
| `filename`        | string | The name of the file.                                                  |
| _metadata-fields_ | _any_  | When the `extended` parameter is set to true all available metadata    |
|                   |        | fields are included in the object.  See the                            |^
|                   |        | [metadata response spec](#gcode-metadata-spec) for details.  Note that |^
|                   |        | the `filename` in the metadata spec, which is a relative path, will    |
|                   |        | not overwrite the `filename` above, which is not a path.               |
{ #dir-req-file-info-spec } File Info

| Field   | Type | Description                              |
| ------- | :--: | ---------------------------------------- |
| `free`  | int  | The amount of free space in bytes.       |
| `used`  | int  | The amount of used data in bytes.        |
| `total` | int  | The total capacity of the disk in bytes. |
{ #disk-usage-spec } Disk Usage

| Field         |  Type  | Description                                                |
| ------------- | :----: | ---------------------------------------------------------- |
| `name`        | string | The name of the root node for the requested directory.     |
| `permissions` | string | The available [permissions](#file-permissions-desc) of the |
|               |        | root node.                                                 |^
{ #root-info-spec } Root Info

///

## Create directory

Creates a directory at the specified path.

```{.http .apirequest title="HTTP Request"}
POST /server/files/directory
Content-Type: application/json

{
    "path": "gcodes/my_new_dir"
}
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.post_directory",
    "params": {
        "path": "gcodes/my_new_dir"
    },
    "id": 6548
}
```

/// api-parameters
    open: True

| Name   |  Type  | Default      | Description                                                |
| ------ | :----: | ------------ | ---------------------------------------------------------- |
| `path` | string | **REQUIRED** | The path to the directory to create, including its `root`. |
|        |        |              | Note that the parent directory must exist.                 |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "item": {
        "path": "my_new_dir",
        "root": "gcodes",
        "modified": 1676983427.3732708,
        "size": 4096,
        "permissions": "rw"

    },
    "action": "create_dir"
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                                 |
| -------- | :----: | ----------------------------------------------------------- |
| `item`   | object | An `Item Details` object describing the directory created.  |
|          |        | #create-dir-item-details-spec                               |+
| `action` | string | A description of the action taken by the host.  Will always |
|          |        | be `create_dir` for this request.                           |^

| Field         |  Type  | Description                                          |
| ------------- | :----: | ---------------------------------------------------- |
| `path`        | string | The path of the new directory, relative to the root. |
| `root`        | string | The root node the directory was created under.       |
| `modified`    | float  | The last modified date in Unix Time (seconds).       |
| `size`        |  int   | The size of the directory.  Will generally be 4096.  |
| `permissions` | string | Permissions available on the new directory.          |
{ #create-dir-item-details-spec } Item Details

///

## Delete directory
Deletes a directory at the specified path.

```{.http .apirequest title="HTTP Request"}
DELETE /server/files/directory?path=gcodes/my_subdir&force=false
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.delete_directory",
    "params": {
        "path": "gcodes/my_subdir",
        "force": false
    },
    "id": 6545
}
```

/// api-parameters
    open: True

| Name    |  Type  | Default      | Description                                                 |
| ------- | :----: | ------------ | ----------------------------------------------------------- |
| `path`  | string | **REQUIRED** | The path to the directory to delete, including its `root`.  |
|         |        |              | Note that the directory must be empty if `force` is `false` |^
| `force` |  bool  | `false`      | When set to `true` the directory and all of its contents    |
|         |        |              | will be deleted.                                            |^

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "item": {
        "path": "my_subdir",
        "root": "gcodes",
        "modified": 0,
        "size": 0,
        "permissions": ""

    },
    "action": "delete_dir"
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                                 |
| -------- | :----: | ----------------------------------------------------------- |
| `item`   | object | An `Item Details` object describing the directory deleted.  |
|          |        | #delete-dir-item-details-spec                               |+
| `action` | string | A description of the action taken by the host.  Will always |
|          |        | be `delete_dir` for this request.                           |^

| Field         |  Type  | Description                                                   |
| ------------- | :----: | ------------------------------------------------------------- |
| `path`        | string | The path of the deleted directory, relative to the root.      |
| `root`        | string | The root node the directory existed under prior to removal.   |
| `modified`    | float  | The last modified date in Unix Time (seconds).  Should be     |
|               |        | 0 if the delete was successful.                               |^
| `size`        |  int   | The size of the removed directory.  Should be 0 if the delete |
|               |        | was successful.                                               |^
| `permissions` | string | Permissions available on the removed directory.  Should be    |
|               |        | an empty string if the delete was successful.                 |^
{ #delete-dir-item-details-spec } Item Details

///

## Move a file or directory

Moves a file or directory from one location to another. The following
conditions must be met for a move successful move:

- The source item must exist.
- The user that owns the Moonraker process must have the appropriate
  file permissions.
- Neither the source nor destination can be loaded by Klipper's `virtual_sdcard`.
  If the source is a directory, it must not contain a file loaded by the
  `virtual_sdcard`.

When specifying the `source` and `dest`, the `root` directory should be
prefixed. Currently the only supported roots for `dest` are `gcodes`"
and `config`".

This endpoint may also be used to rename a file or directory.   Be aware that an
attempt to rename a directory to a directory that exists with the same name will
*move* the source directory into the destination directory.  Also be aware
that renaming a file to a file that already exists will overwrite the existing
file.

```{.http .apirequest title="HTTP Request"}
POST /server/files/move
Content-Type: application/json

{
    "source": "gcodes/orig_dir/my_file.gcode",
    "dest": "gcodes/new_dir/my_file.gcode"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.move",
    "params": {
        "source": "gcodes/orig_dir/my_file.gcode",
        "dest": "gcodes/new_dir/my_file.gcode"
    },
    "id": 5664
}
```

/// api-parameters
    open: True

| Name     |  Type  | Default      | Description                                         |
| -------- | :----: | ------------ | --------------------------------------------------- |
| `source` | string | **REQUIRED** | The source file or directory to move.               |
|          |        |              | This is a path that must start with the root node.  |^
| `dest`   | string | **REQUIRED** | The destination path.  The path must start with the |
|          |        |              | root node.                                          |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
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
```
///

/// api-response-spec
    open: True

| Field         |  Type  | Description                                             |
| ------------- | :----: | ------------------------------------------------------- |
| `item`        | object | A `Destination Item` object.                            |
|               |        | #move-dest-item-spec                                    |+
| `source_item` | object | A `Source Item` object.                                 |
|               |        | #move-source-item-spec                                  |+
| `action`      | string | A description of the action taken.  Will be `move_file` |
|               |        | if a file was moved or `move_dir` if a directory was    |^
|               |        | moved.                                                  |^

| Field         |  Type  | Description                                               |
| ------------- | :----: | --------------------------------------------------------- |
| `root`        | string | The root node of the destination file or directory.       |
| `path`        | string | The path, relative to the root node, of the destination   |
|               |        | file or directory.                                        |^
| `modified`    | float  | The last modified time of the destination file or         |
|               |        | directory.  This is expressed in Unix Time (seconds).     |^
| `size`        |  int   | The size, in bytes, of the destination file or directory. |
| `permissions` | string | The permissions available on the destination file or      |
|               |        | directory.                                                |^
{ #move-dest-item-spec } Destination Item

| Field  |  Type  | Description                                                   |
| ------ | :----: | ------------------------------------------------------------- |
| `root` | string | The root node of the source file or directory that was moved. |
| `path` | string | The path, relative to the root node, of the source file or    |
|        |        | directory that was moved.                                     |^
{ #move-source-item-spec } Source Item

///


## Copy a file or directory

Copies a file or directory from one location to another.  A successful copy has
the same prerequisites as a move with one exception, a copy may complete if the
source file or directory is loaded by the `virtual_sdcard`.

```{.http .apirequest title="HTTP Request"}
POST /server/files/copy
Content-Type: application/json

{
    "source": "gcodes/my_file.gcode",
    "dest": "gcodes/new_dir/my_file.gcode"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.copy",
    "params": {
        "source": "gcodes/my_file.gcode",
        "dest": "gcodes/new_dir/my_file.gcode"
    },
    "id": 5623
}
```

/// api-parameters
    open: True

| Name     |  Type  | Default      | Description                                         |
| -------- | :----: | ------------ | --------------------------------------------------- |
| `source` | string | **REQUIRED** | The source file or directory to copy.               |
|          |        |              | This is a path that must start with the root node.  |^
| `dest`   | string | **REQUIRED** | The destination path.  The path must start with the |
|          |        |              | root node.                                          |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "item": {
        "root": "gcodes",
        "path": "subdir/my_file.gcode",
        "modified": 1676940082.8595376,
        "size": 384096,
        "permissions": "rw"
    },
    "action": "create_file"
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                             |
| -------- | :----: | ------------------------------------------------------- |
| `item`   | object | A `Destination Item` object.                            |
|          |        | #copy-dest-item-spec                                    |+
| `action` | string | A description of the action taken.  Expand for details. |
|          |        | #copy-action-desc                                       |+

| Field         |  Type  | Description                                               |
| ------------- | :----: | --------------------------------------------------------- |
| `root`        | string | The root node of the destination file or directory.       |
| `path`        | string | The path, relative to the root node, of the destination   |
|               |        | file or directory.                                        |^
| `modified`    | float  | The last modified time of the destination file or         |
|               |        | directory.  This is expressed in Unix Time (seconds).     |^
| `size`        |  int   | The size, in bytes, of the destination file or directory. |
| `permissions` | string | The permissions available on the destination file or      |
|               |        | directory.                                                |^
{ #copy-dest-item-spec } Destination Item

| Name          | Description                                             |
| ------------- | ------------------------------------------------------- |
| `create_file` | A new file was created by the copy operation.           |
| `modify_file` | An existing file was modified (overwritten) by the copy |
|               | operation.                                              |^
| `create_dir`  | A new directory was created by the copy operation.  In  |
|               | addition, children files and directories may have been  |^
|               | created.                                                |^
{ #copy-action-desc }
///

## Create a ZIP archive

Creates a `zip` file consisting of one or more files.

```{.http .apirequest title="HTTP Request"}
POST /server/files/zip
Content-Type: application/json

{
    "dest": "config/error_logs.zip",
    "items": [
        "config/printer.cfg",
        "logs",
        "gcodes/subfolder"
    ],
    "store_only": false
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.zip",
    "params": {
        "dest": "config/error_logs.zip",
        "items": [
            "config/printer.cfg",
            "logs",
            "gcodes/subfolder"
        ],
        "store_only": false
    },
    "id": 5623
}
```

/// api-parameters
    open: True

| Name         |   Type   | Default                             | Description                                |
| ------------ | :------: | ----------------------------------- | ------------------------------------------ |
| `dest`       |  string  | `config/collection-{timestamp}.zip` | Path to the destination archive file.      |
|              |          |                                     | The path must begin with a valid "root"    |^
|              |          |                                     | that has write permission.                 |^
| `items`      | [string] | **REQUIRED**                        | An array of paths indicating the items     |
|              |          |                                     | to be included in the archive.  Each       |^
|              |          |                                     | path must start with a valid root. An      |^
|              |          |                                     | item may be a file or directory.           |^
| `store_only` |   bool   | `false`                             | When set to `true` the contents of the zip |
|              |          |                                     | archive are not compressed.  Otherwise the |^
|              |          |                                     | `deflation` algorithm will be used to      |^
|              |          |                                     | compress the contents.                     |^


///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "destination": {
        "root": "config",
        "path": "error_logs.zip",
        "modified": 1676984423.8892415,
        "size": 420,
        "permissions": "rw"
    },
    "action": "zip_files"
}
```
///

/// api-response-spec
    open: True

| Field         |  Type  | Description                                                |
| ------------- | :----: | ---------------------------------------------------------- |
| `destination` | object | A `Zip Destination` object containing details about the    |
|               |        | archived file.                                             |^
|               |        | #zip-destination-spec                                      |+
| `action`      | string | The action taken be the file manager. Will be `zip_files`. |

| Field         |  Type  | Description                                                         |
| ------------- | :----: | ------------------------------------------------------------------- |
| `root`        | string | The root node of the destination file or directory.                 |
| `path`        | string | The path of the zip archive, relative to the `root`.                |
| `modified`    | float  | The last modified time in unix time.                                |
| `size`        |  int   | The size of the file in bytes.                                      |
| `permissions` | string | The available [permissions](#file-permissions-desc) of the archive. |
{ #zip-destination-spec } Zip Destination

///

## File download
Retrieves file `filename` at root `root`.  The `filename` must include
the relative path if it is not in the root folder.

```{.http .apirequest title="HTTP Request"}
GET /server/files/{root}/{filename}
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// api-response-spec
    open: True

The body of the response contains the contents of the requested file.

///

## File upload
Upload a file.  Currently files may be uploaded to the `gcodes` or `config`
roots, with `gcodes` being the default.  If one wishes to upload
to a subdirectory, the path may be added to the upload's file name
(relative to the root). If the directory does not exist an error will be
returned.  Alternatively, the `path` form argument may be set, as explained
below.

```{.http .apirequest title="HTTP Request"}
POST /server/files/upload`
Content-Type: multipart/form-data

------FormBoundaryemap3PkuvKX0B3HH
Content-Disposition: form-data; name="file"; filename="myfile.gcode"
Content-Type: application/octet-stream

<binary data>
------FormBoundaryemap3PkuvKX0B3HH--
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// api-parameters
    open: True

The file data must be included in the request's body as `multipart/form-data`
(ie: `<input type="file">`).  The following *optional* arguments may also be
added to the form-data:

| Name       |  Type  | Default  | Description                                                    |
| ---------- | :----: | -------- | -------------------------------------------------------------- |
| `root`     | string | `gcodes` | The root location in which to upload the file.  Currently      |
|            |        |          | this may only be `gcodes` or `config`.                         |^
| `path`     | string |          | An optional path, relative to the `root`, indicating a         |
|            |        |          | subfolder in which to save the file.  If the subfolder does    |^
|            |        |          | not exist it will be created.                                  |^
| `checksum` | string |          | An optional SHA256 hex digest calculated by the client for     |
|            |        |          | the uploaded file.  If this argument is supplied the server    |^
|            |        |          | will compare it to its own checksum calculation after the      |^
|            |        |          | upload has completed.  A checksum mismatch will result in a    |^
|            |        |          | 422 error.                                                     |^
| `print`    | string | `false`  | Available only for files uploaded to the `gcodes` root.  When  |
|            |        |          | set to `true` Moonraker will command Klippy to start the print |^
|            |        |          | after the upload has successfully completed.                   |^

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "item": {
        "path": "Lock Body Shim 1mm_0.2mm_FLEX_MK3S_2h30m.gcode",
        "root": "gcodes",
        "modified": 1676984527.636818,
        "size": 71973,
        "permissions": "rw"
    },
    "print_started": false,
    "print_queued": false,
    "action": "create_file"
}
```
///

/// api-response-spec
    open: True

| Field           |  Type  | Description                                                           |
| --------------- | :----: | --------------------------------------------------------------------- |
| `item`          | object | An `Uploaded Item` object.                                            |
|                 |        | #uploaded-item-spec                                                   |+
| `print_started` |  bool  | Set to `true` if the uploaded file has successfully started printing. |
| `print_queued ` |  bool  | Set to `true` if the uploaded file has been queued for printing       |
|                 |        | at a later time.                                                      |^
| `action`        | string | Action taken by the file manager. Will always be "create_file".       |

| Field         |  Type  | Description                                                         |
| ------------- | :----: | ------------------------------------------------------------------- |
| `path`        | string | The path of the uploaded file, relative to the `root`.              |
| `root`        | string | The root node of the destination file or directory.                 |
| `modified`    | float  | The last modified time in unix time.                                |
| `size`        |  int   | The size of the file in bytes.                                      |
| `permissions` | string | The available [permissions](#file-permissions-desc) of the archive. |
{ #uploaded-item-spec } Uploaded Item

In addition to the above returned object, all successful uploads will respond with
a 201 response code and set the `Location` response header to the full path of
the uploaded file.
///

## File delete
Delete a file in the requested root.  If the file exists in a subdirectory,
its relative path must be part of the `{filename}` argument.

```{.http .apirequest title="HTTP Request"}
DELETE /server/files/{root}/{filename}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.files.delete_file",
    "params": {
        "path": "{root}/{filename}"
    },
    "id": 1323
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "item": {
        "path": "Lock Body Shim 1mm_0.2mm_FLEX_MK3S_2h30m.gcode",
        "root": "gcodes",
        "size": 0,
        "modified": 0,
        "permissions": ""
    },
    "action": "delete_file"
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                                     |
| -------- | :----: | --------------------------------------------------------------- |
| `item`   | object | A `Deleted Item` object.                                        |
|          |        | #deleted-item-spec                                              |+
| `action` | string | Action taken by the file manager. Will always be "delete_file". |

| Field         |  Type  | Description                                                          |
| ------------- | :----: | -------------------------------------------------------------------- |
| `path`        | string | The path of the deleted file, relative to the `root`.                |
| `root`        | string | The root node of the deleted file or directory.                      |
| `modified`    | float  | The last modified time in unix time.  Should always be 0 as the file |
|               |        | no longer exists.                                                    |^
| `size`        |  int   | The size of the file in bytes. Should always be 0 as the file no     |
|               |        | longer exits.                                                        |^
| `permissions` | string | The available [permissions](#file-permissions-desc) of the archive.  |
|               |        | should always be an empty string as the file no longer exists.       |^
{ #deleted-item-spec } Deleted Item

///

## Download klippy.log

/// Note
Logs are now available in the `logs` root.  Front ends should consider
presenting all available logs using "file manager" type of UI.  That said,
If Klipper has not been configured to write logs in the `logs` root then
this endpoint is available as a fallback.
///

```{.http .apirequest title="HTTP Request"}
GET /server/files/klippy.log
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// api-response-spec
    open: True

The body of the response contains contents of `klippy.log`.

///

## Download moonraker.log

/// Note
Logs are now available in the `logs` root.  Front ends should consider
presenting all available logs using "file manager" type of UI.  That said,
If Moonraker has not been configured to write logs in the `logs` root then
this endpoint is available as a fallback.
///

```{.http .apirequest title="HTTP Request"}
GET /server/files/moonraker.log
```

```{.json .apirequest title="JSON-RPC Request"}
Not Available
```

/// api-response-spec
    open: True

The body of the response contains the contents of `moonraker.log`.

///
