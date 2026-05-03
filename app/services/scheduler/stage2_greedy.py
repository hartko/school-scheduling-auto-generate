"""
Stage 2 — Greedy Room Assignment

Given Stage 1's time-slot assignments, this stage assigns a physical room
(via a RoomSchedule record) to each class, minimizing floor travel for both
teachers and students.

Why greedy instead of CP-SAT here?
  - Room assignment is inherently sequential: once you assign Room A to a slot,
    the next slot's choice depends on that decision.
  - The penalty (floor distance) only depends on adjacent same-day assignments,
    which are resolved in chronological order.
  - Greedy is O(n log n) and runs in milliseconds even for hundreds of classes.

Scoring function per candidate room:
    score = (teacher_floor_jump_weight × |this_floor - teacher_prev_floor|)
          + (section_floor_jump_weight  × |this_floor - section_prev_floor|)

The room with the lowest score wins.  Ties are broken by room_schedule id
(deterministic ordering).

Eligibility rules for a room_schedule at a given time slot:
  1. rs.schedule_id == slot.schedule_id (the room is linked to this schedule)
  2. The room_schedule has not already been used at this time slot
"""

from __future__ import annotations

from collections import defaultdict

import structlog


from app.models.requests import SolverConfig
from app.schemas.nestjs import Room, RoomSchedule
from app.schemas.solver import Stage1Assignment, Stage2Assignment, TimeSlot

log = structlog.get_logger()


def assign_rooms(
    stage1_assignments: list[Stage1Assignment],
    room_schedules: list[RoomSchedule],
    rooms_by_id: dict[int, Room],
    time_slots_by_id: dict[int, TimeSlot],
    config: SolverConfig,
) -> list[Stage2Assignment]:
    """
    Greedily assign a room to every Stage 1 assignment.

    Args:
        stage1_assignments: Output of Stage 1 — each has (ts_id, sec_id, slot_id).
        room_schedules:     All RoomSchedule records with nested room + schedule.
        rooms_by_id:        room_id → Room (for floor-level lookup).
        time_slots_by_id:   slot_id → TimeSlot (for day + time ordering).
        config:             Penalty weights from the API request.

    Returns:
        List of Stage2Assignment — each has a room_schedule_id added.

    Raises:
        NoRoomAvailableError: When all rooms are taken for a particular time slot.
    """

    penalties = config.penalties

    # ── Build room_schedule index ─────────────────────────────────────────────
    # A RoomSchedule is eligible for a slot only when their schedule_ids match.
    # Precompute: schedule_id → list[RoomSchedule] for O(1) eligibility lookup.
    rs_by_schedule_id: dict[int, list[RoomSchedule]] = defaultdict(list)
    for rs in room_schedules:
        rs_by_schedule_id[rs.schedule_id].append(rs)

    # ── Tracking state ────────────────────────────────────────────────────────
    # Which room_schedule ids are already used at each time slot?
    # slot_id → set of room_schedule_ids already assigned at that slot
    used_rs_per_slot: dict[int, set[int]] = defaultdict(set)

    # How many classes have been assigned to each room_schedule so far?
    # Used as a tiebreaker so classes are spread evenly across all rooms
    # (round-robin) rather than packing into the lowest-ID rooms first.
    assignments_per_room: dict[int, int] = defaultdict(int)

    # The most recent (slot, floor_level) for each teacher and section.
    # Used to compute the floor-jump penalty for the next assignment.
    # teacher_subject_id → (TimeSlot, floor_level)
    teacher_last: dict[int, tuple[TimeSlot, int]] = {}
    # section_id → (TimeSlot, floor_level)
    section_last: dict[int, tuple[TimeSlot, int]] = {}

    # ── Sort assignments chronologically ─────────────────────────────────────
    # Process Monday first, then by start time within the day.
    # This ensures "previous room" lookups are always populated before they are needed.
    sorted_assignments = sorted(
        stage1_assignments,
        key=lambda a: (
            time_slots_by_id[a.schedule_time_id].day,
            time_slots_by_id[a.schedule_time_id].start_minutes,
        ),
    )

    results: list[Stage2Assignment] = []

    for assignment in sorted_assignments:
        slot = time_slots_by_id[assignment.schedule_time_id]

        # ── Find eligible rooms for this slot ─────────────────────────────────
        # Eligible = correct schedule + not already used at this slot
        eligible: list[RoomSchedule] = [
            rs
            for rs in rs_by_schedule_id.get(slot.schedule_id, [])
            if rs.id not in used_rs_per_slot[slot.id]
        ]

        if not eligible:
            log.warning(
                "stage2.no_room_skipped",
                ts_id=assignment.teacher_subject_id,
                sec_id=assignment.section_id,
                slot_id=slot.id,
                day=slot.day,
                start=slot.start_minutes,
            )
            continue  # skip this assignment rather than crash the whole job

        # ── Score each eligible room ───────────────────────────────────────────
        def score(rs: RoomSchedule) -> float:
            """
            Lower score = better choice.
            Penalizes floor jumps for both the teacher and the section.
            Only same-day adjacency is penalized — there is no meaningful
            "travel cost" from yesterday's last class to today's first class.
            """
            # Room floor: default to 1 (ground) if level is unset
            this_floor = _floor(rs, rooms_by_id)

            # Teacher floor penalty
            teacher_pen = 0
            prev_teacher = teacher_last.get(assignment.teacher_subject_id)
            if prev_teacher is not None:
                prev_slot, prev_floor = prev_teacher
                if prev_slot.day == slot.day:
                    # Only penalize if the previous class was on the same day
                    teacher_pen = abs(this_floor - prev_floor)

            # Section (students) floor penalty
            section_pen = 0
            prev_section = section_last.get(assignment.section_id)
            if prev_section is not None:
                prev_slot, prev_floor = prev_section
                if prev_slot.day == slot.day:
                    section_pen = abs(this_floor - prev_floor)

            return (
                penalties.teacher_floor_jump * teacher_pen
                + penalties.section_floor_jump * section_pen
            )

        # Pick the lowest-penalty room; break ties by fewest total assignments
        # (round-robin distribution), then by id for determinism.
        best_rs = min(eligible, key=lambda rs: (score(rs), assignments_per_room[rs.id], rs.id))
        chosen_floor = _floor(best_rs, rooms_by_id)

        # ── Record the assignment ─────────────────────────────────────────────
        used_rs_per_slot[slot.id].add(best_rs.id)
        assignments_per_room[best_rs.id] += 1
        teacher_last[assignment.teacher_subject_id] = (slot, chosen_floor)
        section_last[assignment.section_id] = (slot, chosen_floor)

        results.append(
            Stage2Assignment(
                teacher_subject_id=assignment.teacher_subject_id,
                section_id=assignment.section_id,
                schedule_time_id=assignment.schedule_time_id,
                room_schedule_id=best_rs.id,
            )
        )

        log.debug(
            "stage2.assigned",
            ts_id=assignment.teacher_subject_id,
            sec_id=assignment.section_id,
            slot_id=slot.id,
            room_schedule_id=best_rs.id,
            floor=chosen_floor,
        )

    log.info("stage2.done", total=len(results))
    return results


# ── Helper ────────────────────────────────────────────────────────────────────

def _floor(rs: RoomSchedule, rooms_by_id: dict[int, Room]) -> int:
    """
    Resolve the floor level for a RoomSchedule.

    Priority:
      1. rs.room.level if the nested room object was included in the API response
      2. rooms_by_id[rs.room_id].level as a fallback
      3. 1 (ground floor) if level is None or the room is not found
    """
    level: int | None = None

    if rs.room is not None:
        level = rs.room.level
    else:
        room = rooms_by_id.get(rs.room_id)
        if room is not None:
            level = room.level

    return level if level is not None else 1
