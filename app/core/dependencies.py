"""
FastAPI dependency factories.
Use these with Depends() in route handlers to inject shared resources.
"""

from fastapi import Request
from app.repositories.job_repository import JobRepository
from app.clients.nestjs_client import NestJSClient


def get_job_repo(request: Request) -> JobRepository:
    """Return the app-scoped in-memory job repository stored on app.state."""
    return request.app.state.job_repo


def get_nestjs_client() -> NestJSClient:
    """Return a fresh NestJS client instance (used as async context manager in routes)."""
    return NestJSClient()
