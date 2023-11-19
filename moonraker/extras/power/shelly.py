from __future__ import annotations

from typing import Dict, Any
from urllib.parse import quote, urlencode

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class Shelly(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin", default_password="")
        self.output_id = config.getint("output_id", 0)
        self.timer = config.get("timer", "")

    async def _send_shelly_command(self, command: str) -> Dict[str, Any]:
        query_args: Dict[str, Any] = {}
        out_cmd = f"relay/{self.output_id}"
        if command in ["on", "off"]:
            query_args["turn"] = command
            if command == "off" and self.timer != "":
                query_args["turn"] = "on"
                query_args["timer"] = self.timer
        elif command != "info":
            raise self.server.error(f"Invalid shelly command: {command}")
        if self.password != "":
            out_pwd = f"{quote(self.user)}:{quote(self.password)}@"
        else:
            out_pwd = ""
        query = urlencode(query_args)
        url = f"{self.protocol}://{out_pwd}{quote(self.addr)}/{out_cmd}?{query}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_shelly_command("info")
        state: str = res["ison"]
        timer_remaining = res["timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_shelly_command(state)
        state = res["ison"]
        timer_remaining = res["timer_remaining"] if self.timer != "" else 0
        return "on" if state and timer_remaining == 0 else "off"
