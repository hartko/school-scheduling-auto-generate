"""Aggregates all v1 route groups into a single router."""

from fastapi import APIRouter
from app.api.v1.endpoints.scheduler import router as scheduler_router

api_v1_router = APIRouter()
api_v1_router.include_router(scheduler_router)
