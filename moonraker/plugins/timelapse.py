# Timelapse plugin
#
# Copyright (C) 2020 Christoph Frei <fryakatkop@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
###################################
import logging
import os
import glob
from datetime import datetime
from tornado.ioloop import IOLoop


class Timelapse:

    def __init__(self, config):
        # setup vars
        self.renderisrunning = False
        self.framecount = 0
        self.enabled = config.getboolean("enabled", True)
        self.crf = config.getint("constant_rate_factor", 23)
        self.framerate = config.getint("output_framerate", 30)
        self.timeformatcode = config.get("time_format_code", "%Y%m%d_%H%M")
        self.snapshoturl = config.get(
            "snapshoturl", "http://localhost:8080/?action=snapshot")
        self.pixelformat = config.get("pixelformat", "yuv420p")
        self.extraoutputparams = config.get("extraoutputparams", "")
        out_dir_cfg = config.get("output_path", "~/timelapse/")
        self.out_dir = os.path.expanduser(out_dir_cfg)
        self.temp_dir = "/tmp/timelapse/"
        self.lastcmdreponse = ""

        # setup directories
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.out_dir, exist_ok=True)

        # setup eventhandlers and endpoints
        self.server = config.get_server()
        file_manager = self.server.lookup_plugin("file_manager")
        file_manager.register_directory("timelapse", self.out_dir)
        self.server.register_event_handler(
            "server:gcode_response", self.handle_status_update)
        self.server.register_remote_method(
            "timelapse_newframe", self.call_timelapse_newframe)
        self.server.register_endpoint(
            "/machine/timelapse/render", ['POST'], self.timelapse_render)
        self.server.register_endpoint(
            "/machine/timelapse/settings", ['GET', 'POST'],
            self.webrequest_timelapse_settings)

    async def webrequest_timelapse_settings(self, webrequest):
        action = webrequest.get_action()
        if action == 'POST':
            args = webrequest.get_args()
            # logging.info("webreq_args: " + str(args))
            for arg in args:
                val = args.get(arg)
                if arg == "enabled":
                    self.enabled = webrequest.get_boolean(arg)
                if arg == "constant_rate_factor":
                    self.crf = webrequest.get_int(arg)
                if arg == "output_framerate":
                    self.framerate = webrequest.get_int(arg)
                if arg == "pixelformat":
                    self.pixelformat = webrequest.get(arg)
                if arg == "extraoutputparams":
                    self.extraoutputparams = webrequest.get(arg)
        return {
                'enabled': self.enabled,
                'constant_rate_factor': self.crf,
                'output_framerate': self.framerate,
                'pixelformat': self.pixelformat,
                'extraoutputparams': self.extraoutputparams
            }

    def call_timelapse_newframe(self):
        if self.enabled:
            ioloop = IOLoop.current()
            ioloop.spawn_callback(self.timelapse_newframe)
        # else:
            # logging.info("NEW_FRAME macro ignored timelapse is disabled")

    async def timelapse_newframe(self):
        self.framecount += 1
        framefile = "frame" + str(self.framecount).zfill(6) + ".jpg"
        cmd = "wget " + self.snapshoturl + " -O " \
              + self.temp_dir + framefile
        # logging.info(f"cmd: {cmd}")
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")

    async def webrequest_timelapse_render(self, webrequest):
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.timelapse_render)
        return "ok"

    def handle_status_update(self, status):
        if status == "File selected":
            # print_started
            self.timelapse_cleanup()
        elif status == "Done printing file":
            # print_done
            if self.enabled:
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.timelapse_render)

    def timelapse_cleanup(self):
        # logging.info("timelapse_cleanup")
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if filelist:
            for filepath in filelist:
                os.remove(filepath)
        self.framecount = 0

    async def timelapse_render(self, webrequest=None):
        # logging.info("timelapse_render")
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if not filelist:
            msg = "no frames to render skipping"
            logging.info(msg)
            status = "skipped"
            cmd = outfile = None
        if self.renderisrunning:
            msg = "render is already running"
            logging.info(msg)
            status = "alreadyrunning"
            cmd = outfile = None
        else:
            self.renderisrunning = True
            self.framecount = 0
            klippy_apis = self.server.lookup_plugin("klippy_apis")
            result = await klippy_apis.query_objects({'print_stats': None})
            pstats = result.get("print_stats", {})
            gcodefile = pstats.get("filename", "")  # .split(".", 1)[0]
            # logging.info(f"gcodefile: {gcodefile}")
            now = datetime.now()
            date_time = now.strftime(self.timeformatcode)
            inputfiles = self.temp_dir + "frame%6d.jpg"
            outsuffix = ".mp4"
            outfile = "timelapse_" + gcodefile + "_" + date_time + outsuffix
            cmd = "ffmpeg" \
                  + " -r " + str(self.framerate) \
                  + " -i '" + inputfiles + "'" \
                  + " -crf " + str(self.crf) \
                  + " -vcodec libx264" \
                  + " -pix_fmt " + self.pixelformat \
                  + " " + self.extraoutputparams \
                  + " '" + self.out_dir + outfile + "' -y"
            logging.info(f"start FFMPEG: {cmd}")
            shell_command = self.server.lookup_plugin("shell_command")
            scmd = shell_command.build_shell_command(cmd, self.ffmpeg_response)
            try:
                cmdstatus = await scmd.run(timeout=None, verbose=True)
            except Exception:
                logging.exception(f"Error running cmd '{cmd}'")

            # check success
            if cmdstatus:
                status = "success"
                msg = f"Rendering Video successful: {outfile}"
                result = {'action': 'render', 'status': 'success', 'filename': outfile}
            else:
                status = "error"
                response = self.lastcmdreponse.decode("utf-8")
                msg = f"Rendering Video failed: {response}"
                result = {'action': 'render', 'status': 'error', 'response': response}
                                      
            self.renderisrunning = False

        return {
                'status': status,
                'msg': msg,
                'file': outfile,
                'cmd': cmd
            }

    def ffmpeg_response(self, response):
        # logging.info(f"ffmpegResponse: {response}")
        self.lastcmdreponse = response

def load_plugin(config):
    return Timelapse(config)
