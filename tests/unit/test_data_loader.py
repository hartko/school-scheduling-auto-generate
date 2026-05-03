"""
Unit tests for the data loader.

Verifies:
  - parse_time converts "HH:MM" strings correctly
  - Break slots are excluded from the solver's time slot list
  - required_pairs are built correctly from section-subject assignments
  - Subjects with no teacher are skipped (not a crash)
"""

import pytest
from app.services.scheduler.data_loader import build_solver_input, parse_time
from app.schemas.nestjs import (
    Room, RoomSchedule, Schedule, ScheduleTime, TeacherSubject
)


def test_parse_time_basic():
    assert parse_time("08:00") == 480
    assert parse_time("08:30") == 510
    assert parse_time("13:00") == 780
    assert parse_time("00:00") == 0


def _make_schedule(sid: int, times: list[dict]) -> Schedule:
    """Helper to build a Schedule with ScheduleTime children."""
    return Schedule(
        id=sid,
        name=f"Schedule {sid}",
        schedule_code=f"SCH-{sid:03}",
        scheduleTimes=[
            ScheduleTime(
                id=t["id"],
                schedule_id=sid,
                start_time=t["start"],
                end_time=t["end"],
                is_break=t.get("is_break", False),
                day=t.get("day", 0),
            )
            for t in times
        ],
    )


def test_break_slots_excluded():
    """Slots with is_break=True must not appear in SolverInput.time_slots."""
    schedule = _make_schedule(1, [
        {"id": 1, "start": "08:00", "end": "09:00", "is_break": False},
        {"id": 2, "start": "09:00", "end": "09:15", "is_break": True},   # break
        {"id": 3, "start": "09:15", "end": "10:15", "is_break": False},
    ])
    room = Room(id=1, name="Room 1", level=1)
    rs = RoomSchedule(id=1, room_id=1, schedule_id=1, room=room, schedule=schedule)
    ts = TeacherSubject(id=1, teacher_id=1, subject_id=1)

    result = build_solver_input(
        teacher_subjects=[ts],
        room_schedules=[rs],
        rooms=[room],
        schedules=[schedule],
        section_assignments=[{"section_id": 1, "subject_ids": [1]}],
    )

    slot_ids = [s.id for s in result.time_slots]
    assert 2 not in slot_ids, "Break slot must be excluded"
    assert 1 in slot_ids
    assert 3 in slot_ids


def test_required_pairs_built_correctly():
    """Each (section, subject) with a matching teacher produces one required pair."""
    schedule = _make_schedule(1, [
        {"id": 10, "start": "08:00", "end": "09:00"},
    ])
    room = Room(id=1, name="Room 1", level=1)
    rs = RoomSchedule(id=1, room_id=1, schedule_id=1, room=room, schedule=schedule)

    teacher_subjects = [
        TeacherSubject(id=1, teacher_id=1, subject_id=101),  # Teacher A → Math
        TeacherSubject(id=2, teacher_id=2, subject_id=102),  # Teacher B → Science
    ]

    result = build_solver_input(
        teacher_subjects=teacher_subjects,
        room_schedules=[rs],
        rooms=[room],
        schedules=[schedule],
        section_assignments=[
            {"section_id": 1, "subject_ids": [101, 102]},
        ],
    )

    # section 1 needs subject 101 (ts_id=1) and subject 102 (ts_id=2)
    assert (1, 1) in result.required_pairs
    assert (2, 1) in result.required_pairs
    assert len(result.required_pairs) == 2


def test_multiple_teachers_for_same_subject():
    """When two teachers can cover a subject, both options appear as required pairs."""
    schedule = _make_schedule(1, [{"id": 10, "start": "08:00", "end": "09:00"}])
    room = Room(id=1, name="Room 1", level=1)
    rs = RoomSchedule(id=1, room_id=1, schedule_id=1, room=room, schedule=schedule)

    teacher_subjects = [
        TeacherSubject(id=1, teacher_id=1, subject_id=101),
        TeacherSubject(id=2, teacher_id=2, subject_id=101),  # second teacher for same subject
    ]

    result = build_solver_input(
        teacher_subjects=teacher_subjects,
        room_schedules=[rs],
        rooms=[room],
        schedules=[schedule],
        section_assignments=[{"section_id": 1, "subject_ids": [101]}],
    )

    # Both teacher options should be present as pairs; the solver picks one
    assert (1, 1) in result.required_pairs
    assert (2, 1) in result.required_pairs


def test_subject_with_no_teacher_is_skipped():
    """A subject with no TeacherSubject record should not crash — just skip."""
    schedule = _make_schedule(1, [{"id": 10, "start": "08:00", "end": "09:00"}])
    room = Room(id=1, name="Room 1", level=1)
    rs = RoomSchedule(id=1, room_id=1, schedule_id=1, room=room, schedule=schedule)

    result = build_solver_input(
        teacher_subjects=[],   # no teachers assigned
        room_schedules=[rs],
        rooms=[room],
        schedules=[schedule],
        section_assignments=[{"section_id": 1, "subject_ids": [999]}],
    )

    assert result.required_pairs == []
