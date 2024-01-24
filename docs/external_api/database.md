# Database Management

The following endpoints provide access to Moonraker's internal sqlite database.
The primary table exposed to clients is divided into `namespaces`.  Each client
may define its own namespace to store information.  From the client's point of
view, a namespace is an `object`.  Items in the database are accessed by providing
a namespace and a key.  A key may be specified as string, where a "." is a
delimiter to access nested fields. Alternatively the key may be specified
as an array of strings, where each string references a nested field.
This is useful for scenarios where a namespace contains fields that include
a "." character, such as a file name.

/// note
Moonraker reserves several namespaces for internal use. Clients may read from
these namespaces but they may not modify them.
///

For example, assume the following object is stored in the "superclient"
namespace:

```json
{
    "settings": {
        "console": {
            "enable_autocomplete": true
        }
    },
    "theme": {
        "background_color": "black"
    }
}
```
One may access the `enable_autocomplete` field by supplying `superclient` as
the `namespace` argument and `settings.console.enable_autocomplete` or
`["settings", "console", "enable_autocomplete"]` as the `key` argument for
the request.  The entire settings object could be accessed by providing
`settings` or `["settings"]` as the `key` argument.  The entire namespace
may be read by omitting the `key` argument, however as explained below it
is not possible to modify a namespace without specifying a key.

## List Database Info

Lists all namespaces with read and/or write access.  Also lists database
backup files.

```{.http .apirequest title="HTTP Request"}
GET /server/database/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.list",
    "id": 8694
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "namespaces": [
        "gcode_metadata",
        "webcams",
        "update_manager",
        "announcements",
        "database",
        "moonraker"
    ],
    "backups": [
        "sqldb-backup-20240513-134542.db",
        "testbackup.db",
        "testbackup2.db"
    ]
}
```
///

/// api-response-spec
    open: True

| Field        |   Type   | Description                                          |
| ------------ | :------: | ---------------------------------------------------- |
| `namespaces` | [string] | An array of namespaces registered with the database  |
|              |          | that may be read by clients.                         |^
| `backups`    | [string] | An array of database backup filenames that have been |
|              |          | created.                                             |^

///

## Get Database Item

Retrieves an item from a specified namespace. The `key` argument may be
omitted, in which case an object representing the entire namespace will
be returned in the `value` field.  If the `key` is provided and does not
exist in the database an error will be returned.

```{.http .apirequest title="HTTP Request"}
GET /server/database/item?namespace={namespace}&key={key}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.get_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 5644
}
```

/// api-parameters
    open: True

| Name        |        Type        | Default      | Description                                |
| ----------- | :----------------: | ------------ | ------------------------------------------ |
| `namespace` |       string       | **REQUIRED** | The namespace of the item to retrieve.     |
| `key`       | string \| [string] | null         | The key indicating the field or fields     |
|             |      \| null       |              | within the namespace to retrieve.  May     |^
|             |                    |              | be a string, where nested fields are       |^
|             |                    |              | separated by a ".", or a list of strings.  |^
|             |                    |              | If the key is omitted the entire namespace |^
|             |                    |              | will be returned.                          |^

///


/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "namespace": "moonraker",
    "key": "file_manager.metadata_version",
    "value": 2
}
```
///

/// api-response-spec
    open: True

| Field       |        Type        | Description                                       |
| ----------- | :----------------: | ------------------------------------------------- |
| `namespace` |       string       | The namespace of the returned item.               |
| `key`       | string \| [string] | The key indicating the requested field(s).        |
|             |      \| null       |                                                   |^
| `value`     |        any         | The value of the requested item.  This can be any |
|             |                    | valid JSON type.                                  |^

///

## Add Database Item
Inserts an item into the database.  If the `namespace` does not exist
it will be created.  If the `key` specifies a nested field, all parents
will be created if they do not exist.  If the key exists it will be
overwritten with the provided `value`.  The `key` parameter must be provided,
as it is not possible to assign a value directly to a namespace.

/// note
If the request parameters are placed in the query string and the `value`
is not a string type, then `value` argument must provide a
[type hint](./introduction.md#query-string-type-hints).  It is strongly
recommended to put parameters in the body of the request wrapped in a
JSON object.
///

```{.http .apirequest title="HTTP Request"}
POST /server/database/item
Content-Type: application/json

{
    "namespace": "my_client",
    "key": "settings.some_count",
    "value": 100
}
```

/// api-parameters
    open: True

| Name        |        Type        | Default      | Description                               |
| ----------- | :----------------: | ------------ | ----------------------------------------- |
| `namespace` |       string       | **REQUIRED** | The namespace where the value             |
|             |                    |              | should be inserted.                       |^
| `key`       | string \| [string] | **REQUIRED** | The key indicating the field or fields    |
|             |                    |              | where the value should be inserted.       |^
| `value`     |        any         | **REQUIRED** | The value to insert in the database.  May |
|             |                    |              | be any valid JSON type.                   |^

///

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.post_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}",
        "value": 100
    },
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "namespace": "test",
    "key": "settings.some_count",
    "value": 9001
}
```
///

/// api-response-spec
    open: True

| Field       |        Type        | Description                                 |
| ----------- | :----------------: | ------------------------------------------- |
| `namespace` |       string       | The namespace where the value was inserted. |
| `key`       | string \| [string] | The key indicating the field or fields      |
|             |                    | where the value was inserted.               |^
| `value`     |        any         | The value inserted into the database.  May  |
|             |                    | be any valid JSON type.                     |^

///

## Delete Database Item

Deletes an item from a `namespace` at the specified `key`. If the key does not
exist in the namespace an error will be returned.  If the deleted item results
in an empty namespace, the namespace will be removed from the database.

```{.http .apirequest title="HTTP Request"}
DELETE /server/database/item?namespace={namespace}&key={key}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.delete_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 4654
}
```
/// api-parameters
    open: True

| Name        |        Type        | Default      | Description                            |
| ----------- | :----------------: | ------------ | -------------------------------------- |
| `namespace` |       string       | **REQUIRED** | The namespace where the item should be |
|             |                    |              | should be removed.                     |^
| `key`       | string \| [string] | **REQUIRED** | The key indicating the field or fields |
|             |                    |              | where the item should be removed.    |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "namespace": "test",
    "key": "settings.some_count",
    "value": 9001
}
```
///

/// api-response-spec
    open: True

| Field       |        Type        | Description                                |
| ----------- | :----------------: | ------------------------------------------ |
| `namespace` |       string       | The namespace containing the item removed. |
| `key`       | string \| [string] | The key indicating the field or fields     |
|             |                    | where the item was removed.                |^
| `value`     |        any         | The of the item at the removed field. May  |
|             |                    | be any valid JSON type.                    |^

///

## Compact Database

Compacts and defragments the the sqlite database using the `VACUUM` command.
This endpoint cannot be requested when Klipper is printing.

```{.http .apirequest title="HTTP Request"}
POST /server/database/compact
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.compact",
    "id": 4654
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "previous_size": 139264,
    "new_size": 122880
}
```
///

/// api-response-spec
    open: True

| Field           | Type | Description                                        |
| --------------- | :--: | -------------------------------------------------- |
| `previous_size` | int  | Size in bytes of the database prior to compaction. |
| `new_size`      | int  | Size in bytes of the database after compaction.    |

///

## Backup Database

Creates a backup of the current database.  The backup will be
created in the `<data_path>/backup/database/<filename>`.

This API cannot be requested when Klipper is printing.

```{.http .apirequest title="HTTP Request"}
POST /server/database/backup
Content-Type: application/json

{
    "filename": "sql-db-backup.db"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.post_backup",
    "params": {
        "filename": "sql-db-backup.db"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default                    | Description                |
| ---------- | :----: | -------------------------- | -------------------------- |
| `filename` | string | sqldb-backup-{timespec}.db | The file name of the saved |
|            |        |                            | backup file.               |^

//// note
The `{timespec}` of the default `filename` is in the following format:

`<year><month><day>-<hour><minute><second>`
////

///

```{.json .apiresponse title="Example Response"}
{
    "backup_path": "/home/test/printer_data/backup/database/sql-db-backup.db"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                                            |
| ------------- | :----: | ------------------------------------------------------ |
| `backup_path` | string | The complete absolute path where the backup was saved. |

///

## Delete a backup

Deletes a previously backed up database.

```{.http .apirequest title="HTTP Request"}
DELETE /server/database/backup?filename=sql-db-backup.db
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.delete_backup",
    "params": {
        "filename": "sql-db-backup.db"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default      | Description                                    |
| ---------- | :----: | ------------ | ---------------------------------------------- |
| `filename` | string | **REQUIRED** | The name of the backup file to delete. Must be |
|            |        |              | a valid filename reported by the               |^
|            |        |              | [database list endpoint](#list-database-info). |^

///

```{.json .apiresponse title="Example Response"}
{
    "backup_path": "/home/test/printer_data/backup/database/sql-db-backup.db"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                                              |
| ------------- | :----: | -------------------------------------------------------- |
| `backup_path` | string | The complete absolute path where the backup was removed. |

///


## Restore Database

Restores a previously backed up sqlite database file. The backup
must be located at `<data_path>/backup/database/<filename>`. The
`<filename>` must be a valid filename reported in by the
[database list](#list-database-info) API.

This API cannot be requested when Klipper is printing.

/// Note
Moonraker will restart immediately after this request is processed.
///

```{.http .apirequest title="HTTP Request"}
POST /server/database/restore
Content-Type: application/json

{
    "filename": "sql-db-backup.db"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.database.restore",
    "params": {
        "filename": "sql-db-backup.db"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default      | Description                                     |
| ---------- | :----: | ------------ | ----------------------------------------------- |
| `filename` | string | **REQUIRED** | The name of the backup file to restore. Must be |
|            |        |              | a valid filename reported by the                |^
|            |        |              | [database list endpoint](#list-database-info).  |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "restored_tables": [
        "table_registry",
        "namespace_store",
        "authorized_users",
        "job_history",
        "job_totals"
    ],
    "restored_namespaces": [
        "database",
        "fluidd",
        "gcode_metadata",
        "mainsail",
        "moonraker",
        "update_manager",
        "webcams"
    ]
}
```
///

/// api-response-spec
    open: True

| Field                 |   Type   | Description                                 |
| --------------------- | :------: | ------------------------------------------- |
| `restored_tables`     | [string] | An array of table names that were recovered |
|                       |          | after the restore operation.                |^
| `restored_namespaces` | [string] | An array of namespaces that were recovered  |
|                       |          | after the restore operation.                |^

///

## Debug endpoints

Below are a number of debug endpoints available when Moonraker has been
launched with [debug features enabled](../installation.md#command-line-usage).
Front ends should not rely on these endpoints in production releases, however
they may be useful during development.  Developers writing extensions and/or
additions to Moonraker may also find these endpoints useful.

/// Warning
Debug endpoints may expose security vulnerabilities.  They should only be
enabled by developers on secured machines.
///

### List Database Info (debug)

Debug version of the [List Database Info](#list-database-info) endpoint.
Returns all namespaces, including those exclusively reserved for Moonraker.
In addition all registered SQL tables are reported.


```{.http .apirequest title="HTTP Request"}
GET /debug/database/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "debug.database.list",
    "id": 8694
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "namespaces": [
        "gcode_metadata",
        "webcams",
        "update_manager",
        "announcements",
        "database",
        "moonraker"
    ],
    "backups": [
        "sqldb-backup-20240513-134542.db",
        "testbackup.db",
        "testbackup2.db"
    ],
    "tables": [
        "job_history",
        "job_totals",
        "namespace_store",
        "table_registry",
        "authorized_users"
    ]
}
```
///

/// api-response-spec
    open: True

| Field        |   Type   | Description                                              |
| ------------ | :------: | -------------------------------------------------------- |
| `namespaces` | [string] | An array of all namespaces registered with the database. |
| `backups`    | [string] | An array of database backup filenames that have been     |
|              |          | created.                                                 |^
| `tables`     | [string] | An array of tables created within the database.          |

///

### Get Database Item (debug)

Debug version of the [Get Database Item](#get-database-item) endpoint.
Keys within protected and forbidden namespaces may be read.

```http title="HTTP Request"
GET /debug/database/item?namespace={namespace}&key={key}
```
```json title="JSON-RPC Request"
{
    "jsonrpc": "2.0",
    "method": "debug.database.get_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 5644
}
```

See the [Get Database Item](#get-database-item) endpoint for the
`Parameter Specification`, `Example Response`, and `Response Specification`.

### Add Database Item (debug)

Debug version of the [Add Database Item](#add-database-item) endpoint.
Keys within protected and forbidden namespaces may be inserted.

/// Warning
Modifying protected namespaces outside of Moonraker can result in
broken functionality and is not supported for production environments.
Issues opened with reports/queries related to this endpoint will be
redirected to this documentation and closed.
///

```{.http .apirequest title="HTTP Request"}
POST /debug/database/item
Content-Type: application/json

{
    "namespace": "my_client",
    "key": "settings.some_count",
    "value": 100
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "debug.database.post_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}",
        "value": 100
    },
    "id": 4654
}
```

See the [Add Database Item](#add-database-item) endpoint for the
`Parameter Specification`, `Example Response`, and `Response Specification`.

### Delete Database Item (debug)

Debug version of [Delete Database Item](#delete-database-item).  Keys within
protected and forbidden namespaces may be removed.

/// Warning
Modifying protected namespaces outside of Moonraker can result in
broken functionality and is not supported for production environments.
Issues opened with reports/queries related to this endpoint will be
redirected to this documentation and closed.
///

```{.http .apirequest title="HTTP Request"}
DELETE /debug/database/item?namespace={namespace}&key={key}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "debug.database.delete_item",
    "params": {
        "namespace": "{namespace}",
        "key": "{key}"
    },
    "id": 4654
}
```

See the [Delete Database Item](#delete-database-item) endpoint for the
`Parameter Specification`, `Example Response`, and `Response Specification`.


### Get Database Table

Requests all the contents of a specified table.

```{.http .apirequest title="HTTP Request"}
GET /debug/database/table?table=job_history
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "debug.database.table",
    "params": {
        "table": "job_history"
    },
    "id": 4654
}
```

/// api-parameters
    open: True

| Name    |  Type  | Default      | Description                       |
| ------- | :----: | ------------ | --------------------------------- |
| `table` | string | **REQUIRED** | The name of the table to request. |

///



Returns:

An object with the table's name and a list of all rows contained
within the table.  The `rowid` will always be included for each
row, however it may be represented by an alias.  In the example
below the alias for `rowid` is `job_id`.

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "table_name": "job_history",
    "rows": [
        {
            "job_id": 1,
            "user": "No User",
            "filename": "active_test.gcode",
            "status": "completed",
            "start_time": 1690749153.2661753,
            "end_time": 1690749173.076986,
            "print_duration": 0.0,
            "total_duration": 19.975574419135228,
            "filament_used": 0.0,
            "metadata": {
                "size": 211,
                "modified": 1635771217.0,
                "uuid": "627371e0-faa5-4ced-8bb4-7017d29226fa",
                "slicer": "Unknown",
                "gcode_start_byte": 8,
                "gcode_end_byte": 211
            },
            "auxiliary_data": [],
            "instance_id": "default"
        },
        {
            "job_id": 2,
            "user": "No User",
            "filename": "active_test.gcode",
            "status": "completed",
            "start_time": 1701262034.9242446,
            "end_time": 1701262054.7332363,
            "print_duration": 0.0,
            "total_duration": 19.990913168992847,
            "filament_used": 0.0,
            "metadata": {
                "size": 211,
                "modified": 1635771217.0,
                "uuid": "627371e0-faa5-4ced-8bb4-7017d29226fa",
                "slicer": "Unknown",
                "gcode_start_byte": 8,
                "gcode_end_byte": 211
            },
            "auxiliary_data": {
                "spool_ids": [
                    2
                ]
            },
            "instance_id": "default"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field        |   Type   | Description                                        |
| ------------ | :------: | -------------------------------------------------- |
| `table_name` |  string  | The name of the table requested.                   |
| `rows`       | [object] | An array of row objects.  The fields for each      |
|              |          | object are columns defined by the table schema.    |^
|              |          | The `rowid` will always be included for each row,  |^
|              |          | however it may be represented by an alias.  In the |^
|              |          | example above, `job_id` is an alias for `rowid`.   |^

///
