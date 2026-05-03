"""
Scheduler API endpoints.

All routes are thin — they delegate business logic to services and repositories.
The heavy work (solving) runs in a FastAPI BackgroundTask so the HTTP response
is returned immediately with a job_id the client can poll.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.clients.nestjs_client import NestJSClient
from app.core.dependencies import get_job_repo, get_nestjs_client
from app.core.exceptions import JobNotFoundError, JobNotReadyError
from app.models.requests import GenerateRequest
from app.models.responses import (
    CommitResponse,
    ConflictResponse,
    JobResponse,
    JobStatus,
    ProposedClassGroup,
    ResultResponse,
)
from app.repositories.job_repository import JobRepository
from app.services.conflict_service import detect_conflicts
from app.workers.solver_task import run_solver_task

log = structlog.get_logger()
router = APIRouter(prefix="/scheduler", tags=["Scheduler"])


# ── Generate ──────────────────────────────────────────────────────────────────

@router.post(
    "/generate",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the scheduling solver",
    description=(
        "Kicks off the two-stage scheduler as a background task. "
        "Returns a job_id immediately. Poll GET /jobs/{job_id} to track progress."
    ),
)
async def generate_schedule(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    job_repo: JobRepository = Depends(get_job_repo),
) -> JobResponse:
    # Create a new job entry in the in-memory store
    job_id = job_repo.create_job()

    # Enqueue the solver as a background task — response returns immediately
    background_tasks.add_task(run_solver_task, job_id, request, job_repo)

    log.info("api.generate_started", job_id=job_id)
    return JobResponse(job_id=job_id, status=JobStatus.PENDING)


# ── Job status ────────────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}",
    response_model=JobResponse,
    summary="Poll job status",
    description="Returns the current status and progress (0-100) of a scheduling job.",
)
async def get_job_status(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repo),
) -> JobResponse:
    data = job_repo.get_job(job_id)
    if data is None:
        raise JobNotFoundError(job_id)

    return JobResponse(
        job_id=job_id,
        status=data["status"],
        progress=data["progress"],
        message=data.get("message"),
    )


# ── Result preview ────────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}/result",
    response_model=ResultResponse,
    summary="Get proposed class groups",
    description=(
        "Returns the full list of proposed class group assignments for admin review. "
        "The job must be in 'done' status — poll /jobs/{job_id} first."
    ),
)
async def get_job_result(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repo),
) -> ResultResponse:
    data = job_repo.get_job(job_id)
    if data is None:
        raise JobNotFoundError(job_id)

    if data["status"] != JobStatus.DONE:
        raise JobNotReadyError(job_id, data["status"])

    class_groups = [ProposedClassGroup(**cg) for cg in data["result"]]
    return ResultResponse(
        job_id=job_id,
        status=JobStatus.DONE,
        class_groups=class_groups,
        total=len(class_groups),
    )


# ── Commit ────────────────────────────────────────────────────────────────────

@router.post(
    "/jobs/{job_id}/commit",
    response_model=CommitResponse,
    summary="Save proposed schedule to NestJS",
    description=(
        "POSTs every proposed class group to the NestJS /class-group endpoint. "
        "Failed items are reported in the response but do not stop other items. "
        "The job must be in 'done' status."
    ),
)
async def commit_job(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repo),
    nestjs: NestJSClient = Depends(get_nestjs_client),
) -> CommitResponse:
    data = job_repo.get_job(job_id)
    if data is None:
        raise JobNotFoundError(job_id)

    if data["status"] != JobStatus.DONE:
        raise JobNotReadyError(job_id, data["status"])

    committed = 0
    failed = 0
    errors: list[str] = []

    items = [
        {
            "teacher_subject_id": cg["teacher_subject_id"],
            "room_schedule_id": cg["room_schedule_id"],
            "section_id": cg["section_id"],
            **({"schedule_time_id": cg["schedule_time_id"]} if cg.get("schedule_time_id") is not None else {}),
        }
        for cg in data["result"]
    ]

    async with nestjs as client:
        try:
            result = await client.bulk_create_class_groups(items)
            committed = result.get("count", len(items))
        except Exception as exc:
            failed = len(items)
            errors.append(str(exc))
            log.warning("api.commit_bulk_failed", job_id=job_id, error=str(exc))

    log.info("api.commit_done", job_id=job_id, committed=committed, failed=failed)
    return CommitResponse(job_id=job_id, committed=committed, failed=failed, errors=errors)


# ── Cancel / delete ───────────────────────────────────────────────────────────

@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a job",
    description="Removes the job and its result from the in-memory store.",
)
async def delete_job(
    job_id: str,
    job_repo: JobRepository = Depends(get_job_repo),
) -> None:
    if job_repo.get_job(job_id) is None:
        raise JobNotFoundError(job_id)
    job_repo.delete_job(job_id)


# ── Conflict check ────────────────────────────────────────────────────────────

@router.get(
    "/conflicts",
    response_model=ConflictResponse,
    summary="Check existing class groups for conflicts",
    description=(
        "Fetches all class groups from NestJS and reports any teacher or section "
        "double-bookings. Useful for auditing manually created schedules."
    ),
)
async def check_conflicts(
    nestjs: NestJSClient = Depends(get_nestjs_client),
) -> ConflictResponse:
    async with nestjs as client:
        class_groups   = await client.get_class_groups()
        room_schedules = await client.get_room_schedules()
        schedules      = await client.get_schedules()
        ts             = await client.get_teacher_subjects()

    conflicts = detect_conflicts(class_groups, room_schedules, schedules, ts)
    return ConflictResponse(total_conflicts=len(conflicts), conflicts=conflicts)
