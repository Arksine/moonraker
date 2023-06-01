# History cache for printer jobs
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import time
import logging
from asyncio import Lock

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
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .database import MoonrakerDatabase as DBComp
    from .job_state import JobState
    from .file_manager.file_manager import FileManager

HIST_NAMESPACE = "history"
MAX_JOBS = 10000

class History:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.file_manager: FileManager = self.server.lookup_component(
            'file_manager')
        self.request_lock = Lock()
        database: DBComp = self.server.lookup_component("database")
        self.job_totals: Dict[str, float] = database.get_item(
            "moonraker", "history.job_totals",
            {
                'total_jobs': 0,
                'total_time': 0.,
                'total_print_time': 0.,
                'total_filament_used': 0.,
                'longest_job': 0.,
                'longest_print': 0.
            }).result()

        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_event_handler(
            "job_state:started", self._on_job_started)
        self.server.register_event_handler(
            "job_state:complete", self._on_job_complete)
        self.server.register_event_handler(
            "job_state:cancelled", self._on_job_cancelled)
        self.server.register_event_handler(
            "job_state:standby", self._on_job_standby)
        self.server.register_event_handler(
            "job_state:error", self._on_job_error)
        self.server.register_notification("history:history_changed")

        self.server.register_endpoint(
            "/server/history/job", ['GET', 'DELETE'], self._handle_job_request)
        self.server.register_endpoint(
            "/server/history/list", ['GET'], self._handle_jobs_list)
        self.server.register_endpoint(
            "/server/history/totals", ['GET'], self._handle_job_totals)
        self.server.register_endpoint(
            "/server/history/reset_totals", ['POST'],
            self._handle_job_total_reset)

        database.register_local_namespace(HIST_NAMESPACE)
        self.history_ns = database.wrap_namespace(HIST_NAMESPACE,
                                                  parse_keys=False)

        self.current_job: Optional[PrinterJob] = None
        self.current_job_id: Optional[str] = None
        self.next_job_id: int = 0
        self.cached_job_ids = self.history_ns.keys().result()
        if self.cached_job_ids:
            self.next_job_id = int(self.cached_job_ids[-1], 16) + 1

    async def _handle_job_request(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        async with self.request_lock:
            action = web_request.get_action()
            if action == "GET":
                job_id = web_request.get_str("uid")
                if job_id not in self.cached_job_ids:
                    raise self.server.error(f"Invalid job uid: {job_id}", 404)
                job = await self.history_ns[job_id]
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
        async with self.request_lock:
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
                    job: Dict[str, Any] = await self.history_ns[job_id]
                    if job['start_time'] > since:
                        break
                    start_num += 1

            if before != -1:
                while end_num > 0:
                    job_id = self.cached_job_ids[end_num-1]
                    job = await self.history_ns[job_id]
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
                job = await self.history_ns[job_id]
                jobs.append(self._prep_requested_job(job, job_id))
                i += 1

            return {"count": count, "jobs": jobs}

    async def _handle_job_totals(self,
                                 web_request: WebRequest
                                 ) -> Dict[str, Dict[str, float]]:
        return {'job_totals': self.job_totals}

    async def _handle_job_total_reset(self,
                                      web_request: WebRequest,
                                      ) -> Dict[str, Dict[str, float]]:
        if self.current_job is not None:
            raise self.server.error(
                "Job in progress, cannot reset totals")
        last_totals = dict(self.job_totals)
        self.job_totals = {
            'total_jobs': 0,
            'total_time': 0.,
            'total_print_time': 0.,
            'total_filament_used': 0.,
            'longest_job': 0.,
            'longest_print': 0.
        }
        database: DBComp = self.server.lookup_component("database")
        await database.insert_item(
            "moonraker", "history.job_totals", self.job_totals)
        return {'last_totals': last_totals}

    def _on_job_started(self,
                        prev_stats: Dict[str, Any],
                        new_stats: Dict[str, Any]
                        ) -> None:
        if self.current_job is not None:
            # Finish with the previous state
            self.finish_job("cancelled", prev_stats)
        self.add_job(PrinterJob(new_stats))

    def _on_job_complete(self,
                         prev_stats: Dict[str, Any],
                         new_stats: Dict[str, Any]
                         ) -> None:
        self.finish_job("completed", new_stats)

    def _on_job_cancelled(self,
                          prev_stats: Dict[str, Any],
                          new_stats: Dict[str, Any]
                          ) -> None:
        self.finish_job("cancelled", new_stats)

    def _on_job_error(self,
                      prev_stats: Dict[str, Any],
                      new_stats: Dict[str, Any]
                      ) -> None:
        self.finish_job("error", new_stats)

    def _on_job_standby(self,
                        prev_stats: Dict[str, Any],
                        new_stats: Dict[str, Any]
                        ) -> None:
        # Backward compatibility with
        # `CLEAR_PAUSE/SDCARD_RESET_FILE` workflow
        self.finish_job("cancelled", prev_stats)

    def _handle_shutdown(self) -> None:
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        self.finish_job("klippy_shutdown", last_ps)

    def _handle_disconnect(self) -> None:
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        self.finish_job("klippy_disconnect", last_ps)

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
        logging.debug(
            f"History Job Added - Id: {job_id}, File: {job.filename}"
        )
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
        cj = self.current_job
        if (
            pstats.get('filename') != cj.get('filename') or
            pstats.get('total_duration', 0.) < cj.get('total_duration')
        ):
            # Print stats have been reset, do not update this job with them
            pstats = {}

        self.current_job.finish(status, pstats)
        # Regrab metadata incase metadata wasn't parsed yet due to file upload
        self.grab_job_metadata()
        self.save_current_job()
        self._update_job_totals()
        logging.debug(
            f"History Job Finished - Id: {self.current_job_id}, "
            f"File: {self.current_job.filename}, "
            f"Status: {status}"
        )
        self.send_history_event("finished")
        self.current_job = None
        self.current_job_id = None

    async def get_job(self,
                      job_id: Union[int, str]
                      ) -> Optional[Dict[str, Any]]:
        if isinstance(job_id, int):
            job_id = f"{job_id:06X}"
        return await self.history_ns.get(job_id, None)

    def grab_job_metadata(self) -> None:
        if self.current_job is None:
            return
        filename: str = self.current_job.get("filename")
        mdst = self.file_manager.get_metadata_storage()
        metadata: Dict[str, Any] = mdst.get(filename, {})
        if metadata:
            # Add the start time and job id to the
            # persistent metadata storage
            metadata.update({
                'print_start_time': self.current_job.get('start_time'),
                'job_id': self.current_job_id
            })
            mdst.insert(filename, metadata.copy())
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
        mtime = job.get("metadata", {}).get("modified", None)
        job['job_id'] = job_id
        job['exists'] = self.file_manager.check_file_exists(
            "gcodes", job['filename'], mtime
        )
        return job

    def on_exit(self) -> None:
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        self.finish_job("server_exit", last_ps)

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
