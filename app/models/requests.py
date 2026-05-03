"""
Pydantic models for incoming API request bodies.
"""

from pydantic import BaseModel, Field
from app.core.config import settings


class PenaltyConfig(BaseModel):
    """
    Weights that control how strongly each fairness goal is optimized.
    Higher value = solver tries harder to avoid that situation.

    Stage 1 penalties (time-slot assignment):
      - teacher_gap:        penalizes idle time between a teacher's classes on the same day
      - teacher_day_spread: penalizes a wide first-to-last class window for a teacher
      - section_gap:        same as teacher_gap but for student sections
      - section_day_spread: same as teacher_day_spread but for sections

    Stage 2 penalties (room assignment):
      - teacher_floor_jump: penalizes moving a teacher between floors between classes
      - section_floor_jump: penalizes moving students between floors (weighted higher
                            because 30+ students carry bags and climb stairs together)
    """
    teacher_gap: int = 0
    teacher_day_spread: int = 0
    teacher_floor_jump: int = 0
    section_gap: int = 0
    section_day_spread: int = 0
    section_floor_jump: int = 0


class SolverConfig(BaseModel):
    """Tuning parameters forwarded to the solver."""
    penalties: PenaltyConfig = Field(default_factory=PenaltyConfig)
    # Wall-clock seconds the CP-SAT solver may run before returning best found
    solver_time_limit_seconds: int = Field(
        default_factory=lambda: settings.default_solver_time_limit_seconds
    )


class SubjectWithUnits(BaseModel):
    """A subject with its credit unit count for a specific section."""
    subject_id: int
    units: int = Field(default=1, ge=1, description="Credit units; 1 unit = 1 hour/day, so 3 units → assigned on 3 different days")


class SectionAssignment(BaseModel):
    """
    Declares that a given section must be taught the listed subjects.
    The solver will pick appropriate teacher-subject records for each subject
    and assign it to exactly `units` different time slots (one per day).
    """
    section_id: int
    subjects: list[SubjectWithUnits]


class GenerateRequest(BaseModel):
    """
    Request body for POST /scheduler/generate.

    Example:
        {
            "assignments": [
                {
                    "section_id": 1,
                    "subjects": [
                        { "subject_id": 2, "units": 3 },
                        { "subject_id": 5, "units": 1 }
                    ]
                }
            ],
            "config": {
                "penalties": { "section_floor_jump": 12 },
                "solver_time_limit_seconds": 90
            }
        }
    """
    assignments: list[SectionAssignment]
    config: SolverConfig = Field(default_factory=SolverConfig)
