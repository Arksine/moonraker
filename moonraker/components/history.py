# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import logging
import time

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Optional,
    Dict,
    List,
)
if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from . import database
    from . import klippy_apis
    from . import file_manager
    DBComp = database.MoonrakerDatabase
    APIComp = klippy_apis.KlippyAPI
    FMComp = file_manager.FileManager

HIST_NAMESPACE = "history"
MAX_JOBS = 10000

class History:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.file_manager: FMComp = self.server.lookup_component(
            'file_manager')
        database: DBComp = self.server.lookup_component("database")
        self.gcdb = database.wrap_namespace("gcode_metadata", parse_keys=False)
        self.job_totals: Dict[str, float] = database.get_item(
            "moonraker", "history.job_totals",
            {
                'total_jobs': 0,
                'total_time': 0.,
                'total_print_time': 0.,
                'total_filament_used': 0.,
                'longest_job': 0.,
                'longest_print': 0.
            })

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
        self.server.register_endpoint(
            "/server/history/totals", ['GET'], self._handle_job_totals)

        database.register_local_namespace(HIST_NAMESPACE)
        self.history_ns = database.wrap_namespace(HIST_NAMESPACE,
                                                  parse_keys=False)

        self.current_job: Optional[PrinterJob] = None
        self.current_job_id: Optional[str] = None
        self.print_stats: Dict[str, Any] = {}
        self.next_job_id: int = 0
        self.cached_job_ids = self.history_ns.keys()
        if self.cached_job_ids:
            self.next_job_id = int(self.cached_job_ids[-1], 16) + 1

    async def _init_ready(self) -> None:
        klippy_apis: APIComp = self.server.lookup_component('klippy_apis')
        sub: Dict[str, Optional[List[str]]] = {"print_stats": None}
        try:
            result = await klippy_apis.subscribe_objects(sub)
        except self.server.error as e:
            logging.info(f"Error subscribing to print_stats")
        self.print_stats = result.get("print_stats", {})

    async def _handle_job_request(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        action = web_request.get_action()
        if action == "GET":
            job_id = web_request.get_str("uid")
            if job_id not in self.cached_job_ids:
                raise self.server.error(f"Invalid job uid: {job_id}", 404)
            job = self.history_ns[job_id]
            return {"job": self._prep_requested_job(job, job_id)}
        if action == "DELETE":
            all = web_request.get_boolean("all", False)
            if all:
                deljobs = self.cached_job_ids
                self.history_ns.clear()
                self.cached_job_ids = []
                self.next_job_id = 0
                return {'deleted_jobs': deljobs}

            job_id = web_request.get_str("uid")
            if job_id not in self.cached_job_ids:
                raise self.server.error(f"Invalid job uid: {job_id}", 404)

            self.delete_job(job_id)
            return {'deleted_jobs': [job_id]}
        raise self.server.error("Invalid Request Method")

    async def _handle_jobs_list(self,
                                web_request: WebRequest
                                ) -> Dict[str, Any]:
        i = 0
        count = 0
        end_num = len(self.cached_job_ids)
        jobs: List[Dict[str, Any]] = []
        start_num = 0

        before = web_request.get_float("before", -1)
        since = web_request.get_float("since", -1)
        limit = web_request.get_int("limit", 50)
        start = web_request.get_int("start", 0)
        order = web_request.get_str("order", "desc")

        if order not in ["asc", "desc"]:
            raise self.server.error(f"Invalid `order` value: {order}", 400)

        reverse_order = (order == "desc")

        # cached jobs is asc order, find lower and upper boundary
        if since != -1:
            while start_num < end_num:
                job_id = self.cached_job_ids[start_num]
                job: Dict[str, Any] = self.history_ns[job_id]
                if job['start_time'] > since:
                    break
                start_num += 1

        if before != -1:
            while end_num > 0:
                job_id = self.cached_job_ids[end_num-1]
                job = self.history_ns[job_id]
                if job['end_time'] < before:
                    break
                end_num -= 1

        if start_num >= end_num or end_num == 0:
            return {"count": 0, "jobs": []}

        i = start
        count = end_num - start_num

        if limit == 0:
            limit = MAX_JOBS

        while i < count and len(jobs) < limit:
            if reverse_order:
                job_id = self.cached_job_ids[end_num - i - 1]
            else:
                job_id = self.cached_job_ids[start_num + i]
            job = self.history_ns[job_id]
            jobs.append(self._prep_requested_job(job, job_id))
            i += 1

        return {"count": count, "jobs": jobs}

    async def _handle_job_totals(self,
                                 web_request: WebRequest
                                 ) -> Dict[str, Dict[str, float]]:
        return {'job_totals': self.job_totals}

    async def _status_update(self, data: Dict[str, Any]) -> None:
        ps = data.get("print_stats", {})
        if "state" in ps:
            old_state: str = self.print_stats['state']
            new_state: str = ps['state']
            new_ps = dict(self.print_stats)
            new_ps.update(ps)

            if new_state is not old_state:
                if new_state == "printing" and self.current_job is None:
                    # always add new job if no existing job is present
                    self.add_job(PrinterJob(new_ps))
                elif self.current_job is not None:
                    if new_state == "complete":
                        self.finish_job("completed", new_ps)
                    elif new_state == "cancelled":
                        self.finish_job("cancelled", new_ps)
                    elif new_state == "standby":
                        # Backward compatibility with
                        # `CLEAR_PAUSE/SDCARD_RESET_FILE` workflow
                        self.finish_job("cancelled", self.print_stats)
                    elif new_state == "error":
                        self.finish_job("error", new_ps)
                    elif new_state == "printing" and \
                            self._check_need_cancel(new_ps):
                        # Finish with the previous state
                        self.finish_job("cancelled", self.print_stats)
                        self.add_job(PrinterJob(new_ps))

        self.print_stats.update(ps)

    def _handle_shutdown(self) -> None:
        self.finish_job("klippy_shutdown", self.print_stats)

    def _handle_disconnect(self) -> None:
        self.finish_job("klippy_disconnect", self.print_stats)

    def _check_need_cancel(self, new_stats: Dict[str, Any]) -> bool:
        # Cancel if the file name has changed, total duration has
        # decreased, or if job is not resuming from a pause
        ps = self.print_stats
        return ps['filename'] != new_stats['filename'] or \
            ps['total_duration'] > new_stats['total_duration'] or \
            ps['state'] != "paused"

    def add_job(self, job: PrinterJob) -> None:
        if len(self.cached_job_ids) >= MAX_JOBS:
            self.delete_job(self.cached_job_ids[0])
        job_id = f"{self.next_job_id:06X}"
        self.current_job = job
        self.current_job_id = job_id
        self.grab_job_metadata()
        self.history_ns[job_id] = job.get_stats()
        self.cached_job_ids.append(job_id)
        self.next_job_id += 1
        self.send_history_event("added")

    def delete_job(self, job_id: Union[int, str]) -> None:
        if isinstance(job_id, int):
            job_id = f"{job_id:06X}"

        if job_id in self.cached_job_ids:
            del self.history_ns[job_id]
            self.cached_job_ids.remove(job_id)

    def finish_job(self, status: str, pstats: Dict[str, Any]) -> None:
        if self.current_job is None:
            return

        self.current_job.finish(status, pstats)
        # Regrab metadata incase metadata wasn't parsed yet due to file upload
        self.grab_job_metadata()
        self.save_current_job()
        self._update_job_totals()
        self.send_history_event("finished")
        self.current_job = None
        self.current_job_id = None

    def get_job(self, job_id: Union[int, str]) -> Optional[Dict[str, Any]]:
        if isinstance(job_id, int):
            job_id = f"{job_id:06X}"
        return self.history_ns.get(job_id, None)

    def grab_job_metadata(self) -> None:
        if self.current_job is None:
            return
        filename: str = self.current_job.get("filename")
        metadata: Dict[str, Any] = self.gcdb.get(filename, {})
        if metadata:
            # Add the start time and job id to the
            # persistent metadata storage
            metadata.update({
                'print_start_time': self.current_job.get('start_time'),
                'job_id': self.current_job_id
            })
            self.gcdb[filename] = metadata
        # We don't need to store these fields in the
        # job metadata, as they are redundant
        metadata.pop('print_start_time', None)
        metadata.pop('job_id', None)
        if "thumbnails" in metadata:
            thumb: Dict[str, Any]
            for thumb in metadata['thumbnails']:
                thumb.pop('data', None)
        self.current_job.set("metadata", metadata)

    def save_current_job(self) -> None:
        if self.current_job is None or self.current_job_id is None:
            return
        self.history_ns[self.current_job_id] = self.current_job.get_stats()

    def _update_job_totals(self) -> None:
        if self.current_job is None:
            return
        job = self.current_job
        self.job_totals['total_jobs'] += 1
        self.job_totals['total_time'] += job.get('total_duration')
        self.job_totals['total_print_time'] += job.get('print_duration')
        self.job_totals['total_filament_used'] += job.get('filament_used')
        self.job_totals['longest_job'] = max(
            self.job_totals['longest_job'], job.get('total_duration'))
        self.job_totals['longest_print'] = max(
            self.job_totals['longest_print'], job.get('print_duration'))
        database: DBComp = self.server.lookup_component("database")
        database.insert_item(
            "moonraker", "history.job_totals", self.job_totals)

    def send_history_event(self, evt_action: str) -> None:
        if self.current_job is None or self.current_job_id is None:
            return
        job = self._prep_requested_job(
            self.current_job.get_stats(), self.current_job_id)
        self.server.send_event("history:history_changed",
                               {'action': evt_action, 'job': job})

    def _prep_requested_job(self,
                            job: Dict[str, Any],
                            job_id: str
                            ) -> Dict[str, Any]:
        job['job_id'] = job_id
        job['exists'] = self.file_manager.check_file_exists(
            "gcodes", job['filename'])
        return job

    def on_exit(self) -> None:
        self.finish_job("server_exit", self.print_stats)

class PrinterJob:
    def __init__(self, data: Dict[str, Any] = {}) -> None:
        self.end_time: Optional[float] = None
        self.filament_used: float = 0
        self.filename: Optional[str] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self.print_duration: float = 0.
        self.status: str = "in_progress"
        self.start_time = time.time()
        self.total_duration: float = 0.
        self.update_from_ps(data)

    def finish(self,
               status: str,
               print_stats: Dict[str, Any] = {}
               ) -> None:
        self.end_time = time.time()
        self.status = status
        self.update_from_ps(print_stats)

    def get(self, name: str) -> Any:
        if not hasattr(self, name):
            return None
        return getattr(self, name)

    def get_stats(self) -> Dict[str, Any]:
        return self.__dict__.copy()

    def set(self, name: str, val: Any) -> None:
        if not hasattr(self, name):
            return
        setattr(self, name, val)

    def update_from_ps(self, data: Dict[str, Any]) -> None:
        for i in data:
            if hasattr(self, i):
                setattr(self, i, data[i])

def load_component(config: ConfigHelper) -> History:
    return History(config)
