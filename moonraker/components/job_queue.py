# Print Job Queue Implementation
#
# Copyright (C) 2021 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import asyncio
import time
import logging
from ..common import JobEvent, RequestType

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
    from ..common import WebRequest, UserInfo
    from .klippy_apis import KlippyAPI
    from .file_manager.file_manager import FileManager
    from .job_state import JobState

class JobQueue:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.queued_jobs: Dict[str, QueuedJob] = {}
        self.lock = asyncio.Lock()
        self.pause_requested: bool = False
        self.load_on_start = config.getboolean("load_on_startup", False)
        self.automatic = config.getboolean("automatic_transition", False)
        self.queue_state: str = "paused"
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
            "job_state:state_changed", self._on_job_state_changed
        )

        self.server.register_notification("job_queue:job_queue_changed")
        self.server.register_remote_method("pause_job_queue", self.pause_queue)
        self.server.register_remote_method("start_job_queue", self.start_queue)

        self.server.register_endpoint(
            "/server/job_queue/job", RequestType.POST | RequestType.DELETE,
            self._handle_job_request
        )
        self.server.register_endpoint(
            "/server/job_queue/pause", RequestType.POST, self._handle_pause_queue
        )
        self.server.register_endpoint(
            "/server/job_queue/start", RequestType.POST, self._handle_start_queue
        )
        self.server.register_endpoint(
            "/server/job_queue/status", RequestType.GET, self._handle_queue_status
        )
        self.server.register_endpoint(
            "/server/job_queue/jump", RequestType.POST, self._handle_jump
        )

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
        has_requested_pause = self.pause_requested
        await self.pause_queue()
        self.pause_requested = has_requested_pause

    async def _on_job_state_changed(self, job_event: JobEvent, *args) -> None:
        if job_event == JobEvent.COMPLETE:
            await self._on_job_complete()
        elif job_event.aborted:
            await self._on_job_abort()

    async def _on_job_complete(self) -> None:
        if not self.automatic:
            return
        async with self.lock:
            # Transition to the next job in the queue
            if self.queue_state == "ready" and self.queued_jobs:
                event_loop = self.server.get_event_loop()
                self._set_queue_state("loading")
                self.pop_queue_handle = event_loop.delay_callback(
                    self.job_delay, self._pop_job)

    async def _on_job_abort(self) -> None:
        async with self.lock:
            if self.queued_jobs:
                self._set_queue_state("paused")

    async def _pop_job(self, need_transition: bool = True) -> None:
        self.pop_queue_handle = None
        async with self.lock:
            if self.queue_state == "paused":
                return
            if not self.queued_jobs:
                self._set_queue_state("paused")
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
                        self._set_queue_state("paused")
                        raise self.server.error(
                            "Queue State Changed during Transition Gcode")
                self._set_queue_state("starting")
                await kapis.start_print(
                    filename, wait_klippy_started=True, user=job.user
                )
            except self.server.error:
                logging.exception(f"Error Loading print: {filename}")
                self._set_queue_state("paused")
            else:
                self.queued_jobs.pop(uid, None)
                if self.queue_state == "starting":
                    # Set the queue to ready if items are in the queue
                    # and auto transition is enabled
                    set_ready = len(self.queued_jobs) > 0 and self.automatic
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
                        reset: bool = False,
                        user: Optional[UserInfo] = None
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
                queued_job = QueuedJob(fname, user)
                self.queued_jobs[queued_job.job_id] = queued_job
            self._send_queue_event(action="jobs_added")
            if self.automatic and not self.pause_requested:
                jstate: JobState = self.server.lookup_component("job_state")
                last_evt = jstate.get_last_job_event()
                if last_evt.is_printing or last_evt == JobEvent.PAUSED:
                    self._set_queue_state("ready")

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
        # Acquire the lock to wait for any pending operations to complete
        async with self.lock:
            self.pause_requested = True

    async def start_queue(self) -> None:
        async with self.lock:
            self.pause_requested = False
            if self.queue_state in ("ready", "paused"):
                if not self.queued_jobs:
                    self._set_queue_state("paused")
                elif await self._check_can_print():
                    self._set_queue_state("loading")
                    event_loop = self.server.get_event_loop()
                    self.pop_queue_handle = event_loop.delay_callback(
                        0.01, self._pop_job, False
                    )
                else:
                    self._set_queue_state("ready" if self.automatic else "paused")

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

    async def _handle_job_request(
        self, web_request: WebRequest
    ) -> Dict[str, Any]:
        req_type = web_request.get_request_type()
        if req_type == RequestType.POST:
            files = web_request.get_list('filenames')
            reset = web_request.get_boolean("reset", False)
            # Validate that all files exist before queueing
            user = web_request.get_current_user()
            await self.queue_job(files, reset=reset, user=user)
        elif req_type == RequestType.DELETE:
            if web_request.get_boolean("all", False):
                await self.delete_job([], all=True)
            else:
                job_ids = web_request.get_list('job_ids')
                await self.delete_job(job_ids)
        else:
            raise self.server.error(f"Invalid request type: {req_type}")
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
    def __init__(self, filename: str, user: Optional[UserInfo] = None) -> None:
        self.filename = filename
        self.job_id = f"{id(self):016X}"
        self.time_added = time.time()
        self._user = user

    def __str__(self) -> str:
        return self.filename

    @property
    def user(self) -> Optional[UserInfo]:
        return self._user

    def as_dict(self, cur_time: float) -> Dict[str, Any]:
        return {
            'filename': self.filename,
            'job_id': self.job_id,
            'time_added': self.time_added,
            'time_in_queue': cur_time - self.time_added
        }

def load_component(config: ConfigHelper) -> JobQueue:
    return JobQueue(config)
