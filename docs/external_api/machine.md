# System Administration

The endpoints in this section provide administrative functions for
the host machine and operating system.

## Get System Info

```{.http .apirequest title="HTTP Request"}
GET /machine/system_info
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.system_info",
    "id": 4665
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "system_info": {
        "provider": "systemd_dbus",
        "cpu_info": {
            "cpu_count": 4,
            "bits": "32bit",
            "processor": "armv7l",
            "cpu_desc": "ARMv7 Processor rev 4 (v7l)",
            "serial_number": "b898bdb4",
            "hardware_desc": "BCM2835",
            "model": "Raspberry Pi 3 Model B Rev 1.2",
            "total_memory": 945364,
            "memory_units": "kB"
        },
        "sd_info": {
            "manufacturer_id": "03",
            "manufacturer": "Sandisk",
            "oem_id": "5344",
            "product_name": "SU32G",
            "product_revision": "8.0",
            "serial_number": "46ba46",
            "manufacturer_date": "4/2018",
            "capacity": "29.7 GiB",
            "total_bytes": 31914983424
        },
        "distribution": {
            "name": "Raspbian GNU/Linux 10 (buster)",
            "id": "raspbian",
            "version": "10",
            "version_parts": {
                "major": "10",
                "minor": "",
                "build_number": ""
            },
            "like": "debian",
            "codename": "buster"
        },
        "available_services": [
            "klipper",
            "klipper_mcu",
            "moonraker"
        ],
        "instance_ids": {
            "moonraker": "moonraker",
            "klipper": "klipper"
        },
        "service_state": {
            "klipper": {
                "active_state": "active",
                "sub_state": "running"
            },
            "klipper_mcu": {
                "active_state": "active",
                "sub_state": "running"
            },
            "moonraker": {
                "active_state": "active",
                "sub_state": "running"
            }
        },
        "virtualization": {
            "virt_type": "none",
            "virt_identifier": "none"
        },
        "python": {
            "version": [
                3,
                9,
                2,
                "final",
                0
            ],
            "version_string": "3.9.2 (default, Feb 28 2021, 17:03:44)  [GCC 10.2.1 20210110]"
        },
        "network": {
            "wlan0": {
                "mac_address": "<redacted_mac>",
                "ip_addresses": [
                    {
                        "family": "ipv4",
                        "address": "192.168.1.127",
                        "is_link_local": false
                    },
                    {
                        "family": "ipv6",
                        "address": "<redacted_ipv6>",
                        "is_link_local": false
                    },
                    {
                        "family": "ipv6",
                        "address": "fe80::<redacted>",
                        "is_link_local": true
                    }
                ]
            }
        },
        "canbus": {
            "can0": {
                "tx_queue_len": 128,
                "bitrate": 500000,
                "driver": "mcp251x"
            },
            "can1": {
                "tx_queue_len": 128,
                "bitrate": 500000,
                "driver": "gs_usb"
            }
        }
    }
}
```
///

/// api-response-spec
    open: True

| Field         |  Type  | Description                                                 |
| ------------- | :----: | ----------------------------------------------------------- |
| `system_info` | object | A top level [System Info](#sys-info-spec) object containing |
|               |        | various attributes that report info.                        |^
|               |        | #sys-info-spec                                              |+


| Field                |   Type   | Description                                                    |
| -------------------- | :------: | -------------------------------------------------------------- |
| `python`             |  object  | A `Python Info` object containing details about the Python     |
|                      |          | interpreter.                                                   |^
|                      |          | #python-info-spec                                              |+
| `cpu_info`           |  object  | A `CPU Info` object containing details about the host CPU.     |
|                      |          | #cpu-info-spec                                                 |+
| `sd_info`            |  object  | An `SDCard Info` object containing data about the host SD      |
|                      |          | Card. If no SD Card is detected this object will be empty.     |^
|                      |          | #sdcard-info-spec                                              |+
| `distribution`       |  object  | A `Distribution` object containing details about the host      |
|                      |          | Linux Distribution.                                            |^
|                      |          | #distribution-spec                                             |+
| `virtualization`     |  object  | A `Virtualization` object containing virtualization status     |
|                      |          | of the environment running Moonraker.                          |^
|                      |          | #virtualization-spec                                           |+
| `network`            |  object  | A `Network Info` object containing information about the       |
|                      |          | system's current network state.                                |^
|                      |          | #network-info-spec                                             |+
| `canbus`             |  object  | A `Canbus Info` object containing information about CAN        |
|                      |          | interfaces detected on the system.                             |^
|                      |          | #canbus-info-spec                                              |+
| `provider`           |  string  | The currently configured system provider type. Expand for a    |
|                      |          | list of available providers.                                   |^
|                      |          | #system-provider-desc                                          |+
| `available_services` | [string] | A list of detected services Moonraker is authorized to manage. |
| `service_state`      |  object  | A `Service State` object containing information about services |
|                      |          | Moonraker is monitoring.  This will be an empty object if no   |^
|                      |          | services are being monitored.                                  |^
|                      |          | #service-state-spec                                            |+
| `instance_ids`       |  object  | An `Instance ID` object matching known application names to    |
|                      |          | their detected system unit name.                               |^
|                      |          | #instance-id-spec                                              |+
{ #sys-info-spec } System Info

| Field            |     Type      | Description                                                                            |
| ---------------- | :-----------: | -------------------------------------------------------------------------------------- |
| `version`        | [int\|string] | A tuple indicating the version of the Python interpreter running Moonraker.            |
|                  |               | A complete description of the reported value can be found in                           |^
|                  |               | [Python's Documentation](https://docs.python.org/3/library/sys.html#sys.version_info). |^
| `version_string` |    string     | The Python version in string form.                                                     |
{ #python-info-spec } Python Info

| Field           |    Type     | Description                                                          |
| --------------- | :---------: | -------------------------------------------------------------------- |
| `cpu_count`     | int \| null | The number of CPU's detected.  Will be `null` if detection fails.    |
| `bits`          |   string    | The bit width of the architecture.  This is based on how the Python  |
|                 |             | binary was compiled. It is possible for a 64-bit capable processor   |^
|                 |             | to report 32-bits.  Will be an empty string if detection fails.      |^
| `cpu_desc`      |   string    | The CPU description as reported in `/proc/cpuinfo`.  Will be an      |
|                 |             | empty string if detection fails.                                     |^
| `serial_number` |   string    | The serial number of the CPU as reported in `/proc/cpuinfo`. Will be |
|                 |             | an empty string if detection fails.                                  |^
| `hardware_desc` |   string    | The hardware description as reported in `/proc/cpu_info`. Will be an |
|                 |             | empty string if detection fails.                                     |^
| `model`         |   string    | The model as reported in `/proc/cpu_info.`  Will be an empty string  |
|                 |             | if detection fails.                                                  |^
| `total_memory`  | int \| null | The total system memory as reported in `/proc/meminfo`. This is an   |
|                 |             | integer value and should always be specified in kilobytes.  The      |^
|                 |             | `memory_units` field may be used to validate the unit type.  Will    |^
|                 |             | be null if memory detection fails.                                   |^
| `memory_units`  |   string    | The detected units for memory reporting.  Should always be "kB", or  |
|                 |             | an empty string if detection fails.                                  |^
{ #cpu-info-spec } CPU Info

| Field               |  Type  | Description                                                    |
| ------------------- | :----: | -------------------------------------------------------------- |
| `manufacturer_id`   | string | A 2 character hex string identifying the manufacturer.         |
| `manufacturer`      | string | The manufacturer's name, if known.                             |
| `oem_id`            | string | A 4 character hex string identifying the OEM.                  |
| `product_name`      | string | An ascii string, up to 10 characters, identifying the          |
|                     |        | name of the SD Card.                                           |^
| `product_revision`  | string | Version of the product. Reported as `major.minor`, ie: `1.0`   |
| `serial_number`     | string | Serial number of the SD Card.  Will be 8 hex characters.       |
| `manufacturer_date` | string | Date of manufacture.  Reported as `month/year`, ie: `10/2022`. |
| `capacity`          | string | Reported capacity of the card.  Units are postfixed.           |
| `total_bytes`       |  int   | Reported capacity in bytes.                                    |
{ #sdcard-info-spec } SDCard Info

| Field           |  Type  | Description                                                 |
| --------------- | :----: | ----------------------------------------------------------- |
| `name`          | string | Full name of the Linux distribution.  Will be an empty      |
|                 |        | string if the name cannot be determined.                    |^
| `id`            | string | Distribution ID, ie: `ubuntu`.  Will be an empty string if  |
|                 |        | the id cannot be determined.                                |^
| `like`          | string | Parent distribution, ie: `debian`.  Will be an empty string |
|                 |        | if there is no parent distribution.                         |^
| `codename`      | string | The codename of the distribution, ie: `bookworm`.  Will be  |
|                 |        | an empty string if there is no codename.                    |^
| `version`       | string | The version number of the distribution.  Will be an empty   |
|                 |        | string if the version cannot be determined.                 |^
| `version_parts` | object | The version broken into parts and reported as an object.    |
|                 |        | Fields are `major`, `minor`, and `release`.  Any values     |^
|                 |        | not present will be reported as empty strings.              |^
| `release_info`  | object | An object containing key-value pairs extracted from the     |
|                 |        | `os-release` file.  The keys are variable, the values will  |^
|                 |        | always be strings.  If the `os-release` file does not exist |^
|                 |        | this will be an empty object.                               |^
{ #distribution-spec } Distribution

| Field       |  Type  | Description                                                          |
| ----------- | :----: | -------------------------------------------------------------------- |
| `virt_type` | string | The type of virtualization detected. Expand to view available types. |
|             |        | #virt-type-desc                                                      |+
| `virt_id`   | string | The virtualization identifier.  Will be `none` if no virtualization  |
|             |        | is detected.  Otherwise the value describes the virtualization       |^
|             |        | software hosting the instance of Moonraker.                          |^
{ #virtualization-spec } Virtualization

| Name        | Description                                    |
| ----------- | ---------------------------------------------- |
| `none`      | No virtualization detected.                    |
| `container` | Moonraker is running inside a container.       |
| `vm`        | Moonraker is running inside a virtual machine. |
{ #virt-type-desc } Virtualization Types

| Field      |  Type  | Description                                                |
| ---------- | :----: | ---------------------------------------------------------- |
| _variable_ | object | This object contains zero or more items, where each field  |
|            |        | is the name of a detected network interface and each value |^
|            |        | is a `Network Interface` object.                           |^
|            |        | #network-interface-spec                                    |+
{ #network-info-spec } Network Info

| Field          |   Type   | Description                                                |
| -------------- | :------: | ---------------------------------------------------------- |
| `ip_addresses` | [object] | A list of `IP Address` objects describing the IPs assigned |
|                |          | to the interface.                                          |^
|                |          | #ip-address-spec                                           |+
| `mac_address`  |  string  | The MAC address of the hardware bound to the interface.    |
{ #network-interface-spec } Network Interface

| Field           |  Type  | Description                                               |
| --------------- | :----: | --------------------------------------------------------- |
| `address`       | string | The detected IP address.                                  |
| `family`        | string | The family type of the address.  Can be `ipv4` or `ipv6`. |
| `is_link_local` |  bool  | A boolean value indicating if the address is "link local" |
|                 |        | address.                                                  |^
{ #ip-address-spec } IP Address

| Field      |  Type  | Description                                               |
| ---------- | :----: | --------------------------------------------------------- |
| _variable_ | object | This object contains zero or more items, where each field |
|            |        | is the name of a CAN interface and each value is a        |^
|            |        | `CAN Interface` object.                                   |^
|            |        | #can-interface-spec                                       |+
{ #canbus-info-spec } Canbus Info

| Field          |  Type  | Description                                        |
| -------------- | :----: | -------------------------------------------------- |
| `tx_queue_len` |  int   | The configured TX queue length of the interface.   |
| `bitrate`      |  int   | The configured CAN bitrate of the interface.       |
| `driver`       | string | The name of the hardware driver used to manage the |
|                |        | interface.                                         |^
{ #can-interface-spec } CAN interface

| Field      |  Type  | Description                                               |
| ---------- | :----: | --------------------------------------------------------- |
| _variable_ | object | This object contains zero or more items, where the fields |
|            |        | are the services Moonraker is monitoring and the values   |^
|            |        | are `Unit Status` objects.                                |^
|            |        | #unit-status-spec                                         |+
{ #service-state-spec } Service State

| Field          |  Type  | Description                                                          |
| -------------- | :----: | -------------------------------------------------------------------- |
| `active_state` | string | The current `ACTIVE` state reported by the provider for the service. |
| `sub_state`    | string | The current `SUB` state reported by the provider for the service.    |
{ #unit-status-spec } Unit Status

| Field       |  Type  | Description                                         |
| ----------- | :----: | --------------------------------------------------- |
| `moonraker` | string | The detected unit name for the `moonraker` service. |
| `klipper`   | string | The detected unit name for the `klipper` service.   |
{ #instance-id-spec }

| Provider          | description                                                           |
| ----------------- | --------------------------------------------------------------------- |
| `none`            | No system provider is configured.  This disables service management   |
|                   | and monitoring.                                                       |
| `systemd_cli`     | System management and monitoring is performed using systemd over      |
|                   | the command line, ie: `systemctl`.                                    |^
| `systemd_dbus`    | System management and monitoring is performed using systemd over      |
|                   | DBus.  When DBus is available this is preferable to the CLI provider. |^
| `supervisord_cli` | System management and monitoring is performed using supervisord over  |
|                   | the command line.                                                     |^
{ #system-provider-desc }
///

## Shutdown the Operating System

Commands the Operating System to shutdown.  The following pre-requisites must be
met to successfully perform this action:

- The `provider` must be `systemd_cli` or `systemd_dbus`.
- Moonraker must have permission to shutdown the host.
- Moonraker must not be running inside a container.

```{.http .apirequest title="HTTP Request"}
POST /machine/shutdown
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.shutdown",
    "id": 4665
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Reboot the Operating System

Commands the Operating System to shutdown.  The following pre-requisites must be
met to successfully perform this action:

- The `provider` must be `systemd_cli` or `systemd_dbus`.
- Moonraker must have permission to reboot the host.
- Moonraker must not be running inside a container.

```{.http .apirequest title="HTTP Request"}
POST /machine/reboot
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.reboot",
    "id": 4665
}
```

```{.text .apiresponse title="Response"}
"ok"
```

## Restart a system service

Commands a service to restart. The following pre-requisites must be
met to successfully perform this action:

- The `provider` must NOT be `none`.
- The service must be present in the list of `allowed_services`.
- Moonraker must have the necessary permissions to manage services.

```{.http .apirequest title="HTTP Request"}
POST /machine/services/restart
Content-Type: application/json

{
    "service": "klipper"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.services.restart",
    "params": {
        "service": "klipper"
    },
    "id": 4656
}
```

/// api-parameters
    open: True
| Name      |  Type  | Default      | Description                         |
| --------- | :----: | ------------ | ----------------------------------- |
| `service` | string | **REQUIRED** | The name of the service to restart. |
///

```{.text .apiresponse title="Response"}
"ok"
```

## Stop a system service

Commands a service to stop. The following pre-requisites must be
met to successfully perform this action:

- The `provider` must NOT be `none`.
- The service must be present in the list of `allowed_services`.
- Moonraker must have the necessary permissions to manage services.

```{.http .apirequest title="HTTP Request"}
POST /machine/services/stop
Content-Type: application/json

{
    "service": "klipper"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.services.stop",
    "params": {
        "service": "klipper"
    },
    "id": 4645
}
```

/// api-parameters
    open: True
| Name      |  Type  | Default      | Description                      |
| --------- | :----: | ------------ | -------------------------------- |
| `service` | string | **REQUIRED** | The name of the service to stop. |
///

```{.text .apiresponse title="Response"}
"ok"
```

## Start a system service

Commands a service to start. The following pre-requisites must be
met to successfully perform this action:

- The `provider` must NOT be `none`.
- The service must be present in the list of `allowed_services`.
- Moonraker must have the necessary permissions to manage services.

```{.http .apirequest title="HTTP Request"}
POST /machine/services/start
Content-Type: application/json

{
    "service": "klipper"
}
```
```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.services.start",
    "params": {
        "service": "klipper"
    },
    "id": 4645
}
```

/// api-parameters
    open: True
| Name      |  Type  | Default      | Description                       |
| --------- | :----: | ------------ | --------------------------------- |
| `service` | string | **REQUIRED** | The name of the service to start. |
///

```{.text .apiresponse title="Response"}
"ok"
```

## Get process statistics

Requests system usage information.  This includes CPU usage, network usage,
etc.

```{.http .apirequest title="HTTP Request"}
GET /machine/proc_stats
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.proc_stats",
    "id": 7896
}
```
/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "moonraker_stats": [
        {
            "time": 1626612666.850755,
            "cpu_usage": 2.66,
            "memory": 24732,
            "mem_units": "kB"
        },
        {
            "time": 1626612667.8521338,
            "cpu_usage": 2.62,
            "memory": 24732,
            "mem_units": "kB"
        }
    ],
    "throttled_state": {
        "bits": 0,
        "flags": []
    },
    "cpu_temp": 45.622,
    "network": {
        "lo": {
            "rx_bytes": 113516429,
            "tx_bytes": 113516429,
            "bandwidth": 3342.68
        },
        "wlan0": {
            "rx_bytes": 48471767,
            "tx_bytes": 113430843,
            "bandwidth": 4455.91
        }
    },
    "system_cpu_usage": {
        "cpu": 2.53,
        "cpu0": 3.03,
        "cpu1": 5.1,
        "cpu2": 1.02,
        "cpu3": 1
    },
    "system_uptime": 2876970.38089603,
    "websocket_connections": 4
}
```
///

/// api-response-spec
    open: True

| Field                   |      Type      | Description                                                       |
| ----------------------- | :------------: | ----------------------------------------------------------------- |
| `moonraker_stats`       |    [object]    | An array of `Moonraker Stats` objects.  The array is a            |
|                         |                | FIFO queue, where the first index is the oldest sample.           |^
|                         |                | Moonraker process stats are sampled roughly every second.         |^
|                         |                | The maximum size of the queue is 30 samples.                      |^
|                         |                | #moonraker-proc-stat-spec                                         |+
| `throttled_state`       | object \| null | An `Throttled State` object containing details about the CPU's    |
|                         |                | throttled state.  This information is only available on Raspberry |^
|                         |                | Pi hosts, on other hardware this value will be `null`.            |^
|                         |                | #throttled-state-spec                                             |+
| `cpu_temp`              | float \| null  | The current CPU temperature.  Will be `null` if the               |
|                         |                | temperature data is unavailable.                                  |^
| `network`               |     object     | A `Network Usage` object containing detailed network usage        |
|                         |                | data.  Will be an empty object if this information is             |^
|                         |                | unavailable.                                                      |^
|                         |                | #network-usage-spec                                               |+
| `system_cpu_usage`      |     object     | A `CPU Usage` object containing detailed CPU usage data.          |
|                         |                | Will be an empty object if this information is unavailable.       |^
|                         |                | #cpu-usage-spec                                                   |+
| `system_memory`         |     object     | A `Memory Usage` object containing detailed memory usage          |
|                         |                | data.  Will be an empty object if this information is             |^
|                         |                | unavailable.                                                      |^
|                         |                | #memory-usage-spec                                                |+
| `system_uptime`         |     float      | The time elapsed, in seconds, since system boot.                  |
| `websocket_connections` |      int       | The current number of open websocket connections.                 |
{ #proc-stats-response-spec}

| Field       |      Type      | Description                                                         |
| ----------- | :------------: | ------------------------------------------------------------------- |
| `time`      |     float      | The Unix time, in seconds, the sample was taken.                    |
| `cpu_usage` |     float      | The CPU usage of the moonraker process at this sample.              |
| `memory`    |  int \| null   | The memory usage of the moonraker process at this sample.  This     |
|             |                | should always be in kilobytes.  The `mem_units` field may be used   |^
|             |                | to validate. Will be `null` if memory information is not available. |^
| `mem_units` | string \| null | The memory units reported.  Should always be `kB`.  Will be `null`  |
|             |                | if memory information is not available.                             |^
{ #moonraker-proc-stat-spec } Moonraker Stats

| Field   |   Type   | Description                                                 |
| ------- | :------: | ----------------------------------------------------------- |
| `bits`  |   int    | The current throttled state as an integer.  A bitwise AND   |
|         |          | can be performed against this value to generate custom flag |^
|         |          | descriptions.                                               |^
| `flags` | [string] | A list of `Throttled Flags` describing the current state.   |
|         |          | #throttled-flags-desc                                       |+
{ #throttled-state-spec } Throttled State

| Flag                           | Bit Offset |
| ------------------------------ | :--------: |
| Under-Voltage Detected         |  `1 << 0`  |
| Frequency Capped               |  `1 << 1`  |
| Currently Throttled            |  `1 << 2`  |
| Temperature Limit Active       |  `1 << 3`  |
| Previously Under-Volted        | `1 << 16`  |
| Previously Frequency Capped    | `1 << 17`  |
| Previously Throttled           | `1 << 18`  |
| Previously Temperature Limited | `1 << 16`  |
{ #throttled-flags-desc } Throttled Flags

| Field      |  Type  | Description                                                |
| ---------- | :----: | ---------------------------------------------------------- |
| _variable_ | object | An object where the keys indicate a network interface name |
|            |        | and the values are `Interface Usage` objects containing    |^
|            |        | detailed usage info.  Will be empty if no network          |^
|            |        | interfaces are detected.                                   |^
|            |        | #net-interface-usage-spec                                  |+
{ #network-usage-spec } Network Usage

| Field        | Type  | Description                                                            |
| ------------ | :---: | ---------------------------------------------------------------------- |
| `bandwidth`  | float | The current estimated bandwidth used in `bytes/sec`.  This             |
|              |       | value combines bytes received and bytes transmitted.                   |^
| `rx_bytes`   |  int  | Total bytes received since the interface was brought up.               |
| `tx_bytes`   |  int  | Total bytes transmitted since the interface was brought up.            |
| `rx_packets` |  int  | Total packets received since the interface was brought up.             |
| `tx_packets` |  int  | Total packets transmitted since the interface was brought up.          |
| `rx_errs`    |  int  | Total receive errors since the interface was brought up.               |
| `tx_errs`    |  int  | Total transmission errors since the interface was brought up.          |
| `rx_drop`    |  int  | Total receive packets dropped since the interface was brought up.      |
| `tx_drop`    |  int  | Total transmission packets dropped since the interface was brought up. |
{ #net-interface-usage-spec } Network Interface Usage

| Field  | Type  | Description                                                   |
| ------ | :---: | ------------------------------------------------------------- |
| `cpu`  | float | Current overall CPU usage expressed as a percentage (0-100).  |
| `cpuX` | float | On multi-core CPU the usage for each individual CPU will be   |
|        |       | reported.  Starts at `cpu0`, the postfixed decimal increasing |^
|        |       | for each core.  As with `cpu`, this value is expressed as a   |^
|        |       | percentage.                                                   |^
{ #cpu-usage-spec } CPU Usage

| Field       | Type | Description                            |
| ----------- | :--: | -------------------------------------- |
| `total`     | int  | Total memory in kilobytes.             |
| `available` | int  | Current available memory in kilobytes. |
| `used`      | int  | Currently used memory in kilobytes.    |
{ #memory-usage-spec } Memory Usage

///

## Get Sudo Info

Retrieves sudo information status.  Optionally checks if Moonraker has
permission to run commands as root.

```{.http .apirequest title="HTTP Request"}
GET /machine/sudo/info?check_access=false
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.sudo.info",
    "params": {
        "check_access": false
    },
    "id": 7896
}
```
/// api-parameters
    open: True

| Name           | Type | Default | Description                                      |
| -------------- | :--: | ------- | ------------------------------------------------ |
| `check_access` | bool | `false` | When `true` Moonraker will attempt to run a sudo |
|                |      |         | command in a effort to check if sudo permission  |^
|                |      |         | is available.                                    |^

///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "sudo_access": null,
    "linux_user": "pi",
    "sudo_requested": false,
    "request_messages": []
}
```
///

/// api-response-spec
    open: True
| Field              |     Type     | Description                                                        |
| ------------------ | :----------: | ------------------------------------------------------------------ |
| `sudo_access`      | bool \| null | The result of a requested sudo permission check.  Will             |
|                    |              | be `null` if the check was not requested via the                   |^
|                    |              | `check_access` parameter.                                          |^
| `linux_user`       |    string    | The name of the linux user the Moonraker process belongs to.       |
| `sudo_requested`   |     bool     | Returns `true` if an internal Moonraker component has              |
|                    |              | requested a sudo password to perform some task, `false` otherwise. |
| `request_messages` |   [string]   | If one or more internal components have requested sudo access,     |
|                    |              | each will provide a description of the request available in this   |^
|                    |              | array.                                                             |^
///

## Set sudo password

Sets the sudo password currently used by Moonraker.  The password
is not persistent across Moonraker restarts.  If Moonraker has one or
more pending sudo requests they will be processed.

```{.http .apirequest title="HTTP Request"}
POST /machine/sudo/password
Content-Type: application/json

{
    "password": "linux_user_password"
}
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.sudo.password",
    "params": {
        "password": "linux_user_password"
    },
    "id": 7896
}
```

/// api-parameters
    open: True

| Name       |  Type  | Default      | Description                                |
| ---------- | :----: | ------------ | ------------------------------------------ |
| `password` | string | **REQUIRED** | The linux user password necessary to grant |
|            |        |              | Moonraker sudo permission.                 |^


///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "sudo_responses": [
        "Sudo password successfully set."
    ],
    "is_restarting": false
}
```
///

/// api-response-spec
    open: True

| Field            |   Type   | Description                                              |
| ---------------- | :------: | -------------------------------------------------------- |
| `sudo_responses` | [string] | If any Moonraker component has an outstanding sudo       |
|                  |          | it will process the task and provide a response included |^
|                  |          | in this array.                                           |^
| `is_restarting`  |   bool   | If a processed sudo request intends to restart Moonraker |
|                  |          | this value will be `true`, otherwise `false`.            |^

///

/// Note
This request will return an error if the supplied password is
incorrect or if any pending sudo requests fail.
///

## List USB Devices

Returns a list of all USB devices currently detected on the system.

```{.http .apirequest title="HTTP Request"}
GET /machine/peripherals/usb
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.peripherals.usb",
    "id": 7896
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "usb_devices": [
        {
            "device_num": 1,
            "bus_num": 1,
            "vendor_id": "1d6b",
            "product_id": "0002",
            "usb_location": "1:1",
            "manufacturer": "Linux 6.1.0-rpi7-rpi-v8 dwc_otg_hcd",
            "product": "DWC OTG Controller",
            "serial": "3f980000.usb",
            "class": "Hub",
            "subclass": null,
            "protocol": "Single TT",
            "description": "Linux Foundation 2.0 root hub"
        },
        {
            "device_num": 3,
            "bus_num": 1,
            "vendor_id": "046d",
            "product_id": "0825",
            "usb_location": "1:3",
            "manufacturer": "Logitech, Inc.",
            "product": "Webcam C270",
            "serial": "<unique serial number>",
            "class": "Miscellaneous Device",
            "subclass": null,
            "protocol": "Interface Association",
            "description": "Logitech, Inc. Webcam C270"
        },
        {
            "device_num": 2,
            "bus_num": 1,
            "vendor_id": "1a40",
            "product_id": "0101",
            "usb_location": "1:2",
            "manufacturer": "Terminus Technology Inc.",
            "product": "USB 2.0 Hub",
            "serial": null,
            "class": "Hub",
            "subclass": null,
            "protocol": "Single TT",
            "description": "Terminus Technology Inc. Hub"
        },
        {
            "device_num": 5,
            "bus_num": 1,
            "vendor_id": "0403",
            "product_id": "6001",
            "usb_location": "1:5",
            "manufacturer": "FTDI",
            "product": "FT232R USB UART",
            "serial": "<unique serial number>",
            "class": null,
            "subclass": null,
            "protocol": null,
            "description": "Future Technology Devices International, Ltd FT232 Serial (UART) IC"
        },
        {
            "device_num": 4,
            "bus_num": 1,
            "vendor_id": "1d50",
            "product_id": "614e",
            "usb_location": "1:4",
            "manufacturer": "Klipper",
            "product": "stm32f407xx",
            "serial": "<unique serial number>",
            "class": "Communications",
            "subclass": null,
            "protocol": null,
            "description": "OpenMoko, Inc. Klipper 3d-Printer Firmware"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field         |   Type   | Description                       |
| ------------- | :------: | --------------------------------- |
| `usb_devices` | [object] | An array of `USB Device` objects. |
|               |          | #usb-device-spec                  |+


| Field          |  Type   | Description                                         |
| -------------- | :-----: | --------------------------------------------------- |
| `bus_num`      |   int   | The USB bus number as reported by the host.         |
| `device_num`   |   int   | The USB device number as reported by the host.      |
| `usb_location` | string  | A combination of the bus number and device number,  |
|                |         | yielding a unique location ID on the host system.   |^
| `vendor_id`    | string  | The vendor ID as reported by the driver.            |
| `product_id`   | string  | The product ID as reported by the driver.           |
| `manufacturer` | string  | The manufacturer name as reported by the driver.    |
|                | \| null | This will be `null` if no manufacturer is found.    |^
| `product`      | string  | The product description as reported by the driver.  |
|                | \| null | This will be `null` if no description is found.     |^
| `class`        | string  | The class description as reported by the driver.    |
|                | \| null | This will be `null` if no description is found.     |^
| `subclass`     | string  | The subclass description as reported by the driver. |
|                | \| null | This will be `null` if no description is found.     |^
| `protocol`     | string  | The protocol description as reported by the driver. |
|                | \| null | This will be `null` if no description is found.     |^
| `description`  | string  | The full device description string as reported by   |
|                | \| null | the usb.ids file. This will be `null` if no         |^
|                |         | description is found.                               |^
{ #usb-device-spec } USB Device
///

## List Serial Devices

Returns a list of all serial devices detected on the system.  These may be USB
CDC-ACM devices or hardware UARTs.

```{.http .apirequest title="HTTP Request"}
GET /machine/peripherals/serial
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.peripherals.serial",
    "id": 7896
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "serial_devices": [
        {
            "device_type": "hardware_uart",
            "device_path": "/dev/ttyS0",
            "device_name": "ttyS0",
            "driver_name": "serial8250",
            "path_by_hardware": null,
            "path_by_id": null,
            "usb_location": null
        },
        {
            "device_type": "usb",
            "device_path": "/dev/ttyACM0",
            "device_name": "ttyACM0",
            "driver_name": "cdc_acm",
            "path_by_hardware": "/dev/serial/by-path/platform-3f980000.usb-usb-0:1.2:1.0",
            "path_by_id": "/dev/serial/by-id/usb-Klipper_stm32f407xx_unique_serial-if00",
            "usb_location": "1:4"
        },
        {
            "device_type": "usb",
            "device_path": "/dev/ttyUSB0",
            "device_name": "ttyUSB0",
            "driver_name": "ftdi_sio",
            "path_by_hardware": "/dev/serial/by-path/platform-3f980000.usb-usb-0:1.4:1.0-port0",
            "path_by_id": "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_unique_serial-if00-port0",
            "usb_location": "1:5"
        },
        {
            "device_type": "hardware_uart",
            "device_path": "/dev/ttyAMA0",
            "device_name": "ttyAMA0",
            "driver_name": "uart-pl011",
            "path_by_hardware": null,
            "path_by_id": null,
            "usb_location": null
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field            | Type     | Description                          |
| ---------------- | -------- | ------------------------------------ |
| `serial_devices` | [object] | An array of `Serial Device` objects. |
|                  |          | #serial-device-spec                  |+


| Field              |  Type   | Description                                                 |
| ------------------ | :-----: | ----------------------------------------------------------- |
| `device_type`      | string  | The type of serial device. Can be `hardware_uart` or `usb`. |
| `device_path`      | string  | The absolute file path to the device.                       |
| `device_name`      | string  | The device file name as reported by sysfs.                  |
| `driver_name`      | string  | The name of the device driver.                              |
| `path_by_hardware` | string | A symbolic link to the device based on its physical         |
|                    | \| null         | connection, ie: usb port.  Will be `null` if no             |^
|                    |         | matching link exists.                                       |^
| `path_by_id`       | string | A symbolic link the the device based on its reported IDs.   |
|                    | \| null         | Will be `null` if no matching link exists.                  |^
| `usb_location`     | string | An identifier derived from the reported usb bus and .       |
|                    | \| null         | device numbers Can be used to match results from            |^
|                    |         | `/machine/peripherals/usb`. Will be `null` for non-usb      |^
|                    |         | devices.                                                    |^
{ #serial-device-spec } Serial Device
///

## List Video Capture Devices

Retrieves a list of V4L2 video capture devices on the system.  If
the python3-libcamera system package is installed this request will
also return libcamera devices.

```{.http .apirequest title="HTTP Request"}
GET /machine/peripherals/video
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.peripherals.video",
    "id": 7896
}
```

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "v4l2_devices": [
        {
            "device_name": "video0",
            "device_path": "/dev/video0",
            "camera_name": "unicam",
            "driver_name": "unicam",
            "hardware_bus": "platform:3f801000.csi",
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "READWRITE",
                "STREAMING",
                "IO_MC"
            ],
            "version": "6.1.63",
            "path_by_hardware": "/dev/v4l/by-path/platform-3f801000.csi-video-index0",
            "path_by_id": null,
            "alt_name": "unicam-image",
            "usb_location": null,
            "modes": []
        },
        {
            "device_name": "video1",
            "device_path": "/dev/video1",
            "camera_name": "UVC Camera (046d:0825)",
            "driver_name": "uvcvideo",
            "hardware_bus": "usb-3f980000.usb-1.1",
            "modes": [
                {
                    "format": "YUYV",
                    "description": "YUYV 4:2:2",
                    "flags": [],
                    "resolutions": [
                        "640x480",
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "752x416",
                        "800x448",
                        "800x600",
                        "864x480",
                        "960x544",
                        "960x720",
                        "1024x576",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                },
                {
                    "format": "MJPG",
                    "description": "Motion-JPEG",
                    "flags": [
                        "COMPRESSED"
                    ],
                    "resolutions": [
                        "640x480",
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "752x416",
                        "800x448",
                        "800x600",
                        "864x480",
                        "960x544",
                        "960x720",
                        "1024x576",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                }
            ],
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "STREAMING"
            ],
            "version": "6.1.63",
            "path_by_hardware": "/dev/v4l/by-path/platform-3f980000.usb-usb-0:1.1:1.0-video-index0",
            "path_by_id": "/dev/v4l/by-id/usb-046d_0825_66EF0390-video-index0",
            "alt_name": "UVC Camera (046d:0825)",
            "usb_location": "1:3",
            "modes": [
                {
                    "format": "YUYV",
                    "description": "YUYV 4:2:2",
                    "flags": [],
                    "resolutions": [
                        "640x480",
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "752x416",
                        "800x448",
                        "800x600",
                        "864x480",
                        "960x544",
                        "960x720",
                        "1024x576",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                },
                {
                    "format": "MJPG",
                    "description": "Motion-JPEG",
                    "flags": [
                        "COMPRESSED"
                    ],
                    "resolutions": [
                        "640x480",
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "752x416",
                        "800x448",
                        "800x600",
                        "864x480",
                        "960x544",
                        "960x720",
                        "1024x576",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                }
            ]
        },
        {
            "device_name": "video14",
            "device_path": "/dev/video14",
            "camera_name": "bcm2835-isp",
            "driver_name": "bcm2835-isp",
            "hardware_bus": "platform:bcm2835-isp",
            "modes": [],
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "STREAMING"
            ],
            "version": "6.1.63",
            "path_by_hardware": null,
            "path_by_id": null,
            "alt_name": "bcm2835-isp-capture0",
            "usb_location": null,
            "modes": []
        },
        {
            "device_name": "video15",
            "device_path": "/dev/video15",
            "camera_name": "bcm2835-isp",
            "driver_name": "bcm2835-isp",
            "hardware_bus": "platform:bcm2835-isp",
            "modes": [],
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "STREAMING"
            ],
            "version": "6.1.63",
            "path_by_hardware": null,
            "path_by_id": null,
            "alt_name": "bcm2835-isp-capture1",
            "usb_location": null,
            "modes": []
        },
        {
            "device_name": "video21",
            "device_path": "/dev/video21",
            "camera_name": "bcm2835-isp",
            "driver_name": "bcm2835-isp",
            "hardware_bus": "platform:bcm2835-isp",
            "modes": [],
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "STREAMING"
            ],
            "version": "6.1.63",
            "path_by_hardware": "/dev/v4l/by-path/platform-bcm2835-isp-video-index1",
            "path_by_id": null,
            "alt_name": "bcm2835-isp-capture0",
            "usb_location": null,
            "modes": []
        },
        {
            "device_name": "video22",
            "device_path": "/dev/video22",
            "camera_name": "bcm2835-isp",
            "driver_name": "bcm2835-isp",
            "hardware_bus": "platform:bcm2835-isp",
            "modes": [],
            "capabilities": [
                "VIDEO_CAPTURE",
                "EXT_PIX_FORMAT",
                "STREAMING"
            ],
            "version": "6.1.63",
            "path_by_hardware": "/dev/v4l/by-path/platform-bcm2835-isp-video-index2",
            "path_by_id": null,
            "alt_name": "bcm2835-isp-capture1",
            "usb_location": null,
            "modes": []
        }
    ],
    "libcamera_devices": [
        {
            "libcamera_id": "/base/soc/i2c0mux/i2c@1/ov5647@36",
            "model": "ov5647",
            "modes": [
                {
                    "format": "SGBRG10_CSI2P",
                    "resolutions": [
                        "640x480",
                        "1296x972",
                        "1920x1080",
                        "2592x1944"
                    ]
                }
            ]
        },
        {
            "libcamera_id": "/base/soc/usb@7e980000/usb-port@1/usb-port@1-1.1:1.0-046d:0825",
            "model": "UVC Camera (046d:0825)",
            "modes": [
                {
                    "format": "MJPEG",
                    "resolutions": [
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "640x480",
                        "752x416",
                        "800x448",
                        "864x480",
                        "800x600",
                        "960x544",
                        "1024x576",
                        "960x720",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                },
                {
                    "format": "YUYV",
                    "resolutions": [
                        "160x120",
                        "176x144",
                        "320x176",
                        "320x240",
                        "352x288",
                        "432x240",
                        "544x288",
                        "640x360",
                        "640x480",
                        "752x416",
                        "800x448",
                        "864x480",
                        "800x600",
                        "960x544",
                        "1024x576",
                        "960x720",
                        "1184x656",
                        "1280x720",
                        "1280x960"
                    ]
                }
            ]
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field               |   Type   | Description                             |
| ------------------- | :------: | --------------------------------------- |
| `v4l2_devices`      | [object] | An array of `V4L2 Device` objects.      |
|                     |          | #v4l2-device-spec                       |+
| `libcamera_devices` | [object] | An array of `Libcamera Device` objects. |
|                     |          | #libcamera-device-spec                  |+

| Field              |   Type   | Description                                              |
| ------------------ | :------: | -------------------------------------------------------- |
| `device_name`      |  string  | The V4L2 name assigned to the device.  This is typically |
|                    |          | the name of the file associated with the device.         |^
| `device_path`      |  string  | The absolute system path to the device file.             |
| `camera_name`      |  string  | The camera name reported by the device driver.           |
| `driver_name`      |  string  | The name of the driver loaded for the device.            |
| `alt_name`         |  string  | An alternative device name optionally reported by        |
|                    | \| null  | sysfs.  Will be `null` if the name file does not exist.  |^
| `hardware_bus`     |  string  | A description of the hardware location of the device     |
| `capabilities`     |  array   | An array of strings indicating the capabilities the      |
|                    |          | device supports as reported by V4L2.                     |^
| `version`          |  string  | The device version as reported by V4L2.                  |
| `path_by_hardware` |  string  | A symbolic link to the device based on its physical      |
|                    | \| null  | connection, ie: usb port.. Will be  `null` if no         |^
|                    |          | matching link exists.                                    |^
| `path_by_id`       |  string  | A symbolic link the the device based on its reported     |
|                    | \| null  | ID. Will be  `null` if no matching link exists.          |^
| `usb_location`     |  string  | An identifier derived from the reported usb bus and      |
|                    | \| null  | device numbers. Will be `null` for non-usb devices.      |^
| `modes`            | [object] | An array of V4L2 Mode objects, each describing supported |
|                    |          | modes.  If no modes reporting discrete resolutions are   |^
|                    |          | detected this array will be empty.                       |^
|                    |          | #v4l2-mode-spec                                          |+
{ #v4l2-device-spec } V4L2 Device

| Field         |   Type   | Description                                                  |
| ------------- | :------: | ------------------------------------------------------------ |
| `format`      |  string  | The pixel format of the mode in V4l2 fourcc format.          |
| `description` |  string  | A description of the mode provided by the driver.            |
| `flags`       | [string] | A list of strings indicating the special conditions relating |
|               |          | to the format.  Can include `COMPRESSED` and/or `EMULATED`.  |^
|               |          | An empty array indicates no flags set.                       |^
| `resolutions` | [string] | An array of strings describing the discrete resolutions      |
|               |          | supported by the mode.  Each entry is reported as            |^
|               |          | `<WIDTH>x<HEIGHT>`                                           |^
{ #v4l2-mode-spec } V4L2 Mode

| Field          |   Type   | Description                                             |
| -------------- | :------: | ------------------------------------------------------- |
| `libcamera_id` |  string  | The ID of the device as reported by libcamera.          |
| `model`        |  string  | The model name of the device.                           |
| `modes`        | [object] | An array of `Libcamera Mode` objects, each describing a |
|                |          | mode supported by the device.                           |^
|                |          | #libcamera-mode-spec                                    |+
{ #libcamera-device-spec } Libcamera Device

| Field         |   Type   | Description                                                 |
| ------------- | :------: | ----------------------------------------------------------- |
| `format`      |  string  | The pixel format of the mode.                               |
| `resolutions` | [string] | An array of strings describing the resolutions supported by |
|               |          | the mode.  Each entry is reported as `<WIDTH>x<HEIGHT>`     |^
{ #libcamera-mode-spec } Libcamera Mode
///

## Query Unassigned Canbus UUIDs

Queries the provided canbus interface for unassigned Klipper or Katapult
node IDs.

!!! Warning
    It is recommended that frontends provide users with an explanation
    of how UUID queries work and the potential pitfalls when querying
    a bus with multiple unassigned nodes.  An "unassigned" node is a
    CAN node that has not been activated by Katapult or Klipper.  If
    either Klipper or Katapult has connected to the node, it will be
    assigned a Node ID and therefore will no longer respond to queries.
    A device reset is required to remove the assignment.

    When multiple unassigned nodes are on the network, each responds to
    the query at roughly the same time.  This results in arbitration
    errors.  Nodes will retry the send until the response reports success.
    However, nodes track the count of arbitration errors, and once a
    specific threshold is reached they will go into a "bus off" state. A
    device reset is required to reset the counter and recover from "bus off".

    For this reason, it is recommended that users only issue a query when
    a single unassigned node is on the network.  If a user does wish to
    query multiple unassigned nodes it is vital that they reset all nodes
    on the network before running Klipper.

```{.http .apirequest title="HTTP Request"}
GET /machine/peripherals/canbus?interface=can0
```

```{.json .apirequest title="JSON-RPC Request"}
{
    "jsonrpc": "2.0",
    "method": "machine.peripherals.canbus",
    "params": {
        "interface": "can0"
    },
    "id": 7896
}
```

/// api-parameters
    open: True
| Name        |  Type  | Default | Description                       |
| ----------- | :----: | ------- | --------------------------------- |
| `interface` | string | `can0`  | The cansocket interface to query. |
///

/// collapse-code
```{.json .apiresponse title="Example Response"}
{
    "can_uuids": [
        {
            "uuid": "11AABBCCDD",
            "application": "Klipper"
        }
    ]
}
```
///

/// api-response-spec
    open: True

| Field       |   Type   | Description                                                    |
| ----------- | :------: | -------------------------------------------------------------- |
| `can_uuids` | [object] | An array of discovered `CAN UUID` objects, Will be empty if no |
|             |          | unassigned CAN nodes are found.                                |^
|             |          | #can-uuid-spec                                                 |+


| Field         |  Type  | Description                                                 |
| ------------- | :----: | ----------------------------------------------------------- |
| `uuid`        | string | The UUID of the unassigned node.                            |
| `application` | string | The name of the application running on the unassigned Node. |
|               |        | Should be "Klipper" or "Katapult".                          |^
{ #can-uuid-spec } Can UUID
///