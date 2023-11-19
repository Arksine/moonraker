from __future__ import annotations

from typing import cast, List, Dict, Any
from urllib.parse import quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class HueDevice(HTTPDevice):

    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config)
        self.device_id = config.get("device_id")
        self.device_type = config.get("device_type", "light")
        if self.device_type == "group":
            self.state_key = "action"
            self.on_state = "all_on"
        else:
            self.state_key = "state"
            self.on_state = "on"

    async def _send_power_request(self, state: str) -> str:
        new_state = True if state == "on" else False
        url = (
            f"{self.protocol}://{quote(self.addr)}/api/{quote(self.user)}"
            f"/{self.device_type}s/{quote(self.device_id)}"
            f"/{quote(self.state_key)}"
        )
        ret = await self.client.request("PUT", url, body={"on": new_state})
        resp = cast(List[Dict[str, Dict[str, Any]]], ret.json())
        state_url = (
            f"/{self.device_type}s/{self.device_id}/{self.state_key}/on"
        )
        return (
            "on" if resp[0]["success"][state_url]
            else "off"
        )

    async def _send_status_request(self) -> str:
        url = (
            f"{self.protocol}://{quote(self.addr)}/api/{quote(self.user)}"
            f"/{self.device_type}s/{quote(self.device_id)}"
        )
        ret = await self.client.request("GET", url)
        resp = cast(Dict[str, Dict[str, Any]], ret.json())
        return "on" if resp["state"][self.on_state] else "off"
