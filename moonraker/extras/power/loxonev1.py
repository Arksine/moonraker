from __future__ import annotations

from typing import Dict, Any
from urllib.parse import quote

from moonraker.components.power import HTTPDevice
from moonraker.confighelper import ConfigHelper


class Loxonev1(HTTPDevice):
    def __init__(self, config: ConfigHelper) -> None:
        super().__init__(config, default_user="admin",
                         default_password="admin")
        self.output_id = config.get("output_id", "")

    async def _send_loxonev1_command(self, command: str) -> Dict[str, Any]:
        if command in ["on", "off"]:
            out_cmd = f"jdev/sps/io/{quote(self.output_id)}/{command}"
        elif command == "info":
            out_cmd = f"jdev/sps/io/{quote(self.output_id)}"
        else:
            raise self.server.error(f"Invalid loxonev1 command: {command}")
        if self.password != "":
            out_pwd = f"{quote(self.user)}:{quote(self.password)}@"
        else:
            out_pwd = ""
        url = f"http://{out_pwd}{quote(self.addr)}/{out_cmd}"
        return await self._send_http_command(url, command)

    async def _send_status_request(self) -> str:
        res = await self._send_loxonev1_command("info")
        state = res["LL"]["value"]
        return "on" if int(state) == 1 else "off"

    async def _send_power_request(self, state: str) -> str:
        res = await self._send_loxonev1_command(state)
        state = res["LL"]["value"]
        return "on" if int(state) == 1 else "off"
