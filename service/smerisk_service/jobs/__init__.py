"""Queue long-running calculations and retain results for 24 hours (§2.3).

Jobs live in the memory of the process: the service has no durable state. A restart loses them, and the
application keeps what it wants from the result.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from smerisk_service import logs
from smerisk_service.errors import error_body


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class JobAccepted(BaseModel):
    job_id: str
    status_url: str


class JobError(BaseModel):
    status_code: int
    """HTTP status the synchronous call would have returned: 422, 409, 413, or 500."""
    body: dict[str, Any]


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    progress: float
    """From 0 to 1."""
    created_at: dt.datetime
    finished_at: dt.datetime | None
    expires_at: dt.datetime
    result: dict[str, Any] | None
    """The full response envelope once the job has succeeded."""
    error: JobError | None


@dataclass
class _Job:
    id: str
    caller: str
    created: float
    label: str = ""
    status: JobStatus = JobStatus.PENDING
    finished: float | None = None
    result: dict[str, Any] | None = None
    error: JobError | None = None

    def view(self, ttl: int) -> JobView:
        def stamp(t: float) -> dt.datetime:
            return dt.datetime.fromtimestamp(t, dt.UTC)

        return JobView(
            job_id=self.id,
            status=self.status,
            progress=1.0 if self.status in (JobStatus.SUCCEEDED, JobStatus.FAILED) else 0.0,
            created_at=stamp(self.created),
            finished_at=stamp(self.finished) if self.finished else None,
            expires_at=stamp((self.finished or self.created) + ttl),
            result=self.result,
            error=self.error,
        )


@dataclass
class JobStore:
    workers: int
    ttl_seconds: int
    max_per_caller: int
    _jobs: dict[str, _Job] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _executor: ThreadPoolExecutor = field(init=False)

    def __post_init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="smerisk-job")

    def submit(self, caller: str, compute: Callable[[], BaseModel], label: str = "") -> _Job:
        """Queue ``compute``, whose result is a response envelope; 429 when the caller has too many jobs.

        ``label`` names the calculation in the logs (the route).
        """
        self.purge()
        with self._lock:
            active = sum(
                j.caller == caller and j.status in (JobStatus.PENDING, JobStatus.RUNNING) for j in self._jobs.values()
            )
            if active >= self.max_per_caller:
                logs.event("job.refused", logging.WARNING, caller=caller, label=label, active=active)
                raise HTTPException(429, f"{active} jobs pending or running; wait for one to finish")
            job = _Job(id=uuid.uuid4().hex, caller=caller, created=time.time(), label=label)
            self._jobs[job.id] = job
            queued = sum(j.status == JobStatus.PENDING for j in self._jobs.values())
        logs.event("job.submitted", job_id=job.id, caller=caller, label=label, queued=queued)
        # The worker keeps the request id of the call that created the job, for its logs and its trace id.
        self._executor.submit(contextvars.copy_context().run, self._run, job, compute)
        return job

    def _run(self, job: _Job, compute: Callable[[], BaseModel]) -> None:
        job.status = JobStatus.RUNNING
        started = time.perf_counter()
        logs.event("job.started", job_id=job.id, label=job.label, waited_ms=round((time.time() - job.created) * 1000))
        try:
            job.result = compute().model_dump(mode="json")
            job.status = JobStatus.SUCCEEDED
        except Exception as exc:  # every failure is reported in the job, never raised in the worker
            status, body = error_body(exc)
            job.error = JobError(status_code=status, body=body)
            job.status = JobStatus.FAILED
        job.finished = time.time()
        logs.event(
            "job.finished",
            logging.INFO if job.status == JobStatus.SUCCEEDED else logging.WARNING,
            job_id=job.id,
            label=job.label,
            status=job.status.value,
            http_status=job.error.status_code if job.error else 200,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )

    def get(self, caller: str, job_id: str) -> JobView | None:
        """The job, only for the caller that created it."""
        self.purge()
        job = self._jobs.get(job_id)
        return job.view(self.ttl_seconds) if job is not None and job.caller == caller else None

    def purge(self) -> None:
        limit = time.time() - self.ttl_seconds
        with self._lock:
            expired = [i for i, j in self._jobs.items() if (j.finished or time.time()) < limit]
            for job_id in expired:
                del self._jobs[job_id]
        if expired:
            logs.event("job.expired", count=len(expired))

    def wait(self, timeout: float = 60) -> None:
        """Wait until no job is pending or running (tests, graceful shutdown)."""
        end = time.time() + timeout
        while any(j.status in (JobStatus.PENDING, JobStatus.RUNNING) for j in self._jobs.values()):
            if time.time() > end:
                raise TimeoutError("jobs still running")
            time.sleep(0.01)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
