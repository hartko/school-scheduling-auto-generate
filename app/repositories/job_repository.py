"""
In-memory job store for background scheduling jobs.

Since only one admin uses this service at a time, a plain Python dict is
sufficient — no Redis or database required.  The store lives on app.state
and is cleared when the server restarts (which is acceptable for this use case).

Job state shape:
    {
        "status":   "pending" | "running" | "done" | "failed",
        "progress": 0-100,
        "message":  str | None,   # current stage description
        "result":   list[dict] | None,   # Stage2Assignment dicts when done
        "error":    str | None,   # error message when failed
    }
"""

from __future__ import annotations

import uuid
import structlog

from app.models.responses import JobStatus

log = structlog.get_logger()


class JobRepository:
    """
    Simple dict-backed store for solver job state.
    All methods are synchronous — asyncio's single-threaded event loop makes
    dict access thread-safe for this single-user scenario.
    """

    def __init__(self) -> None:
        # job_id (str UUID) → job state dict
        self._store: dict[str, dict] = {}

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_job(self) -> str:
        """Create a new job in PENDING state and return its UUID."""
        job_id = str(uuid.uuid4())
        self._store[job_id] = {
            "status": JobStatus.PENDING,
            "progress": 0,
            "message": None,
            "result": None,
            "error": None,
        }
        log.info("job.created", job_id=job_id)
        return job_id

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        progress: int = 0,
        message: str | None = None,
    ) -> None:
        """Update the status and progress of an existing job."""
        if job_id not in self._store:
            log.warning("job.update_missing", job_id=job_id)
            return
        self._store[job_id].update(status=status, progress=progress, message=message)
        log.debug("job.status_updated", job_id=job_id, status=status, progress=progress)

    def store_result(self, job_id: str, result: list[dict]) -> None:
        """Store the solver result and mark the job as DONE."""
        if job_id not in self._store:
            log.warning("job.store_result_missing", job_id=job_id)
            return
        self._store[job_id].update(
            status=JobStatus.DONE,
            progress=100,
            message="Schedule generated successfully",
            result=result,
        )
        log.info("job.done", job_id=job_id, total_class_groups=len(result))

    def store_error(self, job_id: str, error: str) -> None:
        """Record an error and mark the job as FAILED."""
        if job_id not in self._store:
            log.warning("job.store_error_missing", job_id=job_id)
            return
        self._store[job_id].update(
            status=JobStatus.FAILED,
            message="Solver failed",
            error=error,
        )
        log.error("job.failed", job_id=job_id, error=error)

    def delete_job(self, job_id: str) -> None:
        """Remove a job from the store (used by the cancel endpoint)."""
        self._store.pop(job_id, None)
        log.info("job.deleted", job_id=job_id)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict | None:
        """Return the raw job state dict, or None if the job does not exist."""
        return self._store.get(job_id)

    def list_jobs(self) -> list[dict]:
        """Return all jobs (useful for debugging)."""
        return [{"job_id": k, **v} for k, v in self._store.items()]
