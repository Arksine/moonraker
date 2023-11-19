from __future__ import annotations

from typing import Dict, Any, Optional, List, cast
from urllib.parse import quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class SmartThings(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_port=443, default_protocol="https")
        self.device: str = config.get("device", "")
        self.token: str = config.gettemplate("token").render()

    async def _send_smartthings_command(self, command: str) -> Dict[str, Any]:
        body: Optional[List[Dict[str, Any]]] = None
        if (command == "on" or command == "off"):
            method = "POST"
            url = (
                f"{self.protocol}://{quote(self.addr)}"
                f"/v1/devices/{quote(self.device)}/commands"
            )
            body = [
                {
                    "component": "main",
                    "capability": "switch",
                    "command": command
                }
            ]
        elif command == "info":
            method = "GET"
            url = (
                f"{self.protocol}://{quote(self.addr)}/v1/devices/"
                f"{quote(self.device)}/components/main/capabilities/"
                "switch/status"
            )
        else:
            raise self.server.error(
                f"Invalid SmartThings command: {command}")

        headers = {
            'Authorization': f'Bearer {self.token}'
        }
        response = await self.client.request(
            method, url, body=body, headers=headers,
            attempts=3, enable_cache=False
        )
        msg = f"Error sending SmartThings command: {command}"
        response.raise_for_status(msg)
        data = cast(dict, response.json())
        return data

    async def _send_status_request(self) -> str:
        res = await self._send_smartthings_command("info")
        return res["switch"]["value"].lower()

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_smartthings_command(state)
        acknowledgment = res["results"][0]["status"].lower()
        return state if acknowledgment == "accepted" else "error"
