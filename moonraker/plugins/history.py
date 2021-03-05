# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, time
from tornado.ioloop import IOLoop

SAVE_INTERVAL = 5

HIST_NAMESPACE = "history"
METADATA_KEYS = ['estimated_time','modified','slicer','slicer_version']

class History:
    def __init__(self, config):
        self.server = config.get_server()
        self.database = self.server.lookup_plugin("database")
        self.gcdb = self.database.wrap_namespace("gcode_metadata",
            parse_keys=False)
        self.current_job = None
        self.file_metadata = None
        self.jobs = {}
        self.job_id = -1
        self.last_update_time = 0
        self.print_stats = {}

        self.server.register_event_handler(
            "server:klippy_ready", self._init_ready)
        self.server.register_event_handler(
            "server:status_update", self._status_update)


        self.server.register_endpoint(
            "/printer/history/list", ['GET'], self._handle_jobs_list)
        self.server.register_endpoint(
            "/printer/history/delete", ['DELETE'], self._handle_job_delete)

        self.database.register_local_namespace(HIST_NAMESPACE)
        try:
            self.job_id = self.database.get_item(HIST_NAMESPACE,
                "job_auto_inc_id")
        except:
            logging.info("Creating job history namespace in database")
            self.database.insert_item(HIST_NAMESPACE,"job_auto_inc_id", 0)
            self.job_id = 0
            self.database.insert_item(HIST_NAMESPACE,"prints", self.jobs)

        jobs = self.database.get_item(HIST_NAMESPACE,"prints")
        for job in jobs:
            self.jobs[job] = PrinterJob(jobs[job])

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

    async def _handle_job_delete(self, web_request):
        args = web_request.get_args()
        if "all" in args:
            deljobs = []
            jobs = list(self.jobs)
            for job in jobs:
                self.delete_job(job)
                deljobs.append(job)
            return deljobs
        if "id" not in args:
            raise self.server.error("No ID to delete")
        if args['id'] not in self.jobs:
            raise self.server.error(f"Invalid job id: {args['id']}")

        logging.debug("Jobs %s: %s" % (args['id'], self.jobs))
        return self.delete_job(args['id'])

    async def _handle_jobs_list(self, web_request):
        args = web_request.get_args()
        if "id" in args:
            if args['id'] not in self.jobs:
                raise self.server.error(f"Invalid job id: {args['id']}")
            return {args['id']: self.get_job(args['id']).get_stats()}

        before = None if 'before' not in args else int(args['before'])
        since = None if 'since' not in args else int(args['since'])
        limit = int(args['limit']) if "limit" in args else 50
        start = int(args['start']) if "start" in args else 0
        if start > (len(self.jobs)-1) or len(self.jobs) == 0:
            return {"count": len(self.jobs), "prints": {}}

        jobs = {}
        i = 0
        start_num = 0
        end_num = len(self.jobs)
        for id in list(self.jobs):
            if since != None and since > self.get_job(id).get('start_time'):
                start_num += 1
                continue
            if before != None and before < self.get_job(id).get('end_time'):
                end_num -= 1
                continue
            if limit != 0 and i >= limit:
                continue
            if start != 0:
                start -= 1
                continue
            jobs[id] = self.get_job(id).get_stats()
            i += 1

        return {"count": end_num - start_num, "prints": jobs}

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
                        self.add_job(PrinterJob(self.print_stats))
                    elif new_state == "complete" and self.current_job != None:
                        for s in list(ps):
                            self.print_stats[s] = ps[s]
                        self.finish_job("completed", self.print_stats)
                    elif new_state == "standby"  and self.current_job != None:
                        self.finish_job("cancelled", self.print_stats)

            for s in list(ps):
                self.print_stats[s] = ps[s]

            if time.time() > self.last_update_time + SAVE_INTERVAL:
                if self.current_job != None:
                    self.last_update_time = time.time()
                    self.jobs[self.current_job].update_from_ps(self.print_stats)
                    self.save_current_job()

    def add_job(self, job):
        job_id = self.job_id
        self.job_id += 1
        self.database.insert_item(HIST_NAMESPACE,"job_auto_inc_id", self.job_id)

        self.current_job = job_id
        self.jobs[job_id] = job
        self.grab_job_metadata()
        self.save_current_job()

    def delete_job(self, id):
        if id not in self.jobs:
            return False
        del self.jobs[id]
        self.database.delete_item(HIST_NAMESPACE, "jobs.%s" % id)
        return id

    def finish_job(self, status, updates):
        if self.current_job == None:
            return

        self.jobs[self.current_job].finish("completed", self.print_stats)
        self.save_current_job()
        self.current_job = None

    def get_job(self, id):
        if id not in self.jobs:
            return None
        return self.jobs.get(id)

    def grab_job_metadata(self):
        if self.current_job == None or self.current_job not in self.jobs:
            return

        gcdb = self.database.wrap_namespace("gcode_metadata",  parse_keys=False)
        logging.debug("gcdb: %s" % gcdb)
        filename = self.jobs[self.current_job].get("filename")
        if filename not in gcdb:
            return

        metadata = {}
        for i in METADATA_KEYS:
            if i in gcdb[filename]:
                self.jobs[self.current_job].update_file_metadata(
                    {i: gcdb[filename][i]})

    def save_current_job(self):
        self.database.insert_item(HIST_NAMESPACE, "prints.%s" %
            self.current_job, self.jobs[self.current_job].get_stats())

    def save_to_database(self):
        self.database.insert_item(HIST_NAMESPACE, "prints",
            json.loads(json.dumps(self.jobs,default=lambda o: o.__dict__)))

class PrinterJob:
    def __init__(self, data={}, file_metadata={}):
        self.end_time = None
        self.filament_used = 0
        self.filename = None
        self.print_duration = 0
        self.status = "in_progress"
        self.start_time = time.time()
        self.total_duration = 0
        self.file_metadata = file_metadata
        self.update_from_ps(data)
        self.update_file_metadata(file_metadata)

    def finish(self, status, print_stats={}):
        self.end_time = time.time()
        self.status = status
        self.update_from_ps(print_stats)

    def get(self, name):
        if not hasattr(self, name):
            return None
        return getattr(self, name)

    def get_stats(self):
        return {k: getattr(self, k) for k in self.__dict__}

    def get_metadata(self):
        return self.file_metadata

    def set(self, name, val):
        if not hasattr(self, name):
            return
        setattr(self, name, val)

    def update_from_ps(self, data):
        for i in data:
            if hasattr(self, i):
                setattr(self, i, data[i])

    def update_file_metadata(self, file_metadata={}):
        for i in file_metadata.keys():
            self.file_metadata[i] = file_metadata[i]

def load_plugin(config):
    return History(config)
