# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, time
from tornado.ioloop import IOLoop

class History:
    def __init__(self, config):
        self.server = config.get_server()
        self.db = self.server.lookup_plugin('database')
        self.current_job = None
        self.file_metadata = None
        self.jobs = {}
        self.job_id = 0
        self.print_stats = {}

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)


        self.server.register_endpoint(
            "/printer/jobs/list", ['GET'], self._handle_jobs_list)

        try:
            jobs = self.db.get("history")
            if jobs != None:
                id = max([int(id) for id in list(jobs)])
                for job in list(jobs):
                    self.jobs[job] = PrinterJob(jobs[job])
                    self.job_id = id + 1
        except:
            logging.info("Error reading history from database. Starting "
                "empty database")

    async def _init_ready(self):
        klippy_apis = self.server.lookup_plugin('klippy_apis')
        self.print_stats = {}

        try:
            result = await klippy_apis.query_objects({'print_stats': None})
        except self.server.error as e:
            logging.info(f"Error getting print_stats: {e}")
        self.print_stats = result.get("print_stats", {})

        sub = {"print_stats": None}
        try:
            status = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")

    async def _handle_jobs_list(self, data):
        jobs = []
        for id in list(self.jobs):
            jobs.append(self.get_job(id).get_stats())
        return jobs

    async def _status_update(self, data):
        if "print_stats" in data:
            ps = data['print_stats']

            if "filename" in ps:
                file_store = self.server.lookup_plugin('file_manager')
                if ps['filename'] != "":
                    self.file_metadata = file_store.get_file_metadata(
                        ps['filename'])
                else:
                    self.file_metadata = None

            if "state" in ps:
                old_state = self.print_stats['state']
                new_state = ps['state']

                if new_state is not old_state:
                    if new_state == "printing" and old_state != "paused":
                        for s in list(ps):
                            self.print_stats[s] = ps[s]
                        self.current_job = PrinterJob(self.print_stats)
                        self.add_job(self.current_job)
                    elif new_state == "complete":
                        for s in list(ps):
                            self.print_stats[s] = ps[s]

                        self.current_job.finish("completed", self.print_stats)
                        self.current_job = None

                        self.save_to_database()

                    elif new_state == "standby":
                        self.current_job.finish("cancelled", self.print_stats)

            for s in list(ps):
                self.print_stats[s] = ps[s]

    def add_job(self, job):
        job_id = self.job_id
        self.job_id += 1

        job.set("id", job_id)
        self.jobs[job_id] = job

    def save_to_database(self):
        self.db.set("history", json.loads(json.dumps(self.jobs,
            default=lambda o: o.__dict__)))
        self.db.request_save()

    def get_job(self, id):
        if id not in self.jobs:
            return None
        return self.jobs.get(id)

class PrinterJob:
    def __init__(self, data={}, file_metadata=None):
        self.id = 0
        self.end_time = None
        self.filament_used = 0
        self.filename = None
        self.print_duration = 0
        self.status = "in_progress"
        self.start_time = time.time()
        self.total_duration = 0
        self.file_metadata = file_metadata
        self._update_data(data)
        self._update_file_metadata(file_metadata)
    def finish(self, status, print_stats):
        self.end_time = time.time()
        self.status = status
        self._update_data(print_stats)
    def get(self, name):
        if not hasattr(self, name):
            return None
        return getattr(self, name)
    def get_stats(self):
        return {k: getattr(self, k) for k in self.__dict__
            if k != "file_metadata"}
    def get_metadata(self):
        return self.file_metadata
    def set(self, name, val):
        if not hasattr(self, name):
            return
        setattr(self, name, val)
    def _update_file_metadata(self, file_metadata):
        self.file_metadata = file_metadata
    def _update_data(self, data):
        for i in data:
            if hasattr(self, i):
                setattr(self, i, data[i])

def load_plugin(config):
    return History(config)
