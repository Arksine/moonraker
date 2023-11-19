from __future__ import annotations

import asyncio
import logging
from typing import Optional

from moonraker.components.mqtt import MQTTClient
from moonraker.components.power import PowerDevice
from moonraker.components.template import JinjaTemplate
from moonraker.confighelper import ConfigHelper


class MQTTDevice(PowerDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.mqtt: MQTTClient = self.server.load_component(config, 'mqtt')
        self.eventloop = self.server.get_event_loop()
        self.cmd_topic: str = config.get('command_topic')
        self.cmd_payload: JinjaTemplate = config.gettemplate('command_payload')
        self.retain_cmd_state = config.getboolean('retain_command_state', False)
        self.query_topic: Optional[str] = config.get('query_topic', None)
        self.query_payload = config.gettemplate('query_payload', None)
        self.must_query = config.getboolean('query_after_command', False)
        if self.query_topic is not None:
            self.must_query = False

        self.state_topic: str = config.get('state_topic')
        self.state_timeout = config.getfloat('state_timeout', 2.)
        self.state_response = config.load_template('state_response_template',
                                                   "{payload}")
        self.qos: Optional[int] = config.getint('qos', None, minval=0, maxval=2)
        self.mqtt.subscribe_topic(
            self.state_topic, self._on_state_update, self.qos)
        self.query_response: Optional[asyncio.Future] = None
        self.server.register_event_handler(
            "mqtt:connected", self._on_mqtt_connected)
        self.server.register_event_handler(
            "mqtt:disconnected", self._on_mqtt_disconnected)

    def _on_state_update(self, payload: bytes) -> None:
        last_state = self.state
        in_request = self.request_lock.locked()
        err: Optional[Exception] = None
        context = {
            'payload': payload.decode()
        }
        try:
            response = self.state_response.render(context)
        except Exception as e:
            err = e
            self.state = "error"
        else:
            response = response.lower()
            if response not in ["on", "off"]:
                err_msg = "Invalid State Received. " \
                    f"Raw Payload: '{payload.decode()}', Rendered: '{response}"
                logging.info(f"MQTT Power Device {self.name}: {err_msg}")
                err = self.server.error(err_msg, 500)
                self.state = "error"
            else:
                self.state = response
        if not in_request and last_state != self.state:
            logging.info(f"MQTT Power Device {self.name}: External Power "
                         f"event detected, new state: {self.state}")
            self.notify_power_changed()
        if (
            self.query_response is not None and
            not self.query_response.done()
        ):
            if err is not None:
                self.query_response.set_exception(err)
            else:
                self.query_response.set_result(response)

    async def _on_mqtt_connected(self) -> None:
        async with self.request_lock:
            if self.state in ["on", "off"]:
                return
            self.state = "init"
            success = False
            while self.mqtt.is_connected():
                self.query_response = self.eventloop.create_future()
                try:
                    await self._wait_for_update(self.query_response)
                except asyncio.TimeoutError:
                    # Only wait once if no query topic is set.
                    # Assume that the MQTT device has set the retain
                    # flag on the state topic, and therefore should get
                    # an immediate response upon subscription.
                    if self.query_topic is None:
                        logging.info(f"MQTT Power Device {self.name}: "
                                     "Initialization Timed Out")
                        break
                except Exception:
                    logging.exception(f"MQTT Power Device {self.name}: "
                                      "Init Failed")
                    break
                else:
                    success = True
                    break
                await asyncio.sleep(2.)
            self.query_response = None
            if not success:
                self.state = "error"
            else:
                logging.info(
                    f"MQTT Power Device {self.name} initialized")
            if (
                self.initial_state is not None and
                self.state in ["on", "off"]
            ):
                new_state = "on" if self.initial_state else "off"
                if new_state != self.state:
                    logging.info(
                        f"Power Device {self.name}: setting initial "
                        f"state to {new_state}"
                    )
                    await self.set_power(new_state)
                await self.process_bound_services()
                # Don't reset on next connection
                self.initial_state = None
            self.notify_power_changed()

    async def _on_mqtt_disconnected(self):
        if (
            self.query_response is not None and
            not self.query_response.done()
        ):
            self.query_response.set_exception(
                self.server.error("MQTT Disconnected", 503))
        async with self.request_lock:
            self.state = "error"
            self.notify_power_changed()

    async def refresh_status(self) -> None:
        if (
            self.query_topic is not None and
            (self.must_query or self.state not in ["on", "off"])
        ):
            if not self.mqtt.is_connected():
                raise self.server.error(
                    f"MQTT Power Device {self.name}: "
                    "MQTT Not Connected", 503)
            self.query_response = self.eventloop.create_future()
            try:
                await self._wait_for_update(self.query_response)
            except Exception:
                logging.exception(f"MQTT Power Device {self.name}: "
                                  "Failed to refresh state")
                self.state = "error"
            self.query_response = None

    async def _wait_for_update(self, fut: asyncio.Future,
                               do_query: bool = True
                               ) -> str:
        if self.query_topic is not None and do_query:
            payload: Optional[str] = None
            if self.query_payload is not None:
                payload = self.query_payload.render()
            await self.mqtt.publish_topic(self.query_topic, payload,
                                          self.qos)
        return await asyncio.wait_for(fut, timeout=self.state_timeout)

    async def set_power(self, state: str) -> None:
        if not self.mqtt.is_connected():
            raise self.server.error(
                f"MQTT Power Device {self.name}: "
                "MQTT Not Connected", 503)
        self.query_response = self.eventloop.create_future()
        new_state = "error"
        try:
            payload = self.cmd_payload.render({'command': state})
            await self.mqtt.publish_topic(
                self.cmd_topic, payload, self.qos,
                retain=self.retain_cmd_state)
            new_state = await self._wait_for_update(
                self.query_response, do_query=self.must_query)
        except Exception:
            logging.exception(
                f"MQTT Power Device {self.name}: Failed to set state")
            new_state = "error"
        self.query_response = None
        self.state = new_state
        if self.state == "error":
            raise self.server.error(
                f"MQTT Power Device {self.name}: Failed to set "
                f"device to state '{state}'", 500)
