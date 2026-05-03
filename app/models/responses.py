"""
Pydantic models for API response bodies.
"""

from enum import StrEnum
from pydantic import BaseModel


class JobStatus(StrEnum):
    """Lifecycle states of a background scheduling job."""
    PENDING = "pending"   # created, not yet started
    RUNNING = "running"   # solver is actively working
    DONE    = "done"      # solver finished; result is available
    FAILED  = "failed"    # solver encountered an unrecoverable error


class JobResponse(BaseModel):
    """Returned by GET /jobs/{job_id} — shows current job status."""
    job_id: str
    status: JobStatus
    # 0–100 progress indicator updated at each solver stage
    progress: int = 0
    # Human-readable description of the current stage (e.g. "Fetching data")
    message: str | None = None


class ProposedClassGroup(BaseModel):
    """
    A single proposed class group assignment ready to be reviewed.
    If the admin approves, this gets POSTed to NestJS /class-group.
    """
    teacher_subject_id: int
    section_id: int
    room_schedule_id: int
    # Included for preview display (e.g. show the time slot in the UI)
    schedule_time_id: int


class ResultResponse(BaseModel):
    """Returned by GET /jobs/{job_id}/result when the job is done."""
    job_id: str
    status: JobStatus
    class_groups: list[ProposedClassGroup]
    total: int


class CommitResponse(BaseModel):
    """Returned by POST /jobs/{job_id}/commit after pushing results to NestJS."""
    job_id: str
    committed: int          # number of class groups successfully created
    failed: int             # number that failed (NestJS rejected or network error)
    errors: list[str]       # error messages for failed items


class ConflictItem(BaseModel):
    """Describes a single scheduling conflict found in existing class groups."""
    # "teacher_double_booked" or "section_double_booked"
    conflict_type: str
    description: str
    # IDs of the class groups involved in the conflict
    class_group_ids: list[int]


class ConflictResponse(BaseModel):
    """Returned by GET /scheduler/conflicts."""
    total_conflicts: int
    conflicts: list[ConflictItem]
