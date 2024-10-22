# KlipperEstimator - Run uploaded gcode files through klipper_estimator
#
# Copyright (C) 2024 Nelson Graça <graca.nelson@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license

import asyncio
import logging
import os
import os.path
import stat
from typing import cast
from moonraker.components.http_client import HttpClient


class KlipperEstimator:
    def __init__(self, config) -> None:
        self.server = config.get_server()
        self.name = config.get_name()
        self.ke_exec: str = config.get("executable_path", "/tmp/klipper-estimator")
        self.download = config.getboolean("download", False)
        self.tag = config.get("tag", "latest")
        if self.tag != "latest":
            self.tag = f"tags/{self.tag}"
        self.asset_name = config.get("asset_name", "klipper_estimator_rpi")
        self.file_manager = self.server.lookup_component('file_manager')
        self.gc_path = self.file_manager.get_directory()
        hostname = self.server.get_host_info()["hostname"]
        port = self.server.get_host_info()["port"]
        self.url = f"http://{hostname}:{port}/"
        self.server.register_event_handler(
            "file_manager:filelist_changed",
            self._handle_filelist_changed
        )
        if self.download:
            asyncio.create_task(self._download())

    async def _download(self) -> None:
        client: HttpClient = self.server.lookup_component("http_client")
        response = await client.github_api_request(
            f"repos/Annex-Engineering/klipper_estimator/releases/{self.tag}")
        response.raise_for_status(
            "Failed to get latest Klipper Estimator info.")
        data = cast(dict, response.json())
        tag_name = data["tag_name"]
        try:
            current_version = open(f"{self.ke_exec}.version", "r").read()
        except Exception as e:
            logging.info("Cant determine current version")
            logging.info(e)
            current_version = "9999"
        if current_version != tag_name or not os.path.isfile(self.ke_exec):
            for asset in data["assets"]:
                if asset["name"] == self.asset_name:
                    down_url = asset["browser_download_url"]
                    content_type = asset["content_type"]
                    logging.info(
                        f"Downloading {self.asset_name} from {down_url}")
                    try:
                        b = await client.get_file(down_url, content_type)
                        f = open(self.ke_exec, "wb")
                        f.write(b)
                        f.close()
                        os.chmod(self.ke_exec,
                                 os.stat(self.ke_exec).st_mode | stat.S_IEXEC)
                    except Exception as e:
                        logging.error(e)
                    logging.info("Done")
                    file = open(f"{self.ke_exec}.version", "w")
                    file.write(tag_name)
                    file.close()
        else:
            logging.info("klipper-estimator already latest")

    async def _handle_filelist_changed(self, event) -> None:
        action = event["action"]
        path = event["item"]["path"]
        if action in ["create_file", "modify_file"]:
            if path.lower().endswith(".gcode"):
                full_path = f"{self.gc_path}/{path}"
                logging.info(f"Running klipper-estimator in {path}")
                await self._run_estimator(full_path)

    async def _run_estimator(self, path) -> None:
        path = path.replace("\"", "\\\"")
        cmd = " ".join(
            [self.ke_exec, "--config_moonraker_url", self.url, "post-process",
             f"\"{path}\""])
        logging.info(f"Running {cmd}")
        timeout = 10.
        result = bytearray()
        sc = self.server.lookup_component('shell_command')
        scmd = sc.build_shell_command(cmd, callback=result.extend,
                                      log_stderr=True)
        if not await scmd.run(timeout=timeout):
            logging.error("KlipperEstimator failed!")
        logging.info(f"KlipperEstimator output: {result.decode()}")


def load_component(config) -> KlipperEstimator:
    return KlipperEstimator(config)
