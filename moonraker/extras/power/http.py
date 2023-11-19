from __future__ import annotations

import asyncio
import logging
from typing import Dict, Any

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class GenericHTTP(HTTPDevice):
    def __init__(self, config: ConfigHelper,) -> None:
        super().__init__(config, is_generic=True)
        self.urls: Dict[str, str] = {
            "on": config.gettemplate("on_url").render(),
            "off": config.gettemplate("off_url").render(),
            "status": config.gettemplate("status_url").render()
        }
        self.request_template = config.gettemplate(
            "request_template", None, is_async=True
        )
        self.response_template = config.gettemplate("response_template", is_async=True)

    async def _send_generic_request(self, command: str) -> str:
        request = self.client.wrap_request(
            self.urls[command], request_timeout=20., attempts=3, retry_pause_time=1.
        )
        context: Dict[str, Any] = {
            "command": command,
            "http_request": request,
            "async_sleep": asyncio.sleep,
            "log_debug": logging.debug,
            "urls": dict(self.urls)
        }
        if self.request_template is not None:
            await self.request_template.render_async(context)
            response = request.last_response()
            if response is None:
                raise self.server.error("Failed to receive a response")
        else:
            response = await request.send()
        response.raise_for_status()
        result = (await self.response_template.render_async(context)).lower()
        if result not in ["on", "off"]:
            raise self.server.error(f"Invalid result: {result}")
        return result

    async def _send_power_request(self, state: str) -> str:
        return await self._send_generic_request(state)

    async def _send_status_request(self) -> str:
        return await self._send_generic_request("status")
