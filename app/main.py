"""
FastAPI application factory.

Startup (lifespan):
  - Configures structured logging
  - Creates the in-memory job repository and attaches it to app.state
    so every request handler can access it via Depends(get_job_repo)

The app exposes:
  - All scheduler routes under /api/v1/scheduler/...
  - Swagger UI at /docs
  - ReDoc at /redoc
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.repositories.job_repository import JobRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once at startup and once at shutdown.
    We initialise shared resources here so they are available for the full
    lifetime of the application (not re-created per request).
    """
    # Set up structured logging before anything else logs
    configure_logging()

    # Create and attach the in-memory job store
    # Accessible in route handlers via: request.app.state.job_repo
    app.state.job_repo = JobRepository()

    yield  # application runs here

    # Nothing to tear down — the in-memory store is garbage-collected on exit


def create_app() -> FastAPI:
    app = FastAPI(
        title="School Scheduling Engine",
        description=(
            "Auto-generates conflict-free, fair class group assignments for a school. "
            "Uses a two-stage solver: CP-SAT for time-slot assignment (Stage 1) "
            "and a greedy algorithm for room assignment (Stage 2)."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # Allow the Next.js frontend (and local dev) to call this API
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",        # Next.js dev server
            "https://sci-scheduler.vercel.app",  # production frontend
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register custom exception handlers (JobNotFoundError → 404, etc.)
    register_exception_handlers(app)

    # Mount all v1 routes under the configured prefix (default: /api/v1)
    app.include_router(api_v1_router, prefix=settings.api_prefix)

    return app


# Module-level app instance used by uvicorn
app = create_app()
