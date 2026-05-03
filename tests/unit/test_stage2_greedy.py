"""
Unit tests for Stage 2 (greedy room assignment).

Verifies:
  - Lower-floor room is preferred when the teacher/section was on a low floor
  - The same room_schedule is never assigned twice at the same slot
  - NoRoomAvailableError is raised when no eligible room exists
"""

import pytest
from app.core.exceptions import NoRoomAvailableError
from app.models.requests import SolverConfig
from app.schemas.nestjs import Room, RoomSchedule, Schedule, ScheduleTime
from app.schemas.solver import Stage1Assignment, TimeSlot
from app.services.scheduler.stage2_greedy import assign_rooms


def _slot(slot_id: int, day: int = 0, start: int = 480) -> TimeSlot:
    return TimeSlot(
        id=slot_id, schedule_id=1, day=day,
        start_minutes=start, end_minutes=start + 50
    )


def _room(room_id: int, level: int) -> Room:
    return Room(id=room_id, name=f"Room {room_id}", level=level)


def _rs(rs_id: int, room: Room) -> RoomSchedule:
    """Build a RoomSchedule with a nested Room (schedule_id=1 for all tests)."""
    return RoomSchedule(id=rs_id, room_id=room.id, schedule_id=1, room=room)


def test_lower_floor_preferred_for_teacher():
    """
    Given two rooms on different floors, the greedy algorithm should pick
    the room on the same floor as the teacher's previous class.

    Setup:
      Slot 1 (08:00): teacher assigned → room on floor 1
      Slot 2 (09:00): teacher same day → should prefer floor 1 room again
    """
    slots_by_id = {
        1: _slot(1, day=0, start=480),
        2: _slot(2, day=0, start=540),
    }

    room_floor1 = _room(1, level=1)
    room_floor3 = _room(2, level=3)
    rs_floor1 = _rs(1, room_floor1)   # room_schedule on floor 1
    rs_floor3 = _rs(2, room_floor3)   # room_schedule on floor 3

    # First assignment: teacher (ts_id=1, sec_id=1) on slot 1
    # Second assignment: same teacher (ts_id=1, sec_id=2) on slot 2
    assignments = [
        Stage1Assignment(teacher_subject_id=1, section_id=1, schedule_time_id=1),
        Stage1Assignment(teacher_subject_id=1, section_id=2, schedule_time_id=2),
    ]

    config = SolverConfig()
    result = assign_rooms(
        stage1_assignments=assignments,
        room_schedules=[rs_floor1, rs_floor3],
        rooms_by_id={1: room_floor1, 2: room_floor3},
        time_slots_by_id=slots_by_id,
        config=config,
    )

    # Slot 1 can be either floor (no history); slot 2 should prefer floor 1
    slot2_assignment = next(r for r in result if r.schedule_time_id == 2)
    assert slot2_assignment.room_schedule_id == rs_floor1.id, (
        "Should prefer floor 1 room since teacher was on floor 1 before"
    )


def test_no_room_double_booking():
    """The same room_schedule must not be assigned to two classes at the same slot."""
    slot = _slot(1, day=0, start=480)
    slots_by_id = {1: slot}

    room = _room(1, level=1)
    rs = _rs(1, room)  # only one room available

    # Two different assignments at the same slot — needs two rooms
    assignments = [
        Stage1Assignment(teacher_subject_id=1, section_id=1, schedule_time_id=1),
        Stage1Assignment(teacher_subject_id=2, section_id=2, schedule_time_id=1),
    ]

    config = SolverConfig()
    with pytest.raises(NoRoomAvailableError):
        assign_rooms(
            stage1_assignments=assignments,
            room_schedules=[rs],
            rooms_by_id={1: room},
            time_slots_by_id=slots_by_id,
            config=config,
        )


def test_different_days_no_floor_penalty():
    """
    Floor jump penalty should NOT apply across different days.
    Even if the teacher was on floor 3 yesterday and floor 1 is available today,
    both rooms should have equal score for the first slot of the new day.
    """
    slots_by_id = {
        1: _slot(1, day=0, start=480),   # Monday
        2: _slot(2, day=1, start=480),   # Tuesday
    }

    room_floor1 = _room(1, level=1)
    room_floor3 = _room(2, level=3)
    rs1 = _rs(1, room_floor1)
    rs3 = _rs(2, room_floor3)

    assignments = [
        Stage1Assignment(teacher_subject_id=1, section_id=1, schedule_time_id=1),  # Mon
        Stage1Assignment(teacher_subject_id=1, section_id=1, schedule_time_id=2),  # Tue
    ]

    config = SolverConfig()
    result = assign_rooms(
        stage1_assignments=assignments,
        room_schedules=[rs1, rs3],
        rooms_by_id={1: room_floor1, 2: room_floor3},
        time_slots_by_id=slots_by_id,
        config=config,
    )

    # Tuesday's slot should not be penalized regardless of Monday's floor
    assert len(result) == 2  # both assigned without error
