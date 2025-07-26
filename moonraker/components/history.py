# History cache for printer jobs
#
# Copyright (C) 2024 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import time
import logging
from asyncio import Lock
from ..common import (
    JobEvent,
    RequestType,
    HistoryFieldData,
    FieldTracker,
    SqlTableDefinition
)

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Union,
    Optional,
    Dict,
    List,
    Tuple
)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from ..common import WebRequest, UserInfo
    from .database import MoonrakerDatabase as DBComp
    from .job_state import JobState
    from .file_manager.file_manager import FileManager
    from .database import DBProviderWrapper
    Totals = Dict[str, Union[float, int]]
    AuxTotals = List[Dict[str, Any]]

BASE_TOTALS = {
    "total_jobs": 0,
    "total_time": 0.,
    "total_print_time": 0.,
    "total_filament_used": 0.,
    "longest_job": 0.,
    "longest_print": 0.
}
HIST_TABLE = "job_history"
TOTALS_TABLE = "job_totals"

def _create_totals_list(
    job_totals: Dict[str, Any],
    aux_totals: List[Dict[str, Any]],
    instance: str = "default"
) -> List[Tuple[str, str, Any, Any, str]]:
    """
    Returns a list of Tuples formatted for SQL Database insertion.

    Fields of each tuple are in the following order:
        provider, field, maximum, total, instance_id
    """
    totals_list: List[Tuple[str, str, Any, Any, str]] = []
    for key, value in job_totals.items():
        total = value if key.startswith("total_") else None
        maximum = value if total is None else None
        totals_list.append(("history", key, maximum, total, instance))
    for item in aux_totals:
        if not isinstance(item, dict):
            continue
        totals_list.append(
            (
                item["provider"],
                item["field"],
                item["maximum"],
                item["total"],
                instance
            )
        )
    return totals_list

class TotalsSqlDefinition(SqlTableDefinition):
    name = TOTALS_TABLE
    prototype = (
        f"""
        {TOTALS_TABLE} (
            provider TEXT NOT NULL,
            field TEXT NOT NULL,
            maximum REAL,
            total REAL,
            instance_id TEXT NOT NULL,
            PRIMARY KEY (provider, field, instance_id)
        )
        """
    )
    version = 1

    def migrate(self, last_version: int, db_provider: DBProviderWrapper) -> None:
        if last_version == 0:
            # Migrate from "moonraker" namespace to a table
            logging.info("Migrating history totals from moonraker namespace...")
            hist_ns: Dict[str, Any] = db_provider.get_item("moonraker", "history", {})
            job_totals: Dict[str, Any] = hist_ns.get("job_totals", BASE_TOTALS)
            aux_totals: List[Dict[str, Any]] = hist_ns.get("aux_totals", [])
            if not isinstance(job_totals, dict):
                job_totals = dict(BASE_TOTALS)
            if not isinstance(aux_totals, list):
                aux_totals = []
            totals_list = _create_totals_list(job_totals, aux_totals)
            sql_conn = db_provider.connection
            with sql_conn:
                sql_conn.executemany(
                    f"INSERT OR IGNORE INTO {TOTALS_TABLE} VALUES(?, ?, ?, ?, ?)",
                    totals_list
                )
            try:
                db_provider.delete_item("moonraker", "history")
            except Exception:
                pass

class HistorySqlDefinition(SqlTableDefinition):
    name = HIST_TABLE
    prototype = (
        f"""
        {HIST_TABLE} (
            job_id INTEGER PRIMARY KEY ASC,
            user TEXT NOT NULL,
            filename TEXT,
            status TEXT NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL,
            print_duration REAL NOT NULL,
            total_duration REAL NOT NULL,
            filament_used REAL NOT NULL,
            metadata pyjson,
            auxiliary_data pyjson NOT NULL,
            instance_id TEXT NOT NULL
        )
        """
    )
    version = 1

    def _get_entry_item(
        self, entry: Dict[str, Any], name: str, default: Any = 0.
    ) -> Any:
        val = entry.get(name)
        if val is None:
            return default
        return val

    def migrate(self, last_version: int, db_provider: DBProviderWrapper) -> None:
        if last_version == 0:
            conn = db_provider.connection
            for batch in db_provider.iter_namespace("history", 1000):
                conv_vals: List[Tuple[Any, ...]] = []
                entry: Dict[str, Any]
                for key, entry in batch.items():
                    if not isinstance(entry, dict):
                        logging.info(
                            f"History migration, skipping invalid value: {key} {entry}"
                        )
                        continue
                    try:
                        conv_vals.append(
                            (
                                None,
                                self._get_entry_item(entry, "user", "No User"),
                                self._get_entry_item(entry, "filename", "unknown"),
                                self._get_entry_item(entry, "status", "error"),
                                self._get_entry_item(entry, "start_time"),
                                self._get_entry_item(entry, "end_time"),
                                self._get_entry_item(entry, "print_duration"),
                                self._get_entry_item(entry, "total_duration"),
                                self._get_entry_item(entry, "filament_used"),
                                self._get_entry_item(entry, "metadata", {}),
                                self._get_entry_item(entry, "auxiliary_data", []),
                                "default"
                            )
                        )
                    except KeyError:
                        continue
                if not conv_vals:
                    continue
                placeholders = ",".join("?" * len(conv_vals[0]))
                with conn:
                    conn.executemany(
                        f"INSERT INTO {HIST_TABLE} VALUES({placeholders})",
                        conv_vals
                    )
            db_provider.wipe_local_namespace("history")

class History:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.file_manager: FileManager = self.server.lookup_component('file_manager')
        self.request_lock = Lock()
        FieldTracker.class_init(self)
        self.auxiliary_fields: List[HistoryFieldData] = []
        database: DBComp = self.server.lookup_component("database")
        self.history_table = database.register_table(HistorySqlDefinition())
        self.totals_table = database.register_table(TotalsSqlDefinition())
        self.job_totals: Totals = dict(BASE_TOTALS)
        self.aux_totals: AuxTotals = []

        self.server.register_event_handler(
            "server:klippy_disconnect", self._handle_disconnect)
        self.server.register_event_handler(
            "server:klippy_shutdown", self._handle_shutdown)
        self.server.register_event_handler(
            "job_state:state_changed", self._on_job_state_changed)
        self.server.register_event_handler(
            "klippy_apis:job_start_complete", self._on_job_requested)
        self.server.register_notification("history:history_changed")

        self.server.register_endpoint(
            "/server/history/job", RequestType.GET | RequestType.DELETE,
            self._handle_job_request
        )
        self.server.register_endpoint(
            "/server/history/list", RequestType.GET, self._handle_jobs_list
        )
        self.server.register_endpoint(
            "/server/history/totals", RequestType.GET, self._handle_job_totals
        )
        self.server.register_endpoint(
            "/server/history/reset_totals", RequestType.POST,
            self._handle_job_total_reset
        )

        self.current_job: Optional[PrinterJob] = None
        self.current_job_id: Optional[int] = None
        self.job_user: str = "No User"
        self.job_paused: bool = False

    async def component_init(self) -> None:
        # Popluate totals
        valid_aux_totals = [
            (item.provider, item.name) for item in self.auxiliary_fields
            if item.has_totals()
        ]
        cursor = await self.totals_table.execute(f"SELECT * from {TOTALS_TABLE}")
        await cursor.set_arraysize(200)
        for row in await cursor.fetchall():
            provider, field, maximum, total, _ = tuple(row)
            if provider == "history":
                self.job_totals[field] = total if maximum is None else maximum
            elif (provider, field) in valid_aux_totals:
                item = dict(row)
                item.pop("instance_id", None)
                self.aux_totals.append(item)
        # Check for interrupted jobs
        cursor = await self.history_table.execute(
            f"SELECT job_id FROM {HIST_TABLE} WHERE status = 'in_progress'"
        )
        interrupted_jobs: List[int] = [row[0] for row in await cursor.fetchall()]
        if interrupted_jobs:
            async with self.history_table as tx:
                await tx.execute(
                    f"UPDATE {HIST_TABLE} SET status = 'interrupted' "
                    "WHERE status = 'in_progress'"
                )
            self.server.add_log_rollover_item(
                "interrupted_history",
                "The following jobs were detected as interrupted: "
                f"{interrupted_jobs}"
            )

    async def _handle_job_request(self,
                                  web_request: WebRequest
                                  ) -> Dict[str, Any]:
        async with self.request_lock:
            req_type = web_request.get_request_type()
            if req_type == RequestType.GET:
                job_id = web_request.get_str("uid")
                cursor = await self.history_table.execute(
                    f"SELECT * FROM {HIST_TABLE} WHERE job_id = ?", (int(job_id, 16),)
                )
                result = await cursor.fetchone()
                if result is None:
                    raise self.server.error(f"Invalid job uid: {job_id}", 404)
                job = dict(result)
                return {"job": self._prep_requested_job(job, job_id)}
            if req_type == RequestType.DELETE:
                all = web_request.get_boolean("all", False)
                if all:
                    cursor = await self.history_table.execute(
                        f"SELECT job_id FROM {HIST_TABLE} WHERE instance_id = ?",
                        ("default",)
                    )
                    await cursor.set_arraysize(1000)
                    deljobs = [f"{row[0]:06X}" for row in await cursor.fetchall()]
                    async with self.history_table as tx:
                        await tx.execute(
                            f"DELETE FROM {HIST_TABLE} WHERE instance_id = ?",
                            ("default",)
                        )
                    return {'deleted_jobs': deljobs}

                job_id = web_request.get_str("uid")
                async with self.history_table as tx:
                    cursor = await tx.execute(
                        f"DELETE FROM {HIST_TABLE} WHERE job_id = ?", (int(job_id, 16),)
                    )
                if cursor.rowcount < 1:
                    raise self.server.error(f"Invalid job uid: {job_id}", 404)
                return {'deleted_jobs': [job_id]}
            raise self.server.error("Invalid Request Method")

    async def _handle_jobs_list(self,
                                web_request: WebRequest
                                ) -> Dict[str, Any]:
        async with self.request_lock:
            before = web_request.get_float("before", -1)
            since = web_request.get_float("since", -1)
            limit = web_request.get_int("limit", 50)
            start = web_request.get_int("start", 0)
            order = web_request.get_str("order", "desc").upper()

            if order not in ["ASC", "DESC"]:
                raise self.server.error(f"Invalid `order` value: {order}", 400)
            # Build SQL Select Statement
            values: List[Any] = ["default"]
            sql_statement = f"SELECT * FROM {HIST_TABLE} WHERE instance_id = ?"
            if before != -1:
                sql_statement += " and end_time < ?"
                values.append(before)
            if since != -1:
                sql_statement += " and start_time > ?"
                values.append(since)
            sql_statement += f" ORDER BY job_id {order}"
            if limit > 0:
                sql_statement += " LIMIT ? OFFSET ?"
                values.append(limit)
                values.append(start)
            cursor = await self.history_table.execute(sql_statement, values)
            await cursor.set_arraysize(1000)
            jobs: List[Dict[str, Any]] = []
            for row in await cursor.fetchall():
                job = dict(row)
                job_id = f"{row['job_id']:06X}"
                jobs.append(self._prep_requested_job(job, job_id))
            return {"count": len(jobs), "jobs": jobs}

    async def _handle_job_totals(
        self, web_request: WebRequest
    ) -> Dict[str, Union[Totals, AuxTotals]]:
        return {
            "job_totals": self.job_totals,
            "auxiliary_totals": self.aux_totals
        }

    async def _handle_job_total_reset(
        self, web_request: WebRequest
    ) -> Dict[str, Union[Totals, AuxTotals]]:
        if self.current_job is not None:
            raise self.server.error("Job in progress, cannot reset totals")
        last_totals = self.job_totals
        self.job_totals = dict(BASE_TOTALS)
        last_aux_totals = self.aux_totals
        self._update_aux_totals(reset=True)
        totals_list = _create_totals_list(self.job_totals, self.aux_totals)
        async with self.totals_table as tx:
            await tx.execute(
                f"DELETE FROM {TOTALS_TABLE} WHERE instance_id = ?", ("default",)
            )
            await tx.executemany(
                f"INSERT INTO {TOTALS_TABLE} VALUES(?, ?, ?, ?, ?)", totals_list
            )
        return {
            "last_totals": last_totals,
            "last_auxiliary_totals": last_aux_totals
        }

    async def _on_job_state_changed(
        self,
        job_event: JobEvent,
        prev_stats: Dict[str, Any],
        new_stats: Dict[str, Any]
    ) -> None:
        self.job_paused = job_event == JobEvent.PAUSED
        if job_event == JobEvent.STARTED:
            if self.current_job is not None:
                # Finish with the previous state
                await self.finish_job("cancelled", prev_stats)
            await self.add_job(PrinterJob(new_stats))
        elif job_event == JobEvent.COMPLETE:
            await self.finish_job("completed", new_stats)
        elif job_event == JobEvent.ERROR:
            await self.finish_job("error", new_stats)
        elif job_event in (JobEvent.CANCELLED, JobEvent.STANDBY):
            # Cancel on "standby" for backward compatibility with
            # `CLEAR_PAUSE/SDCARD_RESET_FILE` workflow
            await self.finish_job("cancelled", prev_stats)

    def _on_job_requested(self, user: Optional[UserInfo]) -> None:
        username = user.username if user is not None else "No User"
        self.job_user = username
        if self.current_job is not None:
            self.current_job.user = username

    async def _handle_shutdown(self) -> None:
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        await self.finish_job("klippy_shutdown", last_ps)

    async def _handle_disconnect(self) -> None:
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        await self.finish_job("klippy_disconnect", last_ps)

    async def add_job(self, job: PrinterJob) -> None:
        async with self.request_lock:
            self.current_job = job
            self.current_job_id = None
            self.current_job.user = self.job_user
            self.grab_job_metadata()
            for field in self.auxiliary_fields:
                field.tracker.reset()
            self.current_job.set_aux_data(self.auxiliary_fields)
            new_id = await self.save_job(job, None)
            if new_id is None:
                logging.info(f"Error saving job, filename '{job.filename}'")
                return
            self.current_job_id = new_id
            job_id = f"{new_id:06X}"
            self.update_metadata(job_id)
            logging.debug(
                f"History Job Added - Id: {job_id}, File: {job.filename}"
            )
            self.send_history_event("added")

    async def save_job(self, job: PrinterJob, job_id: Optional[int]) -> Optional[int]:
        values: List[Any] = [
            job_id,
            job.user,
            job.filename,
            job.status,
            job.start_time,
            job.end_time,
            job.print_duration,
            job.total_duration,
            job.filament_used,
            job.metadata,
            job.auxiliary_data,
            "default"
        ]
        placeholders = ",".join("?" * len(values))
        async with self.history_table as tx:
            cursor = await tx.execute(
                f"REPLACE INTO {HIST_TABLE} VALUES({placeholders})", values
            )
        return cursor.lastrowid

    async def delete_job(self, job_id: Union[int, str]) -> None:
        if isinstance(job_id, str):
            job_id = int(job_id, 16)
        async with self.history_table as tx:
            tx.execute(
                f"DELETE FROM {HIST_TABLE} WHERE job_id = ?", (job_id,)
            )

    async def finish_job(self, status: str, pstats: Dict[str, Any]) -> None:
        async with self.request_lock:
            if self.current_job is None or self.current_job_id is None:
                self._reset_current_job()
                return
            if (
                pstats.get('filename') != self.current_job.filename or
                pstats.get('total_duration', 0.) < self.current_job.total_duration
            ):
                # Print stats have been reset, do not update this job with them
                pstats = {}
            self.current_job.user = self.job_user
            self.current_job.finish(status, pstats)
            # Regrab metadata incase metadata wasn't parsed yet due to file upload
            self.grab_job_metadata()
            self.current_job.set_aux_data(self.auxiliary_fields)
            job_id = f"{self.current_job_id:06X}"
            await self.save_job(self.current_job, self.current_job_id)
            self.update_metadata(job_id)
            await self._update_job_totals()
            logging.debug(
                f"History Job Finished - Id: {job_id}, "
                f"File: {self.current_job.filename}, "
                f"Status: {status}"
            )
            self.send_history_event("finished")
            self._reset_current_job()

    def _reset_current_job(self) -> None:
        self.current_job = None
        self.current_job_id = None
        self.job_user = "No User"

    async def get_job(
        self, job_id: Union[int, str]
    ) -> Optional[Dict[str, Any]]:
        if isinstance(job_id, str):
            job_id = int(job_id, 16)
        cursor = await self.history_table.execute(
            f"SELECT * FROM {HIST_TABLE} WHERE job_id = ?", (job_id,)
        )
        result = await cursor.fetchone()
        return dict(result) if result is not None else result

    def grab_job_metadata(self) -> None:
        if self.current_job is None:
            return
        filename: str = self.current_job.filename
        mdst = self.file_manager.get_metadata_storage()
        metadata: Dict[str, Any] = mdst.get(filename, {})
        # We don't need to store these fields in the
        # job metadata, as they are redundant
        metadata.pop('print_start_time', None)
        metadata.pop('job_id', None)
        if "thumbnails" in metadata:
            thumb: Dict[str, Any]
            for thumb in metadata['thumbnails']:
                thumb.pop('data', None)
        self.current_job.metadata = metadata

    def update_metadata(self, job_id: str) -> None:
        if self.current_job is None:
            return
        mdst = self.file_manager.get_metadata_storage()
        filename: str = self.current_job.filename
        metadata: Dict[str, Any] = mdst.get(filename, {})
        if metadata:
            # Add the start time and job id to the
            # persistent metadata storage
            metadata.update({
                'print_start_time': self.current_job.get('start_time'),
                'job_id': job_id
            })
            mdst.insert(filename, metadata)

    async def _update_job_totals(self) -> None:
        if self.current_job is None:
            return
        job = self.current_job
        self._accumulate_total("total_jobs", 1)
        self._accumulate_total("total_time", job.total_duration)
        self._accumulate_total("total_print_time", job.print_duration)
        self._accumulate_total("total_filament_used", job.filament_used)
        self._maximize_total("longest_job", job.total_duration)
        self._maximize_total("longest_print", job.print_duration)
        self._update_aux_totals()
        totals_list = _create_totals_list(self.job_totals, self.aux_totals)
        async with self.totals_table as tx:
            await tx.executemany(
                f"REPLACE INTO {TOTALS_TABLE} VALUES(?, ?, ?, ?, ?)", totals_list
            )

    def _accumulate_total(self, field: str, val: Union[int, float]) -> None:
        self.job_totals[field] += val

    def _maximize_total(self, field: str, val: Union[int, float]) -> None:
        self.job_totals[field] = max(self.job_totals[field], val)

    def _update_aux_totals(self, reset: bool = False) -> None:
        last_totals = self.aux_totals
        self.aux_totals = [
            field.get_totals(last_totals, reset)
            for field in self.auxiliary_fields
            if field.has_totals()
        ]

    def send_history_event(self, evt_action: str) -> None:
        if self.current_job is None or self.current_job_id is None:
            return
        job_id = f"{self.current_job_id:06X}"
        job = self._prep_requested_job(self.current_job.get_stats(), job_id)
        self.server.send_event(
            "history:history_changed", {'action': evt_action, 'job': job}
        )

    def _prep_requested_job(
        self, job: Dict[str, Any], job_id: str
    ) -> Dict[str, Any]:
        fm = self.file_manager
        mtime = job.get("metadata", {}).get("modified", None)
        job["exists"] = fm.check_file_exists("gcodes", job['filename'], mtime)
        job["job_id"] = job_id
        job.pop("instance_id", None)
        return job

    def register_auxiliary_field(self, new_field: HistoryFieldData) -> None:
        if new_field.provider == "history":
            raise self.server.error("Provider name 'history' is reserved")
        for field in self.auxiliary_fields:
            if field == new_field:
                raise self.server.error(
                    f"Field {field.name} already registered by "
                    f"provider {field.provider}."
                )
        self.auxiliary_fields.append(new_field)

    def tracking_enabled(self, check_paused: bool) -> bool:
        if self.current_job is None:
            return False
        return not self.job_paused if check_paused else True

    async def on_exit(self) -> None:
        if self.current_job is None:
            return
        jstate: JobState = self.server.lookup_component("job_state")
        last_ps = jstate.get_last_stats()
        await self.finish_job("server_exit", last_ps)

class PrinterJob:
    def __init__(self, data: Dict[str, Any] = {}) -> None:
        self.end_time: Optional[float] = None
        self.filament_used: float = 0
        self.filename: str = ""
        self.metadata: Dict[str, Any] = {}
        self.print_duration: float = 0.
        self.status: str = "in_progress"
        self.start_time = time.time()
        self.total_duration: float = 0.
        self.auxiliary_data: List[Dict[str, Any]] = []
        self.user: str = "No User"
        self.update_from_ps(data)

    def finish(self,
               status: str,
               print_stats: Dict[str, Any] = {}
               ) -> None:
        self.end_time = time.time()
        self.status = status if status is not None else "error"
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

    def set_aux_data(self, fields: List[HistoryFieldData]) -> None:
        self.auxiliary_data = [field.as_dict() for field in fields]

    def update_from_ps(self, data: Dict[str, Any]) -> None:
        for i in data:
            if hasattr(self, i) and data[i] is not None:
                setattr(self, i, data[i])


def load_component(config: ConfigHelper) -> History:
    return History(config)
