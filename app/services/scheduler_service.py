"""
Scheduler Service — orchestrates the full solve pipeline.

Flow:
  1. Fetch all required data from NestJS (teachers, rooms, schedules, etc.)
  2. Build a SolverInput via the data loader
  3. Run Stage 1 (CP-SAT) to assign time slots
  4. Run Stage 2 (greedy) to assign rooms
  5. Return Stage2Assignments ready to be stored or committed
"""

from __future__ import annotations

import asyncio
import structlog

from app.clients.nestjs_client import NestJSClient
from app.models.requests import GenerateRequest
from app.schemas.solver import Stage2Assignment
from app.services.scheduler.data_loader import build_solver_input
from app.services.scheduler.stage1_cpsat import solve_stage1
from app.services.scheduler.stage2_greedy import assign_rooms

log = structlog.get_logger()


async def run_scheduler(
    request: GenerateRequest,
    progress_callback,   # callable(progress: int, message: str)
) -> list[Stage2Assignment]:
    """
    Execute the full two-stage scheduling pipeline.

    Args:
        request:           The validated API request (assignments + config).
        progress_callback: Called at each stage to update the job's progress
                           field in the job repository (0-100).

    Returns:
        List of Stage2Assignment — one per class group to be created.
    """

    async with NestJSClient() as client:
        # ── Fetch all data from NestJS ────────────────────────────────────────
        await progress_callback(10, "Fetching teachers and subjects")
        teacher_subjects = await client.get_teacher_subjects()

        await progress_callback(20, "Fetching rooms and schedules")
        room_schedules = await client.get_room_schedules()
        rooms = await client.get_rooms()
        schedules = await client.get_schedules()

        log.info(
            "scheduler.data_fetched",
            teacher_subjects=len(teacher_subjects),
            room_schedules=len(room_schedules),
            rooms=len(rooms),
            schedules=len(schedules),
        )

    # ── Build solver input ────────────────────────────────────────────────────
    await progress_callback(25, "Building solver input")
    solver_input = build_solver_input(
        teacher_subjects=teacher_subjects,
        room_schedules=room_schedules,
        rooms=rooms,
        schedules=schedules,
        section_assignments=[a.model_dump() for a in request.assignments],
    )

    log.info(
        "scheduler.solver_input_ready",
        required_pairs=len(solver_input.required_pairs),
        time_slots=len(solver_input.time_slots),
    )

    # ── Stage 1: assign time slots (CP-SAT) ───────────────────────────────────
    # CP-SAT is CPU-bound — run it in a thread pool so the asyncio event loop
    # remains responsive while the solver works (which can take up to ~60s).
    await progress_callback(30, "Running Stage 1: assigning time slots (this may take a minute)")
    loop = asyncio.get_event_loop()
    stage1_result = await loop.run_in_executor(
        None,  # use the default ThreadPoolExecutor
        lambda: solve_stage1(solver_input, request.config),
    )

    log.info("scheduler.stage1_complete", assignments=len(stage1_result))

    # ── Stage 2: assign rooms (greedy) ────────────────────────────────────────
    await progress_callback(85, "Running Stage 2: assigning rooms")
    stage2_result = assign_rooms(
        stage1_assignments=stage1_result,
        room_schedules=room_schedules,
        rooms_by_id=solver_input.rooms_by_id,
        time_slots_by_id=solver_input.time_slots_by_id,
        config=request.config,
    )

    log.info("scheduler.stage2_complete", assignments=len(stage2_result))
    return stage2_result
