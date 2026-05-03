"""
Integration tests for the scheduler API endpoints.

Uses httpx.AsyncClient with ASGI transport (no real network).
The solver's BackgroundTask is patched so tests run instantly.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.models.responses import JobStatus


@pytest.mark.asyncio
async def test_generate_returns_202_with_job_id(async_client, job_repo):
    """POST /generate should return 202 and a job_id immediately."""

    # Patch the background task so the solver doesn't actually run
    with patch("app.api.v1.endpoints.scheduler.run_solver_task", new=AsyncMock()):
        response = await async_client.post(
            "/api/v1/scheduler/generate",
            json={
                "assignments": [{"section_id": 1, "subject_ids": [1, 2]}],
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] == JobStatus.PENDING


@pytest.mark.asyncio
async def test_get_job_status_pending(async_client, job_repo):
    """GET /jobs/{job_id} should return PENDING for a newly created job."""
    job_id = job_repo.create_job()

    response = await async_client.get(f"/api/v1/scheduler/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == JobStatus.PENDING


@pytest.mark.asyncio
async def test_get_job_not_found(async_client):
    """GET /jobs/{id} for an unknown job_id should return 404."""
    response = await async_client.get("/api/v1/scheduler/jobs/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_result_when_not_done_returns_409(async_client, job_repo):
    """GET /jobs/{id}/result should return 409 if the job is still running."""
    job_id = job_repo.create_job()
    job_repo.update_status(job_id, JobStatus.RUNNING, progress=30)

    response = await async_client.get(f"/api/v1/scheduler/jobs/{job_id}/result")
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_get_result_when_done(async_client, job_repo):
    """GET /jobs/{id}/result should return the class groups when the job is done."""
    job_id = job_repo.create_job()
    job_repo.store_result(job_id, [
        {
            "teacher_subject_id": 1,
            "section_id": 1,
            "schedule_time_id": 10,
            "room_schedule_id": 5,
        }
    ])

    response = await async_client.get(f"/api/v1/scheduler/jobs/{job_id}/result")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["class_groups"][0]["teacher_subject_id"] == 1


@pytest.mark.asyncio
async def test_delete_job_returns_204(async_client, job_repo):
    """DELETE /jobs/{id} should remove the job and return 204."""
    job_id = job_repo.create_job()

    response = await async_client.delete(f"/api/v1/scheduler/jobs/{job_id}")
    assert response.status_code == 204

    # Job should be gone
    response = await async_client.get(f"/api/v1/scheduler/jobs/{job_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_job_returns_404(async_client):
    """DELETE /jobs/{id} for an unknown id should return 404."""
    response = await async_client.delete("/api/v1/scheduler/jobs/ghost-id")
    assert response.status_code == 404
