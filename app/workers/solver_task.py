"""
Background solver task — runs the two-stage scheduler asynchronously.

FastAPI's BackgroundTasks launches this coroutine after returning the 202
response to the client.  The admin can poll GET /jobs/{job_id} to track
progress and retrieve the result when done.

Why run_in_executor for Stage 1?
  OR-Tools CP-SAT is a CPU-bound operation.  Running it directly in an async
  function would block the event loop, freezing all other requests.
  asyncio.get_event_loop().run_in_executor(None, fn) offloads it to a thread
  pool so the event loop stays responsive while the solver works.
"""

from __future__ import annotations

import asyncio
from functools import partial

import structlog

from app.models.requests import GenerateRequest
from app.models.responses import JobStatus
from app.repositories.job_repository import JobRepository
from app.services.scheduler_service import run_scheduler

log = structlog.get_logger()


async def run_solver_task(
    job_id: str,
    request: GenerateRequest,
    job_repo: JobRepository,
) -> None:
    """
    Entry point called by FastAPI BackgroundTasks.

    Updates job status at each stage so the client can display progress:
        5%  → RUNNING: starting
        10% → fetching data from NestJS
        25% → building solver input
        30% → Stage 1 (CP-SAT, may take up to time_limit seconds)
        85% → Stage 2 (greedy room assignment)
        100%→ DONE

    All exceptions are caught, logged, and stored as FAILED so the client
    always gets a meaningful status instead of a silent timeout.
    """

    async def progress_callback(progress: int, message: str) -> None:
        """Update the job's progress in the in-memory store."""
        job_repo.update_status(job_id, JobStatus.RUNNING, progress=progress, message=message)
        log.debug("solver_task.progress", job_id=job_id, progress=progress, message=message)

    try:
        job_repo.update_status(job_id, JobStatus.RUNNING, progress=5, message="Starting solver")
        log.info("solver_task.started", job_id=job_id)

        # run_scheduler is async for the NestJS fetch phase, but Stage 1 (CP-SAT)
        # is CPU-bound.  The scheduler_service wraps the CPU work in run_in_executor
        # internally via _run_stage1_in_thread().
        stage2_assignments = await run_scheduler(request, progress_callback)

        # Serialize Stage2Assignment dataclasses to plain dicts for storage
        result = [
            {
                "teacher_subject_id": a.teacher_subject_id,
                "section_id":         a.section_id,
                "schedule_time_id":   a.schedule_time_id,
                "room_schedule_id":   a.room_schedule_id,
            }
            for a in stage2_assignments
        ]

        job_repo.store_result(job_id, result)
        log.info("solver_task.completed", job_id=job_id, total=len(result))

    except Exception as exc:
        # Catch everything so the job is always marked FAILED rather than stuck
        error_msg = f"{type(exc).__name__}: {exc}"
        job_repo.store_error(job_id, error_msg)
        log.exception("solver_task.failed", job_id=job_id, error=error_msg)
