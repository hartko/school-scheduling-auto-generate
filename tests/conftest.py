"""
Shared pytest fixtures used across unit and integration tests.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.repositories.job_repository import JobRepository


@pytest.fixture
def job_repo() -> JobRepository:
    """A fresh in-memory job repository for each test."""
    return JobRepository()


@pytest.fixture
async def async_client(job_repo: JobRepository):
    """
    An httpx AsyncClient that talks directly to the FastAPI app (no network).
    Injects a fresh job_repo so tests don't share state.
    """
    application = create_app()
    # Override the app-level job_repo with the test fixture's instance
    application.state.job_repo = job_repo

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        yield client
