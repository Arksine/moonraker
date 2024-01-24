# Extensions

Moonraker has limited support for 3rd party extensions through the
use of its API.  Extensions must establish a Websocket or Unix Socket
connection and [identify](./server.md#identify-connection) themselves
as an `agent`.

The endpoints in this section can be broken down into two categories:

- Endpoints used by Front Ends and other clients to manage and manipulate
  extensions.
- Endpoints specific to agents that provide functional enhancements not
  available to other client types.

## Extension Management

### List Extensions

Returns a list of all available extensions.  Currently Moonraker can only
be officially extended through connected `agents`.

```{.http .apirequest title="HTTP Request"}
GET /server/extensions/list
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.extensions.list",
    "id": 4564
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "agents": [
        {
            "name": "moonagent",
            "version": "0.0.1",
            "type": "agent",
            "url": "https://github.com/arksine/moontest"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field    |   Type   | Description                                         |
| -------- | :------: | --------------------------------------------------- |
| `agents` | [object] | An array of [Agent Info](#agent-info-spec) objects. |

| Field     |  Type  | Description                                        |
| --------- | :----: | -------------------------------------------------- |
| `name`    | string | The name provided by the registered agent.         |
| `version` | string | The version of the software reported by the agent. |
| `type`    | string | The client type.  Will always be `agent`.          |
| `url`     | string | A url to the agent software's webpage.             |
{ #agent-info-spec } Agent Info

///

### Call an extension method

This endpoint may be used to call a method on a connected agent.
The request effectively relays a JSON-RPC request from a front end
or other client to the agent.  Agents should document their
available methods so Moonraker client developers can interact
with them.

```{.http .apirequest title="HTTP Request"}
POST /server/extensions/request
Content-Type: application/json

{
    "agent": "moonagent",
    "method": "moontest.hello_world",
    "arguments": {"argone": true, "argtwo": 9000}
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.extensions.request",
    "params":{
        "agent": "moonagent",
        "method": "moontest.hello_world",
        "arguments": {"argone": true, "argtwo": 9000}
    },
    "id": 4564
}
```

Parameters:

/// api-parameters
    open: True

| Name        |      Type       | Default      | Description                      |
| ----------- | :-------------: | ------------ | -------------------------------- |
| `agent`     |     string      | **REQUIRED** | The name of the registered agent |
|             |                 |              | hosting the requested method.    |^
| `method`    |     string      | **REQUIRED** | The name of the method to call.  |
| `arguments` | array \| object | null         | The arguments to send with the   |
|             |                 |              | method.  This may be an array    |^
|             |                 |              | containing positional arguments  |^
|             |                 |              | or an object containing keyword  |^
|             |                 |              | arguments.  A value of `null`    |^
|             |                 |              | will omit arguments from the     |^
|             |                 |              | request.                         |^

///

/// api-response-spec
    open: True

The result received from the agent will be returned directly.  See
the agent's documentation for response specifications

///

## Agent specific endpoints

/// Note
These endpoints are only available to connections that have
identified themselves as an `agent` type.
///

### Send an agent event

Sends a [JSON-RPC notification](./jsonrpc_notifications.md#agent-events)
containing the supplied event info to all of Moonraker's persistent
connections.

```{.http .apirequest title="HTTP Request"}
Not Available
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "connection.send_event",
    "params":{
        "event": "my_event",
        "data": {"my_arg": "optional data"}
    }
}
```

/// api-parameters
    open: True

| Name    |  Type  | Default      | Description                                  |
| ------- | :----: | ------------ | -------------------------------------------- |
| `event` | string | **REQUIRED** | The name of the event.  This may be any      |
|         |        |              | name other than those reserved by Moonraker. |^
| `data`  |  any   | undefined    | The data to send with the event. This can be |
|         |        |              | any valid JSON decodable value.  If omitted  |^
|         |        |              | no data is sent with the event.              |^

//// Note
The `connected` and `disconnected` events are reserved for use
by Moonraker and may not be sent from agents.
////

///

```{.apiresponse title="Response when JSON-RPC 'id' present"}
ok
```

/// note
An agent may send an event without specifying the JSON-RPC `id` field.
In this case Moonraker will not return a response.
///


### Register a method with Klipper

Registers a "remote method" with Klipper that can be called
from GCode Macros.

```{.http .apirequest title="HTTP Request"}
Not Available
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "connection.register_remote_method",
    "params": {
        "method_name": "firemon_alert_heated"
    }
}
```

/// api-parameters
    open: True

| Name          |  Type  | Default      | Description                                   |
| ------------- | :----: | ------------ | --------------------------------------------- |
| `method_name` | string | **REQUIRED** | The name of the remote method to register     |
|               |        |              | with Klipper.  It is recommended for agents   |^
|               |        |              | to use a unique identifier, such as a prefix, |^
|               |        |              | to prevent collisions with other remote       |^
|               |        |              | methods registered with Klipper.              |^

///

```{.apiresponse title= Response"}
ok
```

/// Note
Methods registered by agents will persist until the agent disconnects.
Upon connection it is only necessary that they register their desired
methods once.
///

#### Remote Method Example

Presume an application named `firemon` has connected to Moonraker's websocket
and identified itself as an `agent`. After identification it registers a
remote method named `firemon_alert_heated` using the JSON-RPC request
example above.

In addition, the user has following `gcode_macro` configured in `printer.cfg`:

```ini
# printer.cfg

[gcode_macro ALERT_HEATED]
gcode:
  {% if not params %}
    {action_call_remote_method("firemon_alert_heated")}
  {% else %}
    {% set htr = params.HEATER|default("unknown") %}
    {% set tmp = params.TEMP|default(0)|float %}
    {action_call_remote_method(
        "firemon_alert_heated", heater=htr, temp=tmp)}
  {% endif %}


```

When the `ALERT_HEATED HEATER=extruder TEMP=200` gcode is executed by Klipper,
the agent will receive the following JSON-RPC request from Moonraker:

```{.json .apiresponse title="Remote Method Call"}
{
    "jsonrpc": "2.0",
    "method": "firemon_alert_heated",
    "params": {
        "heater": "extruder",
        "temp": 200
    }
}
```

When the `ALERT_HEATED` gcode is executed with no parameters, the agent will
receive the following JSON-RPC request from Moonraker:

```{.json .apiresponse title="Remote Method Call"}
{
    "jsonrpc": "2.0",
    "method": "monitor_alert_heated"
}
```

/// Note
Remote methods called from Klipper never contain the JSON-RPC "id" field,
as Klipper does not accept return values to remote methods.
///
