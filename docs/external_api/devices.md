# Switches, Sensors, and Devices

This document covers the API for managing various devices
through Moonraker.  It should be noted that the endpoints
here are only available when such devices are added to
Moonraker's configuration.

## Power Endpoints

Moonraker's `power` component enables switch-like device management.
Various device types are supported, including GPIOs and HTTP controlled
devices.

The endpoints in this section are available when
one or more `[power <device_name>]` sections are configured
in `moonraker.conf`.

### Get Device List

```{.http .apirequest title="HTTP Request"}
GET /machine/device_power/devices
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.devices",
    "id": 5646
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "devices": [
        {
            "device": "green_led",
            "status": "off",
            "locked_while_printing": true,
            "type": "gpio"
        },
        {
            "device": "printer",
            "status": "off",
            "locked_while_printing": false,
            "type": "tplink_smartplug"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field     |   Type   | Description                                                  |
| --------- | :------: | ------------------------------------------------------------ |
| `devices` | [object] | An array of [Power Device Status](#power-device-status-spec) |
|           |          | objects.                                                     |^

| Field                   |  Type  | Description                                            |
| ----------------------- | :----: | ------------------------------------------------------ |
| `device`                | string | The configured name of the device.                     |
| `status`                | string | The current [state](#power-state-desc) of the device. |
| `locked_while_printing` |  bool  | When set to `true` the power device status             |
|                         |        | may not be changed when Klipper is printing.           |^
| `type`                  | string | The [Device Type](#power-type-desc) of the            |
|                         |        | device.                                                |^
{ #power-device-status-spec } Power Device Status

| Device State | Description                      |
| ------------ | -------------------------------- |
| `on`         | The device is powered on.        |
| `off`        | The device is powered off.       |
| `init`       | The device is initializing.      |
| `error`      | The device encountered an error. |
{ #power-state-desc } Power Device State

| Device Type        | Description                                                |
| ------------------ | ---------------------------------------------------------- |
| `gpio`             | The device is controlled by a GPIO on the local machine.   |
| `klipper_device`   | The device is controlled by Klipper.                       |
| `tplink_smartplug` | The device is a TPLink Smartplug Device (aka Kasa Device.) |
| `tasmota`          | The device is a HTTP device running Tasmota firmware.      |
| `shelly`           | The device is a Shelly branded device (V1 API).            |
| `homeseer`         | The device is a device managed by HomeSeer.                |
| `homeassistant`    | The device is a device managed by Home Assistant.          |
| `loxonev1`         | The device is a Loxone V1 device.                          |
| `rf`               | The device is a RF device with a GPIO interface.           |
| `mqtt`             | The device is a device available over MQTT.                |
| `smartthings`      | The device is a Samsung SmartThings device.                |
| `hue`              | The device is a Phillips Hue device.                       |
| `http`             | The device is a generic HTTP device.                       |
| `uhubctl`          | The device is a USB port with a controller compatible with |
|                    | `uhubctl`.                                                 |^
{ #power-type-desc } Device Type

//// Note
It is possible for unofficial 3rd party extensions to register their own
Device Types and implementations.
////
///

### Get Device State
Requests the device state for a single configured device.

```{.http .apirequest title="HTTP Request"}
GET /machine/device_power/device?device=green_led
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.get_device",
    "params": {
        "device": "green_led"
    },
    "id": 4564
}
```

```{.json .apiresponse title="Example Response"}
{
    "green_led": "off"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                            |
| ------------- | :----: | -------------------------------------- |
| *device_name* | string | The current [state](#power-state-desc) |
|               |        | of the requested device.               |^

///

### Set Device State
Toggle, turn on, or turn off a specified device.

```{.http .apirequest title="HTTP Request"}
POST /machine/device_power/device
Content-Type: application/json

{
    "device": "green_led",
    "action": "on"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.post_device",
    "params": {
        "device": "green_led",
        "action": "on"
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name     |  Type  | Default      | Description                             |
| -------- | :----: | ------------ | --------------------------------------- |
| `device` | string | **REQUIRED** | The name of the device to manage.       |
| `action` | string | **REQUIRED** | The [action](#power-device-action-desc) |
|          |        |              | to perform on the device.               |^

| Action   | Description           |
| -------- | --------------------- |
| `on`     | Turns the device on.  |
| `off`    | Turns the device off. |
| `toggle` | Toggles device state. |
{ #power-device-action-desc } Power Device Action

///

```{.json .apiresponse title="Example Response"}
{
    "green_led": "off"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                            |
| ------------- | :----: | -------------------------------------- |
| *device_name* | string | The current [state](#power-state-desc) |
|               |        | of the requested device.               |^

///

### Get Batch Device Status
Get power status for the requested devices.  At least one device must be
specified.

```{.http .apirequest title="HTTP Request"}
GET /machine/device_power/status?dev_one&dev_two
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.status",
    "params": {
        "dev_one": null,
        "dev_two": null
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name     | Type | Default | Description                                |
| -------- | :--: | ------- | ------------------------------------------ |
| *device* | null | null    | There may be multiple devices specified,   |
|          |      |         | where the keys the requested device names. |^
|          |      |         | Values should always be `null`.            |^

//// Note
The strangeness of this parameter specification is an artifact
from an early attempt to simplify the query string and maintain
compatibility with JSON parameters.
////

///


```{.json .apiresponse title="Example Response"}
{
    "green_led": "off",
    "printer": "off"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                            |
| ------------- | :----: | -------------------------------------- |
| *device_name* | string | The current [state](#power-state-desc) |
|               |        | of the requested device.               |^

///

### Batch Power On Devices
Power on the requested devices.  At least one device must be
specified.

```{.http .apirequest title="HTTP Request"}
POST /machine/device_power/on
Content-Type: application/json

{
    "dev_one": null,
    "dev_two": null
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.on",
    "params": {
        "dev_one": null,
        "dev_two": null
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name     | Type | Default | Description                                |
| -------- | :--: | ------- | ------------------------------------------ |
| *device* | null | null    | There may be multiple devices specified,   |
|          |      |         | where the keys the requested device names. |^
|          |      |         | Values should always be `null`.            |^

//// Note
The strangeness of this parameter specification is an artifact
from an early attempt to simplify query string parameters and maintain
compatibility with JSON parameters.
////

///

```{.json .apiresponse title="Example Response"}
{
    "green_led": "on",
    "printer": "on"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                                      |
| ------------- | :----: | ------------------------------------------------ |
| *device_name* | string | The current [state](#power-state-desc)           |
|               |        | of the requested device. The field name          |^
|               |        | of the response is the device's configured       |^
|               |        | name. The response may contain multiple devices. |^

///



### Batch Power Off Devices
Power off the requested devices.  At least one device must be
specified.

```{.http .apirequest title="HTTP Request"}
POST /machine/device_power/off?dev_one&dev_two
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.device_power.off",
    "params": {
        "dev_one": null,
        "dev_two": null
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name     | Type | Default | Description                                |
| -------- | :--: | ------- | ------------------------------------------ |
| *device* | null | null    | There may be multiple devices specified,   |
|          |      |         | where the keys the requested device names. |^
|          |      |         | Values should always be `null`.            |^

//// Note
The strangeness of this parameter specification is an artifact
from an early attempt to simplify query string parameters and maintain
compatibility with JSON parameters.
////

///

```{.json .apiresponse title="Example Response"}
{
    "green_led": "off",
    "printer": "off"
}
```

/// api-response-spec
    open: True

| Field         |  Type  | Description                                      |
| ------------- | :----: | ------------------------------------------------ |
| *device_name* | string | The current [state](#power-state-desc)           |
|               |        | of the requested device. The field name          |^
|               |        | of the response is the device's configured       |^
|               |        | name. The response may contain multiple devices. |^

///

## WLED Endpoints

The `wled` component can be used to perform high level management of
devices running WLED firmware.

The endpoints in this section are available when one or more `[wled <dev_name>]`
sections are configured in `moonraker.conf`.

For lower-level control of wled consider using the WLED
[JSON API](https://kno.wled.ge/interfaces/json-api/) directly.

### Get strips

```{.http .apirequest title="HTTP Request"}
GET /machine/wled/strips
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.strips",
    "id": 7123
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "strips": {
        "lights": {
            "strip": "lights",
            "status": "on",
            "chain_count": 79,
            "preset": -1,
            "brightness": 255,
            "intensity": -1,
            "speed": -1,
            "error": null
        },
        "desk": {
            "strip": "desk",
            "status": "on",
            "chain_count": 60,
            "preset": 8,
            "brightness": -1,
            "intensity": -1,
            "speed": -1,
            "error": null
        }
    }
}
```
///

/// api-response-spec
    open: True

| Field    |  Type  | Description                                                 |
| -------- | :----: | ----------------------------------------------------------- |
| `strips` | object | A container of [WLED Strip Status](#wled-strip-status-spec) |
|          |        | objects.  The keys in this object will be the name of strip |^
|          |        | with the values containing strip status.                    |^

| Field         |      Type      | Description                                 |
| ------------- | :------------: | ------------------------------------------- |
| `strip`       |     string     | The configured name of the strip.           |
| `status`      |     string     | The current state of the WLED strip. Will   |
|               |                | be `on` if the strip is enabled or `off`    |^
|               |                | if the strip is disabled.                   |^
| `chain_count` |      int       | The number of LEDs configured on the chain. |
| `preset`      |      int       | The numbered preset. Will be -1 if no       |
|               |                | preset is selected.                         |^
| `brightness`  |      int       | The brightness value set by Moonraker. Will |
|               |                | be -1 if Moonraker has not set this value.  |^
| `intensity`   |      int       | The intensity value set by Moonraker. Will  |
|               |                | be -1 if Moonraker has not set this value.  |^
| `speed`       |      int       | The speed value set by Moonraker. Will      |
|               |                | be -1 if Moonraker has not set this value.  |^
| `error`       | string \| null | A message describing last error returned    |
|               |                | from an attempted  WLED command.  Will be   |^
|               |                | `null` if no error is returned.             |^
{ #wled-strip-status-spec } WLED Strip Status

///

### Get strip status

```{.http .apirequest title="HTTP Request"}
GET /machine/wled/status?strip1&strip2
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.status",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7124
}
```

/// api-parameters
    open: True

| Name    | Type | Default | Description                               |
| ------- | :--: | ------- | ----------------------------------------- |
| *strip* | null | null    | There may be multiple strips specified,   |
|         |      |         | where the keys the requested strip names. |^
|         |      |         | Values should always be `null`.           |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "on",
        "chain_count": 79,
        "preset": -1,
        "brightness": 255,
        "intensity": -1,
        "speed": -1,
        "error": null
    },
    "desk": {
        "strip": "desk",
        "status": "on",
        "chain_count": 60,
        "preset": 8,
        "brightness": -1,
        "intensity": -1,
        "speed": -1,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                  |
| ------- | :----: | -------------------------------------------- |
| *strip* | object | There may be multiple `strips`, where the    |
|         |        | keys are strip names and the values are      |^
|         |        | [WLED strip status](#wled-strip-status-spec) |^
|         |        | objects.                                     |^

///

### Turn strip on

Turns the specified strips on to the initial colors or initial preset.

```{.http .apirequest title="HTTP Request"}
POST /machine/wled/on
Content-Type: application/json

{
    "lights": null,
    "desk": null
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.on",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7125
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "on",
        "chain_count": 79,
        "preset": -1,
        "brightness": 255,
        "intensity": -1,
        "speed": -1,
        "error": null
    },
    "desk": {
        "strip": "desk",
        "status": "on",
        "chain_count": 60,
        "preset": 8,
        "brightness": -1,
        "intensity": -1,
        "speed": -1,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                  |
| ------- | :----: | -------------------------------------------- |
| *strip* | object | There may be multiple `strips`, where the    |
|         |        | keys are strip names and the values are      |^
|         |        | [WLED strip status](#wled-strip-status-spec) |^
|         |        | objects.                                     |^

///

### Turn strip off

Turns off all specified strips.

```{.http .apirequest title="HTTP Request"}
POST /machine/wled/off
Content-Type: application/json

{
    "lights": null,
    "desk": null
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.off",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7126
}
```

/// api-parameters
    open: True

| Name    | Type | Default | Description                               |
| ------- | :--: | ------- | ----------------------------------------- |
| *strip* | null | null    | There may be multiple strips specified,   |
|         |      |         | where the keys the requested strip names. |^
|         |      |         | Values should always be `null`.           |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "off",
        "chain_count": 79,
        "preset": -1,
        "brightness": 255,
        "intensity": -1,
        "speed": -1,
        "error": null
    },
    "desk": {
        "strip": "desk",
        "status": "off",
        "chain_count": 60,
        "preset": 8,
        "brightness": -1,
        "intensity": -1,
        "speed": -1,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                  |
| ------- | :----: | -------------------------------------------- |
| *strip* | object | There may be multiple `strips`, where the    |
|         |        | keys are strip names and the values are      |^
|         |        | [WLED strip status](#wled-strip-status-spec) |^
|         |        | objects.                                     |^

///

### Toggle strip on/off state

Toggles the current enabled state for the requested strips.

```{.http .apirequest title="HTTP Request"}
POST /machine/wled/toggle
Content-Type: application/json

{
    "lights": null,
    "desk": null
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.toggle",
    "params": {
        "lights": null,
        "desk": null
    },
    "id": 7127
}
```

/// api-parameters
    open: True

| Name    | Type | Default | Description                               |
| ------- | :--: | ------- | ----------------------------------------- |
| *strip* | null | null    | There may be multiple strips specified,   |
|         |      |         | where the keys the requested strip names. |^
|         |      |         | Values should always be `null`.           |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "on",
        "chain_count": 79,
        "preset": -1,
        "brightness": 255,
        "intensity": -1,
        "speed": -1,
        "error": null
    },
    "desk": {
        "strip": "desk",
        "status": "off",
        "chain_count": 60,
        "preset": 8,
        "brightness": -1,
        "intensity": -1,
        "speed": -1,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                  |
| ------- | :----: | -------------------------------------------- |
| *strip* | object | There may be multiple `strips`, where the    |
|         |        | keys are strip names and the values are      |^
|         |        | [WLED strip status](#wled-strip-status-spec) |^
|         |        | objects.                                     |^

///

### Get individual strip state

```{.http .apirequest title="HTTP Request"}
GET /machine/wled/strip?strip=lights
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.get_strip",
    "params": {
        "strip": "lights"
    }
    "id": 7128
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "on",
        "chain_count": 79,
        "preset": 1,
        "brightness": 50,
        "intensity": 255,
        "speed": 255,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                        |
| ------- | :----: | -------------------------------------------------- |
| *strip* | object | An object containing the requested strip's current |
|         |        | status.  The key is the strip's name, the value is |^
|         |        | an [WLED strip status](#wled-strip-status-spec)    |^
|         |        | object.                                            |^

///

### Control individual strip state

Toggle, turn on, turn off, turn on with preset, turn on with brightness, or
turn on preset will some of brightness, intensity, and speed. Or simply set
some of brightness, intensity, and speed.

```{.http .apirequest title="HTTP Request"}
POST /machine/wled/strip
Content-Type: application/json

{
    "strip": "lights",
    "action" "on",
    "preset": 3,
    "brightness": 200,
    "intensity": 50,
    "speed": 180
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.wled.post_strip",
    "params": {
        "strip": "lights",
        "action" "on",
        "preset": 3,
        "brightness": 200,
        "intensity": 50,
        "speed": 180
    },
    "id": 7128
}
```

/// api-parameters
    open: True

| Name         |  Type  | Default         | Description                                    |
| ------------ | :----: | --------------- | ---------------------------------------------- |
| `strip`      | string | **REQUIRED**    | The name of the strip to control.              |
| `action`     | string | **REQUIRED**    | The [WLED Action](#wled-action-desc) to        |
|              |        |                 | execute on the strip.                          |^
| `preset`     |  int   | **INITIAL_VAL** | The numbered preset stored on the WLED         |
|              |        |                 | controller.  The `preset` is only applied when |^
|              |        |                 | a strip is enabled, either through the `on` or |^
|              |        |                 | `toggle` actions.                              |^
| `brightness` |  int   | **CURRENT_VAL** | Changes the `brightness` of the LEDs on the    |
|              |        |                 | strip.  The permitted range is 1-255.          |^
| `intensity`  |  int   | **CURRENT_VAL** | Changes the `intensity` value of the current   |
|              |        |                 | preset.  The permitted range is 0-255.  This   |^
|              |        |                 | setting is ignored if no preset is active.     |^
| `speed`      |  int   | **CURRENT_VAL** | Changes the `speed` value of the current       |
|              |        |                 | preset.  The permitted range is 0-255.  This   |^
|              |        |                 | setting is ignored if no preset is active.     |^

| Action    | Description                                               |
| --------- | --------------------------------------------------------- |
| `on`      | Enable the strip. The `on` action may be accompanied      |
|           | by one or more of the `preset`, `brightness`, `intensity` |^
|           | or `speed` parameters, which will be applied immediately. |^
| `off`     | Disable the strip.                                        |
| `toggle`  | Toggle the strip's enabled state.                         |
| `control` | Modify `brightness`, `intensity`, and/or `speed` without  |
|           | changing the current enabled state.  At least one of the  |^
|           | these parameters must be provided when the action is      |^
|           | `control`.                                                |^
{ #wled-action-desc } WLED Action

//// Note
When a strip is enabled the `brightness`, `intensity`, and `speed`
values will be reset to the preset's default values.
////

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "lights": {
        "strip": "lights",
        "status": "on",
        "chain_count": 79,
        "preset": 1,
        "brightness": 50,
        "intensity": 255,
        "speed": 255,
        "error": null
    }
}
```
///

/// api-response-spec
    open: True

| Field   |  Type  | Description                                        |
| ------- | :----: | -------------------------------------------------- |
| *strip* | object | An object containing the requested strip's current |
|         |        | status.  The key is the strip's name, the value is |^
|         |        | an [WLED strip status](#wled-strip-status-spec)    |^
|         |        | object.                                            |^

///

## Sensor endpoints

The endpoints in this section are available when at least one
`[sensor <sensor_name>]` section has been configured in `moonraker.conf`.

### Get Sensor List

```{.http .apirequest title="HTTP Request"}
GET /server/sensors/list?extended=False
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.sensors.list",
    "params": {
        "extended": false
    }
    "id": 5646
}
```

/// api-parameters
    open: True

| Name       | Type | Default | Description                              |
| ---------- | :--: | ------- | ---------------------------------------- |
| `extended` | bool | false   | When set to `true` the status for each   |
|            |      |         | sensor will include `parameter_info` and |^
|            |      |         | `history_fields` fields.                 |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "sensors": {
        "sensor1": {
            "id": "sensor1",
            "friendly_name": "Sensor 1",
            "type": "mqtt",
            "values": {
                "value1": 0,
                "value2": 119.8
            },
            "parameter_info": [
                {
                    "units": "kWh",
                    "name": "value1"
                },
                {
                    "units": "V",
                    "name": "value2"
                }
            ],
            "history_fields": [
                {
                    "field": "power_consumption",
                    "provider": "sensor sensor1",
                    "description": "Printer Power Consumption",
                    "strategy": "delta",
                    "units": "kWh",
                    "init_tracker": true,
                    "exclude_paused": false,
                    "report_total": true,
                    "report_maximum": true,
                    "precision": 6,
                    "parameter": "value1"
                },
                {
                    "field": "max_voltage",
                    "provider": "sensor sensor1",
                    "description": "Maximum voltage",
                    "strategy": "maximum",
                    "units": "V",
                    "init_tracker": true,
                    "exclude_paused": false,
                    "report_total": false,
                    "report_maximum": false,
                    "precision": 6,
                    "parameter": "value2"
                }
            ]
        }
    }
}
```
///

/// api-response-spec
    open: True

| Field     |  Type  | Description                                  |
| --------- | :----: | -------------------------------------------- |
| `sensors` | object | An object containing the sensor status.      |
|           |        | Each key will be the sensor's ID, each       |^
|           |        | value will be a                              |^
|           |        | [sensor status](#sensor-status-spec) object. |^

| Field            |   Type   | Description                                 |
| ---------------- | :------: | ------------------------------------------- |
| `id`             |  string  | The sensor's configured ID.                 |
| `friendly_name`  |  string  | The sensor's configured friendly name.      |
| `type`           |  string  | The sensor's configured type.  Currently    |
|                  |          | only `mqtt` types are supported.            |^
| `values`         |  object  | A `Sensor Values` object reporting the      |
|                  |          | most recent values measured by the sensor.  |^
|                  |          | #sensor-values-spec                         |+
| `parameter_info` | [object] | An array of `Parameter Info` objects.  Only |
|                  |          | included with `extended` responses.         |^
| `history_fields` | [object] | An array of `History Field` objects.  Only  |
|                  |          | reported with `extended` responses.  Will   |^
|                  |          | be an empty list if no history fields are   |^
|                  |          | configured for the sensor.                  |^
{ #sensor-status-spec } Sensor Status

| Field        | Type | Description                                     |
| ------------ | :--: | ----------------------------------------------- |
| *value_name* | any  | The object may contain multiple `values`, where |
|              |      | each key is the name of a parameter tracked     |^
|              |      | by the sensor, and the value is the most        |^
|              |      | recent reported measurement.                    |^
{ #sensor-values-spec } Sensor Values

| Field    |  Type  | Description                                           |
| -------- | :----: | ----------------------------------------------------- |
| `name`   | string | The name of a parameter measured by the sensor.       |
| _custom_ | string | The `parameter_info` object may contain additional    |
|          |        | custom fields provided in the sensor's configuration. |^
|          |        | It is common for a sensor to add a `units` field      |^
|          |        | specifying the type of data measured by the sensor.   |^
{ #sensor-parameter-info-spec } Parameter Info

| Field            |      Type      | Description                                          |
| ---------------- | :------------: | ---------------------------------------------------- |
| `field`          |     string     | The name of the auxiliary field to be stored in      |
|                  |                | the [job history](./history.md).                     |^
| `provider`       |     string     | The object providing data for history                |
|                  |                | tracking.  Will be the sensor's config               |^
|                  |                | section name, ie: `sensor my_sensor`.                |^
| `description`    |     string     | A brief description of the measurement.              |
| `strategy`       |     string     | The [strategy](#sensor-history-strategy) used to     |
|                  |                | track data stored in the job history.                |^
| `units`          | string \| null | The units, if applicable, for the value stored       |
|                  |                | in history.                                          |^
| `init_tracker`   |      bool      | When `true` the first value tracked will be          |
|                  |                | initialized to the most recent sensor measurement.   |^
| `exclude_paused` |      bool      | When `true` tracking will exclude measurements       |
|                  |                | taken while a job is paused.                         |^
| `report_total`   |      bool      | When `true` the final tracked value will be          |
|                  |                | accumulated and  included in the `history`           |^
|                  |                | component's job totals.                              |^
| `report_maximum` |      bool      | When `true` the maximum final tracked value during a |
|                  |                | job will be included in the `history` component's    |^
|                  |                | job totals.                                          |^
| `precision`      |      int       | The precision of the final tracked value, presuming  |
|                  |                | it is a float.                                       |^
| `parameter`      |     string     | The `name` of the sensor parameter to track.         |
{ #sensor-history-field-spec } History Fields

| Strategy     | Description                                              |
| ------------ | -------------------------------------------------------- |
| `basic`      | Stores the last value measured during a job.             |
| `delta`      | Stores the difference between the last and first values  |
|              | measured during a job.                                   |^
| `accumulate` | Stores the cumulative value of all measurements reported |
|              | during the job.                                          |^
| `average`    | Stores an average of all measurements taken during the   |
|              | job.                                                     |^
| `maximum`    | Stores the maximum value measured during the job.        |
| `minimum`    | Stores the minimum value measured during the job.        |
| `collect`    | Stores all values measured during the job in an array.   |
{ #sensor-history-strategy } History Tracking Strategy

///

### Get Sensor Information
Returns the status for a single configured sensor.

```{.http .apirequest title="HTTP Request"}
GET /server/sensors/info?sensor=sensor1&extended=false
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.sensors.info",
    "params": {
        "sensor": "sensor1",
        "extended": false
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default      | Description                              |
| ---------- | :----: | ------------ | ---------------------------------------- |
| `sensor`   | string | **REQUIRED** | The ID of the requested sensor.          |
| `extended` |  bool  | false        | When set to `true` the status for the    |
|            |        |              | sensor will include `parameter_info` and |^
|            |        |              | `history_fields` fields.                 |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "id": "sensor1",
    "friendly_name": "Sensor 1",
    "type": "mqtt",
    "values": {
        "value1": 0.0,
        "value2": 120.0
    },
    "parameter_info": [
        {
            "units": "kWh",
            "name": "value1"
        },
        {
            "units": "V",
            "name": "value2"
        }
    ],
    "history_fields": [
        {
            "field": "power_consumption",
            "provider": "sensor sensor1",
            "description": "Printer Power Consumption",
            "strategy": "delta",
            "units": "kWh",
            "init_tracker": true,
            "exclude_paused": false,
            "report_total": true,
            "report_maximum": true,
            "precision": 6,
            "parameter": "value1"
        },
        {
            "field": "max_voltage",
            "provider": "sensor sensor1",
            "description": "Maximum voltage",
            "strategy": "maximum",
            "units": "V",
            "init_tracker": true,
            "exclude_paused": false,
            "report_total": false,
            "report_maximum": false,
            "precision": 6,
            "parameter": "value2"
        }
    ]
}
```
///

/// api-response-spec
    open: True

The response specification is a [Sensor Status](#sensor-status-spec) object.

///

### Get Sensor Measurements
Returns all recorded measurements for a configured sensor.

```{.http .apirequest title="HTTP Request"}
GET /server/sensors/measurements?sensor=sensor1
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.sensors.measurements",
    "params": {
        "sensor": "sensor1"
    },
    "id": 4564
}
```
/// api-parameters
    open: True

| Name       |  Type  | Default      | Description                              |
| ---------- | :----: | ------------ | ---------------------------------------- |
| `sensor`   | string | **REQUIRED** | The ID of the requested sensor.          |

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "sensor1": {
        "value1": [
            3.1,
            3.2,
            3.0
        ],
        "value2": [
            120.0,
            120.0,
            119.9
        ]
    }
}
```
///

/// api-response-spec
    open: True

| Field       |  Type  | Description                                        |
| ----------- | :----: | -------------------------------------------------- |
| *sensor_id* | object | A [Sensor Measurements](#sensor-measurements-spec) |
|             |        | object.  The key for this item will be the sensor  |^
|             |        | id.                                                |^

| Field        |      Type      | Description                                  |
| ------------ | :------------: | -------------------------------------------- |
| *param_name* | [float \| int] | An array of decimal numbers containing all   |
|              |                | stored measurements for the named parameter. |^
|              |                | There may be multiple items in this          |^
|              |                | object, where they keys are parameter names. |^
{ #sensor-measurements-spec } Sensor Measurements

///

### Get Batch Sensor Measurements
Returns recorded measurements for all sensors.

```{.http .apirequest title="HTTP Request"}
GET /server/sensors/measurements
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.sensors.measurements",
    "id": 4564
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "sensor1": {
        "value1": [
            3.1,
            3.2,
            3.0
        ],
        "value2": [
            120.0,
            120.0,
            119.9
        ]
    },
    "sensor2": {
        "value_a": [
            1,
            1,
            0
        ]
    }
}
```
///

/// api-response-spec
    open: True

| Field       |  Type  | Description                                        |
| ----------- | :----: | -------------------------------------------------- |
| *sensor_id* | object | A [Sensor Measurements](#sensor-measurements-spec) |
|             |        | object.  There may be multiple sensor items, where |
|             |        | the keys are sensor IDs.                           |^

///

## MQTT Endpoints

Moonraker supports `mqtt` connections for communicating with other
devices on the network.  In addition to the [power](#power-endpoints)
and [sensor](#sensor-endpoints) implementations Moonraker provides
endpoints for clients to publish and subscribe to topics on the
network. These endpoints are available when `[mqtt]` has been configured
in `moonraker.conf`.

/// Note
These endpoints are not available over the `mqtt` transport as they
are redundant.  MQTT clients can publish and subscribe to
topics directly.
///

### Publish a topic

```{.http .apirequest title="HTTP Request"}
POST /server/mqtt/publish
Content-Type: application/json

{
    "topic": "home/test/pub",
    "payload": "hello",
    "qos": 0,
    "retain": false,
    "timeout": 5
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.mqtt.publish",
    "params":{
        "topic": "home/test/pub",
        "payload": "hello",
        "qos": 0,
        "retain": false,
        "timeout": 5
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name      |  Type  | Default            | Description                              |
| --------- | :----: | ------------------ | ---------------------------------------- |
| `topic`   | string | **REQUIRED**       | The topic to publish to the network.     |
| `payload` |  any   | null               | The payload to send with the topic.      |
|           |        |                    | May be a boolean, float, integer,        |^
|           |        |                    | object, or array.  Objects and Arrays    |^
|           |        |                    | are JSON encoded.  When this parameter   |^
|           |        |                    | is omitted an empty payload is sent.     |^
| `qos`     |  int   | **CONFIG_DEFAULT** | The QOS level to use when publishing a   |
|           |        |                    | topic.  Valid range is 0-2.              |^
| `retain`  |  bool  | false              | When set to `true` the topic's retain    |
|           |        |                    | flag is set.                             |^
| `timeout` | float  | null               | A timeout, in seconds, in which          |
|           |        |                    | Moonraker will wait for acknowledgement  |^
|           |        |                    | from the broker.  If the timeout is      |^
|           |        |                    | exceeded the request will return with a  |^
|           |        |                    | 504 error.  Only applies to QOS levels 1 |^
|           |        |                    | 2. When omitted the request will wait    |^
|           |        |                    | indefinitely.                            |^

//// tip
The `retain` flag tells the broker to save, or "retain", the payload
associated with the topic.  Only the most recent payload published
to the topic is retained. Subsequent subscribers to the topic will
immediately receive the retained payload.

To clear a retained value of a topic, publish the topic with an empty
payload and `retain` set to `true`.
////

///

```{.json .apiresponse title="Example Response"}
{
    "topic": "home/test/pub"
}
```

/// api-response-spec
    open: True

| Field   |  Type  | Description                                |
| ------- | :----: | ------------------------------------------ |
| `topic` | string | The topic that was successfully published. |

///

### Subscribe to a topic


```{.http .apirequest title="HTTP Request"}
POST /server/mqtt/subscribe
Content-Type: application/json

{
    "topic": "home/test/sub",
    "qos": 0,
    "timeout": 5
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "server.mqtt.subscribe",
    "params":{
        "topic": "home/test/sub",
        "qos": 0,
        "timeout": 5
    },
    "id": 4564
}
```

/// api-parameters
    open: True

| Name      |  Type  | Default            | Description                                |
| --------- | :----: | ------------------ | ------------------------------------------ |
| `topic`   | string | **REQUIRED**       | The topic to subscribe to.  Wildcards      |
|           |        |                    | are **not** allowed.                       |^
| `qos`     |  int   | **CONFIG_DEFAULT** | The QOS level to use for the subscription. |
|           |        |                    | Valid range is 0-2.                        |^
| `timeout` | float  | null               | A timeout, in seconds, to wait until a     |
|           |        |                    | response is received.  The request will    |^
|           |        |                    | return with a 504 error if the timeout     |^
|           |        |                    | is exceeded.  By default the request will  |^
|           |        |                    | wait indefinitely.                         |^

///

/// note
If the topic was previously published with a retained payload this request
will return immediately with the retained value.
///

```{.json .apiresponse title="Example Response"}
{
    "topic": "home/test/pub",
    "payload": "test"
}
```

/// api-response-spec
    open: True

| Field     |               Type                | Description                          |
| --------- | :-------------------------------: | ------------------------------------ |
| `topic`   |              string               | The name of the topic subscribed to. |
| `payload` | string \| object \| array \| null | The payload received with the topic. |

//// note
If the `payload` contains a JSON value it will decoded and
set as an object or array before re-encoding the full response back
to JSON.  Otherwise it will be a string or null if the
payload is empty.
////

///
