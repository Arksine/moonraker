# Print Job Queue Implementation
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import time
import logging

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Optional,
    Dict,
    List,
    Union,
)
if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest
    from .klippy_apis import KlippyAPI
    from .file_manager.file_manager import FileManager

class JobQueue:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.queued_jobs: Dict[str, QueuedJob] = {}
        self.lock = asyncio.Lock()
        self.load_on_start = config.getboolean("load_on_startup", False)
        self.automatic = config.getboolean("automatic_transition", False)
        self.queue_state: str = "ready" if self.automatic else "paused"
        self.job_delay = config.getfloat("job_transition_delay", 0.01)
        if self.job_delay <= 0.:
            raise config.error(
                "Value for option 'job_transition_delay' in section [job_queue]"
                " must be above 0.0")
        self.job_transition_gcode = config.get(
            "job_transition_gcode", "").strip()
        self.pop_queue_handle: Optional[asyncio.TimerHandle] = None

        self.server.register_event_handler(
            "server:klippy_ready", self._handle_ready)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_event_handler(
            "job_state:complete", self._on_job_complete)
        self.server.register_event_handler(
            "job_state:error", self._on_job_abort)
        self.server.register_event_handler(
            "job_state:cancelled", self._on_job_abort)

        self.server.register_notification("job_queue:job_queue_changed")
        self.server.register_remote_method("pause_job_queue", self.pause_queue)
        self.server.register_remote_method("start_job_queue",
                                           self.start_queue)

        self.server.register_endpoint(
            "/server/job_queue/job", ['POST', 'DELETE'],
            self._handle_job_request)
        self.server.register_endpoint(
            "/server/job_queue/pause", ['POST'], self._handle_pause_queue)
        self.server.register_endpoint(
            "/server/job_queue/start", ['POST'], self._handle_start_queue)
        self.server.register_endpoint(
            "/server/job_queue/status", ['GET'], self._handle_queue_status)
        self.server.register_endpoint(
            "/server/job_queue/jump", ['POST'], self._handle_jump)

    async def _handle_ready(self) -> None:
        async with self.lock:
            if not self.load_on_start or not self.queued_jobs:
                return
            # start a queued print
            if self.queue_state in ['ready', 'paused']:
                event_loop = self.server.get_event_loop()
                self._set_queue_state("loading")
                self.pop_queue_handle = event_loop.delay_callback(
                    1., self._pop_job, False)

    async def _handle_shutdown(self) -> None:
        await self.pause_queue()
        if not self.queued_jobs and self.automatic:
            self._set_queue_state("ready")

    async def _on_job_complete(self,
                               prev_stats: Dict[str, Any],
                               new_stats: Dict[str, Any]
                               ) -> None:
        if not self.automatic:
            return
        async with self.lock:
            # Transition to the next job in the queue
            if self.queue_state == "ready" and self.queued_jobs:
                event_loop = self.server.get_event_loop()
                self._set_queue_state("loading")
                self.pop_queue_handle = event_loop.delay_callback(
                    self.job_delay, self._pop_job)

    async def _on_job_abort(self,
                            prev_stats: Dict[str, Any],
                            new_stats: Dict[str, Any]
                            ) -> None:
        async with self.lock:
            if self.queued_jobs:
                self._set_queue_state("paused")

    async def _pop_job(self, need_transition: bool = True) -> None:
        self.pop_queue_handle = None
        async with self.lock:
            if self.queue_state == "paused":
                return
            if not self.queued_jobs:
                qs = "ready" if self.automatic else "paused"
                self._set_queue_state(qs)
                return
            kapis: KlippyAPI = self.server.lookup_component('klippy_apis')
            uid, job = list(self.queued_jobs.items())[0]
            filename = str(job)
            can_print = await self._check_can_print()
            if not can_print or self.queue_state != "loading":
                self._set_queue_state("paused")
                return
            try:
                if self.job_transition_gcode and need_transition:
                    await kapis.run_gcode(self.job_transition_gcode)
                    # Check to see if the queue was paused while running
                    # the job transition gcode
                    if self.queue_state != "loading":
                        raise self.server.error(
                            "Queue State Changed during Transition Gcode")
                self._set_queue_state("starting")
                await kapis.start_print(filename, wait_klippy_started=True)
            except self.server.error:
                logging.exception(f"Error Loading print: {filename}")
                self._set_queue_state("paused")
            else:
                self.queued_jobs.pop(uid, None)
                if self.queue_state == "starting":
                    # If the queue was not paused while starting the print,
                    set_ready = not self.queued_jobs or self.automatic
                    self.queue_state = "ready" if set_ready else "paused"
                self._send_queue_event(action="job_loaded")

    async def _check_can_print(self) -> bool:
        # Query the latest stats
        kapis: KlippyAPI = self.server.lookup_component('klippy_apis')
        try:
            result = await kapis.query_objects({"print_stats": None})
        except Exception:
            # Klippy not connected
            return False
        if 'print_stats' not in result:
            return False
        state: str = result['print_stats']['state']
        if state in ["printing", "paused"]:
            return False
        return True

    async def queue_job(self,
                        filenames: Union[str, List[str]],
                        check_exists: bool = True,
                        reset: bool = False
                        ) -> None:
        async with self.lock:
            # Make sure that the file exists
            if isinstance(filenames, str):
                filenames = [filenames]
            if check_exists:
                # Make sure all files exist before adding them to the queue
                for fname in filenames:
                    self._check_job_file(fname)
            if reset:
                self.queued_jobs.clear()
            for fname in filenames:
                queued_job = QueuedJob(fname)
                self.queued_jobs[queued_job.job_id] = queued_job
            self._send_queue_event(action="jobs_added")

    async def delete_job(self,
                         job_ids: Union[str, List[str]],
                         all: bool = False
                         ) -> None:
        async with self.lock:
            if not self.queued_jobs:
                # No jobs in queue, nothing to delete
                return
            if all:
                self.queued_jobs.clear()
            elif job_ids:
                if isinstance(job_ids, str):
                    job_ids = [job_ids]
                for uid in job_ids:
                    self.queued_jobs.pop(uid, None)
            else:
                # Don't notify, nothing was deleted
                return
            self._send_queue_event(action="jobs_removed")

    async def pause_queue(self) -> None:
        self._set_queue_state("paused")
        if self.pop_queue_handle is not None:
            self.pop_queue_handle.cancel()
            self.pop_queue_handle = None
        # Acquire the lock to wait for any pending operations to
        # complete
        await self.lock.acquire()
        self.lock.release()

    async def start_queue(self) -> None:
        async with self.lock:
            if self.queue_state != "loading":
                if self.queued_jobs and await self._check_can_print():
                    self._set_queue_state("loading")
                    event_loop = self.server.get_event_loop()
                    self.pop_queue_handle = event_loop.delay_callback(
                        0.01, self._pop_job, False
                    )
                else:
                    qs = "ready" if self.automatic else "paused"
                    self._set_queue_state(qs)
    def _job_map_to_list(self) -> List[Dict[str, Any]]:
        cur_time = time.time()
        return [job.as_dict(cur_time) for
                job in self.queued_jobs.values()]

    def _check_job_file(self, job_name: str) -> None:
        fm: FileManager = self.server.lookup_component('file_manager')
        if not fm.check_file_exists("gcodes", job_name):
            raise self.server.error(
                f"G-Code File {job_name} does not exist")

    def _set_queue_state(self, new_state: str) -> None:
        if new_state != self.queue_state:
            self.queue_state = new_state
            self._send_queue_event()

    def _send_queue_event(self, action: str = "state_changed"):
        updated_queue: Optional[List[Dict[str, Any]]] = None
        if action != "state_changed":
            updated_queue = self._job_map_to_list()
        event_loop = self.server.get_event_loop()
        event_loop.delay_callback(
            .05, self.server.send_event, "job_queue:job_queue_changed",
            {
                'action': action,
                'updated_queue': updated_queue,
                'queue_state': self.queue_state
            })

    async def _handle_job_request(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        action = web_request.get_action()
        if action == "POST":
            files = web_request.get_list('filenames')
            reset = web_request.get_boolean("reset", False)
            # Validate that all files exist before queueing
            await self.queue_job(files, reset=reset)
        elif action == "DELETE":
            if web_request.get_boolean("all", False):
                await self.delete_job([], all=True)
            else:
                job_ids = web_request.get_list('job_ids')
                await self.delete_job(job_ids)
        else:
            raise self.server.error(f"Invalid action: {action}")
        return {
            'queued_jobs': self._job_map_to_list(),
            'queue_state': self.queue_state
        }

    async def _handle_pause_queue(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        await self.pause_queue()
        return {
            'queued_jobs': self._job_map_to_list(),
            'queue_state': self.queue_state
        }

    async def _handle_start_queue(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        await self.start_queue()
        return {
            'queued_jobs': self._job_map_to_list(),
            'queue_state': self.queue_state
        }

    async def _handle_queue_status(self,
                                   web_request: WebRequest
                                   ) -> Dict[str, Any]:
        return {
            'queued_jobs': self._job_map_to_list(),
            'queue_state': self.queue_state
        }

    async def _handle_jump(self, web_request: WebRequest) -> Dict[str, Any]:
        job_id: str = web_request.get("job_id")
        async with self.lock:
            job = self.queued_jobs.pop(job_id, None)
            if job is None:
                raise self.server.error(f"Invalid job id: {job_id}")
            new_queue = {job_id: job}
            new_queue.update(self.queued_jobs)
            self.queued_jobs = new_queue
        return {
            'queued_jobs': self._job_map_to_list(),
            'queue_state': self.queue_state
        }

    async def close(self):
        await self.pause_queue()

class QueuedJob:
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.job_id = f"{id(self):016X}"
        self.time_added = time.time()

    def __str__(self) -> str:
        return self.filename

    def as_dict(self, cur_time: float) -> Dict[str, Any]:
        return {
            'filename': self.filename,
            'job_id': self.job_id,
            'time_added': self.time_added,
            'time_in_queue': cur_time - self.time_added
        }

def load_component(config: ConfigHelper) -> JobQueue:
    return JobQueue(config)
