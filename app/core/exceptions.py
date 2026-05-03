"""
Custom exception classes and FastAPI exception handlers.
All domain errors should raise one of these so the API returns consistent JSON bodies.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# ── Domain exceptions ─────────────────────────────────────────────────────────

class JobNotFoundError(Exception):
    """Raised when a job_id does not exist in the in-memory store."""
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Job '{job_id}' not found")


class JobNotReadyError(Exception):
    """Raised when trying to read/commit a job that has not finished yet."""
    def __init__(self, job_id: str, status: str) -> None:
        self.job_id = job_id
        self.status = status
        super().__init__(f"Job '{job_id}' is not done (current status: {status})")


class NestJSClientError(Exception):
    """Raised when all retries to the NestJS API have been exhausted."""


class SolverInfeasibleError(Exception):
    """Raised when the CP-SAT solver cannot find any valid schedule."""


class NoRoomAvailableError(Exception):
    """Raised when Stage 2 cannot find a free room for a time slot."""


# ── FastAPI exception handlers ────────────────────────────────────────────────

def register_exception_handlers(app: FastAPI) -> None:
    """Attach all custom exception handlers to the FastAPI app."""

    @app.exception_handler(JobNotFoundError)
    async def job_not_found_handler(request: Request, exc: JobNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(JobNotReadyError)
    async def job_not_ready_handler(request: Request, exc: JobNotReadyError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NestJSClientError)
    async def nestjs_client_handler(request: Request, exc: NestJSClientError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(SolverInfeasibleError)
    async def solver_infeasible_handler(request: Request, exc: SolverInfeasibleError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
