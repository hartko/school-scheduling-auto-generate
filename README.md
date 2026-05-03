# School Scheduling Engine

A FastAPI service that auto-generates conflict-free, fair class group assignments for a school using a two-stage solver: **CP-SAT** (Google OR-Tools) for time-slot assignment and a **greedy** algorithm for room assignment.

---

## Tech Stack

| | |
|---|---|
| **Framework** | FastAPI 0.115+ |
| **Solver** | Google OR-Tools CP-SAT (ortools 9.11+) |
| **HTTP client** | httpx (async, calls the NestJS backend) |
| **Validation** | Pydantic v2, pydantic-settings |
| **Logging** | structlog (structured JSON logs) |
| **Runtime** | Python 3.12+ |
| **Package manager** | uv |

---

## Setup

### 1. Install dependencies

```bash
# with uv (recommended)
uv sync

# or with pip
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e .
```

### 2. Configure environment

Copy `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

```env
# URL of the NestJS backend
NESTJS_BASE_URL=http://localhost:4000

# Records per page when paginating NestJS endpoints
NESTJS_PAGE_SIZE=100

# HTTP timeout per request (seconds)
NESTJS_TIMEOUT_SECONDS=30.0

# Retries on transient NestJS errors (exponential back-off: 1s, 2s, 4s…)
NESTJS_MAX_RETRIES=3

# CP-SAT solver wall-clock time limit (seconds)
DEFAULT_SOLVER_TIME_LIMIT_SECONDS=300

# Log level: DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO
```

All settings have defaults — the app runs without a `.env` file in development.

### 3. Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

- **Swagger UI** → http://localhost:8000/docs
- **ReDoc** → http://localhost:8000/redoc

---

## API

All routes are prefixed with `/api/v1/scheduler`.

### `POST /generate`

Starts the two-stage solver as a background task. Returns a `job_id` immediately.

**Request body:**
```json
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
    "penalties": {
      "teacher_gap": 0,
      "teacher_day_spread": 0,
      "teacher_floor_jump": 0,
      "section_gap": 0,
      "section_day_spread": 0,
      "section_floor_jump": 0
    },
    "solver_time_limit_seconds": 300
  }
}
```

**Response:**
```json
{ "job_id": "abc123", "status": "pending", "progress": 0 }
```

---

### `GET /jobs/{job_id}`

Poll job progress. `progress` is 0–100. `status` is one of `pending | running | done | failed`.

---

### `GET /jobs/{job_id}/result`

Returns the proposed class groups once the job is `done`.

```json
{
  "job_id": "abc123",
  "status": "done",
  "total": 915,
  "class_groups": [
    {
      "teacher_subject_id": 12,
      "section_id": 3,
      "room_schedule_id": 7,
      "schedule_time_id": 42
    }
  ]
}
```

---

### `POST /jobs/{job_id}/commit`

Saves all proposed class groups to the NestJS backend in a single bulk request.

```json
{ "job_id": "abc123", "committed": 915, "failed": 0, "errors": [] }
```

---

### `DELETE /jobs/{job_id}`

Removes a job and its result from the in-memory store.

---

### `GET /conflicts`

Fetches all existing class groups from NestJS and reports any teacher or section double-bookings.

---

## How the Solver Works

### Stage 1 — CP-SAT (Time-Slot Assignment)

**Hard constraints (must always hold):**

| # | Constraint |
|---|---|
| 1 | Each `(teacher_subject, section)` pair is assigned to exactly `N` time slots, one per day (N = credit units) |
| 1b | A subject may be taught at most once per day per section |
| 2 | A teacher cannot teach two classes at the same time slot |
| 3 | A section cannot be in two places at the same time slot |
| 4 | When multiple teachers can cover the same subject for the same section, exactly one teacher is chosen |
| 5 | Simultaneous classes per slot cannot exceed the number of rooms available for that schedule |

**Soft objective (always active):**

- **Morning preference** — each assignment is penalised by `(slot.start_minutes − earliest_start)` minutes. The solver minimises total late-minutes, naturally clustering classes into early morning slots so teachers finish earlier.

**Optional soft penalties (default: all 0):**

| Penalty | Effect |
|---|---|
| `teacher_gap` | Penalises idle minutes between a teacher's back-to-back classes per day |
| `teacher_day_spread` | Penalises a wide first-to-last class window for a teacher |
| `section_gap` | Same as `teacher_gap` but for student sections |
| `section_day_spread` | Same as `teacher_day_spread` but for sections |

---

### Stage 2 — Greedy (Room Assignment)

Processes time-slot assignments chronologically (earliest slot first):

1. Find all **eligible** room-schedules: linked to the same schedule as the slot, and not already used at this slot.
2. Score each eligible room: `teacher_floor_jump_weight × |Δfloor|  +  section_floor_jump_weight × |Δfloor|`
3. **Round-robin tiebreaker** — when scores tie (e.g. all weights = 0), prefer the room with the fewest total assignments so far. This spreads classes evenly across all rooms before any room gets a second batch.
4. Record the assignment and update tracking state.

---

## Project Structure

```
app/
├── main.py                        # FastAPI app factory, CORS, lifespan
├── api/
│   └── v1/
│       ├── router.py
│       └── endpoints/
│           └── scheduler.py       # All HTTP route handlers
├── clients/
│   └── nestjs_client.py           # Async httpx client for the NestJS API
├── core/
│   ├── config.py                  # Settings (pydantic-settings, reads .env)
│   ├── dependencies.py            # FastAPI Depends() providers
│   ├── exceptions.py              # Custom exceptions + handlers
│   └── logging.py                 # structlog configuration
├── models/
│   ├── requests.py                # GenerateRequest, SolverConfig, PenaltyConfig
│   └── responses.py               # JobResponse, ResultResponse, CommitResponse
├── repositories/
│   └── job_repository.py          # In-memory job store (thread-safe)
├── schemas/
│   ├── nestjs.py                  # Pydantic models for NestJS API responses
│   └── solver.py                  # Internal solver data types (SolverInput, etc.)
├── services/
│   ├── scheduler_service.py       # Orchestrates data loading + stage 1 + stage 2
│   ├── conflict_service.py        # Post-hoc conflict detection
│   └── scheduler/
│       ├── data_loader.py         # Transforms NestJS data → SolverInput
│       ├── stage1_cpsat.py        # CP-SAT time-slot assignment
│       └── stage2_greedy.py       # Greedy room assignment
└── workers/
    └── solver_task.py             # Background task that runs the full solver pipeline
```

---

## Running Tests

```bash
pytest
```

Test layout:

```
tests/
├── unit/
│   ├── test_data_loader.py
│   ├── test_stage1_cpsat.py
│   └── test_stage2_greedy.py
└── integration/
    ├── test_scheduler_api.py
    └── test_nestjs_client.py
```

---

## Docker

```bash
docker compose up --build
```

The `docker-compose.yml` starts the engine on port 8000. Set `NESTJS_BASE_URL` to point at your NestJS container or host.
