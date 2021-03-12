# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, time
from tornado.ioloop import IOLoop

SAVE_INTERVAL = 5

HIST_NAMESPACE = "history"
JOBS_AUTO_INC_KEY = "history_auto_inc_id"

class History:
    def __init__(self, config):
        self.server = config.get_server()
        self.database = self.server.lookup_plugin("database")
        self.gcdb = self.database.wrap_namespace("gcode_metadata",
            parse_keys=False)
        self.current_job = None
        self.current_job_id = None
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

        self.database.register_local_namespace(HIST_NAMESPACE)
        self.history_ns = self.database.wrap_namespace(HIST_NAMESPACE,
            parse_keys=False)

        if JOBS_AUTO_INC_KEY not in self.database.ns_keys("moonraker"):
            self.database.insert_item("moonraker", JOBS_AUTO_INC_KEY, 0)

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
            for job in self.history_ns.keys():
                self.delete_job(job, False)
                deljobs.append(job)
            self.database.update_item("moonraker", JOBS_AUTO_INC_KEY, 0);
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
            if id not in self.history_ns:
                raise self.server.error(f"Invalid job id: {id}")
            return {id: self.history_ns.get(id, {})}

        before = web_request.get_float("before", -1)
        since = web_request.get_float("since", -1)
        limit = web_request.get_int("limit", 50)
        start = web_request.get_int("start", 0)
        if start > (len(self.history_ns)-1) or len(self.history_ns) == 0:
            return {"count": len(self.history_ns), "prints": {}}

        i = 0
        end_num = len(self.history_ns)
        jobs = {}
        start_num = 0

        for id in self.history_ns.keys():
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

    def _save_job_on_error(self):
        if self.current_job != None:
            self.save_current_job()

    def add_job(self, job):
        self.current_job_id = str(self.database.get_item("moonraker",
            JOBS_AUTO_INC_KEY))
        self.database.update_item("moonraker", JOBS_AUTO_INC_KEY,
            int(self.current_job_id)+1)
        self.current_job = job
        self.grab_job_metadata()
        self.history_ns.insert(self.current_job_id, job.get_stats())

    def delete_job(self, id, check_metadata=True):
        id = str(id)

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

    def grab_job_metadata(self):
        if self.current_job == None:
            return

        filename = self.current_job.get("filename")
        if filename not in self.gcdb:
            return

        metadata = {k:v for k,v in self.gcdb.get(filename).items()
            if k != "thumbnails"}
        self.current_job.set("metadata", metadata)

    def save_current_job(self):
        self.history_ns.update_child(self.current_job_id,
            self.current_job.get_stats())

class PrinterJob:
    def __init__(self, data={}, file_metadata={}):
        self.end_time = None
        self.filament_used = 0
        self.filename = None
        self.metadata = None
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
