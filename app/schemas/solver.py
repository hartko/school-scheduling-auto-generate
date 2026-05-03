"""
Internal data structures passed between the solver stages.
These are plain dataclasses (not Pydantic models) for speed — they never
leave the service layer, so JSON serialization is not needed.
"""

from dataclasses import dataclass, field


# ── Time slot representation ──────────────────────────────────────────────────

@dataclass
class TimeSlot:
    """
    A single ScheduleTime record after parsing.
    Times are stored as integer minutes-since-midnight so the CP-SAT solver
    can do integer arithmetic (e.g. gap = slot_b.start - slot_a.end).
    """
    id: int
    schedule_id: int
    day: int                # 0 = Monday … 6 = Sunday
    start_minutes: int      # e.g. "08:30" → 510
    end_minutes: int        # e.g. "09:30" → 570
    is_break: bool = False  # break slots are excluded from the solver


# ── Solver input bundle ───────────────────────────────────────────────────────

@dataclass
class SolverInput:
    """
    Everything Stage 1 (CP-SAT) needs, pre-indexed for fast lookup.
    Built by data_loader.build_solver_input().
    """

    # All non-break TimeSlots across every schedule
    time_slots: list[TimeSlot]

    # Fast lookup: slot.id → TimeSlot
    time_slots_by_id: dict[int, TimeSlot]

    # All TeacherSubject records (from NestJS)
    teacher_subjects: list  # list[TeacherSubject]

    # Fast lookup: teacher_subject_id → TeacherSubject
    ts_by_id: dict[int, object]

    # All RoomSchedule records with nested room + schedule populated
    room_schedules: list    # list[RoomSchedule]

    # Lookup: schedule_id → list[RoomSchedule]
    # Used in Stage 2 to find eligible rooms for a given time slot
    rs_by_schedule_id: dict[int, list]

    # Lookup: room_id → Room
    rooms_by_id: dict[int, object]

    # The pairs the solver must assign:
    # each tuple is (teacher_subject_id, section_id)
    required_pairs: list[tuple[int, int]]

    # How many time slots (on different days) each pair must be assigned.
    # 1 unit = 1 hour/day, so a 3-unit subject needs 3 slots on 3 different days.
    # key: (teacher_subject_id, section_id) → units
    pair_units: dict[tuple[int, int], int]


# ── Stage output structures ───────────────────────────────────────────────────

@dataclass
class Stage1Assignment:
    """
    Output of Stage 1: a (teacher_subject, section) pair has been assigned
    to a specific time slot.  Room is not yet determined.
    """
    teacher_subject_id: int
    section_id: int
    schedule_time_id: int   # which TimeSlot was chosen


@dataclass
class Stage2Assignment:
    """
    Output of Stage 2: a room_schedule has been chosen for the assignment.
    This is the final ClassGroup ready to POST to NestJS.
    """
    teacher_subject_id: int
    section_id: int
    schedule_time_id: int   # kept for preview/display purposes
    room_schedule_id: int   # which RoomSchedule was assigned
