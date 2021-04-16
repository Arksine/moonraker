//  Main javascript for for Klippy Web Server Example
//
//  Copyright (C) 2019 Eric Callahan <arksine.code@gmail.com>
//
//  This file may be distributed under the terms of the GNU GPLv3 license

import JsonRPC from "./json-rpc.js?v=0.1.2";

var origin = location.origin;
var ws_url = (window.location.protocol == "https" ? "wss://" : "ws://")
    + location.host;

// API Definitions
var api = {
    printer_info: {
        url: "/printer/info",
        method: "printer.info"
    },
    gcode_script: {
        url: "/printer/gcode/script",
        method: "printer.gcode.script"
    },
    gcode_help: {
        url: "/printer/gcode/help",
        method: "printer.gcode.help"
    },
    start_print: {
        url: "/printer/print/start",
        method: "printer.print.start"
    },
    cancel_print: {
        url: "/printer/print/cancel",
        method: "printer.print.cancel"
    },
    pause_print: {
        url: "/printer/print/pause",
        method: "printer.print.pause"
    },
    resume_print: {
        url: "/printer/print/resume",
        method: "printer.print.resume"
    },
    query_endstops: {
        url: "/printer/query_endstops/status",
        method: "printer.query_endstops.status"
    },
    object_list: {
        url: "/printer/objects/list",
        method: "printer.objects.list"
    },
    object_status: {
        url: "/printer/objects/query",
        method: "printer.objects.query"
    },
    object_subscription: {
        url: "/printer/objects/subscribe",
        method: "printer.objects.subscribe"
    },
    temperature_store: {
        url: "/server/temperature_store",
        method: "server.temperature_store"
    },
    estop: {
        url: "/printer/emergency_stop",
        method: "printer.emergency_stop"
    },
    restart: {
        url: "/printer/restart",
        method: "printer.restart"
    },
    firmware_restart: {
        url: "/printer/firmware_restart",
        method: "printer.firmware_restart"
    },

    // File Management Apis
    file_list:{
        url: "/server/files/list",
        method: "server.files.list"
    },
    metadata: {
        url: "/server/files/metadata",
        method: "server.files.metadata"
    },
    directory: {
        url: "/server/files/directory",
        method: {
            get: "server.files.get_directory",
            post: "server.files.post_directory",
            delete: "server.files.delete_directory"
        }
    },
    move: {
        url: "/server/files/move",
        method: "server.files.move"
    },
    copy: {
        url: "/server/files/copy",
        method: "server.files.copy"
    },
    delete_file: {
        method: "server.files.delete_file"
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
    cfg_files: {
        url: "/server/files/config/"
    },
    cfg_examples: {
        url: "/server/files/config_examples/"
    },

    // Server APIs
    server_info: {
        url: "/server/info",
        method: "server.info"
    },

    // Machine APIs
    reboot: {
        url: "/machine/reboot",
        method: "machine.reboot"
    },
    shutdown: {
        url: "/machine/shutdown",
        method: "machine.shutdown"
    },

    // Access APIs
    apikey: {
        url: "/access/api_key"
    },
    oneshot_token: {
        url: "/access/oneshot_token"
    },
    login: {
        url: "/access/login"
    },
    logout: {
        url: "/access/logout"
    },
    refresh_jwt: {
        url: "/access/refresh_jwt"
    },
    user: {
        url: "/access/user"
    },
    reset_password: {
        url: "/access/user/password"
    }
}

var websocket = null;
var apikey = null;
var auth_token = null;
var refresh_token = window.localStorage.getItem('refresh_token');
var paused = false;
var klippy_ready = false;
var api_type = 'http';
var is_printing = false;
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
    if (line_count >= 200) {
        let rm = line_count - 200
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
            if (!klippy_ready)
                update_term("Klippy Ready");
            break;
        case "shutdown":
            // Either M112 was entered or there was a printer error.  We
            // probably want to notify the user and disable certain controls.
            klippy_ready = false;
            update_term("Klipper has shutdown, check klippy.log for info");
            break;
    }
}

function process_mesh(result) {
    let bed_mesh = result.status.bed_mesh;
    let matrix = bed_mesh.probed_matrix;
    if (!(matrix instanceof Array) ||  matrix.length < 3 ||
        !(matrix[0] instanceof Array) || matrix[0].length < 3) {
        // make sure that the matrix is valid
        console.log("Invalid Mesh Received");
        return;
    }
    let coordinates = [];
    let x_distance = (bed_mesh.mesh_max[0] - bed_mesh.mesh_min[0]) /
        (matrix[0].length - 1);
    let y_distance = (bed_mesh.mesh_max[1] - bed_mesh.mesh_min[1]) /
        (matrix.length - 1);
    let x_idx = 0;
    let y_idx = 0;
    for (const x_axis of matrix) {
        x_idx = 0;
        let y_coord = bed_mesh.mesh_min[1] + (y_idx * y_distance);
        for (const z_coord of x_axis) {
            let x_coord = bed_mesh.mesh_min[0] + (x_idx * x_distance);
            x_idx++;
            coordinates.push([x_coord, y_coord, z_coord]);
        }
        y_idx++;
    }
    console.log("Processed Mesh Coordinates:");
    console.log(coordinates);
}
//***********End UI Update Functions****************/

//***********Websocket-Klipper API Functions (JSON-RPC)************/
function get_file_list(root) {
    let args = {root: root}
    json_rpc.call_method_with_kwargs(api.file_list.method, args)
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        console.log(result);
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

        if (websocket.id == null)
            get_websocket_id();

        if (result.state == "ready") {
            if (!klippy_ready) {
                update_term("Klippy Hostname: " + result.hostname +
                    " | CPU: " + result.cpu_info +
                    " | Build Version: " + result.software_version);
                klippy_ready = true;

                // Add our subscriptions the the UI is configured to do so.
                if ($("#cbxSub").is(":checked")) {
                    // If autosubscribe is check, request the subscription now
                    const sub = {
                        objects: {
                            gcode_move: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                            idle_timeout: null,
                            pause_resume: null,
                            toolhead: null,
                            virtual_sdcard: null,
                            heater_bed: null,
                            extruder: ["temperature", "target"],
                            fan: null,
                            print_stats: null}
                        };
                    add_subscription(sub);
                } else {
                    get_status({idle_timeout: null, pause_resume: null});
                }
            }
        } else {
            if (result.state == "error") {
                update_term(result.state_message);
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
        setTimeout(() => {
            get_klippy_info();
        }, 2000);
    });
}

function get_websocket_id() {
    json_rpc.call_method("server.websocket.id")
    .then((result) => {
        // result is an "ok" acknowledgment that the gcode has
        // been successfully processed
        websocket.id = result.websocket_id;
        console.log(`Websocket ID Received: ${result.websocket_id}`);
    })
    .catch((error) => {
        update_error("server.websocket.id", error);
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

function get_object_list() {
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

function get_mesh() {
    json_rpc.call_method_with_kwargs(
        api.object_status.method, {objects: {bed_mesh: null}})
    .then((result) => {
        process_mesh(result);
    })
    .catch((error) => {
        update_error(api.object_status.method, error);
    });
}

function add_subscription(printer_objects) {
    json_rpc.call_method_with_kwargs(
        api.object_subscription.method, printer_objects)
    .then((result) => {
        // result is the the state from all fetched data
        handle_status_update(result.status)
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_subscription.method, error);
    });
}

function get_subscribed() {
    json_rpc.call_method(api.object_subscription.method)
    .then((result) => {
        // result is a dictionary containing all currently subscribed
        // printer objects/attributes
        console.log(result);
    })
    .catch((error) => {
        update_error(api.object_subscription.method, error);
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

function make_directory(dir_path) {
    json_rpc.call_method_with_kwargs(
        api.directory.method.post, {'path': dir_path})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.directory.method.post, error);
    });
}

function delete_directory(dir_path) {
    json_rpc.call_method_with_kwargs(
        api.directory.method.delete,
        {'path': dir_path, 'force': true})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.directory.method.delete, error);
    });
}

function delete_file(file_path) {
    json_rpc.call_method_with_kwargs(
        api.delete_file.method, {'path': file_path})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.delete_file.method, error);
    });
}

function copy_item(source_path, dest_path) {
    json_rpc.call_method_with_kwargs(
        api.copy.method, {'source': source_path, 'dest': dest_path})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.copy.method, error);
    });
}

function move_item(source_path, dest_path) {
    json_rpc.call_method_with_kwargs(
        api.move.method, {'source': source_path, 'dest': dest_path})
    .then((result) => {
        // result is an "ok" acknowledgement that the
        // print has started
        console.log(result);
    })
    .catch((error) => {
        update_error(api.move.method, error);
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
                case "print_stats.filename":
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
                case "webhooks.state":
                    handle_klippy_state(val);
                default:
                    update_streamdiv(name, attr, val);

            }
        }
    }
}
json_rpc.register_method("notify_status_update", handle_status_update);

function handle_klippy_disconnected() {
    // Klippy has disconnected from the MCU and is prepping to
    // restart.  The client will receive this signal right before
    // the websocket disconnects.  If we need to do any kind of
    // cleanup on the client to prepare for restart this would
    // be a good place.
    klippy_ready = false;
    update_term("Klippy Disconnected");
}
json_rpc.register_method("notify_klippy_disconnected", handle_klippy_disconnected);

function handle_klippy_ready() {
    update_term("Klippy Is READY");
    console.log("Klippy Ready Recieved");
    get_klippy_info();
}
json_rpc.register_method("notify_klippy_ready", handle_klippy_ready);

function handle_power_changed(power_status) {
    console.log(`Power Changed:`);
    console.log(power_status)
}
json_rpc.register_method("notify_power_changed", handle_power_changed);

function handle_update_response(response) {
    if (response.complete === true) {
        console.log("Update complete");
        console.log(response);
    }
    update_term(response.message)
}
json_rpc.register_method("notify_update_response", handle_update_response);

function handle_file_list_changed(file_info) {
    // This event fires when a client has either added or removed
    // a gcode file.
    // Update the jstree based on the action and info
    let parent_node = parse_node_path(file_info.item.root, file_info.item.path);
    $("#filelist").jstree('refresh_node', parent_node);
    if (file_info.action == "move_item") {
        let src_parent_node = parse_node_path(
            file_info.source_item.root, file_info.source_item.path)
        if (src_parent_node != parent_node)
            $("#filelist").jstree('refresh_node', src_parent_node);
    }
    console.log("Filelist Changed:");
    console.log(file_info);
}
json_rpc.register_method("notify_filelist_changed", handle_file_list_changed);

function handle_metadata_update(metadata) {
    console.log(metadata);
}
json_rpc.register_method("notify_metadata_update", handle_metadata_update);

//***********End Klipper Event Handlers (JSON-RPC)*****************/

//*****************Websocket Batch GCode Tests*********************/

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
                method: api.gcode_script.method,
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
                api.gcode_script.method, {script: gc});
        } catch (err) {
            console.log("Error executing gcode macro: " + err.message);
            break;
        }
    }
}

//**************End Websocket Batch GCode Tests********************/

//*****************HTTP Helper Functions***************************/

function encode_filename(path) {
    let parts = path.split("/")
    if (!parts.length) {
        return "";
    }
    let fname = encodeURIComponent(parts.pop())
    if (parts.length) {
        return parts.join("/") + "/" + fname;
    }
    return fname;
}

function run_request(url, method, callback=null)
{
    let settings = {
        url: url,
        method: method,
        statusCode: {
            401: function() {
                if (refresh_token != null) {
                    refresh_json_web_token(run_request, url, method, callback);
                }
                else {
                    auth_token = null;
                    $("#do_login").click();
                }
            }
        },
        success: (resp, status) => {
            console.log(resp);
            if (callback != null)
                callback(resp)
            return false;
        }};
    if (websocket.id != null) {
        let fdata = new FormData();
        fdata.append("connection_id", websocket.id);
        settings.data = fdata
        settings.contentType = false,
        settings.processData = false
    }
    if (auth_token != null)
        settings.headers = {"Authorization": `Bearer ${auth_token}`};
    else if (apikey != null)
        settings.headers = {"X-Api-Key": apikey};
    $.ajax(settings);
}

function form_get_request(api_url, query_string="", callback=null) {
    let url = origin + api_url + query_string;
    run_request(url, 'GET', callback);
}

function form_post_request(api_url, query_string="", callback=null) {
    let url = origin + api_url + query_string;
    run_request(url, 'POST', callback);
}

function form_delete_request(api_url, query_string="", callback=null) {
    let url = origin + api_url + query_string;
    run_request(url, 'DELETE', callback);
}

function form_download_request(uri) {
    let dl_url = origin + uri;
    if (apikey != null || auth_token != null) {
        form_get_request(api.oneshot_token.url, "",
            (resp) => {
                let token = resp.result;
                dl_url += "?token=" + token;
                do_download(dl_url);
                return false;
            });
    } else {
        do_download(dl_url);
    }
}

//*************End HTTP Helper Functions***************************/

//***************JSTree Helper Functions***************************/

function parse_node_path(root, path) {
    let slice_idx = path.lastIndexOf("/");
    let node_path = "";
    if (slice_idx != -1)
        node_path = "/" + path.slice(0, slice_idx);
    return root + node_path;
}

function get_selected_node() {
    let js_instance = $("#filelist").jstree(true);
    let sel = js_instance.get_selected();
    if (!sel.length)
        return null;
    sel = sel[0];
    return js_instance.get_node(sel);
}

function get_selected_item(type="file") {
    let node = get_selected_node();
    if (node == null || node.type != type)
        return "";
    return node.id;
}

function generate_children(result, parent) {
    let children = [];
    result.dirs.sort((a, b) => {
        return a.dirname > b.dirname ? 1 : -1;
    })
    result.files.sort((a, b) => {
        return a.filename > b.filename ? 1 : -1;
    });
    for (let dir of result.dirs) {
        let full_path = parent.id + "/" + dir.dirname;
        children.push({text: dir.dirname, id: full_path,
                        type: "dir", children: true,
                        mutable: parent.original.mutable});
    }
    for (let file of result.files) {
        let full_path = parent.id + "/" + file.filename;
        children.push({text: file.filename, id: full_path,
                        type: "file", mutable: parent.original.mutable});
    }
    return children;
}

function jstree_populate_children(node, callback) {
    if (api_type == "http") {
        let qs = `?path=${node.id}`;
        let settings = {url: origin + api.directory.url + qs};
        if (auth_token != null)
            settings.headers = {"Authorization": `Bearer ${auth_token}`};
        else if (apikey != null)
            settings.headers = {"X-Api-Key": apikey};
        $.get(settings, (resp, status) => {
            callback(generate_children(resp.result, node));
            return false;
        });
    } else {
        json_rpc.call_method_with_kwargs(
            api.directory.method.get, {path: node.id})
        .then((result) => {
            callback(generate_children(result, node));
        })
        .catch((error) => {
            console.log(error)
            update_error(api.directory.method.get, error);
        });
    }
}

function jstree_download_file() {
    update_progress(0, 100);
    let filename = get_selected_item();
    if (filename) {
        let url = `/server/files/${encode_filename(filename)}`;
        form_download_request(url);
    }
}

var paste_item = null;
function jstree_paste() {
    if (paste_item == null) {
        console.log(`Invalid Paste Command`);
        return
    }
    let node = get_selected_node()
    if (node == null || node == paste_item.source_node) {
        paste_item = null;
        return;
    }
    let source_path = paste_item.source_node.id;
    let dest_path = node.id;
    // TODO: Need checks here.
    // - If the source is a file and the action is move, the destination must
    // - be a directory?
    if (paste_item.source_node.id == node.id) {
        // Can't move or copy to the same item
        return;
    } else if (paste_item.source_node.type != "file" && node.type == "file") {
        // Can't copy or move a directory into a file
        return;
    } else if (paste_item.action == "copy" && node.type != "file") {
       // When copying to a folder add the file/folder name to the destination
       dest_path += `/${paste_item.source_node.text}`;
    }
    if (api_type == 'http') {
        let api_url = paste_item.action == "move" ? api.move.url : api.copy.url;
        let qs = `?source=${source_path}&dest=${dest_path}`;
        form_post_request(api_url, qs);
    } else {
        // Websocket
        if (paste_item.action == "move")
            move_item(source_path, dest_path);
        else
            copy_item(source_path, dest_path);
    }
    paste_item = null;
}

function jstree_delete_item() {
    let node = get_selected_node();
    if (node == null) {
        console.log("Invalid item selection, cannot delete");
    }
    if (api_type == 'http') {
        let api_url;
        let qs = "";
        if (node.type == "file") {
            api_url = `/server/files/${encode_filename(node.id)}`;
        } else {
            api_url = api.directory.url;
            qs = `?path=${encode_filename(node.id)}&force=true`;
        }
        form_delete_request(api_url, qs);
    } else {
        if (node.type == "file")
            delete_file(node.id);
        else
            delete_directory(node.id);
    }
}

function jstree_new_folder(node, status, cancelled) {
    if (!status)
        return;
    let instance = $("#filelist").jstree(true);
    if (cancelled) {
        console.log("Create Folder Cancelled")
        instance.delete_node(node);
        return;
    }
    console.log(`Create Folder: ${node}`);
    let parent = instance.get_node(instance.get_parent(node));
    if (parent.type == "file") {
        console.log("Invalid folder, cannot create")
        instance.delete_node(node);
        return;
    }
    let path = parent.id + "/" + node.text;
    if (api_type == 'http') {
        let url = api.directory.url;
        let qs = "?path=" + path;
        form_post_request(url, qs);

    } else {
        make_directory(path);
    }
}

function jstree_rename(node, status, cancelled) {
    if (!status || cancelled)
        return;
    let source_path = node.id;
    let instance = $("#filelist").jstree(true);
    let dest_path = instance.get_parent(node) + "/" + node.text;
    if (api_type == 'http') {
        let qs = `?source=${source_path}&dest=${dest_path}`;
        form_post_request(api.move.url, qs);
    } else {
        move_item(source_path, dest_path);
    }
}

function jstree_start_print() {
    let filename = get_selected_item();
    if (filename && filename.startsWith("gcodes/")) {
        filename = filename.slice(7)
        let qs = `?filename=${encode_filename(filename)}`;
        if (api_type == 'http') {
            form_post_request(api.start_print.url, qs);
        } else {
            start_print(filename);
        }
    }
}

function jstree_get_metadata() {
    let filename = get_selected_item();
    if (filename && filename.startsWith("gcodes/")) {
        filename = filename.slice(7);
        if (api_type == 'http') {
            let qs = `?filename=${encode_filename(filename)}`;
            form_get_request(api.metadata.url, qs);
        } else {
            get_metadata(filename);
        }
    }
}

//***********End JSTree Helper Functions***************************/

// A simple reconnecting websocket
class KlippyWebsocket {
    constructor(addr) {
        this.base_address = addr;
        this.connected = false;
        this.ws = null;
        this.onmessage = null;
        this.onopen = null;
        this.id = null;
        this.reconnect = true
        this.connect();
    }

    connect() {
        // Doing the websocket connection here allows the websocket
        // to reconnect if its closed. This is nice as it allows the
        // client to easily recover from Klippy restarts without user
        // intervention
        if (apikey != null || auth_token != null) {
            // Fetch a oneshot token to pass websocket authorization
            let token_settings = {
                url: origin + api.oneshot_token.url,
            }
            if (auth_token != null)
                token_settings.headers = {"Authorization": `Bearer ${auth_token}`};
            else
                token_settings.headers = {"X-Api-Key": apikey};
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
            this.id = null;
            // TODO:  Need to cancel any pending JSON-RPC requests
            if (this.reconnect) {
                console.log("Websocket Closed, reconnecting in 1s: ", e.reason);
                setTimeout(() => {
                    if (this.reconnect)
                        this.connect();
                }, 1000);
            }
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
        this.reconnect = false
        this.ws.close();
    }

};

function create_websocket(url) {
    if (websocket != null)
        websocket.close()
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

function login_jwt_user(user, pass, do_create) {
    if (!user || !pass) {
        alert("Invalid username/password")
        return;
    }
    let close_btn_name = "#login_close"
    if (do_create)
        close_btn_name = "#signup_close"
    let settings = {
        url: origin + api.login.url,
        data: JSON.stringify({username: user, password: pass}),
        contentType: "application/json",
        dataType: 'json'
    }
    if (do_create) {
        settings.url = origin + api.user.url;
        if (auth_token != null)
                settings.headers = {"Authorization": `Bearer ${auth_token}`};
        else if (apikey != null)
            settings.headers = {"X-Api-Key": apikey};
    }
    $.post(settings, (resp, status) => {
        let res = resp.result;
        console.log("Login Response:");
        console.log(res);
        auth_token = res.token;
        refresh_token = res.refresh_token;
        window.localStorage.setItem('refresh_token', refresh_token);
        $('.req-login').prop('disabled', false);
        $(close_btn_name).click();
        check_authorization();
    }).fail(() => {
        console.log("Login Failed");
        alert("Login Failed")
    });
}

function logout_jwt_user() {
    if (auth_token == null) {
        console.log("No User Logged In")
        return;
    }
    let settings = {
        url: origin + api.logout.url,
        contentType: "application/json",
        dataType: 'json',
        headers: {
            "Authorization": `Bearer ${auth_token}`
        }
    }

    $.post(settings, (resp, status) => {
        let res = resp.result;
        console.log("Logout Response:");
        console.log(res);
        auth_token = null;
        refresh_token = null;
        window.localStorage.removeItem('refresh_token');
        $('.req-login').prop('disabled', true);
    }).fail(() => {
        console.log("Logout User Failed");
    });
}

function delete_jwt_user(pass) {
    if (!pass) {
        alert("Invalid Password, Cannot Delete User");
        return;
    }
    if (auth_token == null) {
        console.log("No User Logged In")
        return;
    }
    let settings = {
        method: 'DELETE',
        url: origin + api.user.url,
        contentType: "application/json",
        data: JSON.stringify({password: pass}),
        dataType: 'json',
        headers: {
            "Authorization": `Bearer ${auth_token}`
        },
        success: (resp, status) => {
            let res = resp.result;
            console.log("Delete User Response:");
            console.log(res);
            auth_token = null;
            refresh_token = null;
            window.localStorage.removeItem('refresh_token');
            $('.req-login').prop('disabled', true);
        }
    }

    $.ajax(settings)
    .fail(() => {
        console.log("Delete User Failed");
    });
}

function change_jwt_password(old_pass, new_pass) {
    if (!old_pass || !new_pass) {
        alert("Invalid input for change password")
        return;
    }
    let settings = {
        url: origin + api.reset_password.url,
        data: JSON.stringify({password: old_pass, new_password: new_pass}),
        contentType: "application/json",
        dataType: 'json',
        headers: {
            "Authorization": `Bearer ${auth_token}`
        }
    }
    $.post(settings, (resp, status) => {
        let res = resp.result;
        console.log("Change Password Response:");
        console.log(res);
        $("#changepass_close").click();
    }).fail(() => {
        console.log("Failed to change password");
        alert("Password Reset Failed")
    });
}

function refresh_json_web_token(callback, ...args) {
    let settings = {
        url: origin + api.refresh_jwt.url,
        data: JSON.stringify({refresh_token: refresh_token}),
        contentType: "application/json",
        dataType: 'json',
    }
    $.post(settings, (resp, status) => {
        let res = resp.result;
        console.log("Refresh JWT Response:");
        console.log(res);
        auth_token = res.token;
        $('.req-login').prop('disabled', false);
        if (callback != null)
            callback(...args);
    }).fail(() => {
        console.log("Refresh JWT Failed");
        auth_token = null;
        refresh_token = null;
        window.localStorage.removeItem('refresh_token');
        $('.req-login').prop('disabled', true);
        $("#do_login").click();
    });
}

function check_authorization() {
    // send a HTTP "run gcode" command
    let settings = {
        url: origin + api.server_info.url,
        statusCode: {
            401: function() {
                if (refresh_token != null) {
                    refresh_json_web_token(check_authorization);
                } else {
                    $("#do_login").click();
                }
            }
        }
    }
    if (auth_token != null)
        settings.headers = {"Authorization": `Bearer ${auth_token}`};
    else if (apikey != null)
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
    $('.req-login').prop('disabled', true);
    $('input[type=radio][name=test_type]').on('change', function() {
        api_type = $(this).val();
        $('.reqws').prop('disabled', (api_type == 'http'));
        $('#apimethod').prop('hidden', (api_type == "websocket"));
        $('#apiargs').prop('hidden', (api_type == "http"));
    });

    // Instantiate basic jstree
    $('#filelist').jstree({
        core: {
            multiple: false,
            check_callback: true,
            data: function (node, cb) {
                if (node.id === "#") {
                    cb([
                        {text: "gcodes", id: "gcodes",
                         type: "root", children: true, mutable: true},
                        {text: "config", id: "config",
                         type: "root", children: true, mutable: true},
                        {text: "config_examples", id: "config_examples",
                         type: "root", children: true, mutable: false}
                    ]);
                } else {
                    jstree_populate_children(node, cb);
                }
            }
        },
        types: {
            default: {
                icon: "jstree-folder"
            },
            "#": {
                valid_children: ["root"],
                max_children: 3
            },
            root: {
                icon: "jstree-folder",
                valid_children: ["dir", "file"]
            },
            file: {
                icon: "jstree-file",
                valid_children: []
            },
            dir: {
                icon: "jstree-folder",
                valid_children: ["dir", "file"]
            }
        },
        contextmenu: {
            items: (node, cb) => {
                let actions = Object();
                if (node.type != "file") {
                    // Can upload, can paste
                    actions.upload = {
                        label: "Upload File",
                        _disabled: !node.original.mutable,
                        action: () => {
                            $('#upload-file').click();
                        }
                    }
                    actions.new_folder = {
                        label: "New Folder",
                        _disabled: !node.original.mutable,
                        action: () => {
                            let instance = $("#filelist").jstree(true);
                            let par = get_selected_node();
                            if (par == null)
                                return false;
                            instance.create_node(par, {
                                type: "dir",
                            }, "first", (ch_node) => {
                                if (ch_node) {
                                    let val = instance.edit(
                                        ch_node.id, null, jstree_new_folder);
                                    if (val === false) {
                                        console.log(instance.last_error());
                                    }
                                } else {
                                    console.log("Invalid Child, cannot create dir");
                                }
                            });

                        },
                        separator_after: true
                    }
                } else {
                    actions.download = {
                        label: "Download File",
                        action: jstree_download_file,
                        separator_after: true
                    }
                }
                if (node.type != "root") {
                    if (node.type == "file" && node.id.startsWith("gcodes")) {
                        actions.print = {
                            label: "Start Print",
                            _disabled: is_printing,
                            action: jstree_start_print
                        }
                        actions.metadata = {
                            label: "Get Metadata",
                            separator_after: true,
                            action: jstree_get_metadata
                        }
                    }
                    // can delete, cut (move), copy, or rename(move)
                    actions.rename = {
                        label: "Rename",
                        _disabled: !node.original.mutable,
                        action: () => {
                            let instance = $("#filelist").jstree(true);
                            let cur = get_selected_node();
                            if (cur == null)
                                return false;
                            instance.edit(cur, null, jstree_rename);
                        }
                    }
                    actions.delete = {
                        label: "Delete",
                        _disabled: !node.original.mutable,
                        action: jstree_delete_item,
                        separator_after: true
                    }
                    actions.edit = {
                        label: "Edit",
                        submenu: {}
                    }
                    actions.edit.submenu.cut = {
                        label: "Cut",
                        _disabled: !node.original.mutable,
                        action: () => {
                            paste_item = {
                                action: "move",
                                source_node: node
                            }
                        }
                    }
                    actions.edit.submenu.copy = {
                        label: "Copy",
                        action: () => {
                            paste_item = {
                                action: "copy",
                                source_node: node
                            }
                        }
                    }
                }
                if (!("edit" in actions)) {
                    actions.edit = {
                        label: "Edit",
                        submenu: {}
                    }
                }
                actions.edit.submenu.paste = {
                    label: "Paste",
                    _disabled: paste_item == null ||
                        paste_item.source_node.id == node.id ||
                        !node.original.mutable,
                    action: jstree_paste
                }
                cb(actions);
            }
        },
        plugins: ["types", "contextmenu"]
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
            let qs = "?script=" + line;
            form_post_request(api.gcode_script.url, qs);
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
            if (auth_token != null)
                settings.headers = {"Authorization": `Bearer ${auth_token}`};
            else if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            if (sendtype == "get") {
                console.log("Sending GET " + url);
                form_get_request(url);
            } else if (sendtype == "post") {
                console.log("Sending POST " + url);
                form_post_request(url);
            } else if (sendtype == "delete") {
                console.log("Sending DELETE " + url);
                form_delete_request(url);
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

    // Uploads a selected file to the server
    $('#upload-file').change(() => {
        update_progress(0, 100);
        let file = $('#upload-file').prop('files')[0];
        let dir = get_selected_item("dir");
        if (!dir)
            dir = get_selected_item("root");
        if (file && dir) {
            dir = dir.split('/');
            let root = dir[0];
            let directory = dir.slice(1).join("/");
            console.log("Sending Upload Request...");
            // It might not be a bad idea to validate that this is
            // a gcode file here, and reject and other files.

            // If you want to allow multiple selections, the below code should be
            // done in a loop, and the 'let file' above should be the entire
            // array of files and not the first element

            let fdata = new FormData();
            fdata.append("file", file);
            fdata.append("root", root);
            fdata.append("path", directory);
            let settings = {
                url: origin + api.upload.url,
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
            if (auth_token != null)
                settings.headers = {"Authorization": `Bearer ${auth_token}`};
            else if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.ajax(settings);
            $('#upload-file').val('');
        }
    });

    // Pause/Resume a currently running print.  The specific gcode executed
    // is configured in printer.cfg.
    $("#btnpauseresume").click(() =>{
        if (api_type == 'http') {
            let path = paused ? api.resume_print.url : api.pause_print.url
            form_post_request(path);
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
            form_post_request(api.cancel_print.url);
        } else {
            cancel_print();
        }
    });

    // Refresh File List
    $("#btngetfiles").click(() =>{
        if (api_type == 'http') {
            form_get_request(api.file_list.url);
        } else {
            get_file_list();
        }
    });

    $('#btnqueryendstops').click(() => {
        if (api_type == 'http') {
            form_get_request(api.query_endstops.url);
        } else {
            get_endstops();
        }
    });

     // Post Subscription Request
     $('#btnsubscribe').click(() => {
        if (api_type == 'http') {
            let qs = "?gcode_move=gcode_position,speed,speed_factor,extrude_factor" +
                    "&toolhead&virtual_sdcard&heater_bed&extruder=temperature,target&fan&idle_timeout&pause_resume"  +
                    "&print_stats";
            form_post_request(api.object_subscription.url, qs);
        } else {
            const sub = {
                objects: {
                    gcode_move: ["gcode_position", "speed", "speed_factor", "extrude_factor"],
                    idle_timeout: null,
                    pause_resume: null,
                    toolhead: null,
                    virtual_sdcard: null,
                    heater_bed: null,
                    extruder: ["temperature", "target"],
                    fan: null,
                    print_stats: null}
                };
            add_subscription(sub);
        }
    });

    $('#btngethelp').click(() => {
        if (api_type == 'http') {
            form_get_request(api.gcode_help.url);
        } else {
            get_gcode_help();
        }
    });

    $('#btngetobjs').click(() => {
        if (api_type == 'http') {
            form_get_request(api.object_list.url);
        } else {
            get_object_list();
        }
    });

    $('#btntestmesh').click(() => {
        if (api_type == 'http') {
            let settings = {url: origin + api.object_status.url + "?bed_mesh"};
            if (auth_token != null)
                settings.headers = {"Authorization": `Bearer ${auth_token}`};
            else if (apikey != null)
                settings.headers = {"X-Api-Key": apikey};
            $.get(settings, (resp, status) => {
                process_mesh(resp.result)
                return false;
            });
        } else {
            get_mesh();
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
            form_post_request(api.estop.url);
        } else {
            emergency_stop();
        }
    });

    $('#btnrestart').click(() => {
        if (api_type == 'http') {
            form_post_request(api.restart.url);
        } else {
            restart();
        }
    });

    $('#btnfirmwarerestart').click(() => {
        if (api_type == 'http') {
            form_post_request(api.firmware_restart.url);
        } else {
            firmware_restart();
        }
    });

    $('#btnreboot').click(() => {
        if (api_type == 'http') {
            form_post_request(api.reboot.url);
        } else {
            reboot();
        }
    });

    $('#btnshutdown').click(() => {
        if (api_type == 'http') {
            form_post_request(api.shutdown.url);
        } else {
            shutdown();
        }
    });

    $('#btngetlog').click(() => {
        form_download_request(api.klippy_log.url);
    });

    $('#btnmoonlog').click(() => {
        form_download_request(api.moonraker_log.url);
    });

    $('#btnloginuser').click(() => {
        $("#do_login").click();
    });

    $('#btncreateuser').click(() => {
        $("#do_signup").click();
    });

    $('#btnlogout').click(() => {
        logout_jwt_user();
    });

    $('#btndeluser').click(() => {
        let password = window.prompt("Verify your password:")
        delete_jwt_user(password);
    });

    $('#btnchangepass').click(() => {
        $("#do_changepass").click();
    });

    $('#btnsetapikey').click(() => {
        let defkey = apikey;
        if (!defkey)
            defkey = ""
        let new_key = window.prompt("Enter your API Key", defkey);
        if (!new_key)
            apikey = null;
        else
            apikey = new_key;
        check_authorization();
    });

    $("#do_login").leanModal({
        top : 200,
        overlay : 0.4,
        closeButton: "#login_close"
    });

    $("#do_signup").leanModal({
        top : 200,
        overlay : 0.4,
        closeButton: "#signup_close"
    });

    $("#do_changepass").leanModal({
        top : 200,
        overlay : 0.4,
        closeButton: "#changepass_close"
    });

    $("#login_close").click(() => {
        //$("#login_username").val("");
        $("#login_password").val("");
        $("#nav_home").click();
    });

    $("#signup_close").click(() => {
        $("#signup_username").val("");
        $("#signup_password").val("");
        $("#signup_verify_pass").val("");
        $("#nav_home").click();
    });

    $("#changepass_close").click(() => {
        $("#changepass_oldpass").val("");
        $("#changepass_newpass").val("");
        $("#changepass_verify_pass").val("");
        $("#nav_home").click();
    });

    $("#login_form").submit((evt)=> {
        let user = $("#login_username").val()
        let pass = $("#login_password").val()
        if (user != "" && pass != "")
            login_jwt_user(user, pass, false);
        else
            alert("Invalid username/password");
        return false;
    });

    $("#signup_form").submit((evt)=> {
        let user = $("#signup_username").val()
        let pass = $("#signup_password").val()
        let verify_pass = $("#signup_verify_pass").val()
        if (user != "" && pass != "" && pass == verify_pass)
            login_jwt_user(user, pass, true);
        else
            alert("Invalid username/password");
        return false;
    });

    $("#changepass_form").submit((evt)=> {
        let old_pass = $("#changepass_oldpass").val()
        let new_pass = $("#changepass_newpass").val()
        let verify_pass = $("#changepass_verify_pass").val()
        if (old_pass != "" && new_pass != "" && new_pass == verify_pass)
            change_jwt_password(old_pass, new_pass)
        else
            alert("All fields are required to change password");
        return false;
    });

    check_authorization();
};
