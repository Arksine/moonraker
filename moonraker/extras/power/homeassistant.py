from __future__ import annotations

import asyncio
from typing import Dict, Any, Optional, cast
from urllib.parse import quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class HomeAssistant(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_port=8123)
        self.device: str = config.get("device")
        self.token: str = config.gettemplate("token").render()
        self.domain: str = config.get("domain", "switch")
        self.status_delay: float = config.getfloat("status_delay", 1.)

    async def _send_homeassistant_command(self, command: str) -> Dict[str, Any]:
        body: Optional[Dict[str, Any]] = None
        if command in ["on", "off"]:
            out_cmd = f"api/services/{quote(self.domain)}/turn_{command}"
            body = {"entity_id": self.device}
            method = "POST"
        elif command == "info":
            out_cmd = f"api/states/{quote(self.device)}"
            method = "GET"
        else:
            raise self.server.error(
                f"Invalid homeassistant command: {command}")
        url = f"{self.protocol}://{quote(self.addr)}:{self.port}/{out_cmd}"
        headers = {
            'Authorization': f'Bearer {self.token}'
        }
        data: Dict[str, Any] = {}
        response = await self.client.request(
            method, url, body=body, headers=headers,
            attempts=3, enable_cache=False
        )
        msg = f"Error sending homeassistant command: {command}"
        response.raise_for_status(msg)
        if method == "GET":
            data = cast(dict, response.json())
        return data

    async def _send_status_request(self) -> str:
        res = await self._send_homeassistant_command("info")
        return res["state"]

    async def _send_power_request(self, state: str) -> str:
        await self._send_homeassistant_command(state)
        await asyncio.sleep(self.status_delay)
        res = await self._send_status_request()
        return res
