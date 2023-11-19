from __future__ import annotations

from typing import Dict, Any
from urllib.parse import urlencode, quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class HomeSeer(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin", default_password="")
        self.device = config.getint("device")

    async def _send_homeseer(
        self, request: str, state: str = ""
    ) -> Dict[str, Any]:
        query_args = {
            "user": self.user,
            "pass": self.password,
            "request": request,
            "ref": self.device,
        }
        if state:
            query_args["label"] = state
        query = urlencode(query_args)
        url = (
            f"{self.protocol}://{quote(self.user)}:{quote(self.password)}@"
            f"{quote(self.addr)}/JSON?{query}"
        )
        return await self._send_http_command(url, request)

    async def _send_status_request(self) -> str:
        res = await self._send_homeseer("getstatus")
        return res["Devices"][0]["status"].lower()

    async def _send_power_request(self, state: str) -> str:
        await self._send_homeseer(
            "controldevicebylabel", state.capitalize()
        )
        return state
