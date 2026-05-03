"""
Conflict Service — detects scheduling conflicts in existing ClassGroup records.

A conflict exists when:
  - "teacher_double_booked": the same teacher is assigned to two class groups
    that share the same time slot.
  - "section_double_booked": the same section is assigned to two class groups
    that share the same time slot.

This is useful for auditing class groups that were created manually or imported
outside the scheduler engine.
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from app.models.responses import ConflictItem
from app.schemas.nestjs import ClassGroup, RoomSchedule, Schedule, TeacherSubject

log = structlog.get_logger()


def detect_conflicts(
    class_groups: list[ClassGroup],
    room_schedules: list[RoomSchedule],
    schedules: list[Schedule],
    teacher_subjects: list[TeacherSubject],
) -> list[ConflictItem]:
    """
    Scan all existing class groups for double-booking conflicts.

    Args:
        class_groups:    All ClassGroup records from NestJS.
        room_schedules:  All RoomSchedule records (to resolve slot times).
        schedules:       All Schedule records with nested scheduleTimes[].
        teacher_subjects: All TeacherSubject records (to resolve teacher_id).

    Returns:
        List of ConflictItem describing each conflict found.
    """

    # ── Build lookup indexes ───────────────────────────────────────────────────
    # room_schedule_id → set of schedule_time_ids (the slots that rs occupies)
    rs_to_slot_ids: dict[int, set[int]] = {}
    # schedule_id → set of schedule_time_ids
    schedule_to_slot_ids: dict[int, set[int]] = defaultdict(set)

    for schedule in schedules:
        for st in schedule.scheduleTimes:
            schedule_to_slot_ids[schedule.id].add(st.id)

    for rs in room_schedules:
        rs_to_slot_ids[rs.id] = schedule_to_slot_ids.get(rs.schedule_id, set())

    # teacher_subject_id → teacher_id
    ts_to_teacher: dict[int, int] = {ts.id: ts.teacher_id for ts in teacher_subjects}

    # ── Map entities to (class_group_id, slot_id) pairs ───────────────────────
    # teacher_id  → list of (cg_id, slot_id)
    teacher_slots: dict[int, list[tuple[int, int]]] = defaultdict(list)
    # section_id  → list of (cg_id, slot_id)
    section_slots: dict[int, list[tuple[int, int]]] = defaultdict(list)

    for cg in class_groups:
        if cg.teacher_subject_id is None or cg.room_schedule_id is None or cg.section_id is None:
            # Incomplete class group — skip
            continue

        teacher_id = ts_to_teacher.get(cg.teacher_subject_id)
        slot_ids = rs_to_slot_ids.get(cg.room_schedule_id, set())

        for slot_id in slot_ids:
            if teacher_id is not None:
                teacher_slots[teacher_id].append((cg.id, slot_id))
            section_slots[cg.section_id].append((cg.id, slot_id))

    conflicts: list[ConflictItem] = []

    # ── Check teacher double-bookings ─────────────────────────────────────────
    for teacher_id, entries in teacher_slots.items():
        # Group by slot_id; if any slot appears more than once → conflict
        slot_to_cg_ids: dict[int, list[int]] = defaultdict(list)
        for cg_id, slot_id in entries:
            slot_to_cg_ids[slot_id].append(cg_id)

        for slot_id, cg_ids in slot_to_cg_ids.items():
            if len(cg_ids) > 1:
                conflicts.append(
                    ConflictItem(
                        conflict_type="teacher_double_booked",
                        description=(
                            f"Teacher (teacher_id={teacher_id}) is assigned to "
                            f"{len(cg_ids)} classes at the same time slot "
                            f"(schedule_time_id={slot_id})."
                        ),
                        class_group_ids=cg_ids,
                    )
                )

    # ── Check section double-bookings ─────────────────────────────────────────
    for section_id, entries in section_slots.items():
        slot_to_cg_ids: dict[int, list[int]] = defaultdict(list)
        for cg_id, slot_id in entries:
            slot_to_cg_ids[slot_id].append(cg_id)

        for slot_id, cg_ids in slot_to_cg_ids.items():
            if len(cg_ids) > 1:
                conflicts.append(
                    ConflictItem(
                        conflict_type="section_double_booked",
                        description=(
                            f"Section (section_id={section_id}) is scheduled in "
                            f"{len(cg_ids)} classes at the same time slot "
                            f"(schedule_time_id={slot_id})."
                        ),
                        class_group_ids=cg_ids,
                    )
                )

    log.info("conflict_service.done", total_conflicts=len(conflicts))
    return conflicts
