"""
Stage 1 — CP-SAT Time-Slot Assignment

Uses Google OR-Tools CP-SAT to assign each (teacher_subject, section) pair
to exactly one time slot while optimizing for fairness.

HARD constraints (must always be satisfied):
  1. Each (teacher_subject, section) pair is assigned to exactly one slot.
  2. A teacher cannot be in two places at the same time slot.
  3. A section cannot be in two places at the same time slot.
  4. If multiple teachers can cover the same subject for the same section,
     exactly one of them is chosen (the others are left unassigned).

SOFT constraints (minimized as a weighted penalty sum):
  - teacher_gap:        idle minutes between a teacher's back-to-back classes per day
  - teacher_day_spread: total minutes from a teacher's first to last class per day
  - section_gap:        idle minutes between a section's back-to-back classes per day
  - section_day_spread: total minutes from a section's first to last class per day

The solver runs for at most `solver_time_limit_seconds` and returns the best
feasible solution found within that window.
"""

from __future__ import annotations

from collections import defaultdict

import structlog
from ortools.sat.python import cp_model

from app.core.exceptions import SolverInfeasibleError
from app.models.requests import SolverConfig
from app.schemas.solver import SolverInput, Stage1Assignment, TimeSlot

log = structlog.get_logger()


def solve_stage1(
    solver_input: SolverInput,
    config: SolverConfig,
) -> list[Stage1Assignment]:
    """
    Run the CP-SAT solver and return a list of time-slot assignments.

    Args:
        solver_input: Pre-indexed data from data_loader.build_solver_input().
        config:       Penalty weights and time limit from the API request.

    Returns:
        List of Stage1Assignment — one per required (teacher_subject, section) pair.

    Raises:
        SolverInfeasibleError: When no valid schedule exists within the constraints.
    """

    model = cp_model.CpModel()
    penalties = config.penalties
    slots: list[TimeSlot] = solver_input.time_slots
    num_slots = len(slots)

    # Map slot list index → TimeSlot for O(1) access inside loops
    slot_by_index: dict[int, TimeSlot] = {i: s for i, s in enumerate(slots)}

    # ── Decision variables ────────────────────────────────────────────────────
    # x[(ts_id, sec_id, slot_idx)] = 1 if teacher_subject ts_id teaches
    # section sec_id during slot at index slot_idx, else 0.
    x: dict[tuple[int, int, int], cp_model.IntVar] = {}
    for (ts_id, sec_id) in solver_input.required_pairs:
        for si in range(num_slots):
            x[(ts_id, sec_id, si)] = model.new_bool_var(
                f"x_ts{ts_id}_sec{sec_id}_slot{si}"
            )

    # ── Build pair_units lookup ───────────────────────────────────────────────
    # (ts_id, sec_id) → how many distinct-day slots must be assigned (= credit units)
    pair_units = solver_input.pair_units

    # ── Group pairs by teacher and section for constraint loops ───────────────
    # teacher_id → list of (ts_id, sec_id) pairs that involve that teacher
    teacher_to_pairs: dict[int, list[tuple[int, int]]] = defaultdict(list)
    # section_id → list of (ts_id, sec_id) pairs that involve that section
    section_to_pairs: dict[int, list[tuple[int, int]]] = defaultdict(list)
    # (section_id, subject_id) → list of (ts_id, sec_id) options for that subject
    # Used to enforce "pick exactly one teacher per section-subject"
    sec_subj_to_pairs: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)

    for (ts_id, sec_id) in solver_input.required_pairs:
        ts = solver_input.ts_by_id[ts_id]
        teacher_to_pairs[ts.teacher_id].append((ts_id, sec_id))
        section_to_pairs[sec_id].append((ts_id, sec_id))
        sec_subj_to_pairs[(sec_id, ts.subject_id)].append((ts_id, sec_id))

    # Pairs that are handled by constraint 4 (multi-teacher groups) — skip in C1
    multi_teacher_pairs: set[tuple[int, int]] = set()
    for pairs in sec_subj_to_pairs.values():
        if len(pairs) > 1:
            multi_teacher_pairs.update(pairs)

    # Group slots by day (needed for the one-slot-per-day constraint)
    slots_by_day_early: dict[int, list[tuple[int, TimeSlot]]] = defaultdict(list)
    for si, slot in slot_by_index.items():
        slots_by_day_early[slot.day].append((si, slot))

    # ── HARD CONSTRAINT 1: Each single-teacher pair assigned exactly N slots ──
    # For pairs with only one eligible teacher: sum of assigned slots == units.
    for (ts_id, sec_id) in solver_input.required_pairs:
        if (ts_id, sec_id) in multi_teacher_pairs:
            continue  # handled by constraint 4
        units = pair_units.get((ts_id, sec_id), 1)
        model.add(
            sum(x[(ts_id, sec_id, si)] for si in range(num_slots)) == units
        )

    # ── HARD CONSTRAINT 1b: At most one slot per day per pair ─────────────────
    # A subject may only be taught once per day regardless of unit count.
    for (ts_id, sec_id) in solver_input.required_pairs:
        for day_slots in slots_by_day_early.values():
            day_indices = [si for (si, _) in day_slots]
            model.add(
                sum(x[(ts_id, sec_id, si)] for si in day_indices) <= 1
            )

    # ── HARD CONSTRAINT 2: One teacher per slot ───────────────────────────────
    # A teacher cannot teach two classes simultaneously.
    for teacher_id, pairs in teacher_to_pairs.items():
        for si in range(num_slots):
            model.add(sum(x[(ts_id, sec_id, si)] for (ts_id, sec_id) in pairs) <= 1)

    # ── HARD CONSTRAINT 3: One class per section per slot ────────────────────
    # A section of students cannot be in two places at once.
    for sec_id, pairs in section_to_pairs.items():
        for si in range(num_slots):
            model.add(sum(x[(ts_id, sec_id, si)] for (ts_id, sec_id) in pairs) <= 1)

    # ── HARD CONSTRAINT 4: Exactly one teacher per (section, subject) ─────────
    # When multiple teachers can cover the same subject for the same section,
    # select exactly one teacher; that teacher must cover all N units.
    for (sec_id, subj_id), pairs in sec_subj_to_pairs.items():
        if len(pairs) <= 1:
            continue

        # One binary variable per teacher option indicating whether they are chosen
        chosen: dict[tuple[int, int], cp_model.IntVar] = {
            (ts_id, sec_id): model.new_bool_var(f"chosen_ts{ts_id}_sec{sec_id}")
            for (ts_id, sec_id) in pairs
        }
        model.add_exactly_one(chosen.values())

        for (ts_id, sec_id) in pairs:
            units = pair_units.get((ts_id, sec_id), 1)
            total = sum(x[(ts_id, sec_id, si)] for si in range(num_slots))
            # Chosen teacher teaches exactly `units` slots; others teach 0
            model.add(total == units).only_enforce_if(chosen[(ts_id, sec_id)])
            model.add(total == 0).only_enforce_if(chosen[(ts_id, sec_id)].Not())

    # ── HARD CONSTRAINT 5: Don't exceed available rooms per slot ─────────────
    # For each time slot, simultaneous classes <= rooms available for that
    # slot's schedule.  Without this, Stage 2 (greedy room assignment) would
    # run out of rooms and crash.
    rooms_per_schedule: dict[int, int] = {
        sched_id: len(rs_list)
        for sched_id, rs_list in solver_input.rs_by_schedule_id.items()
    }
    for si in range(num_slots):
        slot = slot_by_index[si]
        cap = rooms_per_schedule.get(slot.schedule_id, 0)
        if cap == 0:
            # No rooms on this schedule — nothing can be assigned here
            model.add(
                sum(x[(ts_id, sec_id, si)] for (ts_id, sec_id) in solver_input.required_pairs
                    if (ts_id, sec_id, si) in x) == 0
            )
        else:
            model.add(
                sum(x[(ts_id, sec_id, si)] for (ts_id, sec_id) in solver_input.required_pairs
                    if (ts_id, sec_id, si) in x) <= cap
            )

    # ── SOFT CONSTRAINTS — penalty term collection ────────────────────────────
    # CP-SAT minimizes integer costs, so we accumulate weighted penalty terms
    # and pass them to model.minimize() at the end.
    penalty_terms: list[cp_model.LinearExprT] = []

    # Group slots by day for intra-day penalty calculations
    # day → sorted list of (slot_index, TimeSlot) by start_minutes
    slots_by_day: dict[int, list[tuple[int, TimeSlot]]] = defaultdict(list)
    for si, slot in slot_by_index.items():
        slots_by_day[slot.day].append((si, slot))
    for day in slots_by_day:
        slots_by_day[day].sort(key=lambda t: t[1].start_minutes)

    def _add_gap_and_spread_penalties(
        entity_to_pairs: dict[int, list[tuple[int, int]]],
        gap_weight: int,
        spread_weight: int,
        entity_label: str,
    ) -> None:
        # Skip entirely when both weights are 0 — no variables or constraints needed
        if gap_weight == 0 and spread_weight == 0:
            return
        """
        Add gap and day-spread penalties for either teachers or sections.

        Gap penalty:
            For each consecutive pair of slots (a, b) on the same day,
            if both are assigned to the same entity, penalize by:
                gap_weight × (b.start_minutes - a.end_minutes)
            This discourages idle time between classes.

        Day-spread penalty:
            For each pair of slots (first, last) on the same day,
            if both are assigned to the same entity, penalize by:
                spread_weight × (last.end_minutes - first.start_minutes)
            This discourages schedules that span very early to very late.

        The product of two boolean variables (both_assigned) is linearized
        using a standard auxiliary boolean:
            both = new_bool_var()
            add(x[a] + x[b] >= 2).only_enforce_if(both)
            add(x[a] + x[b] <= 1).only_enforce_if(both.Not())
        """
        for entity_id, pairs in entity_to_pairs.items():
            for day, day_slots in slots_by_day.items():
                # Slot indices that belong to this entity on this day
                entity_day_slot_indices = [si for (si, _) in day_slots]

                # --- Gap penalties between consecutive slots ---
                for k in range(len(day_slots) - 1):
                    si_a, slot_a = day_slots[k]
                    si_b, slot_b = day_slots[k + 1]
                    gap_minutes = slot_b.start_minutes - slot_a.end_minutes

                    if gap_minutes <= 0:
                        # Slots are back-to-back or overlap — no gap to penalize
                        continue

                    # Sum of x values across all pairs for this entity at slot_a
                    assigned_a = sum(
                        x[(ts_id, sec_id, si_a)]
                        for (ts_id, sec_id) in pairs
                        if (ts_id, sec_id, si_a) in x
                    )
                    assigned_b = sum(
                        x[(ts_id, sec_id, si_b)]
                        for (ts_id, sec_id) in pairs
                        if (ts_id, sec_id, si_b) in x
                    )

                    # Auxiliary bool: both slots are assigned to this entity
                    both = model.new_bool_var(
                        f"gap_{entity_label}{entity_id}_d{day}_s{si_a}_{si_b}"
                    )
                    # both == 1 only when assigned_a >= 1 AND assigned_b >= 1
                    model.add(assigned_a >= 1).only_enforce_if(both)
                    model.add(assigned_b >= 1).only_enforce_if(both)
                    model.add(assigned_a + assigned_b <= 1).only_enforce_if(both.Not())

                    penalty_terms.append(gap_weight * gap_minutes * both)

                # --- Day-spread penalty (first slot to last slot on a day) ---
                if len(day_slots) < 2:
                    continue

                si_first, slot_first = day_slots[0]
                si_last, slot_last = day_slots[-1]
                spread_minutes = slot_last.end_minutes - slot_first.start_minutes

                assigned_first = sum(
                    x[(ts_id, sec_id, si_first)]
                    for (ts_id, sec_id) in pairs
                    if (ts_id, sec_id, si_first) in x
                )
                assigned_last = sum(
                    x[(ts_id, sec_id, si_last)]
                    for (ts_id, sec_id) in pairs
                    if (ts_id, sec_id, si_last) in x
                )

                both_ends = model.new_bool_var(
                    f"spread_{entity_label}{entity_id}_d{day}"
                )
                model.add(assigned_first >= 1).only_enforce_if(both_ends)
                model.add(assigned_last >= 1).only_enforce_if(both_ends)
                model.add(assigned_first + assigned_last <= 1).only_enforce_if(
                    both_ends.Not()
                )

                penalty_terms.append(spread_weight * spread_minutes * both_ends)

    # Apply gap + spread penalties for teachers
    _add_gap_and_spread_penalties(
        teacher_to_pairs,
        gap_weight=penalties.teacher_gap,
        spread_weight=penalties.teacher_day_spread,
        entity_label="t",
    )

    # Apply gap + spread penalties for sections (students)
    _add_gap_and_spread_penalties(
        section_to_pairs,
        gap_weight=penalties.section_gap,
        spread_weight=penalties.section_day_spread,
        entity_label="s",
    )

    # ── Built-in morning preference ───────────────────────────────────────────
    # Penalize late-slot assignments with a QUADRATIC cost so that afternoon
    # slots are exponentially more expensive than morning ones.
    # A class at 8am (60 min late) costs 60² = 3,600 pts.
    # A class at 1pm (360 min late) costs 360² = 129,600 pts — ~36× worse.
    # This steep curve forces CP-SAT to pack ALL feasible classes into the
    # morning before spilling into the afternoon, so teachers finish earlier.
    earliest_start = min(s.start_minutes for s in slots) if slots else 420
    for (ts_id, sec_id) in solver_input.required_pairs:
        for si in range(num_slots):
            late_minutes = slot_by_index[si].start_minutes - earliest_start
            if late_minutes > 0:
                penalty_terms.append(late_minutes * late_minutes * x[(ts_id, sec_id, si)])

    # ── Objective: minimize total penalty ─────────────────────────────────────
    if penalty_terms:
        model.minimize(sum(penalty_terms))

    # ── Solve ─────────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.max_time_in_seconds = config.solver_time_limit_seconds
    solver.parameters.log_search_progress = True

    log.info(
        "stage1.solving",
        num_pairs=len(solver_input.required_pairs),
        num_slots=num_slots,
        time_limit=config.solver_time_limit_seconds,
    )

    status = solver.solve(model)

    log.info(
        "stage1.solver_status",
        status=solver.status_name(status),
        wall_time=round(solver.wall_time, 2),
    )

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        is_infeasible = status == cp_model.INFEASIBLE
        raise SolverInfeasibleError(
            ("CP-SAT proved no valid schedule exists (INFEASIBLE). "
             "Check teacher capacity and room availability."
             if is_infeasible else
             f"CP-SAT could not find a valid schedule within the time limit ({config.solver_time_limit_seconds}s). "
             "Try increasing the time limit or reducing the number of required pairs.")
        )

    log.info(
        "stage1.done",
        status=solver.status_name(status),
        objective=solver.objective_value,
        wall_time=round(solver.wall_time, 2),
    )

    # ── Extract assignments ───────────────────────────────────────────────────
    assignments: list[Stage1Assignment] = []
    for (ts_id, sec_id, si), var in x.items():
        if solver.value(var) == 1:
            assignments.append(
                Stage1Assignment(
                    teacher_subject_id=ts_id,
                    section_id=sec_id,
                    schedule_time_id=slot_by_index[si].id,
                )
            )

    log.info("stage1.assignments_extracted", total=len(assignments))
    return assignments
