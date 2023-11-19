from __future__ import annotations

from typing import Dict, Any
from urllib.parse import urlencode, quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class Tasmota(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin", default_password="")
        self.output_id = config.getint("output_id", 1)
        self.timer = config.get("timer", "")

    async def _send_tasmota_command(self, command: str) -> Dict[str, Any]:
        if command in ["on", "off"]:
            out_cmd = f"Power{self.output_id} {command}"
            if self.timer != "" and command == "off":
                out_cmd = f"Backlog Delay {self.timer}0; {out_cmd}"
        elif command == "info":
            out_cmd = f"Power{self.output_id}"
        else:
            raise self.server.error(f"Invalid tasmota command: {command}")
        query = urlencode({
            "user": self.user,
            "password": self.password,
            "cmnd": out_cmd
        })
        url = f"{self.protocol}://{quote(self.addr)}/cm?{query}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_tasmota_command("info")
        try:
            state: str = res[f"POWER{self.output_id}"].lower()
        except KeyError as e:
            if self.output_id == 1:
                state = res["POWER"].lower()
            else:
                raise KeyError(e)
        return state

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_tasmota_command(state)
        if self.timer == "" or state != "off":
            try:
                state = res[f"POWER{self.output_id}"].lower()
            except KeyError as e:
                if self.output_id == 1:
                    state = res["POWER"].lower()
                else:
                    raise KeyError(e)
        return state
