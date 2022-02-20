from __future__ import annotations
import pytest
import json
import asyncio
import tornado.websocket

from typing import (
    TYPE_CHECKING,
    Union,
    Tuple,
    Callable,
    Dict,
    List,
    Any,
    Optional,
)

if TYPE_CHECKING:
    from tornado.websocket import WebSocketClientConnection

class WebsocketError(Exception):
    def __init__(self, code, *args: object) -> None:
        super().__init__(*args)
        self.code = code

class WebsocketClient:
    error = WebsocketError
    def __init__(self,
                 type: str = "ws",
                 port: int = 7010
                 ) -> None:
        self.ws: Optional[WebSocketClientConnection] = None
        self.pending_requests: Dict[int, asyncio.Future] = {}
        self.notify_cbs: Dict[str, List[Callable[..., None]]] = {}
        assert type in ["ws", "wss"]
        self.url = f"{type}://127.0.0.1:{port}/websocket"

    async def connect(self, token: Optional[str] = None) -> None:
        url = self.url
        if token is not None:
            url += f"?token={token}"
        self.ws = await tornado.websocket.websocket_connect(
            url, connect_timeout=2.,
            on_message_callback=self._on_message_received)

    async def request(self,
                      remote_method: str,
                      args: Dict[str, Any] = {}
                      ) -> Dict[str, Any]:
        if self.ws is None:
            pytest.fail("Websocket Not Connected")
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        req, req_id = self._encode_request(remote_method, args)
        self.pending_requests[req_id] = fut
        await self.ws.write_message(req)
        return await asyncio.wait_for(fut, 2.)

    def _encode_request(self,
                        method: str,
                        args: Dict[str, Any]
                        ) -> Tuple[str, int]:
        request: Dict[str, Any] = {
            'jsonrpc': "2.0",
            'method': method,
        }
        if args:
            request['params'] = args
        req_id = id(request)
        request["id"] = req_id
        return json.dumps(request), req_id

    def _on_message_received(self, message: Union[str, bytes, None]) -> None:
        if isinstance(message, str):
            self._decode_jsonrpc(message)

    def _decode_jsonrpc(self, data: str) -> None:
        try:
            resp: Dict[str, Any] = json.loads(data)
        except json.JSONDecodeError:
            pytest.fail(f"Websocket JSON Decode Error: {data}")
        header = resp.get('jsonrpc', "")
        if header != "2.0":
            # Invalid Json, set error if we can get the id
            pytest.fail(f"Invalid jsonrpc header: {data}")
        req_id: Optional[int] = resp.get("id")
        method: Optional[str] = resp.get("method")
        if method is not None:
            if req_id is None:
                params = resp.get("params", [])
                if not isinstance(params, list):
                    pytest.fail("jsonrpc notification params"
                                f"should always be a list: {data}")
                if method in self.notify_cbs:
                    for func in self.notify_cbs[method]:
                        func(*params)
            else:
                # This is a request from the server (should not happen)
                pytest.fail(f"Server should not request from client: {data}")
        elif req_id is not None:
            pending_fut = self.pending_requests.pop(req_id, None)
            if pending_fut is None:
                # No future pending for this response
                return
            # This is a response
            if "result" in resp:
                pending_fut.set_result(resp["result"])
            elif "error" in resp:
                err = resp["error"]
                try:
                    code = err["code"]
                    msg = err["message"]
                except Exception:
                    pytest.fail(f"Invalid jsonrpc error: {data}")
                exc = WebsocketError(code, msg)
                pending_fut.set_exception(exc)
            else:
                pytest.fail(
                    f"Invalid jsonrpc packet, no result or error: {data}")
        else:
            # Invalid json
            pytest.fail(f"Invalid jsonrpc packet, no id: {data}")

    def register_notify_callback(self, name: str, callback) -> None:
        if name in self.notify_cbs:
            self.notify_cbs[name].append(callback)
        else:
            self.notify_cbs[name][callback]

    def close(self):
        for fut in self.pending_requests.values():
            if not fut.done():
                fut.set_exception(WebsocketError(
                    0, "Closing Websocket Client"))
        if self.ws is not None:
            self.ws.close(1000, "Test Complete")
