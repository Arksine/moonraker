## Components

Components in Moonraker are used to extend Moonraker's functionality,
similar to "extras" in Klipper.  Moonraker divides components into
two categories, "core" components and "optional" components.  A core
component gets its configuration from the `[server]` section and is
loaded when Moonraker starts.  For example, the `file_manager` is a
core component.   If a core component fails to load Moonraker will
exit with an error.

Optional components must be configured in `moonraker.conf`.  If they
have no specific configuration, a bare section, such as `[octoprint_compat]`
must be present in `moonraker.conf`.  Unlike with core components,
Moonraker will still start if an optional component fails to load.
Its failed status will be available for clients to query and present
to the user.

### Basic Example

Components exist in the `components` directory.  The below example
shows how an `example.py` component might look:
```python
# Example Component
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

class Example:
    def __init__(self, config):
        self.server = config.get_server()
        self.name = config.get_name()

        # Raises an error if "example_int_option" is not configured in
        # the [example] section
        self.example_int_opt = config.getint("example_int_option")

        # Returns a NoneType if "example_float_option is not configured
        # in the config
        self.example_float_opt = config.getfloat("example_float_option", None)

        self.server.register_endpoint("/server/example", ['GET'],
                                      self._handle_example request)

    async def request_some_klippy_state(self):
        klippy_apis = self.server.lookup_component('klippy_apis')
        return await klippy_apis.query_objects({'print_stats': None})

    async def _handle_example_request(self, web_request):
        web_request.get_int("required_reqest_param")
        web_request.get_float("optional_request_param", None)
        state = await self.request_some_klippy_state()
        return {"example_return_value": state}

def load_component(config):
    return Example(config)

```
If you have created a "Klippy extras" module then the above should look
look familiar.  Moonraker attempts to use similar method for adding
extensions, making easier Klipper contributors to add new functionality
to Moonraker.   Be aware that there is no "Reactor" object in Moonraker,
it uses `asyncio` for coroutines.  Like Klippy, you should not write
code that blocks the main thread.

### The ConfigWrapper Object

As shown above, each component is passed a config object.  This object
will be a `ConfigWrapper` type, which is an object that wraps a
configuration section to simply access to the native `ConfigParser`.
A `ConfigWrapper` should never be directly instantiated.

#### *ConfigWrapper.get_server()*

Returns the primary [server](#the-server-object) instance.

#### *ConfigWrapper.get_name()*

Returns the configuration section name associated with this `ConfigWrapper`.

#### *ConfigWrapper.get(option_name, default=Sentinel)*

Returns the value of the option`option_name` as a string.  If
the option does not exist, returns `default`.  If `default` is
not provided raises a `ConfigError`.

#### *ConfigWrapper.getint(option_name, default=Sentinel)*

Returns the value of the option`option_name` as an integer.  If
the option does not exist, returns `default`.  If `default` is
not provided raises a `ConfigError`.

#### *ConfigWrapper.getfloat(option_name, default=Sentinel)*

Returns the value of the option`option_name` as a float.  If
the option does not exist, returns `default`.  If `default` is
not provided raises a `ConfigError`.

#### *ConfigWrapper.getboolean(option_name, default=Sentinel)*

Returns the value of the option`option_name` as a boolean.  If
the option does not exist, returns `default`.  If `default` is
not provided raises a `ConfigError`.

#### *ConfigWrapper.has_section(section_name)*

Returns True if a section matching `section_name` is in the configuration,
otherwise False.

Note that a ConfigWrapper object also implements `__contains__`,
which is an alias for `has_section`, ie: `section_name in config_instance`

#### *ConfigWrapper.getsection(section_name)*

Returns a Config object for the section matching `section_name`.  If the
section does not exist in the configuration raises a `ConfigError`.

Note that a ConfigWrapper object also implements `__getitem__`,
which is an alias for `get_section`, ie: `config_instance[section_name]`

#### *ConfigWrapper.get_options()*

Returns a dict mapping options to values for all options in the Config
object.

#### *ConfigWrapper.get_prefix_sections(prefix)*

Returns a list section names in the configuration that start with `prefix`.
These strings can be used to retreve ConfigWrappers via
[get_section()](#configwrappergetsectionsection_name).

### The Server Object

The server instance represents the central management object in Moonraker.
It can be used to register endpoints, register notifications, look up other
components, send events, and more.

#### *Server.lookup_component(component_name, default=Sentinel)*

Attempts to look up a loaded component, returning the result.  If
the component has not been loaded, `default` will be returned.
If `default` is not provided a `ServerError` will be raised.

#### *Server.load_component(config, component_name, default=Sentinel)*

Attempts to load an uninitialized component and returns the result.  It is
only valid to call this within a a component's `__init__()` method, and
should only be necessary if one optional component relies on another.  Core components will always be loaded before optional components, thus an optional
component may always call
[lookup_component()](#serverlookup_componentcomponent_name-defaultsentinel)
when it needs a reference to core component.

If the component fails to load `default` will be returned.  If `default`
is not provided a `ServerError` will be raised.

#### *Server.register_endpoint(uri, request_methods, callback, protocol=["http", "websocket"], wrap_result=True)*

Registers the supplied `uri` with the server.

The `request_methods` argument should be a list of strings containing any
combination of `GET`, `POST`, and `DELETE`.

The `callback` is executed when a request matching the `uri` and a
`request_method` is received.  The callback function will be passed a
`WebRequest` object with details about the request.  This function
should be able of handling each registered `request_method`.  The
provided callback must be a coroutine.

The `protocol` is a list containing any combination of `http` and `websocket`.
If `websocket` is selected associated JSON-RPC methods will be generated based
on what is supplied by the `uri` and `request_methods` argument. A unique
JSON_RPC method is generated for each request method.  For example:
```python
self.server.register_endpoint("/server/example", ["POST"], self._handle_request)
```
would register a JSON-RPC method like:
```
server.example
```

However, if multiple requests methods are supplied, the generated JSON-RPC
methods will differ:
```python
self.server.register_endpoint("/server/example", ["GET", "POST", "DELETE"],
                              self._handle_request)
```
would register:
```
server.get_example
server.post_example
server.delete_example
```

The `wrap_result` argument applies only to the `http` protocol.  In Moonraker
all http requests return a result with a JSON body.  By default, the value returned
by a `callback` is wrapped in a dict:
```python
{"result": return_value}
```
It is only necessary to set this to false if you need to return a body that
does not match this result.  For example, the `[octoprint_compat]` component
uses this functionality to return results in a format that match what
Octoprint itself would return.

#### *Server.register_event_handler(event, callback)*

Registers the provided `callback` method to be executed when the
provided `event` is sent.  The callback may be a coroutine, however it
is not required.

#### *Server.send_event(event, \*args)*

Emits the event named `event`, calling all callbacks registered to the
event.  All positional arguments in `*args` will be passed to each
callback.  Event names should be in the form of
`"module_name:event_description"`.

#### *Server.register_notification(event_name, notify_name=None)*

Registers a websocket notification to be pushed when `event_name`
is emitted.  By default JSON-RPC notifcation sent will be in the form of
`notify_{event_description}`.  For example, when the server sends the
`server:klippy_connected` event, the JSON_RPC notification will be
`notify_klippy_connected`.

If a `notify_name` is provided it will override the `{event_description}`
extracted from the `event_name`.  For example, if the `notify_name="kconnect`
were specfied when registering the `server:klippy_connected` event, the
websocket would emit a `notify_kconnect` notification.

#### *Server.get_host_info()*

Returns a tuple of the current host name of the PC and the port Moonraker
is serving on.

#### *Server.get_klippy_info()*

Returns a dict containing the values from the most recent `info` request to
Klippy.  If Klippy has never connected this will be an empty dict.

### The WebRequest Object

All callbacks registered with the
[register_endpoint()](#serverregister_endpointuri-request_methods-callback-protocolhttp-websocket-wrap_resulttrue)
method are passed a WebRequest object when they are executed.  This object
contains information about the request including its endpoint name and arguments
parsed from the request.

#### *WebRequest.get_endpoint()*

Returns the URI registered with this request, ie: `/server/example`.

#### *WebRequest.get_action()*

Returns the request action, which is synonomous with its HTTP request
method.  Will be either `GET`, `POST`, or `DELETE`.  This is useful
if your endpoint was registered with multiple request methods and
needs to handle each differently.

#### *WebRequest.get_connection()*

Returns the associated Websocket connection ID.  This will be `None`
for HTTP requests when no associated websocket is connected to
the client.

#### *WebRequest.get_args()*

Returns a reference to the entire argument dictionary.  Useful if
one request handler needs to preprocess the arguments before
passing the WebRequest on to another request handler.

#### *WebRequest.get(key, default=Sentinel)*

Returns the request argument at the provided `key`.  If the key is not
present `default` will be returned. If `default` is not provided a
`SeverError` will be raised.

#### *WebRequest.get_str(key, default=Sentinel)*

Retrieves the request argument at the provided `key` and converts it
to a string, returning the result. If the key is not present the `default`
value will be returned.  If `default` is not provided or if the attempt at
type conversion fails a `SeverError` will be raised.

#### *WebRequest.get_int(key, default=Sentinel)*

Retrieves the request argument at the provided `key` and converts it
to an integer, returning the result. If the key is not present the `default`
value will be returned.  If `default` is not provided or if the attempt at
type conversion fails a `SeverError` will be raised.

#### *WebRequest.get_float(key, default=Sentinel)*

Retrieves the request argument at the provided `key` and converts it
to a float, returning the result. If the key is not present the `default`
value will be returned.  If `default` is not provided or if the attempt at
type conversion fails a `SeverError` will be raised.

#### *WebRequest.get_boolean(key, default=Sentinel)*

Retrieves the request argument at the provided `key` and converts it
to a boolean, returning the result. If the key is not present the `default`
value will be returned.  If `default` is not provided or if the attempt at
type conversion fails a `SeverError` will be raised.
