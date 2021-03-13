# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import time

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
            "server:klippy_disconnect", self._handle_disconnect)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_notification("history:history_changed")

        self.server.register_endpoint(
            "/server/history/job", ['GET', 'DELETE'], self._handle_job_request)
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

    async def _handle_job_request(self, web_request):
        action = web_request.get_action()
        if action == "GET":
            id = web_request.get_str("id")
            if id not in self.history_ns:
                raise self.server.error(f"Invalid job id: {id}", 404)
            job = self.history_ns[id]
            job['job_id'] = id
            return {"job": job}
        if action == "DELETE":
            all = web_request.get_boolean("all", False)
            if all:
                deljobs = []
                for job in self.history_ns.keys():
                    self.delete_job(job)
                    deljobs.append(job)
                self.database.insert_item("moonraker", JOBS_AUTO_INC_KEY, 0)
                self.metadata = []
                return {'deleted_jobs': deljobs}

            id = web_request.get_str("id")
            if id not in self.history_ns.keys():
                raise self.server.error(f"Invalid job id: {id}", 404)

            self.delete_job(id)
            return {'deleted_jobs': [id]}

    async def _handle_jobs_list(self, web_request):
        i = 0
        end_num = len(self.history_ns)
        jobs = []
        start_num = 0

        before = web_request.get_float("before", -1)
        since = web_request.get_float("since", -1)
        limit = web_request.get_int("limit", 50)
        start = web_request.get_int("start", 0)
        if start >= end_num or end_num == 0:
            return {"count": 0, "jobs": {}}

        for id in self.history_ns.keys():
            job = self.history_ns[id]
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
            job['job_id'] = id
            jobs.append(job)
            i += 1

        return {"count": end_num - start_num, "jobs": jobs}

    async def _status_update(self, data):
        ps = data.get("print_stats", {})
        if "state" in ps:
            old_state = self.print_stats['state']
            new_state = ps['state']
            new_ps = dict(self.print_stats)
            new_ps.update(ps)

            if new_state is not old_state:
                if new_state == "printing" and self.current_job is None:
                    # always add new job if no existing job is present
                    self.add_job(PrinterJob(new_ps))
                elif self.current_job is not None:
                    if new_state == "complete":
                        self.finish_job("completed", new_ps)
                    if new_state == "standby":
                        self.finish_job("cancelled", self.print_stats)
                    elif new_state == "error":
                        self.finish_job("error", new_ps)
                    elif new_state == "printing" and \
                            self._check_need_cancel(new_ps):
                        # Finish with the previous state
                        self.finish_job("cancelled", self.print_stats)
                        self.add_job(PrinterJob(new_ps))

        self.print_stats.update(ps)

    def _handle_shutdown(self):
        self.finish_job("klippy_shutdown", self.print_stats)

    def _handle_disconnect(self):
        self.finish_job("klippy_disconnect", self.print_stats)

    def _check_need_cancel(self, new_stats):
        # Cancel if the file name has changed, total duration has
        # decreased, or if job is not resuming from a pause
        ps = self.print_stats
        return ps['filename'] != new_stats['filename'] or \
            ps['total_duration'] > new_stats['total_duration'] or \
            ps['state'] != "paused"

    def add_job(self, job):
        self.current_job_id = str(
            self.database.get_item("moonraker", JOBS_AUTO_INC_KEY))
        self.database.insert_item("moonraker", JOBS_AUTO_INC_KEY,
                                  int(self.current_job_id)+1)
        self.current_job = job
        self.grab_job_metadata()
        self.history_ns[self.current_job_id] = job.get_stats()
        self.send_history_event("added")

    def delete_job(self, id):
        id = str(id)

        if id in self.history_ns.keys():
            del self.history_ns[id]

        return

    def finish_job(self, status, pstats):
        if self.current_job is None:
            return

        self.current_job.finish(status, pstats)
        # Regrab metadata incase metadata wasn't parsed yet due to file upload
        self.grab_job_metadata()
        self.save_current_job()
        self.send_history_event("finished")
        self.current_job = None
        self.current_job_id = None

    def get_job(self, id):
        id = str(id)
        return self.history_ns.get(id, None)

    def grab_job_metadata(self):
        if self.current_job is None:
            return

        filename = self.current_job.get("filename")
        metadata = self.gcdb.get(filename, {})
        metadata.pop("thumbnails", None)
        self.current_job.set("metadata", metadata)

    def save_current_job(self):
        self.history_ns[self.current_job_id] = self.current_job.get_stats()

    def send_history_event(self, evt_action):
        job = dict(self.current_job.get_stats())
        job['job_id'] = self.current_job_id
        self.server.send_event("history:history_changed",
                               {'action': evt_action, 'job': job})

    def on_exit(self):
        self.finish_job("server_exit", self.print_stats)

class PrinterJob:
    def __init__(self, data={}):
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
