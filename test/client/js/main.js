//  Main javascript for for Klippy Web Server Example
//
//  Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
//
//  This file may be distributed under the terms of the GNU GPLv3 license

import JsonRPC from "./json-rpc.js?v=0.1.2";

// API Definitions
var api = {
    printer_info: {
        url: "/printer/info",
        method: "get_printer_info"
    },
    gcode_script: {
        url: "/printer/gcode/script",
        method: "post_printer_gcode_script"
    },
    gcode_help: {
        url: "/printer/gcode/help",
        method: "get_printer_gcode_help"
    },
    start_print: {
        url: "/printer/print/start",
        method: "post_printer_print_start"
    },
    cancel_print: {
        url: "/printer/print/cancel",
        method: "post_printer_print_cancel"
    },
    pause_print: {
        url: "/printer/print/pause",
        method: "post_printer_print_pause"
    },
    resume_print: {
        url: "/printer/print/resume",
        method: "post_printer_print_resume"
    },
    query_endstops: {
        url: "/printer/query_endstops/status",
        method: "get_printer_query_endstops_status"
    },
    object_list: {
        url: "/printer/objects/list",
        method: "get_printer_objects_list"
    },
    object_status: {
        url: "/printer/objects/status",
        method: "get_printer_objects_status"
    },
    object_subscription: {
        url: "/printer/objects/subscription",
        method: {
            post: "post_printer_objects_subscription",
            get: "get_printer_objects_subscription"
        },
    },
    temperature_store: {
        url: "/server/temperature_store",
        method: "get_server_temperature_store"
    },
    estop: {
        url: "/printer/emergency_stop",
        method: "post_printer_emergency_stop"
    },
    restart: {
        url: "/printer/restart",
        method: "post_printer_restart"
    },
    firmware_restart: {
        url: "/printer/firmware_restart",
        method: "post_printer_firmware_restart"
    },

    // File Management Apis
    file_list:{
        url: "/server/files/list",
        method: "get_file_list"
    },
    metadata: {
        url: "/server/files/metadata",
        method: "get_file_metadata"
    },
    directory: {
        url: "/server/files/directory"
    },
    upload: {
        url: "/server/files/upload"
    },
    gcode_files: {
        url: "/server/files/gcodes/"
    },
    klippy_log: {
        url: "/server/files/klippy.log"
    },
    moonraker_log: {
        url: "/server/files/moonraker.log"
    },
    printer_cfg: {
        url: "/server/files/config/printer.cfg"
    },
    included_cfg_files: {
        url: "/server/files/config/include/"
    },

    // Machine APIs
    reboot: {
        url: "/machine/reboot",
        method: "post_machine_reboot"
    },
    shutdown: {
        url: "/machine/shutdown",
        method: "post_machine_shutdown"
    },

    // Access APIs
    apikey: {
        url: "/access/api_key"
    },
    oneshot_token: {
        url: "/access/oneshot_token"
    }
}

var websocket = null;
var apikey = null;
var paused = false;
var klippy_ready = false;
var api_type = 'http';
var is_printing = false;
var upload_location = "gcodes"
var file_list_type = "gcodes";
var json_rpc = new JsonRPC();

function round_float (value) {
    if (typeof value == "number" && !Number.isInteger(value)) {
        return value.toFixed(2);
    }
    return value;
}

//****************UI Update Functions****************/
var line_count = 0;
function update_term(msg) {
    let start = '<div id="line' + line_count + '">';
    $("#term").append(start + msg + "</div>");
    line_count++;
    if (line_count >= 50) {
        let rm = line_count - 50
        $("#line" + rm).remove();
    }
    if ($("#cbxAuto").is(":checked")) {
        $("#term").stop().animate({
        scrollTop: $("#term")[0].scrollHeight
        }, 800);
    }
}

const max_stream_div_width = 5;
var stream_div_width = max_stream_div_width;
var stream_div_height = 0;
function update_streamdiv(obj, attr, val) {
    if (stream_div_width >= max_stream_div_width) {
        stream_div_height++;
        stream_div_width = 0;
        $('#streamdiv').append("<div id='sdrow" + stream_div_height +
                               "' style='display: flex'></div>");
    }
    let id = obj.replace(/\s/g, "_") + "_" + attr;
    if ($("#" + id).length == 0) {
        $('#sdrow' + stream_div_height).append("<div style='width: 10em; border: 2px solid black'>"
            + obj + " " + attr + ":<div id='" + id + "'></div></div>");
        stream_div_width++;
    }

    let out = "";
    if (val instanceof Array) {
        val.forEach((value, idx, array) => {
            out += round_float(value);
            if (idx < array.length -1) {
                out += ", "
            }
        });
    } else {
        out = round_float(val);
    }
    $("#" + id).text(out);
}

function update_filelist(filelist) {
    $("#filelist").empty();
    for (let file of filelist) {
        $("#filelist").append(
            "<option value='" + file.filename + "'>" +
            file.filename + "</option>");
    }
}

function update_configlist(cfglist) {
    $("#filelist").empty();
    // Add base printer.cfg
    $("#filelist").append(
        "<option value='printer.cfg'>printer.cfg</option>");
    for (let file of cfglist) {
        let fname = "include/" + file.filename;
        $("#filelist").append(
            "<option value='" + fname + "'>" +
            fname + "</option>");
    }
}

var last_progress = 0;
function update_progress(loaded, total) {
    let progress = parseInt(loaded / total * 100);
    if (progress - last_progress > 1 || progress >= 100) {
        if (progress >= 100) {
            last_progress = 0;
            progress = 100;
            console.log("File transfer complete")
        } else {
            last_progress = progress;
        }
        $('#upload_progress').text(progress);
        $('#progressbar').val(progress);
    }
}

function update_error(cmd, msg) {
    if (msg instanceof Object)
        msg = JSON.stringify(msg);
    // Handle server error responses
    update_term("Command [" + cmd + "] resulted in an error: " + msg);
    console.log("Error processing " + cmd +": " + msg);
}
//***********End UI Update Functions****************/

//***********Websocket-Klipper API Functions (JSON-RPC)************/
function get_file_list() {
    let args = {root: file_list_type}
    json_rpc.call_method_with_kwargs(api.file_list.method, args)
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        if (file_list_type == "config")
            update_configlist(result);
        else
            update_filelist(result);
    })
    .catch((error) => {
        update_error(api.file_list.method, error);
    });
}

function get_klippy_info() {
    // A "get_klippy_info" websocket request.  It returns
    // the hostname (which should be equal to location.host), the
    // build version, and if the Host is ready for commands.  Its a
    // good idea to fetch this information after the websocket connects.
    // If the Host is in a "ready" state, we can do some initialization
    json_rpc.call_method(api.printer_info.method)
    .then((result) => {

        if (result.is_ready) {
            if (!klippy_ready) {
                update_term("Klippy Hostname: " + result.hostname +
                    " | CPU: " + result.cpu +
                    " | Build Version: " + result.version);
                klippy_ready = true;
                // Klippy has transitioned from not ready to ready.
                // It is now safe to fetch the file list.
                get_file_list();

                // Add our subscriptions the the UI is configured to do so.
                if ($("#cbxSub").is(":checked")) {
                    // If autosubscribe is check, request the subscription now
                    const sub = {
                        gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                        idle_timeout: [],
                        pause_resume: [],
                        toolhead: [],
                        virtual_sdcard: [],
                        heater_bed: [],
                        extruder: ["temperature", "target"],
                        fan: []};
                    add_subscription(sub);
                } else {
                    get_status({idle_timeout: [], pause_resume: []});
                }
            }
        } else {
            if (result.error_detected) {
                update_term(result.message);
            } else {
                update_term("Waiting for Klippy ready status...");
            }
            console.log("Klippy Not Ready, checking again in 2s: ");
            setTimeout(() => {
                get_klippy_info();
            }, 2000);
        }

    })
    .catch((error) => {
        update_error(api.printer_info.method, error);
    });
}

function run_gcode(gcode) {
    json_rpc.call_method_with_kwargs(
        api.gcode_script.method, {script: gcode})
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        update_term(result);
    })
    .catch((error) => {
        update_error(api.gcode_script.method, error);
    });
}

function get_status(printer_objects) {
    // Note that this is just an example of one particular use of get_status.
    // In a robust client you would likely pass a callback to this function
    // so that you can respond to various status requests.  It would also
    // be possible to subscribe to status requests and update the UI accordingly
    json_rpc.call_method_with_kwargs(api.object_status.method, printer_objects)
    .then((result) => {
        if ("idle_timeout" in result) {
            // Its a good idea that the user understands that some functionality,
            // such as file manipulation, is disabled during a print.  This can be
            // done by disabling buttons or by notifying the user via a popup if they
            // click on an action that is not allowed.
            if ("state" in result.idle_timeout) {
                let state = result.idle_timeout.state.toLowerCase();
                is_printing = (state == "printing");
                if (!$('#cbxFileTransfer').is(":checked")) {
                    $('.toggleable').prop(
                        'disabled', (api_type == 'websocket' || is_printing));
                }
                $('#btnstartprint').prop('disabled', is_printing);
            }
        }
        if ("pause_resume" in result) {
            if ("is_paused" in result.pause_resume) {
                paused = result.pause_resume.is_paused;
                let label = paused ? "Resume Print" : "Pause Print";
                $('#btnpauseresume').text(label);
            }
        }
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_status.method, error);
    });
}

function get_object_info() {
    json_rpc.call_method(api.object_list.method)
    .then((result) => {
        // result will be a dictionary containing all available printer
        // objects available for query or subscription
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_list.method, error);
    });
}

function add_subscription(printer_objects) {
    json_rpc.call_method_with_kwargs(
        api.object_subscription.method.post, printer_objects)
    .then((result) => {
        // result is simply an "ok" acknowledgement that subscriptions
        // have been added for requested objects
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_subscription.method.post, error);
    });
}

function get_subscribed() {
    json_rpc.call_method(api.object_subscription.method.get)
    .then((result) => {
        // result is a dictionary containing all currently subscribed
        // printer objects/attributes
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_subscription.method.get, error);
    });
}

function get_endstops() {
    json_rpc.call_method(api.query_endstops.method)
    .then((result) => {
        // A response to a "get_endstops" websocket request.
        // The result contains an object of key/value pairs,
        // where the key is the endstop (ie:x, y, or z) and the
        // value is either "open" or "TRIGGERED".
        console.log(result);
    })
    .catch((error) => {
        update_error(api.query_endstops.method, error);
    });
}

function start_print(file_name) {
    json_rpc.call_method_with_kwargs(
        api.start_print.method, {'filename': file_name})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.start_print.method, error);
    });
}

function cancel_print() {
    json_rpc.call_method(api.cancel_print.method)
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been canceled
        console.log(result);
    })
    .catch((error) => {
        update_error(api.cancel_print.method, error);
    });
}

function pause_print() {
    json_rpc.call_method(api.pause_print.method)
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been paused
        console.log("Pause Command Executed")
    })
    .catch((error) => {
        update_error(api.pause_print.method, error);
    });
}

function resume_print() {
    json_rpc.call_method(api.resume_print.method)
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been resumed
        console.log("Resume Command Executed")
    })
    .catch((error) => {
        update_error(api.resume_print.method, error);
    });
}

function get_metadata(file_name) {
    json_rpc.call_method_with_kwargs(
        api.metadata.method, {'filename': file_name})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.metadata.method, error);
    });
}

function get_gcode_help() {
    json_rpc.call_method(api.gcode_help.method)
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been resumed
        console.log(result)
    })
    .catch((error) => {
        update_error(api.gcode_help.method, error);
    });
}

function emergency_stop() {
    json_rpc.call_method(api.estop.method)
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has been resumed
        console.log(result)
    })
    .catch((error) => {
        update_error(api.estop.method, error);
    });
}

function restart() {
    // We are unlikely to receive a response from a restart
    // request as the websocket will disconnect, so we will
    // call json_rpc.notify instead of call_function.
    json_rpc.notify(api.restart.method);
}

function firmware_restart() {
    // As above, we would not likely receive a response from
    // a firmware_restart request
    json_rpc.notify(api.firmware_restart.method);
}

function reboot() {
    json_rpc.notify(api.reboot.method);
}

function shutdown() {
    json_rpc.notify(api.shutdown.method);
}

//***********End Websocket-Klipper API Functions (JSON-RPC)********/

//***********Klipper Event Handlers (JSON-RPC)*********************/

function handle_gcode_response(response) {
    // This event contains all gcode responses that would
    // typically be printed to the terminal.  Its possible
    // That multiple lines can be bundled in one response,
    // so if displaying we want to be sure we split them.
    let messages = response.split("\n");
    for (let msg of messages) {
        update_term(msg);
    }
}
json_rpc.register_method("notify_gcode_response", handle_gcode_response);

function handle_status_update(status) {
    // This is subscribed status data.  Here we do a nested
    // for-each to determine the klippy object name ("name"),
    // the attribute we want ("attr"), and the attribute's
    // value ("val")
    for (let name in status) {
        let obj = status[name];
        for (let attr in obj) {
            let full_name = name + "." + attr;
            let val = obj[attr];
            switch(full_name) {
                case "virtual_sdcard.filename":
                    $('#filename').prop("hidden", val == "");
                    $('#filename').text("Loaded File: " + val);
                    break;
                case "pause_resume.is_paused":
                    if (paused != val) {
                        paused = val;
                        let label = paused ? "Resume Print" : "Pause Print";
                        $('#btnpauseresume').text(label);
                        console.log("Paused State Changed: " + val);
                        update_streamdiv(name, attr, val);
                    }
                    break;
                case "idle_timeout.state":
                    let state = val.toLowerCase();
                    if (state != is_printing) {
                        is_printing = (state == "printing");
                        if (!$('#cbxFileTransfer').is(":checked")) {
                            $('.toggleable').prop(
                                'disabled', (api_type == 'websocket' || is_printing));
                        }
                        $('#btnstartprint').prop('disabled', is_printing);
                        update_streamdiv(name, attr, val);
                    }
                    break;
                default:
                    update_streamdiv(name, attr, val);

            }
        }
    }
}
json_rpc.register_method("notify_status_update", handle_status_update);

function handle_klippy_state(state) {
    // Klippy state can be "ready", "disconnect", and "shutdown".  This
    // differs from Printer State in that it represents the status of
    // the Host software
    switch(state) {
        case "ready":
            // It would be possible to use this event to notify the
            // client that the printer has started, however the server
            // may not start in time for clients to receive this event.
            // It is being kept in case
            update_term("Klippy Ready");
            break;
        case "disconnect":
            // Klippy has disconnected from the MCU and is prepping to
            // restart.  The client will receive this signal right before
            // the websocket disconnects.  If we need to do any kind of
            // cleanup on the client to prepare for restart this would
            // be a good place.
            klippy_ready = false;
            update_term("Klippy Disconnected");
            setTimeout(() => {
                get_klippy_info();
            }, 2000);
            break;
        case "shutdown":
            // Either M112 was entered or there was a printer error.  We
            // probably want to notify the user and disable certain controls.
            klippy_ready = false;
            update_term("Klipper has shutdown, check klippy.log for info");
            break;
    }
}
json_rpc.register_method("notify_klippy_state_changed", handle_klippy_state);

function handle_file_list_changed(file_info) {
    // This event fires when a client has either added or removed
    // a gcode file.
    if (file_list_type == file_info.root)
        get_file_list(file_info.root);
    console.log("Filelist Changed:");
    console.log(file_info);
}
json_rpc.register_method("notify_filelist_changed", handle_file_list_changed);

//***********End Klipper Event Handlers (JSON-RPC)*****************/

// The function below is an example of one way to use JSON-RPC's batch send
// method.  Generally speaking it is better and easier to use individual
// requests, as matching requests with responses in a batch requires more
// work from the developer
function send_gcode_batch(gcodes) {
    // The purpose of this function is to provide an example of a JSON-RPC
    // "batch request".  This function takes an array of gcodes and sends
    // them as a batch command.  This would behave like a Klipper Gcode Macro
    // with one signficant difference...if one gcode in the batch requests
    // results in an error, Klipper will continue to process subsequent gcodes.
    // A Klipper Gcode Macro will immediately stop execution of the macro
    // if an error is encountered.

    let batch = [];
    for (let gc of gcodes) {
        batch.push(
            {
                method: 'post_printer_gcode_script',
                type: 'request',
                params: {script: gc}
            });
    }

    // The batch request returns a promise with all results
    json_rpc.send_batch_request(batch)
    .then((results) => {
        for (let res of results) {
            // Each result is an object with three keys:
            // method:  The method executed for this result
            // index:  The index of the original request
            // result: The successful result


            // Use the index to look up the gcode parameter in the original
            // request
            let orig_gcode = batch[res.index].params[0];
            console.log("Batch Gcode " + orig_gcode +
            " successfully executed with result: " + res.result);
        }
    })
    .catch((err) => {
        // Like the result, the error is an object.  However there
        // is an "error" in place of the "result key"
        let orig_gcode = batch[err.index].params[0];
        console.log("Batch Gcode <" + orig_gcode +
        "> failed with error: " + err.error.message);
    });
}

// The function below demonstrates a more useful method of sending
// a client side gcode macro.  Like a Klipper macro, gcode execution
// will stop immediately upon encountering an error.  The advantage
// of a client supporting their own macros is that there is no need
// to restart the klipper host after creating or deleting them.
async function send_gcode_macro(gcodes) {
    for (let gc of gcodes) {
        try {
            let result = await json_rpc.call_method_with_kwargs(
                'post_printer_gcode_script', {script: gc});
        } catch (err) {
            console.log("Error executing gcode macro: " + err.message);
            break;
        }
    }
}

// A simple reconnecting websocket
class KlippyWebsocket {
    constructor(addr) {
        this.base_address = addr;
        this.connected = false;
        this.ws = null;
        this.onmessage = null;
        this.onopen = null;
        this.connect();
    }

    connect() {
        // Doing the websocket connection here allows the websocket
        // to reconnect if its closed. This is nice as it allows the
        // client to easily recover from Klippy restarts without user
        // intervention
        if (apikey != null) {
            // Fetch a oneshot token to pass websocket authorization
            let token_settings = {
                url: api.oneshot_token.url,
                headers: {
                    "X-Api-Key": apikey
                }
            }
            $.get(token_settings, (data, status) => {
                let token = data.result;
                let url = this.base_address + "/websocket?token=" + token;
                this.ws = new WebSocket(url);
                this._set_callbacks();
            }).fail(() => {
                console.log("Failed to retreive oneshot token");
            })
        } else {
            this.ws = new WebSocket(this.base_address + "/websocket");
            this._set_callbacks();
        }
    }

    _set_callbacks() {
        this.ws.onopen = () => {
            this.connected = true;
            console.log("Websocket connected");
            if (this.onopen != null)
                this.onopen();
        };

        this.ws.onclose = (e) => {
            klippy_ready = false;
            this.connected = false;
            console.log("Websocket Closed, reconnecting in 1s: ", e.reason);
            setTimeout(() => {
                this.connect();
            }, 1000);
        };

        this.ws.onerror = (err) => {
            klippy_ready = false;
            console.log("Websocket Error: ", err.message);
            this.ws.close();
        };

        this.ws.onmessage = (e) => {
            // Tornado Server Websockets support text encoded frames.
            // The onmessage callback will send the data straight to
            // JSON-RPC
            this.onmessage(e.data);
        };
    }

    send(data) {
        // Only allow send if connected
        if (this.connected) {
            this.ws.send(data);
        } else {
            console.log("Websocket closed, cannot send data");
        }
    }

    close() {
        // TODO: Cancel the timeout
        this.ws.close();
    }

};

function create_websocket(url) {
    if (websocket != null)
        websocket.close()
    let prefix = window.location.protocol == "https" ? "wss://" : "ws://";
    let ws_url = prefix + location.host
    websocket = new KlippyWebsocket(ws_url);
    websocket.onopen = () => {
        // Depending on the state of the printer, all enpoints may not be
        // available when the websocket is first opened.  The "get_klippy_info"
        // method is available, and should be used to determine if Klipper is
        // in the "ready" state.  When Klipper is "ready", all endpoints should
        // be registered and available.

        // These could be implemented JSON RPC Batch requests and send both
        // at the same time, however it is easier to simply do them
        // individually
        get_klippy_info();
    };
    json_rpc.register_transport(websocket);
}

function check_authorization() {
    // send a HTTP "run gcode" command
    let settings = {
        url: api.printer_info.url,
        statusCode: {
            401: function() {
                    // Show APIKey Popup
                    let result = window.prompt("Enter a valid API Key:", "");
                    if (result == null || result.length != 32) {
                        console.log("Invalid API Key: " + result);
                        apikey = null;
                    } else {
                        apikey = result;
                    }
                    check_authorization();
                }
            }
    }
    if (apikey != null)
        settings.headers = {"X-Api-Key": apikey};
    $.get(settings, (data, status) => {
        // Create a websocket if /printer/info successfully returns
        create_websocket();
    })
}

function do_download(url) {
    $('#hidden_link').attr('href', url);
    $('#hidden_link')[0].click();
}

window.onload = () => {
    // Handle changes between the HTTP and Websocket API
    $('.reqws').prop('disabled', true);
    $('input[type=radio][name=test_type]').on('change', function() {
        api_type = $(this).val();
        let disable_transfer = (!$('#cbxFileTransfer').is(":checked") && is_printing);
        $('.toggleable').prop(
            'disabled', (api_type == 'websocket' || disable_transfer));
        $('.reqws').prop('disabled', (api_type == 'http'));
        $('#apimethod').prop('hidden', (api_type == "websocket"));
        $('#apiargs').prop('hidden', (api_type == "http"));
    });

    $('input[type=radio][name=file_type]').on('change', function() {
        file_list_type = $(this).val();
        get_file_list();
    });

    $('#cbxFileTransfer').on('change', function () {
        let disabled = false;
        if (!$(this).is(":checked")) {
            disabled = (api_type == 'websocket' || is_printing);
        }
        $('.toggleable').prop( 'disabled', disabled);
    });

    // Send a gcode.  Note that in the test client nearly every control
    // checks a radio button to see if the request should be sent via
    // the REST API or the Websocket API.  A real client will choose one
    // or the other, so the "api_type" check will be unnecessary
    $('#gcform').submit((evt) => {
        let line = $('#gcform [type=text]').val();
        $('#gcform [type=text]').val('');
        update_term(line);
        if (api_type == 'http') {
            // send a HTTP "run gcode" command
            let settings = {url: api.gcode_script.url + "?script=" + line};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (data, status) => {
                update_term(data.result);
            });
        } else {
            // Send a websocket "run gcode" command.
            run_gcode(line);
        }
        return false;
    });

    // Send a command to the server.  This can be either an HTTP
    // get request formatted as the endpoint(ie: /objects) or
    // a websocket command.  The websocket command needs to be
    // formatted as if it were already json encoded.
    $('#apiform').submit((evt) => {
        // Send to a user defined endpoint and log the response
        if (api_type == 'http') {
            let sendtype = $("input[type=radio][name=api_cmd_type]:checked").val();
            let url = $('#apirequest').val();
            let settings = {url: url}
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            if (sendtype == "get") {
                console.log("Sending GET " + url);
                $.get(settings, (resp, status) => {
                    console.log(resp);
                });
            } else if (sendtype == "post") {
                console.log("Sending POST " + url);
                $.post(settings, (resp, status) => {
                    console.log(resp);
                });
            } else if (sendtype == "delete") {
                console.log("Sending DELETE " + url);
                settings.method = "DELETE";
                settings.success = (resp, status) => {
                    console.log(resp);
                };
                $.ajax(settings);
            }
        } else {
            let method = $('#apirequest').val().trim();
            let args = $('#apiargs').val();
            if (args != "") {
                try {
                    args = JSON.parse("{" + args + "}");
                } catch (error) {
                    console.log("Unable to parse arguments");
                    return
                }
                json_rpc.call_method_with_kwargs(method, args)
                .then((result) => {
                    console.log(result);
                })
                .catch((error) => {
                    update_error(method, error);
                });
            } else {
                json_rpc.call_method(method)
                .then((result) => {
                    console.log(result);
                })
                .catch((error) => {
                    update_error(method, error);
                });
            }
        }
        return false;
    });

    //  Hidden file element's click is forwarded to the button
    $('#btnupload').click(() => {
        if (api_type == "http") {
            upload_location = file_list_type;
            $('#upload-file').click();
        } else {
            console.log("File Upload not supported over websocket")
        }
    });

    // Uploads a selected file to the server
    $('#upload-file').change(() => {
        update_progress(0, 100);
        let file = $('#upload-file').prop('files')[0];
        if (file) {
            console.log("Sending Upload Request...");
            // It might not be a bad idea to validate that this is
            // a gcode file here, and reject and other files.

            // If you want to allow multiple selections, the below code should be
            // done in a loop, and the 'let file' above should be the entire
            // array of files and not the first element
            if (api_type == 'http') {
                let fdata = new FormData();
                fdata.append("file", file);
                if (upload_location.startsWith("config")) {
                    fdata.append("root", "config");
                    if (upload_location == "config_main")
                        fdata.append("primary_config", "true");
                } else {
                    fdata.append("root", upload_location);
                }
                let settings = {
                    url: api.upload.url,
                    data: fdata,
                    cache: false,
                    contentType: false,
                    processData: false,
                    method: 'POST',
                    xhr: () => {
                        let xhr = new window.XMLHttpRequest();
                        xhr.upload.addEventListener("progress", (evt) => {
                            if (evt.lengthComputable) {
                                update_progress(evt.loaded, evt.total);
                            }
                        }, false);
                        return xhr;
                    },
                    success: (resp, status) => {
                        console.log(resp);
                        return false;
                    }
                };
                if (apikey != null)
                    settings.headers = {"X-Api-Key": apikey};
                $.ajax(settings);
            } else {
                console.log("File Upload not supported over websocket")
            }
            $('#upload-file').val('');
        }
    });

    // Download a file from the server.  This implementation downloads
    // whatever is selected in the <select> element
    $('#btndownload').click(() => {
        update_progress(0, 100);
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = api.gcode_files.url + filename;
                if (file_list_type == "config") {
                    url = api.included_cfg_files.url + filename;
                    if (filename.startsWith("include/")) {
                        url = api.included_cfg_files.url + filename.slice(8);
                    } else if (filename == "printer.cfg") {
                        url = api.printer_cfg.url;
                    }
                    else {
                        console.log("Cannot download file: " + filename);
                        return false;
                    }
                }
                let dl_url = "http://" + location.host + url;
                if (apikey != null) {
                    let settings = {
                        url: api.oneshot_token.url,
                        headers: {"X-Api-Key": apikey}
                    };
                    $.get(settings, (resp, status) => {
                        let token = resp.result;
                        dl_url += "?token=" + token;
                        do_download(dl_url);
                        return false;
                    });
                } else {
                    do_download(dl_url);
                }
            } else {
                console.log("File Download not supported over websocket")
            }
        }
    });

    // Delete a file from the server.  This implementation deletes
    // whatever is selected in the <select> element
    $("#btndelete").click(() =>{
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = api.gcode_files.url + filename;
                if (file_list_type == "config") {
                    url = api.included_cfg_files.url + filename;
                    if (filename.startsWith("include/")) {
                        url = api.included_cfg_files.url + filename.slice(8);
                    } else {
                        console.log("Cannot Delete printer.cfg");
                        return false;
                    }
                }
                let settings = {
                    url: url,
                    method: 'DELETE',
                    success: (resp, status) => {
                        console.log(resp);
                        return false;
                    }
                };
                if (apikey != null)
                    settings.headers = {"X-Api-Key": apikey};
                $.ajax(settings);
            } else {
                console.log("File Delete not supported over websocket")
            }
        }
    });

    // Start a print.  This implementation starts the print for the
    // file selected in the <select> element
    $("#btnstartprint").click(() =>{
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let settings = {url: api.start_print.url + "?filename=" + filename};
                if (apikey != null)
                    settings.headers = {"X-Api-Key": apikey};
                $.post(settings, (resp, status) => {
                        console.log(resp);
                        return false;
                });
            } else {
                start_print(filename);
            }
        }
    });

    // Pause/Resume a currently running print.  The specific gcode executed
    // is configured in printer.cfg.
    $("#btnpauseresume").click(() =>{
        if (api_type == 'http') {
            let settings = {url: paused ? api.resume_print.url : api.pause_print.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp.result);
                return false;
            });
        } else {
            if (paused) {
                resume_print();
            } else {
                pause_print();
            }
        }
    });

    // Cancel a currently running print. The specific gcode executed
    // is configured in printer.cfg.
    $("#btncancelprint").click(() =>{
        if (api_type == 'http') {
            let settings = {url: api.cancel_print.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            cancel_print();
        }
    });

    // Get File Metadata
    $("#btngetmetadata").click(() =>{
        let filename = $("#filelist").val();
        if (filename) {
            if (api_type == 'http') {
                let url = api.metadata.url + "?filename=" + filename;
                let settings = {url: url};
                if (apikey != null)
                    settings.headers = {"X-Api-Key": apikey};
                $.get(settings, (resp, status) => {
                        console.log(resp);
                        return false;
                });
            } else {
                get_metadata(filename);
            }
        }
    });

    // Refresh File List
    $("#btngetfiles").click(() =>{
        if (api_type == 'http') {
            let url = api.file_list.url + "?root=" + file_list_type;
            let settings = {url: url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                    console.log(resp);
                    return false;
            });
        } else {
            get_file_list();
        }
    });

    $('#btnwritecfg').click(() => {
        if (api_type == "http") {
            upload_location = "config_main";
            $('#upload-file').click();
        } else {
            console.log("File Upload not supported over websocket")
        }
    });

    $('#btngetcfg').click(() => {
        if (api_type == 'http') {
            let dl_url = "http://" + location.host + api.printer_cfg.url;
            if (apikey != null) {
                let settings = {
                    url: api.oneshot_token.url,
                    headers: {"X-Api-Key": apikey}
                };
                $.get(settings, (resp, status) => {
                    let token = resp.result;
                    dl_url += "?token=" + token;
                    do_download(dl_url);
                    return false;
                });
            } else {
                do_download(dl_url);
            }
        } else {
            console.log("Get Log not supported over websocket")
        }
    });

    $('#btnqueryendstops').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.query_endstops.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            get_endstops();
        }
    });

     // Post Subscription Request
     $('#btnsubscribe').click(() => {
        if (api_type == 'http') {
            let url = api.object_subscription.url + "?gcode=gcode_position,speed,speed_factor,extrude_factor" +
                    "&toolhead&virtual_sdcard&heater_bed&extruder=temperature,target&fan&idle_timeout&pause_resume";
            let settings = {url: url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (data, status) => {
                console.log(data);
            });
        } else {
            const sub = {
                gcode: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                idle_timeout: [],
                pause_resume: [],
                toolhead: [],
                virtual_sdcard: [],
                heater_bed: [],
                extruder: ["temperature", "target"],
                fan: []};
            add_subscription(sub);
        }
    });

    // Get subscription info
    $('#btngetsub').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.object_subscription.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                console.log(resp);
            });
        } else {
            get_subscribed();
        }
    });

    $('#btngethelp').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.gcode_help.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            get_gcode_help();
        }
    });

    $('#btngetobjs').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.object_list.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            get_object_info();
        }
    });

    $('#btnsendbatch').click(() => {
        let default_gcs = "M118 This works,RESPOND TYPE=invalid,M118 Execs Despite an Error";
        let result = window.prompt("Enter a set of comma separated gcodes:", default_gcs);
        if (result == null || result == "") {
            console.log("Batch GCode Send Cancelled");
            return;
        }
        let gcodes = result.trim().split(',');
        send_gcode_batch(gcodes);
    });

    $('#btnsendmacro').click(() => {
        let default_gcs =  "M118 This works,RESPOND TYPE=invalid,M118 Should Not Exec";
        let result = window.prompt("Enter a set of comma separated gcodes:", default_gcs);
        if (result == null || result == "") {
            console.log("Gcode Macro Cancelled");
            return;
        }
        let gcodes = result.trim().split(',');
        send_gcode_macro(gcodes);
    });

    $('#btnestop').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.estop.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            emergency_stop();
        }
    });

    $('#btnrestart').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.restart.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            restart();
        }
    });

    $('#btnfirmwarerestart').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.firmware_restart.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            firmware_restart();
        }
    });

    $('#btnreboot').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.reboot.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            reboot();
        }
    });

    $('#btnshutdown').click(() => {
        if (api_type == 'http') {
            let settings = {url: api.shutdown.url};
            if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.post(settings, (resp, status) => {
                console.log(resp);
                return false;
            });
        } else {
            shutdown();
        }
    });

    $('#btngetlog').click(() => {
        if (api_type == 'http') {
            let dl_url = "http://" + location.host + api.klippy_log.url;
            if (apikey != null) {
                let settings = {
                    url: api.oneshot_token.url,
                    headers: {"X-Api-Key": apikey}
                };
                $.get(settings, (resp, status) => {
                    let token = resp.result;
                    dl_url += "?token=" + token;
                    do_download(dl_url);
                    return false;
                });
            } else {
                do_download(dl_url);
            }
        } else {
            console.log("Get Log not supported over websocket")
        }
    });

    $('#btnmoonlog').click(() => {
        if (api_type == 'http') {
            let dl_url = "http://" + location.host + api.moonraker_log.url;
            if (apikey != null) {
                let settings = {
                    url: api.oneshot_token.url,
                    headers: {"X-Api-Key": apikey}
                };
                $.get(settings, (resp, status) => {
                    let token = resp.result;
                    dl_url += "?token=" + token;
                    do_download(dl_url);
                    return false;
                });
            } else {
                do_download(dl_url);
            }
        } else {
            console.log("Get Log not supported over websocket")
        }
    });

    check_authorization();
};
