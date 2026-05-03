"""
Transforms raw NestJS API data into the structured SolverInput that
Stage 1 (CP-SAT) and Stage 2 (greedy) consume.

Main responsibilities:
  1. Convert "HH:MM" time strings to integer minutes for solver arithmetic.
  2. Filter out break slots — breaks are not assignable class periods.
  3. Build required_pairs: for every (section, subject) declared in the request,
     find all TeacherSubject records that can cover it and emit one pair per option.
     The solver will pick exactly one teacher per (section, subject).
  4. Build fast-lookup indexes so the solver doesn't do O(n) scans.
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from app.schemas.nestjs import RoomSchedule, Room, Schedule, TeacherSubject
from app.schemas.solver import SolverInput, TimeSlot

log = structlog.get_logger()


# ── Time parsing ──────────────────────────────────────────────────────────────

def parse_time(t: str) -> int:
    """
    Convert a "HH:MM" string to integer minutes since midnight.

    Example:
        parse_time("08:30") → 510
        parse_time("13:00") → 780
    """
    hours, minutes = t.split(":")
    return int(hours) * 60 + int(minutes)


# ── Main builder ──────────────────────────────────────────────────────────────

def build_solver_input(
    teacher_subjects: list[TeacherSubject],
    room_schedules: list[RoomSchedule],
    rooms: list[Room],
    schedules: list[Schedule],
    section_assignments: list[dict],  # [{ "section_id": int, "subject_ids": [int] }]
) -> SolverInput:
    """
    Build a fully-indexed SolverInput from raw NestJS data and the user's
    section-subject assignment request.

    Args:
        teacher_subjects:    All TeacherSubject records from NestJS.
        room_schedules:      All RoomSchedule records (with nested room + schedule).
        rooms:               All Room records (for floor-level lookup in Stage 2).
        schedules:           All Schedule records with nested scheduleTimes[].
        section_assignments: From the API request body — which section needs
                             which subjects.

    Returns:
        A SolverInput ready to be handed to stage1_cpsat.solve_stage1().
    """

    # ── Step 1: Build all non-break TimeSlots ─────────────────────────────────
    # Flatten every ScheduleTime from every Schedule.
    # Break slots are excluded because they are not assignable teaching periods.
    all_slots: list[TimeSlot] = []
    for schedule in schedules:
        for st in schedule.scheduleTimes:
            if st.is_break:
                # Skip break periods — they exist only to show gaps in timetables
                continue
            all_slots.append(
                TimeSlot(
                    id=st.id,
                    schedule_id=st.schedule_id,
                    day=st.day,
                    start_minutes=parse_time(st.start_time),
                    end_minutes=parse_time(st.end_time),
                    is_break=False,
                )
            )

    slots_by_id: dict[int, TimeSlot] = {s.id: s for s in all_slots}
    log.info("data_loader.slots_built", total=len(all_slots))
    log.info("data_loader.teacher_subjects_received", total=len(teacher_subjects))
    log.info("data_loader.section_assignments_received", total=len(section_assignments))

    # ── Step 2: Index TeacherSubjects ─────────────────────────────────────────
    ts_by_id: dict[int, TeacherSubject] = {ts.id: ts for ts in teacher_subjects}

    # Map subject_id → list of TeacherSubject records that can teach it.
    # There may be multiple teachers qualified for the same subject.
    subject_to_ts: dict[int, list[TeacherSubject]] = defaultdict(list)
    for ts in teacher_subjects:
        subject_to_ts[ts.subject_id].append(ts)

    # ── Step 3: Build required_pairs and pair_units ───────────────────────────
    # For each (section, subject) we emit one pair per eligible teacher-subject.
    # The CP-SAT solver will choose exactly one teacher per (section, subject)
    # and assign it to exactly `units` different time slots (one per day).
    required_pairs: list[tuple[int, int]] = []  # (teacher_subject_id, section_id)
    pair_units: dict[tuple[int, int], int] = {}  # (ts_id, sec_id) → units

    for assignment in section_assignments:
        section_id: int = assignment["section_id"]
        for subj in assignment["subjects"]:
            subject_id: int = subj["subject_id"]
            units: int = max(1, subj.get("units", 1))
            eligible = subject_to_ts.get(subject_id, [])
            if not eligible:
                log.warning(
                    "data_loader.no_teacher_for_subject",
                    subject_id=subject_id,
                    section_id=section_id,
                )
                continue
            for ts in eligible:
                required_pairs.append((ts.id, section_id))
                pair_units[(ts.id, section_id)] = units

    no_teacher_count = sum(
        1 for a in section_assignments for s in a["subjects"]
        if not subject_to_ts.get(s["subject_id"])
    )
    log.info(
        "data_loader.required_pairs_built",
        total=len(required_pairs),
        subjects_with_no_teacher=no_teacher_count,
        unique_subject_ids_in_request=len({s["subject_id"] for a in section_assignments for s in a["subjects"]}),
        unique_subject_ids_in_ts=len({ts.subject_id for ts in teacher_subjects}),
    )

    # ── Step 4: Index RoomSchedules ───────────────────────────────────────────
    # Stage 2 needs: "given a time slot, which room_schedules are eligible?"
    # A RoomSchedule is eligible for a slot when rs.schedule_id == slot.schedule_id.
    rs_by_schedule_id: dict[int, list[RoomSchedule]] = defaultdict(list)
    for rs in room_schedules:
        rs_by_schedule_id[rs.schedule_id].append(rs)

    # ── Step 5: Index Rooms ───────────────────────────────────────────────────
    rooms_by_id: dict[int, Room] = {r.id: r for r in rooms}

    return SolverInput(
        time_slots=all_slots,
        time_slots_by_id=slots_by_id,
        teacher_subjects=teacher_subjects,
        ts_by_id=ts_by_id,
        room_schedules=room_schedules,
        rs_by_schedule_id=dict(rs_by_schedule_id),
        rooms_by_id=rooms_by_id,
        required_pairs=required_pairs,
        pair_units=pair_units,
    )
