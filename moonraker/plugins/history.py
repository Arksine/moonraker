# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, time
from tornado.ioloop import IOLoop

SAVE_INTERVAL = 5

HIST_NAMESPACE = "history"
JOBS_AUTO_INC_KEY = "job_auto_inc_id"

class History:
    def __init__(self, config):
        self.server = config.get_server()
        self.database = self.server.lookup_plugin("database")
        self.gcdb = self.database.wrap_namespace("gcode_metadata",
            parse_keys=False)
        self.current_job = None
        self.jobs = []
        self.job_id = -1
        self.last_update_time = 0
        self.print_stats = {}

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)
        self.server.register_event_handler(
            "server:klippy_disconnect", self._save_job_on_error)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._save_job_on_error)


        self.server.register_endpoint(
            "/server/history/delete", ['DELETE'], self._handle_job_delete)
        self.server.register_endpoint(
            "/server/history/list", ['GET'], self._handle_jobs_list)
        self.server.register_endpoint(
            "/server/history/metadata", ['GET'], self._handle_job_metadata)

        self.database.register_local_namespace(HIST_NAMESPACE)
        self.history_ns = self.database.wrap_namespace(HIST_NAMESPACE,
            parse_keys=False)
        try:
            cur_job_id = self.history_ns.get(JOBS_AUTO_INC_KEY)
        except:
            logging.info("Creating job history namespace in database")
            self.history_ns.insert(JOBS_AUTO_INC_KEY, 0)

        self.jobs = []
        self.metadata = []
        keys = self.history_ns.keys()
        keys.remove(JOBS_AUTO_INC_KEY)
        for key in keys:
            if key.startswith("md_"):
                self.metadata.append(key[3:])
            else:
                self.jobs.append(key)

    async def _init_ready(self):
        klippy_apis = self.server.lookup_plugin('klippy_apis')
        sub = {"print_stats": None}
        try:
            result = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
        self.print_stats = result.get("print_stats", {})

    async def _handle_job_delete(self, web_request):
        all = web_request.get_boolean("all", False)
        id = str(web_request.get_int("id", -1))
        if all:
            deljobs = []
            jobs = self.jobs.copy()
            for job in jobs:
                self.delete_job(job, False)
                deljobs.append(job)
            for key in self.history_ns.keys():
                self.history_ns.delete(key);
            self.history_ns.insert(JOBS_AUTO_INC_KEY, 0);
            self.metadata = []
            return deljobs

        if id == -1:
            raise self.server.error("No ID to delete")

        if id not in self.history_ns.keys():
            raise self.server.error(f"Invalid job id: {id}")

        self.delete_job(id)
        return [id]

    async def _handle_jobs_list(self, web_request):
        id = str(web_request.get_int("id", -1))
        if id != "-1":
            if id not in self.history_ns.keys():
                raise self.server.error(f"Invalid job id: {id}")
            return {id: self.get_job(id).get_stats()}

        before = web_request.get_float("before", -1)
        since = web_request.get_float("since", -1)
        limit = web_request.get_int("limit", 50)
        start = web_request.get_int("start", 0)
        if start > (len(self.jobs)-1) or len(self.jobs) == 0:
            return {"count": len(self.jobs), "prints": {}}

        i = 0
        end_num = len(self.jobs)
        jobs = {}
        start_num = 0

        for id in self.jobs:
            job = self.history_ns.get(id)
            if since != -1 and since > job.get('start_time'):
                start_num += 1
                continue
            if before != -1 and before < job.get('end_time'):
                end_num -= 1
                continue
            if limit != 0 and i >= limit:
                continue
            if start != 0:
                start -= 1
                continue
            jobs[id] = job
            i += 1

        return {"count": end_num - start_num, "prints": jobs}

    async def _handle_job_metadata(self, web_request):
        id = str(web_request.get_int("id", -1))
        if id == "-1" or id not in self.jobs:
            raise self.server.error(f"Invalid job id: {id}")

        metadata = self.get_metadata(id)
        return metadata

    async def _status_update(self, data):
        if "print_stats" in data:
            ps = data['print_stats']

            if "state" in ps:
                old_state = self.print_stats['state']
                new_state = ps['state']

                if new_state is not old_state:
                    if new_state == "printing" and old_state != "paused":
                        self.print_stats.update(ps)
                        self.add_job(PrinterJob(self.print_stats))
                    elif new_state == "complete" and self.current_job != None:
                        self.print_stats.update(ps)
                        self.finish_job("completed", self.print_stats)
                    elif new_state == "standby"  and self.current_job != None:
                        self.finish_job("cancelled", self.print_stats)

            self.print_stats.update(ps)

            if time.time() > self.last_update_time + SAVE_INTERVAL:
                if self.current_job != None:
                    self.last_update_time = time.time()
                    self.current_job.update_from_ps(self.print_stats)
                    self.save_current_job()

    def _save_job_on_error(self):
        if self.current_job != None:
            self.save_current_job()

    def add_job(self, job):
        self.current_job_id = str(self.history_ns.get(JOBS_AUTO_INC_KEY))
        self.history_ns.update_child(JOBS_AUTO_INC_KEY,
            int(self.current_job_id)+1)
        self.jobs.append(self.current_job_id)
        self.current_job = job
        self.grab_job_metadata()
        self.history_ns.insert(self.current_job_id, job.get_stats())

    def delete_job(self, id, check_metadata=True):
        id = str(id)

        if id in self.jobs:
            self.jobs.remove(id)

        if check_metadata == True:
            other_jobs_using_metadata = False
            filename = self.get_job(id).get("filename")
            index = self.get_job(id).get("metadata")
            for job in self.jobs:
                if self.get_job(job).get("filename") == filename:
                    if self.get_job(job).get("metadata") == index:
                        other_jobs_using_metadata = True

            if other_jobs_using_metadata == False:
                if len(self.history_ns.get([f"md_{filename}"]).keys()) == 1:
                    self.metadata.remove(f"{filename}")
                    self.history_ns.delete([f"md_{filename}"])
                else:
                    self.history_ns.delete([f"md_{filename}", index])

        if id in self.history_ns.keys():
            self.history_ns.delete(id)

        return

    def finish_job(self, status, updates):
        if self.current_job == None:
            return

        self.current_job.finish("completed", self.print_stats)
        # Regrab metadata incase metadata wasn't parsed yet due to file upload
        self.grab_job_metadata()
        self.save_current_job()
        self.current_job = None
        self.current_job_id = None

    def get_job(self, id):
        id = str(id)
        if id not in self.history_ns.keys():
            return None
        return self.history_ns.get(id)

    def get_metadata(self, id):
        job = self.get_job(id)
        md_dict = self.history_ns.get([f"md_{job['filename']}"])
        if job['metadata'] not in md_dict:
            return None
        return md_dict[job['metadata']]

    def grab_job_metadata(self):
        if self.current_job == None:
            return

        filename = self.current_job.get("filename")
        if filename not in self.gcdb:
            return

        metadata = {k:v for k,v in self.gcdb.get(filename).items()
            if k != "thumbnails"}

        if filename in self.metadata:
            md_dict = self.history_ns.get([f"md_{filename}"])
            key = -1
            i = 0
            for k, v in md_dict.items():
                if int(k) > i:
                    i = int(k)
                if metadata['modified'] == v['modified']:
                    key = k
                    break
            if key != -1:
                self.current_job.set("metadata", key)
            else:
                md_dict.update({i+1: metadata})
                self.current_job.set("metadata", i+1)
                self.history_ns.update_child([f"md_{filename}"], md_dict)
        else:
            self.metadata.append(filename)
            self.current_job.set("metadata", 0)
            self.history_ns.insert([f"md_{filename}"], {0:metadata})

    def save_current_job(self):
        self.history_ns.update_child(self.current_job_id,
            self.current_job.get_stats())

class PrinterJob:
    def __init__(self, data={}, file_metadata={}):
        self.end_time = None
        self.filament_used = 0
        self.filename = None
        self.metadata = 0
        self.print_duration = 0
        self.status = "in_progress"
        self.start_time = time.time()
        self.total_duration = 0
        self.update_from_ps(data)

    def finish(self, status, print_stats={}):
        self.end_time = time.time()
        self.status = status
        self.update_from_ps(print_stats)

    def get(self, name):
        if not hasattr(self, name):
            return None
        return getattr(self, name)

    def get_stats(self):
        return self.__dict__

    def set(self, name, val):
        if not hasattr(self, name):
            return
        setattr(self, name, val)

    def update_from_ps(self, data):
        for i in data:
            if hasattr(self, i):
                setattr(self, i, data[i])

def load_plugin(config):
    return History(config)
