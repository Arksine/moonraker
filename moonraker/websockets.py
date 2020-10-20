# Websocket Request/Response Handler
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import logging
import tornado
import json
from tornado.ioloop import IOLoop
from tornado.websocket import WebSocketHandler, WebSocketClosedError
from utils import ServerError

class Sentinel:
    pass

class JsonRPC:
    def __init__(self):
        self.methods = {}

    def register_method(self, name, method):
        self.methods[name] = method

    def remove_method(self, name):
        self.methods.pop(name, None)

    async def dispatch(self, data):
        response = None
        try:
            request = json.loads(data)
        except Exception:
            msg = f"Websocket data not json: {data}"
            logging.exception(msg)
            response = self.build_error(-32700, "Parse error")
            return json.dumps(response)
        logging.debug("Websocket Request::" + data)
        if isinstance(request, list):
            response = []
            for req in request:
                resp = await self.process_request(req)
                if resp is not None:
                    response.append(resp)
            if not response:
                response = None
        else:
            response = await self.process_request(request)
        if response is not None:
            response = json.dumps(response)
            logging.debug("Websocket Response::" + response)
        return response

    async def process_request(self, request):
        req_id = request.get('id', None)
        rpc_version = request.get('jsonrpc', "")
        method_name = request.get('method', None)
        if rpc_version != "2.0" or not isinstance(method_name, str):
            return self.build_error(-32600, "Invalid Request", req_id)
        method = self.methods.get(method_name, None)
        if method is None:
            return self.build_error(-32601, "Method not found", req_id)
        if 'params' in request:
            params = request['params']
            if isinstance(params, list):
                response = await self.execute_method(method, req_id, *params)
            elif isinstance(params, dict):
                response = await self.execute_method(method, req_id, **params)
            else:
                return self.build_error(-32600, "Invalid Request", req_id)
        else:
            response = await self.execute_method(method, req_id)
        return response

    async def execute_method(self, method, req_id, *args, **kwargs):
        try:
            result = await method(*args, **kwargs)
        except TypeError as e:
            return self.build_error(-32603, "Invalid params", req_id)
        except ServerError as e:
            return self.build_error(e.status_code, str(e), req_id)
        except Exception as e:
            return self.build_error(-31000, str(e), req_id)

        if req_id is None:
            return None
        else:
            return self.build_result(result, req_id)

    def build_result(self, result, req_id):
        return {
            'jsonrpc': "2.0",
            'result': result,
            'id': req_id
        }

    def build_error(self, code, msg, req_id=None):
        return {
            'jsonrpc': "2.0",
            'error': {'code': code, 'message': msg},
            'id': req_id
        }

class WebsocketManager:
    def __init__(self, server):
        self.server = server
        self.websockets = {}
        self.ws_lock = tornado.locks.Lock()
        self.rpc = JsonRPC()

        # Register events
        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_klippy_disconnect)
        self.server.register_event_handler(
            "server:gcode_response", self._handle_gcode_response)
        self.server.register_event_handler(
            "server:status_update", self._handle_status_update)
        self.server.register_event_handler(
            "file_manager:filelist_changed", self._handle_filelist_changed)
        self.server.register_event_handler(
            "file_manager:metadata_update", self._handle_metadata_update)
        self.server.register_event_handler(
            "gpio_power:power_changed", self._handle_power_changed)

    async def _handle_klippy_disconnect(self):
        await self.notify_websockets("klippy_disconnected")

    async def _handle_gcode_response(self, response):
        await self.notify_websockets("gcode_response", response)

    async def _handle_status_update(self, status):
        await self.notify_websockets("status_update", status)

    async def _handle_filelist_changed(self, flist):
        await self.notify_websockets("filelist_changed", flist)

    async def _handle_metadata_update(self, metadata):
        await self.notify_websockets("metadata_update", metadata)

    async def _handle_power_changed(self, pstatus):
        await self.notify_websockets("power_changed", pstatus)

    def register_local_handler(self, api_def, callback):
        for ws_method, req_method in \
                zip(api_def.ws_methods, api_def.request_methods):
            rpc_cb = self._generate_local_callback(
                api_def.endpoint, req_method, callback)
            self.rpc.register_method(ws_method, rpc_cb)

    def register_remote_handler(self, api_def):
        ws_method = api_def.ws_methods[0]
        rpc_cb = self._generate_callback(api_def.endpoint)
        self.rpc.register_method(ws_method, rpc_cb)

    def remove_handler(self, ws_method):
        self.rpc.remove_method(ws_method)

    def _generate_callback(self, endpoint):
        async def func(**kwargs):
            result = await self.server.make_request(endpoint, kwargs)
            return result
        return func

    def _generate_local_callback(self, endpoint, request_method, callback):
        async def func(**kwargs):
            result = await callback(endpoint, request_method, kwargs)
            return result
        return func

    def has_websocket(self, ws_id):
        return ws_id in self.websockets

    async def add_websocket(self, ws):
        async with self.ws_lock:
            self.websockets[ws.uid] = ws
            logging.info(f"New Websocket Added: {ws.uid}")

    async def remove_websocket(self, ws):
        async with self.ws_lock:
            old_ws = self.websockets.pop(ws.uid, None)
            if old_ws is not None:
                logging.info(f"Websocket Removed: {ws.uid}")

    async def notify_websockets(self, name, data=Sentinel):
        msg = {'jsonrpc': "2.0", 'method': "notify_" + name}
        if data != Sentinel:
            msg['params'] = [data]
        notification = json.dumps(msg)
        async with self.ws_lock:
            for ws in list(self.websockets.values()):
                try:
                    ws.write_message(notification)
                except WebSocketClosedError:
                    self.websockets.pop(ws.uid, None)
                    logging.info(f"Websocket Removed: {ws.uid}")
                except Exception:
                    logging.exception(
                        f"Error sending data over websocket: {ws.uid}")

    async def close(self):
        async with self.ws_lock:
            for ws in self.websockets.values():
                ws.close()
            self.websockets = {}

class WebSocket(WebSocketHandler):
    def initialize(self, wsm, auth):
        self.wsm = wsm
        self.auth = auth
        self.rpc = self.wsm.rpc
        self.uid = id(self)

    async def open(self):
        await self.wsm.add_websocket(self)

    def on_message(self, message):
        io_loop = IOLoop.current()
        io_loop.spawn_callback(self._process_message, message)

    async def _process_message(self, message):
        try:
            response = await self.rpc.dispatch(message)
            if response is not None:
                self.write_message(response)
        except Exception:
            logging.exception("Websocket Command Error")

    def on_close(self):
        io_loop = IOLoop.current()
        io_loop.spawn_callback(self.wsm.remove_websocket, self)

    def check_origin(self, origin):
        if self.settings['enable_cors']:
            # allow CORS
            return True
        else:
            return super(WebSocket, self).check_origin(origin)

    # Check Authorized User
    def prepare(self):
        if not self.auth.check_authorized(self.request):
            raise tornado.web.HTTPError(401, "Unauthorized")
