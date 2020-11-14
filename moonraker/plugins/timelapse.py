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
        self.framecount = 0
        self.crf = config.get("constant_rate_factor" , 23) 
        self.framerate = config.get("output_framerate" , 15)
        self.timeformatcode = config.get("time_format_code", "%Y%m%d_%H%M")
        out_dir = config.get("output_path" , "~/timelapse/")
        self.out_dir = os.path.expanduser(out_dir)
        self.temp_dir = "/tmp/timelapse/"        
        os.makedirs(self.temp_dir, exist_ok=True)   
        os.makedirs(self.out_dir, exist_ok=True)
        self.server = config.get_server() 
        self.server.register_remote_method(
            "timelapse_newframe", self.call_timelapse_newframe)
        self.server.register_remote_method(
            "timelapse_finish", self.call_timelapse_finish)
        
    def call_timelapse_newframe(self):
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.timelapse_newframe)
      
    async def timelapse_newframe(self):
        # cleanup if there are old frames
        if self.framecount == 0:
            filelist = glob.glob(self.temp_dir + "frame*.jpg")
            if filelist:
                for filepath in filelist:
                    os.remove(filepath)
        self.framecount += 1        
        framefile = "frame" + str(self.framecount).zfill(6) + ".jpg"
        cmd = "wget http://localhost:8080/?action=snapshot -O " \
            + self.temp_dir + framefile
        logging.info("cmd: " + cmd)
        shell_command = self.server.lookup_plugin('shell_command')
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=2., verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")
               
    def call_timelapse_finish(self):
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.timelapse_finish)
            
    async def timelapse_finish(self):
        #logging.info("timelapse_finish")
        klippy_apis = self.server.lookup_plugin("klippy_apis")
        result = await klippy_apis.query_objects({'print_stats': None})
        pstats = result.get("print_stats", {})
        gcodefile = pstats.get("filename", "") #.split(".", 1)[0]
        #logging.info("gcodefile: " + gcodefile)
        now = datetime.now() 
        date_time = now.strftime(self.timeformatcode)
        inputfiles = self.temp_dir + "frame%6d.jpg"        
        outsuffix = ".mp4"
        outfile = self.out_dir + "timelapse_" \
                + gcodefile + "_" + date_time + outsuffix
        cmd = "ffmpeg" \
              + " -r " + str(self.framerate) \
              + " -i '" + inputfiles + "'" \
              + " -crf " + str(self.crf) \
              + " -vcodec libx264" \
              + " '" + outfile + "' -y" 
        logging.info("cmd: " + cmd)
        shell_command = self.server.lookup_plugin("shell_command")
        scmd = shell_command.build_shell_command(cmd, None)
        try:
            await scmd.run(timeout=None, verbose=False)
        except Exception:
            logging.exception(f"Error running cmd '{cmd}'")
        self.framecount = 0
            
def load_plugin(config):
    return Timelapse(config)
