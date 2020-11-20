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
        # setup global vars
        self.framecount = 0
        self.enabled = config.getboolean("enabled" , True) 
        self.crf = config.getint("constant_rate_factor" , 23) 
        self.framerate = config.getint("output_framerate" , 30)
        self.timeformatcode = config.get("time_format_code", "%Y%m%d_%H%M")
        out_dir_cfg = config.get("output_path" , "~/timelapse/")
        self.out_dir = os.path.expanduser(out_dir_cfg)
        self.temp_dir = "/tmp/timelapse/"
        
        # setup directories 
        os.makedirs(self.temp_dir, exist_ok=True)   
        os.makedirs(self.out_dir, exist_ok=True)
        
        # setup eventhandlers and endpoints
        self.server = config.get_server() 
        file_manager = self.server.lookup_plugin("file_manager")
        file_manager.register_directory("timelapses", self.out_dir)
        self.server.register_event_handler(
            "server:gcode_response", self.handle_status_update)
        self.server.register_remote_method(
            "timelapse_newframe", self.call_timelapse_newframe)
        self.server.register_endpoint(
            "/machine/timelapse/finish", ['POST'], self.webrequest_timelapse_finish)
        self.server.register_endpoint(
            "/machine/timelapse/settings", ['GET', 'POST'], self.webrequest_timelapse_settings)
                
    async def webrequest_timelapse_settings(self, webrequest):
        action = webrequest.get_action()
        if action == 'POST':
            args = webrequest.get_args()
            logging.info("webreq_args: " + str(args))
            for arg in args:
                val = args.get(arg)
                if arg == "enabled":
                    self.enabled = webrequest.get_boolean(arg)
                    logging.info("enabled_new: " + str(self.enabled) + " type: " + str(type(self.enabled)))
                if arg == "constant_rate_factor":
                    self.crf = webrequest.get_int(arg)                
                    logging.info("crf_new: " + str(self.crf) + " type: " + str(type(self.crf)))
                if arg == "output_framerate":
                    self.framerate = webrequest.get_int(arg)                
                    logging.info("framerate_new: " + str(self.framerate) + " type: " + str(type(self.framerate)))
        return {
            'enabled': self.enabled,
            'constant_rate_factor': self.crf,
            'output_framerate': self.framerate
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
        
    async def webrequest_timelapse_finish(self, webrequest):
        ioloop = IOLoop.current()
        ioloop.spawn_callback(self.timelapse_finish)
        return "ok"
            
    def handle_status_update(self, status):
        if status == "File selected":
            #print_started
            self.timelapse_cleanup()
        elif status == "Done printing file":
            #print_done
            if self.enabled:
                ioloop = IOLoop.current()
                ioloop.spawn_callback(self.timelapse_finish)
            
    def timelapse_cleanup(self):
        logging.info("timelapse_cleanup")
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if filelist:
            for filepath in filelist:
                os.remove(filepath)
        self.framecount = 0     
        
    async def timelapse_finish(self):
        logging.info("timelapse_finish")
        filelist = glob.glob(self.temp_dir + "frame*.jpg")
        if not filelist:
            logging.info("timelapse_finish: no frames, skip video render ")
        else:
            self.framecount = 0
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
            
def load_plugin(config):
    return Timelapse(config)
