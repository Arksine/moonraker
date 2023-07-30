# MQTT client implementation for Moonraker
#
# Copyright (C) 2021  Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import socket
import asyncio
import logging
import json
import pathlib
import ssl
from collections import deque
import paho.mqtt.client as paho_mqtt
from ..common import Subscribable, WebRequest, APITransport, JsonRPC

# Annotation imports
from typing import (
    List,
    Optional,
    TYPE_CHECKING,
    Any,
    Callable,
    Coroutine,
    Dict,
    Union,
    Tuple,
    Awaitable,
    Deque,
)
if TYPE_CHECKING:
    from ..app import APIDefinition
    from ..confighelper import ConfigHelper
    from ..klippy_connection import KlippyConnection as Klippy
    FlexCallback = Callable[[bytes], Optional[Coroutine]]
    RPCCallback = Callable[..., Coroutine]

DUP_API_REQ_CODE = -10000
MQTT_PROTOCOLS = {
    'v3.1': paho_mqtt.MQTTv31,
    'v3.1.1': paho_mqtt.MQTTv311,
    'v5': paho_mqtt.MQTTv5
}

class ExtPahoClient(paho_mqtt.Client):
    # Override reconnection to take a connected socket.  This allows Moonraker
    # create the socket connection asynchronously
    def reconnect(self, sock: Optional[socket.socket] = None):
        """Reconnect the client after a disconnect. Can only be called after
        connect()/connect_async()."""
        if len(self._host) == 0:
            raise ValueError('Invalid host.')
        if self._port <= 0:
            raise ValueError('Invalid port number.')

        if hasattr(self, "_out_packet_mutex"):
            # Paho Mqtt Version < 1.6.x
            self._in_packet = {
                "command": 0,
                "have_remaining": 0,
                "remaining_count": [],
                "remaining_mult": 1,
                "remaining_length": 0,
                "packet": b"",
                "to_process": 0,
                "pos": 0
            }
            with self._out_packet_mutex:
                self._out_packet = deque()  # type: ignore

            with self._current_out_packet_mutex:
                self._current_out_packet = None
        else:
            self._in_packet = {
                "command": 0,
                "have_remaining": 0,
                "remaining_count": [],
                "remaining_mult": 1,
                "remaining_length": 0,
                "packet": bytearray(b""),
                "to_process": 0,
                "pos": 0
            }
            self._out_packet = deque()  # type: ignore

        with self._msgtime_mutex:
            self._last_msg_in = paho_mqtt.time_func()
            self._last_msg_out = paho_mqtt.time_func()

        self._ping_t = 0
        self._state = paho_mqtt.mqtt_cs_new

        self._sock_close()

        # Put messages in progress in a valid state.
        self._messages_reconnect_reset()

        if sock is None:
            sock = self._create_socket_connection()

        if self._ssl:
            # SSL is only supported when SSLContext is available
            # (implies Python >= 2.7.9 or >= 3.2)

            verify_host = not self._tls_insecure
            try:
                # Try with server_hostname, even it's not supported in
                # certain scenarios
                sock = self._ssl_context.wrap_socket(
                    sock,
                    server_hostname=self._host,
                    do_handshake_on_connect=False,
                )
            except ssl.CertificateError:
                # CertificateError is derived from ValueError
                raise
            except ValueError:
                # Python version requires SNI in order to handle
                # server_hostname, but SNI is not available
                sock = self._ssl_context.wrap_socket(
                    sock,
                    do_handshake_on_connect=False,
                )
            else:
                # If SSL context has already checked hostname, then don't need
                #  to do it again
                if (hasattr(self._ssl_context, 'check_hostname') and
                        self._ssl_context.check_hostname):
                    verify_host = False

            assert isinstance(sock, ssl.SSLSocket)
            sock.settimeout(self._keepalive)
            sock.do_handshake()

            if verify_host:
                ssl.match_hostname(sock.getpeercert(), self._host)  # type: ignore

        if self._transport == "websockets":
            sock.settimeout(self._keepalive)
            sock = paho_mqtt.WebsocketWrapper(
                sock, self._host, self._port, self._ssl,
                self._websocket_path, self._websocket_extra_headers
            )

        self._sock = sock
        assert self._sock is not None
        self._sock.setblocking(False)
        self._registered_write = False
        self._call_socket_open()

        return self._send_connect(self._keepalive)

class SubscriptionHandle:
    def __init__(self, topic: str, callback: FlexCallback) -> None:
        self.callback = callback
        self.topic = topic

class BrokerAckLogger:
    def __init__(self, topics: List[str], action: str) -> None:
        self.topics = topics
        self.action = action

    def __call__(self, fut: asyncio.Future) -> None:
        if self.action == "subscribe":
            res: Union[List[int], List[paho_mqtt.ReasonCodes]]
            res = fut.result()
            log_msg = "MQTT Subscriptions Acknowledged"
            if len(res) != len(self.topics):
                log_msg += "\nTopic / QOS count mismatch, " \
                    f"\nTopics: {self.topics} " \
                    f"\nQoS responses: {res}"
            else:
                for topic, qos in zip(self.topics, res):
                    log_msg += f"\n Topic: {topic} | "
                    if isinstance(qos, paho_mqtt.ReasonCodes):
                        log_msg += qos.getName()
                    else:
                        log_msg += f"Granted QoS {qos}"
        elif self.action == "unsubscribe":
            log_msg = "MQTT Unsubscribe Acknowledged"
            for topic in self.topics:
                log_msg += f"\n Topic: {topic}"
        else:
            log_msg = f"Unknown action: {self.action}"
        logging.debug(log_msg)


SubscribedDict = Dict[str, Tuple[int, List[SubscriptionHandle]]]

class AIOHelper:
    def __init__(self, client: paho_mqtt.Client) -> None:
        self.loop = asyncio.get_running_loop()
        self.client = client
        self.client.on_socket_open = self._on_socket_open
        self.client.on_socket_close = self._on_socket_close
        self.client._on_socket_register_write = self._on_socket_register_write
        self.client._on_socket_unregister_write = \
            self._on_socket_unregister_write
        self.misc_task: Optional[asyncio.Task] = None

    def _on_socket_open(self,
                        client: paho_mqtt.Client,
                        userdata: Any,
                        sock: socket.socket
                        ) -> None:
        logging.info("MQTT Socket Opened")
        self.loop.add_reader(sock, client.loop_read)
        self.misc_task = self.loop.create_task(self.misc_loop())

    def _on_socket_close(self,
                         client: paho_mqtt.Client,
                         userdata: Any,
                         sock: socket.socket
                         ) -> None:
        logging.info("MQTT Socket Closed")
        self.loop.remove_reader(sock)
        if self.misc_task is not None:
            self.misc_task.cancel()

    def _on_socket_register_write(self,
                                  client: paho_mqtt.Client,
                                  userdata: Any,
                                  sock: socket.socket
                                  ) -> None:
        self.loop.add_writer(sock, client.loop_write)

    def _on_socket_unregister_write(self,
                                    client: paho_mqtt.Client,
                                    userdata: Any,
                                    sock: socket.socket
                                    ) -> None:
        self.loop.remove_writer(sock)

    async def misc_loop(self) -> None:
        while self.client.loop_misc() == paho_mqtt.MQTT_ERR_SUCCESS:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
        logging.info("MQTT Misc Loop Complete")


class MQTTClient(APITransport, Subscribable):
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.klippy: Klippy = self.server.lookup_component("klippy_connection")
        self.address: str = config.get('address')
        self.port: int = config.getint('port', 1883)
        user = config.gettemplate('username', None)
        self.user_name: Optional[str] = None
        if user:
            self.user_name = user.render()
        pw_file_path = config.get('password_file', None, deprecate=True)
        pw_template = config.gettemplate('password', None)
        self.password: Optional[str] = None
        if pw_file_path is not None:
            pw_file = pathlib.Path(pw_file_path).expanduser().absolute()
            if not pw_file.exists():
                raise config.error(
                    f"Password file '{pw_file}' does not exist")
            self.password = pw_file.read_text().strip()
        if pw_template is not None:
            self.password = pw_template.render()
        protocol = config.get('mqtt_protocol', "v3.1.1")
        self.protocol = MQTT_PROTOCOLS.get(protocol, None)
        if self.protocol is None:
            raise config.error(
                f"Invalid value '{protocol}' for option 'mqtt_protocol' "
                "in section [mqtt]. Must be one of "
                f"{MQTT_PROTOCOLS.values()}")
        self.instance_name = config.get('instance_name', socket.gethostname())
        if '+' in self.instance_name or '#' in self.instance_name:
            raise config.error(
                "Option 'instance_name' in section [mqtt] cannot "
                "contain a wildcard.")
        self.qos = config.getint("default_qos", 0)
        if self.qos > 2 or self.qos < 0:
            raise config.error(
                "Option 'default_qos' in section [mqtt] must be "
                "between 0 and 2")
        self.publish_split_status = \
            config.getboolean("publish_split_status", False)
        self.client = ExtPahoClient(protocol=self.protocol)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
        self.client.on_publish = self._on_publish
        self.client.on_subscribe = self._on_subscribe
        self.client.on_unsubscribe = self._on_unsubscribe
        self.connect_evt: asyncio.Event = asyncio.Event()
        self.disconnect_evt: Optional[asyncio.Event] = None
        self.connect_task: Optional[asyncio.Task] = None
        self.subscribed_topics: SubscribedDict = {}
        self.pending_responses: List[asyncio.Future] = []
        self.pending_acks: Dict[int, asyncio.Future] = {}

        self.server.register_endpoint(
            "/server/mqtt/publish", ["POST"],
            self._handle_publish_request,
            transports=["http", "websocket", "internal"])
        self.server.register_endpoint(
            "/server/mqtt/subscribe", ["POST"],
            self._handle_subscription_request,
            transports=["http", "websocket", "internal"])

        # Subscribe to API requests
        self.json_rpc = JsonRPC(self.server, transport="MQTT")
        self.api_request_topic = f"{self.instance_name}/moonraker/api/request"
        self.api_resp_topic = f"{self.instance_name}/moonraker/api/response"
        self.klipper_status_topic = f"{self.instance_name}/klipper/status"
        self.klipper_state_prefix = f"{self.instance_name}/klipper/state"
        self.moonraker_status_topic = f"{self.instance_name}/moonraker/status"
        status_cfg: Dict[str, Any] = config.getdict("status_objects", {},
                                                    allow_empty_fields=True)
        self.status_objs: Dict[str, Any] = {}
        for key, val in status_cfg.items():
            if val is not None:
                self.status_objs[key] = [v.strip() for v in val.split(',')
                                         if v.strip()]
            else:
                self.status_objs[key] = None
        if status_cfg:
            logging.debug(f"MQTT: Status Objects Set: {self.status_objs}")
            self.server.register_event_handler(
                "server:klippy_started", self._handle_klippy_started
            )

        self.timestamp_deque: Deque = deque(maxlen=20)
        self.api_qos = config.getint('api_qos', self.qos)
        if config.getboolean("enable_moonraker_api", True):
            api_cache = self.server.register_api_transport("mqtt", self)
            for api_def in api_cache.values():
                if "mqtt" in api_def.supported_transports:
                    self.register_api_handler(api_def)
            self.subscribe_topic(self.api_request_topic,
                                 self._process_api_request,
                                 self.api_qos)

        self.server.register_remote_method("publish_mqtt_topic",
                                           self._publish_from_klipper)
        logging.info(
            f"\nReserved MQTT topics:\n"
            f"API Request: {self.api_request_topic}\n"
            f"API Response: {self.api_resp_topic}\n"
            f"Moonraker Status: {self.moonraker_status_topic}\n"
            f"Klipper Status: {self.klipper_status_topic}")

    async def component_init(self) -> None:
        # We must wait for the IOLoop (asyncio event loop) to start
        # prior to retrieving it
        self.helper = AIOHelper(self.client)
        if self.user_name is not None:
            self.client.username_pw_set(self.user_name, self.password)
        self.client.will_set(self.moonraker_status_topic,
                             payload=json.dumps({'server': 'offline'}),
                             qos=self.qos, retain=True)
        self.client.connect_async(self.address, self.port)
        self.connect_task = self.event_loop.create_task(
            self._do_reconnect(first=True)
        )

    async def _handle_klippy_started(self, state: str) -> None:
        if self.status_objs:
            args = {'objects': self.status_objs}
            try:
                await self.klippy.request(
                    WebRequest("objects/subscribe", args, conn=self))
            except self.server.error:
                pass

    def _on_message(self,
                    client: str,
                    user_data: Any,
                    message: paho_mqtt.MQTTMessage
                    ) -> None:
        topic = message.topic
        if topic in self.subscribed_topics:
            cb_hdls = self.subscribed_topics[topic][1]
            for hdl in cb_hdls:
                self.event_loop.register_callback(
                    hdl.callback, message.payload)
        else:
            logging.debug(
                f"Unregistered MQTT Topic Received: {topic}, "
                f"payload: {message.payload.decode()}")

    def _on_connect(self,
                    client: paho_mqtt.Client,
                    user_data: Any,
                    flags: Dict[str, Any],
                    reason_code: Union[int, paho_mqtt.ReasonCodes],
                    properties: Optional[paho_mqtt.Properties] = None
                    ) -> None:
        logging.info("MQTT Client Connected")
        if reason_code == 0:
            self.publish_topic(self.moonraker_status_topic,
                               {'server': 'online'}, retain=True)
            subs = [(k, v[0]) for k, v in self.subscribed_topics.items()]
            if subs:
                res, msg_id = client.subscribe(subs)
                if msg_id is not None:
                    sub_fut: asyncio.Future = asyncio.Future()
                    topics = list(self.subscribed_topics.keys())
                    sub_fut.add_done_callback(
                        BrokerAckLogger(topics, "subscribe"))
                    self.pending_acks[msg_id] = sub_fut
            self.connect_evt.set()
            self.server.send_event("mqtt:connected")
        else:
            if isinstance(reason_code, int):
                err_str = paho_mqtt.connack_string(reason_code)
            else:
                err_str = reason_code.getName()
            self.server.set_failed_component("mqtt")
            self.server.add_warning(f"MQTT Connection Failed: {err_str}")

    def _on_disconnect(self,
                       client: paho_mqtt.Client,
                       user_data: Any,
                       reason_code: int,
                       properties: Optional[paho_mqtt.Properties] = None
                       ) -> None:
        if self.disconnect_evt is not None:
            self.disconnect_evt.set()
        elif self.is_connected():
            # The server connection was dropped, attempt to reconnect
            logging.info("MQTT Server Disconnected, reason: "
                         f"{paho_mqtt.error_string(reason_code)}")
            if self.connect_task is None:
                self.connect_task = asyncio.create_task(self._do_reconnect())
            self.server.send_event("mqtt:disconnected")
        self.connect_evt.clear()

    def _on_publish(self,
                    client: paho_mqtt.Client,
                    user_data: Any,
                    msg_id: int
                    ) -> None:
        pub_fut = self.pending_acks.pop(msg_id, None)
        if pub_fut is not None and not pub_fut.done():
            pub_fut.set_result(None)

    def _on_subscribe(self,
                      client: paho_mqtt.Client,
                      user_data: Any,
                      msg_id: int,
                      flex: Union[List[int], List[paho_mqtt.ReasonCodes]],
                      properties: Optional[paho_mqtt.Properties] = None
                      ) -> None:
        sub_fut = self.pending_acks.pop(msg_id, None)
        if sub_fut is not None and not sub_fut.done():
            sub_fut.set_result(flex)

    def _on_unsubscribe(self,
                        client: paho_mqtt.Client,
                        user_data: Any,
                        msg_id: int,
                        properties: Optional[paho_mqtt.Properties] = None,
                        reasoncodes: Optional[paho_mqtt.ReasonCodes] = None
                        ) -> None:
        unsub_fut = self.pending_acks.pop(msg_id, None)
        if unsub_fut is not None and not unsub_fut.done():
            unsub_fut.set_result(None)

    async def _do_reconnect(self, first: bool = False) -> None:
        logging.info("Attempting MQTT Connect/Reconnect")
        last_err: Exception = Exception()
        while True:
            if not first:
                try:
                    await asyncio.sleep(2.)
                except asyncio.CancelledError:
                    raise
            first = False
            try:
                sock = await self.event_loop.create_socket_connection(
                    (self.address, self.port), timeout=10
                )
                self.client.reconnect(sock)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if type(last_err) is not type(e) or last_err.args != e.args:
                    logging.exception("MQTT Connection Error")
                    last_err = e
                continue
            self.client.socket().setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, 2048)
            break
        self.connect_task = None

    async def wait_connection(self, timeout: Optional[float] = None) -> bool:
        try:
            await asyncio.wait_for(self.connect_evt.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    def is_connected(self) -> bool:
        return self.connect_evt.is_set()

    def subscribe_topic(self,
                        topic: str,
                        callback: FlexCallback,
                        qos: Optional[int] = None
                        ) -> SubscriptionHandle:
        if '#' in topic or '+' in topic:
            raise self.server.error("Wildcards may not be used")
        qos = qos or self.qos
        if qos > 2 or qos < 0:
            raise self.server.error("QOS must be between 0 and 2")
        hdl = SubscriptionHandle(topic, callback)
        sub_handles = [hdl]
        need_sub = True
        if topic in self.subscribed_topics:
            prev_qos, sub_handles = self.subscribed_topics[topic]
            qos = max(qos, prev_qos)
            sub_handles.append(hdl)
            need_sub = qos != prev_qos
        self.subscribed_topics[topic] = (qos, sub_handles)
        if self.is_connected() and need_sub:
            res, msg_id = self.client.subscribe(topic, qos)
            if msg_id is not None:
                sub_fut: asyncio.Future = asyncio.Future()
                sub_fut.add_done_callback(
                    BrokerAckLogger([topic], "subscribe"))
                self.pending_acks[msg_id] = sub_fut
        return hdl

    def unsubscribe(self, hdl: SubscriptionHandle) -> None:
        topic = hdl.topic
        if topic in self.subscribed_topics:
            sub_hdls = self.subscribed_topics[topic][1]
            try:
                sub_hdls.remove(hdl)
            except Exception:
                pass
            if not sub_hdls:
                del self.subscribed_topics[topic]
                res, msg_id = self.client.unsubscribe(topic)
                if msg_id is not None:
                    unsub_fut: asyncio.Future = asyncio.Future()
                    unsub_fut.add_done_callback(
                        BrokerAckLogger([topic], "unsubscribe"))
                    self.pending_acks[msg_id] = unsub_fut

    def publish_topic(self,
                      topic: str,
                      payload: Any = None,
                      qos: Optional[int] = None,
                      retain: bool = False
                      ) -> Awaitable[None]:
        qos = qos or self.qos
        if qos > 2 or qos < 0:
            raise self.server.error("QOS must be between 0 and 2")
        pub_fut: asyncio.Future = asyncio.Future()
        if isinstance(payload, (dict, list)):
            try:
                payload = json.dumps(payload)
            except json.JSONDecodeError:
                raise self.server.error(
                    "Dict or List is not json encodable") from None
        elif isinstance(payload, bool):
            payload = str(payload).lower()
        try:
            msg_info = self.client.publish(topic, payload, qos, retain)
            if msg_info.is_published():
                pub_fut.set_result(None)
            else:
                if qos == 0:
                    # There is no delivery guarantee for qos == 0, so
                    # it is possible that the on_publish event will
                    # not be called if paho mqtt encounters an error
                    # during publication.  Return immediately as
                    # a workaround.
                    if msg_info.rc != paho_mqtt.MQTT_ERR_SUCCESS:
                        err_str = paho_mqtt.error_string(msg_info.rc)
                        pub_fut.set_exception(self.server.error(
                            f"MQTT Publish Error: {err_str}", 503))
                    else:
                        pub_fut.set_result(None)
                    return pub_fut
                self.pending_acks[msg_info.mid] = pub_fut
        except ValueError:
            pub_fut.set_exception(self.server.error(
                "MQTT Message Queue Full", 529))
        except Exception as e:
            pub_fut.set_exception(self.server.error(
                f"MQTT Publish Error: {e}", 503))
        return pub_fut

    async def publish_topic_with_response(self,
                                          topic: str,
                                          response_topic: str,
                                          payload: Any = None,
                                          qos: Optional[int] = None,
                                          retain: bool = False,
                                          timeout: Optional[float] = None
                                          ) -> bytes:
        qos = qos or self.qos
        if qos > 2 or qos < 0:
            raise self.server.error("QOS must be between 0 and 2")
        resp_fut: asyncio.Future = asyncio.Future()
        resp_hdl = self.subscribe_topic(
            response_topic, resp_fut.set_result, qos)
        self.pending_responses.append(resp_fut)
        try:
            await asyncio.wait_for(self.publish_topic(
                topic, payload, qos, retain), timeout)
            await asyncio.wait_for(resp_fut, timeout)
        except asyncio.TimeoutError:
            logging.info(f"Response to request {topic} timed out")
            raise self.server.error("MQTT Request Timed Out", 504)
        finally:
            try:
                self.pending_responses.remove(resp_fut)
            except Exception:
                pass
            self.unsubscribe(resp_hdl)
        return resp_fut.result()

    async def _handle_publish_request(self,
                                      web_request: WebRequest
                                      ) -> Dict[str, Any]:
        topic: str = web_request.get_str("topic")
        payload: Any = web_request.get("payload", None)
        qos: int = web_request.get_int("qos", self.qos)
        retain: bool = web_request.get_boolean("retain", False)
        timeout: Optional[float] = web_request.get_float('timeout', None)
        try:
            await asyncio.wait_for(self.publish_topic(
                topic, payload, qos, retain), timeout)
        except asyncio.TimeoutError:
            raise self.server.error("MQTT Publish Timed Out", 504)
        return {
            "topic": topic
        }

    async def _handle_subscription_request(self,
                                           web_request: WebRequest
                                           ) -> Dict[str, Any]:
        topic: str = web_request.get_str("topic")
        qos: int = web_request.get_int("qos", self.qos)
        timeout: Optional[float] = web_request.get_float('timeout', None)
        resp: asyncio.Future = asyncio.Future()
        hdl: Optional[SubscriptionHandle] = None
        try:
            hdl = self.subscribe_topic(topic, resp.set_result, qos)
            self.pending_responses.append(resp)
            await asyncio.wait_for(resp, timeout)
            ret: bytes = resp.result()
        except asyncio.TimeoutError:
            raise self.server.error("MQTT Subscribe Timed Out", 504)
        finally:
            try:
                self.pending_responses.remove(resp)
            except Exception:
                pass
            if hdl is not None:
                self.unsubscribe(hdl)
        try:
            payload = json.loads(ret)
        except json.JSONDecodeError:
            payload = ret.decode()
        return {
            'topic': topic,
            'payload': payload
        }

    async def _process_api_request(self, payload: bytes) -> None:
        response = await self.json_rpc.dispatch(payload.decode())
        if response is not None:
            await self.publish_topic(self.api_resp_topic, response,
                                     self.api_qos)

    def register_api_handler(self, api_def: APIDefinition) -> None:
        if api_def.callback is None:
            # Remote API, uses RPC to reach out to Klippy
            mqtt_method = api_def.jrpc_methods[0]
            rpc_cb = self._generate_remote_callback(api_def.endpoint)
            self.json_rpc.register_method(mqtt_method, rpc_cb)
        else:
            # Local API, uses local callback
            for mqtt_method, req_method in \
                    zip(api_def.jrpc_methods, api_def.request_methods):
                rpc_cb = self._generate_local_callback(
                    api_def.endpoint, req_method, api_def.callback)
                self.json_rpc.register_method(mqtt_method, rpc_cb)
        logging.info(
            "Registering MQTT JSON-RPC methods: "
            f"{', '.join(api_def.jrpc_methods)}")

    def remove_api_handler(self, api_def: APIDefinition) -> None:
        for jrpc_method in api_def.jrpc_methods:
            self.json_rpc.remove_method(jrpc_method)

    def _generate_local_callback(self,
                                 endpoint: str,
                                 request_method: str,
                                 callback: Callable[[WebRequest], Coroutine]
                                 ) -> RPCCallback:
        async def func(args: Dict[str, Any]) -> Any:
            self._check_timestamp(args)
            result = await callback(WebRequest(endpoint, args, request_method))
            return result
        return func

    def _generate_remote_callback(self, endpoint: str) -> RPCCallback:
        async def func(args: Dict[str, Any]) -> Any:
            self._check_timestamp(args)
            result = await self.klippy.request(WebRequest(endpoint, args))
            return result
        return func

    def _check_timestamp(self, args: Dict[str, Any]) -> None:
        ts = args.pop("mqtt_timestamp", None)
        if ts is not None:
            if ts in self.timestamp_deque:
                logging.debug("Duplicate MQTT API request received")
                raise self.server.error(
                    "Duplicate MQTT Request", DUP_API_REQ_CODE)
            else:
                self.timestamp_deque.append(ts)

    def send_status(self,
                    status: Dict[str, Any],
                    eventtime: float
                    ) -> None:
        if not status or not self.is_connected():
            return
        if self.publish_split_status:
            for objkey in status:
                objval = status[objkey]
                for statekey in objval:
                    payload = {'eventtime': eventtime,
                               'value': objval[statekey]}
                    self.publish_topic(
                        f"{self.klipper_state_prefix}/{objkey}/{statekey}",
                        payload, retain=True)
        else:
            payload = {'eventtime': eventtime, 'status': status}
            self.publish_topic(self.klipper_status_topic, payload)


    def get_instance_name(self) -> str:
        return self.instance_name

    async def close(self) -> None:
        if self.connect_task is not None:
            self.connect_task.cancel()
            self.connect_task = None
        if not self.is_connected():
            return
        await self.publish_topic(self.moonraker_status_topic,
                                 {'server': 'offline'},
                                 retain=True)
        self.disconnect_evt = asyncio.Event()
        self.client.disconnect()
        try:
            await asyncio.wait_for(self.disconnect_evt.wait(), 2.)
        except asyncio.TimeoutError:
            logging.info("MQTT Disconnect Timeout")
        futs = list(self.pending_acks.values())
        futs.extend(self.pending_responses)
        for fut in futs:
            if fut.done():
                continue
            fut.set_exception(
                self.server.error("Moonraker Shutdown", 503))

    async def _publish_from_klipper(self,
                                    topic: str,
                                    payload: Any = None,
                                    qos: Optional[int] = None,
                                    retain: bool = False,
                                    use_prefix: bool = False
                                    ) -> None:
        if use_prefix:
            topic = f"{self.instance_name}/{topic.lstrip('/')}"
        await self.publish_topic(topic, payload, qos, retain)


def load_component(config: ConfigHelper) -> MQTTClient:
    return MQTTClient(config)
