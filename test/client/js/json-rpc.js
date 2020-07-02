// Base JSON-RPC Client implementation
export default class JsonRPC {
    constructor() {
       this.id_counter = 0;
       this.methods = new Object();
       this.pending_callbacks = new Object();
       this.transport = null;
    }

    _create_uid() {
        let uid = this.id_counter;
        this.id_counter++;
        return uid.toString();
    }

    _build_request(method_name, uid, kwargs, ...args) {
        let request = {
            jsonrpc: "2.0",
            method: method_name};
        if (uid != null) {
            request.id = uid;
        }
        if (kwargs != null) {
            request.params = kwargs
        }
        else if (args.length > 0) {
            request.params = args;
        }
        return request;
    }

    register_method(method_name, method) {
        this.methods[method_name] = method
    }

    register_transport(transport) {
        // The transport must have a send method.  It should
        // have an onmessage callback that fires when it
        // receives data, but it would also be valid to directly call
        // JsonRPC.process_received if necessary
        this.transport = transport;
        this.transport.onmessage = this.process_received.bind(this)
    }

    send_batch_request(requests) {
        // Batch requests take an array of requests.  Each request
        // should be an object with the following attribtues:
        // 'method' - The name of the method to execture
        // 'type' - May be "request" or "notification"
        // 'params' - method parameters, if applicable
        //
        // If a method has no parameters then the 'params' attribute
        // should not be included.

        if (this.transport == null)
            return Promise.reject(Error("No Transport Initialized"));

        let batch_request = [];
        let promises = [];
        requests.forEach((request, idx) => {
            let name = request.method;
            let args = [];
            let kwargs = null;
            let uid = null;
            if ('params' in request) {
                if (request.params instanceof Object)
                    kwargs = request.params;
                else
                    args = request.params;
            }
            if (request.type == "request") {
                uid = this._create_uid();
                promises.push(new Promise((resolve, reject) => {
                    this.pending_callbacks[uid] = (result, error) => {
                        let response = {method: name, index: idx};
                        if (error != null) {
                            response.error = error;
                            reject(response);
                        } else {
                            response.result = result;
                            resolve(response);
                        }
                    }
                }));
            }
            batch_request.push(this._build_request(
                name, uid, kwargs, ...args));
        });

        this.transport.send(JSON.stringify(batch_request));
        return Promise.all(promises);
    }

    call_method(method_name, ...args) {
        let uid = this._create_uid();
        let request = this._build_request(
            method_name, uid, null, ...args);
        if (this.transport != null) {
            this.transport.send(JSON.stringify(request));
            return new Promise((resolve, reject) => {
                this.pending_callbacks[uid] = (result, error) => {
                    if (error != null) {
                        reject(error);
                    } else {
                        resolve(result);
                    }
                }
            });
        }
        return Promise.reject(Error("No Transport Initialized"));
    }

    call_method_with_kwargs(method_name, kwargs) {
        let uid = this._create_uid();
        let request = this._build_request(method_name, uid, kwargs);
        if (this.transport != null) {
            this.transport.send(JSON.stringify(request));
            return new Promise((resolve, reject) => {
                this.pending_callbacks[uid] = (result, error) => {
                    if (error != null) {
                        reject(error);
                    } else {
                        resolve(result);
                    }
                }
            });
        }
        return Promise.reject(Error("No Transport Initialized"));
    }

    notify(method_name, ...args) {
        let notification = this._build_request(
            method_name, null, null, ...args);
        if (this.transport != null) {
            this.transport.send(JSON.stringify(notification));
        }
    }

    process_received(encoded_data) {
        let rpc_data = JSON.parse(encoded_data);
        if (rpc_data instanceof Array) {
            // batch request/response
            for (let data of rpc_data) {
                this._validate_and_dispatch(data);
            }
        } else {
            this._validate_and_dispatch(rpc_data);
        }
    }

    _validate_and_dispatch(rpc_data) {
        if (rpc_data.jsonrpc != "2.0") {
            console.log("Invalid JSON-RPC data");
            console.log(rpc_data);
            return;
        }

        if ("result" in rpc_data || "error" in rpc_data) {
            // This is a response to a client request
            this._handle_response(rpc_data);
        } else if ("method" in rpc_data) {
            // This is a server side notification/event
            this._handle_request(rpc_data);
        } else {
            // Invalid RPC data
            console.log("Invalid JSON-RPC data");
            console.log(rpc_data);
        }
    }

    _handle_request(request) {
        // Note:  This implementation does not fully conform
        // to the JSON-RPC protocol.  The server only sends
        // events (notifications) to the client, and it is
        // not concerned with client-side errors.  Thus
        // this implementation does not attempt to track
        // request id's, nor does it send responses back
        // to the server
        let method = this.methods[request.method];
        if (method == null) {
            console.log("Invalid Method: " + request.method);
            return;
        }
        if ("params" in request) {
            let args = request.params;
            if (args instanceof Array)
                method(...args);
            else if (args instanceof Object) {
                // server passed keyword arguments which we currently do not support
                console.log("Keyword Parameters Not Supported:");
                console.log(request);
            } else {
                console.log("Invalid Parameters");
                console.log(request);
            }
        } else {
            method();
        }
    }

    _handle_response(response) {
        if (response.result != null && response.id != null) {
            let uid = response.id;
            let response_finalize = this.pending_callbacks[uid];
            if (response_finalize != null) {
                response_finalize(response.result);
                delete this.pending_callbacks[uid];
            } else {
                console.log("No Registered RPC Call for uid:");
                console.log(response);
            }
        } else if (response.error != null) {
            // Check ID, depending on the error it may or may not be available
            let uid = response.id;
            let response_finalize = this.pending_callbacks[uid];
            if (response_finalize != null) {
                response_finalize(null, response.error);
                delete this.pending_callbacks[uid];
            } else {
                console.log("JSON-RPC error recieved");
                console.log(response.error);
            }
        } else {
            console.log("Invalid JSON-RPC response");
            console.log(response);
        }
    }
}