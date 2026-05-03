"""
Pydantic models that mirror the NestJS / Prisma data models exactly.
These are used to parse and validate every response from the NestJS API.

Field names must match the JSON keys returned by NestJS (snake_case as defined
in the Prisma schema: prisma/schema.prisma).
"""

from __future__ import annotations
from typing import Generic, TypeVar
from pydantic import BaseModel


# ── Core entities ─────────────────────────────────────────────────────────────

class Teacher(BaseModel):
    id: int
    first_name: str
    last_name: str
    middle_name: str | None = None
    teacher_code: str | None = None
    email: str | None = None


class Room(BaseModel):
    id: int
    name: str
    capacity: int | None = None
    # Floor number — used by Stage 2 to minimize vertical travel between classes.
    # None is treated as floor 1 (ground level) throughout the solver.
    level: int | None = None


class Subject(BaseModel):
    id: int
    name: str
    code: str | None = None


class Section(BaseModel):
    id: int
    name: str
    code: str


class ScheduleTime(BaseModel):
    id: int
    schedule_id: int
    # "HH:MM" strings — converted to integer minutes by the data loader
    start_time: str
    end_time: str
    # When True this slot is a break; breaks are excluded from the solver
    is_break: bool
    # Day of week: 0 = Monday … 6 = Sunday (matches the NestJS/Prisma convention)
    day: int


class Schedule(BaseModel):
    id: int
    name: str
    description: str | None = None
    schedule_code: str | None = None
    # Nested time slots returned by GET /schedules/:id
    scheduleTimes: list[ScheduleTime] = []


# ── Junction / assignment entities ────────────────────────────────────────────

class TeacherSubject(BaseModel):
    """Links a teacher to a subject they are qualified to teach."""
    id: int
    teacher_id: int
    subject_id: int


class RoomSchedule(BaseModel):
    """
    Links a room to a schedule template.
    When fetched from GET /api/room-schedules the NestJS API includes
    nested `room` and `schedule` objects (with scheduleTimes).
    """
    id: int
    room_id: int
    schedule_id: int
    # Populated by the NestJS include when fetching /api/room-schedules
    room: Room | None = None
    schedule: Schedule | None = None


class ClassGroup(BaseModel):
    """
    The final assignment: one teacher-subject assigned to one room-schedule
    for one section.  This is what we generate and POST back to NestJS.
    """
    id: int
    teacher_subject_id: int | None = None
    room_schedule_id: int | None = None
    section_id: int | None = None
    schedule_time_id: int | None = None


# ── Pagination wrapper ────────────────────────────────────────────────────────

class PaginationMeta(BaseModel):
    """Matches the `pagination` object returned by every NestJS list endpoint."""
    totalItems: int
    currentPage: int
    itemsPerPage: int
    itemCount: int
    totalPages: int


T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic wrapper: { data: T[], pagination: PaginationMeta }"""
    data: list[T]
    pagination: PaginationMeta
