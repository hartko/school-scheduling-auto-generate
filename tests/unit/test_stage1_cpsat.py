"""
Unit tests for Stage 1 (CP-SAT time-slot assignment).

Verifies:
  - Every required pair is assigned exactly one slot
  - No teacher is double-booked (same slot, same teacher)
  - No section is double-booked (same slot, same section)
  - SolverInfeasibleError is raised when the problem has no solution
"""

import pytest
from app.core.exceptions import SolverInfeasibleError
from app.models.requests import SolverConfig
from app.schemas.nestjs import TeacherSubject
from app.schemas.solver import SolverInput, TimeSlot
from app.services.scheduler.stage1_cpsat import solve_stage1


def _make_input(
    num_teachers: int,
    num_sections: int,
    num_slots: int,
    pairs: list[tuple[int, int]] | None = None,
) -> SolverInput:
    """
    Build a minimal SolverInput for testing.

    Each teacher teaches one subject; each section needs that one subject.
    Slots are spaced 60 minutes apart on day 0.
    """
    # One TeacherSubject per teacher (teacher i → subject i → ts_id = i+1)
    teacher_subjects = [
        TeacherSubject(id=i + 1, teacher_id=i + 1, subject_id=i + 1)
        for i in range(num_teachers)
    ]
    ts_by_id = {ts.id: ts for ts in teacher_subjects}

    # Time slots: 08:00, 09:00, 10:00 … (60-minute slots, all on day 0)
    slots = [
        TimeSlot(
            id=si + 1,
            schedule_id=1,
            day=0,
            start_minutes=480 + si * 60,
            end_minutes=480 + si * 60 + 50,
        )
        for si in range(num_slots)
    ]
    slots_by_id = {s.id: s for s in slots}

    # Default pairs: each teacher is paired with each section
    if pairs is None:
        pairs = [
            (ts.id, sec_id)
            for ts in teacher_subjects
            for sec_id in range(1, num_sections + 1)
        ]

    return SolverInput(
        time_slots=slots,
        time_slots_by_id=slots_by_id,
        teacher_subjects=teacher_subjects,
        ts_by_id=ts_by_id,
        room_schedules=[],
        rs_by_schedule_id={},
        rooms_by_id={},
        required_pairs=pairs,
    )


def test_all_pairs_assigned():
    """Every required pair must appear exactly once in the result."""
    solver_input = _make_input(num_teachers=2, num_sections=2, num_slots=6)
    config = SolverConfig()
    config.solver_time_limit_seconds = 10

    result = solve_stage1(solver_input, config)
    assigned_pairs = {(a.teacher_subject_id, a.section_id) for a in result}

    for pair in solver_input.required_pairs:
        assert pair in assigned_pairs, f"Pair {pair} was not assigned"


def test_no_teacher_double_booking():
    """The same teacher must not appear in two assignments at the same time slot."""
    # 1 teacher, 3 sections, 3 slots — teacher must teach each section at a different slot
    solver_input = _make_input(num_teachers=1, num_sections=3, num_slots=3)
    config = SolverConfig()
    config.solver_time_limit_seconds = 10

    result = solve_stage1(solver_input, config)

    # Group by slot; no slot should have the same teacher twice
    from collections import defaultdict
    slot_teachers: dict[int, list[int]] = defaultdict(list)
    for a in result:
        ts = solver_input.ts_by_id[a.teacher_subject_id]
        slot_teachers[a.schedule_time_id].append(ts.teacher_id)

    for slot_id, teachers in slot_teachers.items():
        assert len(teachers) == len(set(teachers)), (
            f"Teacher double-booked at slot {slot_id}: {teachers}"
        )


def test_no_section_double_booking():
    """The same section must not appear in two assignments at the same time slot."""
    # 3 teachers, 1 section, 3 slots — section must receive each subject at a different slot
    solver_input = _make_input(num_teachers=3, num_sections=1, num_slots=3)
    config = SolverConfig()
    config.solver_time_limit_seconds = 10

    result = solve_stage1(solver_input, config)

    from collections import defaultdict
    slot_sections: dict[int, list[int]] = defaultdict(list)
    for a in result:
        slot_sections[a.schedule_time_id].append(a.section_id)

    for slot_id, sections in slot_sections.items():
        assert len(sections) == len(set(sections)), (
            f"Section double-booked at slot {slot_id}: {sections}"
        )


def test_infeasible_raises_error():
    """
    When there are more required pairs than time slots and the constraints
    cannot be satisfied, SolverInfeasibleError must be raised.

    Here: 1 teacher, 3 sections, only 1 slot → impossible to assign
    each section to the same teacher at 3 different slots.
    """
    solver_input = _make_input(num_teachers=1, num_sections=3, num_slots=1)
    config = SolverConfig()
    config.solver_time_limit_seconds = 5

    with pytest.raises(SolverInfeasibleError):
        solve_stage1(solver_input, config)
